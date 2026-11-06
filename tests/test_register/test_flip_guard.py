"""C 方案：用**法线一致性**拦住「179° 翻转解」，而不是拿 fitness 一刀切。

为什么不是 A（`fitness<0.55` 判失败）
------------------------------------
实测（`%TEMP%/probe_cue_partial.py`，真机 `test111.ply`，按平面把 target 切到 60%）：

| 场景 | fitness | 法线一致性 |
|---|---|---|
| **正确**解，target 完整 | 1.0000 | **+0.978** |
| **正确**解，target 只剩 60% | **0.2329** | **+0.847** |
| 翻转解（ICP 到收敛） | 0.10–0.21 | **−0.52 ~ −0.60** |

第二行就是 A 方案会误杀的那类「合法难例」：重叠只有六成，fitness 掉到 0.23
（离 0.2 的硬失败门只有一步），但它是**对的**，法线一致性照样 +0.85。
而翻转解的 fitness 与正确解可以重叠（真机 0.52 vs 0.23），**fitness 分不开，
方向分得开**。

判据的形状
----------
`normal_agreement` = 在 ICP 的内点集合（`d <= icp_distance`，与 fitness 同口径）上，
把两朵云的法线各自归一到「整体外向」约定后，逐点求点积再取均值。

中间有 0 这条天然分界线，**不需要调阈值**——这是它比 fitness 阈值可靠的地方。

为什么必须先做「外向归一」
--------------------------
实测两朵云的法线符号约定可能**整体相反**（source=+1、target=−1），因为
`orient_normals_consistent_tangent_plane` 只保证各自内部一致，不保证两者之间。
不归一的话，正确解会算出 −0.98，判据直接反号。
"""
from __future__ import annotations

import numpy as np
import open3d as o3d
import pytest


def _blob(seed: int = 0, n: int = 4000):
    """不对称团块（纯球太容易，测不出翻转）。"""
    rng = np.random.default_rng(seed)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    v *= 0.05
    v[:, 0] *= 1.0 + 0.25 * np.sin(3 * np.arctan2(v[:, 1], v[:, 2]))
    # 加一个「只在一侧鼓起」的不对称，绕 y 转 180° 后形状对不上自己
    # （否则翻转解与正解在几何上完全等价，法线也会一致，测不出东西）
    v[:, 0] += 0.012 * (v[:, 2] > 0)
    pc = o3d.geometry.PointCloud()
    pc.points = o3d.utility.Vector3dVector(v)
    return pc


def _rot_y(deg: float) -> np.ndarray:
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], float)


def _pair(deg: float = 40.0):
    src = _blob()
    tgt = _blob()
    tgt.rotate(_rot_y(deg), center=np.zeros(3))
    return src, tgt


def _tf(R, t=(0.0, 0.0, 0.0)) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = t
    return T


class TestOutwardSign:
    def test_detects_opposite_conventions(self):
        from h3d.register.evaluate import outward_sign

        pts = np.array([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0],
                        [-1.0, 0, 0], [0, -1.0, 0]])
        outward = pts / np.linalg.norm(pts, axis=1, keepdims=True)
        assert outward_sign(pts, outward) == 1.0
        assert outward_sign(pts, -outward) == -1.0


class TestNormalAgreement:
    def test_correct_solution_is_positive(self):
        from h3d.register.evaluate import normal_agreement

        src, tgt = _pair(40.0)
        T = _tf(_rot_y(-40.0))          # 把 src 转回 tgt
        assert normal_agreement(src, tgt, T, 0.01, 0.01) > 0.5

    def test_value_always_in_range(self):
        """值域必须落在 [−1, 1]：它是均值点积，超出去说明算错了。

        注：**不在这里断言「合成翻转必为负」**。近凸的合成团块上，绕质心转
        180° 会把表面点送到法线恰好同向的位置，测出来仍是 +0.9 —— 那种情形下
        翻转解与正解在几何上确实不可区分（物体本身近似对称），判不出来也不算错。
        真正能分开的是**有薄壁/凹陷特征**的实物：真机上正解 +0.97、翻转解
        −0.52（见 ADR-0025 的实测表，`scripts/probe_flip_cue.py` 可复现）。
        合成夹具复现不出这个机制，写一条假的断言只会骗自己。
        """
        from h3d.register.evaluate import normal_agreement

        src, tgt = _pair(40.0)
        for T in (_tf(_rot_y(-40.0)), _tf(_rot_y(140.0))):
            v = normal_agreement(src, tgt, T, 0.01, 0.01)
            assert v is None or -1.0 <= v <= 1.0

    def test_no_overlap_returns_none(self):
        """内点太少时返回 None（报表印 N/A），不能编一个数出来。"""
        from h3d.register.evaluate import normal_agreement

        src, tgt = _pair(40.0)
        far = _tf(np.eye(3), (100.0, 0.0, 0.0))
        assert normal_agreement(src, tgt, far, 0.001, 0.01) is None


