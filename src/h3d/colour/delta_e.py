"""h3d.colour.delta_e : CIEDE2000 色差计算与分级（X2 色彩量化，可选）。

设计依据：
    《设计书》§20 / §39 —— 基于 colour-science 计算 CIEDE2000 色差，
        评估建模纹理与实采色卡的色彩保真度；
    《软著方案》2.4 —— colour 后端 colour-science，delta_e_method=CIE2000；
    ISO/CIE 分级约定：ΔE < 2 不可感知 / 2-4 轻微 / 4-8 明显 / > 8 很大。

实现要点：
    1. colour-science 0.4.x 的色差函数位于 ``colour.difference`` 子模块
       （顶层 ``colour.delta_E_CIE2000`` 不存在），已按 0.4.7 适配。
    2. RGB(sRGB, D65) -> XYZ -> Lab 用 colour-science 标准变换链，
       避免手写矩阵（可复现，软著「可量化」原则）。
    3. 批处理向量化：多色块一次调用（colour-science 支持数组输入）。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from h3d.core import get_logger

log = get_logger("colour.delta_e")


def rgb_to_lab(rgb) -> np.ndarray:
    """sRGB（0~1，D65）-> CIELAB。"""
    import colour

    xyz = colour.sRGB_to_XYZ(np.asarray(rgb, dtype=np.float64))
    return np.asarray(colour.XYZ_to_Lab(xyz), dtype=np.float64)


def delta_e(lab1, lab2, method: str = "CIE2000") -> np.ndarray:
    """批量色差计算（CIELAB 输入），支持单值 / 数组。

    参数
    ----
    lab1 / lab2: CIELAB 颜色（(n,3) 或 (3,)）。
    method:      CIE2000 / CIE1976 / CIE1994。

    返回
    ----
    逐色块 ΔE（与输入同形状）。
    """
    import colour.difference as diff

    a = np.asarray(lab1, dtype=np.float64)
    b = np.asarray(lab2, dtype=np.float64)
    if method == "CIE2000":
        fn = diff.delta_E_CIE2000
    elif method == "CIE1976":
        fn = diff.delta_E_CIE1976
    elif method == "CIE1994":
        fn = lambda x, y: diff.delta_E_CIE1994(x, y, textiles=False)
    else:  # pragma: no cover - 配置层 choices 已拦截
        raise ValueError(f"未知色差方法 {method!r}")
    return np.asarray(fn(a, b), dtype=np.float64)


def batch_delta_e(rgb_ref, rgb_test, method: str = "CIE2000") -> dict[str, Any]:
    """sRGB 参考 vs 实测批量色差 + 统计（设计书 §39.2 逐色块 DE + 均值）。

    参数
    ----
    rgb_ref:  参考 sRGB（(n,3)，如色卡标准值）。
    rgb_test: 实测 sRGB（(n,3)，如建模纹理提取的色块）。

    返回
    ----
    dict：per_patch_de / mean_de / max_de / min_de / n_patches。
    """
    ref, test = np.asarray(rgb_ref, dtype=np.float64), np.asarray(rgb_test, dtype=np.float64)
    if ref.shape != test.shape or ref.ndim != 2 or ref.shape[1] != 3:
        raise ValueError(f"色块形状不匹配: ref={ref.shape} test={test.shape}（需 (n,3)）")
    lab_ref = rgb_to_lab(ref)
    lab_test = rgb_to_lab(test)
    de = delta_e(lab_ref, lab_test, method)
    return {
        "per_patch_de": de,
        "mean_de": float(np.mean(de)),
        "max_de": float(np.max(de)),
        "min_de": float(np.min(de)),
        "n_patches": int(len(de)),
        "method": method,
    }


# ΔE 分级（CIE 约定，单位与 CIE2000 一致）
_GRADE_BOUNDS = [
    ("≤2 不可感知", lambda v: v < 2.0),
    ("2-4 轻微", lambda v: (v >= 2.0) & (v < 4.0)),
    ("4-8 明显", lambda v: (v >= 4.0) & (v < 8.0)),
    (">8 很大", lambda v: v >= 8.0),
]


def classify_delta_e(values, jnd_threshold: float = 2.3) -> dict[str, Any]:
    """ΔE 分级统计 + JND（恰可察觉差）比例。

    参数
    ----
    values:       逐色块 ΔE 数组。
    jnd_threshold: JND 阈值（CIE2000 单位，默认 2.3）。

    返回
    ----
    dict：grades（分级 -> 色块数）/ jnd_ratio（ΔE > 阈值占比）/
          above_jnd / n_patches。
    """
    v = np.asarray(values, dtype=np.float64)
    n = len(v)
    grades = {name: int(mask(v).sum()) for name, mask in _GRADE_BOUNDS}
    above = int((v > jnd_threshold).sum())
    return {
        "grades": grades,
        "jnd_ratio": float(above / n) if n else 0.0,
        "above_jnd": above,
        "n_patches": n,
    }


__all__ = ["rgb_to_lab", "delta_e", "batch_delta_e", "classify_delta_e"]
