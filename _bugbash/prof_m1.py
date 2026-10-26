"""M1 实机耗时定位：逐步计时 + 峰值内存（用真实 fused_dense.ply）。"""
from __future__ import annotations

import os
import sys
import time

import numpy as np
import psutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PLY = r"E:\兵马俑照片\测试测试111\10_dense\relic_001\fused_dense.ply"
PROC = psutil.Process()


def rss() -> str:
    return f"{PROC.memory_info().rss / 1024 ** 3:.2f}GB"


def step(name, fn):
    t = time.perf_counter()
    print(f"[{time.strftime('%H:%M:%S')}] >>> {name} (rss={rss()})", flush=True)
    out = fn()
    dt = time.perf_counter() - t
    n = len(out.points) if hasattr(out, "points") else "-"
    print(f"[{time.strftime('%H:%M:%S')}] <<< {name}: {dt:.1f}s  -> {n} 点  rss={rss()}",
          flush=True)
    return out


def main() -> int:
    from h3d.io import load_point_cloud
    from h3d.preprocess import estimate_normals, statistical_outlier_removal, voxel_down_sample
    from h3d.preprocess.normal_estimate import orient_normals_consistent

    pcd = step("load", lambda: load_point_cloud(PLY))
    pts = np.asarray(pcd.points)
    print("点数", len(pts), "bbox diag",
          float(np.linalg.norm(pts.max(0) - pts.min(0))), flush=True)
    print("颜色", pcd.has_colors(), "法线", pcd.has_normals(), flush=True)

    # 自适应尺度参考：平均点间距（MPS）
    from scipy.spatial import cKDTree
    t = time.perf_counter()
    tree = cKDTree(pts[:20000])
    d, _ = tree.query(pts[:20000], k=2)
    mps = float(np.median(d[:, 1]))
    print(f"MPS(采样2万) = {mps:.5f}  用时 {time.perf_counter()-t:.1f}s", flush=True)

    voxel = 0.02
    pcd = step(f"voxel_down_sample({voxel})", lambda: voxel_down_sample(pcd, voxel))
    pcd = step("statistical_outlier_removal(20, 2.0)",
               lambda: statistical_outlier_removal(pcd, nb_neighbors=20, std_ratio=2.0))
    pcd = step("estimate_normals(radius=0.06, max_nn=20)",
               lambda: estimate_normals(pcd, max_nn=20, radius=voxel * 3.0))
    pcd = step("orient_normals_consistent(k=15)",
               lambda: orient_normals_consistent(pcd, k=15))

    from h3d.preprocess.background import remove_background
    print(f"[{time.strftime('%H:%M:%S')}] >>> remove_background (eps=0.02*diag)", flush=True)
    t = time.perf_counter()
    out, bg = remove_background(pcd, max_planes=0, keep_cluster_frac=0.25)
    print(f"[{time.strftime('%H:%M:%S')}] <<< remove_background: "
          f"{time.perf_counter()-t:.1f}s -> {len(out.points)} 点 rss={rss()}", flush=True)
    print("bg metrics:", bg, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
