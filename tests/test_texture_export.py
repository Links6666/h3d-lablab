"""tests.test_texture_export : 纹理导出/交付完整性（2026-10-18 实机回归）。

覆盖三处「跑完却拿不到可用模型」的缺陷：

1. **投影像素坐标系错配**：S1 把照片缩到 2400 长边喂 COLMAP，内参属于
   ``photos_small``；纹理阶段读原图。实测 2399×1601 vs 4496×3000 = 1.874×，
   旧实现直接拿内参系 u/v 当原图索引 → 纹理整体错位且无报错。
2. **OBJ 旁挂文件未随交付**：旧 ``export_delivery`` 只复制 ``.obj``，
   ``.mtl`` / ``_diffuse.png`` 留在 ``50_tex`` → 交付模型无贴图。
3. **GLB 静默丢纹理**：Open3D 写 glb 不写 ``TEXCOORD_0`` / ``images``，
   必须改由 trimesh 写出，并保证 baseColorFactor 为纯白（否则压暗 60%）。
"""
from __future__ import annotations

import json
import os
import struct

import numpy as np
import open3d as o3d
import pytest

from h3d.photo_texture import project_photos_to_texture, resolve_photo_dir
from h3d.texture.uv import export_textured

# ---------------------------------------------------------------------------
# 1) 内参系 -> 实际图像像素系换算
# ---------------------------------------------------------------------------


def _plane_mesh():
    mesh = o3d.geometry.TriangleMesh()
    mesh.vertices = o3d.utility.Vector3dVector(
        [[-0.4, -0.4, 0.0], [0.4, -0.4, 0.0],
         [0.4, 0.4, 0.0], [-0.4, 0.4, 0.0]])
    mesh.triangles = o3d.utility.Vector3iVector([[0, 1, 2], [0, 2, 3]])
    mesh.compute_vertex_normals()
    return mesh


_UV = np.array([[0.15, 0.15], [0.85, 0.15], [0.85, 0.85], [0.15, 0.85]])
_FACES = np.array([[0, 1, 2], [0, 2, 3]])


def _gradient(h, w):
    """按像素坐标编码颜色的测试图（R=u, G=v, B=常量）。"""
    u = np.linspace(0, 255, w)[None, :].repeat(h, axis=0)
    v = np.linspace(0, 255, h)[:, None].repeat(w, axis=1)
    b = np.full((h, w), 60.0)
    return np.stack([u, v, b], axis=2).astype(np.uint8)


def _cams():
    cameras = {1: {"w": 100, "h": 100, "fx": 100.0, "fy": 100.0,
                   "cx": 50.0, "cy": 50.0}}
    images = [
        {"name": "a.jpg", "R": np.eye(3), "t": np.array([0.0, 0.0, 1.0]),
         "cam_id": 1},
        {"name": "b.jpg", "R": np.eye(3), "t": np.array([0.02, 0.0, 1.0]),
         "cam_id": 1},
    ]
    return cameras, images


def _write_photos(dirpath, size):
    from PIL import Image
    arr = _gradient(size, size)
    for n in ("a.jpg", "b.jpg"):
        Image.fromarray(arr).save(os.path.join(dirpath, n), quality=100)


