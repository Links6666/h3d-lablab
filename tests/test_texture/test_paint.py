"""tests.test_texture.test_paint : 顶点色映射单元测试。"""
from __future__ import annotations

import numpy as np
import open3d as o3d
import pytest

from h3d.texture import has_colors, paint_vertex_colors


def _sphere_mesh(resolution=30):
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=1.0, resolution=resolution)
    mesh.compute_vertex_normals()
    return mesh


def _colored_ref(n=2000, seed=0):
    """带 RGB 颜色的参考点云（颜色 = 坐标归一化）。"""
    rng = np.random.default_rng(seed)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(rng.uniform(-1, 1, (n, 3)))
    pcd.colors = o3d.utility.Vector3dVector(
        (np.asarray(pcd.points) + 1.0) / 2.0)  # 0~1
    return pcd


class TestHasColors:
    def test_colored_true(self):
        assert has_colors(_colored_ref()) is True

    def test_plain_false(self):
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(np.random.rand(10, 3))
        assert has_colors(pcd) is False

    def test_empty_false(self):
        assert has_colors(o3d.geometry.PointCloud()) is False


class TestPaintVertexColors:
    def test_paints_mesh(self):
        """网格顶点取最近邻参考点颜色，成功上色且值域合法。"""
        mesh = _sphere_mesh()
        ref = _colored_ref()
        assert paint_vertex_colors(mesh, ref) is True
        assert mesh.has_vertex_colors()
        colors = np.asarray(mesh.vertex_colors)
        assert colors.shape == (len(mesh.vertices), 3)
        assert colors.min() >= 0.0 and colors.max() <= 1.0

    def test_returns_false_when_ref_plain(self):
        """参考点云无颜色 -> 返回 False（不抛异常，由调用方降级）。"""
        mesh = _sphere_mesh()
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(np.random.rand(100, 3))
        assert paint_vertex_colors(mesh, pcd) is False
        assert not mesh.has_vertex_colors()

    def test_returns_false_when_mesh_empty(self):
        ref = _colored_ref()
        assert paint_vertex_colors(o3d.geometry.TriangleMesh(), ref) is False
