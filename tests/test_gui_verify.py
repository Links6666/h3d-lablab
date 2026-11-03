"""GUI「核验交付包」按钮。

核验以前只有 `h3d verify` 命令行，GUI 用户够不着。这一组用例断言的是**用户
看到什么**：点一下按钮，出问题的条目必须出现在弹窗与界面日志里。

为什么不用 `QMessageBox(...)` + `exec()`：那是真模态，offscreen 测试里没人点
按钮会一直卡住（conftest 只桩了静态方法）。所以槽里走静态弹窗，完整清单进
可滚动的界面日志——顺带 UX 也更好。
"""
from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6")

TESTS = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(TESTS, "..", "src"))
sys.path.insert(0, TESTS)

from _delivery_fixture import add_accuracy_numbers, build_project  # noqa: E402


def _win(root):
    from h3d.gui.app import MainWindow
    from h3d.gui.config_edit import ProjectConfig

    cfg = ProjectConfig()
    cfg.name = "p"
    cfg.root_dir = str(root)
    return MainWindow(cfg)


def _capture(monkeypatch, kind):
    """把某个静态弹窗换成记录器，返回一个装着调用参数的列表。"""
    from PySide6.QtWidgets import QMessageBox

    calls = []

    def _spy(parent, title, text, *a, **k):
        calls.append((title, text))
        return QMessageBox.Ok

    monkeypatch.setattr(QMessageBox, kind, staticmethod(_spy))
    return calls


class TestVerifyButton:
    def test_button_exists(self, tmp_path, _qapp_session):
        win = _win(tmp_path)
        assert win.btn_verify.text() == "核验交付包"
        assert win.btn_verify.isEnabled()

    def test_clean_project_shows_information(self, tmp_path, monkeypatch,
                                             _qapp_session):
        root = build_project(tmp_path, m6_done=["a"], delivered=["a"])
        add_accuracy_numbers(root, ["a"])      # 数值与 manifest 一致才叫「干净」
        win = _win(root)
        info = _capture(monkeypatch, "information")
        warn = _capture(monkeypatch, "warning")
        crit = _capture(monkeypatch, "critical")

        win.btn_verify.click()

        assert info, "干净的交付包应当弹「通过」"
        assert not crit
        assert "PASS" in info[0][1]
        assert not warn

    def test_broken_project_shows_critical_and_names_the_item(
            self, tmp_path, monkeypatch, _qapp_session):
        root = build_project(tmp_path, m6_done=["a", "b"], delivered=["a"])
        add_accuracy_numbers(root, ["a"])
        win = _win(root)
        crit = _capture(monkeypatch, "critical")
        info = _capture(monkeypatch, "information")

        win.btn_verify.click()

        assert crit, "缺件应当弹「有问题」"
        assert not info
        assert "b" in crit[0][1], crit[0][1]

    def test_failure_is_also_written_to_the_visible_log(
            self, tmp_path, monkeypatch, _qapp_session):
        """弹窗只放前几条；完整清单必须落到界面日志里（可滚动、可复制）。"""
        root = build_project(tmp_path, m6_done=["a", "b"], delivered=["a"])
        add_accuracy_numbers(root, ["a"])
        win = _win(root)
        _capture(monkeypatch, "critical")

        win.btn_verify.click()

        logged = win.log.toPlainText() if hasattr(win.log, "toPlainText") else ""
        assert "核验" in logged, logged[-400:]
        assert "FAIL" in logged, logged[-400:]

    def test_no_project_is_a_warning_not_a_crash(self, tmp_path, monkeypatch,
                                                 _qapp_session):
        win = _win(tmp_path)                       # 空目录，没有任何项目
        win.cfg.root_dir = ""
        warn = _capture(monkeypatch, "warning")

        win.btn_verify.click()

        assert warn
        assert "项目" in warn[0][1]