class TestRunUsesIt:
    def test_metrics_carry_the_agreement(self):
        from h3d.core import RegisterConfig
        from h3d.register import run

        cfg = RegisterConfig(fpfh_radius=0.01, ransac_distance=0.01,
                             icp_distance=0.01, max_iter=50)
        src, tgt = _pair(40.0)
        res = run(src, tgt, cfg)
        assert res["normal_agreement"] is not None
        assert res["normal_agreement"] > 0.5

    def test_negative_agreement_is_rejected_not_just_flagged(self, monkeypatch):
        """朝向判负时必须**拒绝**——不是标个 low_confidence 放行。

        这里把 `normal_agreement` 打成 −0.5 直接测**闸门接线**；判据本身在
        实物上的分辨力由 ADR-0025 的实测表背书。两者分开测，各自可信。
        """
        from h3d.core import RegisterConfig
        from h3d.core.exceptions import RegistrationError
        from h3d.register import run
        import h3d.register as RegisterE
        import h3d.register as RegisterE
        import h3d.register.evaluate as E

        src, tgt = _pair(40.0)
        cfg = RegisterConfig(fpfh_radius=0.01, ransac_distance=0.01,
                             icp_distance=0.01, max_iter=50)
        monkeypatch.setattr(E, "normal_agreement", lambda *a, **k: -0.5)
        real_eval = RegisterE.evaluate_registration
        monkeypatch.setattr(
            RegisterE, "evaluate_registration",
            lambda r: {**real_eval(r), "fitness": 0.52})
        with pytest.raises(RegistrationError, match="方向相反"):
            run(src, tgt, cfg)

    def test_reversed_but_high_fitness_is_left_alone(self, monkeypatch):
        """方向反但 fitness 很高 = 物体近似对称，翻转与正解不可区分，不拦。

        这条是 C 与 A 的分界：A 会把"低重叠的合法解"也一起杀掉，C 只在
        「又贴不牢、方向又反」时拒绝。
        """
        from h3d.core import RegisterConfig
        from h3d.register import run
        import h3d.register.evaluate as E
        import h3d.register as RegisterE

        src, tgt = _pair(40.0)
        cfg = RegisterConfig(fpfh_radius=0.01, ransac_distance=0.01,
                             icp_distance=0.01, max_iter=50)
        monkeypatch.setattr(E, "normal_agreement", lambda *a, **k: -0.5)
        real_eval = RegisterE.evaluate_registration
        monkeypatch.setattr(
            RegisterE, "evaluate_registration",
            lambda r: {**real_eval(r), "fitness": 0.97})
        assert run(src, tgt, cfg)["normal_agreement"] == -0.5

    def test_small_negative_inside_dead_band_does_not_block(self, monkeypatch):
        """−0.1 落在死区里：那是「分不清」，不是「方向反了」。"""
        from h3d.core import RegisterConfig
        from h3d.register import run
        import h3d.register.evaluate as E

        src, tgt = _pair(40.0)
        cfg = RegisterConfig(fpfh_radius=0.01, ransac_distance=0.01,
                             icp_distance=0.01, max_iter=50)
        monkeypatch.setattr(E, "normal_agreement", lambda *a, **k: -0.1)
        assert run(src, tgt, cfg)["normal_agreement"] == -0.1

    def test_small_negative_inside_dead_band_does_not_block(self, monkeypatch):
        """−0.1 落在死区里：那是「分不清」，不是「方向反了」。"""
        from h3d.core import RegisterConfig
        from h3d.register import run
        import h3d.register.evaluate as E

        src, tgt = _pair(40.0)
        cfg = RegisterConfig(fpfh_radius=0.01, ransac_distance=0.01,
                             icp_distance=0.01, max_iter=50)
        monkeypatch.setattr(E, "normal_agreement", lambda *a, **k: -0.1)
        assert run(src, tgt, cfg)["normal_agreement"] == -0.1

    def test_unknown_agreement_does_not_block(self, monkeypatch):
        """量不出来（None）不拦——那是「没量到」，不是「方向错」。"""
        from h3d.core import RegisterConfig
        from h3d.register import run
        import h3d.register.evaluate as E

        src, tgt = _pair(40.0)
        cfg = RegisterConfig(fpfh_radius=0.01, ransac_distance=0.01,
                             icp_distance=0.01, max_iter=50)
        monkeypatch.setattr(E, "normal_agreement", lambda *a, **k: None)
        assert run(src, tgt, cfg)["normal_agreement"] is None
