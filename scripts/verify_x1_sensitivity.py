"""X1 M3C2 灵敏度标定回路：造「已知形变」的两期，反查「报出多少 vs 注入多少」。

一条命令、确定性、秒级。它只做一件事：把 M3C2 的输入换成**解析可知形变**的
合成点云，看它报出的距离场是不是那个数。

    python scripts/verify_x1_sensitivity.py                    # 球面，6 档注入
    python scripts/verify_x1_sensitivity.py --shape cylinder    # 侧壁（法向水平）
    python scripts/verify_x1_sensitivity.py --noise 0.0005      # 加 0.5mm 法向噪声

为什么需要它（ADR-0005）：真机 1.1.12 上**同源两期**报出 ``max_change=177mm``，
而两期点云互最近邻中位只有 4.83mm。没有已知真值，就分不清是
「M3C2 尺度没调对」还是「两期输入根本不是同一份几何」——这两种情况的修法完全
不同（前者调参数，后者改输入定位）。合成形变是唯一能给出真值的对照。

判据（绿）：
    * d=0    -> |median| <= 1mm  且 |max| <= change_mm（同源必须报 0）
    * d>0    -> |median - d| <= max(1mm, 15% x d)（沿法向的位移必须被量到）
    * 单调    -> median 随 d 单调增，否则是尺度错位而不是噪声

注：统计用**中位数**而不是 ``max_change_mm``。max 由个别核心点决定，曲面边缘
一两个点的法向偏差就能把它顶到几十倍；要判断「整体尺度对不对」只能看中位。
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from h3d.change import run as change_run  # noqa: E402
from h3d.core import ChangeConfig  # noqa: E402

#: 默认注入档位（毫米）。0 是同源对照，必须报 0；其余给出线性段与饱和段。
DEFAULT_DELTAS = (0.0, 1.0, 2.0, 5.0, 10.0, 20.0)


def _fibonacci_sphere(n: int, radius: float) -> np.ndarray:
    """球面 Fibonacci 均匀采样（法向 = 径向，天然覆盖「不朝 Z」的情形）。"""
    i = np.arange(n, dtype=np.float64) + 0.5
    phi = np.arccos(1.0 - 2.0 * i / n)          # 极角，均匀分布
    theta = np.pi * (1.0 + 5.0 ** 0.5) * i      # 黄金角
    return radius * np.stack(
        [np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)],
        axis=1)


def _fibonacci_cylinder(n: int, radius: float, height: float) -> np.ndarray:
    """圆柱侧壁均匀采样。法向水平——真机（1.8 m 兵马俑躯干）的主形态。

    为什么单列一个形态：``py4dgeo`` 的 ``orientation_vector`` 默认 ``[0,0,1]``，
    会把所有法向翻到 +Z 半球。球面上这错得温和（只是符号），侧壁上错得致命
    （圆柱斜穿物体）——真机 450mm 那次就是这个。合成侧壁能把这条单独锁住。
    """
    i = np.arange(n, dtype=np.float64) + 0.5
    theta = np.pi * (1.0 + 5.0 ** 0.5) * i
    z = ((i / n) - 0.5) * height
    return np.stack([radius * np.cos(theta), radius * np.sin(theta), z], axis=1)


def make_pair(shape: str, n: int, radius: float, height: float,
              delta_m: float, noise_m: float, seed: int = 0):
    """返回 (t1, t2)：同一组采样方向，t2 沿**法向**外移 ``delta_m``。

    同一组方向很关键——两期点数、拓扑一一对应，M3C2 报出的距离就等于注入值，
    不存在「点云不同源」这个混淆项。
    """
    rng = np.random.default_rng(seed)
    if shape == "cylinder":
        normals = _fibonacci_cylinder(n, 1.0, height)
        scale = radius
        base = normals * np.array([scale, scale, 1.0])
    else:
        normals = _fibonacci_sphere(n, 1.0)
        base = normals * radius
    unit = normals / np.linalg.norm(normals, axis=1, keepdims=True)

    t1 = base + unit * rng.normal(0.0, noise_m, n)[:, None]
    t2 = base + unit * (delta_m + rng.normal(0.0, noise_m, n))[:, None]
    return t1, t2


def _stats(result: dict) -> dict:
    """从 M3C2 结果里取判读用的统计量（米 -> 毫米）。"""
    dist = np.asarray(result["distances"], dtype=np.float64) * 1000.0
    valid = dist[~np.isnan(dist)]
    unc = np.asarray(result["uncertainties"], dtype=np.float64) * 1000.0
    unc = unc[~np.isnan(unc)]
    return {
        "n_core": len(dist),
        "n_valid": len(valid),
        "median": float(np.median(valid)) if len(valid) else float("nan"),
        "mean": float(np.mean(valid)) if len(valid) else float("nan"),
        "p90": float(np.percentile(np.abs(valid), 90)) if len(valid) else float("nan"),
        "amax": float(np.max(np.abs(valid))) if len(valid) else float("nan"),
        "lod": float(np.median(unc)) if len(unc) else float("nan"),
        "mps": float(result["mps_m"]) * 1000.0,
        "cyl": float(result["cyl_radius_m"]) * 1000.0,
        "norm": float(result["normal_radius_m"]) * 1000.0,
    }


def _verdict(delta_mm: float, s: dict) -> tuple[bool, str]:
    """判据分两层：绝对值（尺度）与符号（堆积/侵蚀的方向）。

    符号单独判，因为它不体现在 ``|·|`` 里：注**外移**（材料增加 = 堆积）必须
    报正数。旧实现绝对值精确到 0.05% 却全部报负——只看绝对值的判据会把它放过去，
    而 ``detect_changes`` 正拿符号定「堆积 / 风化」，结论直接反。
    """
    if delta_mm <= 0.0:
        ok = abs(s["median"]) <= 1.0 and abs(s["amax"]) <= max(2.0, s["lod"])
        return ok, "同源报 0" if ok else "**同源不归零**"
    tol = max(1.0, 0.15 * delta_mm)
    err = abs(s["median"] - delta_mm)
    if s["median"] < 0:
        return False, f"符号反（报 {s['median']:.2f}，外移应为正）"
    return err <= tol, f"误差 {err:.2f}mm（容差 {tol:.2f}）"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shape", default="sphere", choices=["sphere", "cylinder"])
    ap.add_argument("--points", type=int, default=40000)
    ap.add_argument("--radius", type=float, default=0.10, help="米（球/柱半径）")
    ap.add_argument("--height", type=float, default=0.30, help="米（仅圆柱）")
    ap.add_argument("--noise", type=float, default=0.0, help="法向噪声 σ（米）")
    ap.add_argument("--deltas", default="", help="逗号分隔的注入档（mm）")
    ap.add_argument("--grid", default="32,32", help="核心点网格 nx,ny")
    args = ap.parse_args()

    deltas = ([float(x) for x in args.deltas.split(",") if x.strip()]
              if args.deltas else list(DEFAULT_DELTAS))
    nx, ny = (int(v) for v in args.grid.split(","))
    cfg = ChangeConfig(grid=[nx, ny], cores=4, change_mm=2.0)
    tag = f"{args.shape} {args.points} 点 R={args.radius * 1000:.0f}mm"
    if args.shape == "cylinder":
        tag += f" H={args.height * 1000:.0f}mm"
    if args.noise > 0:
        tag += f" σ={args.noise * 1000:.2f}mm"
    print(f"合成两期：{tag}  核心点网格 {nx}x{ny}\n")

    rows, ok_all = [], True
    for d in deltas:
        t1, t2 = make_pair(args.shape, args.points, args.radius,
                           args.height, d / 1000.0, args.noise)
        res = change_run(_pcd(t1), _pcd(t2), cfg)
        s = _stats(res["m3c2"])
        ok, note = _verdict(d, s)
        ok_all &= ok
        rows.append((d, s, ok, note))
        print(f"  d={d:6.2f}mm  有效 {s['n_valid']:4d}/{s['n_core']:<4d} "
              f"median={s['median']:8.3f}  mean={s['mean']:8.3f}  "
              f"p90|·|={s['p90']:7.3f}  max|·|={s['amax']:8.3f}  "
              f"LoD={s['lod']:6.3f}  {'PASS' if ok else 'FAIL'}  {note}")

    s0 = rows[0][1]
    print(f"\n尺度：MPS={s0['mps']:.3f}mm  normal_r={s0['norm']:.2f}mm  "
          f"cyl_r={s0['cyl']:.2f}mm  （cyl/MPS={s0['cyl'] / max(s0['mps'], 1e-9):.2f}）")

    vals = [r[1]["median"] for r in rows if not np.isnan(r[1]["median"])]
    mono = all(b >= a - 1e-6 for a, b in zip(vals, vals[1:]))
    print(f"单调性：{'PASS' if mono else 'FAIL'}"
          f"（median 序列 {['%.2f' % v for v in vals]}）")

    # 线性度：d>0 各档对 d 做最小二乘，斜率应 ≈ 1（斜率 0.5 = 尺度错一半，
    # 斜率 1000 = 单位混了 mm/m）。
    pos = [(r[0], r[1]["median"]) for r in rows
           if r[0] > 0 and not np.isnan(r[1]["median"])]
    if len(pos) >= 2:
        x = np.array([p[0] for p in pos])
        y = np.array([p[1] for p in pos])
        slope, icept = np.polyfit(x, y, 1)
        print(f"线性度：median ≈ {slope:.3f} × d {icept:+.3f}mm"
              f"（理想 1.000 / 0.000）")

    print(f"\n结论：{'全部 PASS —— M3C2 尺度可信' if ok_all and mono else '存在 FAIL —— 尺度不可信，禁止置 calibrated=True'}")
    return 0 if (ok_all and mono) else 1


def _pcd(pts: np.ndarray):
    import open3d as o3d

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(np.asarray(pts, np.float64))
    return pcd


if __name__ == "__main__":
    raise SystemExit(main())
