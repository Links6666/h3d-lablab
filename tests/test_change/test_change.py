"""tests.test_change : X1 M3C2 多时相形变检测单元测试。"""
from __future__ import annotations

import numpy as np
import open3d as o3d
import pytest

from h3d.change import compute_m3c2, detect_changes, run
from h3d.core import ChangeConfig


def _clouds(n=15000, deformation=0.03, seed=0, nan_ratio=0.0):
    """两期点云：t2 在 t1 上叠加正弦形变（模拟堆积/风化）。"""
    rng = np.random.default_rng(seed)
    pts1 = rng.uniform(0, 1, (n, 3))
    pts2 = pts1.copy()
    pts2[:, 2] += deformation * np.sin(pts1[:, 0] * 5)
    if nan_ratio > 0:  # 污染 t2（模拟坏点）
        mask = rng.random(n) < nan_ratio
        pts2[mask] = np.nan
    c1 = o3d.geometry.PointCloud()
    c1.points = o3d.utility.Vector3dVector(pts1)
    c2 = o3d.geometry.PointCloud()
    c2.points = o3d.utility.Vector3dVector(pts2)
    return c1, c2


def _cfg(**kw):
    base = dict(cores=2, change_mm=5.0, normal_radius_k=8.0,
                cyl_radius_k=3.0, max_distance_k=50.0, grid=[24, 24])
    base.update(kw)
    return ChangeConfig(**base)


class TestComputeM3C2:
    def test_distance_field_shape(self):
        c1, c2 = _clouds()
        result = compute_m3c2(c1, c2, _cfg())
        assert result["distances"].shape == (24 * 24,)
        assert result["uncertainties"].shape == (24 * 24,)
        assert result["corepoints"].shape[1] == 3
        assert result["mps_m"] > 0

    def test_deformed_cloud_detects_distance(self):
        """形变点云：距离场应检测到正弦位移（|Δ| 与变形量同量级）。"""
        c1, c2 = _clouds(deformation=0.03)
        result = compute_m3c2(c1, c2, _cfg())
        dist_mm = np.abs(result["distances"]) * 1000.0
        assert np.nanmax(dist_mm) > 5.0  # 3% 变形远大于 5mm 阈值

    def test_empty_input_raises(self):
        from h3d.core import InputDataError
        c1 = o3d.geometry.PointCloud()
        c2, _ = _clouds()
        with pytest.raises(InputDataError):
            compute_m3c2(c1, c2, _cfg())


class TestDetectChanges:
    def test_threshold_marking(self):
        c1, c2 = _clouds(deformation=0.03)
        result = compute_m3c2(c1, c2, _cfg())
        changes = detect_changes(result, threshold_mm=5.0)
        assert changes["n_changed"] > 0
        assert changes["changed_ratio"] > 0.1
        assert changes["max_change_mm"] > 5.0
        assert len(changes["indices"]) > 0

    def test_high_threshold_marks_fewer(self):
        c1, c2 = _clouds(deformation=0.03)
        result = compute_m3c2(c1, c2, _cfg())
        loose = detect_changes(result, threshold_mm=50.0)
        strict = detect_changes(result, threshold_mm=2.0)
        assert loose["n_changed"] <= strict["n_changed"]

    def test_sample_limit(self):
        c1, c2 = _clouds(deformation=0.03)
        result = compute_m3c2(c1, c2, _cfg())
        changes = detect_changes(result, 1.0, sample_limit=10)
        assert len(changes["indices"]) <= 10


class TestRun:
    def test_run_pipeline(self):
        c1, c2 = _clouds(deformation=0.03)
        result = run(c1, c2, _cfg())
        assert "m3c2" in result and "changes" in result and "metrics" in result
        m = result["metrics"]
        assert m["max_change_mm"] > 5.0
        assert m["n_corepoints"] == 24 * 24

    def test_nan_input_sanitized(self):
        """NaN 坏点被过滤，不崩溃（此前中断的防护验证）。"""
        c1, c2 = _clouds(nan_ratio=0.05)
        result = run(c1, c2, _cfg())
        assert result["metrics"]["n_corepoints"] > 0

    def test_grid_clamped(self):
        """超大网格被自动收缩（内存防护）。"""
        c1, c2 = _clouds(n=8000)
        result = run(c1, c2, _cfg(grid=[500, 500]))
        assert result["metrics"]["n_corepoints"] <= 4096

    def test_identical_clouds_zero_change(self):
        """两期完全一致：变化量接近 0（数值容差内）。"""
        c1, _ = _clouds()
        result = run(c1, c1, _cfg())
        assert result["metrics"]["max_change_mm"] < 0.5
