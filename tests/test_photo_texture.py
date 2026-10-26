"""tests.test_photo_texture : P1 photo_texture 可选 stage 单元测试。"""
from __future__ import annotations

import json
import os

import numpy as np
import open3d as o3d
import pytest

from h3d.core import PipelineConfig
from h3d.runner import STAGE_REGISTRY
from h3d.runner.task import Task, TaskStatus
from h3d.photo_texture import load_capture_meta, project_photos_to_texture
from h3d.texture.uv import unwrap_mesh


def _sphere_mesh(resolution=30):
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=1.0, resolution=resolution)
    mesh.compute_vertex_normals()
    return mesh


def _make_meta(n=4):
    """合成位姿元数据：环绕相机（半径 3.6，朝向原点）。"""
    images = []
    cameras = {1: {"w": 800, "h": 600, "fx": 800.0, "fy": 800.0,
                   "cx": 400.0, "cy": 300.0}}
    for i in range(n):
        az = 2 * np.pi * i / n
        eye = np.array([3.6 * np.cos(az), 3.6 * np.sin(az), 0.3])
        f = -eye / np.linalg.norm(eye)
        up = np.array([0.0, 0.0, 1.0])
        s = np.cross(f, up)
        s = s / np.linalg.norm(s)
        u = np.cross(s, f)
        R = np.stack([s, u, -f], axis=1).T  # 世界->相机
        images.append({"name": f"img_{i:02d}.jpg", "R": R, "t": eye, "cam_id": 1})
    return cameras, images


def _write_photos(photo_dir, n=4, size=(600, 800)):
    from PIL import Image
    os.makedirs(photo_dir, exist_ok=True)
    rng = np.random.default_rng(0)
    for i in range(n):
        img = np.clip(rng.random((size[0], size[1], 3)) * 255, 0, 255).astype(np.uint8)
        Image.fromarray(img).save(os.path.join(photo_dir, f"img_{i:02d}.jpg"))


class TestP1Registered:
    def test_stage_registered(self):
        assert "P1" in STAGE_REGISTRY

    def test_disabled_by_default(self, tmp_path):
        cfg = PipelineConfig()
        task = Task(item_id="r1", raw_paths=[], root=str(tmp_path))
        from h3d.photo_texture import run_photo_texture
        res = run_photo_texture(task, cfg)
        assert res.status == TaskStatus.SKIPPED


class TestProjectPhotos:
    def test_projection_produces_texture(self, tmp_path):
        mesh = _sphere_mesh()
        new_mesh, uv, faces = unwrap_mesh(mesh)
        cameras, images = _make_meta(n=4)
        photo_dir = str(tmp_path / "photos")
        _write_photos(photo_dir, n=4)
        img, metrics = project_photos_to_texture(
            new_mesh, uv, faces, images, cameras, photo_dir,
            size=128, top_k=3, samples_per_tri=4)
        assert img.shape == (128, 128, 3)
        assert metrics["projected_cameras"] >= 2  # 多视角参与
        assert (img.sum(axis=2) > 20).mean() > 0.01  # 有覆盖

    def test_insufficient_photos_raises(self, tmp_path):
        mesh = _sphere_mesh()
        new_mesh, uv, faces = unwrap_mesh(mesh)
        cameras, images = _make_meta(n=1)
        photo_dir = str(tmp_path / "p1")
        _write_photos(photo_dir, n=1)
        with pytest.raises(ValueError):
            project_photos_to_texture(new_mesh, uv, faces, images, cameras,
                                      photo_dir, size=64)


class TestLoadCaptureMeta:
    def test_roundtrip(self, tmp_path):
        cameras, images = _make_meta(n=3)
        from h3d.sfm_solve import write_capture_meta
        out = str(tmp_path / "capture_meta.json")
        write_capture_meta(cameras, images, out)
        cams, imgs = load_capture_meta(out)
        assert len(cams) == 1 and len(imgs) == 3
        assert imgs[0]["R"].shape == (3, 3)
