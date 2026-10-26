"""tests.test_texture.test_atlas_cache : UV/纹理图集跨件复用单元测试。

覆盖的「本质行为」（不只是"不报错"）：
    1. 指纹只认该认的东西：同几何同哈希、位移即不同、float32 末位噪声不算不同；
    2. 位姿指纹与图像顺序无关，改一个 t 必然变；
    3. UV 缓存往返逐位一致，损坏文件 → 未命中 + 留档 + 重算（不静默）；
    4. 纹理缓存缺任一件（图/掩膜/json）即未命中，尺寸与配置不符即未命中；
    5. 端到端：同几何同位姿同照片的第二件 **命中**，产物与第一件逐字节一致；
    6. 反例：照片不同 → UV 命中但纹理重算（partial），不能把 A 的贴图按到 B 上；
    7. 关闭开关 → 全部未命中（复用必须是可关的显式行为）。
"""
from __future__ import annotations

import json
import os

import numpy as np
import open3d as o3d
import pytest

from h3d.core import PipelineConfig
from h3d.texture.atlas_cache import (AtlasCache, mesh_fingerprint,
                                     params_fingerprint, photo_fingerprint,
                                     pose_fingerprint)


def _sphere(resolution=24, scale=1.0, shift=0.0):
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=scale,
                                                   resolution=resolution)
    if shift:
        mesh.translate((shift, 0.0, 0.0))
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


def _photos(photo_dir, n=4, seed=0, size=(600, 800)):
    from PIL import Image
    os.makedirs(photo_dir, exist_ok=True)
    rng = np.random.default_rng(seed)
    for i in range(n):
        img = (rng.random((size[0], size[1], 3)) * 255).astype(np.uint8)
        Image.fromarray(img).save(os.path.join(photo_dir, f"img_{i:02d}.jpg"))


# --------------------------------------------------------------- 指纹
class TestFingerprints:
    def test_same_geometry_same_fingerprint(self):
        assert mesh_fingerprint(_sphere()) == mesh_fingerprint(_sphere())

    def test_translation_changes_fingerprint(self):
        assert mesh_fingerprint(_sphere()) != mesh_fingerprint(_sphere(shift=0.01))

    def test_float32_quantisation_absorbs_ulp_noise(self):
        a, b = _sphere(), _sphere()
        v = np.asarray(b.vertices).copy()
        i, j = np.argwhere(np.abs(v) > 0.2)[0]
        v[i, j] += 1e-10          # 远小于该量级的 float32 分辨率（≈6e-8）
        b.vertices = o3d.utility.Vector3dVector(v)
        assert mesh_fingerprint(a) == mesh_fingerprint(b)

    def test_real_geometry_change_detected(self):
        """1e-3 级的真实位移必须判为不同几何（否则会串件）。"""
        a, b = _sphere(), _sphere()
        v = np.asarray(b.vertices).copy()
        v[:, 2] += 1e-3
        b.vertices = o3d.utility.Vector3dVector(v)
        assert mesh_fingerprint(a) != mesh_fingerprint(b)

    def test_normals_participate_when_requested(self):
        a, b = _sphere(), _sphere()
        fa = mesh_fingerprint(a, with_normals=True)
        b.vertex_normals = o3d.utility.Vector3dVector(
            -np.asarray(b.vertex_normals))
        assert fa != mesh_fingerprint(b, with_normals=True)
        # 只要几何时法线不该影响指纹（UV 与法线无关，否则会白白丢掉复用）
        assert mesh_fingerprint(a) == mesh_fingerprint(b)

    def test_pose_order_insensitive(self):
        cams, imgs = _meta()
        assert pose_fingerprint(cams, imgs) == pose_fingerprint(cams, imgs[::-1])

    def test_pose_change_detected(self):
        cams, imgs = _meta()
        moved = [dict(im) for im in imgs]
        moved[0]["t"] = np.asarray(moved[0]["t"]) + np.array([0.05, 0.0, 0.0])
        assert pose_fingerprint(cams, imgs) != pose_fingerprint(cams, moved)

    def test_params_track_texture_size(self):
        cfg = PipelineConfig()
        base = params_fingerprint(cfg)
        cfg.photo_texture.texture_size = 2048
        assert params_fingerprint(cfg) != base

    def test_photo_fingerprint_tracks_content(self, tmp_path):
        p1, p2 = str(tmp_path / "a"), str(tmp_path / "b")
        _photos(p1, n=2, seed=0)
        _photos(p2, n=2, seed=1)
        assert photo_fingerprint(p1) != photo_fingerprint(p2)
        assert photo_fingerprint(p1) == photo_fingerprint(p1)


