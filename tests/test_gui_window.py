"""tests.test_gui_window : 主窗口尺寸稳定性 + 阶段耗时展示。

实机问题（2026-10-26）：「管线一跑窗口就时不时自己变大变窄，进度看不全」。
根因是**布局最小尺寸随运行时文字变化**——QLabel 的 ``minimumSizeHint`` 等于
整段文字宽度，文字变长 → 布局要求更多空间 → Qt 把顶层窗口撑大；文字变短时
窗口不会缩回。所以这一组用例的核心断言是：

    **把状态文字换成很长的一串，窗口尺寸必须一点不变。**

这类回归只测"能创建窗口"是抓不到的（旧实现在构造时也完全正常，只有跑起来
才跳），必须真的去比对尺寸。
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402


def _win(tmp_path, **over):
    from h3d.gui.app import MainWindow
    from h3d.gui.config_edit import ProjectConfig

    cfg = ProjectConfig()
    cfg.name = "p"
    cfg.root_dir = str(tmp_path)
    for k, v in over.items():
        setattr(cfg, k, v)
    return MainWindow(cfg)


class TestElidedLabel:
    def test_minimum_width_is_zero(self, _qapp_session):
        """最小宽度 0 = 不管文字多长，都不会逼布局变宽。"""
        from h3d.gui.widgets import ElidedLabel
        lb = ElidedLabel("x" * 400, min_chars=12)
        assert lb.minimumSizeHint().width() == 0

    def test_size_hint_bounded(self, _qapp_session):
        """自然宽度按 min_chars 估算，不随全文线性增长。"""
        from h3d.gui.widgets import ElidedLabel
        short = ElidedLabel("x", min_chars=12)
        long = ElidedLabel("x" * 600, min_chars=12)
        assert long.sizeHint().width() == short.sizeHint().width()

    def test_tooltip_keeps_full_text(self, _qapp_session):
        """省略显示不等于丢信息：全文必须能从 tooltip/接口读到。"""
        from h3d.gui.widgets import ElidedLabel
        text = "深度图 12/30 · 最后输出 45s 前（计算中）"
        lb = ElidedLabel("")
        lb.setText(text)
        assert lb.text() == text
        assert lb.toolTip() == text


class TestWindowGeometryStability:
    def test_long_status_text_does_not_resize(self, tmp_path, _qapp_session):
        """状态行/进度条文字变长 → 窗口尺寸不变（实机跳变的核心回归位）。"""
        w = _win(tmp_path)
        try:
            w.show()
            before = (w.width(), w.height())
            for n in (1, 60, 240):
                w.lbl_state.setText("● S1 运行中 · " + "深度图 12/30 " * n)
                w.lbl_elapsed.setText("总 01:23:45 · S1 45:00 · " + "x" * n)
                w.lbl_count.setText("第 12/34 件 · S1")
                w._on_progress(68)
                _qapp_session.processEvents()
                assert (w.width(), w.height()) == before, (
                    f"文字长度 {n} 时窗口尺寸从 {before} 变成 "
                    f"{(w.width(), w.height())}")
        finally:
            w.close()

    def test_progress_format_is_short(self, tmp_path, _qapp_session):
        """``QProgressBar`` 的格式串也参与 sizeHint，禁止塞长文本。"""
        w = _win(tmp_path)
        try:
            w._on_items_total(3)
            w._on_stage("M1", "running")
            w._on_progress(42)
            assert w.progress.format() == "%p%"
            assert "第" in w.lbl_count.text()
        finally:
            w.close()

    def test_result_bar_text_does_not_resize(self, tmp_path, _qapp_session):
        w = _win(tmp_path)
        try:
            w.show()
            before = (w.width(), w.height())
            w._show_result(False, "M3 失败：" + "很长的原因" * 80)
            _qapp_session.processEvents()
            assert (w.width(), w.height()) == before
            # 全文仍在（tooltip），信息没被截断丢失
            assert "M3 失败" in w.lbl_result.toolTip()
        finally:
            w.close()

    def test_config_panel_is_scrollable(self, tmp_path, _qapp_session):
        """左侧配置区必须在滚动区里：高级参数展开不会把窗口顶高。"""
        from PySide6.QtWidgets import QScrollArea
        w = _win(tmp_path)
        try:
            assert isinstance(w.config_scroll, QScrollArea)
            assert w.config_scroll.widget() is not None
            assert w.config_scroll.widgetResizable() is True
        finally:
            w.close()

    def test_minimum_size_fits_screen(self, tmp_path, _qapp_session):
        """最小尺寸必须 ≤ 屏幕可用区，否则底部永久出屏、用户拉不动。"""
        from PySide6.QtGui import QGuiApplication
        w = _win(tmp_path)
        try:
            geo = QGuiApplication.primaryScreen().availableGeometry()
            assert w.minimumWidth() <= geo.width()
            assert w.minimumHeight() <= geo.height()
            assert w.width() <= geo.width()
            assert w.height() <= geo.height()
        finally:
            w.close()

    def test_lamps_laid_out_in_grid(self, tmp_path, _qapp_session):
        """10 个阶段灯不能挤成一行（1366 px 屏上每个只剩 40 px）。"""
        from h3d.gui.runner import STAGE_ORDER
        w = _win(tmp_path)
        try:
            assert set(w.lamps) == set(STAGE_ORDER)
            rows = {w.lamps_grid.getItemPosition(w.lamps_grid.indexOf(
                w.lamps[sid]))[0] for sid in STAGE_ORDER}
            assert len(rows) >= 2, "阶段灯应为多行排布"
        finally:
            w.close()


class TestStageTimingDisplay:
    def test_fmt_duration(self):
        from h3d.gui.timing import fmt_duration
        assert fmt_duration(0) == "00:00"
        assert fmt_duration(75) == "01:15"
        assert fmt_duration(3725) == "1:02:05"
        assert fmt_duration(None) == "—"

    def test_manifest_sums_per_stage(self, tmp_path):
        """同一阶段多件文物各跑一次 → 求和（用户问的是"这阶段一共多久"）。"""
        import json

        from h3d.gui.timing import stage_times_from_manifest
        p = tmp_path / "run_manifest.json"
        p.write_text(json.dumps({"stages": {
            "r1/S1": {"status": "done", "elapsed_s": 100.0},
            "r1/M1": {"status": "done", "elapsed_s": 1.0},
            "r2/S1": {"status": "done", "elapsed_s": 50.0},
            "r2/M1": {"status": "skipped"},
        }}), encoding="utf-8")
        info = stage_times_from_manifest(str(p))
        assert info["stages"]["S1"] == 150.0
        assert info["stages"]["M1"] == 1.0
        assert info["total"] == 151.0
        assert info["n_items"] == 2

    def test_manifest_missing_is_silent(self, tmp_path):
        from h3d.gui.timing import stage_times_from_manifest
        info = stage_times_from_manifest(str(tmp_path / "nope.json"))
        assert info["stages"] == {} and info["total"] == 0.0

    def test_strip_shows_and_keeps(self, _qapp_session):
        """耗时条常驻：结算后数字留在标签上（不是一闪而过）。"""
        from h3d.gui.timing import StageTimingStrip
        strip = StageTimingStrip(("S1", "M1"))
        strip.set_seconds("S1", 754)
        assert "12:34" in strip._chips["S1"].text()
        strip.set_status("S1", "done")
        assert "12:34" in strip._chips["S1"].text(), "状态变化不得清掉耗时"
        strip.reset()
        assert "—" in strip._chips["S1"].text()

    def test_app_timing_accumulates_per_stage(self, tmp_path, _qapp_session,
                                              monkeypatch):
        """GUI 侧：running → done 之间累计；多件重复进入同阶段要累加。"""
        import h3d.gui.app as app_mod

        clock = {"t": 1000.0}
        monkeypatch.setattr(app_mod, "fmt_duration", app_mod.fmt_duration)
        w = _win(tmp_path)
        try:
            # 用可控时钟替换 time.time（app 内部是局部 import time，故打补丁
            # 到 time 模块本身，作用域仅本用例）
            import time as _t
            real_time = _t.time
            monkeypatch.setattr(_t, "time", lambda: clock["t"])
            w._on_stage("S1", "running")
            clock["t"] += 120
            w._on_stage("S1", "done")
            assert w.stage_timing.seconds("S1") == pytest.approx(120, abs=1)
            # 第二件又跑一遍 S1
            w._on_stage("S1", "running")
            clock["t"] += 30
            w._on_stage("S1", "done")
            assert w.stage_timing.seconds("S1") == pytest.approx(150, abs=1)
            monkeypatch.setattr(_t, "time", real_time)
        finally:
            w.close()

    def test_elapsed_label_shows_total_and_stage(self, tmp_path, _qapp_session):
        import time as _t
        w = _win(tmp_path)
        try:
            w._t0 = _t.time() - 3725
            w._cur_stage = "M4"
            w._stage_t0["M4"] = _t.time() - 65
            w._on_tick()
            text = w.lbl_elapsed.text()
            assert "总 01:02:05" in text
            assert "M4 01:05" in text
        finally:
            w.close()
