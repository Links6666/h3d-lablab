"""tests.test_gui_constraints : 生成约束的确认闸门与只读展示。

覆盖三件事：
1. :class:`Constraints` 的校验/取舍提示（纯逻辑，无 Qt）；
2. 确认结果必须**落盘**（config.yaml 的 runner.constraints + confirmed）；
3. 主窗口的行为：确认过就不再弹框、拒绝确认则不启动、运行期输入区被替换。
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

import yaml  # noqa: E402


class TestConstraintsModel:
    def test_defaults_are_usable(self):
        from h3d.gui.constraints import Constraints
        c = Constraints(scene="scene2")
        assert c.validate() == []
        assert c.dense is True
        assert c.texture_size == 2048

    def test_scene2_requires_dense(self):
        """纯照片场景关掉稠密 = 没有几何来源，必须拦住。"""
        from h3d.gui.constraints import Constraints
        c = Constraints(scene="scene2", dense=False)
        assert any("稠密" in e for e in c.validate())

    def test_empty_formats_rejected(self):
        from h3d.gui.constraints import Constraints
        c = Constraints(export_formats=[])
        assert any("格式" in e for e in c.validate())

    def test_ply_only_warns_but_passes(self):
        """只交 ply 是取舍而非错误：管线会自动补导 OBJ 承载图集。"""
        from h3d.gui.constraints import Constraints
        c = Constraints(export_formats=["ply"])
        assert c.validate() == []
        assert any("ply" in w for w in c.warns())

    def test_obj_present_no_warning(self):
        from h3d.gui.constraints import Constraints
        c = Constraints(export_formats=["ply", "obj"])
        assert c.warns() == []

    def test_big_atlas_and_depth_warn(self):
        from h3d.gui.constraints import Constraints
        c = Constraints(texture_size=4096, poisson_depth=9)
        warns = " ".join(c.warns())
        assert "4096" in warns and "超精细" in warns

    def test_roundtrip_dict(self):
        from h3d.gui.constraints import Constraints
        c = Constraints(dense=False, texture_size=1024,
                        export_formats=["obj"], quality=False)
        back = Constraints.from_dict(c.to_dict())
        assert back == c

    def test_from_dict_ignores_unknown_keys(self):
        from h3d.gui.constraints import Constraints
        c = Constraints.from_dict({"texture_size": 4096, "bogus": 1})
        assert c.texture_size == 4096

    def test_summary_rows_cover_every_constraint(self):
        from h3d.gui.constraints import Constraints
        rows = dict(Constraints().summary_rows())
        for key in ("场景", "稠密重建", "网格精度", "降采样", "纹理分辨率",
                    "交付格式", "几何质检", "强制重算"):
            assert key in rows

    def test_estimate_mentions_minutes(self):
        from h3d.gui.constraints import Constraints, estimate_minutes
        text = estimate_minutes(Constraints(), n_photos=30)
        assert "分钟" in text


class TestProjectConfigIntegration:
    def test_unconfirmed_by_default(self):
        from h3d.gui.config_edit import ProjectConfig
        assert ProjectConfig().constraints_confirmed is False

    def test_apply_marks_confirmed(self):
        from h3d.gui.config_edit import ProjectConfig
        from h3d.gui.constraints import Constraints, apply_to_project_config
        cfg = ProjectConfig()
        apply_to_project_config(cfg, Constraints(texture_size=4096,
                                                 export_formats=["obj"]))
        assert cfg.constraints_confirmed is True
        assert cfg.texture_size == 4096
        assert cfg.export_formats == ["obj"]

    def test_yaml_carries_snapshot(self, tmp_path):
        """子进程只认 yaml：界面确认了不落盘 = 没确认。"""
        from h3d.gui.config_edit import ProjectConfig
        from h3d.gui.constraints import Constraints, apply_to_project_config
        cfg = ProjectConfig()
        cfg.root_dir = str(tmp_path)
        apply_to_project_config(cfg, Constraints(texture_size=1024))
        cfg.build_layout()
        path = cfg.to_yaml(str(tmp_path / "config.yaml"))
        data = yaml.safe_load(open(path, encoding="utf-8"))
        assert data["runner"]["constraints_confirmed"] is True
        assert data["runner"]["constraints"]["texture_size"] == 1024

    def test_from_root_restores_snapshot(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump({
            "project": {"name": "p"},
            "runner": {"constraints_confirmed": True,
                       "constraints": {"texture_size": 4096, "dense": True}},
        }, allow_unicode=True), encoding="utf-8")
        from h3d.gui.config_edit import ProjectConfig
        cfg = ProjectConfig.from_root(str(tmp_path), "p", "scene2")
        assert cfg.constraints_confirmed is True
        assert cfg.constraints["texture_size"] == 4096

    def test_from_root_without_confirmed_flag_stays_unconfirmed(self, tmp_path):
        """手写 yaml 里塞了 constraints 但没写 confirmed → 仍要确认一次。"""
        p = tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump({
            "project": {"name": "p"},
            "runner": {"constraints": {"texture_size": 4096}},
        }, allow_unicode=True), encoding="utf-8")
        from h3d.gui.config_edit import ProjectConfig
        cfg = ProjectConfig.from_root(str(tmp_path), "p", "scene2")
        assert cfg.constraints_confirmed is False


class TestMainWindowGate:
    def _win(self, tmp_path, **over):
        from h3d.gui.app import MainWindow
        from h3d.gui.config_edit import ProjectConfig
        cfg = ProjectConfig()
        cfg.name = "p"
        cfg.root_dir = str(tmp_path)
        for k, v in over.items():
            setattr(cfg, k, v)
        return MainWindow(cfg)

    def test_cancel_dialog_aborts_start(self, tmp_path, _qapp_session,
                                        monkeypatch):
        """未确认约束 → 不启动管线（不确认不跑）。"""
        from PySide6.QtWidgets import QDialog
        from h3d.gui import constraints as C

        w = self._win(tmp_path)
        try:
            monkeypatch.setattr(C.ConstraintsDialog, "exec", lambda self: 0)
            called = []
            monkeypatch.setattr(w, "_preflight_before_run",
                                lambda p: called.append(1) or True)
            w._start_run()
            assert called == [], "拒绝确认后不得进入自检/启动"
            assert w.runner is None
        finally:
            w.close()

    def test_accept_writes_back_to_config(self, tmp_path, _qapp_session,
                                         monkeypatch):
        """确认对话框 → 约束写进 cfg 并标记 confirmed。"""
        from PySide6.QtWidgets import QDialog
        from h3d.gui import constraints as C

        w = self._win(tmp_path)
        try:
            def _exec(self):
                self.cb_tex.setCurrentIndex(0)          # 1024
                self.chk_fmt["obj"].setChecked(True)
                self.chk_fmt["ply"].setChecked(False)
                self._refresh_note()
                return QDialog.Accepted

            monkeypatch.setattr(C.ConstraintsDialog, "exec", _exec)
            monkeypatch.setattr(w, "_preflight_before_run", lambda p: False)
            w._start_run()
            assert w.cfg.constraints_confirmed is True
            assert w.cfg.texture_size == 1024
            assert w.cfg.export_formats == ["obj", "glb"]
        finally:
            w.close()

    def test_confirmed_skips_dialog(self, tmp_path, _qapp_session, monkeypatch):
        from h3d.gui import constraints as C
        from h3d.gui.constraints import Constraints, apply_to_project_config

        w = self._win(tmp_path)
        try:
            apply_to_project_config(w.cfg, Constraints(texture_size=4096))
            opened = []
            monkeypatch.setattr(
                C.ConstraintsDialog, "exec",
                lambda self: opened.append(1) or 0)
            monkeypatch.setattr(w, "_preflight_before_run", lambda p: False)
            w._start_run()
            assert opened == [], "已确认过的项目不该每次都弹框"
        finally:
            w.close()

    def test_running_ui_swaps_inputs_for_summary(self, tmp_path, _qapp_session):
        """运行期：输入区隐藏、只读约束卡出现、约束按钮禁用。"""
        from h3d.gui.constraints import Constraints, apply_to_project_config

        w = self._win(tmp_path)
        try:
            apply_to_project_config(w.cfg, Constraints(dense=True))
            w._set_running_ui(True)
            assert w.inputs_host.isHidden() is True
            assert w.constraint_box.isHidden() is False
            assert w.btn_edit_constraints.isEnabled() is False
            assert w.btn_cancel.isEnabled() is True
            w._set_running_ui(False)
            assert w.inputs_host.isHidden() is False
            assert w.constraint_box.isHidden() is True
            assert w.btn_edit_constraints.isEnabled() is True
        finally:
            w.close()

    def test_summary_is_read_only(self, tmp_path, _qapp_session):
        """只读卡里不得存在任何输入控件（否则又是"看起来能改"）。"""
        from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox,
                                       QLineEdit)
        from h3d.gui.constraints import Constraints

        w = self._win(tmp_path)
        try:
            w.constraint_box.set_constraints(Constraints())
            for cls in (QComboBox, QCheckBox, QLineEdit, QAbstractSpinBox):
                assert w.constraint_box.findChildren(cls) == []
        finally:
            w.close()
