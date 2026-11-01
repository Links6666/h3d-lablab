"""tests.test_change.test_change_sensitivity : 已知形变标定的回归锁（X1）。

锁的是 2026-11-01 合成标定查出来的三件事，任何一条回退都必须变红：

1. **尺度**：注入 5mm 外移必须报出 5mm（误差 ≤0.5mm）。旧实现这条是过的，
   所以真正的新增保护在后两条。
2. **符号**：外移（材料增加 = 堆积）必须报**正**。旧实现逐档报 -1.000 /
   -2.004 / -5.003 / -10.002 / -20.010——绝对值精确、符号全反，而
   ``detect_changes`` 正拿符号定「堆积 / 风化」。
3. **侧壁**：法向水平（真机兵马俑躯干形态）也要能测。``py4dgeo`` 的
   ``orientation_vector`` 默认 ``[0,0,1]``，侧壁上照默认会把圆柱斜穿物体。

判读用**中位数**不是 ``max_change_mm``：max 由一两个边缘核心点决定，把它
当成「尺度对不对」的证据会把噪声当信号。
"""
from __future__ import annotations

import numpy as np
import open3d as o3d
import pytest

from h3d.change import run as change_run
from h3d.change.m3c2_wrapper import _orient_outward
from h3d.core import ChangeConfig


def _sphere(n: int, radius: float) -> np.ndarray:
    """Fibonacci 球面采样（法向 = 径向，覆盖「不朝 Z」的方向）。"""
    i = np.arange(n, dtype=np.float64) + 0.5
    phi = np.arccos(1.0 - 2.0 * i / n)
    theta = np.pi * (1.0 + 5.0 ** 0.5) * i
    return radius * np.stack([np.cos(theta) * np.sin(phi),
                              np.sin(theta) * np.sin(phi),
                              np.cos(phi)], axis=1)


def _cylinder(n: int, radius: float, height: float) -> np.ndarray:
    """圆柱侧壁采样（法向水平）。"""
    i = np.arange(n, dtype=np.float64) + 0.5
    theta = np.pi * (1.0 + 5.0 ** 0.5) * i
    z = ((i / n) - 0.5) * height
    return np.stack([radius * np.cos(theta), radius * np.sin(theta), z], axis=1)


def _pair(shape="sphere", n=30000, radius=0.10, height=0.30, delta=0.0):
    base = _cylinder(n, radius, height) if shape == "cylinder" else _sphere(n, radius)
    unit = base / np.linalg.norm(base, axis=1, keepdims=True)
    return base, base + unit * delta


def _pcd(pts):
    p = o3d.geometry.PointCloud()
    p.points = o3d.utility.Vector3dVector(np.asarray(pts, np.float64))
    return p


def _cfg(grid=(20, 20)):
    return ChangeConfig(cores=2, change_mm=2.0, grid=list(grid))


def _stats(result):
    d = np.asarray(result["m3c2"]["distances"], np.float64) * 1000.0
    d = d[~np.isnan(d)]
    assert len(d) > 0, "全部核心点都是 NaN —— 尺度参数把圆柱打空了"
    return float(np.median(d)), float(result["m3c2"]["mps_m"]) * 1000.0


def _median_mm(result):
    return _stats(result)[0]


class TestKnownDeformation:
    @pytest.mark.parametrize("delta_mm", [2.0, 5.0, 10.0])
    def test_outward_shift_measures_positive(self, delta_mm):
        """沿法向外移 = 材料增加 = 堆积，报数必须为正且等于注入量。"""
        t1, t2 = _pair(delta=delta_mm / 1000.0)
        med = _median_mm(change_run(_pcd(t1), _pcd(t2), _cfg()))
        assert med > 0, f"符号反了：外移 {delta_mm}mm 报成 {med:.3f}mm"
        assert abs(med - delta_mm) <= max(0.5, 0.15 * delta_mm), \
            f"注入 {delta_mm}mm 报出 {med:.3f}mm"

    def test_cylinder_side_wall(self):
        """侧壁（法向水平）：默认 orientation_vector=[0,0,1] 会在这里翻车。

        容差按 MPS 表达，不是硬编码：M3C2 的法向搜索半径是 ``10×MPS``，曲面
        上这么大的邻域会把法向「拉平」一点，投影距离随曲率偏小。R=100mm、
        MPS≈2.5mm 时实测偏 4.0%，量级 ≈0.4×MPS——这是方法固有偏差，
        与「尺度错一半」「单位混了 mm/m」那种错不是一个量级。
        """
        t1, t2 = _pair(shape="cylinder", delta=0.005)
        med, mps = _stats(change_run(_pcd(t1), _pcd(t2), _cfg()))
        tol = max(0.75, 0.6 * mps)
        assert med > 0, f"符号反了：侧壁外移 5mm 报成 {med:.3f}mm"
        assert abs(med - 5.0) <= tol, \
            f"侧壁注入 5mm 报出 {med:.3f}mm（MPS={mps:.2f}mm，容差 {tol:.2f}）"

    def test_same_epoch_reports_zero(self):
        """同源两期必须报 0（这是判断「输入对不对」的基线）。"""
        t1, _ = _pair()
        med = _median_mm(change_run(_pcd(t1), _pcd(t1), _cfg()))
        assert abs(med) < 0.1, f"同源两期报出 {med:.3f}mm"

    def test_inward_shift_is_negative(self):
        """内移（风化/侵蚀）报负——符号语义本身也要锁，否则改朝外改过头。"""
        t1, t2 = _pair(delta=-0.005)
        med = _median_mm(change_run(_pcd(t1), _pcd(t2), _cfg()))
        assert med < 0, f"内移报成 {med:.3f}mm，应为负"
        assert abs(med + 5.0) <= 0.75, f"内移 5mm 报出 {med:.3f}mm"


class TestOrientOutward:
    def test_flips_inward_normals(self):
        """整片法向朝内时应整体翻成朝外（球面：法向 = 径向）。"""
        pts = _sphere(400, 0.05)
        inward = -pts / np.linalg.norm(pts, axis=1, keepdims=True)
        out = _orient_outward(pts, inward)
        radial = pts / np.linalg.norm(pts, axis=1, keepdims=True)
        # 绝大多数应与径向同向（逐点允许个别凹处例外，整体必须翻正）
        agree = np.einsum("ij,ij->i", out, radial)
        assert np.mean(agree > 0) > 0.95, \
            f"朝外比例 {np.mean(agree > 0):.2f}，定向没生效"

    def test_keeps_outward_normals(self):
        """已经朝外的不许翻过去。"""
        pts = _sphere(400, 0.05)
        outward = pts / np.linalg.norm(pts, axis=1, keepdims=True)
        out = _orient_outward(pts, outward)
        radial = pts / np.linalg.norm(pts, axis=1, keepdims=True)
        assert np.mean(np.einsum("ij,ij->i", out, radial) > 0) > 0.95

    def test_too_few_points_is_noop(self):
        """点太少（邻域不足）时原样返回，不硬猜方向。"""
        pts = _sphere(6, 0.05)
        normals = -pts / np.linalg.norm(pts, axis=1, keepdims=True)
        out = _orient_outward(pts, normals)
        assert np.allclose(out, normals)
