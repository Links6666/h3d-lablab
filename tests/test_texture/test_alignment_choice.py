"""对齐路径择优：两条都跑，按「对齐后有多少面能被正面看到」取优。

2026-11-03 真机：M4 的策略是「控制点优先、失败才回退自动 ICP」。控制点那条
解出了约 90° 的旋转错（与自动 ICP 的形状主轴 |cos|=0.001），却因为
`in_frame` 从 0 → 1.0 而被判通过——**那个闸门区分不了 0° 和 90°**：对齐前激光系
里根本没有相机，任何把物体搬到相机前方的解都会报 1.0。

能区分的量是「对齐后有多少面能被任一台相机正面看到」：真机实测自动 ICP 0.98、
控制点 0.69。
"""
import numpy as np
import open3d as o3d
import pytest


CAM = {1: {"w": 800, "h": 600, "fx": 800.0, "fy": 800.0, "cx": 400.0, "cy": 300.0}}


def _sphere(radius=1.0, resolution=24):
    m = o3d.geometry.TriangleMesh.create_sphere(radius=radius,
                                                resolution=resolution)
    m.compute_vertex_normals()
    return m


def _ring_cameras(radius=4.0, n=8):
    """围一圈 + 两个极点。

    只摆赤道一圈是不够的：球两极的面（法线接近 ±z）对任何一台赤道相机的
    cos 都是负的，实测只能得到 0.65——那不是判据不好，是**采集没覆盖**。
    真实采集是三维分布的，所以这里补上极点相机。
    """
    out = []
    for i in range(n):
        a = 2 * np.pi * i / n
        t = np.array([radius * np.cos(a), radius * np.sin(a), 0.3])
        out.append({"name": f"c{i}.jpg", "t": t,
                    "R": np.eye(3), "cam_id": 1})
    for name, t in (("up", np.array([0.0, 0.0, radius])),
                    ("down", np.array([0.0, 0.0, -radius]))):
        out.append({"name": name + ".jpg", "t": t,
                    "R": np.eye(3), "cam_id": 1})
    return out


class TestVisibleScore:
    def test_surrounded_object_scores_high(self):
        """相机环绕一圈 → 绝大多数面都能被正面看到。"""
        from h3d.photo_texture import visible_score

        m = _sphere()
        s = visible_score(np.asarray(m.vertices), np.asarray(m.triangles),
                          _ring_cameras(), CAM)
        assert s > 0.95, s

    def test_object_behind_all_cameras_scores_low(self):
        """把物体挪到所有相机背后 → 分数必须掉下来。

        这里用翻转法线等价地模拟「看到的是背面」——择优要能分辨朝向对错。
        """
        from h3d.photo_texture import visible_score

        m = _sphere()
        v = np.asarray(m.vertices).copy()
        t = np.asarray(m.triangles).copy()
        t = t[:, ::-1]          # 反绕 → 面法线整体朝内
        s = visible_score(v, t, _ring_cameras(), CAM)
        # 口径是 max(正向, 反向)，整体翻面仍应算「能看见」——这条锁语义：
        # 择优不能用「全局翻面」去惩罚一个只是符号不同的解。
        assert s > 0.95, s

    def test_half_ring_scores_about_half(self):
        """只在一侧摆相机 → 背面的面看不到，分数应明显低于 1。"""
        from h3d.photo_texture import visible_score

        m = _sphere()
        cams = []
        for i in range(4):
            a = np.pi * 0.5 * i / 3          # 只覆盖 90° 弧
            cams.append({"name": f"c{i}.jpg",
                         "t": np.array([4 * np.cos(a), 4 * np.sin(a), 0.2]),
                         "R": np.eye(3), "cam_id": 1})
        s = visible_score(np.asarray(m.vertices), np.asarray(m.triangles),
                          cams, CAM)
        assert 0.3 < s < 0.95, s

    def test_empty_input_is_zero_not_crash(self):
        from h3d.photo_texture import visible_score

        assert visible_score(np.zeros((0, 3)), np.zeros((0, 3), int),
                             _ring_cameras(), CAM) == 0.0
        assert visible_score(np.asarray(_sphere().vertices),
                             np.asarray(_sphere().triangles), [], CAM) == 0.0


class TestChooseAlignment:
    def test_picks_the_candidate_that_is_actually_facing_the_cameras(self):
        """两个候选：一个摆对、一个整体挪到远处 → 必须选摆对的。"""
        from h3d.photo_texture import choose_alignment

        m = _sphere()
        v = np.asarray(m.vertices)
        t = np.asarray(m.triangles)
        cams = _ring_cameras()

        good = v.copy()
        bad = v + np.array([80.0, 0.0, 0.0])      # 挪到相机视锥之外
        name, verts, metrics = choose_alignment(
            [("control_points", bad, t, {"method": "cp"}),
             ("auto_icp", good, t, {"method": "icp"})],
            cams, CAM)
        assert name == "auto_icp", (name, metrics)
        np.testing.assert_allclose(verts, good)

    def test_reports_every_candidate_score(self):
        """择优必须把每个候选的分数留下——否则下次争论没有依据。"""
        from h3d.photo_texture import choose_alignment

        m = _sphere()
        v, t = np.asarray(m.vertices), np.asarray(m.triangles)
        name, _, metrics = choose_alignment(
            [("auto_icp", v, t, {})], _ring_cameras(), CAM)
        assert name == "auto_icp"
        assert "align_candidates" in metrics
        assert metrics["align_candidates"][0]["name"] == "auto_icp"
        assert 0.0 <= metrics["align_candidates"][0]["visible"] <= 1.0

    def test_single_candidate_is_returned_as_is(self):
        from h3d.photo_texture import choose_alignment

        m = _sphere()
        v, t = np.asarray(m.vertices), np.asarray(m.triangles)
        name, verts, _ = choose_alignment([("only", v, t, {})],
                                          _ring_cameras(), CAM)
        assert name == "only"
        np.testing.assert_allclose(verts, v)

    def test_empty_candidates_raise(self):
        from h3d.photo_texture import choose_alignment

        with pytest.raises(ValueError):
            choose_alignment([], _ring_cameras(), CAM)
