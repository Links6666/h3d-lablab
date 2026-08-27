"""tests.test_colour : X2 色彩量化单元测试。"""
from __future__ import annotations

import numpy as np
import pytest

from h3d.colour import batch_delta_e, classify_delta_e, run
from h3d.colour.color_calib import (
    COLORCHECKER24_SRGB,
    apply_calibration,
    fit_linear_calibration,
)
from h3d.colour.delta_e import rgb_to_lab
from h3d.core import ColourConfig


class TestDeltaE:
    def test_identical_colors_zero(self):
        lab = rgb_to_lab(np.array([0.5, 0.4, 0.3]))
        de = batch_delta_e([[0.5, 0.4, 0.3]], [[0.5, 0.4, 0.3]])["mean_de"]
        assert de == pytest.approx(0.0, abs=1e-6)

    def test_different_colors_positive(self):
        de = batch_delta_e([[0.5, 0.4, 0.3]], [[0.9, 0.2, 0.1]])["mean_de"]
        assert de > 5.0

    def test_methods_consistent(self):
        rgb = [[0.6, 0.3, 0.2]]
        de2000 = batch_delta_e(rgb, [[0.4, 0.5, 0.3]], "CIE2000")["mean_de"]
        de1976 = batch_delta_e(rgb, [[0.4, 0.5, 0.3]], "CIE1976")["mean_de"]
        assert de2000 > 0 and de1976 > 0

    def test_shape_mismatch_raises(self):
        with pytest.raises(ValueError):
            batch_delta_e([[0.5, 0.4, 0.3]], [[0.5, 0.4]])

    def test_lab_output_shape(self):
        lab = rgb_to_lab(np.array([[0.5, 0.4, 0.3], [0.9, 0.1, 0.5]]))
        assert lab.shape == (2, 3)


class TestClassify:
    def test_grades_distribution(self):
        result = classify_delta_e(np.array([1.0, 3.0, 5.0, 10.0, 2.5]), 2.3)
        assert result["grades"]["≤2 不可感知"] == 1
        assert result["grades"]["2-4 轻微"] == 2
        assert result["grades"]["4-8 明显"] == 1
        assert result["grades"][">8 很大"] == 1
        assert result["above_jnd"] == 4  # 3.0/5.0/10.0/2.5 > 2.3
        assert result["jnd_ratio"] == pytest.approx(0.8)

    def test_all_above_jnd(self):
        result = classify_delta_e(np.array([5.0, 6.0]), 2.3)
        assert result["jnd_ratio"] == 1.0


class TestCalibration:
    def test_fit_and_apply(self):
        """校准矩阵应把实测色块映射回参考色块（线性变换可精确还原）。"""
        # 近单位矩阵 + 小偏移：保证钳位不触发（钳位会破坏线性关系）
        matrix = np.array([[1.02, 0.0, 0.0], [0.0, 0.98, 0.0], [0.0, 0.0, 1.01]])
        offset = np.array([0.01, -0.01, 0.005])
        reference = np.clip(COLORCHECKER24_SRGB, 0.08, 0.92)
        # 实测 = 参考经合成变换（不经过 clip 的原始线性结果）
        measured = reference @ matrix + offset
        fit_m, fit_o = fit_linear_calibration(measured, reference)
        restored = apply_calibration(measured, fit_m, fit_o)
        # 还原值与参考之差应极小（最小二乘精确拟合线性关系）
        assert np.max(np.abs(restored - reference)) < 1e-6

    def test_colorchecker_shape(self):
        assert COLORCHECKER24_SRGB.shape == (24, 3)
        assert COLORCHECKER24_SRGB.min() >= 0 and COLORCHECKER24_SRGB.max() <= 1


class TestRunColour:
    def test_run_with_calibration(self):
        rng = np.random.default_rng(0)
        measured = np.clip(COLORCHECKER24_SRGB + rng.normal(0, 0.02, COLORCHECKER24_SRGB.shape), 0, 1)
        result = run(measured, COLORCHECKER24_SRGB,
                     ColourConfig(use_calibration=True, jnd_threshold=2.3))
        assert result["n_patches"] == 24
        assert result["mean_de"] > 0
        assert result["calibrated"] is True
        assert set(result["grades"].keys()) == {"≤2 不可感知", "2-4 轻微", "4-8 明显", ">8 很大"}

    def test_run_without_calibration(self):
        result = run(COLORCHECKER24_SRGB, COLORCHECKER24_SRGB,
                     ColourConfig(use_calibration=False))
        assert result["calibrated"] is False
        assert result["mean_de"] < 0.1

    def test_default_reference_auto(self):
        """省略参考时自动用内建 ColorChecker24（n=24）。"""
        result = run(COLORCHECKER24_SRGB, None, ColourConfig())
        assert result["mean_de"] < 0.1

    def test_patch_count_mismatch_raises(self):
        with pytest.raises(ValueError):
            run([[0.5, 0.5, 0.5]] * 5, None, ColourConfig())  # 5 != 24

    def test_out_of_range_clamped(self):
        """越界 sRGB 值被钳位（不抛异常）。"""
        bad = COLORCHECKER24_SRGB.copy()
        bad[0] = [1.5, -0.2, 0.5]
        result = run(bad, COLORCHECKER24_SRGB, ColourConfig(use_calibration=False))
        assert result["n_patches"] == 24
