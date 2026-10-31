"""图集继承的渲染级自检（B1 切片 4）。

判据为什么是「逐纹素比值」而不是「烘出来了、有颜色」
----------------------------------------------------
继承的失效方式有两种，都不会报错：

1. **位姿变换算错** → 照片投到错误位置，图集整体错位或大面积空白；
2. **UV 被重新展开** → texel 与基准期不再一一对应，图集看起来完全正常，
   但跨期 ΔE 从此无意义（这是 B1 要防的根本问题）。

所以这里造一个**已知答案**的场景：

    基准相机 C，复测相机 C' = 逆变换(C)（另加微小噪声）
    继承：把 C' 正变换回去 → 应还原成 C → 纹理应几乎与基准图集一致

再反向验证：把变换用反（C' 再正变换一次），纹素差必须显著变大 —— 否则
上面的判据没有判别力，等于什么都没测。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

SIZE = 256


def _scene(tmp_path, n=4):
    """基准场景：球 + 环绕相机 + 图案可辨的照片。

    照片用**渐变 + 棋盘**而不是纯噪声：噪声在 2 px 错位时就会跳变几百灰度，
    于是「轻微抖动」与「变换完全用反」在数值上无法区分 —— 判据就没有判别力。
    渐变每像素只差 0.6 灰度、棋盘只占少数像素，于是：
    亚像素/几像素抖动 → 中位差个位数；相机搬到错误视角 → 中位差几十。
    """
    import open3d as o3d
    from PIL import Image

    photo_dir = str(tmp_path / "photos")
    os.makedirs(photo_dir, exist_ok=True)
    cameras = {1: {"w": 400, "h": 300, "fx": 420.0, "fy": 420.0,
                   "cx": 200.0, "cy": 150.0}}
    yy, xx = np.mgrid[0:300, 0:400]
    images = []
    for i in range(n):
        az = 2 * np.pi * i / n
        eye = np.array([3.6 * np.cos(az), 3.6 * np.sin(az), 0.3])
        f = -eye / np.linalg.norm(eye)
        s_ax = np.cross(f, np.array([0.0, 0.0, 1.0]))
        s_ax /= np.linalg.norm(s_ax)
        u = np.cross(s_ax, f)
        R = np.stack([s_ax, u, -f], axis=1).T
        images.append({"name": f"img_{i:02d}.png", "R": R, "t": eye, "cam_id": 1})
        # 每台相机给一个固定相位偏移：不同视角的图案不同，但仍空间连续
        ph = int(400 * i / n)
        g = ((xx + ph) % 400) * 255 // 400
        b = (yy * 255 // 300)
        chk = (((xx + ph) // 32 + yy // 32) % 2) * 200 + 30
        img = np.stack([g, b, chk], axis=-1).astype(np.uint8)
        Image.fromarray(img).save(os.path.join(photo_dir, f"img_{i:02d}.png"))
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=1.0, resolution=20)
    mesh.compute_vertex_normals()
    from h3d.texture.uv import unwrap_mesh

    mesh_uv, uv, faces = unwrap_mesh(mesh)
    return mesh_uv, uv, faces, images, cameras, photo_dir


def _sim(angle_deg=35.0, s=1.0, t=(0.0, 0.0, 0.0)):
    from h3d.archive.inherit import Similarity

    return Similarity.from_rotation((0.3, 0.5, 0.8), np.deg2rad(angle_deg), t, s)


def _jitter(images, deg=0.3, pos=0.005, seed=11):
    """给复测相机加一点噪声：真实复测不会精确还原基准位姿。"""
    rng = np.random.default_rng(seed)
    out = []
    for im in images:
        from h3d.archive.inherit import Similarity

        d = Similarity.from_rotation(rng.normal(size=3), np.deg2rad(deg))
        moved = dict(im)
        moved["R"] = d.R @ im["R"]
        moved["t"] = im["t"] + rng.normal(scale=pos, size=3)
        out.append(moved)
    return out


def _diff(a, b):
    """两张图集的中位绝对差（0-255 值域）。"""
    return float(np.median(np.abs(a.astype(np.int16) - b.astype(np.int16))))


def _bake(mesh, uv, faces, images, cameras, photo_dir):
    from h3d.photo_texture import project_photos_to_texture

    return project_photos_to_texture(mesh, uv, faces, images, cameras,
                                     photo_dir, size=SIZE, top_k=3,
                                     occlusion=True)


class TestInheritedAtlas:
    def test_inherited_matches_base(self, tmp_path):
        """复测相机还原后，继承图集应与基准图集几乎一致。"""
        from h3d.archive.inherit import inherit_texture

        mesh, uv, faces, images, cameras, photo_dir = _scene(tmp_path)
        base_img, base_metrics = _bake(mesh, uv, faces, images, cameras,
                                       photo_dir)

        sim = _sim(angle_deg=35.0, s=1.0, t=(0.4, -0.25, 0.1))
        # 复测坐标系里的相机 = 基准相机做逆变换，再加一点噪声
        from h3d.archive.inherit import transform_cameras

        rec_images = _jitter(transform_cameras(images, sim.inverse()))
        inh_img, metrics = inherit_texture(
            mesh, uv, faces, rec_images, cameras, photo_dir, sim,
            size=SIZE, atlas_version="atlas-v1")

        d = _diff(base_img, inh_img)
        assert d < 12, f"继承图集与基准中位差 {d}（应很小）—— 位姿变换或采样有误"
        assert metrics["inherited"] is True
        assert metrics["atlas_version"] == "atlas-v1"
        # 画幅内可见比例用**相对基准**判：绝对值取决于相机布置（4 台赤道相机
        # 本来就看不到极区），拿它当阈值只是拍脑袋。继承不该比基准更差。
        assert (metrics["in_frame_fraction"]
                >= base_metrics["in_frame_fraction"] - 0.05)

    def test_wrong_transform_is_detectable(self, tmp_path):
        """判别力：变换用反时必须显著变差，否则上一条判据形同虚设。"""
        from h3d.archive.inherit import inherit_texture, transform_cameras

        mesh, uv, faces, images, cameras, photo_dir = _scene(tmp_path)
        base_img, base_metrics = _bake(mesh, uv, faces, images, cameras,
                                       photo_dir)

        sim = _sim(angle_deg=35.0, s=1.0, t=(0.4, -0.25, 0.1))
        rec_images = _jitter(transform_cameras(images, sim.inverse()))
        wrong_img, wrong_metrics = inherit_texture(
            mesh, uv, faces, rec_images, cameras, photo_dir, sim.inverse(),
            size=SIZE)

        d = _diff(base_img, wrong_img)
        assert d > 30, f"反向变换的中位差只有 {d}，判据没有判别力"
        # 这里刻意**不看** in_frame_fraction：球体各向同性，相机搬到任意视角
        # 都还能看到球面，实测反向变换只把该比例从 0.799 打到 0.717 —— 用覆盖率
        # 判位姿对错会放过整片错位（r17 就吃过「覆盖率正常但几何搬错」的亏）。
        # 纹素差才是外部可核对的判据：错误的相机看到的是别的视角的颜色。
        assert wrong_metrics["inherited"] is True

    def test_does_not_reunwrap(self, tmp_path, monkeypatch):
        """继承绝不许重新展开 UV —— 那会让 texel 对应关系失效。"""
        import h3d.texture.uv as uv_mod

        from h3d.archive.inherit import inherit_texture

        # 先造场景，再打桩：_scene 自己要用 unwrap_mesh
        mesh, uv, faces, images, cameras, photo_dir = _scene(tmp_path)
        called = []
        monkeypatch.setattr(uv_mod, "unwrap_mesh",
                            lambda *a, **k: called.append(1) or (None, None, None))
        inherit_texture(mesh, uv, faces, images, cameras, photo_dir, _sim(),
                        size=SIZE)
        assert called == [], "继承路径不得调用 unwrap_mesh"

    def test_records_source_epoch(self, tmp_path):
        """图集必须能追到「谁定义、谁继承」，报表要显示这个。"""
        from h3d.archive.inherit import inherit_texture

        mesh, uv, faces, images, cameras, photo_dir = _scene(tmp_path)
        _, metrics = inherit_texture(
            mesh, uv, faces, images, cameras, photo_dir, _sim(), size=SIZE,
            atlas_version="atlas-v1", base_epoch="E1", source_epoch="E2")
        assert metrics["atlas_base_epoch"] == "E1"
        assert metrics["atlas_source_epoch"] == "E2"
