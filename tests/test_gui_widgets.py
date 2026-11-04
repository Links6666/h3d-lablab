"""``ElidedLabel`` 的尺寸/绘制必须是**可重入**的。

Qt 会在构造尚未完成、或析构途中来问 ``sizeHint``。原实现直接读
``self._min_chars``，那时属性还不存在 —— 于是测试输出里出现

    Error calling Python override of QLabel::sizeHint(): AttributeError ...

这类异常不会让任何断言变红，只是往 stderr 喷栈。**打包 windowed 版 stderr=None，
等于彻底静默**——所以它属于「坏了也没人知道」那一类，得用测试钉住。
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


def test_size_hint_survives_partial_construction(_qapp_session):
    from h3d.gui.widgets import ElidedLabel

    lbl = ElidedLabel("x", min_chars=8)
    del lbl.__dict__["_min_chars"]          # 模拟 __init__ 尚未跑到那一步
    size = lbl.sizeHint()
    assert size.width() > 0 and size.height() > 0


def test_paint_event_survives_missing_state(_qapp_session):
    from PySide6.QtGui import QPixmap

    from h3d.gui.widgets import ElidedLabel

    lbl = ElidedLabel("一段会被省略的文字")
    for key in ("_full", "_mode"):
        lbl.__dict__.pop(key, None)
    pix = lbl.grab()                        # 真跑一次 paintEvent
    assert not pix.isNull()


def test_minimum_size_hint_never_asks_for_width(_qapp_session):
    """最小宽度恒为 0 —— 这是「窗口不被文字撑大」的根本。"""
    from h3d.gui.widgets import ElidedLabel

    assert ElidedLabel("很长" * 40).minimumSizeHint().width() == 0