class TestPixelSpaceConversion:
    def test_upscaled_photo_yields_same_texture(self, tmp_path):
        """同一场景、同一内参，照片放大 2 倍 -> 纹理必须一致。

        内参属于 100×100 坐标系；把照片按 2× 最近邻放大到 200×200 后，
        采样点必须跟着放大到 (2u, 2v)，否则取到的是完全不同的像素。
        """
        d1 = tmp_path / "native"
        d2 = tmp_path / "double"
        d1.mkdir()
        d2.mkdir()
        _write_photos(str(d1), 100)
        _write_photos(str(d2), 200)

        cameras, images = _cams()
        tex1, m1 = project_photos_to_texture(
            _plane_mesh(), _UV, _FACES, images, cameras, str(d1),
            size=64, top_k=2, samples_per_tri=4)
        tex2, m2 = project_photos_to_texture(
            _plane_mesh(), _UV, _FACES, images, cameras, str(d2),
            size=64, top_k=2, samples_per_tri=4)

        assert m1["projected_cameras"] == 2 and m2["projected_cameras"] == 2
        # 关键断言：像素换算正确时两张纹理逐像素一致（容差仅覆盖 JPEG 量化）
        diff = np.abs(tex1.astype(int) - tex2.astype(int))
        assert diff.max() <= 8, f"纹理不一致，最大像素差 {diff.max()}"
        assert m2["pixel_scale"][0] == pytest.approx(2.0)

    def test_metric_reports_scale(self, tmp_path):
        d = tmp_path / "p"
        d.mkdir()
        _write_photos(str(d), 100)
        cameras, images = _cams()
        _, metrics = project_photos_to_texture(
            _plane_mesh(), _UV, _FACES, images, cameras, str(d),
            size=32, top_k=2)
        assert metrics["pixel_scale"] == [1.0]


class TestResolvePhotoDir:
    def _cfg(self, photo_dir=""):
        from h3d.core import PipelineConfig
        cfg = PipelineConfig()
        cfg.photo_texture.photo_dir = photo_dir
        return cfg

    def test_item_subdir(self, tmp_path):
        from PIL import Image
        item = tmp_path / "00_photo" / "r1"
        item.mkdir(parents=True)
        Image.fromarray(_gradient(20, 20)).save(item / "a.jpg")
        got = resolve_photo_dir(str(tmp_path), "r1", self._cfg())
        assert os.path.abspath(got) == os.path.abspath(item)

    def test_flat_layout(self, tmp_path):
        """照片直接平铺在 00_photo/ 下（不建 <item> 子目录）也要能找到。"""
        from PIL import Image
        base = tmp_path / "00_photo"
        base.mkdir()
        Image.fromarray(_gradient(20, 20)).save(base / "a.jpg")
        got = resolve_photo_dir(str(tmp_path), "r1", self._cfg())
        assert os.path.abspath(got) == os.path.abspath(base)

    def test_config_photo_dir_wins(self, tmp_path):
        alt = tmp_path / "pics" / "r1"
        alt.mkdir(parents=True)
        got = resolve_photo_dir(str(tmp_path), "r1",
                                self._cfg(str(tmp_path / "pics")))
        assert os.path.abspath(got) == os.path.abspath(alt)


# ---------------------------------------------------------------------------
# 2) OBJ 三件套命名一致 + GLB 内嵌纹理
# ---------------------------------------------------------------------------


def _glb_json(path):
    blob = open(path, "rb").read()
    clen, _ = struct.unpack_from("<II", blob, 12)
    return json.loads(blob[20:20 + clen].decode("utf-8")), blob


class TestExportTextured:
    def test_obj_triplet_consistent(self, tmp_path):
        mesh = _plane_mesh()
        img = _gradient(48, 48)
        outs = export_textured(mesh, str(tmp_path), "r1", "L1.0",
                               ["obj"], uv=_UV, image=img)
        assert len(outs) == 1
        obj = tmp_path / "r1_L1.0.obj"
        mtl = tmp_path / "r1_L1.0.mtl"
        png = tmp_path / "r1_L1.0_diffuse.png"
        assert obj.exists() and mtl.exists() and png.exists()
        # 旧实现会留下一个 r1_L1.0_0.png，与质检/交付的口径不一致
        assert not (tmp_path / "r1_L1.0_0.png").exists()
        text = mtl.read_text(encoding="utf-8")
        assert "map_Kd r1_L1.0_diffuse.png" in text

    def test_glb_has_texture_and_uv(self, tmp_path):
        mesh = _plane_mesh()
        img = _gradient(48, 48)
        export_textured(mesh, str(tmp_path), "r1", "L1.0",
                        ["glb"], uv=_UV, image=img)
        js, _ = _glb_json(str(tmp_path / "r1_L1.0.glb"))
        assert js.get("images"), "GLB 缺少内嵌贴图"
        assert js.get("textures"), "GLB 缺少 texture 定义"
        attrs = [p.get("attributes", {}) for m in js["meshes"]
                 for p in m["primitives"]]
        assert any("TEXCOORD_0" in a for a in attrs), "GLB 缺少 UV"
        mat = js["materials"][0]
        pbr = mat.get("pbrMetallicRoughness", {})
        assert "baseColorTexture" in pbr
        # trimesh 默认 0.4 灰会把贴图压暗，必须显式纯白
        assert pbr.get("baseColorFactor")[:3] == [1.0, 1.0, 1.0]


