"""tests.test_texture.test_align_status : 对齐结论必须是**三态**，且能被报表读到。

问题（2026-11-01）
------------------
M4 有四条对齐路径，各自往外吐不同形状的 metrics：

| 路径 | 原有键 |
|---|---|
| `geometry_is_sfm` | `status="skipped"` |
| `already_aligned` | `status="skipped"` |
| 控制点两阶段 | `method/n_control_points/scale_s/rmse_after`——**没有 status** |
| 自动 ICP | `accepted/reject_reason`——**没有 status** |

报表按 `status` 取对齐结论，于是两条真跑过的路都印成空值：读者无法区分
「没对齐」「对齐了」「对齐了但被拒」。这与 ADR-0009 D3 加的那道投影验收直接
冲突——验收写进 `accepted` 和 `reject_reason`，报表却看不到。

三态的定义（与项目其它终态同一套词汇）：

    applied  —— 变换已应用
    rejected —— 判无效，保持/回退原坐标系
    skipped  —— 本来就不需要对齐

`accepted` 保留为布尔兼容键（外部脚本可能已经在读），但它与 `status` 必须自洽。
"""
from __future__ import annotations

import pytest

from h3d.photo_texture import apply_align_status


class TestThreeStates:
    def test_applied(self):
        m = apply_align_status({}, applied=True)
        assert m["status"] == "applied"
        assert m["accepted"] is True

    def test_rejected(self):
        m = apply_align_status({}, applied=False)
        assert m["status"] == "rejected"
        assert m["accepted"] is False

    def test_not_attempted_keeps_skipped(self):
        """already_aligned / geometry_is_sfm 不该被改写成 rejected。"""
        m = apply_align_status({"status": "skipped",
                                "reason": "already_aligned"}, applied=None)
        assert m["status"] == "skipped"
        assert m["reason"] == "already_aligned"
        assert "accepted" not in m, "没尝试对齐就不该有 accepted 布尔"

    def test_not_attempted_defaults_to_skipped(self):
        m = apply_align_status({}, applied=None)
        assert m["status"] == "skipped"


class TestRejectionDetailsSurvive:
    def test_reason_and_ratios_preserved(self):
        """拒绝理由与前后比例要原样留在 metrics 里（报表要能解释为什么不动）。"""
        src = {"reject_reason": "投影覆盖 42.9% -> 1.5%",
               "in_frame_before": 0.4293, "in_frame_after": 0.0148}
        m = apply_align_status(src, applied=False)
        assert m["reject_reason"] == "投影覆盖 42.9% -> 1.5%"
        assert m["in_frame_before"] == pytest.approx(0.4293)
        assert m["status"] == "rejected"

    def test_does_not_mutate_input(self):
        """返回副本——调用方可能还要拿原 metrics 记日志。"""
        src = {"method": "control_points"}
        m = apply_align_status(src, applied=True)
        assert "status" not in src
        assert m["method"] == "control_points"

    def test_status_is_always_one_of_three(self):
        for applied in (True, False, None):
            m = apply_align_status({}, applied)
            assert m["status"] in ("applied", "rejected", "skipped")
