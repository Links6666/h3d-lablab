"""GUI「文物档案」页（B0 切片 6）：打开项目即建档 + 时相列表。

为什么这页必须存在
------------------
档案层做完四个切片后，建档只有 CLI 一条路。GUI 用户打开项目、跑完一批，
``archive/`` 永远不存在，于是切片 5 的「产物登记」在界面上从未发生 ——
功能上写了，体验上不存在。

本页承担两件事：打开项目时把档建起来；把时相与产物版本摊开给用户看。
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

ITEM = "relic_001"


def _proj(tmp_path, *, with_outputs: bool = True):
    stage = tmp_path / "阶段成果"
    (stage / "00_raw" / ITEM).mkdir(parents=True)
    (stage / "00_raw" / ITEM / "scan.ply").write_bytes(b"PLY-raw\n")
    if with_outputs:
        (stage / "30_dense" / ITEM).mkdir(parents=True)
        (stage / "30_dense" / ITEM / f"{ITEM}.ply").write_bytes(b"PLY\n")
        (stage / "50_tex" / ITEM).mkdir(parents=True)
        (stage / "50_tex" / ITEM / f"{ITEM}_L1.0_diffuse.png").write_bytes(b"PNG\n")
        (stage / "90_meta").mkdir(parents=True)
        (stage / "90_meta" / f"{ITEM}_capture_meta.json").write_text(
            "{}", encoding="utf-8")
    return tmp_path


def _win(tmp_path):
    from h3d.gui.app import MainWindow
    from h3d.gui.config_edit import ProjectConfig

    cfg = ProjectConfig()
    cfg.name = "p"
    cfg.root_dir = str(tmp_path)
    cfg.item_id = ITEM
    return MainWindow(cfg)


class TestArchiveTab:
    def test_tab_present(self, tmp_path, _qapp_session):
        w = _win(_proj(tmp_path))
        try:
            assert w.tabs.indexOf(w.archive_panel) >= 0
            assert w.tabs.tabText(w.tabs.indexOf(w.archive_panel)) == "文物档案"
        finally:
            w.close()

    def test_opening_project_creates_archive(self, tmp_path, _qapp_session):
        """打开项目 = 建档。否则 GUI 用户永远不会有档案。"""
        root = _proj(tmp_path)
        w = _win(root)
        try:
            assert (root / "archive" / "artifact.json").is_file()
            assert (root / "archive" / "epochs" / "E1.json").is_file()
        finally:
            w.close()

    def test_rows_rendered(self, tmp_path, _qapp_session):
        root = _proj(tmp_path)
        w = _win(root)
        try:
            panel = w.archive_panel
            assert panel.table.rowCount() == 1
            assert panel.table.item(0, 0).text() == "E1"
            # 基准期与产物版本都要看得见
            assert "基准期" in panel.table.item(0, 3).text()
            assert "v1" in panel.table.item(0, 4).text()
        finally:
            w.close()

    def test_plain_directory_creates_nothing(self, tmp_path, _qapp_session):
        """普通目录（无项目痕迹）不许冒出 archive/，面板给出说明。"""
        d = tmp_path / "随便一个目录"
        d.mkdir()
        w = _win(d)
        try:
            assert not (d / "archive").exists()
            assert w.archive_panel.table.rowCount() == 0
            assert "H3D 项目" in w.archive_panel.lbl_note.text()
        finally:
            w.close()

    def test_fresh_project_shows_empty_epoch(self, tmp_path, _qapp_session):
        """有采集数据、还没跑：时相在、版本 0，文案要说清「尚无产物」。"""
        root = _proj(tmp_path, with_outputs=False)
        w = _win(root)
        try:
            panel = w.archive_panel
            assert panel.table.rowCount() == 1
            assert "尚无产物" in panel.table.item(0, 4).text()
        finally:
            w.close()

    def test_refresh_picks_up_new_versions(self, tmp_path, _qapp_session):
        """跑完一批后刷新 —— 只读，不重复建档。"""
        import datetime as _dt

        from h3d.archive import Archive, CanonicalAsset

        root = _proj(tmp_path)
        w = _win(root)
        try:
            before = (root / "archive" / "artifact.json").read_bytes()
            arch = Archive.open(str(root))
            arch.register(CanonicalAsset(
                epoch_id="E1", version=1, content_key="b" * 64,
                mesh_path=f"阶段成果/30_dense/{ITEM}/{ITEM}.ply",
                cloud_path=None, texture_path=None, uv_atlas_ref="atlas-v1",
                cameras_path=None, params={"pipeline": "runner"},
                created_at=_dt.datetime.now().isoformat(timespec="seconds")))
            w._refresh_archive()
            assert "2 版" in w.archive_panel.table.item(0, 4).text()
            assert (root / "archive" / "artifact.json").read_bytes() == before
        finally:
            w.close()

    def test_no_root_dir_does_not_crash(self, tmp_path, _qapp_session):
        from h3d.gui.app import MainWindow
        from h3d.gui.config_edit import ProjectConfig

        cfg = ProjectConfig()
        cfg.name = "p"
        cfg.root_dir = ""
        w = MainWindow(cfg)
        try:
            assert w.archive_panel.table.rowCount() == 0
        finally:
            w.close()

    def test_ensure_catches_up_after_data_arrives(self, tmp_path,
                                                  _qapp_session):
        """先建项目、后放数据：打开那一刻没有件目录，跑之前必须补建。

        这是「向导里只填了路径、稍后才拷数据」的常见路径。若只在打开时
        建一次档，这类项目永远没有 archive/，切片 5 的登记也永远不生效。
        """
        (tmp_path / "config.yaml").write_text("project: {}\n", encoding="utf-8")
        w = _win(tmp_path)
        try:
            assert not (tmp_path / "archive").exists(), "没有件目录时不该建档"
            raw = tmp_path / "阶段成果" / "00_raw" / ITEM
            raw.mkdir(parents=True)
            (raw / "scan.ply").write_bytes(b"PLY-raw\n")
            w._ensure_archive()
            assert (tmp_path / "archive" / "artifact.json").is_file()
            assert w.archive_panel.table.rowCount() == 1
        finally:
            w.close()


from PySide6.QtCore import QObject, Signal


class FakeWorker(QObject):
    """替身：记录构造参数，不真跑烘焙（2048² 一次要几十秒）。"""

    done = Signal(bool, str, dict)
    instances: list = []

    def __init__(self, base, source, parent=None):
        super().__init__(parent)
        self.base, self.source = base, source
        self.started = False
        self.running = False
        FakeWorker.instances.append(self)

    def isRunning(self):  # noqa: N802 - 模仿 QThread
        return self.running

    def start(self):
        self.started = True
        self.running = True

    def deleteLater(self):  # noqa: N802
        self.running = False


class TestInheritButton:
    def test_button_present(self, tmp_path, _qapp_session):
        w = _win(_proj(tmp_path))
        try:
            assert "继承" in w.archive_panel.btn_inherit.text()
        finally:
            w.close()

    def test_disabled_without_archive(self, tmp_path, _qapp_session):
        """普通目录没档案 → 没有基准可继承，按钮必须灰着。"""
        d = tmp_path / "plain"
        d.mkdir()
        w = _win(d)
        try:
            assert w.archive_panel.btn_inherit.isEnabled() is False
        finally:
            w.close()

    def test_enabled_with_archive(self, tmp_path, _qapp_session):
        w = _win(_proj(tmp_path))
        try:
            assert w.archive_panel.btn_inherit.isEnabled() is True
        finally:
            w.close()

    def test_cancel_dialog_starts_nothing(self, tmp_path, _qapp_session,
                                          monkeypatch):
        from PySide6.QtWidgets import QFileDialog

        FakeWorker.instances.clear()
        monkeypatch.setattr("h3d.gui.archive_view.InheritWorker", FakeWorker)
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            staticmethod(lambda *a, **k: ""))
        w = _win(_proj(tmp_path))
        try:
            w.archive_panel._inherit()
            assert FakeWorker.instances == []
        finally:
            w.close()

    def test_starts_worker_with_selected_base(self, tmp_path, _qapp_session,
                                              monkeypatch):
        from PySide6.QtWidgets import QFileDialog

        FakeWorker.instances.clear()
        monkeypatch.setattr("h3d.gui.archive_view.InheritWorker", FakeWorker)
        base = str(tmp_path / "another_project")
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            staticmethod(lambda *a, **k: base))
        w = _win(_proj(tmp_path))
        try:
            w.archive_panel._inherit()
            assert len(FakeWorker.instances) == 1
            worker = FakeWorker.instances[0]
            assert worker.base == base
            assert worker.source == str(tmp_path)
            assert worker.started is True
            # 跑起来就锁住按钮，避免连点堆多个烘焙任务
            assert w.archive_panel.btn_inherit.isEnabled() is False
        finally:
            w.close()

    def test_second_click_ignored_while_running(self, tmp_path, _qapp_session,
                                                monkeypatch):
        from PySide6.QtWidgets import QFileDialog

        FakeWorker.instances.clear()
        monkeypatch.setattr("h3d.gui.archive_view.InheritWorker", FakeWorker)
        base = str(tmp_path / "another")
        monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                            staticmethod(lambda *a, **k: base))
        w = _win(_proj(tmp_path))
        try:
            w.archive_panel._inherit()
            w.archive_panel._inherit()
            assert len(FakeWorker.instances) == 1, "运行中不得再起第二个任务"
        finally:
            w.close()

    def test_done_reports_and_refreshes(self, tmp_path, _qapp_session):
        """完成后必须刷新（新版本可见），并把结论写进说明行。"""
        w = _win(_proj(tmp_path))
        try:
            refreshed = []
            w.archive_panel.archive_changed.connect(lambda: refreshed.append(1))
            w.archive_panel._on_inherit_done(True, "已继承：relic_001@atlas-v1", {})
            assert "已继承" in w.archive_panel.lbl_note.text()
            assert refreshed == [1]
            assert w.archive_panel.btn_inherit.isEnabled() is True
        finally:
            w.close()

    def test_failure_keeps_button_enabled(self, tmp_path, _qapp_session):
        w = _win(_proj(tmp_path))
        try:
            w.archive_panel._on_inherit_done(False, "基准期没有图集记录", {})
            assert "没有图集" in w.archive_panel.lbl_note.text()
            assert w.archive_panel.btn_inherit.isEnabled() is True
        finally:
            w.close()
