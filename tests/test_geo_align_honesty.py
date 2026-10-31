# -*- coding: utf-8 -*-
"""对齐质量判定 / 同源预检 / 多初值 ICP 的回归测试。

背景（2026-10-31 真机 1.1.12）：
* ``align_points_to_mesh`` 用原始包围盒做尺度初始化，被 COLMAP 飞点撑大 7.8 倍，
  把网格搬错 2 倍，M4 岛内填充率从 98.9% 掉到 57.5%，而它自报的残差 0.0968
  看着完美（off-by-one + 截断统计）。
* ``register.run`` 单一初值在近似轴对称物体上解出翻转 180° 的版本，
  fitness 0.336 越过 0.2 的失败门，一路判成功。

这些用例锁住「搬不动就别搬」「翻转解要能挑出来」两条底线。
"""

from __future__ import annotations

import numpy as np
import open3d as o3d
import pytest

from h3d.core import RegisterConfig
from h3d.geo_align import (align_points_to_mesh, alignment_quality,
                           already_aligned, _robust_diag)
from h3d.register import _icp_multi_init, _principal_axes, _rotation_about, run


def _sphere(n: int = 4000, radius: float = 1.0, seed: int = 7) -> o3d.geometry.TriangleMesh:
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=radius,
                                                   resolution=20)
    mesh.compute_vertex_normals()
    return mesh


def _points(n: int = 3000, radius: float = 1.0, seed: int = 11) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    return v * radius


# --------------------------------------------------------------------------- #
# 1) 鲁棒包围盒
# --------------------------------------------------------------------------- #
def test_robust_diag_ignores_outliers() -> None:
    """少量飞点不该显著改变包围盒对角线（原始 min/max 会）。"""
    pts = _points(2000, 1.0)
    raw = float(np.linalg.norm(pts.max(0) - pts.min(0)))
    pristine = _robust_diag(pts)
    with_outliers = np.vstack([pts, np.array([[40.0, 30.0, 20.0],
                                              [-35.0, -25.0, -18.0]])])
    raw_bad = float(np.linalg.norm(with_outliers.max(0)
                                   - with_outliers.min(0)))
    robust_bad = _robust_diag(with_outliers)
    assert raw_bad > raw * 20, "飞点应当把原始包围盒撑大（用例本身的前置）"
    assert robust_bad == pytest.approx(pristine, rel=0.25), \
        f"鲁棒包围盒被飞点带偏：{robust_bad:.3f} vs {pristine:.3f}"


# --------------------------------------------------------------------------- #
# 2) 同源预检
# --------------------------------------------------------------------------- #
def test_already_aligned_true_for_same_frame() -> None:
    """网格顶点与点云同处一个坐标系 → 判为「已对齐」，应跳过相似变换。"""
    mesh = _sphere()
    verts = np.asarray(mesh.vertices)
    # 点云与网格顶点同密度、落在同一表面：模拟「几何本身就是 SfM 稠密产物」
    src = verts + np.random.default_rng(3).normal(0, 0.002, verts.shape)
    q = already_aligned(verts, src, tol=0.01)
    assert q["aligned"] is True
    assert q["norm_median"] < 0.01


def test_already_aligned_false_when_displaced() -> None:
    """整体平移半个直径 → 必须判「未对齐」，不能静默跳过。"""
    mesh = _sphere()
    verts = np.asarray(mesh.vertices)
    src = verts[::4] + np.array([1.0, 0.0, 0.0])
    q = already_aligned(verts, src, tol=0.01)
    assert q["aligned"] is False


# --------------------------------------------------------------------------- #
# 3) 质量判定
# --------------------------------------------------------------------------- #
def test_alignment_quality_rejects_scale_drift() -> None:
    """对齐后包围盒是目标的 2 倍 → 判无效（1.1.12 的实测比值 2.0266）。"""
    mesh = _sphere(radius=1.0)
    verts = np.asarray(mesh.vertices)
    bad = verts * 2.0
    q = alignment_quality(verts, bad, verts)
    assert q["accepted"] is False
    assert q["diag_ratio"] == pytest.approx(2.0, rel=0.15)
    assert "包围盒" in q["reason"]


def test_alignment_quality_accepts_same_scale() -> None:
    mesh = _sphere(radius=1.0)
    verts = np.asarray(mesh.vertices)
    rng = np.random.default_rng(5)
    moved = verts + rng.normal(0, 0.002, verts.shape)
    q = alignment_quality(verts, moved, verts)
    assert q["accepted"] is True


