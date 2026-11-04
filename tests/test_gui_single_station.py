"""开跑前说清「这件只有 1 份几何」——否则「M2 完成」会被读成「配准通过了」。

2026-11-04 的遗留项
------------------
`D:/h3d_relic_test` 这类项目里 `00_raw/<件>/` 只有一份点云：M2 走**单站直通**
（恒等落盘、十几毫秒），日志写「M2 完成」，界面灯变绿。用户看到的结论是
「配准成功」，而事实是 **FPFH + RANSAC + ICP 一次都没跑过**。

报表侧 r21 已经说清了（`identity` + `identity_reason=single_scan` → HTML 里那段
「配准节点在本件没有被执行过」），但那是**跑完之后**才读得到的东西。用户是先看
到「完成」的，很少有人会回头翻报表去推翻自己刚才的印象。

单站本身**不是缺陷**——中小型文物常只有一站扫描，恒等直通是正确终态。要修的
只是「没说」：开跑前放一句提醒，别让人把直通读成验证。
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

import open3d as o3d  # noqa: E402


def _pc(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pc = o3d.geometry.PointCloud()
    pc.points = o3d.utility.Vector3dVector([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0]])
    o3d.io.write_point_cloud(path, pc)


class TestStationCounts:
    def test_counts_files_per_item_in_new_layout(self, tmp_path):
        from h3d.gui.items import raw_station_counts

        root = tmp_path / "proj"
        stage = root / "阶段成果"
        _pc(str(stage / "00_raw" / "one" / "scan.ply"))
        _pc(str(stage / "00_raw" / "two" / "a.ply"))
        _pc(str(stage / "00_raw" / "two" / "b.ply"))
        assert raw_station_counts(str(root)) == {"one": 1, "two": 2}

    def test_nested_batches_count_as_stations(self, tmp_path):
        """``00_raw/<件>/批次A/x.ply`` 与 ``批次B/y.ply`` 是**两份**几何。

        任务发现规则允许目录内再嵌套（采集批次 / 设备）。只看第一层的实现
        会把两站数成 0 站，于是该报的提醒不报——正是这类"看着对"的副本。
        """
        from h3d.gui.items import raw_station_counts

        root = tmp_path / "proj"
        stage = root / "阶段成果"
        _pc(str(stage / "00_raw" / "r1" / "batchA" / "s1.ply"))
        _pc(str(stage / "00_raw" / "r1" / "batchB" / "s2.ply"))
        assert raw_station_counts(str(root)) == {"r1": 2}

    def test_old_layout_is_counted_too(self, tmp_path):
        from h3d.gui.items import raw_station_counts

        root = tmp_path / "old"
        _pc(str(root / "00_raw" / "r1" / "scan.ply"))
        assert raw_station_counts(str(root)) == {"r1": 1}

    def test_missing_raw_dir_is_not_an_error(self, tmp_path):
        """没有 00_raw（纯照片项目 / 空项目）时返回空字典，不能抛。"""
        from h3d.gui.items import raw_station_counts

        assert raw_station_counts(str(tmp_path)) == {}


class TestCautionText:
    def test_single_station_item_is_named(self):
        from h3d.gui.constraints import ConstraintsDialog

        text = ConstraintsDialog.station_caution({"relic_001": 1})
        assert "relic_001" in text
        assert "配准" in text
        # 必须说清"这是正常终态"，否则用户会以为要修什么
        assert "正常" in text or "不是缺陷" in text

    def test_two_stations_says_nothing(self):
        from h3d.gui.constraints import ConstraintsDialog

        assert ConstraintsDialog.station_caution({"relic_001": 2, "r2": 3}) == ""

    def test_zero_geometry_is_not_reported_as_single_station(self):
        """纯照片件 raw_paths 为空 —— 那不是"单站"，报它就是误报。"""
        from h3d.gui.constraints import ConstraintsDialog

        assert ConstraintsDialog.station_caution({"relic_001": 0}) == ""

    def test_many_items_are_summarised_not_dumped(self):
        from h3d.gui.constraints import ConstraintsDialog

        text = ConstraintsDialog.station_caution({f"r{i}": 1 for i in range(9)})
        assert "9" in text
        assert "r8" not in text          # 不逐条铺满对话框


class TestDialogShowsIt:
    def test_caution_appears_in_the_dialog(self, _qapp_session, tmp_path):
        from h3d.gui.constraints import Constraints, ConstraintsDialog

        dlg = ConstraintsDialog(Constraints(scene="scene1"),
                                station_counts={"relic_001": 1})
        try:
            assert "relic_001" in dlg.lbl_station.text()
            assert dlg.lbl_station.isVisibleTo(dlg)
        finally:
            dlg.close()

    def test_no_caution_means_no_empty_row(self, _qapp_session):
        from h3d.gui.constraints import Constraints, ConstraintsDialog

        dlg = ConstraintsDialog(Constraints(scene="scene1"),
                                station_counts={"relic_001": 3})
        try:
            assert dlg.lbl_station.text() == ""
        finally:
            dlg.close()

    def test_counts_default_to_unknown_without_scanning(self, _qapp_session):
        """不传 station_counts 时对话框照常打开（老调用点不能因此崩）。"""
        from h3d.gui.constraints import Constraints, ConstraintsDialog

        dlg = ConstraintsDialog(Constraints(scene="scene1"))
        try:
            assert dlg.lbl_station.text() == ""
        finally:
            dlg.close()


class TestBriefVariant:
    """对话框铺开讲、日志一行带过——同一判据两种长度，不是两份文案。"""

    def test_brief_is_one_line_and_keeps_the_facts(self):
        from h3d.gui.constraints import ConstraintsDialog

        full = ConstraintsDialog.station_caution({"r1": 1})
        brief = ConstraintsDialog.station_caution({"r1": 1}, brief=True)
        assert brief and brief != full
        assert "r1" in brief
        assert "配准" in brief
        # 日志版每次开跑都写，必须短
        assert len(brief) < len(full)

    def test_both_agree_on_which_items(self):
        from h3d.gui.constraints import ConstraintsDialog

        counts = {"a": 1, "b": 2, "c": 1}
        for brief in (False, True):
            text = ConstraintsDialog.station_caution(counts, brief=brief)
            assert "a" in text and "c" in text and "b" not in text


class TestMainWindowWiresIt:
    """真窗口：两条开跑路径都要在日志里留一句。

    「上次已确认 → 不弹框」是回访用户最常走的那条，只在对话框里提示
    等于对最常走的路不提示。
    """

    def _win(self, tmp_path):
        from h3d.gui.app import MainWindow
        from h3d.gui.config_edit import ProjectConfig

        cfg = ProjectConfig()
        cfg.name = "p"
        cfg.root_dir = str(tmp_path)
        return MainWindow(cfg)

    def test_confirmed_path_logs_the_caution(self, tmp_path, _qapp_session,
                                             monkeypatch):
        from h3d.gui import constraints as C
        from h3d.gui.constraints import Constraints, apply_to_project_config

        stage = tmp_path / "阶段成果"
        _pc(str(stage / "00_raw" / "relic_001" / "scan.ply"))
        w = self._win(tmp_path)
        try:
            apply_to_project_config(w.cfg, Constraints(texture_size=2048))
            monkeypatch.setattr(w, "_preflight_before_run", lambda p: False)
            captured = []
            monkeypatch.setattr(w, "_log", lambda m: captured.append(str(m)))
            w._start_run()
            text = "\n".join(captured)
            assert "relic_001" in text and "恒等直通" in text, captured
        finally:
            w.close()

    def test_two_stations_logs_nothing(self, tmp_path, _qapp_session,
                                       monkeypatch):
        from h3d.gui.constraints import Constraints, apply_to_project_config

        stage = tmp_path / "阶段成果"
        _pc(str(stage / "00_raw" / "relic_001" / "a.ply"))
        _pc(str(stage / "00_raw" / "relic_001" / "b.ply"))
        w = self._win(tmp_path)
        try:
            apply_to_project_config(w.cfg, Constraints(texture_size=2048))
            monkeypatch.setattr(w, "_preflight_before_run", lambda p: False)
            captured = []
            monkeypatch.setattr(w, "_log", lambda m: captured.append(str(m)))
            w._start_run()
            assert not any("恒等直通" in m for m in captured), captured
        finally:
            w.close()


class TestPlainTextOnly:
    def test_no_markdown_markers_in_dialog_text(self):
        """QLabel 是**纯文本**：``**粗体**`` 会原样印出星号。

        真机截图里就出现过「**不发生任何配准变换**」—— 文档里写惯了 markdown，
        搬到界面上忘了它是纯文本。这条测试很便宜，但省一次截图复核。
        """
        from h3d.gui.constraints import ConstraintsDialog

        for brief in (False, True):
            text = ConstraintsDialog.station_caution({"r1": 1}, brief=brief)
            assert "**" not in text, text
            assert "<b>" not in text, text

    def test_constraint_summary_rows_are_plain_text_too(self):
        from h3d.gui.constraints import Constraints

        blob = " ".join(f"{k}={v}" for k, v in Constraints().summary_rows())
        assert "**" not in blob
