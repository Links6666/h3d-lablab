"""h3d.gui.anomaly_view : 「异常筛查」页（X3 结论可视化）。

为什么这页必须存在
------------------
签名结论原本只落在报表 HTML 与 ``regions.csv``：用户要知道「第 3 处到底在哪」，
得自己拿坐标去 2048² 的图集上找。CLI 能跑、报表有数字，但**看不到位置**——
那这一层筛查就停在「跑过一次」。

三块内容：摘要行、完整图斑清单、点一处看那一处的热力图裁剪。

工程约定（都是踩过的坑）
------------------------
* **读图用 PIL 不用 cv2**：``cv2.imread`` 在中文路径下静默返回 ``None``
  （与 Open3D ASSIMP 的 ANSI/GBK 问题同源）。PIL 走 ``open()``，路径按 UTF-8。
* **槽内自己兜住异常**：Qt 槽里逃出去的异常不外传（windowed 打包版 stderr 是
  ``None``），表现是「点了没反应」，比报错更难查。所以 ``_on_select`` 全程
  try/except 并把原因写进说明行。
* **可变文字一律用 :class:`ElidedLabel`**：``QLabel.minimumSizeHint`` 等于整段
  文字宽，换件时摘要变长会把窗口撑宽（见 ``gui/widgets``）。
* **热力图缓存一次**：2048² PNG 约 5 MB，每次点行都重读会有一两百毫秒的卡顿。
"""

from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView,
                               QLabel, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from h3d.anomaly.summary import (ItemSummary, STATUS_LABEL, crop_box,
                                 load_summary, note_line, summary_line)
from h3d.gui.widgets import ElidedLabel


