"""两阶段对齐的第二阶段（最近邻精化）必须受约束、可复现、可自证。

2026-11-03 真机：M4 走 `control_points + nearest_refine`，成品网格对角 3.27，
而稀疏点稳健对角 6.70（期望尺度 ≈1.07）。精化阶段 5 次迭代**每次都解相似
变换且尺度自由**，一路把物体压到一半；`metrics["scale_s"]` 却是第一阶段的
0.764，**看不出精化改了多少**。同一输入三次运行 coverage 0.6335/0.6538/0.6305
——因为 `sample_points_uniformly` 没定种子。
"""
import numpy as np
import open3d as o3d
import pytest


def _sphere(radius=1.0, resolution=30):
    m = o3d.geometry.TriangleMesh.create_sphere(radius=radius,
                                                resolution=resolution)
    m.compute_vertex_normals()
    return m


def _surface_pts(radius=1.0, n=1500, seed=0, half=False):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    v *= radius
    if half:
        v = v[v[:, 2] > 0]          # 只覆盖上半球：自由尺度会往里塌
    return v


class TestRefineIsConstrained:
    def test_reports_the_scale_it_actually_applied(self):
        """精化必须报出**自己施加**的累积尺度，否则 total = 第一阶段 × 未知。"""
        from h3d.ctrl_points import refine_mesh_to_points

        mesh = _sphere()
        _, m = refine_mesh_to_points(mesh, _surface_pts())
        assert "scale_applied" in m, m
        assert m["scale_applied"] == pytest.approx(1.0, abs=0.05), m

    def test_partial_coverage_does_not_collapse_the_scale(self):
        """只覆盖一部分表面时，自由尺度会往里塌；必须被夹住。"""
        from h3d.ctrl_points import refine_mesh_to_points

        mesh = _sphere()
        pts = _surface_pts(half=True)
        out, m = refine_mesh_to_points(mesh, pts)
        assert m["scale_applied"] == pytest.approx(1.0, abs=0.35), m
        # 几何本身也不能被压小
        d0 = np.linalg.norm(np.ptp(np.asarray(mesh.vertices), axis=0))
        d1 = np.linalg.norm(np.ptp(np.asarray(out.vertices), axis=0))
        assert d1 / d0 > 0.7, (d0, d1)

    def test_same_input_gives_identical_output(self):
        """同一输入两次调用必须逐顶点相同（表面采样要定种子）。"""
        from h3d.ctrl_points import refine_mesh_to_points

        mesh = _sphere()
        pts = _surface_pts()
        a, ma = refine_mesh_to_points(mesh, pts)
        b, mb = refine_mesh_to_points(mesh, pts)
        va, vb = np.asarray(a.vertices), np.asarray(b.vertices)
        assert np.abs(va - vb).max() < 1e-9, (
            f"两次结果不同，最大差 {np.abs(va - vb).max():.3e}")
        assert ma["scale_applied"] == pytest.approx(mb["scale_applied"],
                                                   abs=1e-9)
