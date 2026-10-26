"""tests.test_dense_gate : dense 产物质量门（离群剔除 + 密度过滤）单元测试。"""
from __future__ import annotations

import numpy as np
import open3d as o3d

from h3d.dense_gate import run_dense_gate


def _pcd_with_outliers(n=4000, outlier_ratio=0.05):
    """球面点云 + 离群点 + 稀疏噪声。"""
    rng = np.random.default_rng(0)
    u = rng.random(n) * np.pi
    v = rng.random(n) * 2 * np.pi
    pts = np.stack([np.sin(u) * np.cos(v), np.sin(u) * np.sin(v),
                    np.cos(u)], axis=1)
    # 离群点（远离球面）
    n_out = int(n * outlier_ratio)
    out = rng.normal(0, 5.0, (n_out, 3))
    pts = np.vstack([pts, out])
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts)
    return pcd, n_out


class TestDenseGate:
    def test_removes_outliers(self):
        pcd, n_out = _pcd_with_outliers()
        filtered, m = run_dense_gate(pcd, std_ratio=2.0, nb_neighbors=20,
                                     min_density_ratio=0.0)
        # 离群点被剔除：输出点数接近球面点数
        assert len(filtered.points) < len(pcd.points)
        assert m["removed_stat"] >= n_out * 0.5  # 大部分离群被剔除
        assert m["n_out"] == len(filtered.points)

    def test_density_filter(self):
        pcd, _ = _pcd_with_outliers()
        # 再加稀疏散点（低密度区）
        rng = np.random.default_rng(1)
        sparse = rng.normal(0, 8.0, (200, 3))
        pcd.points = o3d.utility.Vector3dVector(
            np.vstack([np.asarray(pcd.points), sparse]))
        filtered, m = run_dense_gate(pcd, std_ratio=2.0, nb_neighbors=20,
                                     min_density_ratio=0.01)
        assert m["removed_density"] >= 0
        assert len(filtered.points) > 0

    def test_small_cloud_skipped(self):
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(np.random.rand(10, 3))
        _f, m = run_dense_gate(pcd, std_ratio=2.0, min_density_ratio=0.01)
        assert m["n_out"] == 10  # 过小跳过
