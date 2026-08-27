"""tests.test_quality.test_metrics : 质检指标单元测试。"""
from __future__ import annotations

import numpy as np
import open3d as o3d
import pytest

from h3d.quality import (
    boundary_ratio,
    estimate_mps,
    qc_rmse,
    surface_coverage,
)


def _sphere_mesh(resolution=60):
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=1.0, resolution=resolution)
    mesh.compute_vertex_normals()
    return mesh


class TestEstimateMps:
    def test_dense_cloud_small_mps(self):
        pcd = _sphere_mesh().sample_points_uniformly(20000)
        mps = estimate_mps(pcd)
        assert 0.005 < mps < 0.05  # 球半径 1 上 2 万点的平均点间距量级

    def test_empty_returns_zero(self):
        assert estimate_mps(o3d.geometry.PointCloud()) == 0.0

    def test_sample_limit(self):
        """超采样上限时自动抽样，仍返回合理 MPS。"""
        pcd = _sphere_mesh(resolution=120).sample_points_uniformly(50000)
        mps = estimate_mps(pcd, sample_limit=5000)
        assert mps > 0


class TestSurfaceCoverage:
    def test_self_cover_near_one(self):
        """网格自身高密度采样作为参考：表面覆盖率应接近 100%。"""
        mesh = _sphere_mesh()
        ref = mesh.sample_points_uniformly(20000)
        cov = surface_coverage(mesh, ref, max_dist=0.05, samples=8000)
        assert cov > 0.95

    def test_far_reference_low_coverage(self):
        """参考点云远离网格时覆盖率趋近 0。"""
        mesh = _sphere_mesh()
        rng = np.random.default_rng(0)
        far = o3d.geometry.PointCloud()
        far.points = o3d.utility.Vector3dVector(
            rng.uniform(-5, 5, (3000, 3)).astype(np.float64))
        cov = surface_coverage(mesh, far, max_dist=0.05, samples=8000)
        assert cov < 0.1

    def test_empty_ref_zero(self):
        mesh = _sphere_mesh()
        assert surface_coverage(mesh, o3d.geometry.PointCloud(), 0.05, 100) == 0.0


class TestQcRmse:
    def test_self_reference_small_rmse(self):
        """网格 vs 自身密采样：RMSE 应为采样间隙量级（远小于几何尺度）。"""
        mesh = _sphere_mesh()
        ref = mesh.sample_points_uniformly(50000)
        rmse = qc_rmse(mesh, ref, samples=10000)
        assert rmse < 30.0  # mm（球半径 1000mm 的 3% 以内）

    def test_far_reference_large_rmse(self):
        mesh = _sphere_mesh()
        rng = np.random.default_rng(0)
        far = o3d.geometry.PointCloud()
        far.points = o3d.utility.Vector3dVector(
            rng.uniform(-5, 5, (3000, 3)).astype(np.float64))
        rmse = qc_rmse(mesh, far, samples=8000)
        assert rmse > 100.0  # mm（比贴合场景高一个数量级）

    def test_empty_nan(self):
        mesh = _sphere_mesh()
        assert np.isnan(qc_rmse(mesh, o3d.geometry.PointCloud(), 100))


class TestBoundaryRatio:
    def test_watertight_sphere_zero(self):
        assert boundary_ratio(_sphere_mesh()) == 0.0

    def test_open_mesh_positive(self):
        """删除部分三角面制造开口：边界边比例 > 0。"""
        mesh = _sphere_mesh(resolution=40)
        tris = np.asarray(mesh.triangles)
        # 删除前 20% 三角面（相邻面被移除制造边界）
        mesh.triangles = o3d.utility.Vector3iVector(tris[len(tris) // 5:])
        assert boundary_ratio(mesh) > 0.0

    def test_empty_mesh_one(self):
        assert boundary_ratio(o3d.geometry.TriangleMesh()) == 1.0
