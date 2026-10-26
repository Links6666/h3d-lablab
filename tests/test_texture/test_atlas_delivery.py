"""tests.test_texture.test_atlas_delivery : 图集必须进交付包（M4→M5→M6 贯通）。

实机背景（2026-10-26，`E:\\兵马俑照片\\测试了哦哦哦`）
------------------------------------------------------
配置写的是 ``texture/photo_texture.export_formats: [ply]``。M4 花了 50.18 s
逐纹素采样出 **2048² / 2,639,926 纹素**的 UV 图集，但：

* ``export_textured`` 只导 PLY —— **PLY 无法引用图像纹理**，图集只留在
  ``99_runs/atlas_cache/tex/`` 里，交付包只剩 85,420 个顶点色（差 31 倍）；
* M5 找 ``<item>_L1.0_diffuse.png``，而该 PNG 只有导出 obj/glb 时才由导出器
  写下 —— 五项纹理指标全 N/A，与 M4 自报的岛内填充率 94.89% 自相矛盾；
* 90_meta 里 ``delivery.models`` 是空列表，而 80_export 里明明躺着 .ply。

本文件锁住四条本质行为：
    1. 图集落盘与 ``export_formats`` **解耦**（只有 ply 也要有 PNG）；
    2. 没有可承载格式时自动补导 OBJ（可关），并在指标里留痕；
    3. M5 能定位到图集（优先 M4 记的路径），绝不把着色掩膜当图集读；
    4. M6 无论导出格式如何都把图集复制进交付包。
"""
from __future__ import annotations

import os

import numpy as np
import open3d as o3d
import pytest

from h3d.core import PipelineConfig


# --------------------------------------------------------------- 夹具
def _sphere(resolution=24, scale=1.0):
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=scale,
                                                   resolution=resolution)
    mesh.compute_vertex_normals()
    return mesh


def _meta(n=4):
    cameras = {1: {"w": 800, "h": 600, "fx": 800.0, "fy": 800.0,
                   "cx": 400.0, "cy": 300.0}}
    images = []
    for i in range(n):
        az = 2 * np.pi * i / n
        eye = np.array([3.6 * np.cos(az), 3.6 * np.sin(az), 0.3])
        f = -eye / np.linalg.norm(eye)
        up = np.array([0.0, 0.0, 1.0])
        s = np.cross(f, up)
        s /= np.linalg.norm(s)
        u = np.cross(s, f)
        R = np.stack([s, u, -f], axis=1).T
        images.append({"name": f"img_{i:02d}.jpg", "R": R, "t": eye, "cam_id": 1})
    return cameras, images


def _photos(photo_dir, n=4, size=(600, 800)):
    from PIL import Image

    os.makedirs(photo_dir, exist_ok=True)
    rng = np.random.default_rng(0)
    for i in range(n):
        img = (rng.random((size[0], size[1], 3)) * 255).astype(np.uint8)
        Image.fromarray(img).save(os.path.join(photo_dir, f"img_{i:02d}.jpg"))


@pytest.fixture()
def item_root(tmp_path):
    """一件的最小工作目录：90_meta 位姿 + 00_photo/<item> 照片。"""
    from h3d.sfm_solve import write_capture_meta

    root = str(tmp_path)
    os.makedirs(os.path.join(root, "90_meta"), exist_ok=True)
    cams, imgs = _meta()
    write_capture_meta(cams, imgs,
                       os.path.join(root, "90_meta", "itemA_capture_meta.json"))
    _photos(os.path.join(root, "00_photo", "itemA"))
    return root


def _cfg(formats, size=256, auto=True):
    cfg = PipelineConfig()
    cfg.photo_texture.texture_size = size
    cfg.photo_texture.export_formats = list(formats)
    cfg.photo_texture.auto_textured_format = auto
    cfg.photo_texture.poisson_blend = False      # 测试只关心落盘，不做融合
    return cfg


