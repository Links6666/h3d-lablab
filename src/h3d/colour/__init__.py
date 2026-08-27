"""h3d.colour : 色彩量化（X2 色差评估，可选模块，L2 业务层）。

设计依据：
    《设计书》§20 / §39 —— 基于 CIEDE2000 评估建模纹理与实采色卡色彩保真度，
        为彩塑造像的色彩管理提供量化依据；
    《软著方案》2.4 —— colour 后端 colour-science（colourchecker=ColorChecker24，
        illuminant=D65，delta_e_method=CIE2000）。

本模块为<b>旁路增强</b>（cfg.colour.enabled 默认 false），独立于主线调用：输入为
实测色块 sRGB（建模纹理中提取的 ColorChecker 色块）与参考 sRGB（色卡标准值），
输出逐色块 ΔE、均值 / 最大 / JND 比例与分级统计。

健壮性防护（与 X1 相同的排查结论）：
    1. colour-science 0.4.x 的色差函数在 ``colour.difference`` 子模块
       （顶层 delta_E_CIE2000 不存在）——已按 0.4.7 适配；
    2. 形状校验：实测 / 参考色块必须同为 (n,3)；
    3. 输入钳位：sRGB 越界值（<0 或 >1）先钳位，防 Lab 变换奇异值。

典型用法::

    from h3d.colour import run
    from h3d.core import ColourConfig
    from h3d.colour.color_calib import COLORCHECKER24_SRGB

    result = run(measured_patches, COLORCHECKER24_SRGB,
                 ColourConfig(use_calibration=True, jnd_threshold=2.3))
"""

from __future__ import annotations

from typing import Any

import numpy as np

from h3d.core import ColourConfig, get_logger

from .delta_e import batch_delta_e, classify_delta_e, rgb_to_lab
from .color_calib import (
    COLORCHECKER24_SRGB,
    apply_calibration,
    fit_linear_calibration,
)

log = get_logger("colour")


def run(measured_patches, reference_patches=None, cfg: ColourConfig | None = None) -> dict[str, Any]:
    """色差评估流水线（X2 色彩量化）。

    流程：形状校验 -> 输入钳位 ->（可选）线性校准 -> RGB->Lab -> 逐色块 ΔE
    -> 统计（均值 / 最大 / 最小）-> JND 与分级。

    参数
    ----
    measured_patches:   实测 sRGB 色块（(n,3)），如建模纹理中提取的色卡色块。
    reference_patches:  参考 sRGB 色块（(n,3)）；省略时用内建 ColorChecker24
                        标准值（需 n == 24）。
    cfg:                ColourConfig（use_calibration / delta_e_method /
                        jnd_threshold）。

    返回
    ----
    dict：per_patch_de / mean_de / max_de / min_de / grades / jnd_ratio /
          calibrated（是否应用了校准）/ n_patches。
    """
    cfg = cfg or ColourConfig()
    measured = _prep(measured_patches, "measured")
    reference = _prep(reference_patches, "reference")
    if reference is None:
        reference = COLORCHECKER24_SRGB
        if len(measured) != len(COLORCHECKER24_SRGB):
            raise ValueError(
                f"实测色块 {len(measured)} 个与内建 ColorChecker24（24 个）不匹配；"
                "请显式传入 reference_patches")

    if measured.shape != reference.shape:
        raise ValueError(
            f"色块形状不匹配: measured={measured.shape} reference={reference.shape}（需 (n,3)）")

    # （可选）线性校准：实测 -> 参考色彩空间（消除光照 / 相机响应差异）
    calibrated = False
    if cfg.use_calibration:
        try:
            matrix, offset = fit_linear_calibration(measured, reference)
            measured = apply_calibration(measured, matrix, offset)
            calibrated = True
        except Exception as exc:  # 校准失败不阻断，降级原始色差
            log.warning("色卡校准失败（%s），降级为未校准色差", exc)

    stats = batch_delta_e(reference, measured, cfg.delta_e_method)
    grades = classify_delta_e(stats["per_patch_de"], cfg.jnd_threshold)

    result = {
        "per_patch_de": stats["per_patch_de"],
        "mean_de": stats["mean_de"],
        "max_de": stats["max_de"],
        "min_de": stats["min_de"],
        "grades": grades["grades"],
        "jnd_ratio": grades["jnd_ratio"],
        "above_jnd": grades["above_jnd"],
        "calibrated": calibrated,
        "method": cfg.delta_e_method,
        "n_patches": stats["n_patches"],
    }
    log.info("X2 色差完成：mean_ΔE=%.2f max_ΔE=%.2f JND 超标 %.1f%%（校准=%s）",
             result["mean_de"], result["max_de"],
             result["jnd_ratio"] * 100, calibrated)
    return result


def _prep(patches, label: str):
    """形状校验 + 输入钳位（防 Lab 变换奇异值）。"""
    if patches is None:
        return None
    arr = np.asarray(patches, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise ValueError(f"{label} 色块形状非法: {arr.shape}（需 (n,3)）")
    if arr.size and (arr.min() < 0.0 or arr.max() > 1.0):
        log.warning("%s 色块存在越界值 [%.2f, %.2f]，已钳位到 [0,1]",
                    label, arr.min(), arr.max())
        arr = np.clip(arr, 0.0, 1.0)
    return arr


__all__ = [
    "batch_delta_e",
    "classify_delta_e",
    "rgb_to_lab",
    "fit_linear_calibration",
    "apply_calibration",
    "COLORCHECKER24_SRGB",
    "run",
]
