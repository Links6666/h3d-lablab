"""h3d.gui.archive_view : 主界面「文物档案」页（时相列表 + 产物版本）。

这一页解决什么问题
------------------
档案层的前四个切片只在命令行可用：``h3d archive migrate`` 建档，
``h3d archive status`` 看时相。GUI 用户打开项目、跑完一批，``archive/``
永远不存在 —— 切片 5 写的「产物登记」在界面上从未发生。

于是本页挂两件事：
1. 打开项目时把档建起来（``archive.workspace.ensure_archive``，幂等）；
2. 把时相、路线、基准期、产物版本摊开。版本数是这套东西对用户唯一有
   感知价值的输出 —— 它说明「这份产物是可复现的一份，不是随手一跑」。

排版纪律（2026-10-26 实机教训）
-------------------------------
所有会变的文字都用 :class:`ElidedLabel`（最小宽度 0 + tooltip）。项目路径、
件号、说明文案的长度都不受控，用 ``QLabel`` 会把主窗口的最小宽度顶大，
表现为「打开项目窗口自己变宽」。
"""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout,
                               QHeaderView, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from h3d.gui.widgets import ElidedLabel

__all__ = ["ArchivePanel", "InheritWorker"]

log = logging.getLogger(__name__)

#: 列定义。顺序变更会打破界面列宽与测试断言，改之前先想清是否真的需要。
_COLUMNS: tuple[str, ...] = ("时相", "采集日期", "路线", "基准期",
                             "产物版本", "尺度来源", "质检")

#: 居中对齐的列（0 时相与 1 日期左对齐更好读）。
_CENTER_COLS = (3, 4, 5, 6)


class InheritWorker(QThread):
    """后台跑图集继承。

    为什么必须开线程：一次 2048² 的逐纹素重投影实测 50 s 量级，跑在主线程
    等于界面假死，而用户的第一反应是再点一次（于是排两个烘焙任务抢 GPU）。
    参照 ``BatchRunner`` 的纪律：新建前查 ``isRunning()``，旧对象 ``deleteLater()``。
    """

    done = Signal(bool, str, dict)

    def __init__(self, base: str, source: str, parent=None):
        super().__init__(parent)
        self._base = base
        self._source = source

    def run(self):  # noqa: D401 - QThread 入口
        try:
            from h3d.archive.inherit_epoch import inherit_epoch

            res = inherit_epoch(self._base, self._source)
            self.done.emit(res.ok, res.message,
                           {"atlas_ref": res.atlas_ref,
                            "texture_path": res.texture_path,
                            "base_epoch": res.base_epoch})
        except Exception as exc:  # noqa: BLE001 - 线程里异常不外传
            log.warning("图集继承失败：%s", exc)
            self.done.emit(False, f"继承失败：{exc}", {})


