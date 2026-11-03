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


class TestMultiStartRotation:
    """COLMAP 世界系的朝向与激光扫描仪无关，可以是任意旋转。

    实机 2026-11-03：单初值（单位阵）在真机上中位最近邻 **112%** 对角线（完全
    卡死），而已知正确朝向时只有 2.5%；卡死之后 Umeyama 顺着错误对应把尺度从
    1.0 拉到 0.43，被质量闸门拒 → 对齐永远走不通、覆盖率为 0。

    **为什么没有「单位阵初值必须失败」这条测试**：合成体上造不出来。凸椭球
    无论转多少度，单位阵初值都能自己爬出来（试过 90° 轴向和 47° 非轴向，
    误差都只有 2.5%~3.9%）。真机那 112% 来自**非凸**的造像几何 + 稀疏点的
    分布不均。硬凑一个合成场景去复现，只会得到一个测不出事的测试。
    这里只锁能锁的部分：**不因为初始朝向不同而崩掉**、尺度不越界、指标齐全。
    """

    @staticmethod
    def _ellipsoid(scale=(1.0, 0.55, 0.3), resolution=40):
        mesh = o3d.geometry.TriangleMesh.create_sphere(radius=1.0,
                                                       resolution=resolution)
        v = np.asarray(mesh.vertices) * np.asarray(scale)
        mesh.vertices = o3d.utility.Vector3dVector(v)
        mesh.compute_vertex_normals()
        return mesh

    @staticmethod
    def _surface_points(scale=(1.0, 0.55, 0.3), n=900, seed=0):
        rng = np.random.default_rng(seed)
        v = rng.normal(size=(n, 3))
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        return v * np.asarray(scale)

    @staticmethod
    def _rot():
        """绕 (0.3,0.7,0.5) 转 47°（非轴向，避免与坐标轴巧合对齐）。"""
        ax = np.array([0.3, 0.7, 0.5])
        ax = ax / np.linalg.norm(ax)
        th = np.deg2rad(47.0)
        K = np.array([[0.0, -ax[2], ax[1]],
                      [ax[2], 0.0, -ax[0]],
                      [-ax[1], ax[0], 0.0]])
        return np.eye(3) + np.sin(th) * K + (1 - np.cos(th)) * (K @ K)

    def test_recovers_when_axes_are_not_aligned(self):
        """点云相对网格转了 47° 并平移：必须仍能对齐且贴合。"""
        from scipy.spatial import cKDTree

        mesh = self._ellipsoid()
        pts = self._surface_points()
        src = pts @ self._rot().T + np.array([4.0, -2.0, 1.0])

        s, R, T, m = align_points_to_mesh(src, mesh)
        assert m["accepted"] is True, m
        moved = s * (src @ R.T) + T
        surf = np.asarray(mesh.sample_points_uniformly(6000).points)
        d = cKDTree(surf).query(moved, k=1)[0]
        # 阈值 0.08：椭球最短半轴 0.3，真机级别的贴合（对角 2% 上下）在这里
        # 够不着——稀疏点均匀铺在光滑曲面上，ICP 收敛后中位误差约 5%。
        assert float(np.median(d)) < 0.08, float(np.median(d))

    def test_returned_transform_reproduces_the_working_set(self):
        """返回的 (s,R,T) 必须能复现内部算出的 work。

        2026-11-03 真踩过：`_icp_similarity_once` 把初值旋转 R0 用在了点上，
        却没折进累积的 (s,R,T)，于是「选出来的最优候选」按返回的变换算回去
        比单位阵初值还差。合成数据上看不出来（凸体都能收敛），只有把两者
        逐点比对才暴露。
        """
        from h3d.geo_align import _icp_similarity_once, _robust_diag

        mesh = self._ellipsoid()
        pts = self._surface_points()
        src = pts @ self._rot().T + np.array([2.0, 3.0, -1.0])
        surf = np.asarray(mesh.sample_points_uniformly(4000).points)
        s0 = _robust_diag(surf) / _robust_diag(src)
        C, M = src.mean(0), surf.mean(0)
        centred = (src - C) * s0 + M

        cand = _icp_similarity_once(mesh, centred, self._rot(), 4000, 6, 90.0)
        reproduced = cand["s"] * (centred @ cand["R"].T) + cand["T"]
        err = float(np.abs(reproduced - cand["work"]).max())
        assert err < 1e-6, f"返回变换复现不出 work，最大偏差 {err:.3e}"

    def test_scale_is_clamped_near_its_initial_estimate(self):
        """尺度不许脱离初值带——那是塌陷，不是估计。"""
        from h3d.geo_align import _SCALE_BAND

        mesh = self._ellipsoid()
        pts = self._surface_points(seed=1)
        src = pts @ self._rot().T + np.array([2.0, 1.0, -4.0])

        _, _, _, m = align_points_to_mesh(src, mesh)
        lo, hi = _SCALE_BAND
        ratio = m["scale_s"] / m["scale_init"]
        assert lo - 1e-6 <= ratio <= hi + 1e-6, (m, ratio)

    def test_metrics_report_scale_init(self):
        """`scale_s` 与 `scale_init` 必须并列可见：两者差多少本身就是判据。"""
        mesh = self._ellipsoid()
        pts = self._surface_points(seed=2)
        src = pts + np.array([0.5, 0.5, 0.5])

        _, _, _, m = align_points_to_mesh(src, mesh)
        assert "scale_init" in m and "scale_s" in m
        assert m["scale_init"] > 0