# --------------------------------------------------- 1) 图集落盘与格式解耦
class TestAtlasAlwaysWritten:
    def test_ply_only_still_writes_atlas_png(self, item_root, tmp_path):
        """只有 ply 时图集仍必须落盘——这是 M5 与交付包的唯一色彩来源。"""
        from h3d.photo_texture import texture_mesh_from_photos

        cfg = _cfg(["ply"])
        out = os.path.join(item_root, "50_tex", "itemA")
        outputs, m = texture_mesh_from_photos(
            _sphere(), item_root, "itemA", cfg, out, geometry_is_sfm=True)

        atlas = os.path.join(out, "itemA_L1.0_diffuse.png")
        assert os.path.exists(atlas), "only-ply 配置下图集没落盘（实机缺陷复现）"
        assert os.path.abspath(atlas) in outputs, "图集必须进 M4 产物列表"
        assert m["atlas_png"] and os.path.exists(m["atlas_png"])
        assert m["atlas_size_px"] == 256
        assert m["atlas_bytes"] > 0
        # 顶点色 PLY 仍在（几何模型兜底）
        assert os.path.exists(os.path.join(out, "itemA_L1.0.ply"))

    def test_atlas_pixels_are_not_blank(self, item_root):
        """图集必须真的带颜色：全黑图集等于没贴图，但文件大小看不出来。"""
        from PIL import Image

        from h3d.photo_texture import texture_mesh_from_photos

        cfg = _cfg(["ply"])
        out = os.path.join(item_root, "50_tex", "itemA")
        texture_mesh_from_photos(_sphere(), item_root, "itemA", cfg, out,
                                 geometry_is_sfm=True)
        arr = np.asarray(Image.open(os.path.join(out, "itemA_L1.0_diffuse.png")))
        assert arr.shape == (256, 256, 3)
        assert arr.max() > 0, "图集全黑（采样失败）"


# ------------------------------------------- 2) 无形承载格式 → 自动补导 OBJ
class TestAutoTexturedFormat:
    def test_ply_only_auto_adds_obj(self, item_root):
        """没有 obj/glb 时图集是孤立文件 → 自动补导 OBJ 让纹理真正被引用。"""
        from h3d.photo_texture import texture_mesh_from_photos

        cfg = _cfg(["ply"])
        out = os.path.join(item_root, "50_tex", "itemA")
        _, m = texture_mesh_from_photos(_sphere(), item_root, "itemA", cfg, out,
                                        geometry_is_sfm=True)
        assert m["formats_requested"] == ["ply"]
        assert "obj" in m["formats"], "应自动补导 obj（auto_textured_format=true）"
        assert m["auto_added_formats"] == ["obj"]
        assert m["texture_carrier"] == "obj"
        assert os.path.exists(os.path.join(out, "itemA_L1.0.obj"))
        assert os.path.exists(os.path.join(out, "itemA_L1.0.mtl"))

    def test_auto_add_can_be_disabled(self, item_root):
        """关掉开关后必须**只告警不补导**（用户可能只要几何存档）。"""
        from h3d.photo_texture import texture_mesh_from_photos

        cfg = _cfg(["ply"], auto=False)
        out = os.path.join(item_root, "50_tex", "itemA")
        _, m = texture_mesh_from_photos(_sphere(), item_root, "itemA", cfg, out,
                                        geometry_is_sfm=True)
        assert m["auto_added_formats"] == []
        assert m["texture_carrier"] is None
        assert not os.path.exists(os.path.join(out, "itemA_L1.0.obj"))
        # 图集仍落盘（与格式解耦的那一条不受开关影响）
        assert os.path.exists(os.path.join(out, "itemA_L1.0_diffuse.png"))

    def test_obj_requested_no_auto_add(self, item_root):
        """本来就有 obj 时不得重复补导、也不得写第二个图集文件。"""
        from h3d.photo_texture import texture_mesh_from_photos

        cfg = _cfg(["obj"])
        out = os.path.join(item_root, "50_tex", "itemA")
        _, m = texture_mesh_from_photos(_sphere(), item_root, "itemA", cfg, out,
                                        geometry_is_sfm=True)
        assert m["formats_requested"] == ["obj"]
        assert m["auto_added_formats"] == []
        assert m["texture_carrier"] == "obj"
        pngs = [f for f in os.listdir(out) if f.endswith(".png")
                and "mask" not in f]
        assert pngs == ["itemA_L1.0_diffuse.png"]


# ----------------------------------------------------- 3) M5 图集定位
class TestLocateTextureAtlas:
    def test_prefers_metric_path(self, tmp_path):
        from h3d.runner import _locate_texture_atlas

        tex = tmp_path / "50_tex" / "itemA"
        tex.mkdir(parents=True)
        (tex / "itemA_L1.0_mask.png").write_bytes(b"x")
        atlas = tex / "itemA_L1.0_diffuse.png"
        atlas.write_bytes(b"y")
        assert _locate_texture_atlas(str(tex), "itemA",
                                     {"atlas_png": str(atlas)}) == str(atlas)

    def test_scan_skips_mask(self, tmp_path):
        """掩膜尺寸与图集相同，误当图集读进去会算出"清晰度 0"的假指标。"""
        from h3d.runner import _locate_texture_atlas

        tex = tmp_path / "50_tex" / "itemA"
        tex.mkdir(parents=True)
        (tex / "itemA_L1.0_mask.png").write_bytes(b"x")
        assert _locate_texture_atlas(str(tex), "itemA", {}) is None

    def test_returns_none_when_absent(self, tmp_path):
        from h3d.runner import _locate_texture_atlas

        assert _locate_texture_atlas(str(tmp_path / "nope"), "itemA", {}) is None


