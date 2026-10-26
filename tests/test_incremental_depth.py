"""tests.test_incremental_depth : 自研增量深度图（论文创新点原型）单元测试。"""
from __future__ import annotations

import numpy as np

from h3d.incremental_depth import depth_from_stereo, incremental_points


def _synth_stereo(disp=20, size=(128, 160)):
    rng = np.random.default_rng(0)
    H, W = size
    ref = rng.random((H, W))
    tgt = np.roll(ref, -disp, axis=1)  # 相机右移 -> 内容左移
    return (np.stack([ref] * 3, -1), np.stack([tgt] * 3, -1))


class TestDepthFromStereo:
    def test_recovers_known_depth(self):
        """已知深度 3.0 的合成立体对 → 恢复深度误差 <2%。"""
        H, W = 128, 160
        fx = 200.0
        K = np.array([[fx, 0, W / 2], [0, fx, H / 2], [0, 0, 1.]])
        b, z = 0.3, 3.0
        disp = int(fx * b / z)
        ref3, tgt3 = _synth_stereo(disp)
        depth = depth_from_stereo(ref3, tgt3, K, np.eye(3),
                                  np.array([b, 0, 0]),
                                  depth_range=(2.0, 5.0), n_disp=64,
                                  window=3, step=4)
        assert depth is not None
        n = (depth > 0).sum()
        assert n > 100
        med = np.median(depth[depth > 0])
        assert abs(med - z) / z < 0.02

    def test_baseline_zero_returns_none(self):
        H, W = 64, 80
        fx = 200.0
        K = np.array([[fx, 0, W / 2], [0, fx, H / 2], [0, 0, 1.]])
        ref3 = np.zeros((H, W, 3))
        out = depth_from_stereo(ref3, ref3, K, np.eye(3),
                                np.zeros(3))
        assert out is None  # 基线 0 → 无法匹配

    def test_incremental_points_backproject(self):
        H, W = 64, 80
        fx = 200.0
        K = np.array([[fx, 0, W / 2], [0, fx, H / 2], [0, 0, 1.]])
        depth = np.full((H, W), 3.0)
        depth[:20, :20] = 0.0  # 部分无效
        R = np.eye(3)
        t = np.array([0.3, 0, 0])
        pts = incremental_points(depth, K, R, t, step=4)
        assert len(pts) > 50
        # 深度 3.0 的反投影点 z 分量 ≈ 3.0（相机前向）
        assert np.abs(pts[:, 2] - 3.0).mean() < 0.5


class TestDepthFull:
    def test_full_perspective_pair(self):
        """完整版（极线+金字塔）：带透视合成立体对恢复深度。"""
        H, W = 96, 128
        fx = 200.0
        K = np.array([[fx, 0, W / 2], [0, fx, H / 2], [0, 0, 1.]])
        rng = np.random.default_rng(0)
        ref = rng.random((H, W))
        theta = np.radians(3)
        R = np.array([[np.cos(theta), 0, np.sin(theta)],
                      [0, 1, 0], [-np.sin(theta), 0, np.cos(theta)]])
        t = np.array([0.3, 0, 0])
        tgt = np.zeros_like(ref)
        Kinv = np.linalg.inv(K)
        for vy in range(H):
            for vx in range(W):
                Xc = 3.0 * (Kinv @ np.array([vx, vy, 1.0]))
                Xw = R.T @ (Xc - t)
                if Xw[2] <= 0:
                    continue
                u = fx * Xw[0] / Xw[2] + K[0, 2]
                v = fx * Xw[1] / Xw[2] + K[1, 2]
                ui, vi = int(u), int(v)
                if 0 <= ui < W and 0 <= vi < H:
                    tgt[vy, vx] = ref[vi, ui]
        from h3d.incremental_depth import depth_from_stereo_full
        depth = depth_from_stereo_full(
            np.stack([ref] * 3, -1), np.stack([tgt] * 3, -1),
            K, np.eye(3), np.zeros(3), R, t,
            depth_range=(2.0, 5.0), n_levels=2, n_candidates=16,
            window=3, step=8)
        assert depth is not None and (depth > 0).sum() > 50
        med = np.median(depth[depth > 0])
        assert abs(med - 3.0) / 3.0 < 0.05

    def test_consistency_ratio(self):
        from h3d.incremental_depth import depth_consistency_ratio
        d = np.zeros((32, 32))
        d[5:25, 5:25] = 3.0
        m = np.ones((32, 32), dtype=bool)
        r = depth_consistency_ratio(d, m)
        assert 0.3 < r < 0.8


class TestMergeFused:
    def test_merge_increments(self, tmp_path):
        """深度图增量合并：fused 点数增加且落盘。"""
        import os
        import open3d as o3d
        fused = os.path.join(str(tmp_path), "fused_dense.ply")
        old = o3d.geometry.PointCloud()
        old.points = o3d.utility.Vector3dVector(np.random.rand(50, 3))
        o3d.io.write_point_cloud(fused, old)
        H, W = 32, 40
        K = np.array([[200., 0, W / 2], [0, 200., H / 2], [0, 0, 1.]])
        depth = np.zeros((H, W))
        depth[8:24, 8:32] = 3.0
        from h3d.incremental_depth import merge_depth_into_fused
        r = merge_depth_into_fused(fused, depth, K, np.eye(3),
                                   np.zeros(3), voxel=0.0)
        assert r["n_added"] > 10
        assert r["fused_points_after"] > r["fused_points_before"]
        assert os.path.exists(fused)


