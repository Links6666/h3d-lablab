"""pytest 全局配置：Qt GUI 测试共享 offscreen QApplication。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session", autouse=True)
def _qapp_session():
    """确保全会话仅一个 offscreen QApplication（Qt 测试稳定）。"""
    from PySide6.QtWidgets import QApplication, QMessageBox
    # 屏蔽模态弹窗（测试自动化）
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
    app = QApplication.instance() or QApplication([])
    yield app
