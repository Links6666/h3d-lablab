"""tests.test_texture.test_align_acceptance : 对齐的**投影级**验收（ADR-0009 D3）。

为什么不能只看 `already_aligned` / `accourated`
----------------------------------------------
真机 2026-11-01：同一份输入只改 `geometry_is_sfm`——

| 配置 | coverage | in_frame | 用上的相机 |
|---|---|---|---|
| 跳过对齐 | 21.22% | 14.54% | 20 |
| 走 icp_align | **1.16%** | **1.46%** | **3** |

对齐之后差 18 倍，而 `align_points_to_mesh` 的 `accepted` 闸门放过了它——那个
闸门看的是「包围盒剩几倍 / 几何离点云多远」，量的是**几何之间的相似度**，
量不到**几何还在不在相机视野里**。

`in_frame_ratio` 直接量后者：有多少比例的顶点能投进至少一台相机的画幅。
它的判据是「对齐后不许比对齐前更差」，而不是任何绝对阈值——因为真机上
「不对齐」本身也只有 14.5%，绝对值在这个数据上根本没有可用门槛。
"""
from __future__ import annotations

import numpy as np
import pytest

from h3d.photo_texture import in_frame_ratio


def _sphere(n=600, radius=0.1):
    i = np.arange(n, dtype=np.float64) + 0.5
    phi = np.arccos(1.0 - 2.0 * i / n)
    theta = np.pi * (1.0 + 5.0 ** 0.5) * i
    return radius * np.stack([np.cos(theta) * np.sin(phi),
                              np.sin(theta) * np.sin(phi),
                              np.cos(phi)], axis=1)


def _look_at_origin(center) -> np.ndarray:
    """相机在 ``center``、看向原点时的 world→cam 旋转矩阵。

    ``pc = R @ (p - t)``，代码约定 ``z < 0`` 在前方 → 相机 z 轴 = center 的单位向量，
    这样原点的 z 分量 = z_cam·(-center) = -|center| < 0，落进前方。
    """
    c = np.asarray(center, dtype=np.float64)
    z_cam = c / np.linalg.norm(c)
    up = np.array([0.0, 0.0, 1.0])
    if abs(np.dot(up, z_cam)) > 0.99:          # 相机在极点附近，换一个参考轴
        up = np.array([0.0, 1.0, 0.0])
    x_cam = np.cross(up, z_cam)
    x_cam /= np.linalg.norm(x_cam)
    y_cam = np.cross(z_cam, x_cam)
    return np.stack([x_cam, y_cam, z_cam])


def _ring_cameras(n=8, dist=0.6, size=800):
    """绕原点均匀布相机，每台都朝原点看。

    判据用 `z < 0` 表示「点在相机前方」，与 `photo_texture` 的投影阶段一致。
    相机 x/y 轴由 `_look_at_origin` 构造，保证原点投在画幅正中。
    """
    images, cameras = [], {}
    for k in range(n):
        a = 2 * np.pi * k / n
        c = np.array([dist * np.cos(a), dist * np.sin(a),
                      dist * 0.35])          # 抬一点，避免全在同一平面
        images.append({
            "name": f"c{k}.jpg",
            "cam_id": 1,
            "t": c.tolist(),
            "R": _look_at_origin(c).tolist(),
        })
    cameras[1] = {"fx": size * 0.8, "fy": size * 0.8,
                  "cx": size / 2.0, "cy": size / 2.0,
                  "w": size, "h": size}
    return images, cameras


class TestInFrameRatio:
    def test_ring_of_cameras_sees_whole_sphere(self):
        """八台相机环绕 → 球面顶点几乎全部投进至少一台画幅。"""
        images, cameras = _ring_cameras()
        r = in_frame_ratio(_sphere(), images, cameras)
        assert r > 0.95, f"环绕布光下只有 {r:.2%} 顶点投进画幅"

    def test_geometry_outside_frustum_falls_out(self):
        """几何被搬到视锥外 → 投不进。

        用单台相机（在 +z、R=I、看 -z）把几何放到 x=50：z=-1 处视锥半宽只有
        0.625，50 远在画幅外。不用「搬 3 米」——相机看向原点时 3 米偏移仍在
        宽视锥内，那种场景测不出东西。
        """
        cam = {"fx": 640.0, "fy": 640.0, "cx": 400.0, "cy": 400.0,
               "w": 800, "h": 800}
        images = [{"name": "c.jpg", "cam_id": 1,
                   "t": [0.0, 0.0, 1.0], "R": np.eye(3).tolist()}]
        pts = _sphere() + np.array([50.0, 0.0, 0.0])
        assert in_frame_ratio(pts, images, {1: cam}) == 0.0

    def test_behind_camera_is_not_counted(self):
        """点在相机背后（z>0）不算。

        相机约定 z<0 在前方；写反会让「投进画幅」整体反号 —— ADR-0005 里
        「cos>0.3 判据反号」踩过同款。R=I、相机在 (0,0,1)，原点在其前方
        （z=-1），(0,0,2) 在其背后（z=+1）。
        """
        cam = {"fx": 640.0, "fy": 640.0, "cx": 400.0, "cy": 400.0,
               "w": 800, "h": 800}
        images = [{"name": "c.jpg", "cam_id": 1,
                   "t": [0.0, 0.0, 1.0], "R": np.eye(3).tolist()}]
        front = np.array([[0.0, 0.0, 0.0]])
        behind = np.array([[0.0, 0.0, 2.0]])
        assert in_frame_ratio(front, images, {1: cam}) == 1.0
        assert in_frame_ratio(behind, images, {1: cam}) == 0.0

    def test_empty_inputs_are_zero_not_nan(self):
        images, cameras = _ring_cameras()
        assert in_frame_ratio(np.zeros((0, 3)), images, cameras) == 0.0
        assert in_frame_ratio(_sphere(), [], cameras) == 0.0

    def test_deterministic_under_sampling(self):
        """抽样必须可复现——对齐决策不能每次跑出不同结论。"""
        images, cameras = _ring_cameras()
        pts = _sphere(2000)
        a = in_frame_ratio(pts, images, cameras, sample=300)
        b = in_frame_ratio(pts, images, cameras, sample=300)
        assert a == b