def _perspective_pair(H=96, W=128, theta_deg=3.0, b=0.3, seed=0):
    """构造带透视合成立体对（深度 3.0 平面）。"""
    fx = 200.0
    K = np.array([[fx, 0, W / 2], [0, fx, H / 2], [0, 0, 1.]])
    rng = np.random.default_rng(seed)
    ref = rng.random((H, W))
    theta = np.radians(theta_deg)
    R = np.array([[np.cos(theta), 0, np.sin(theta)],
                  [0, 1, 0], [-np.sin(theta), 0, np.cos(theta)]])
    t = np.array([b, 0, 0])
    tgt = np.zeros_like(ref)
    Kinv = np.linalg.inv(K)
    for vy in range(H):
        for vx in range(W):
            Xc = 3.0 * (Kinv @ np.array([vx, vy, 1.0]))
            Xw = R.T @ (Xc - t)
            if Xw[2] <= 0:
                continue
            u = fx * Xw[0] / Xw[2] + K[0, 2]
            v = fx * Xw[1] / Xw[2] + K[1, 2]
            ui, vi = int(u), int(v)
            if 0 <= ui < W and 0 <= vi < H:
                tgt[vy, vx] = ref[vi, ui]
    return (np.stack([ref] * 3, -1), np.stack([tgt] * 3, -1),
            K, np.eye(3), np.zeros(3), R, t)


class TestConsistentMultiview:
    def test_consistent_keeps_accuracy(self):
        """双向一致性：保留点深度仍准确（误差 <5%）。"""
        ref3, tgt3, K, Rr, tr, Rt, tt = _perspective_pair()
        from h3d.incremental_depth import depth_from_stereo_consistent
        d = depth_from_stereo_consistent(
            ref3, tgt3, K, Rr, tr, Rt, tt,
            depth_range=(2.0, 5.0), n_levels=1, n_candidates=16,
            window=3, step=8)
        assert d is not None and (d > 0).sum() > 20
        med = np.median(d[d > 0])
        assert abs(med - 3.0) / 3.0 < 0.05

    def test_multiview_fusion(self):
        """多视角融合（2 参考）：有效点非空且深度准确。"""
        ref3, tgt3, K, Rr, tr, Rt, tt = _perspective_pair()
        ref_b, tgt_b, K2, Rr2, tr2, Rt2, tt2 = _perspective_pair(
            theta_deg=-3.0, b=0.25, seed=1)
        from h3d.incremental_depth import depth_from_stereo_multiview
        d = depth_from_stereo_multiview(
            tgt3, [ref3, ref_b], K, Rt, tt,
            [(Rr, tr), (Rr2, tr2)],
            depth_range=(2.0, 5.0), n_candidates=16, window=3, step=8)
        assert d is not None and (d > 0).sum() > 20
        med = np.median(d[d > 0])
        assert abs(med - 3.0) / 3.0 < 0.06


class TestDepthGpu:
    def test_gpu_matches_accuracy(self):
        """GPU 深度：结果与真值一致（cupy 可用时）。"""
        try:
            import cupy  # noqa: F401
        except ImportError:
            import pytest
            pytest.skip("cupy 未安装")
        ref3, tgt3, K, Rr, tr, Rt, tt = _perspective_pair(H=64, W=80)
        from h3d.incremental_depth import depth_from_stereo_gpu
        d = depth_from_stereo_gpu(ref3, tgt3, K, Rt, tt,
                                  depth_range=(2.0, 5.0), n_candidates=16,
                                  window=3, step=6)
        assert d is not None and (d > 0).sum() > 10
        med = np.median(d[d > 0])
        assert abs(med - 3.0) / 3.0 < 0.1


class TestConsistencyGpu:
    def test_vectorized_check(self):
        """一致性校验向量化：与逐点逻辑一致（保留正确掩码）。"""
        H, W = 64, 80
        fx = 200.0
        K = np.array([[fx, 0, W / 2], [0, fx, H / 2], [0, 0, 1.]])
        d_f = np.zeros((H, W))
        d_b = np.zeros((H, W))
        d_f[10:40, 10:60] = 3.0  # 正向区域
        d_b[10:40, 10:60] = 3.0  # 反向一致
        d_b[10:20, 10:30] = 8.0  # 不一致区域（应剔除）
        from h3d.incremental_depth import consistency_check_gpu
        keep = consistency_check_gpu(d_f, d_b, K, np.eye(3), np.zeros(3),
                                     np.eye(3), np.zeros(3), tol_ratio=0.15)
        assert keep[20:40, 30:60].all()  # 一致区保留
        assert not keep[10:20, 10:30].any()  # 不一致区剔除


class TestGpuPatched:
    def test_patched_accuracy(self):
        """批量 patch 提取版：深度恢复正确（cupy 可用时）。"""
        try:
            import cupy  # noqa: F401
        except ImportError:
            import pytest
            pytest.skip("cupy 未安装")
        ref3, tgt3, K, Rr, tr, Rt, tt = _perspective_pair(H=64, W=80)
        from h3d.incremental_depth import depth_from_stereo_gpu_patched
        d = depth_from_stereo_gpu_patched(ref3, tgt3, K, Rt, tt,
                                          depth_range=(2.0, 5.0),
                                          n_candidates=16, window=3, step=6)
        assert d is not None and (d > 0).sum() > 10
        med = np.median(d[d > 0])
        assert abs(med - 3.0) / 3.0 < 0.1