# ----------------------------------------------------- 4) M6 交付图集
class TestPublishAtlas:
    def test_copies_atlas_into_delivery(self, tmp_path):
        from h3d.publish import deliver_texture_atlas

        tex = tmp_path / "50_tex" / "itemA"
        tex.mkdir(parents=True)
        (tex / "itemA_L1.0_diffuse.png").write_bytes(b"atlas-bytes")
        out = tmp_path / "80_export" / "itemA"
        got = deliver_texture_atlas(str(tex), str(out), "itemA", ["ply"])
        assert got and os.path.exists(got)
        assert os.path.basename(got) == "itemA_diffuse.png"
        assert out.joinpath("itemA_diffuse.png").read_bytes() == b"atlas-bytes"

    def test_none_when_vertex_color_path(self, tmp_path):
        """顶点色链路没有图集 —— 必须返回 None（不是报错，也不是乱搬文件）。"""
        from h3d.publish import deliver_texture_atlas

        tex = tmp_path / "50_tex" / "itemA"
        tex.mkdir(parents=True)
        (tex / "itemA_L1.0.ply").write_bytes(b"ply")
        out = tmp_path / "80_export" / "itemA"
        assert deliver_texture_atlas(str(tex), str(out), "itemA", ["ply"]) is None
        assert not (out / "itemA_diffuse.png").exists()


# ------------------------------------------------ 5) M5 纹理质检真正接通
class TestM5ReadsAtlasFromM4:
    def test_texture_metrics_not_na(self, item_root):
        """实机缺陷复现：M4 只导 ply 时 M5 五项纹理指标全是 N/A。

        根因是 M5 找 ``<item>_L1.0_diffuse.png``，而该文件过去只在导出 obj/glb
        时才生成。现在 M4 无条件落盘 + M5 优先读 M4 记的 ``atlas_png``，
        五项指标必须真的算出来（与 M4 自报的岛内填充率一致，而不是 N/A）。
        """
        from h3d.photo_texture import texture_mesh_from_photos
        from h3d.runner import _stage_quality
        from h3d.runner.task import StageResult, Task, TaskStatus

        cfg = _cfg(["ply"])
        out = os.path.join(item_root, "50_tex", "itemA")
        outs, m4 = texture_mesh_from_photos(
            _sphere(), item_root, "itemA", cfg, out, geometry_is_sfm=True)

        # M3 网格 + M2 参考点云（同一球面采样，覆盖率≈100%、rmse≈0）
        mesh = _sphere(resolution=30)
        dense = os.path.join(item_root, "30_dense", "itemA")
        os.makedirs(dense, exist_ok=True)
        mesh_path = os.path.join(dense, "itemA.ply")
        o3d.io.write_triangle_mesh(mesh_path, mesh)
        reg = os.path.join(item_root, "20_registered", "itemA")
        os.makedirs(reg, exist_ok=True)
        ref_path = os.path.join(reg, "itemA_s01.ply")
        o3d.io.write_point_cloud(ref_path, mesh.sample_points_uniformly(20000))

        task = Task(item_id="itemA", root=item_root, raw_paths=["dummy.ply"])
        task.results["M4"] = StageResult("M4", TaskStatus.DONE, outs, dict(m4))
        task.results["M2"] = StageResult("M2", TaskStatus.DONE, [ref_path], {})

        res = _stage_quality(task, cfg)
        m5 = res.metrics
        assert m5["texture_available"] is True, "M5 又没找到图集（N/A 缺陷复现）"
        assert m5["texture_atlas"] == "itemA_L1.0_diffuse.png"
        for key in ("texture_sharpness", "texture_seam",
                    "texture_coverage", "texture_fill_ratio"):
            assert m5[key] is not None, f"{key} 仍是 N/A"
        assert m5["texture_mask_source"] == "mask", "有真实着色掩膜时不得退回亮度阈值"
