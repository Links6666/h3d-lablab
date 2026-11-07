"""诊断：M2 配准的单次失败率（7° 档 6/10）到底来自哪一步。

为什么需要它
------------
r34 实测：同一份真机点云注入 7°，跑 10 次只有 6 次解对。用 worktree 检出 r32
对照也是 6/10，所以**不是某次改动引入的**，是长期存在的不确定性。

r28 的记法把原因归给「RANSAC 内部并行归约不受种子完全约束」，但那是推断，
没有把三步拆开量过。`register.run` 一次做完 FPFH → RANSAC → ICP，任何一步
不稳都会表现为「整体不稳」，光看最终结果分不出是谁。

这个脚本把同一份输入喂给四段，各跑 N 次，只问一个问题：**哪一段的输出开始
出现多个不同值**。

    A. compute_fpfh        → 特征是否确定（哈希）
    B. _ransac_with_fallback → 初值变换是否确定（哈希）
    C. icp_refine（固定初值）→ ICP 是否确定（哈希）
    D. run()（端到端）      → 与 A–C 对照，确认拼起来的行为

判据
----
某一段出现 >1 个唯一值，它就是（或至少是第一个）不稳定的环节。
按 A→B→C 顺序看，**第一个变的那一段就是根因所在**。

用法::

    .venv_lab\\Scripts\\python.exe scripts/diagnose_register_steps.py
    .venv_lab\\Scripts\\python.exe scripts/diagnose_register_steps.py --angle 7 --trials 8
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

DEFAULT_PLY = r"D:/h3d_relic_test/阶段成果/00_raw/relic_001/test111.ply"


def _hash_arr(a) -> str:
    """数组指纹（量化到 1e-9，避免浮点末位噪声淹没真正的差异）。

    为什么要量化：ICP 是迭代的，末位差 1e-32 属于「同一结果」（项目已有结论，
    见 test_register_reproducible）。这里关心的是**决策级**差异（初值选谁、
    收敛到哪个局部极小），量化到 1e-9 足够。
    """
    import numpy as np

    a = np.round(np.asarray(a, dtype=np.float64), 9)
    return hashlib.sha256(a.tobytes()).hexdigest()[:12]


def _rot_err(T, R_true) -> float:
    """变换里的旋转与真值的角差（度）。"""
    import numpy as np

    R = np.asarray(T)[:3, :3]
    rel = R @ R_true.T
    c = (np.trace(rel) - 1.0) / 2.0
    return float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))


def _build_case(ply: str, angle_deg: float, noise_rel: float, seed: int):
    """构造已知真值的两站点云：target 原件，source = 旋转 + 平移 + 噪声。"""
    import numpy as np
    import open3d as o3d

    from h3d.io import load_point_cloud

    pc = load_point_cloud(ply)
    P = np.asarray(pc.points)
    diag = float(np.linalg.norm(P.max(0) - P.min(0)))
    tgt = o3d.geometry.PointCloud(
        o3d.utility.Vector3dVector(P)).voxel_down_sample(diag / 400.0)
    Tp = np.asarray(tgt.points)

    a = np.radians(angle_deg)
    R = np.array([[np.cos(a), 0, np.sin(a)],
                  [0, 1, 0],
                  [-np.sin(a), 0, np.cos(a)]])
    c = Tp.mean(0)
    shift = np.array([0.01 * diag, -0.01 * diag, 0.005 * diag])
    Q = (Tp - c) @ R.T + c + shift
    rng = np.random.default_rng(seed)
    Q = Q + rng.normal(0, noise_rel * diag, Q.shape)
    src = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(Q))
    return src, tgt, R


def _scaled_cfg(diag: float, tmp: str):
    """与真实管线同口径的配置（尺度自适应会按对角线缩放 FPFH/ICP 半径）。

    直接拿默认 0.2 m 半径去配一个 0.1 m 的件必然配不动——那不是 M2 的问题。
    这里走 ``resolve_task_config`` 复现生产配置。
    """
    import numpy as np

    from h3d.core import config as _config
    from h3d.io import save_point_cloud
    from h3d.runner.batch_processor import resolve_task_config
    from h3d.runner.task import Task

    os.makedirs(os.path.join(tmp, "00_raw", "x"), exist_ok=True)
    p1 = os.path.join(tmp, "00_raw", "x", "s01.ply")
    p2 = os.path.join(tmp, "00_raw", "x", "s02.ply")
    save_point_cloud(_DUMMY["tgt"], p1)
    save_point_cloud(_DUMMY["src"], p2)
    cfg, info = resolve_task_config(
        Task(item_id="x", raw_paths=[p1, p2], root=tmp), _config.loads("{}"))
    return cfg.register, info


_DUMMY: dict = {}


def main() -> int:
    ap = argparse.ArgumentParser(description="配准三步拆分诊断")
    ap.add_argument("--ply", default=DEFAULT_PLY)
    ap.add_argument("--angle", type=float, default=7.0)
    ap.add_argument("--noise", type=float, default=0.002,
                    help="噪声（包围盒对角线的比例）")
    ap.add_argument("--trials", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20261031)
    args = ap.parse_args()

    import numpy as np

    from h3d.register import (
        _ransac_with_fallback, compute_fpfh, icp_refine, run,
    )
    from h3d.core import get_logger

    get_logger().setLevel("ERROR")          # 诊断时不看 WARNING 刷屏
    import open3d as o3d

    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)

    src, tgt, R_true = _build_case(args.ply, args.angle, args.noise, args.seed)
    _DUMMY["src"], _DUMMY["tgt"] = src, tgt
    diag = float(np.linalg.norm(
        np.asarray(tgt.points).max(0) - np.asarray(tgt.points).min(0)))
    cfg, info = _scaled_cfg(diag, tempfile.mkdtemp(prefix="diag_"))

    print(f"输入：{os.path.basename(args.ply)}  "
          f"src={len(src.points):,} tgt={len(tgt.points):,}  "
          f"注入 {args.angle}° + 噪声 {args.noise:.3%}  "
          f"每段 {args.trials} 次")
    print(f"配置：fpfh={cfg.fpfh_radius:.4f} ransac={cfg.ransac_distance:.4f} "
          f"icp={cfg.icp_distance:.4f} max_iter={cfg.max_iter} "
          f"(尺度因子 {info.get('factor')})")

    # ---------- A. FPFH 特征 ----------
    f_src0 = compute_fpfh(src, cfg.fpfh_radius)
    f_tgt0 = compute_fpfh(tgt, cfg.fpfh_radius)
    hA_s, hA_t = [], []
    for _ in range(min(3, args.trials)):
        hA_s.append(_hash_arr(compute_fpfh(src, cfg.fpfh_radius).data))
        hA_t.append(_hash_arr(compute_fpfh(tgt, cfg.fpfh_radius).data))
    okA = len(set(hA_s)) == 1 and len(set(hA_t)) == 1
    print(f"\nA. compute_fpfh ×{len(hA_s)}:  "
          f"src 唯一值 {len(set(hA_s))} / tgt 唯一值 {len(set(hA_t))}"
          f"  {'确定' if okA else '**不确定**'}")

    # ---------- B. RANSAC 初值（固定同一份 FPFH）----------
    resB = []
    for _ in range(args.trials):
        r = _ransac_with_fallback(src, tgt, f_src0, f_tgt0,
                                  cfg.ransac_distance)
        resB.append({"h": _hash_arr(r.transformation),
                     "T": np.asarray(r.transformation, dtype=np.float64),
                     "fit": round(float(r.fitness), 4),
                     "err": round(_rot_err(r.transformation, R_true), 3)})
    uB = {x["h"] for x in resB}
    print(f"\nB. _ransac_with_fallback ×{args.trials}: 唯一变换 {len(uB)}"
          f"  {'确定' if len(uB) == 1 else '**不确定**'}")
    for x in resB:
        print(f"     {x['h']}  fitness={x['fit']:.4f}  旋转误差={x['err']:7.3f}°")
    fitB = [x["fit"] for x in resB]
    print(f"     fitness 区间 [{min(fitB):.4f}, {max(fitB):.4f}]")

    # ---------- C. ICP（固定 B 的第一个初值）----------
    # 用 B 段第一次的初值：固定住它，C 段就只测 ICP 一个变量
    T0 = resB[0]["T"]
    resC = []
    for _ in range(args.trials):
        r = icp_refine(src, tgt, T0, cfg.icp_distance, cfg.max_iter,
                       point_to_plane=True)
        resC.append({"h": _hash_arr(r.transformation),
                     "fit": round(float(r.fitness), 4),
                     "err": round(_rot_err(r.transformation, R_true), 3)})
    uC = {x["h"] for x in resC}
    print(f"\nC. icp_refine（固定初值）×{args.trials}: 唯一变换 {len(uC)}"
          f"  {'确定' if len(uC) == 1 else '**不确定**'}")
    for x in resC:
        print(f"     {x['h']}  fitness={x['fit']:.4f}  旋转误差={x['err']:7.3f}°")

    # ---------- D. 端到端 ----------
    resD = []
    for _ in range(args.trials):
        try:
            r = run(src, tgt, cfg)
            resD.append({"h": _hash_arr(r["transformation"]),
                         "fit": round(float(r["fitness"]), 4),
                         "err": round(_rot_err(r["transformation"], R_true), 3),
                         "init": r.get("icp_init")})
        except Exception as exc:  # noqa: BLE001 - 失败本身就是被测行为
            resD.append({"h": f"ERR:{type(exc).__name__}", "fit": 0.0,
                         "err": 999.0, "init": None})
    okD = sum(1 for x in resD if x["err"] <= 2.0)
    print(f"\nD. run() 端到端 ×{args.trials}: 唯一变换 {len({x['h'] for x in resD})}"
          f"  解对 {okD}/{args.trials}（误差 ≤2°）")
    for x in resD:
        print(f"     {x['h']:<14} fitness={x['fit']:.4f} "
              f"旋转误差={x['err']:7.3f}° init={x['init']}")

    # ---------- 结论 ----------
    print("\n" + "=" * 62)
    first_unstable = None
    for name, n in (("A compute_fpfh", 1 if okA else 2),
                    ("B RANSAC 初值", len(uB)),
                    ("C ICP", len(uC)),
                    ("D run()", len({x["h"] for x in resD}))):
        if n > 1 and first_unstable is None:
            first_unstable = name
        print(f"  {name:18s} 唯一结果 {n}")
    print("-" * 62)
    if first_unstable is None:
        print("结论：本次采样内各段都确定——失败率可能只在跨进程时才出现，")
        print("      需要改用「多次子进程」的回路（见 --trials 之外的办法）。")
    else:
        print(f"结论：第一个出现多值的环节是 **{first_unstable}**，根因在这一段。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
