"""tests.test_geo_align : 控制点 ICP 坐标系对齐（创新点 2）单元测试。"""
from __future__ import annotations

import numpy as np
import open3d as o3d
import pytest

from h3d.geo_align import (
    align_points_to_mesh,
    apply_similarity,
    umeyama_similarity,
)


def _sphere_mesh(radius=1.0, resolution=30):
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=radius,
                                                   resolution=resolution)
    mesh.compute_vertex_normals()
    return mesh


class TestUmeyama:
    def test_identity(self):
        src = np.random.default_rng(0).normal(size=(50, 3))
        s, R, T = umeyama_similarity(src, src)
        assert s == pytest.approx(1.0, abs=1e-8)
        np.testing.assert_allclose(R, np.eye(3), atol=1e-8)
        np.testing.assert_allclose(T, np.zeros(3), atol=1e-8)

    def test_known_transform(self):
        """已知相似变换（含尺度/旋转/平移）可精确恢复。"""
        rng = np.random.default_rng(1)
        src = rng.normal(size=(100, 3))
        s_gt, R_gt, T_gt = 2.5, np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]]), \
            np.array([10.0, -5.0, 3.0])
        dst = apply_similarity(src, s_gt, R_gt, T_gt)
        s, R, T = umeyama_similarity(src, dst)
        assert s == pytest.approx(s_gt, rel=1e-6)
        np.testing.assert_allclose(R, R_gt, atol=1e-6)
        np.testing.assert_allclose(T, T_gt, atol=1e-6)

    def test_noise_robust(self):
        rng = np.random.default_rng(2)
        src = rng.normal(size=(200, 3))
        s_gt, R_gt, T_gt = 0.5, np.eye(3), np.array([1.0, 1.0, 1.0])
        dst = apply_similarity(src, s_gt, R_gt, T_gt) + \
            rng.normal(0, 0.01, (200, 3))
        s, R, T = umeyama_similarity(src, dst)
        assert s == pytest.approx(s_gt, rel=0.05)


class TestAlignPointsToMesh:
    def test_align_scale_recovered(self):
        """SfM 任意尺度点云 ↔ 网格表面对齐（模拟尺度差 10 倍）。"""
        mesh = _sphere_mesh()
        rng = np.random.default_rng(3)
        # 真值表面点（采样自网格，带尺度放大 10 倍 + 平移 + 旋转）
        surface = mesh.sample_points_uniformly(2000)
        pts_gt = np.asarray(surface.points)
        s_gt, T_gt = 10.0, np.array([5.0, -3.0, 2.0])
        R_gt = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
        # 源点云（SfM 系）= 真值点的逆变换
        src = (R_gt.T @ (pts_gt - T_gt).T).T / s_gt
        src = src[rng.integers(0, len(src), 300)]

        s, R, T, metrics = align_points_to_mesh(src, mesh)
        # 变换后的源点应贴合网格表面（残差小）
        assert s == pytest.approx(s_gt, rel=0.1)
        assert metrics["n_correspondences"] > 100
        assert metrics["rmse_after"] < 0.05  # 单位半径球 -> 残差 < 5cm 量级

    def test_apply_similarity(self):
        pts = np.random.default_rng(4).normal(size=(10, 3))
        out = apply_similarity(pts, 2.0, np.eye(3), np.array([1, 2, 3]))
        np.testing.assert_allclose(out, pts * 2.0 + np.array([1, 2, 3]))
