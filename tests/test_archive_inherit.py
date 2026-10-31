"""跨时相相机位姿变换（B1 切片 3）。

为什么单独拎出来测
------------------
`capture_meta` 里的 ``t`` 是**相机中心**（不是 OpenCV 的 tvec），投影写法是
``pc = (X - C) @ R.T``、判 ``z < 0``（朝 -Z）。B1 要把复测期的相机搬到基准
坐标系里，只要这个换算错一次，纹理就会整片错位 —— 而流程 rc=0、指标看起来
正常（r17 的教训：自报 rmse 0.0968 而几何搬错 50 倍）。

所以这里不测「烘出来有没有颜色」，测**投影恒等式**：

    对任意点 X，用原相机投影得到的 pc，
    必须等于「先把 X 变换到基准系、再用变换后相机投影」得到的 pc'。

这个恒等式不依赖任何实现细节，错了立刻红，且能反向验证判别力（把变换弄反
时必须明显不等）。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _sim(angle_deg=30.0, axis=(0.0, 0.0, 1.0), t=(0.5, -0.2, 0.1), s=1.37):
    from h3d.archive.inherit import Similarity

    return Similarity.from_rotation(axis, np.deg2rad(angle_deg), t, s)


def _points(n=50, seed=3):
    return np.random.default_rng(seed).normal(size=(n, 3))


class TestSimilarity:
    def test_apply_matches_formula(self):
        sim = _sim()
        X = _points()
        out = sim.apply_points(X)
        expect = sim.s * (X @ sim.R.T) + sim.t
        assert np.allclose(out, expect)

    def test_inverse_roundtrip(self):
        sim = _sim(angle_deg=73.0, s=0.61)
        X = _points()
        back = sim.inverse().apply_points(sim.apply_points(X))
        assert np.allclose(back, X, atol=1e-9)

    def test_scale_from_matrix(self):
        sim = _sim(s=2.5)
        from h3d.archive.inherit import Similarity

        again = Similarity.from_matrix(sim.matrix())
        assert again.s == pytest.approx(2.5, rel=1e-9)
        assert np.allclose(again.R, sim.R, atol=1e-9)
        assert np.allclose(again.t, sim.t, atol=1e-9)

    def test_identity(self):
        from h3d.archive.inherit import Similarity

        sim = Similarity.identity()
        X = _points()
        assert np.allclose(sim.apply_points(X), X)
        assert sim.s == pytest.approx(1.0)

    def test_rejects_reflection(self):
        """带镜像的 4x4 不是相似变换：det<0 会让整件几何翻面，必须拒绝。"""
        from h3d.archive.inherit import Similarity

        M = np.eye(4)
        M[0, 0] = -1.0
        with pytest.raises(ValueError):
            Similarity.from_matrix(M)

    def test_rejects_non_similarity(self):
        """各轴缩放不同（非均匀）不是相似变换。"""
        from h3d.archive.inherit import Similarity

        M = np.diag([1.0, 2.0, 3.0, 1.0])
        with pytest.raises(ValueError):
            Similarity.from_matrix(M)


class TestTransformCameras:
    def _images(self):
        """两台相机：中心 + world->cam 旋转（与 photo_texture 的约定一致）。"""
        out = []
        for eye in (np.array([3.0, 0.0, 0.2]), np.array([-2.5, 1.0, -0.3])):
            f = -eye / np.linalg.norm(eye)
            s_ax = np.cross(f, np.array([0.0, 0.0, 1.0]))
            s_ax /= np.linalg.norm(s_ax)
            u = np.cross(s_ax, f)
            R = np.stack([s_ax, u, -f], axis=1).T
            out.append({"name": "a", "R": R, "t": eye, "cam_id": 1})
        return out

    @staticmethod
    def _project(images, X):
        """复现 photo_texture 的投影写法：pc = (X - C) @ R.T。"""
        out = []
        for im in images:
            out.append((X - im["t"]) @ im["R"].T)
        return out

    def test_projection_identity(self):
        """核心判据：变换前后投影像素一致（等价于 pc 完全相等）。"""
        from h3d.archive.inherit import transform_cameras

        sim = _sim(angle_deg=37.0, s=1.37)
        images = self._images()
        X_rec = _points()
        pc_before = self._project(images, X_rec)
        moved = transform_cameras(images, sim)
        X_base = sim.apply_points(X_rec)
        pc_after = self._project(moved, X_base)
        for a, b in zip(pc_before, pc_after):
            assert np.allclose(a, b, atol=1e-9)

    def test_inverse_transform_does_not_match(self):
        """判别力：把变换用反，投影必须明显不同 —— 否则上面的判据没有区分度。"""
        from h3d.archive.inherit import transform_cameras

        sim = _sim(angle_deg=37.0, s=1.37)
        images = self._images()
        X_rec = _points()
        pc_before = self._project(images, X_rec)
        wrong = transform_cameras(images, sim.inverse())
        pc_wrong = self._project(wrong, sim.apply_points(X_rec))
        diff = max(float(np.abs(a - b).max())
                   for a, b in zip(pc_before, pc_wrong))
        assert diff > 0.1, f"反向变换竟然也能对上（差 {diff:.3g}），判据失效"

    def test_camera_center_transformed(self):
        from h3d.archive.inherit import transform_cameras

        sim = _sim()
        images = self._images()
        moved = transform_cameras(images, sim)
        for old, new in zip(images, moved):
            expect = sim.s * (sim.R @ old["t"]) + sim.t
            assert np.allclose(new["t"], expect, atol=1e-9)

    def test_original_images_not_mutated(self):
        """纯函数：入参不许被就地改（调用方可能还要用原值算别的）。"""
        from h3d.archive.inherit import transform_cameras

        images = self._images()
        R0 = images[0]["R"].copy()
        t0 = images[0]["t"].copy()
        transform_cameras(images, _sim())
        assert np.allclose(images[0]["R"], R0) and np.allclose(images[0]["t"], t0)

    def test_scale_one_keeps_rotation_orthogonal(self):
        """s=1 时变换后的 R 必须是正交阵（1/s 只是投影不变的缩放因子）。"""
        from h3d.archive.inherit import transform_cameras

        images = transform_cameras(self._images(),
                                   _sim(s=1.0, angle_deg=10.0))
        for im in images:
            assert np.allclose(im["R"] @ im["R"].T, np.eye(3), atol=1e-9)
