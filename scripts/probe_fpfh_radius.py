"""扫 FPFH 半径 × 注入角度，判断 7° 失败是「参数选错」还是「描述子极限」。

为什么要有这个脚本
------------------
ADR-0031 把配准的不确定性解决了（单线程可复现），但留下一个更根本的问题：
**7° 档稳定地失败**（30.00° 误差、fitness 0.2588），而 30°/90°/180° 全部通过。

两个候选解释，需要数据来分：

1. **FPFH 半径选错了**（当前 `diag × 0.05`）——换个半径就好，属参数问题，便宜；
2. **FPFH 对这个角度就分辨不出**——换半径也没用，属描述子极限，要换方法。

判据：
  * 若存在某个半径让 7° 的旋转误差 ≤2° → 是 (1)，调参即可；
  * 若所有半径下 7° 都 >10°         → 是 (2)，得改多尺度或换描述子。

同时测「对应点内点率」——这条被长期忽略
----------------------------------------
生产日志里一直有：

    [Open3D WARNING] Too few correspondences (1750) after mutual filter,
                     fall back to original correspondences.

3.3 万点只有 1750 对对应（5%），而且相互过滤后还不够、回退到**未过滤**的对应。

RANSAC 的 `ransac_n=4`、`max_iteration=100000`、`confidence=0.999`。若对应里真内点
比例是 p，抽 4 个全对的概率是 p^4，10 万次迭代能覆盖的最低 p 约为 0.001^(1/4)…

    需要迭代 ≈ log(1-0.999) / log(1 - p^4)

    p=0.05 → p^4=6.3e-6 → 约 1.1e6 次（**10 万次差一个数量级**）
    p=0.10 → p^4=1e-4   → 约 6.9e4 次（够）
    p=0.02 → p^4=1.6e-7 → 约 4.3e7 次（差三个数量级）

所以「内点率」可能比半径更根本。本脚本把它一起量出来。

用法::

    .venv_lab\\Scripts\\python.exe scripts/probe_fpfh_radius.py
    .venv_lab\\Scripts\\python.exe scripts/probe_fpfh_radius.py --angles 7 --radii 0.02,0.05,0.12
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

DEFAULT_PLY = r"D:/h3d_relic_test/阶段成果/00_raw/relic_001/test111.ply"


def _rot_err(T, R_inj) -> float:
    """配准变换与「真值」的角差（度）。

    **真值是 ``R_inj.T``，不是 ``R_inj``。** source 被注入了 ``R_inj``（转到错位
    姿态），配准要做的是把它**转回去**，所以正确解是 ``R_inj`` 的逆。

    第一版这里写成 ``R @ R_inj.T``，于是正确解被算成 2θ 的误差：
    7° 报 14.00°、30° 报 60.00°——两个数都恰好是注入角的 2 倍，那就是
    「方向反了」的算术特征。当时误以为配准全错，白做了一轮半径扫描。
    """
    import numpy as np

    R = np.asarray(T)[:3, :3]
    c = (np.trace(R @ np.asarray(R_inj)) - 1.0) / 2.0
    return float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))


def _build_case(ply: str, angle_deg: float, noise_rel: float, seed: int):
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
    Q = (Tp - c) @ R.T + c + np.array([0.01 * diag, -0.01 * diag, 0.005 * diag])
    rng = np.random.default_rng(seed)
    Q = Q + rng.normal(0, noise_rel * diag, Q.shape)
    src = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(Q))
    return src, tgt, R, diag


def _corr_stats(src, tgt, f_s, f_t, T, thr):
    """对应点数量 + 用 ``T`` 判定的内点率。

    这一步回答的是「RANSAC 的搜索空间有多脏」，与最终 fitness 是两件事：
    fitness 说「变换好不好」，内点率说「能不能找到这个变换」。
    """
    import numpy as np
    from open3d.pipelines.registration import correspondences_from_features

    # 签名只有 (src_feat, tgt_feat, mutual_filter, mutual_consistency_ratio)——
    # 没有 estimation_method。这里与 ransac.py 同口径：mutual_filter=True。
    corr = np.asarray(correspondences_from_features(f_s, f_t,
                                                    mutual_filter=True))
    if len(corr) == 0:
        return 0, 0.0
    ps = np.asarray(src.points)[corr[:, 0]]
    pt = np.asarray(tgt.points)[corr[:, 1]]
    moved = ps @ np.asarray(T)[:3, :3].T + np.asarray(T)[:3, 3]
    d = np.linalg.norm(moved - pt, axis=1)
    return int(len(corr)), float((d <= thr).mean())


def main() -> int:
    ap = argparse.ArgumentParser(description="FPFH 半径 × 角度扫描")
    ap.add_argument("--ply", default=DEFAULT_PLY)
    ap.add_argument("--radii", default="0.5,0.75,1.0,1.5,2.0,3.0",
                    help="FPFH 半径**相对生产生效值**的倍数，逗号分隔。"
                         "1.0 = 与 resolve_task_config 算出来的一致")
    ap.add_argument("--angles", default="7,30", help="注入角度，逗号分隔")
    ap.add_argument("--noise", type=float, default=0.002)
    ap.add_argument("--seed", type=int, default=20261031)
    ap.add_argument("--csv", default="")
    args = ap.parse_args()

    import h3d  # noqa: F401  —— 先设单线程（ADR-0031），否则结果不可比
    import open3d as o3d

    from h3d.core import config as _config
    from h3d.core import get_logger
    from h3d.io import save_point_cloud
    from h3d.register import (
        _icp_multi_init, _ransac_with_fallback, compute_fpfh,
    )
    from h3d.runner.batch_processor import resolve_task_config
    from h3d.runner.task import Task


    def _base_cfg(src, tgt, diag, tmp):
        """生产生效配置（尺度自适应）。

        第一版脚本手搓 `RegisterConfig(fpfh=r, ransac=r, icp=r*0.5)`，
        结果 30° 档也算成 60° 误差——与 `verify_registration_e2e.py` 的
        PASS 0.00° 矛盾。查下去是基准就不对：生产值是
        `fpfh=0.1085 / ransac=0.1085 / icp=0.0542`（diag×0.0162），
        而我扫的是 diag×0.02~0.18，**根本没覆盖到生产点**。
        """
        import os as _os

        _os.makedirs(_os.path.join(tmp, "00_raw", "x"), exist_ok=True)
        p1 = _os.path.join(tmp, "00_raw", "x", "s01.ply")
        p2 = _os.path.join(tmp, "00_raw", "x", "s02.ply")
        save_point_cloud(tgt, p1)
        save_point_cloud(src, p2)
        cfg, _info = resolve_task_config(
            Task(item_id="x", raw_paths=[p1, p2], root=tmp),
            _config.loads("{}"))
        return cfg.register

    get_logger().setLevel("ERROR")
    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)

    radii = [float(x) for x in args.radii.split(",") if x.strip()]
    angles = [float(x) for x in args.angles.split(",") if x.strip()]

    rows = []
    for angle in angles:
        src, tgt, R_true, diag = _build_case(args.ply, angle, args.noise,
                                             args.seed)
        print(f"\n{'=' * 78}\n注入 {angle}°   点云 {len(src.points):,} 点   "
              f"对角线 {diag:.4f}")
        print(f"{'半径':>9} {'倍数':>6} {'对应点':>8} {'内点率':>8} "
              f"{'R-fit':>7} {'R-err°':>8} {'终fit':>7} {'终err°':>8} "
              f"{'t(s)':>6}")
        print("-" * 78)
        import copy as _copy
        import tempfile as _tf

        base = _base_cfg(src, tgt, diag, _tf.mkdtemp(prefix="fpfh_"))
        print(f"生产基准：fpfh={base.fpfh_radius:.4f} "
              f"ransac={base.ransac_distance:.4f} icp={base.icp_distance:.4f}"
              f"（fpfh = diag×{base.fpfh_radius / diag:.4f}）")
        for mult in radii:
            r = base.fpfh_radius * mult
            cfg = _copy.deepcopy(base)
            cfg.fpfh_radius = r
            # 生产里 ransac_distance 与 fpfh_radius 相等，一起缩；
            # icp_distance 是精配准参数，与特征半径无关，**保持不动**——
            # 否则一次改两个变量，分不清是谁的影响。
            cfg.ransac_distance = base.ransac_distance * mult
            t0 = time.time()
            f_s = compute_fpfh(src, cfg.fpfh_radius)
            f_t = compute_fpfh(tgt, cfg.fpfh_radius)
            ras = _ransac_with_fallback(src, tgt, f_s, f_t, cfg.ransac_distance)
            n_corr, inl = _corr_stats(src, tgt, f_s, f_t,
                                      ras.transformation, cfg.ransac_distance)
            icp, tag, _ = _icp_multi_init(src, tgt, ras.transformation, cfg)
            if icp is None:
                icp, tag = ras, "无"
            dt = time.time() - t0

            row = {
                "angle": angle, "radius": round(r, 6), "mult": mult,
                "fpfh_mult_of_diag": round(r / diag, 5),
                "n_corr": n_corr, "inlier_ratio": round(inl, 4),
                "ransac_fitness": round(float(ras.fitness), 4),
                "ransac_rot_err": round(_rot_err(ras.transformation, R_true), 3),
                "final_fitness": round(float(icp.fitness), 4),
                "final_rot_err": round(_rot_err(icp.transformation, R_true), 3),
                "init": tag, "seconds": round(dt, 2),
            }
            rows.append(row)
            print(f"{r:9.4f} {mult:6.2f} {n_corr:8d} {inl:8.1%} "
                  f"{row['ransac_fitness']:7.4f} {row['ransac_rot_err']:8.2f} "
                  f"{row['final_fitness']:7.4f} {row['final_rot_err']:8.2f} "
                  f"{dt:6.1f}")

    # ---------- 结论 ----------
    print(f"\n{'=' * 78}")
    for angle in angles:
        sub = [x for x in rows if x["angle"] == angle]
        ok = [x for x in sub if x["final_rot_err"] <= 2.0]
        best = min(sub, key=lambda x: x["final_rot_err"])
        print(f"{angle:5.0f}°：{len(ok)}/{len(sub)} 个半径能解对；"
              f"最好的是 {best['mult']:.2f}×（误差 {best['final_rot_err']:.2f}°，"
              f"内点率 {best['inlier_ratio']:.1%}）")
        if not ok:
            print(f"         → **所有半径都解不对**：不是半径问题，"
                  f"是描述子/对应质量的问题")
    print("\n读法：内点率 <1% 时 10 万次 RANSAC 迭代不够（需 >1e7 次）；"
          "\n      这时「换半径」只是换了个同样脏的对应集。")

    if args.csv:
        os.makedirs(os.path.dirname(os.path.abspath(args.csv)), exist_ok=True)
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\nCSV → {os.path.abspath(args.csv)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
