"""tests.test_texture.test_uv : UV 图集纹理（xatlas 展开 + 烘焙）单元测试。"""
from __future__ import annotations

import os

import numpy as np
import open3d as o3d
import pytest

from h3d.texture import run
from h3d.texture.uv import (
    attach_texture,
    bake_texture,
    export_textured,
    unwrap_mesh,
)


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


class TestUnwrapMesh:
    def test_unwrap_produces_uv(self):
        mesh = _sphere_mesh()
        new_mesh, uv, faces = unwrap_mesh(mesh)
        assert uv.shape[1] == 2
        assert uv.min() >= 0.0 and uv.max() <= 1.0
        assert len(faces) == len(mesh.triangles)
        assert len(new_mesh.vertices) == len(uv)

    def test_unwrap_preserves_colors(self):
        mesh = _sphere_mesh()
        mesh.paint_uniform_color([0.5, 0.6, 0.7])
        new_mesh, _uv, _faces = unwrap_mesh(mesh)
        assert new_mesh.has_vertex_colors()
        np.testing.assert_allclose(
            np.asarray(new_mesh.vertex_colors)[0], [0.5, 0.6, 0.7], atol=1e-4)

    def test_unwrap_empty_raises(self):
        from h3d.core import InputDataError
        with pytest.raises(InputDataError):
            unwrap_mesh(o3d.geometry.TriangleMesh())


class TestBakeTexture:
    def test_bake_returns_rgb_image(self):
        mesh = _sphere_mesh()
        new_mesh, uv, faces = unwrap_mesh(mesh)
        ref = _colored_ref()
        img = bake_texture(new_mesh, uv, ref, size=128, samples_per_tri=4)
        assert img.shape == (128, 128, 3)
        assert img.dtype == np.uint8
        # 球面 UV 覆盖应留下相当比例非黑像素
        assert (img.sum(axis=2) > 10).mean() > 0.2

    def test_bake_requires_colored(self):
        mesh = _sphere_mesh()
        new_mesh, uv, _faces = unwrap_mesh(mesh)
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(np.random.rand(100, 3))
        with pytest.raises(ValueError):
            bake_texture(new_mesh, uv, pcd, size=64)


class TestExportTextured:
    def test_exports_obj_glb(self, tmp_path):
        mesh = _sphere_mesh(resolution=25)
        new_mesh, uv, faces = unwrap_mesh(mesh)
        img = bake_texture(new_mesh, uv, _colored_ref(), size=64, samples_per_tri=2)
        attach_texture(new_mesh, uv, faces, img)
        files = export_textured(new_mesh, str(tmp_path), "r", "L1",
                                ["obj", "glb"])
        assert len(files) == 2
        for f in files:
            assert os.path.exists(f)
        # OBJ 带纹理可读回
        obj = o3d.io.read_triangle_mesh([f for f in files if f.endswith(".obj")][0],
                                        enable_post_processing=True)
        assert obj.has_textures()

    def test_ply_skipped_with_warning(self, tmp_path):
        mesh = _sphere_mesh(resolution=20)
        new_mesh, uv, faces = unwrap_mesh(mesh)
        img = bake_texture(new_mesh, uv, _colored_ref(), size=64, samples_per_tri=2)
        attach_texture(new_mesh, uv, faces, img)
        files = export_textured(new_mesh, str(tmp_path), "r", "L1", ["ply"])
        assert files == []  # PLY 不支持纹理 -> 跳过


class TestRunUvMode:
    def test_uv_mode_bakes_texture(self, tmp_path):
        mesh = _sphere_mesh(resolution=25)
        ref = _colored_ref()
        from h3d.core import TextureConfig
        cfg = TextureConfig(paint_mode="uv", export_formats=["ply", "obj"],
                            lod_levels=[1.0])
        outputs, metrics = run(mesh, ref, cfg, str(tmp_path), "r1")
        assert metrics["uv_baked"] is True
        names = {os.path.basename(f) for f in outputs}
        assert "r1_L1.0.obj" in names  # 带纹理 OBJ
        assert "r1_L1.0.ply" in names  # 顶点色回退

    def test_uv_mode_degrades_without_color(self, tmp_path):
        mesh = _sphere_mesh(resolution=20)
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(np.random.rand(200, 3))
        from h3d.core import TextureConfig
        cfg = TextureConfig(paint_mode="uv", export_formats=["ply"],
                            lod_levels=[1.0])
        _outputs, metrics = run(mesh, pcd, cfg, str(tmp_path), "r2")
        assert metrics["uv_baked"] is False  # 降级顶点色路径
