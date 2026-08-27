"""tests.test_texture.test_export : 多格式导出单元测试。"""
from __future__ import annotations

import os

import numpy as np
import open3d as o3d
import pytest

from h3d.texture import export_colored


def _colored_mesh(resolution=30):
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=1.0, resolution=resolution)
    mesh.paint_uniform_color([0.9, 0.5, 0.1])
    mesh.compute_vertex_normals()
    return mesh


class TestExportColored:
    def test_all_formats_written(self, tmp_path):
        mesh = _colored_mesh()
        out_dir = tmp_path / "50_tex" / "rock001"
        files = export_colored(mesh, str(out_dir), "rock001", "L1.0",
                               ["ply", "obj", "glb"])
        assert len(files) == 3
        for f in files:
            assert os.path.exists(f)
        names = {os.path.basename(f) for f in files}
        assert names == {"rock001_L1.0.ply", "rock001_L1.0.obj", "rock001_L1.0.glb"}

    def test_ply_roundtrip_keeps_colors(self, tmp_path):
        mesh = _colored_mesh()
        files = export_colored(mesh, str(tmp_path), "r", "L1.0", ["ply"])
        reloaded = o3d.io.read_triangle_mesh(files[0])
        assert reloaded.has_vertex_colors()
        # PLY 颜色 8-bit 量化（1/255 ≈ 0.004），容差取 0.01
        np.testing.assert_allclose(
            np.asarray(reloaded.vertex_colors)[0],
            np.asarray(mesh.vertex_colors)[0], atol=0.01)

    def test_obj_contains_vertex_color(self, tmp_path):
        mesh = _colored_mesh()
        files = export_colored(mesh, str(tmp_path), "r", "L1.0", ["obj"])
        text = open(files[0], encoding="utf-8", errors="ignore").read()
        # Open3D OBJ 顶点行含 r g b 扩展：v x y z r g b（7 列）
        assert any(len(line.split()) == 7 and line.startswith("v ")
                   for line in text.splitlines())

    def test_unsupported_format_skipped(self, tmp_path):
        mesh = _colored_mesh()
        files = export_colored(mesh, str(tmp_path), "r", "L1.0",
                               ["ply", "stl", "dae"])
        assert len(files) == 1
        assert files[0].endswith(".ply")

    def test_geometry_without_colors_exports(self, tmp_path):
        """无顶点色网格也可导出（几何模型降级路径）。"""
        mesh = o3d.geometry.TriangleMesh.create_box(1, 1, 1)
        files = export_colored(mesh, str(tmp_path), "r", "L1.0", ["ply"])
        assert len(files) == 1
