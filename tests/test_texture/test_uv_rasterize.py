"""tests.test_texture.test_uv_rasterize : UV 光栅化的口径（utilization 不能重复计数）。

为什么单列一条（2026-11-01，ADR-0009）
-------------------------------------
排查「UV 图集利用率」时发现 `utilization = len(flat)/size²` 用的是**去重前**的
覆盖点（下一行才 `np.unique`）。岛之间无重叠时结果一样，所以这个错藏了很久；
但一旦有重叠（重复面、共边三角形重复光栅化），`utilization` 会**超过 1.0**，
而它正是 `island_fill_ratio = coverage/utilization` 的分母 —— 分母虚高会让人
误判为「岛内没填满」，把排查方向引去法线/遮挡（实际是 UV 打包或重复面）。

利用率是比值，**超过 1 就一定是算错了**，这是最干净的判据。
"""
from __future__ import annotations

import numpy as np
import pytest

from h3d.photo_texture import _rasterize_uv


def _quad(normals=None):
    """一个铺满 UV [0,1]² 的正方形（2 个三角形）。"""
    verts = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], np.float64)
    uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float64)
    faces = np.array([[0, 1, 2], [0, 2, 3]], np.int64)
    nrm = np.tile([0.0, 0.0, 1.0], (4, 1)) if normals is None else normals
    return verts, nrm, uv, faces


class TestUtilization:
    def test_full_uv_is_about_one(self):
        """正方形铺满 UV → 利用率应≈1（不可能超过 1）。"""
        v, n, uv, f = _quad()
        flag, pos, nrm, util = _rasterize_uv(v, n, uv, f, 64)
        assert 0.95 <= util <= 1.0, util

    def test_duplicate_faces_do_not_double_count(self):
        """重复三角形 → 覆盖点翻倍，但利用率必须仍是≈1。"""
        v, n, uv, f = _quad()
        f2 = np.vstack([f, f])          # 同一组三角形画两遍
        flag, pos, nrm, util = _rasterize_uv(v, n, uv, f2, 64)
        assert util <= 1.0 + 1e-9, f"重复面把利用率算成 {util}（>1 一定是重复计数）"
        assert util == pytest.approx(len(np.unique(flag)) / 64 ** 2, rel=1e-9)

    def test_half_uv_is_about_half(self):
        """只占半个 UV（x∈[0,0.5]）→ 利用率≈0.5。"""
        v, n, uv, f = _quad()
        uv = uv.copy()
        uv[:, 0] *= 0.5
        flag, pos, nrm, util = _rasterize_uv(v, n, uv, f, 64)
        assert 0.45 <= util <= 0.55, util

    def test_degenerate_triangle_still_lights_one_texel(self):
        """退化三角形（面积 0）至少点亮一个纹素，否则贴图会出现针孔。"""
        v = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]], np.float64)
        uv = np.array([[0.2, 0.5], [0.2, 0.5], [0.2, 0.5]], np.float64)
        f = np.array([[0, 1, 2]], np.int64)
        n = np.tile([0.0, 0.0, 1.0], (3, 1))
        flag, pos, nrm, util = _rasterize_uv(v, n, uv, f, 32)
        assert len(flag) >= 1
        assert util == pytest.approx(1 / 32 ** 2)
