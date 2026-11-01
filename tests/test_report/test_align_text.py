"""tests.test_report.test_align_text : 「几何-位姿对齐」一列必须认三态 ``status``。

为什么单列（2026-11-01，ADR-0009 D4）
------------------------------------
M4 的四条对齐路径原先各吐各的键，报表解析器也只能按各自的键猜。三态统一之后
`status` 成为唯一可靠的判据，但解析器的分支还是按旧的布尔键写：

    if align.get("rejected"):        # 读的是布尔键，不是 status
    if align.get("accepted") is True:

于是 `status="rejected"` 的对齐**落到最后的 N/A**——读者看到一片空白，而这一列
存在的唯一理由就是「岛内填充率偏低时区分法线问题还是对齐问题」。

这一列印错不是小事：ADR-0009 里那次真机事故（对齐把 coverage 从 21% 打到 1%）
正是靠它才能被读懂。
"""
from __future__ import annotations

import pytest

from h3d.report.html_export import _align_text


class TestThreeStates:
    def test_rejected(self):
        """被拒绝要有话说，不能是 N/A。"""
        txt = _align_text({"status": "rejected", "accepted": False,
                           "reject_reason": "投影覆盖 42.9% -> 1.0%",
                           "in_frame_before": 0.4293, "in_frame_after": 0.0101})
        assert txt != "N/A", "被拒绝的对齐印成 N/A —— 读者无从判断发生了什么"
        assert "回退" in txt or "无效" in txt, txt

    def test_rejected_shows_projection_numbers(self):
        """拒绝理由里最该露出来的是前后投影覆盖——它是判据本身。"""
        txt = _align_text({"status": "rejected", "accepted": False,
                           "in_frame_before": 0.4293, "in_frame_after": 0.0101})
        assert "42" in txt or "1.0%" in txt or "1%" in txt, txt

    def test_applied(self):
        txt = _align_text({"status": "applied", "accepted": True,
                           "method": "control_points", "scale_s": 1.02})
        assert "已采纳" in txt, txt

    def test_skipped_already_aligned(self):
        txt = _align_text({"status": "skipped",
                           "reason": "already_aligned（几何与 SfM 点云同系）"})
        assert "跳过" in txt and "同系" in txt, txt

    def test_skipped_from_sfm(self):
        txt = _align_text({"status": "skipped",
                           "reason": "geometry_from_sfm（同坐标系，无需对齐）"})
        assert "跳过" in txt, txt


class TestBackwardCompatibility:
    """老 manifest 只有 rejected/accepted 布尔、没有 status —— 仍要读懂。"""

    def test_legacy_rejected_flag(self):
        txt = _align_text({"rejected": True})
        assert "回退" in txt or "无效" in txt, txt

    def test_legacy_accepted_flag(self):
        txt = _align_text({"accepted": True, "method": "icp"})
        assert "已采纳" in txt, txt

    def test_error_falls_back(self):
        txt = _align_text({"error": "控制点不足"})
        assert "失败" in txt or "回退" in txt, txt

    def test_none_is_na(self):
        assert _align_text(None) == "N/A"
        assert _align_text({}) == "N/A"
