"""tests.test_anomaly_temporal : X4 病害时序追踪（两期图斑关联 + 五态）。

X3 只回答「这版图集哪里可疑」，单期数据无法区分「新出现的病害」与「一直就在的
正常起伏」——阈值那条路已经量到尽头（D12/D13：误报与真检在同一强度尺度上不可分）。
X4 换的是**证据类型**：同一个物理位置在两期之间**到底变没变**。

前提是两期共用同一套 UV 布局（B1 的图集继承：基准 mesh/uv/faces + 复测相机走
同一采样器，不重新展开），所以同一物理点落在同一纹素上——「跨期比较」才成立。
这条前提由 ``track`` 自己校验（岛掩膜必须几乎一致），不靠文档提醒。
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.test_anomaly_detect import _gradient, _inject, _round_island


def _pair(a_half: int | None = None, b_half: int | None = None,
          a_colour=(238, 230, 196), b_colour=(238, 230, 196),
          cx: int = 300, cy: int = 200) -> tuple[np.ndarray, np.ndarray]:
    """造两期图集：``a_half`` / ``b_half`` 分别是两期斑块的半边长（None = 无斑块）。"""
    a = _gradient(512)
    b = _gradient(512)
    if a_half:
        _inject(a, cx, cy, a_half, colour=a_colour)
    if b_half:
        _inject(b, cx, cy, b_half, colour=b_colour)
    return a, b


def _detect(img, island=None):
    from h3d.anomaly import detect_atlas_anomalies

    return detect_atlas_anomalies(img, island if island is not None
                                  else _round_island(512))


class TestStatesAreClassified:
    """五态：新增 / 扩大 / 缩小 / 稳定 / 消失。

    这五个状态直接对应保护工作里的处置判断——新增要干预、扩大要加密监测、
    缩小说明干预有效、稳定可继续观察、消失可结案。判错一个就等于给出错误的
    保护建议，所以每种状态都要有独立用例。
    """

    def test_new_spot_appears(self):
        from h3d.anomaly.temporal import track

        a, b = _pair(None, 20)
        rep = track(a, b, _detect(a), _detect(b))
        assert [c.state for c in rep.changes] == ["appeared"], rep.metrics
        assert rep.metrics["appeared"] == 1

    def test_grown_spot_is_grown(self):
        from h3d.anomaly.temporal import track

        a, b = _pair(10, 18)          # 面积约 3.2 倍
        rep = track(a, b, _detect(a), _detect(b))
        states = [c.state for c in rep.changes]
        assert states == ["grown"], states
        assert rep.changes[0].area_ratio == pytest.approx(3.24, rel=0.5)

    def test_shrunk_spot_is_shrank(self):
        from h3d.anomaly.temporal import track

        a, b = _pair(18, 10)
        rep = track(a, b, _detect(a), _detect(b))
        assert [c.state for c in rep.changes] == ["shrank"], rep.metrics

    def test_persisting_spot_is_stable(self):
        from h3d.anomaly.temporal import track

        a, b = _pair(14, 14)          # 面积几乎不变
        rep = track(a, b, _detect(a), _detect(b))
        assert [c.state for c in rep.changes] == ["stable"], rep.metrics

    def test_healed_spot_disappears(self):
        from h3d.anomaly.temporal import track

        a, b = _pair(14, None)
        rep = track(a, b, _detect(a), _detect(b))
        assert [c.state for c in rep.changes] == ["disappeared"], rep.metrics


class TestReverseControls:
    """反向对照：不该报的一个都不许报。"""

    def test_identical_atlas_reports_nothing(self):
        from h3d.anomaly.temporal import track

        a = _gradient(512)
        b = a.copy()
        rep = track(a, b, _detect(a), _detect(b))
        assert rep.changes == []

    def _one_sided_flag(self):
        """造「A 报了、B 没报，但两期像素完全相同」的一对结果。

        像素里确实有一处斑块（所以 A 会报），B 的结果被人为清空——模拟 X3 在
        阈值边界上抖动：同一片像素，一次报一次不报。X4 的答案必须是「没变」。
        """
        from h3d.anomaly import AnomalyResult

        a = _gradient(512)
        _inject(a, 300, 200, 16)
        b = a.copy()                       # 像素完全相同
        island = _round_island(512)
        res_a = _detect(a, island)
        assert res_a.regions, "夹具本身没造出报警"
        res_b = AnomalyResult(score=res_a.score, mask=np.zeros_like(res_a.mask),
                              island=island, regions=[],
                              metrics=dict(res_a.metrics))
        return a, b, res_a, res_b

    def test_one_side_flagged_but_pixels_unchanged_is_not_a_change(self):
        """一期有报警、另一期没有，但**像素没变** → 不是时序变化，是阈值抖动。

        这是 X4 存在的意义：单看 X3，两期结果不一样；加上「像素到底变没变」，
        结论应该是「没变」。挡它的就是 ΔE 门限。
        """
        from h3d.anomaly.temporal import track

        a, b, res_a, res_b = self._one_sided_flag()
        rep = track(a, b, res_a, res_b)
        assert rep.changes == [], "像素没变却报了变化"
        assert rep.metrics["filtered_by_delta_e"] > 0, "不是被门限挡下的，是压根没配到"

    def test_gate_switch_is_what_does_the_filtering(self):
        """双向对照：把门限关掉，同一对数据就必须报出来。

        没有这一条，「不报」可能来自「根本没关联上」——两种原因的处置完全不同
        （前者是判据生效，后者是关联逻辑坏了），必须能分开。
        """
        from h3d.anomaly.temporal import TemporalConfig, track

        a, b, res_a, res_b = self._one_sided_flag()
        loose = track(a, b, res_a, res_b, config=TemporalConfig(delta_e_min=0.0))
        assert loose.changes, "门限关掉后仍然不报 —— 关联或状态判定有问题"
        assert loose.changes[0].delta_e < TemporalConfig().delta_e_min


class TestMatchingGate:
    def test_far_apart_spots_are_not_matched(self):
        """相距很远的两个斑块不能配成「稳定」——那会把两个独立的病害粘成一条。"""
        from h3d.anomaly.temporal import track

        a = _gradient(512)
        b = _gradient(512)
        _inject(a, 120, 120, 14)
        _inject(b, 400, 400, 14)
        rep = track(a, b, _detect(a), _detect(b))
        states = sorted(c.state for c in rep.changes)
        assert states == ["appeared", "disappeared"], states

    def test_layout_mismatch_is_rejected(self):
        """两期必须共用同一套 UV 布局，否则「同一纹素」不成立，比较无意义。"""
        from h3d.anomaly import detect_atlas_anomalies
        from h3d.anomaly.temporal import track

        a = _gradient(512)
        b = _gradient(512)
        isle_a = _round_island(512)
        isle_b = np.zeros_like(isle_a)
        isle_b[:, :256] = True           # 完全不同的布局
        res_a = detect_atlas_anomalies(a, isle_a)
        res_b = detect_atlas_anomalies(b, isle_b)
        with pytest.raises(ValueError, match="布局"):
            track(a, b, res_a, res_b)


class TestDeltaE:
    def test_delta_e_is_cie76_not_8bit_lab(self):
        """ΔE 必须换算成 CIE76（L 取 0~100），否则门限 5 会变成「5/2.55」。

        OpenCV 的 8 位 Lab 里 L 是 0~255（= L* 的 2.55 倍），直接拿三通道算
        欧氏距离会让亮度差被高估 2.55 倍——门限值就失去标准含义。
        """
        from h3d.anomaly.temporal import delta_e_map

        lab = np.zeros((2, 2, 3), np.float32)
        lab[..., 0] = 128.0
        lab2 = lab.copy()
        lab2[..., 0] = 128.0 + 25.5           # L 抬 10 个 CIE 单位（8 位下 25.5）
        d = delta_e_map(lab, lab2)
        assert d.max() == pytest.approx(10.0, abs=0.2)