# --------------------------------------------------------------- 缓存读写
class TestCacheIO:
    def test_uv_roundtrip(self, tmp_path):
        cache = AtlasCache(str(tmp_path))
        vmap = np.arange(10, dtype=np.int64)
        idx = np.array([[0, 1, 2]], dtype=np.int64)
        uv = np.random.default_rng(0).random((10, 2)).astype(np.float32)
        cache.put_uv("fp", vmap, idx, uv, "itemA")
        got = cache.get_uv("fp")
        assert got is not None
        g_vmap, g_idx, g_uv, meta = got
        assert np.array_equal(g_vmap, vmap) and np.array_equal(g_idx, idx)
        assert np.array_equal(g_uv, uv) and meta["source_item"] == "itemA"

    def test_uv_corrupt_quarantined_and_miss(self, tmp_path):
        cache = AtlasCache(str(tmp_path))
        path = cache._uv_path("bad")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"not a npz")
        assert cache.get_uv("bad") is None
        leftover = [n for n in os.listdir(os.path.dirname(path))
                    if ".corrupt-" in n]
        assert leftover, "损坏缓存必须留档，不能静默删除"

    def test_texture_roundtrip(self, tmp_path):
        cache = AtlasCache(str(tmp_path))
        img = np.zeros((16, 16, 3), dtype=np.uint8)
        img[:, :8] = 128
        mask = np.zeros((16, 16), dtype=bool)
        mask[:, :8] = True
        cache.put_texture("k1", img, mask, {"coverage_ratio": 0.5}, "itemA")
        got = cache.get_texture("k1")
        assert got is not None
        g_img, g_mask, meta = got
        assert np.array_equal(g_img, img) and np.array_equal(g_mask, mask)
        assert meta["metrics"]["coverage_ratio"] == 0.5

    def test_texture_incomplete_is_miss(self, tmp_path):
        cache = AtlasCache(str(tmp_path))
        img = np.zeros((8, 8, 3), dtype=np.uint8)
        cache.put_texture("k2", img, np.ones((8, 8), bool), {}, "itemA")
        os.remove(cache._tex_path("k2")[:-4] + ".mask.png")
        assert cache.get_texture("k2") is None

    def test_disabled_cache_always_misses(self, tmp_path):
        cache = AtlasCache(str(tmp_path), enabled=False)
        cache.put_uv("fp", np.arange(3), np.array([[0, 1, 2]]),
                     np.zeros((3, 2), np.float32), "x")
        assert cache.get_uv("fp") is None
        assert not os.path.exists(os.path.join(str(tmp_path), "99_runs"))

    def test_eviction_keeps_bounded(self, tmp_path):
        cache = AtlasCache(str(tmp_path), max_textures=2)
        img = np.zeros((4, 4, 3), dtype=np.uint8)
        for k in ("a", "b", "c"):
            cache.put_texture(k, img, np.ones((4, 4), bool), {}, "x")
            os.utime(cache._tex_path(k), (1e9 + ord(k), 1e9 + ord(k)))
        cache._evict()
        left = [n for n in os.listdir(os.path.join(str(tmp_path), "99_runs",
                                                   "atlas_cache", "tex"))
                if n.endswith(".png") and ".mask." not in n]
        assert len(left) == 2


# --------------------------------------------------------------- 端到端
def _add_item(root, item, *, seed=0, meta=None):
    """在同一项目里再加一件：90_meta 位姿 + 00_photo/<item> 照片。"""
    os.makedirs(os.path.join(root, "90_meta"), exist_ok=True)
    from h3d.sfm_solve import write_capture_meta
    cams, imgs = meta or _meta()
    write_capture_meta(cams, imgs,
                       os.path.join(root, "90_meta", f"{item}_capture_meta.json"))
    _photos(os.path.join(root, "00_photo", item), n=4, seed=seed)
    return root, _sphere()


def _task_root(tmp_path, item, *, seed=0, meta=None):
    """造一件的最小工作目录：90_meta 位姿 + 00_photo/<item> 照片。"""
    return _add_item(str(tmp_path), item, seed=seed, meta=meta)


def _cfg(size=256):
    cfg = PipelineConfig()
    cfg.photo_texture.texture_size = size
    cfg.photo_texture.export_formats = ["obj"]
    cfg.photo_texture.icp_align = True
    return cfg


