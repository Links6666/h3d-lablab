"""h3d.colour.color_calib : 色卡标准化校准流程（X2 色彩量化，可选）。

设计依据：
    《软著方案》2.4 —— colour 后端 colourchecker=ColorChecker24；
    《设计书》§39 —— 评估建模纹理与实采色卡色彩保真度（前提是先把实测色块
        校准到参考色卡的色彩空间，消除光照 / 相机响应差异）。

实现要点：
    1. 内建 ColorChecker Classic 24 色块 sRGB 参考值（BabelColor avg，D65）。
    2. 线性最小二乘校准：实测色块 -> 参考色块，拟合 3x3 矩阵 + 偏移
       （解 12 参数线性方程组，12 个自由度 = 24 色块超定，稳健）。
    3. 应用校正时钳位到 [0, 1]（sRGB 合法域），防越界。
"""

from __future__ import annotations

import numpy as np

from h3d.core import get_logger

log = get_logger("colour.calib")

# ColorChecker Classic 24 色块 sRGB 参考（BabelColor avg，0~1，D65）
COLORCHECKER24_SRGB: np.ndarray = np.array([
    [0.3678, 0.2931, 0.2317],  # 01 dark skin
    [0.6480, 0.4599, 0.3603],  # 02 light skin
    [0.2695, 0.3082, 0.4118],  # 03 blue sky
    [0.3213, 0.3864, 0.2779],  # 04 foliage
    [0.3567, 0.4000, 0.4936],  # 05 blue flower
    [0.3516, 0.5294, 0.4446],  # 06 bluish green
    [0.7149, 0.3945, 0.2268],  # 07 orange
    [0.2321, 0.2833, 0.4916],  # 08 purplish blue
    [0.6282, 0.3372, 0.3545],  # 09 moderate red
    [0.3608, 0.2728, 0.4080],  # 10 purple
    [0.4771, 0.4880, 0.2880],  # 11 yellow green
    [0.6604, 0.5342, 0.2726],  # 12 orange yellow
    [0.1672, 0.2225, 0.5121],  # 13 blue
    [0.2544, 0.4311, 0.3013],  # 14 green
    [0.6024, 0.2413, 0.2321],  # 15 red
    [0.7387, 0.6103, 0.2443],  # 16 yellow
    [0.4900, 0.2766, 0.4317],  # 17 magenta
    [0.2163, 0.4657, 0.4833],  # 18 cyan
    [0.9243, 0.9028, 0.8533],  # 19 white 9.5
    [0.7169, 0.7028, 0.6622],  # 20 neutral 8
    [0.5002, 0.4900, 0.4631],  # 21 neutral 6.5
    [0.3350, 0.3281, 0.3105],  # 22 neutral 5
    [0.1894, 0.1854, 0.1754],  # 23 neutral 3.5
    [0.1033, 0.1013, 0.0955],  # 24 black 2
], dtype=np.float64)


def fit_linear_calibration(measured: np.ndarray, reference: np.ndarray):
    """最小二乘拟合线性颜色校正（3x3 矩阵 + 偏移，共 12 参数）。

    参数
    ----
    measured:  实测 sRGB 色块（(n,3)）。
    reference: 参考 sRGB 色块（(n,3)）。

    返回
    ----
    (matrix, offset)：校正变换 ``rgb_cal = matrix @ rgb + offset``。
    """
    m = np.asarray(measured, dtype=np.float64)
    r = np.asarray(reference, dtype=np.float64)
    if m.shape != r.shape or m.shape[1] != 3:
        raise ValueError(f"校准色块形状不匹配: {m.shape} vs {r.shape}")
    # 设计矩阵：[r, g, b, 1] -> [r', g', b']
    a = np.hstack([m, np.ones((len(m), 1))])          # (n, 4)
    sol, *_ = np.linalg.lstsq(a, r, rcond=None)       # (4, 3)
    matrix = sol[:3]                                   # (3, 3)
    offset = sol[3]                                    # (3,)
    log.info("线性校准拟合完成：%d 色块 -> 3x3 + offset", len(m))
    return matrix, offset


def apply_calibration(rgb: np.ndarray, matrix: np.ndarray,
                      offset: np.ndarray) -> np.ndarray:
    """应用颜色校正并钳位到 sRGB 合法域 [0, 1]。"""
    cal = np.asarray(rgb, dtype=np.float64) @ np.asarray(matrix, dtype=np.float64)
    cal = cal + np.asarray(offset, dtype=np.float64)
    return np.clip(cal, 0.0, 1.0)


__all__ = ["COLORCHECKER24_SRGB", "fit_linear_calibration", "apply_calibration"]