# ---------------------------------------------------------------------------
# 3) 交付包必须带走 .mtl 与贴图，并改写引用
# ---------------------------------------------------------------------------


class TestDeliverySidecars:
    def test_mtl_and_texture_delivered(self, tmp_path):
        from h3d.publish import export_delivery

        tex_dir = tmp_path / "50_tex" / "r1"
        tex_dir.mkdir(parents=True)
        mesh = _plane_mesh()
        img = _gradient(32, 32)
        export_textured(mesh, str(tex_dir), "r1", "L1.0",
                        ["obj"], uv=_UV, image=img)

        out_dir = tmp_path / "80_export" / "r1"
        files = export_delivery(str(tex_dir), str(out_dir), "r1", ["obj"])

        names = sorted(os.path.basename(f) for f in files)
        assert names == ["r1.obj"]
        assert (out_dir / "r1.mtl").exists(), "交付包缺 .mtl"
        assert (out_dir / "r1_diffuse.png").exists(), "交付包缺贴图"
        obj_text = (out_dir / "r1.obj").read_text(encoding="utf-8")
        assert "mtllib r1.mtl" in obj_text
        mtl_text = (out_dir / "r1.mtl").read_text(encoding="utf-8")
        assert "map_Kd r1_diffuse.png" in mtl_text

    def test_vertex_color_obj_delivered_without_mtl(self, tmp_path):
        """顶点色 obj 没有 mtllib，交付不应报错、也不应凭空造 mtl。"""
        from h3d.publish import export_delivery

        tex_dir = tmp_path / "50_tex" / "r1"
        tex_dir.mkdir(parents=True)
        o3d.io.write_triangle_mesh(str(tex_dir / "r1_L1.obj"), _plane_mesh())
        out_dir = tmp_path / "80_export" / "r1"
        files = export_delivery(str(tex_dir), str(out_dir), "r1", ["obj"])
        assert len(files) == 1
        assert (out_dir / "r1.obj").exists()


# ---------------------------------------------------------------------------
# 4) 快速 OBJ 写出器：与 Open3D 逐值等价，但不再为逐角重复 UV（2026-10-24）
# ---------------------------------------------------------------------------