class TestRegressionRule:
    """比较规则：对齐后投影覆盖掉一半以上就判「变差」。

    为什么是「相对」判据：真机「不对齐」的基线只有 14.54%，任何绝对阈值都
    没有可用门槛。而拒绝 = 保持原坐标系 = 等于没做对齐，永远是安全侧。
    """

    def test_clear_drop_is_rejected(self):
        from h3d.photo_texture import alignment_regressed

        assert alignment_regressed(0.2122, 0.0116) is True   # 真机实测那组
        assert alignment_regressed(0.50, 0.20) is True

    def test_small_drop_is_tolerated(self):
        """轻微变差要放行——否则对齐永远被拒，功能等于没做。"""
        from h3d.photo_texture import alignment_regressed

        assert alignment_regressed(0.50, 0.45) is False
        assert alignment_regressed(0.50, 0.50) is False

    def test_improvement_is_accepted(self):
        from h3d.photo_texture import alignment_regressed

        assert alignment_regressed(0.10, 0.60) is False

    def test_zero_baseline_does_not_block(self):
        """基线为 0 时没有可比信息，不能拦——否则这类件永远无法对齐。"""
        from h3d.photo_texture import alignment_regressed

        assert alignment_regressed(0.0, 0.0) is False
        assert alignment_regressed(0.0, 0.4) is False


class TestAlignmentNeeded:
    """「还需不需要对齐」只问一件事：几何在不在相机画幅里。

    真机教训（2026-11-01）：`10_clean` 对 30 台相机 in_frame = 86.7%，已经与位姿
    同系，但对齐照样跑了——结果是 UV 光栅化为空、M4 降级顶点色。跳过对齐后
    coverage 21.2% → 56.8%、岛内填充 31.1% → 87.6%。
    """

    def test_already_in_frame_does_not_need_alignment(self):
        from h3d.photo_texture import alignment_needed

        assert alignment_needed(0.8671) is False     # 真机实测值
        assert alignment_needed(1.0) is False

    def test_mostly_out_of_frame_needs_alignment(self):
        from h3d.photo_texture import alignment_needed

        assert alignment_needed(0.1454) is True      # 脏链路实测值
        assert alignment_needed(0.0) is True

    def test_threshold_boundary(self):
        from h3d.photo_texture import alignment_needed

        assert alignment_needed(0.5) is False        # 恰好一半：判为不需要
        assert alignment_needed(0.49) is True


class TestAlignmentNeeded:
    """「还需不需要对齐」只问一件事：几何在不在相机画幅里。

    真机教训（2026-11-01）：`10_clean` 对 30 台相机 in_frame = 86.7%，已经与位姿
    同系，但对齐照样跑了——结果是 UV 光栅化为空、M4 降级顶点色。跳过对齐后
    coverage 21.2% → 56.8%、岛内填充 31.1% → 87.6%。
    """

    def test_already_in_frame_does_not_need_alignment(self):
        from h3d.photo_texture import alignment_needed

        assert alignment_needed(0.8671) is False     # 真机实测值
        assert alignment_needed(1.0) is False

    def test_mostly_out_of_frame_needs_alignment(self):
        from h3d.photo_texture import alignment_needed

        assert alignment_needed(0.1454) is True      # 脏链路实测值
        assert alignment_needed(0.0) is True

    def test_threshold_boundary(self):
        from h3d.photo_texture import alignment_needed

        assert alignment_needed(0.5) is False        # 恰好一半：判为不需要
        assert alignment_needed(0.49) is True
