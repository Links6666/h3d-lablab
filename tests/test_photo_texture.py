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


class TestTextureOverlap:
    """纹理重叠度（设计书 §17.3）：每个着色纹素有**几台**相机参与。

    为什么这个数必须存在
    --------------------
    ``multiview_color_std`` 只说明"融合进去的几台相机意见差多少"，读不出
    "到底有没有第二台相机参与"。整片纹素全靠单相机硬投影时，色差**恒等于 0**
    （只有一台，没有分歧）——那是最该报警的情况，却也是这个指标最漂亮的情况。
    两个数必须配对读。

    这也是 ``quality.run`` 里 ``"overlap": None`` 那条 N/A 的正主：设计书写了
    这个指标，代码一直没算。
    """

    def _project(self, tmp_path, n):
        mesh = _sphere_mesh()
        new_mesh, uv, faces = unwrap_mesh(mesh)
        cameras, images = _make_meta(n=n)
        photo_dir = str(tmp_path / f"p{n}")
        _write_photos(photo_dir, n=n)
        img, metrics = project_photos_to_texture(
            new_mesh, uv, faces, images, cameras, photo_dir,
            size=128, top_k=3, samples_per_tri=4)
        return metrics

    def test_overlap_is_reported(self, tmp_path):
        m = self._project(tmp_path, n=4)
        assert m["texture_overlap"] is not None
        assert 1.0 <= m["texture_overlap"] <= 4.0
        assert 0.0 < m["texture_multi_view_ratio"] <= 1.0

    def test_more_cameras_means_more_overlap(self, tmp_path):
        """单调性：换相机数必须换结果，否则这个键就是一个常数。"""
        few = self._project(tmp_path, n=4)["texture_overlap"]
        many = self._project(tmp_path, n=8)["texture_overlap"]
        assert many > few + 0.5, (few, many)

    def test_two_cameras_cannot_exceed_two(self, tmp_path):
        """上限由相机数决定：2 台相机不可能有 3 台参与。"""
        m = self._project(tmp_path, n=2)
        assert m["texture_overlap"] <= 2.0

    def test_overlap_and_multi_ratio_are_consistent(self, tmp_path):
        """定义必须自洽：均值 ≥ 1 + 「有 ≥2 台」的那部分比例。

        均值 = 1 + (1/n)Σ(c_i − 1)，而 c_i ≥ 2 的纹素每个至少贡献 1，
        所以 overlap ≥ 1 + multi_ratio 恒成立。自洽性断言比拍一个具体数字
        更能挡住"以后改算法改坏了"。
        """
        m = self._project(tmp_path, n=6)
        assert m["texture_overlap"] >= 1.0 + m["texture_multi_view_ratio"] - 1e-9


class TestTextureOverlap:
    """纹理重叠度（设计书 §17.3）：每个着色纹素有**几台**相机参与。

    为什么这个数必须存在
    --------------------
    ``multiview_color_std`` 只说明"融合进去的几台相机意见差多少"，读不出
    "到底有没有第二台相机参与"。整片纹素全靠单相机硬投影时，色差**恒等于 0**
    （只有一台，没有分歧）——那是最该报警的情况，却也是这个指标最漂亮的情况。
    两个数必须配对读。

    这也是 ``quality.run`` 里 ``"overlap": None`` 那条 N/A 的正主：设计书写了
    这个指标，代码一直没算。
    """

    def _project(self, tmp_path, n):
        mesh = _sphere_mesh()
        new_mesh, uv, faces = unwrap_mesh(mesh)
        cameras, images = _make_meta(n=n)
        photo_dir = str(tmp_path / f"p{n}")
        _write_photos(photo_dir, n=n)
        img, metrics = project_photos_to_texture(
            new_mesh, uv, faces, images, cameras, photo_dir,
            size=128, top_k=3, samples_per_tri=4)
        return metrics

    def test_overlap_is_reported(self, tmp_path):
        m = self._project(tmp_path, n=4)
        assert m["texture_overlap"] is not None
        assert 1.0 <= m["texture_overlap"] <= 4.0
        assert 0.0 < m["texture_multi_view_ratio"] <= 1.0

    def test_more_cameras_means_more_overlap(self, tmp_path):
        """单调性：换相机数必须换结果，否则这个键就是一个常数。"""
        few = self._project(tmp_path, n=4)["texture_overlap"]
        many = self._project(tmp_path, n=8)["texture_overlap"]
        assert many > few + 0.5, (few, many)

    def test_two_cameras_cannot_exceed_two(self, tmp_path):
        """上限由相机数决定：2 台相机不可能有 3 台参与。"""
        m = self._project(tmp_path, n=2)
        assert m["texture_overlap"] <= 2.0

    def test_overlap_and_multi_ratio_are_consistent(self, tmp_path):
        """定义必须自洽：均值 ≥ 1 + 「有 ≥2 台」的那部分比例。

        均值 = 1 + (1/n)Σ(c_i − 1)，而 c_i ≥ 2 的纹素每个至少贡献 1，
        所以 overlap ≥ 1 + multi_ratio 恒成立。自洽性断言比拍一个具体数字
        更能挡住"以后改算法改坏了"。
        """
        m = self._project(tmp_path, n=6)
        assert m["texture_overlap"] >= 1.0 + m["texture_multi_view_ratio"] - 1e-9
