"""tests.test_texture.test_lod : 多分辨率 LOD 单元测试。"""
from __future__ import annotations

import numpy as np
import open3d as o3d
import pytest

from h3d.texture import generate_lods


def _sphere_mesh(resolution=50, colored=True):
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=1.0, resolution=resolution)
    if colored:
        mesh.paint_uniform_color([0.8, 0.4, 0.2])
    return mesh


class TestGenerateLods:
    def test_full_resolution_unchanged(self):
        """level=1.0 返回原始网格（同一对象）。"""
        mesh = _sphere_mesh()
        lods = dict(generate_lods(mesh, [1.0, 0.3], 0))
        assert lods["L1"] is mesh
        assert lods["L1"] == mesh

    def test_lower_level_simplified(self):
        """level<1.0 按比例简化，面数减少且保留顶点色。"""
        mesh = _sphere_mesh()
        lods = dict(generate_lods(mesh, [1.0, 0.3], 0))
        assert len(lods["L0.3"].triangles) < len(lods["L1"].triangles)
        assert len(lods["L0.3"].triangles) <= len(mesh.triangles) * 0.4 + 10
        assert lods["L0.3"].has_vertex_colors()  # QEM 简化保留顶点色

    def test_max_vertices_enforced(self):
        """max_vertices 上限：所有 LOD 顶点数不超过上限。"""
        mesh = _sphere_mesh(resolution=80)
        cap = 2000
        lods = dict(generate_lods(mesh, [1.0, 0.3, 0.1], cap))
        # L1.0 原样（可能超上限，由调用方决定是否接受）；其余 LOD 被压到上限内
        assert len(lods["L0.3"].vertices) <= cap
        assert len(lods["L0.1"].vertices) <= cap

    def test_dedup_levels(self):
        """重复 level 自动去重。"""
        mesh = _sphere_mesh()
        lods = dict(generate_lods(mesh, [1.0, 1.0, 0.5], 0))
        assert len(lods) == 2

    def test_empty_mesh_noop(self):
        """空网格：所有 level 原样返回。"""
        mesh = o3d.geometry.TriangleMesh()
        lods = dict(generate_lods(mesh, [1.0, 0.3], 0))
        assert lods["L1"] is mesh and lods["L0.3"] is mesh
