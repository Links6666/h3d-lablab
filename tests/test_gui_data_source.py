"""GUI 侧的数据来源声明：约束面板 → config.yaml 的 ``runner.data_source``。

这条链最容易断在「界面选了不落盘」（假控件），所以断言点选在 yaml 上，
不选在控件状态上——和第二批约束字段同一个判据。
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

import yaml  # noqa: E402


class TestConstraintsModel:
    def test_default_is_auto(self):
        from h3d.gui.constraints import Constraints

        assert Constraints().data_source == "auto"

    def test_summary_shows_the_declaration(self):
        from h3d.gui.constraints import Constraints

        rows = dict(Constraints(data_source="photogrammetry").summary_rows())
        assert "数据来源" in rows
        assert "照片重建" in rows["数据来源"]

    def test_declared_photogrammetry_warns_before_running(self):
        """用户勾了「照片重建件」就必须在开跑前知道后果：不出毫米。"""
        from h3d.gui.constraints import Constraints

        warns = "；".join(Constraints(data_source="photogrammetry").warns())
        assert "毫米" in warns or "米制" in warns

    def test_scene2_cannot_declare_laser(self):
        """纯照片场景声明「激光件」= 自相矛盾，开跑前拦住。"""
        from h3d.gui.constraints import Constraints

        errs = Constraints(scene="scene2", data_source="laser").validate()
        assert any("激光" in e for e in errs)

    def test_scene1_laser_is_fine(self):
        from h3d.gui.constraints import Constraints

        assert Constraints(scene="scene1", data_source="laser").validate() == []

    def test_auto_does_not_warn(self):
        from h3d.gui.constraints import Constraints

        assert Constraints().warns() == []


class TestLandsInYaml:
    def test_apply_and_to_yaml(self, tmp_path):
        from h3d.gui.config_edit import ProjectConfig
        from h3d.gui.constraints import Constraints, apply_to_project_config

        cfg = ProjectConfig()
        cfg.root_dir = str(tmp_path)
        apply_to_project_config(cfg, Constraints(data_source="photogrammetry"))
        path = cfg.to_yaml(str(tmp_path / "config.yaml"))
        data = yaml.safe_load(open(path, encoding="utf-8"))
        assert data["runner"]["data_source"] == "photogrammetry"
        # 快照也要带上，否则「修改生成约束」重新打开时又变回 auto
        assert data["runner"]["constraints"]["data_source"] == "photogrammetry"

    def test_from_root_restores_it(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump({
            "project": {"name": "p"},
            "runner": {"data_source": "laser"},
        }, allow_unicode=True), encoding="utf-8")
        from h3d.gui.config_edit import ProjectConfig

        assert ProjectConfig.from_root(str(tmp_path), "p", "scene1").data_source \
            == "laser"

    def test_old_yaml_without_the_key_defaults_to_auto(self, tmp_path):
        """老项目（没有这个键）打开后必须是 auto，行为一字不改。"""
        p = tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump({"project": {"name": "p"}},
                                    allow_unicode=True), encoding="utf-8")
        from h3d.gui.config_edit import ProjectConfig

        assert ProjectConfig.from_root(str(tmp_path), "p", "scene1").data_source \
            == "auto"


class TestDialog:
    def test_dialog_exposes_a_combo_and_returns_it(self, _qapp_session):
        from h3d.gui.constraints import Constraints, ConstraintsDialog

        dlg = ConstraintsDialog(Constraints(data_source="photogrammetry"))
        try:
            assert dlg.cb_source.currentIndex() == 2      # auto / laser / photo
            assert dlg.constraints().data_source == "photogrammetry"
            dlg.cb_source.setCurrentIndex(1)
            assert dlg.constraints().data_source == "laser"
        finally:
            dlg.close()

    def test_defaults_button_resets_to_auto(self, _qapp_session):
        from h3d.gui.constraints import Constraints, ConstraintsDialog

        dlg = ConstraintsDialog(Constraints(data_source="photogrammetry"))
        try:
            dlg._on_defaults()
            assert dlg.constraints().data_source == "auto"
        finally:
            dlg.close()
