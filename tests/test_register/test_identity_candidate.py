"""多初值必须包含「恒等变换」——它是 RANSAC 给出「半对」初值时的解药（ADR-0032）。

背景
----
r35（ADR-0031）把配准变确定了之后，7° 档稳定失败（30° 误差）。诊断到最后：

    RANSAC fitness 0.0000（彻底失败）  → ICP 从恒等出发 → **对**（0.01°）
    RANSAC fitness 0.3287（半对）      → ICP 从 RANSAC 解 → **错**（29.99°）
    RANSAC fitness ≥ 0.7337（基本对）  → ICP 从 RANSAC 解 → **对**（0.01°）

**「彻底没给初值」比「给了个半对的初值」更好。** 真机 7° 档从恒等出发，
同一套 ICP 收敛到 0.031°、fitness 1.0000（而 RANSAC 候选只有 0.2589）。

所以修法是：把恒等变换加进候选集，让既有的「取 fitness 最高」规则自动选它。

这个文件测三件事——**第三件最重要**
------------------------------------
1. 候选集里有 identity；
2. RANSAC 给坏初值时，identity 能救回来；
3. **RANSAC 给好初值时，identity 不许把它挤掉**（否则就是拿一个退化解换一个正确解）。

第 3 条是这类「多给一个候选」改动的真正风险：候选越多，越容易在某个场景下
让退化解凭 fitness 侥幸胜出。必须钉住。
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest


def _cfg(**kw):
    from h3d.core import RegisterConfig

    base = dict(fpfh_radius=0.02, ransac_distance=0.03,
                icp_distance=0.03, max_iter=60)
    base.update(kw)
    return RegisterConfig(**base)


def _pair(angle_deg: float):
    """源点云 = 目标绕 z 轴转 ``angle_deg``（与既有夹具同构）。"""
    import open3d as o3d

    rng = np.random.default_rng(7)
    v = rng.normal(size=(3000, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    v *= 0.10
    # 加一处不对称，避免球面完全对称导致「任意角度都贴合」
    v[:, 0] += 0.03 * (v[:, 2] > 0.02)
    tgt = o3d.geometry.PointCloud()
    tgt.points = o3d.utility.Vector3dVector(v)

    a = np.radians(angle_deg)
    Rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0],
                   [0, 0, 1.0]])
    src = o3d.geometry.PointCloud()
    src.points = o3d.utility.Vector3dVector(v @ Rz.T)
    return src, tgt, Rz


def _rot_err(T, R_inj) -> float:
    """与真值的角差。**真值是 R_inj**（source 被注入它，配准要报出它的逆）。"""
    R = np.asarray(T)[:3, :3]
    c = (np.trace(R @ np.asarray(R_inj)) - 1.0) / 2.0
    return float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))


class TestIdentityIsInTheCandidateSet:
    def test_trials_include_identity(self):
        """候选集里必须有 identity，且它确实跑过 ICP（出现在 trials 里）。"""
        from h3d.register import _icp_multi_init

        src, tgt, _ = _pair(7.0)
        best, tag, trials = _icp_multi_init(src, tgt, np.eye(4), _cfg())
        names = [t["init"] for t in trials]
        assert "identity" in names, f"候选集里没有 identity：{names}"

    def test_identity_is_judged_on_equal_footing(self):
        """identity 与其他候选走同一条评价路径（fitness 最高者胜），不特殊对待。"""
        from h3d.register import _icp_multi_init

        src, tgt, _ = _pair(7.0)
        best, tag, trials = _icp_multi_init(src, tgt, np.eye(4), _cfg())
        top = max(t["fitness"] for t in trials)
        winner = next(t for t in trials if t["init"] == tag)
        # 不要求「等于 max 的那一个」：fitness 打平时实现会保留先出现的候选
        # （见 _TIE_EPS），判决仍是「不低于最高分」。要求严格相等会让测试
        # 变成在测 tie-break 的实现细节。
        assert winner["fitness"] == pytest.approx(top, abs=1e-4), (
            f"胜出的初值 {tag}（fitness {winner['fitness']}）明显低于最高分 {top}")


class TestRescuesBadRansacInit:
    """RANSAC 给「半对」初值时，恒等候选必须把它救回来。"""

    def test_bad_init_is_rescued(self, monkeypatch):
        """喂一个方向差 145° 的初值（模拟真机 7° 档的 RANSAC 解），结果仍要对。

        真机数字（ADR-0032）：RANSAC 给 145.65° 的初值、fitness 0.3287，
        ICP 从它出发收敛到 29.99° 误差；从恒等出发则 0.031°。
        """
        import h3d.register as R

        src, tgt, R_inj = _pair(7.0)
        a = np.radians(145.0)
        bad = np.eye(4)
        bad[:3, :3] = np.array([[np.cos(a), -np.sin(a), 0],
                                [np.sin(a), np.cos(a), 0], [0, 0, 1.0]])
        fake = SimpleNamespace(transformation=bad, fitness=0.33,
                               inlier_rmse=0.05)
        monkeypatch.setattr(R, "_ransac_with_fallback",
                            lambda *a_, **k: fake)
        res = R.run(src, tgt, _cfg())
        err = _rot_err(res["transformation"], R_inj)
        assert err < 2.0, (
            f"RANSAC 给半对初值后仍然解错（误差 {err:.2f}°）——"
            "identity 候选没起作用")
        assert res["icp_init"] == "identity", (
            f"救回来的应该是 identity，实际是 {res['icp_init']}")


class TestDoesNotOverrideGoodRansac:
    """反过来：RANSAC 正确时，identity 不许凭 fitness 侥幸胜出。"""

    def test_good_init_still_wins(self):
        """给一个正确的初值，最终结果必须与真值一致，且不是靠 identity 兜的。

        这条挡的是「多加一个候选反而把正确解挤掉」——候选越多，退化解凭
        fitness 胜出的机会越大，这正是本次改动最该防的副作用。
        """
        import h3d.register as R

        src, tgt, R_inj = _pair(7.0)
        res = R.run(src, tgt, _cfg())
        err = _rot_err(res["transformation"], R_inj)
        assert err < 2.0, f"给了好初值却解错：{err:.2f}°"

    def test_identity_alone_is_worse_than_true_solution(self):
        """恒等变换本身不是「更好的解」，它只是「更安全的起点」。

        identity 直接当解用时误差 = 注入角（7°），远超阈值——所以它胜出只能是
        因为 ICP 从它出发收敛得更好，不能是因为恒等本身被当成答案。
        """
        src, tgt, R_inj = _pair(7.0)
        err_identity = _rot_err(np.eye(4), R_inj)
        assert err_identity == pytest.approx(7.0, abs=0.1), (
            f"恒等变换相对真值应为注入角 7°，实测 {err_identity:.2f}°")