class HeatCropView(QLabel):
    """热力图的局部查看器：给一处 bbox，裁出那一块显示。

    不显示整张 2048²：整图上 7 处图斑只占几个像素，等于什么也看不见。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setText("（选中一处图斑查看热力图）")
        self.setMinimumSize(260, 260)
        self.setStyleSheet("border:1px solid #555;background:#1a1a1a;"
                           "color:#888;")
        self.current_bbox: tuple[int, int, int, int] | None = None
        #: 并排图里是否真的有左侧原图（原图缺失时降级成只显示热力图）
        self.has_reference: bool = False
        #: 并排图的 ``(高, 宽)``，供测试核对两半等高
        self.composite_shape: tuple[int, int] = (0, 0)
        self._pix: QPixmap | None = None
        self._cache: dict[str, np.ndarray] = {}

    # ------------------------------------------------------------ API
    def has_image(self) -> bool:
        return self._pix is not None

    def clear(self) -> None:
        self._pix = None
        self.current_bbox = None
        self.has_reference = False
        self.composite_shape = (0, 0)
        self.setPixmap(QPixmap())
        self.setText("（选中一处图斑查看热力图）")

    def show_region(self, heat_png: str | None,
                    bbox: tuple[int, int, int, int],
                    atlas_png: str | None = None) -> bool:
        """裁剪并显示「原图 | 热力图」并排。失败返回 ``False``，**不抛**。

        并排是必要的：热力图告诉用户「哪里可疑」，原图告诉他「那是什么」。
        只有热力图时，他拿到坐标还得自己打开发散图去找那一块——复核没闭环。
        原图缺失时降级成只显示热力图（不阻断定位）。
        """
        heat = self._load(heat_png)
        if heat is None:
            self.clear()
            return False
        h, w = heat.shape[:2]
        x0, y0, x1, y1 = crop_box(bbox, (h, w))
        sub_heat = np.ascontiguousarray(heat[y0:y1 + 1, x0:x1 + 1])
        if sub_heat.size == 0:
            self.clear()
            return False

        ref = self._load(atlas_png)
        if ref is not None and ref.shape[:2] == (h, w):
            sub_ref = np.ascontiguousarray(ref[y0:y1 + 1, x0:x1 + 1])
            # 分隔条：两张图同色系时，没有它看不出哪里是原图哪里是热力图
            sep = np.full((sub_heat.shape[0], 6, 3), 90, np.uint8)
            composite = np.concatenate([sub_ref, sep, sub_heat], axis=1)
            self.has_reference = True
        else:
            composite = sub_heat
            self.has_reference = False

        self.composite_shape = composite.shape[:2]
        img = QImage(composite.data, composite.shape[1], composite.shape[0],
                     3 * composite.shape[1], QImage.Format_RGB888).copy()
        self._pix = QPixmap.fromImage(img)
        self.current_bbox = (x0, y0, x1, y1)
        self._apply_scale()
        return True

    # ------------------------------------------------------------ 内部
    def _load(self, path: str | None) -> np.ndarray | None:
        """按路径缓存一张全尺寸图。2048² PNG 约 5 MB，每次点行重读会卡顿。"""
        if not path or not os.path.isfile(path):
            return None
        hit = self._cache.get(path)
        if hit is not None:
            return hit
        try:
            from PIL import Image

            with Image.open(path) as im:
                arr = np.asarray(im.convert("RGB"))
        except Exception:  # noqa: BLE001 - 坏图/占用都退成「不可用」
            return None
        self._cache[path] = arr
        return arr

    def _apply_scale(self) -> None:
        if self._pix is None:
            return
        target = self.size()
        if target.width() < 40 or target.height() < 40:
            target = self.minimumSize()
        self.setPixmap(self._pix.scaled(target, Qt.KeepAspectRatio,
                                        Qt.SmoothTransformation))

    def resizeEvent(self, event):  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._apply_scale()


class AnomalyPanel(QWidget):
    """异常筛查页：摘要 + 图斑清单 + 局部热力图。"""

    COLUMNS = ("#", "边界框", "纹素", "占岛内", "均分")
    HEADER_TIPS = (
        "图斑序号（按面积降序）",
        "图集纹素坐标 (x0,y0)-(x1,y1)，含端点",
        "该图斑包含的纹素数",
        "占岛内面积的比例（分母 = 图集里所有岛的纹素总数）",
        "图斑内异常分的均值（k 是判异常的门限）",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.summary = ItemSummary(item_id="", status="missing")
        self._loading = False

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        self.lbl_summary = ElidedLabel("", mode=Qt.ElideRight, min_chars=44)
        self.lbl_note = ElidedLabel("", mode=Qt.ElideRight, min_chars=40)
        self.lbl_note.setStyleSheet("QLabel{color:#8a8a8a;font-size:12px;}")
        v.addWidget(self.lbl_summary)
        v.addWidget(self.lbl_note)

        bar = QHBoxLayout()
        self.btn_open_dir = QPushButton("打开产物目录")
        self.btn_open_dir.setToolTip(
            "60_qc/<件>/anomaly/：热力图、掩膜、regions.csv、metrics.json")
        self.btn_open_dir.clicked.connect(self._open_dir)
        self.btn_open_dir.setEnabled(False)
        bar.addWidget(self.btn_open_dir)
        bar.addStretch()
        v.addLayout(bar)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        for i, tip in enumerate(self.HEADER_TIPS):
            self.table.horizontalHeaderItem(i).setToolTip(tip)
        # 列宽：只有「边界框」拉伸，其余按内容收紧。不这样设的话坐标列只有
        # 默认 100 px，2048² 上的四位坐标会被折成三行（实机截图如此）。
        head = self.table.horizontalHeader()
        for i in range(len(self.COLUMNS)):
            head.setSectionResizeMode(i, QHeaderView.Stretch if i == 1
                                      else QHeaderView.ResizeToContents)
        for i, tip in enumerate(self.HEADER_TIPS):
            self.table.horizontalHeaderItem(i).setToolTip(tip)
        # 列宽：只有「边界框」拉伸，其余按内容收紧。不这样设的话坐标列只有
        # 默认 100 px，2048² 上的四位坐标会被折成三行（实机截图如此）。
        head = self.table.horizontalHeader()
        for i in range(len(self.COLUMNS)):
            head.setSectionResizeMode(i, QHeaderView.Stretch if i == 1
                                      else QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._on_select)

        self.viewer = HeatCropView()

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.table)
        split.addWidget(self.viewer)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        v.addWidget(split, 1)

    # ------------------------------------------------------------ API
    def apply(self, summary: ItemSummary) -> None:
        """灌一份读模型（``h3d.anomaly.summary.load_summary`` 的返回值）。"""
        self.summary = summary
        self.lbl_summary.setText((f"{STATUS_LABEL.get(summary.status, summary.status)} · "
                                  f"{summary_line(summary)}")
                                 if summary.status == "ok"
                                 else summary_line(summary))
        self.lbl_note.setText(note_line(summary))
        self.btn_open_dir.setEnabled(bool(summary.out_dir)
                                     and os.path.isdir(summary.out_dir))
        self._fill_table(summary)
        self.viewer.clear()
        if summary.regions:
            # 打开就看到第一处：让「有结论」这件事立刻可见
            self.table.selectRow(0)

    # ------------------------------------------------------------ 内部
    def _fill_table(self, summary: ItemSummary) -> None:
        self._loading = True
        try:
            self.table.setRowCount(0)
            for r in summary.regions:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.table.setItem(row, 0, QTableWidgetItem(str(r.region_id)))
                self.table.setItem(row, 1, QTableWidgetItem(r.bbox_text))
                self.table.setItem(row, 2, QTableWidgetItem(str(r.texels)))
                # 三位小数：真机 7 处的占比是 0.020%~0.043%，两位小数会把
                # 它们全压成 0.02/0.03/0.04，看表格分不出大小
                self.table.setItem(row, 3,
                                   QTableWidgetItem(f"{r.area_ratio * 100:.3f}%"))
                self.table.setItem(row, 4,
                                   QTableWidgetItem(f"{r.score_mean:.2f}"))
        finally:
            self._loading = False

    def _on_select(self) -> None:
        """选中一行 → 显示那一处的热力图裁剪。

        槽内全程兜住：Qt 不外传异常，漏出去就是「点了没反应」。
        """
        if self._loading:
            return
        row = self.table.currentRow()
        if row < 0 or row >= len(self.summary.regions):
            return
        region = self.summary.regions[row]
        try:
            ok = self.viewer.show_region(self.summary.heat_png, region.bbox,
                                         self.summary.atlas_png)
        except Exception as exc:  # noqa: BLE001 - 槽内不得冒泡
            ok = False
            self.lbl_note.setText(f"热力图显示失败：{type(exc).__name__}: {exc}")
            return
        if not ok:
            self.lbl_note.setText(
                "热力图不可用（anomaly_heat.png 缺失或损坏）——"
                "坐标与数字仍可读，可对照原图集人工复核")

    def _open_dir(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        path = self.summary.out_dir
        if path and os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def load_panel_summary(stage_root: str, item_id: str) -> ItemSummary:
    """给面板用的薄包装：读当前件。空件号退成「未筛查」，不抛。"""
    if not stage_root or not item_id:
        return ItemSummary(item_id=item_id or "", status="missing",
                           note="尚未选择件")
    return load_summary(stage_root, item_id)


__all__ = ["AnomalyPanel", "HeatCropView", "load_panel_summary"]
