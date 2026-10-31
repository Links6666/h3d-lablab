"""UV 布局落盘与读回（B1 切片 2）。

为什么必须落盘
--------------
`project_photos_to_texture` 接受外部 ``uv/faces``，所以「继承」在算法上是现成的。
但 M4 现在只在内存里有 UV，跑完即丢 —— 第二次烘焙只能重新展开，而重展开
是否与上次逐 texel 对齐**没有任何保证**。图集继承的前提因此是：

    基准期的 UV 布局必须作为一个文件存在，并且它的指纹进档案（atlas-v1）。

幂等的意义
----------
GUI「打开项目」与每轮 M4 都可能走到同一个落盘点。若每次都重写，
``uv_digest`` 不变但文件 mtime 变，运维上无法区分「图集被重算了」与「只是又跑了一次」。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _uv(n: int = 5):
    rng = np.random.default_rng(7)
    uv = rng.random((n, 2))
    faces = np.array([[0, 1, 2], [2, 3, 4]], dtype=np.int64)
    return uv, faces


class TestSaveUvLayout:
    def test_writes_npz_and_returns_digest(self, tmp_path):
        from h3d.archive.atlas import save_uv_layout, uv_digest

        uv, faces = _uv()
        p = str(tmp_path / "x_uv.npz")
        info = save_uv_layout(p, uv, faces)
        assert os.path.isfile(p)
        assert info["written"] is True
        assert info["digest"] == uv_digest(uv, faces)
        assert info["vertices"] == len(uv) and info["faces"] == len(faces)

    def test_idempotent_does_not_rewrite(self, tmp_path):
        """同内容再存一次：不得重写（mtime 是判断「图集被重算过」的证据）。"""
        from h3d.archive.atlas import save_uv_layout

        uv, faces = _uv()
        p = str(tmp_path / "x_uv.npz")
        save_uv_layout(p, uv, faces)
        mtime = os.path.getmtime(p)
        info = save_uv_layout(p, uv, faces)
        assert info["written"] is False
        assert os.path.getmtime(p) == mtime, "幂等保存不得触碰文件"

    def test_changed_uv_rewrites(self, tmp_path):
        from h3d.archive.atlas import save_uv_layout

        uv, faces = _uv()
        p = str(tmp_path / "x_uv.npz")
        first = save_uv_layout(p, uv, faces)
        uv2 = uv.copy()
        uv2[0, 0] += 0.01
        second = save_uv_layout(p, uv2, faces)
        assert second["written"] is True
        assert second["digest"] != first["digest"]

    def test_creates_parent_dir(self, tmp_path):
        from h3d.archive.atlas import save_uv_layout

        uv, faces = _uv()
        p = str(tmp_path / "a" / "b" / "x_uv.npz")
        save_uv_layout(p, uv, faces)
        assert os.path.isfile(p)


class TestLoadUvLayout:
    def test_roundtrip_digest_matches(self, tmp_path):
        """写完读回，digest 必须一模一样 —— 否则「落盘→重启→继承」每次都判图集变了。"""
        from h3d.archive.atlas import load_uv_layout, save_uv_layout, uv_digest

        uv, faces = _uv()
        p = str(tmp_path / "x_uv.npz")
        save_uv_layout(p, uv, faces)
        uv2, faces2 = load_uv_layout(p)
        assert uv_digest(uv2, faces2) == uv_digest(uv, faces)

    def test_shapes_preserved(self, tmp_path):
        from h3d.archive.atlas import load_uv_layout, save_uv_layout

        uv, faces = _uv()
        p = str(tmp_path / "x_uv.npz")
        save_uv_layout(p, uv, faces)
        uv2, faces2 = load_uv_layout(p)
        assert uv2.shape == (5, 2) and faces2.shape == (2, 3)
        assert faces2.dtype == np.int64

    def test_missing_file_raises(self, tmp_path):
        from h3d.archive.atlas import load_uv_layout

        with pytest.raises(FileNotFoundError):
            load_uv_layout(str(tmp_path / "nope.npz"))

    def test_corrupt_file_is_quarantined(self, tmp_path):
        """损坏文件改名留证 + 可读异常，不能安静返回空数组。"""
        from h3d.archive.atlas import load_uv_layout

        p = tmp_path / "bad_uv.npz"
        p.write_bytes(b"not a real npz")
        with pytest.raises(ValueError):
            load_uv_layout(str(p))
        assert not p.exists(), "损坏文件应被移走"
        assert list(tmp_path.glob("bad_uv.npz.corrupt-*")), "必须留证"

    def test_missing_key_raises(self, tmp_path):
        from h3d.archive.atlas import load_uv_layout

        p = tmp_path / "half.npz"
        np.savez_compressed(str(p), uv=np.zeros((4, 2)))
        with pytest.raises(ValueError):
            load_uv_layout(str(p))


class TestM4Integration:
    """M4 跑完必须留下 UV 布局 —— 否则 B1 的继承无从谈起。"""

    @staticmethod
    def _project(tmp_path, item="r1"):
        import open3d as o3d

        from h3d.sfm_solve import write_capture_meta

        root = tmp_path / "阶段成果"
        photo = root / "00_photo" / item
        photo.mkdir(parents=True)
        rng = np.random.default_rng(0)
        from PIL import Image

        cameras = {1: {"w": 400, "h": 300, "fx": 400.0, "fy": 400.0,
                       "cx": 200.0, "cy": 150.0}}
        images = []
        n = 4
        for i in range(n):
            az = 2 * np.pi * i / n
            eye = np.array([3.6 * np.cos(az), 3.6 * np.sin(az), 0.3])
            f = -eye / np.linalg.norm(eye)
            s = np.cross(f, np.array([0.0, 0.0, 1.0]))
            s /= np.linalg.norm(s)
            u = np.cross(s, f)
            R = np.stack([s, u, -f], axis=1).T
            images.append({"name": f"img_{i:02d}.jpg", "R": R, "t": eye,
                           "cam_id": 1})
            img = np.clip(rng.random((300, 400, 3)) * 255, 0, 255).astype(np.uint8)
            Image.fromarray(img).save(str(photo / f"img_{i:02d}.jpg"))
        (root / "90_meta").mkdir(parents=True)
        write_capture_meta(cameras, images,
                           str(root / "90_meta" / f"{item}_capture_meta.json"))
        mesh = o3d.geometry.TriangleMesh.create_sphere(radius=1.0, resolution=20)
        mesh.compute_vertex_normals()
        return root, item, mesh

    @staticmethod
    def _cfg():
        from h3d.core import PipelineConfig

        cfg = PipelineConfig()
        cfg.photo_texture.texture_size = 256
        cfg.photo_texture.poisson_blend = False
        return cfg

    def test_uv_layout_written(self, tmp_path):
        from h3d.archive.atlas import load_uv_layout, uv_digest
        from h3d.photo_texture import texture_mesh_from_photos

        root, item, mesh = self._project(tmp_path)
        out_dir = str(root / "50_tex" / item)
        _outs, metrics = texture_mesh_from_photos(
            mesh, str(root), item, self._cfg(), out_dir, geometry_is_sfm=True)

        info = metrics.get("uv_layout") or {}
        assert info.get("written") is True, metrics.get("uv_layout_error")
        p = os.path.join(out_dir, f"{item}_uv.npz")
        assert os.path.isfile(p)
        uv, faces = load_uv_layout(p)
        assert uv_digest(uv, faces) == info["digest"]

    def test_second_run_does_not_rewrite(self, tmp_path):
        from h3d.photo_texture import texture_mesh_from_photos

        root, item, mesh = self._project(tmp_path)
        out_dir = str(root / "50_tex" / item)
        cfg = self._cfg()
        texture_mesh_from_photos(mesh, str(root), item, cfg, out_dir,
                                 geometry_is_sfm=True)
        p = os.path.join(out_dir, f"{item}_uv.npz")
        mtime = os.path.getmtime(p)
        _outs, metrics = texture_mesh_from_photos(
            mesh, str(root), item, cfg, out_dir, geometry_is_sfm=True)
        assert metrics["uv_layout"]["written"] is False
        assert os.path.getmtime(p) == mtime
