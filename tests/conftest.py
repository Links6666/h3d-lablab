"""pytest 全局配置：Qt GUI 测试共享 offscreen QApplication。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

#: 用户级状态目录（S1 复用索引等）在测试期间必须指向临时目录，
#: 否则跑一次测试就往开发者真实的 ``~/.h3d`` 里写指纹，而且不同用例
#: 之间会通过它互相影响（2026-10-26 索引提升到用户级后引入）。
_H3D_HOME = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "_h3d_home_tmp")
os.environ.setdefault("H3D_HOME", _H3D_HOME)


@pytest.fixture(autouse=True)
def _isolated_h3d_home(tmp_path, monkeypatch):
    """每个用例独占一个用户级状态目录。

    S1 复用索引已提升到用户级（``~/.h3d``），不复用隔离会让「A 用例登记的
    指纹」被 B 用例命中——这类跨用例串味极难排查（表现是单独跑绿、一起跑红）。
    """
    home = tmp_path / "_h3d_home"
    monkeypatch.setenv("H3D_HOME", str(home))
    yield home


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
