"""定位 M1 内存暴涨点：在真实 677k 稠密点云上逐步跑 remove_background 的每个阶段。

带 4GB 看门狗：一旦 RSS 超过阈值立即打印当前所处阶段并退出，
避免把开发机也拖爆（用户实机已看到 10GB）。
"""
from __future__ import annotations

import os
import sys
import threading
import time

import numpy as np
import psutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PLY = r"E:\兵马俑照片\测试测试111\10_dense\relic_001\fused_dense.ply"
PR = psutil.Process()
STAGE = ["start"]
CAP_GB = 4.0


def rss() -> float:
    return PR.memory_info().rss / 1024 ** 3


def watchdog() -> None:
    while True:
        r = rss()
        if r > CAP_GB:
            print(f"\n!!! RSS {r:.2f}GB > {CAP_GB}GB，当前阶段：{STAGE[0]} —— 中止", flush=True)
            os._exit(9)
        time.sleep(0.5)


def main() -> int:
    import open3d as o3d

    threading.Thread(target=watchdog, daemon=True).start()
    t = time.perf_counter()
    pcd = o3d.io.read_point_cloud(PLY)
    P = np.asarray(pcd.points, dtype=np.float64)
    diag = float(np.linalg.norm(P.max(0) - P.min(0)))
    print(f"load {time.perf_counter()-t:.1f}s  {len(P)} 点  diag={diag:.4f}  rss={rss():.2f}GB",
          flush=True)

    STAGE[0] = "copy"
    t = time.perf_counter()
    work = o3d.geometry.PointCloud(pcd)
    print(f"copy {time.perf_counter()-t:.1f}s rss={rss():.2f}GB", flush=True)

    STAGE[0] = "cluster_dbscan(eps=0.02*diag)"
    t = time.perf_counter()
    labels = np.asarray(work.cluster_dbscan(eps=0.02 * diag, min_points=10))
    print(f"dbscan {time.perf_counter()-t:.1f}s rss={rss():.2f}GB", flush=True)

    valid = labels >= 0
    uniq, counts = np.unique(labels[valid], return_counts=True)
    print(f"簇数 {len(uniq)} 最大簇 {counts.max() if len(counts) else 0} "
          f"({counts.max()/len(P)*100 if len(counts) else 0:.1f}%)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