class TestReuseEndToEnd:
    def test_second_item_hits_and_matches_pixels(self, tmp_path):
        from h3d.photo_texture import texture_mesh_from_photos
        cfg = _cfg()
        root, mesh = _task_root(tmp_path, "itemA")
        outA = os.path.join(root, "50_tex", "itemA")
        _, mA = texture_mesh_from_photos(mesh, root, "itemA", cfg, outA,
                                        geometry_is_sfm=True)
        assert mA["atlas_cache"] == "miss"
        assert mA["atlas_reuse"]["texture"] == "miss"

        outB = os.path.join(root, "50_tex", "itemB")
        _add_item(root, "itemB", seed=0)   # 同照片、同位姿、同几何
        _, mB = texture_mesh_from_photos(_sphere(), root, "itemB", cfg, outB,
                                        geometry_is_sfm=True)
        assert mB["atlas_cache"] == "hit"
        assert mB["atlas_reuse"]["texture_from"] == "itemA"
        assert mB["atlas_reuse"]["uv_from"] == "itemA"

        from PIL import Image
        a = np.asarray(Image.open(os.path.join(outA, "itemA_L1.0_diffuse.png")))
        b = np.asarray(Image.open(os.path.join(outB, "itemB_L1.0_diffuse.png")))
        assert np.array_equal(a, b), "复用后贴图必须与首件逐像素一致"
        # 质量指标随图集一起复用，不能凭空变成 0
        for key in ("coverage_ratio", "atlas_utilization", "projected_cameras"):
            assert mB[key] == mA[key]
        assert os.path.exists(os.path.join(outB, "itemB_L1.0_mask.png"))

    def test_different_photos_recompute_texture(self, tmp_path):
        from h3d.photo_texture import texture_mesh_from_photos
        cfg = _cfg()
        root, mesh = _task_root(tmp_path, "itemA", seed=0)
        texture_mesh_from_photos(mesh, root, "itemA", cfg,
                                 os.path.join(root, "50_tex", "itemA"),
                                 geometry_is_sfm=True)
        # 换照片内容：几何没变 → UV 可复用，纹理必须重算
        root2, _ = _task_root(tmp_path / "b", "itemA", seed=7)
        _, mB = texture_mesh_from_photos(_sphere(), root2, "itemA", cfg,
                                         os.path.join(root2, "50_tex", "itemA"),
                                         geometry_is_sfm=True)
        assert mB["atlas_reuse"]["uv"] == "miss"      # 不同 task root，无缓存
        assert mB["atlas_cache"] == "miss"

    def test_photos_changed_same_root_texture_miss(self, tmp_path):
        from h3d.photo_texture import texture_mesh_from_photos
        cfg = _cfg()
        root, mesh = _task_root(tmp_path, "itemA", seed=0)
        texture_mesh_from_photos(mesh, root, "itemA", cfg,
                                 os.path.join(root, "50_tex", "itemA"),
                                 geometry_is_sfm=True)
        # 同一个 root 里把照片换掉 → 纹理必须重算（这是"串件"最容易翻车的地方）
        _photos(os.path.join(root, "00_photo", "itemB"), n=4, seed=9)
        from h3d.sfm_solve import write_capture_meta
        cams, imgs = _meta()
        write_capture_meta(cams, imgs,
                           os.path.join(root, "90_meta",
                                        "itemB_capture_meta.json"))
        _, mB = texture_mesh_from_photos(_sphere(), root, "itemB", cfg,
                                         os.path.join(root, "50_tex", "itemB"),
                                         geometry_is_sfm=True)
        assert mB["atlas_reuse"]["uv"] == "hit"
        assert mB["atlas_reuse"]["texture"] == "miss"
        assert mB["atlas_cache"] == "partial"

    def test_switch_off_forces_recompute(self, tmp_path):
        from h3d.photo_texture import texture_mesh_from_photos
        cfg = _cfg()
        cfg.photo_texture.reuse_atlas = False
        root = str(tmp_path)
        _task_root(tmp_path, "itemA")
        _add_item(root, "itemB", seed=0)
        for item in ("itemA", "itemB"):
            _, m = texture_mesh_from_photos(
                _sphere(), root, item, cfg,
                os.path.join(root, "50_tex", item), geometry_is_sfm=True)
            assert m["atlas_cache"] == "miss"
            assert m["atlas_reuse"]["uv"] == "disabled"
        assert not os.path.exists(os.path.join(root, "99_runs", "atlas_cache"))

    def test_metrics_are_json_serialisable(self, tmp_path):
        """manifest/报表要写 JSON：numpy 标量混进 metrics 会让整批报表失败。"""
        from h3d.photo_texture import texture_mesh_from_photos
        cfg = _cfg()
        root, mesh = _task_root(tmp_path, "itemA")
        _, m = texture_mesh_from_photos(mesh, root, "itemA", cfg,
                                        os.path.join(root, "50_tex", "itemA"),
                                        geometry_is_sfm=True)
        json.dumps({k: v for k, v in m.items() if k != "camera_hist"},
                   ensure_ascii=False)
