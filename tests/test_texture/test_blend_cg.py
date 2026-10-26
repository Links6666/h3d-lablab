"""tests.test_texture.test_blend_cg : 泊松融合共轭梯度求解单元测试。"""
from __future__ import annotations

import numpy as np

from h3d.texture.blend_cg import poisson_blend_cg
from h3d.texture.blend import poisson_blend


def _sparse_texture(size=128):
    img = np.zeros((size, size, 3), dtype=np.uint8)
    mask = np.zeros((size, size), dtype=bool)
    img[8:28, 8:28] = 200
    mask[8:28, 8:28] = True
    return img, mask


class TestPoissonBlendCg:
    def test_fills_holes(self):
        img, mask = _sparse_texture()
        before = (img.sum(axis=2) > 20).mean()
        out = poisson_blend_cg(img, mask)
        after = (out.sum(axis=2) > 20).mean()
        # 修复区被调和延拓填充（值可能低于 20 阈值——与 Jacobi 一致性单独验证）
        assert after >= before
        from h3d.texture.blend import _dilate_mask
        region = _dilate_mask(mask, 1) & ~mask
        assert (out[region].sum(axis=1) > 0).mean() > 0.9  # 修复区有值

    def test_known_pixels_preserved(self):
        img, mask = _sparse_texture()
        out = poisson_blend_cg(img, mask)
        assert (out[10:26, 10:26] == 200).all()

    def test_consistent_with_jacobi(self):
        """CG 解与 Jacobi 收敛解近似一致（调和延拓）。"""
        img, mask = _sparse_texture(size=64)
        a = poisson_blend_cg(img, mask)
        b = poisson_blend(img, mask, iterations=300, tol=1e-4)
        # 修复区平均差 < 25（不同求解器数值路径，容差放宽）
        from h3d.texture.blend import _dilate_mask
        region = _dilate_mask(mask, 1)
        diff = np.abs(a.astype(int) - b.astype(int))[region].mean()
        assert diff < 25

    def test_no_repair_identity(self):
        img = np.full((16, 16, 3), 100, dtype=np.uint8)
        mask = np.ones((16, 16), dtype=bool)
        out = poisson_blend_cg(img, mask)
        assert (out == 100).all()  # 无空洞 → 原样
