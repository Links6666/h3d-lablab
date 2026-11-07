"""真机探针：按法向一致性筛选到底有没有用（P0-2 / P1-1）。

回答三个问题，每个都要能说「没有」：
    1. ``normal_filter=0``（默认）时结果是否与不筛时**完全一致**（回归前提）；
    2. 打开筛选后，**低重叠**场景的 fitness 会不会退化；
    3. 法向可靠性（c 中位数）在真机点云上到底是多少——0.7 这个阈值合不合适。

用法::

    python scripts/probe_normal_filter.py                       # 120° 注入
    python scripts/probe_normal_filter.py --keep 0.6            # target 只留 60%
    python scripts/probe_normal_filter.py --angle 30 --keep 1.0
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PLY = r"D:/h3d_relic_test/阶段成果/00_raw/relic_001/test111.ply"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ply", default=PLY)
    ap.add_argument("--angle", type=float, default=120.0)
    ap.add_argument("--keep", type=float, default=1.0,
                    help="target 保留比例（按 x 轴切片，模拟部分重叠）")
    ap.add_argument("--noise", type=float, default=0.002)
    ap.add_argument("--filters", default="0,0.7,0.9")
    args = ap.parse_args()

    import open3d as o3d

    from h3d.core import config as _config
    from h3d.io import load_point_cloud, save_point_cloud
    from h3d.register import run
    from h3d.register.normals import normal_quality
    from h3d.runner.batch_processor import resolve_task_config
    from h3d.runner.task import Task

    P = np.asarray(load_point_cloud(args.ply).points)
    diag = float(np.linalg.norm(P.max(0) - P.min(0)))
    full = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(P)).voxel_down_sample(
        diag / 400.0)
    TP = np.asarray(full.points)

    if args.keep < 1.0:
        cut = np.quantile(TP[:, 0], 1.0 - args.keep)
        TP = TP[TP[:, 0] >= cut]
    tgt = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(TP))

    a = np.radians(args.angle)
    R = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0],
                  [-np.sin(a), 0, np.cos(a)]])
    Q = (TP - TP.mean(0)) @ R.T + TP.mean(0) \
        + np.array([0.01, -0.01, 0.005]) * diag
    rng = np.random.default_rng(20261031)
    Q = Q + rng.normal(0, args.noise * diag, Q.shape)
    src = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(Q))

    tmp = tempfile.mkdtemp(prefix="nf_")
    os.makedirs(os.path.join(tmp, "00_raw", "x"), exist_ok=True)
    p1 = os.path.join(tmp, "00_raw", "x", "s01.ply")
    p2 = os.path.join(tmp, "00_raw", "x", "s02.ply")
    save_point_cloud(tgt, p1)
    save_point_cloud(src, p2)
    cfg, _info = resolve_task_config(
        Task(item_id="x", raw_paths=[p1, p2], root=tmp), _config.loads("{}"))

    q = normal_quality(src, radius=cfg.register.fpfh_radius)
    print(f"点云：{len(TP)} 点（target 保留 {args.keep:.0%}），注入 {args.angle}°，"
          f"噪声 {args.noise:.3%} 对角线")
    print(f"法向可靠性：median={q['median']:.3f} frac_low={q['frac_low']:.3f} "
          f"usable={q['usable']}（阈值 0.7 未标定）")
    print(f"{'filter':>7} {'fitness':>8} {'rmse':>10} {'kept':>6} "
          f"{'init':>14} {'旋转误差':>8}")

    for f in [float(x) for x in args.filters.split(",")]:
        # RegisterConfig 是项目自己的 _ConfigBase（不是 pydantic），没有 model_copy；
        # 直接改字段即可——每次循环覆盖，没有跨轮污染。
        cfg.register.normal_filter = f
        try:
            res = run(src, tgt, cfg.register)
        except Exception as exc:  # noqa: BLE001
            print(f"{f:>7.2f}  失败：{type(exc).__name__}: {exc}")
            continue
        T = np.asarray(res["transformation"])
        ang = float(np.degrees(np.arccos(
            np.clip((np.trace(T[:3, :3]) - 1) / 2.0, -1.0, 1.0))))
        print(f"{f:>7.2f} {res['fitness']:>8.4f} {res['inlier_rmse']:>10.6f} "
              f"{res.get('normal_filter_kept', 1.0):>6.3f} "
              f"{res['icp_init']:>14} {ang:>8.2f}°")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