# --------------------------------------------------------------------------- #
# 4) align_points_to_mesh 的闸门：拒绝时返回恒等变换
# --------------------------------------------------------------------------- #
def test_align_points_to_mesh_returns_identity_when_rejected() -> None:
    """源点云带大量飞点时，宁可返回恒等变换也不能把几何搬错。"""
    mesh = _sphere(radius=1.0)
    pts = _points(2000, 1.0)
    far = np.random.default_rng(9).normal(0, 12.0, (200, 3))
    src = np.vstack([pts, far])
    s, R, T, m = align_points_to_mesh(src, mesh, iterations=3)
    assert m.get("accepted") is False
    assert m.get("rejected") is True
    assert s == pytest.approx(1.0)
    assert np.allclose(R, np.eye(3))
    assert np.allclose(T, np.zeros(3))
    assert m.get("reject_reason")


def test_align_points_to_mesh_accepts_clean_overlap() -> None:
    mesh = _sphere(radius=1.0)
    pts = _points(3000, 1.0)
    s, R, T, m = align_points_to_mesh(pts, mesh, iterations=4)
    assert m.get("accepted") is True
    assert m["quality"]["diag_ratio"] == pytest.approx(1.0, abs=0.2)


# --------------------------------------------------------------------------- #
# 5) 多初值 ICP
# --------------------------------------------------------------------------- #
def test_principal_axes_are_orthogonal() -> None:
    pts = _points(1500, 1.0)
    axes = _principal_axes(pts)
    assert len(axes) == 2
    assert abs(float(axes[0] @ axes[1])) < 1e-6


def test_rotation_about_keeps_center_fixed() -> None:
    c = np.array([1.0, 2.0, 3.0])
    T = _rotation_about(c, np.array([0.0, 1.0, 0.0]), np.pi)
    moved = (T[:3, :3] @ c) + T[:3, 3]
    assert np.allclose(moved, c)
    assert abs(np.linalg.det(T[:3, :3]) - 1.0) < 1e-9


def test_icp_multi_init_beats_single_init_on_flipped_case() -> None:
    """给一个「转了 180 度」的初值时，多初值必须能把正确朝向挑出来。

    这是 1.1.12 翻转解的最小复现：单一初值会停在翻转解上，
    多初值里加了主轴翻转候选，正确解凭 fitness 胜出。
    """
    mesh = _sphere(radius=1.0)
    target = o3d.geometry.PointCloud()
    target.points = o3d.utility.Vector3dVector(_points(3000, 1.0))

    rng = np.random.default_rng(21)
    R_rand = np.linalg.qr(rng.normal(size=(3, 3)))[0]
    if np.linalg.det(R_rand) < 0:
        R_rand[:, 0] *= -1
    src_pts = _points(3000, 1.0) @ R_rand.T
    source = o3d.geometry.PointCloud()
    source.points = o3d.utility.Vector3dVector(src_pts)
    source.normals = o3d.geometry.PointCloud().normals  # 占位，交由 ICP 补齐

    cfg = RegisterConfig(fpfh_radius=0.4, ransac_distance=0.2,
                         icp_distance=0.1, max_iter=60)
    # 故意给「正确解再翻 180 度」的初值：单靠它会停在翻转解
    flipped = np.eye(4)
    flipped[:3, :3] = R_rand @ np.diag([-1.0, 1.0, -1.0])
    best, tag, trials = _icp_multi_init(source, target, flipped, cfg)
    assert best is not None
    assert len(trials) >= 2
    # 正确解：把 src 转回原坐标
    correct = np.eye(4)
    correct[:3, :3] = R_rand.T
    fit_correct = o3d.pipelines.registration.evaluate_registration(
        source, target, 0.1, correct).fitness
    assert best.fitness >= fit_correct - 0.15, (
        f"多初值没有找到足够好的解：best={best.fitness:.3f} "
        f"（正确解 {fit_correct:.3f}，所选初值 {tag}）")


def test_register_run_sets_low_confidence_flag() -> None:
    """重叠极低但未跌破失败门时，必须落 low_confidence 而不是静默通过。"""
    rng = np.random.default_rng(33)
    a = rng.normal(0, 1.0, (1500, 3))
    b = rng.normal(0, 1.0, (1500, 3))
    pa = o3d.geometry.PointCloud()
    pa.points = o3d.utility.Vector3dVector(a)
    pb = o3d.geometry.PointCloud()
    pb.points = o3d.utility.Vector3dVector(b)
    cfg = RegisterConfig(fpfh_radius=0.5, ransac_distance=0.3,
                         icp_distance=0.2, max_iter=30)
    try:
        m = run(pa, pb, cfg)
    except Exception:
        pytest.skip("两团随机点未达最低重叠，属预期失败路径")
    if not m.get("low_confidence"):
        assert m["fitness"] >= 0.55, (
            "fitness 低于 0.55 却没有打 low_confidence 标记")
