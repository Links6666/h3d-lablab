"""位姿约定归一：用稀疏点投影判定，而不是靠 det 猜。

2026-11-03 真机事故：`capture_meta.json` 写于 2026-09-15「坐标系修复」之前，
存的是**原生 COLMAP 格式**（``R_co`` + ``tvec``，前方 +Z）。管线按「t = 相机
中心、前方 -Z」读，把 tvec 当成了相机位置 → 30 台里 19 台一条纹素都投不上。
"""
import numpy as np
import pytest


CAM = {1: {"w": 800, "h": 600, "fx": 800.0, "fy": 800.0, "cx": 400.0, "cy": 300.0}}


def _pipeline_pose(center, look_at, up=(0.0, 0.0, 1.0)):
    """构造管线约定的 (R, C)：前方 -Z、t 是相机中心。"""
    C = np.asarray(center, float)
    f = np.asarray(look_at, float) - C
    f /= np.linalg.norm(f)                      # 指向被摄物
    up = np.asarray(up, float)
    s = np.cross(f, up)
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    R = -np.stack([s, u, f], axis=0)            # 前方 -Z：第三列取 -f
    return R, C


def _to_colmap_raw(R_pipe, C):
    """管线位姿 -> COLMAP 原生 (R_co, tvec)：R_co = -R_pipe, tvec = -R_co·C。"""
    R_co = -R_pipe
    tvec = -R_co @ C
    return R_co, tvec


def _object_points(n=400, seed=0):
    """球面上的点，当作「SfM 稀疏点」探针（与相机同系）。"""
    rng = np.random.default_rng(seed)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    return v * 0.5


def _images_around(points, convert):
    """在点云周围摆 4 台相机，返回 (images)；convert=True 时存成原生格式。"""
    c = points.mean(axis=0)
    r = 3.0
    out = []
    for i in range(4):
        az = 2 * np.pi * i / 4
        C = c + np.array([r * np.cos(az), r * np.sin(az), 0.4])
        R_pipe, _ = _pipeline_pose(C, c)
        R, t = _to_colmap_raw(R_pipe, C) if convert else (R_pipe, C)
        out.append({"name": f"img_{i}.jpg", "R": R, "t": t, "cam_id": 1})
    return out


class TestNormalisePoseConvention:
    def test_raw_colmap_convention_is_converted(self):
        """原生格式（det=+1、t=tvec）→ 转换后稀疏点必须投得上。"""
        from h3d.photo_texture import normalise_pose_convention

        pts = _object_points()
        images = _images_around(pts, convert=True)
        m = normalise_pose_convention(images, CAM, pts)

        assert m["pose_converted"] is True
        assert m["pose_convention"] == "colmap_raw"
        assert m["pose_probe_converted"] > 0.9, m
        assert m["pose_probe_as_is"] < 0.2, m

    def test_pipeline_convention_is_left_alone(self):
        """已经是管线格式的不能被翻（翻错方向 = 把好文件读坏）。"""
        from h3d.photo_texture import normalise_pose_convention

        pts = _object_points()
        images = _images_around(pts, convert=False)
        R0 = [im["R"].copy() for im in images]
        t0 = [im["t"].copy() for im in images]
        m = normalise_pose_convention(images, CAM, pts)

        assert m["pose_converted"] is False
        assert m["pose_convention"] == "pipeline"
        for a, b, c, d in zip(images, R0, [im["t"] for im in images], t0):
            np.testing.assert_allclose(a["R"], b)
            np.testing.assert_allclose(c, d)

    def test_converted_result_matches_pipeline_ground_truth(self):
        """转换出来的 (R, C) 必须逐元素等于原始管线位姿。"""
        from h3d.photo_texture import normalise_pose_convention

        pts = _object_points()
        truth = _images_around(pts, convert=False)
        images = _images_around(pts, convert=True)
        normalise_pose_convention(images, CAM, pts)

        for got, want in zip(images, truth):
            np.testing.assert_allclose(got["R"], want["R"], atol=1e-9)
            np.testing.assert_allclose(got["t"], want["t"], atol=1e-9)

    def test_without_probe_points_it_does_not_guess(self):
        """没有稀疏点就判不出来 —— 不许猜（猜错比不改更糟）。"""
        from h3d.photo_texture import normalise_pose_convention

        pts = _object_points()
        images = _images_around(pts, convert=True)
        R0 = images[0]["R"].copy()
        m = normalise_pose_convention(images, CAM, None)

        assert m["pose_convention"] == "unknown"
        assert m["pose_converted"] is False
        np.testing.assert_allclose(images[0]["R"], R0)

    def test_when_neither_reaches_the_threshold_it_does_not_guess(self):
        """两种解释都达不到判据 → 报 unknown、不动。

        这是防御分支，用 `min_frac > 1` 强制走到（真实数据几乎不会命中）。
        为什么不用「把探针挪远」来构造：远点会塌到主点附近，**反而落在画幅
        正中**；「放到相机背后」也不行——环形布置下 A 的背后正对着 B。
        这两次都试过了，构造出来的都是 100% 命中，测不到这一支。
        """
        from h3d.photo_texture import normalise_pose_convention

        pts = _object_points()
        images = _images_around(pts, convert=True)
        R0 = images[0]["R"].copy()
        m = normalise_pose_convention(images, CAM, pts, min_frac=1.01)

        assert m["pose_convention"] == "unknown", m
        assert m["pose_converted"] is False
        np.testing.assert_allclose(images[0]["R"], R0)