class TestFastObjWriter:
    """Open3D 写带纹理 obj 实测 5.34 s/37k 顶点，是复用图集后唯一的大头。

    快速路径手写 ``v/vt/vn/f`` + MTL + 贴图（约 0.2 s）。等价性判据：
    回读后几何点集与逐角 UV 与源网格一致（Open3D 的 OBJ **读取器**会重排
    顶点，所以几何按排序后比较，UV 按逐角序列比较）。
    """

    def test_obj_is_per_vertex_vt_and_equivalent(self, tmp_path):
        mesh = _plane_mesh()
        uv = _UV
        img = _gradient(64, 64)
        export_textured(mesh, str(tmp_path), "r1", "L1.0", ["obj"],
                        uv=uv, image=img)
        obj = tmp_path / "r1_L1.0.obj"
        text = obj.read_text(encoding="utf-8")
        # 逐顶点 vt（4 条），而不是 Open3D 的逐角 12 条
        assert text.count("\nvt ") == len(mesh.vertices), "vt 应逐顶点写"
        assert "mtllib r1_L1.0.mtl" in text
        assert "usemtl r1_L1.0" in text
        assert text.count("\nf ") == len(mesh.triangles)

        r = o3d.io.read_triangle_mesh(str(obj))
        v_src = np.asarray(mesh.vertices)
        v_got = np.asarray(r.vertices)
        assert v_got.shape == v_src.shape
        np.testing.assert_allclose(np.sort(v_got, axis=0),
                                   np.sort(v_src, axis=0), atol=1e-5)
        # 逐角 UV 与「uv 按面索引取值」一致（V 不翻转，否则贴图上下颠倒）
        faces = np.asarray(mesh.triangles).reshape(-1)
        np.testing.assert_allclose(np.asarray(r.triangle_uvs),
                                   np.asarray(uv)[faces], atol=1e-5)

    def test_writer_refuses_without_per_vertex_uv(self, tmp_path):
        """拿不到逐顶点 UV 时必须返回 False 让调用方回退，不能写出错纹理的 obj。"""
        from h3d.texture.uv import _write_obj_textured
        mesh = _plane_mesh()
        img = _gradient(16, 16)
        assert _write_obj_textured(str(tmp_path / "x.obj"), mesh, None, img) is False


# ---------------------------------------------------------------------------
# 4) 快速 OBJ 写出器：与 Open3D 逐值等价，但不再为逐角重复 UV（2026-10-24）
# ---------------------------------------------------------------------------


class TestFastObjWriter:
    """Open3D 写带纹理 obj 实测 5.34 s/37k 顶点，是复用图集后唯一的大头。

    快速路径手写 ``v/vt/vn/f`` + MTL + 贴图（约 0.2 s）。等价性判据：
    回读后几何点集与逐角 UV 与源网格一致（Open3D 的 OBJ **读取器**会重排
    顶点，所以几何按排序后比较，UV 按逐角序列比较）。
    """

    def test_obj_is_per_vertex_vt_and_equivalent(self, tmp_path):
        mesh = _plane_mesh()
        uv = _UV
        img = _gradient(64, 64)
        export_textured(mesh, str(tmp_path), "r1", "L1.0", ["obj"],
                        uv=uv, image=img)
        obj = tmp_path / "r1_L1.0.obj"
        text = obj.read_text(encoding="utf-8")
        # 逐顶点 vt（4 条），而不是 Open3D 的逐角 12 条
        assert text.count("\nvt ") == len(mesh.vertices), "vt 应逐顶点写"
        assert "mtllib r1_L1.0.mtl" in text
        assert "usemtl r1_L1.0" in text
        assert text.count("\nf ") == len(mesh.triangles)

        r = o3d.io.read_triangle_mesh(str(obj))
        v_src = np.asarray(mesh.vertices)
        v_got = np.asarray(r.vertices)
        assert v_got.shape == v_src.shape
        np.testing.assert_allclose(np.sort(v_got, axis=0),
                                   np.sort(v_src, axis=0), atol=1e-5)
        # 逐角 UV 与「uv 按面索引取值」一致（V 不翻转，否则贴图上下颠倒）
        faces = np.asarray(mesh.triangles).reshape(-1)
        np.testing.assert_allclose(np.asarray(r.triangle_uvs),
                                   np.asarray(uv)[faces], atol=1e-5)

    def test_writer_refuses_without_per_vertex_uv(self, tmp_path):
        """拿不到逐顶点 UV 时必须返回 False 让调用方回退，不能写出错纹理的 obj。"""
        from h3d.texture.uv import _write_obj_textured
        mesh = _plane_mesh()
        img = _gradient(16, 16)
        assert _write_obj_textured(str(tmp_path / "x.obj"), mesh, None, img) is False
