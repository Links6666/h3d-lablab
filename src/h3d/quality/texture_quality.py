"""h3d.quality.texture_quality : 纹理质量评估（M5 新增维度）。

设计依据：
    《照片导入方案》/《路线 R2》—— M4 photo 模式（照片投影 + 泊松融合）产出
    纹理图后，M5 质检应可量化「纹理清晰度 / 接缝质量 / 覆盖率」三个维度，
    使纹理链路可评估、可回归。

指标（对纹理图的有效像素区域）：
    texture_sharpness  拉普拉斯方差（Variance of Laplacian）——清晰度；
    texture_seam       有效区域边界梯度均值——接缝不连续程度（越小越好）；
    texture_coverage   非空像素占比——纹理覆盖率；
    ok                 清晰度 > sharpness_min 且 接缝 < seam_max 且 覆盖率 > coverage_min。
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np

from h3d.core import get_logger

log = get_logger("quality.texture")


def expected_coverage_min(samples_per_tri: int = 4, n_faces: int = 10000,
                          texture_size: int = 1024) -> float:
    """【已废弃】按「每面 N 采样点」估算期望覆盖率下限。

    旧口径是给"每三角形撒 4 个点"的旧烘焙器量身定做的：它按
    ``samples_per_tri × n_faces / texels`` 推算下限，于是**着色率越低、
    通过越容易**——实测一个 96% 纹素是黑洞的贴图（着色率 4.6%）也能判"通过"。
    现改为按「UV 岛内填充率」判（见 :func:`texture_quality` 的 ``fill_min``），
    本函数仅保留以兼容旧调用。
    """
    tex_pixels = max(texture_size * texture_size, 1)
    expected = max(samples_per_tri, 1) * max(n_faces, 0) / tex_pixels
    return round(expected * 0.20, 6)


def texture_quality(texture_path: Optional[str],
                    sharpness_min: float = 3.0,
                    seam_max: float = 100.0,
                    coverage_min: float = 0.005,
                    mask_path: Optional[str] = None,
                    atlas_utilization: Optional[float] = None,
                    fill_min: float = 0.85) -> dict:
    """评估纹理图质量（无纹理图/文件缺失时返回全 NaN + ok=False 标注）。

    参数
    ----
    texture_path: diffuse 纹理图路径（png/jpg）；None 或缺文件 -> 不可评估。
    sharpness_min / seam_max: 清晰度 / 接缝阈值。
    coverage_min: 图集有效像素占比下限（无掩膜时的兜底口径）。
    mask_path:    M4 落盘的**真实着色掩膜**（``<item>_L1.0_mask.png``）。
                  有掩膜时覆盖率按掩膜算，不再用"亮度 > 20"猜——暗色器物
                  （青铜/漆木，整片亮度 <20）用亮度当掩膜会把好纹理判成没贴图。
    atlas_utilization: UV 图集利用率（岛面积 / 图集面积，由 M4 指标传入）。
                  给出时额外算 ``texture_fill_ratio = 覆盖率 / 利用率``，
                  这才是"UV 岛内到底填了多少"的真口径。
    fill_min:     岛内填充率下限（默认 0.85）：低于此值说明大量纹素没采到颜色
                  （遮挡/位姿/光栅化问题），即使图集整体利用率正常也应判不达标。

    返回
    ----
    dict：texture_available / texture_sharpness / texture_seam /
          texture_coverage / texture_fill_ratio / texture_ok。
    """
    empty = {"texture_available": False, "texture_sharpness": None,
             "texture_seam": None, "texture_coverage": None,
             "texture_fill_ratio": None, "texture_ok": False}
    if not texture_path or not os.path.exists(texture_path):
        return empty
    try:
        from PIL import Image
        img = np.asarray(Image.open(texture_path).convert("L"),
                         dtype=np.float64)
    except Exception as exc:  # noqa: BLE001 - 图像损坏按不可评估处理
        log.warning("纹理图读取失败（按不可评估处理）：%s", exc)
        return empty
    if img.size == 0:
        return empty

    mask = None
    if mask_path and os.path.exists(mask_path):
        try:
            from PIL import Image as _I
            mask = np.asarray(_I.open(mask_path).convert("L")) > 127
            if mask.shape != img.shape:
                log.warning("着色掩膜尺寸 %s 与纹理 %s 不一致，忽略掩膜",
                            mask.shape, img.shape)
                mask = None
        except Exception as exc:  # noqa: BLE001
            log.warning("着色掩膜读取失败（退回亮度阈值）：%s", exc)
            mask = None
    if mask is None:
        mask = img > 20  # 兜底：有效像素（暗色器物会低估，故优先用掩膜）
        mask_source = "luma"
    else:
        mask_source = "mask"
    if not mask.any():
        # 空/全暗纹理：可评估但覆盖 0（避免 None 传播 TypeError）
        return {**empty, "texture_available": True, "texture_sharpness": 0.0,
                "texture_seam": 0.0, "texture_coverage": 0.0,
                "texture_fill_ratio": 0.0, "mask_source": mask_source}
    # 清晰度：有效区拉普拉斯方差
    lap = _laplacian(img)
    sharpness = float(lap[mask].var()) if mask.any() else 0.0
    # 接缝：有效区边界梯度均值（边界=邻接非有效像素）
    grad = np.hypot(np.gradient(img)[0], np.gradient(img)[1])
    from h3d.texture.blend import _dilate_mask
    edge = mask & _dilate_mask(~mask, 1)
    seam = float(grad[edge].mean()) if edge.any() else 0.0
    coverage = float(mask.mean())
    fill_ratio = None
    if atlas_utilization and atlas_utilization > 0:
        fill_ratio = min(coverage / float(atlas_utilization), 1.0)

    ok = (sharpness > sharpness_min and seam < seam_max)
    if fill_ratio is not None:
        ok = ok and fill_ratio >= fill_min
    else:
        ok = ok and coverage > coverage_min
    return {
        "texture_available": True,
        "texture_sharpness": round(sharpness, 4),
        "texture_seam": round(seam, 4),
        "texture_coverage": round(coverage, 4),
        "texture_fill_ratio": (round(fill_ratio, 4) if fill_ratio is not None else None),
        "texture_ok": bool(ok),
        "mask_source": mask_source,
    }


def _laplacian(img: np.ndarray) -> np.ndarray:
    """3x3 拉普拉斯核卷积（numpy 实现，避免依赖 scipy 滤波）。"""
    k = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)
    pad = np.pad(img, 1, mode="reflect")
    out = np.zeros_like(img)
    for i in range(3):
        for j in range(3):
            if k[i, j] != 0:
                out += k[i, j] * pad[i:i + img.shape[0], j:j + img.shape[1]]
    return out


__all__ = ["texture_quality"]