class ArchivePanel(QWidget):
    """文物档案页：顶部摘要 + 按钮，下面一张时相表。"""

    #: 档案内容变化（继承出新版本）——主窗口据此刷新其它视图
    archive_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._status = None
        self._worker = None
        self._build()

    # ---------------------------------------------------------- UI
    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.lbl_title = ElidedLabel("尚未建档", min_chars=14)
        self.lbl_title.setStyleSheet(
            "QLabel{font-weight:bold;font-size:13px;}")
        head.addWidget(self.lbl_title, 1)
        self.btn_inherit = QPushButton("继承基准图集…")
        self.btn_inherit.setEnabled(False)
        self.btn_inherit.setToolTip(
            "把本项目的影像重投影到另一个项目（基准期）定义的 UV 图集上，"
            "使两期纹理可以逐 texel 比色。不会重新展开 UV。")
        self.btn_inherit.clicked.connect(self._inherit)
        head.addWidget(self.btn_inherit)
        self.btn_open = QPushButton("打开档案目录")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_dir)
        head.addWidget(self.btn_open)
        v.addLayout(head)

        self.lbl_note = ElidedLabel("", min_chars=16)
        self.lbl_note.setStyleSheet("QLabel{color:#9aa4b2;font-size:11px;}")
        v.addWidget(self.lbl_note)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(_COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(110)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Stretch)
        hdr.setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 64)
        v.addWidget(self.table, 1)

    # ---------------------------------------------------------- 数据
    def apply(self, status) -> bool:
        """按 ``ArchiveStatus`` 刷新；``None`` 表示「还没有档案」。

        返回是否成功。**不抛**：本方法被 Qt 槽链调用，异常在 windowed 版
        （``stderr=None``）会被静默吞掉，表现为「面板一直是空的」。
        """
        try:
            self._status = status
            if status is None:
                self.lbl_title.setText("尚未建档")
                self.lbl_note.setText("")
                self.table.setRowCount(0)
                self.btn_open.setEnabled(False)
                self.btn_inherit.setEnabled(False)
                return True

            title = status.artifact_id or "（未识别件号）"
            if status.title and status.title != status.artifact_id:
                title = f"{status.artifact_id} · {status.title}"
            self.lbl_title.setText(title)
            self.lbl_note.setText(status.message)

            rows = list(status.rows)
            self.table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                values = (row.epoch_id,
                          row.captured_on,
                          row.route_text(),
                          row.baseline_text(),
                          row.versions_text(),
                          row.scale_text(),
                          row.qc_status)
                for c, text in enumerate(values):
                    item = QTableWidgetItem(text)
                    if c in _CENTER_COLS:
                        item.setTextAlignment(Qt.AlignCenter)
                    self.table.setItem(r, c, item)
            self.btn_open.setEnabled(os.path.isdir(status.archive_dir))
            self.btn_inherit.setEnabled(status.is_open())
            return True
        except Exception as exc:  # noqa: BLE001 - 面板失败不能影响建模
            log.warning("档案面板刷新失败：%s", exc)
            return False

    def status(self):
        return self._status

    # ---------------------------------------------------------- 继承
    def _inherit(self) -> None:
        """选一个基准项目，把本期影像烘到它的图集上。

        入口自己的判定顺序（照 ``BatchRunner`` 的纪律）：
        1. 本项目必须有档案（没有基准期就没有「本期」可登记）；
        2. 已有任务在跑 → 直接返回，不排队；
        3. 用户取消选目录 → 什么都不做。
        """
        try:
            st = self._status
            if st is None or not st.is_open():
                return
            if self._worker is not None and self._worker.isRunning():
                return
            base = QFileDialog.getExistingDirectory(
                self, "选择基准项目（定义图集的那一个）")
            if not base:
                return
            if self._worker is not None:
                self._worker.deleteLater()
            worker = InheritWorker(base, st.root)
            worker.done.connect(self._on_inherit_done)
            self._worker = worker
            self.btn_inherit.setEnabled(False)
            self.lbl_note.setText(
                "正在继承基准图集…（首次烘焙需数十秒；期间可继续其它操作）")
            worker.start()
        except Exception as exc:  # noqa: BLE001 - 槽内异常不外传
            log.warning("启动图集继承失败：%s", exc)
            self.lbl_note.setText(f"启动失败：{exc}")

    def _on_inherit_done(self, ok: bool, message: str, info) -> None:
        """先刷新、再写结论。

        顺序不能反：``archive_changed`` 会触发主窗口 ``_refresh_archive``，
        而刷新会 ``apply()`` 一次状态、把说明行改回「档案已存在」——
        于是「已继承：…」这句话一闪即失（实测被覆盖，用户读不到结果）。
        """
        try:
            self._worker = None
            st = self._status
            self.btn_inherit.setEnabled(bool(st is not None and st.is_open()))
            if ok:
                self.archive_changed.emit()
            self.lbl_note.setText(message)
        except Exception as exc:  # noqa: BLE001
            log.warning("继承结果展示失败：%s", exc)

    # ---------------------------------------------------------- 动作
    def _open_dir(self) -> None:
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            st = self._status
            if st is None or not os.path.isdir(st.archive_dir):
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(st.archive_dir))
        except Exception as exc:  # noqa: BLE001 - 打开目录失败不影响主流程
            log.warning("打开档案目录失败：%s", exc)
