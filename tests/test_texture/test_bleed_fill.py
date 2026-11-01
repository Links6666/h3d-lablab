"""tests.test_texture.test_bleed_fill : 岛内空洞填充不能越过岛边界。

背景（2026-11-01）
------------------
真机 M4 的 `island_fill_ratio` = 87.64% —— 岛内还有 **12.4% 的纹素没着色**
（裁图里那些黑色空洞）。X3 的 9 处图斑全落在这些空洞边缘，所以「异常」里有一
大半是洞的边界，不是文物病害。

`_bleed` 已经是「迭代四邻域均值扩散」，填洞本可以做——但它的 `iterations=pad`
（默认 3）只够做岛**外缘**的 gutter，够不到岛内部；而不限范围地加迭代会把
**岛外空白**也一起填满（那不是表面，填了等于伪造纹理）。

所以给它加 `limit`：只允许在给定掩膜内扩散。岛外的 gutter 保持原样（pad 次），
岛内的洞可以多填几百像素直到填满（受 `hole_px` 上限约束）。
"""
from __future__ import annotations

import numpy as np
import pytest

from h3d.photo_texture import _bleed


def _canvas(size=41, hole_r=0):
    """中心一块 9×9 着色（红），四周全黑；hole_r>0 时在着色区中心挖一个方洞。"""
    img = np.zeros((size, size, 3), np.float32)
    c = size // 2
    painted = np.zeros((size, size), bool)
    painted[c - 4:c + 5, c - 4:c + 5] = True
    img[painted] = [1.0, 0.0, 0.0]
    if hole_r:
        painted[c - hole_r:c + hole_r + 1, c - hole_r:c + hole_r + 1] = False
    return img, painted, c


class TestLimit:
    def test_fill_stops_at_limit(self):
        """limit 外的像素一个都不能改。"""
        img, painted, c = _canvas()
        island = np.zeros_like(painted)
        island[c - 6:c + 7, c - 6:c + 7] = True       # 岛比着色区大一圈
        out, m = _bleed(img, painted, iterations=99, limit=island)

        assert out[0, 0].sum() == 0, "limit 外的角落被填了 —— 那是岛外空白，不是表面"
        assert m[~island].sum() == 0
        assert m[island & ~painted].any(), "limit 内应该有被填的像素"

    def test_without_limit_still_spreads_everywhere(self):
        """不给 limit 时保持原行为（岛外 gutter 会一直扩到边界）。"""
        img, painted, _ = _canvas()
        out, m = _bleed(img, painted, iterations=99)
        assert m.all(), "无 limit 时应填满整张图（旧行为）"

    def test_interior_hole_gets_filled(self):
        """岛内部的洞要被填上颜色（不是留黑）。"""
        img, painted, c = _canvas(hole_r=2)
        island = np.zeros_like(painted)
        island[c - 6:c + 7, c - 6:c + 7] = True
        out, m = _bleed(img, painted, iterations=20, limit=island)

        hole = np.zeros_like(painted)
        hole[c - 2:c + 3, c - 2:c + 3] = True
        assert out[hole].sum() > 0, "岛内空洞仍是黑的"
        assert m[hole].all()

    def test_filled_hole_color_is_neighbourhood(self):
        """填出来的颜色必须来自邻域（红），不能凭空变出别的颜色。"""
        img, painted, c = _canvas(hole_r=1)
        island = np.zeros_like(painted)
        island[c - 6:c + 7, c - 6:c + 7] = True
        out, _ = _bleed(img, painted, iterations=10, limit=island)
        patch = out[c - 1:c + 2, c - 1:c + 2]
        assert patch[..., 0].min() > 0.5      # 红的
        assert patch[..., 1].max() < 0.2      # 不能串绿


class TestBackwardCompat:
    def test_default_call_unchanged(self):
        """不传 limit 的老调用行为不变（pad 次外扩）。"""
        img, painted, c = _canvas()
        before = img.copy()
        out, m = _bleed(img, painted, iterations=3)
        assert int(m.sum() - painted.sum()) > 0
        np.testing.assert_allclose(out[painted], before[painted], rtol=0)
