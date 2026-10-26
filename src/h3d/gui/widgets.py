"""h3d.gui.widgets : 主界面复用的稳定版控件（窗口不随文本跳变）。

实机缺陷（2026-10-26 反馈）
--------------------------
"管线一跑起来，主窗口就时不时自己变大/变窄，进度看不到全貌"。根因不是
"窗口自己会动"，而是 **Qt 的布局最小尺寸**在动：

* 状态行的 ``QLabel`` 文字会随进度变长（``● S1 运行中 · 深度图 12/30``、
  ``已运行 00:12:34 · 深度图 12/30 · 最后输出 45s 前（计算中）``）。QLabel 的
  ``minimumSizeHint`` 等于**整段文字的宽度**，且默认不换行、不省略；
* 布局里的 stretch 控件拿到空间后，``minimumSizeHint`` 之和超过窗口当前宽度，
  Qt 就会把顶层窗口撑大（用户没拖动，窗口自己变宽）；
* 文字随后变短时窗口**不会缩回去** —— 表现就是"越跑越宽、某一刻突然跳一下"。

修法不是"把字截短"（那样信息就丢了），而是把这类控件换成
:class:`ElidedLabel`：``minimumSizeHint`` 恒为 0、内容按当前宽度省略、**全文
挂在 tooltip 上**且可复制。于是布局的最小宽度不再受运行时文字影响，窗口稳定，
同时信息仍可读到（悬停即见全文）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QLabel, QSizePolicy


class ElidedLabel(QLabel):
    """单行省略标签：宽度不够时中间/右侧省略，全文进 tooltip。

    参数
    ----
    text:    初始文字。
    mode:    省略位置（``Qt.ElideRight`` / ``ElideMiddle`` / ``ElideLeft``）。
    min_chars: ``sizeHint`` 的"自然宽度"按多少个字符估算（默认 12）。

    关键实现点
    ----------
    * ``minimumSizeHint()`` 返回宽度 0 —— 这是**窗口不再被撑大的根本**。
      少这一条，Qt 仍会按文字宽度要求最小空间。
    * ``sizeHint()`` 返回按 ``min_chars`` 估的宽度：足够让布局给出合理初始宽度，
      但不跟着全文长度线性增长（否则左侧面板一开就被顶宽）。
    * 文字变化只更新 ``_full`` 与 tooltip，然后 ``update()`` 重绘，不触发
      ``adjustSize()``。
    """

    def __init__(self, text: str = "", parent=None,
                 mode: Qt.TextElideMode = Qt.ElideRight,
                 min_chars: int = 12):
        super().__init__(parent)
        self._full = ""
        self._mode = mode
        self._min_chars = max(1, int(min_chars))
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    # ---------------------------------------------------------- API
    def setText(self, text: str) -> None:  # noqa: N802 - Qt 命名
        self._full = text or ""
        self.setToolTip(self._full)
        # 父类文字同步存一份：无障碍/自动化测试与 copy() 都读它
        super().setText(self._full)
        self.update()

    def text(self) -> str:
        return self._full

    def fullText(self) -> str:  # noqa: N802 - 与 Qt 命名风格一致
        return self._full

    # ---------------------------------------------------------- 尺寸
    def minimumSizeHint(self):  # noqa: N802
        from PySide6.QtCore import QSize

        return QSize(0, self.fontMetrics().height())

    def sizeHint(self):  # noqa: N802
        from PySide6.QtCore import QSize

        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance("0" * self._min_chars) + 8,
                     fm.height())

    # ---------------------------------------------------------- 绘制
    def paintEvent(self, event):  # noqa: N802 - Qt 命名
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        fm = QFontMetrics(self.font())
        rect = self.contentsRect()
        flags = int(self.alignment()) | int(Qt.AlignVCenter)
        elided = fm.elidedText(self._full, self._mode, rect.width())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(rect, flags, elided)
        painter.end()


__all__ = ["ElidedLabel"]
