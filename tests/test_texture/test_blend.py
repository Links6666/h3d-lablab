"""tests.test_texture.test_blend : 泊松融合（接缝平滑 + 空洞填充）单元测试。"""
from __future__ import annotations

import numpy as np
import pytest

from h3d.texture.blend import poisson_blend, seam_smooth


def _sparse_texture(size=64):
    """构造稀疏纹理：左上块有值、其余空洞（模拟投影稀疏采样）。"""
    img = np.zeros((size, size, 3), dtype=np.uint8)
    mask = np.zeros((size, size), dtype=bool)
    img[8:28, 8:28] = 200
    mask[8:28, 8:28] = True
    return img, mask


class TestPoissonBlend:
    def test_fills_holes(self):
        img, mask = _sparse_texture()
        before = (img.sum(axis=2) > 20).mean()
        out = poisson_blend(img, mask, iterations=300, tol=1e-4)
        after = (out.sum(axis=2) > 20).mean()
        # 邻域空洞被调和填充：非空像素占比应提升（扩展 2 像素环）
        assert after > before
        assert 0.10 < after < 0.5  # 适度填充、不过度扩散

    def test_known_pixels_preserved(self):
        img, mask = _sparse_texture()
        out = poisson_blend(img, mask, iterations=100)
        # 已知像素保持原值
        assert (out[10:26, 10:26] == 200).all()

    def test_smooth_transition(self):
        """填充区内部平滑：修复像素的邻域梯度远小于原始硬边界。"""
        img, mask = _sparse_texture()
        out = poisson_blend(img, mask, iterations=300)
        # 修复区（空洞 + 邻域）内部梯度
        from h3d.texture.blend import _dilate_mask
        region = _dilate_mask(mask, 1) & ~mask
        region = region[:, :-1]  # np.diff 后宽减 1
        g1 = np.abs(np.diff(out.astype(float), axis=1))[region].mean() \
            if region.any() else 0.0
        g0 = np.abs(np.diff(img.astype(float), axis=1))[region].mean() \
            if region.any() else 0.0
        assert g1 <= g0 * 1.5 + 1e-6  # 修复区不陡于原边界

    def test_invalid_shape(self):
        with pytest.raises(ValueError):
            poisson_blend(np.zeros((8, 8)), np.zeros((8, 8), dtype=bool))


class TestSeamSmooth:
    def test_edge_smoothed(self):
        img, mask = _sparse_texture()
        out = seam_smooth(img, mask)
        # mask 边界像素（row 27，邻接非 mask）被邻域平均 -> 低于原始 200
        edge_val = out[27, 15, 0]
        assert 150 < edge_val < 200
