"""tests.test_mesh.test_postprocess : 网格后处理与度量单元测试。"""
from __future__ import annotations

import numpy as np
import open3d as o3d
import pytest

from h3d.mesh import (
    fill_holes,
    hausdorff_distance,
    mesh_metrics,
    simplify_mesh,
)


def _sphere_mesh():
    """带凹凸的球面网格（作为简化 / 度量的基准）。"""
    pcd = o3d.geometry.TriangleMesh.create_sphere(radius=1.0, resolution=80)
    pcd.compute_vertex_normals()
    return pcd


class TestSimplify:
    def test_reduces_to_target(self):
        mesh = _sphere_mesh()
        target = 200
        simplified = simplify_mesh(mesh, target)
        assert len(simplified.triangles) <= target + 10  # QEM 不超目标过多
        assert len(simplified.triangles) < len(mesh.triangles)

    def test_noop_when_target_larger(self):
        mesh = _sphere_mesh()
        out = simplify_mesh(mesh, len(mesh.triangles) + 1000)
        assert out is mesh  # 原样返回同一对象

    def test_noop_when_target_zero(self):
        mesh = _sphere_mesh()
        assert simplify_mesh(mesh, 0) is mesh


class TestHausdorff:
    def test_same_mesh_near_zero(self):
        """同一网格两次独立采样间的 Hausdorff 距离应为采样间隙量级（远小于几何尺度）。

        教学版 18.2 的采样估计法对同网格也会因采样间隙产生小量伪误差，
        100k 采样时约为球半径（1.0）的 2~4%，此处以 <5% 为稳健上界。
        """
        mesh = _sphere_mesh()
        d = hausdorff_distance(mesh, mesh, samples=100000)
        assert d < 0.05  # 球半径 1.0，采样间隙 < 5%

    def test_simplified_mesh_small_error(self):
        """简化后与原始网格的 Hausdorff 距离应较小（QEM 保形）。

        用 80 分辨率的稠密球面（约 6.5k 面）简化到 1000 面，5 万采样点，
        误差约为半径的 5% 量级（教学版 18.1「简化在保形与省存储间取舍」）。
        """
        mesh = _sphere_mesh()
        simplified = simplify_mesh(mesh, 1000)
        d = hausdorff_distance(mesh, simplified, samples=50000)
        assert d < 0.08  # 球半径 1.0，简化误差 < 8%


class TestMeshMetrics:
    def test_counts(self):
        mesh = _sphere_mesh()
        m = mesh_metrics(mesh)
        assert m["n_vertices"] == len(mesh.vertices)
        assert m["n_triangles"] == len(mesh.triangles)
        assert "watertight" in m

    def test_extra_fields(self):
        m = mesh_metrics(_sphere_mesh(), n_input_points=999)
        assert m["n_input_points"] == 999


class TestFillHoles:
    def test_fill_holes_roundtrip(self):
        """trimesh 补洞往返（vertex/faces 索引正确、可构建 open3d 网格）。"""
        mesh = o3d.geometry.TriangleMesh.create_box(1.0, 1.0, 1.0)
        patched = fill_holes(mesh)
        assert len(patched.vertices) > 0
        assert len(patched.triangles) > 0
        assert patched.has_vertex_normals()
