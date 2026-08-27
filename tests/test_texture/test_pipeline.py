"""tests.test_texture.test_pipeline : texture.run 纹理化流水线测试。"""
from __future__ import annotations

import os

import numpy as np
import open3d as o3d
import pytest

from h3d.core import InputDataError, TextureConfig
from h3d.texture import run


def _sphere_mesh(resolution=30):
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=1.0, resolution=resolution)
    mesh.compute_vertex_normals()
    return mesh


def _colored_ref(n=3000, seed=0):
    rng = np.random.default_rng(seed)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(rng.uniform(-1, 1, (n, 3)))
    pcd.colors = o3d.utility.Vector3dVector((np.asarray(pcd.points) + 1.0) / 2.0)
    return pcd


class TestRunTexture:
    def test_colored_full_flow(self, tmp_path):
        """带色参考 -> 顶点色映射 + LOD + 多格式导出到 50_tex。"""
        mesh = _sphere_mesh()
        ref = _colored_ref()
        cfg = TextureConfig(export_formats=["ply", "obj", "glb"],
                            lod_levels=[1.0, 0.3])
        out_dir = str(tmp_path / "50_tex" / "rock001")
        outputs, metrics = run(mesh, ref, cfg, out_dir, "rock001")

        assert metrics["colored"] is True
        assert metrics["n_lods"] == 2
        assert metrics["n_outputs"] == 6
        assert len(outputs) == 6
        for f in outputs:
            assert os.path.exists(f)
        # L1.0 的 PLY 带色（tag 格式 :g -> 1.0 生成 "L1"）
        ply = [f for f in outputs if f.endswith("_L1.ply")][0]
        assert o3d.io.read_triangle_mesh(ply).has_vertex_colors()

    def test_no_color_degrade(self, tmp_path):
        """参考无颜色 -> 降级输出几何模型（colored=False，软著方案 2.4）。"""
        mesh = _sphere_mesh()
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(np.random.rand(500, 3))
        cfg = TextureConfig(export_formats=["ply"], lod_levels=[1.0])
        outputs, metrics = run(mesh, pcd, cfg, str(tmp_path), "r")
        assert metrics["colored"] is False
        assert len(outputs) == 1

    def test_uv_mode_degrades_to_vertex(self, tmp_path):
        """paint_mode='uv' 未实装 -> 降级顶点色 + 警告，仍输出带色模型。"""
        mesh = _sphere_mesh()
        ref = _colored_ref()
        cfg = TextureConfig(paint_mode="uv", export_formats=["ply"],
                            lod_levels=[1.0])
        _outputs, metrics = run(mesh, ref, cfg, str(tmp_path), "r")
        assert metrics["colored"] is True  # 降级后仍上色
        assert metrics["paint_mode"] == "uv"

    def test_empty_mesh_raises_e100(self, tmp_path):
        ref = _colored_ref()
        with pytest.raises(InputDataError) as ei:
            run(o3d.geometry.TriangleMesh(), ref, TextureConfig(),
                str(tmp_path), "r")
        assert ei.value.code == "E100"
