"""tests.test_gui_result_bar : 结果条替代模态弹窗 + 强制重算开关（2026-10-26）。

实机背景
--------
管线跑完后弹 ``QMessageBox``。问题不在「丑」，而在**必须点掉才能继续**：
用户看到「完成」后的下一个动作通常是看模型 / 导出，弹窗正好挡在前面；
失败时更糟——点掉之后想「打开报表」还得自己去文件管理器翻路径。

本版把结论做成界面内常驻的结果条（带「打开输出目录」「查看报表」「重跑」
「关闭」），弹窗只留给真正的失败（结果与预期不符，不看就接着操作更危险）。

同一批还加了「强制重算」开关：勾选后忽略断点续跑、全部阶段重跑，
跑完自动复位（一次性意图，不能变成项目的长期属性）。
"""
from __future__ import annotations

import os

import pytest
import yaml

pytest.importorskip("PySide6")


def _win(tmp_path, **cfg_over):
    from h3d.gui.app import MainWindow
    from h3d.gui.config_edit import ProjectConfig

    cfg = ProjectConfig()
    cfg.name = "p"
    cfg.root_dir = str(tmp_path)
    for k, v in cfg_over.items():
        setattr(cfg, k, v)
    return MainWindow(cfg)


class TestResultBar:
    def test_hidden_initially(self, tmp_path, _qapp_session):
        w = _win(tmp_path)
        try:
            assert w.result_bar.isHidden() is True   # 父窗口未 show，isVisible 恒 False
        finally:
            w.close()

    def test_success_shows_bar_without_dialog(self, tmp_path, _qapp_session,
                                              monkeypatch):
        w = _win(tmp_path)
        calls = []
        monkeypatch.setattr("h3d.gui.app.QMessageBox.information",
                            lambda *a, **k: calls.append(a) or None)
        monkeypatch.setattr("h3d.gui.app.QMessageBox.warning",
                            lambda *a, **k: calls.append(a) or None)
        try:
            w._show_result(True, "完成：1 件")
            assert calls == [], "成功不得弹模态框"
            assert w.result_bar.isHidden() is False
            assert "生成完成" in w.lbl_result.text()
        finally:
            w.close()

    def test_failure_keeps_dialog(self, tmp_path, _qapp_session, monkeypatch):
        """失败路径仍由 _on_finished 弹一次确认框（本用例只验证结果条文案）。"""
        w = _win(tmp_path)
        try:
            w._show_result(False, "1 件失败")
            assert "运行未完成" in w.lbl_result.text()
            assert w.result_bar.isHidden() is False
        finally:
            w.close()

    def test_skipped_only_is_not_red(self, tmp_path, _qapp_session):
        w = _win(tmp_path)
        try:
            w._show_result(True, "无需重跑", skipped_only=True)
            assert "无需重跑" in w.lbl_result.text()
            assert "#3fb950" not in w.lbl_result.text(), "跳过不是成功也不是失败"
        finally:
            w.close()

    def test_open_buttons_follow_artifacts(self, tmp_path, _qapp_session):
        """按钮只暴露**真的存在**的动作（新布局：交付成果/<编号> + report）。"""
        w = _win(tmp_path)
        try:
            w._show_result(True, "x")
            assert w.btn_result_open.isEnabled() is False, "没有产物目录就不该能点"
            assert w.btn_result_report.isEnabled() is False
            (tmp_path / "交付成果" / "r1").mkdir(parents=True)
            rpt = tmp_path / "交付成果" / "report"
            rpt.mkdir()
            (rpt / "report.html").write_text("<html>", encoding="utf-8")
            w._show_result(True, "x")
            assert w.btn_result_open.isEnabled() is True
            assert w.btn_result_report.isEnabled() is True
        finally:
            w.close()

    def test_open_buttons_legacy_layout(self, tmp_path, _qapp_session):
        """旧布局（80_export/<编号> + report）仍要能识别，否则老项目全部变灰。"""
        w = _win(tmp_path)
        try:
            (tmp_path / "80_export" / "r1").mkdir(parents=True)
            rpt = tmp_path / "report"
            rpt.mkdir()
            (rpt / "report.html").write_text("<html>", encoding="utf-8")
            w._show_result(True, "x")
            assert w.btn_result_open.isEnabled() is True
            assert w.btn_result_report.isEnabled() is True
        finally:
            w.close()


class TestForceRerunSwitch:
    def test_checkbox_reflects_cfg(self, tmp_path, _qapp_session):
        w = _win(tmp_path)
        try:
            assert w.chk_force.isChecked() is False
            assert "强制重算" in w.chk_force.text()
        finally:
            w.close()

    def test_cfg_writes_force_flag(self, tmp_path):
        """勾选 → config.yaml 的 runner.force_rerun=true（子进程只认 yaml）。"""
        from h3d.gui.config_edit import ProjectConfig

        cfg = ProjectConfig()
        cfg.root_dir = str(tmp_path)
        cfg.item_id = "r1"
        cfg.force_rerun = True
        path = cfg.to_yaml(str(tmp_path / "config.yaml"))
        data = yaml.safe_load(open(path, encoding="utf-8"))
        assert data["runner"]["force_rerun"] is True

    def test_from_root_never_restores_force_flag(self, tmp_path):
        """一次性意图：打开历史项目不得自动带上「强制重算」。"""
        from h3d.gui.config_edit import ProjectConfig

        p = tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump({
            "project": {"name": "p"},
            "runner": {"force_rerun": True},
        }, allow_unicode=True), encoding="utf-8")
        cfg = ProjectConfig.from_root(str(tmp_path), "p", "scene2")
        assert cfg.force_rerun is False

    def test_confirm_refusal_aborts_start(self, tmp_path, _qapp_session,
                                          monkeypatch):
        """二次确认选「否」→ 不启动管线。"""
        from PySide6.QtWidgets import QMessageBox
        w = _win(tmp_path)
        # 约束已确认（否则会先弹「确认生成约束」，那是另一条分支）
        w.cfg.constraints_confirmed = True
        monkeypatch.setattr("h3d.gui.app.QMessageBox.question",
                            staticmethod(lambda *a, **k: QMessageBox.No))
        try:
            w.chk_force.setChecked(True)
            started = []
            monkeypatch.setattr(w, "_preflight_before_run",
                                lambda p: started.append(1) or True)
            w._start_run()
            assert started == [], "取消确认后不得继续到自检/启动"
            assert w.runner is None
        finally:
            w.close()

    def test_runtime_flag_reset_after_finish(self, tmp_path, _qapp_session,
                                             monkeypatch):
        """跑完自动复位勾选与 cfg 标记，下次运行为正常续跑。"""
        w = _win(tmp_path, force_rerun=True)
        w.chk_force.setChecked(True)
        monkeypatch.setattr(w, "_preview_candidates", lambda: [])
        monkeypatch.setattr("h3d.gui.viewer3d.load_x1_changes",
                            lambda *a, **k: None)
        monkeypatch.setattr("h3d.gui.app.QMessageBox.warning",
                            staticmethod(lambda *a, **k: None))
        try:
            w._on_finished(True, "完成：1 件")
            assert w.cfg.force_rerun is False
            assert w.chk_force.isChecked() is False
            data = yaml.safe_load(open(os.path.join(str(tmp_path),
                                                    "config.yaml"),
                                      encoding="utf-8"))
            assert data["runner"]["force_rerun"] is False
        finally:
            w.close()
