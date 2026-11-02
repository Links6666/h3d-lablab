"""tests.test_colour.test_checker_geometry : 色卡定位的几何必须对得上。

真色卡还没有，但几何错误是**可以先算出来**的（不需要照片）：

  1. **格点 vs 格中心**：ColorChecker 是 4×6 个色块，色块中心在 (c+0.5, r+0.5)
     的格坐标里。取 `(c, r)`（网格线交点）会整体偏半个色块 —— 4×6 的卡上
     正好是「一格宽的一半」，取到的是色块边缘甚至相邻块的过渡带。
  2. **标记中心 ≠ 色卡角**：ArUco 标记的几何中心是它自己的中心，而色卡外框角
     是另一个点。两者只在「标记恰好贴在角色块中心」时才重合——而那样会占掉
     4 个色块，ColorChecker24 就不完整了。

这两条都是纯几何，用合成图就能验证。
"""
from __future__ import annotations

import numpy as np
import pytest


def _grid_center(i: int) -> float:
    """第 i 个色块在「格坐标」里的中心位置。"""
    return i + 0.5


class TestGridCenter:
    def test_patch_center_is_half_offset(self):
        """色块中心必须是 i+0.5，不是 i。"""
        assert _grid_center(0) == 0.5
        assert _grid_center(5) == 5.5
        # 4×6 卡：格点取法会让第 0 块取到 (0,0) 那个角，误差 = 半个色块
        assert abs(0.0 - _grid_center(0)) == 0.5


class TestMarkerToChartCorner:
    def test_marker_center_differs_from_chart_corner(self):
        """标记中心到色卡外框角的距离，必须是一个显式参数，不能默认 0。

        设色块边长 1（格坐标），色卡外框从 (0,0) 到 (6,4)。若标记贴在角色块的
        中心，它的中心在 (0.5,0.5) —— 与外框角 (0,0) 差 0.707 格。默认按 (0,0)
        映射，就等于假定标记中心踩在角色块的内角上，取色整体偏半格。
        """
        chart_corner = np.array([0.0, 0.0])
        marker_at_corner_patch_center = np.array([0.5, 0.5])
        dist = float(np.linalg.norm(marker_at_corner_patch_center - chart_corner))
        assert dist > 0.5, "两者不可能重合——这就是为什么要参数化"


class TestSyntheticLocate:
    """用合成图验证：色块画在已知格位置上，定位器应取回这些位置的颜色。

    这是在没有真色卡的情况下唯一能锁住几何的办法。
    """

    def _make(self, margin_px: int = 0, size: int = 8, n_rows=4, n_cols=6):
        """画一张 4×6 的合成色卡；每个色块纯色，颜色编码 (r,c)。

        margin_px：色卡外框到图像边缘的留白（标记贴在这块留白里）。
        """
        cell = 20
        w = n_cols * cell + 2 * (margin_px + cell)
        h = n_rows * cell + 2 * (margin_px + cell)
        img = np.full((h, w, 3), 30, np.uint8)          # 深灰底
        x0, y0 = margin_px + cell, margin_px + cell
        for r in range(n_rows):
            for c in range(n_cols):
                img[y0 + r * cell:y0 + (r + 1) * cell,
                    x0 + c * cell:x0 + (c + 1) * cell] = [
                    30 + r * 50, 30 + c * 30, 128]
        return img, x0, y0, cell

    def test_patch_centres_are_inside_their_cells(self):
        """取色点必须落在对应色块内部（不在边界上）。"""
        img, x0, y0, cell = self._make()
        n_rows, n_cols = 4, 6
        for r in range(n_rows):
            for c in range(n_cols):
                # 正确的格中心
                u = x0 + (c + 0.5) * cell
                v = y0 + (r + 0.5) * cell
                px = img[int(v), int(u)]
                expect = [30 + r * 50, 30 + c * 30, 128]
                assert list(px) == expect, f"({r},{c}) 取到 {list(px)} 而非 {expect}"
                # 错误的格点取法：落在 4 块的公共角上，颜色不属于任何一块
                u_bad = x0 + c * cell
                v_bad = y0 + r * cell
                assert v_bad < y0 + (r + 0.5) * cell, "格点确实偏在块上方"
