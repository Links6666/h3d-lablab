"""h3d.gui.report_view : 质检报告可视化（P2，HTML 内嵌 + 指标表）。

QTextBrowser 内嵌 report.html（Qt 自带，无 WebEngine 重依赖）+ 质检
指标表格（qc_metrics.json 清晰标注达标/不达标）。
"""

from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QTextBrowser,
                               QVBoxLayout, QWidget)


def _color_for(ok: bool) -> str:
    return "#2d7d46" if ok else "#c0392b"


def build_qc_table(root_dir: str, item_id: str) -> QTableWidget:
    """从 qc_metrics.json 构建质检指标表（达标/不达标着色）。"""
    table = QTableWidget(0, 4)
    table.setHorizontalHeaderLabels(["指标", "数值", "阈值", "判定"])
    metrics_path = os.path.join(root_dir, "60_qc", item_id,
                                "qc_metrics.json")
    if not os.path.exists(metrics_path):
        table.setRowCount(1)
        table.setItem(0, 0, QTableWidgetItem("（无质检产物）"))
        return table
    try:
        m = json.load(open(metrics_path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return table
    rows = [
        ("几何覆盖率", f"{m.get('coverage', 0) * 100:.1f}%",
         f"≥{m.get('thresholds', {}).get('coverage_threshold', 0.8) * 100:.0f}%",
         m.get("coverage", 0) >= m.get("thresholds", {}).get(
             "coverage_threshold", 0.8)),
        ("中误差", f"{m.get('rmse_mm', 0):.2f} mm",
         f"≤{m.get('thresholds', {}).get('rmse_threshold_mm', 5)} mm",
         m.get("rmse_mm", 1e9) <= m.get("thresholds", {}).get(
             "rmse_threshold_mm", 5)),
        ("边界边比例", f"{m.get('boundary_ratio', 0) * 100:.2f}%", "低",
         True),
        ("纹理清晰度", f"{m.get('texture_sharpness', 0):.1f}", "-", True),
        ("纹理覆盖率", f"{m.get('texture_coverage', 0) * 100:.1f}%",
         "≥2%", m.get("texture_coverage", 0) >= 0.02),
    ]
    for name, val, thr, ok in rows:
        r = table.rowCount()
        table.insertRow(r)
        table.setItem(r, 0, QTableWidgetItem(name))
        table.setItem(r, 1, QTableWidgetItem(val))
        table.setItem(r, 2, QTableWidgetItem(thr))
        item = QTableWidgetItem("✅ 达标" if ok else "❌ 未达标")
        item.setForeground(QColor(_color_for(ok)))
        table.setItem(r, 3, item)
    return table


class ReportView(QWidget):
    """质检报告页：HTML 内嵌 + 指标表 + 打开外部浏览器。"""

    def __init__(self, root_dir: str, item_id: str, parent=None):
        super().__init__(parent)
        self.root_dir = root_dir
        self.item_id = item_id
        v = QVBoxLayout(self)
        head = QHBoxLayout()
        head.addWidget(QLabel("<b>质检报告</b>"))
        btn_open = QPushButton("在浏览器打开完整报表")
        btn_open.clicked.connect(self._open_external)
        head.addStretch()
        head.addWidget(btn_open)
        v.addLayout(head)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.table = build_qc_table(root_dir, item_id)
        v.addWidget(self.table, 1)
        v.addWidget(self.browser, 2)
        self._load_html()

    def _load_html(self):
        from h3d.core import paths as _p
        for cand in (_p.report_html(self.root_dir),
                     os.path.join(self.root_dir, "output", "report",
                                  "report.html")):
            if os.path.exists(cand):
                html = open(cand, encoding="utf-8").read()
                self.browser.setHtml(html)
                return
        self.browser.setPlainText("（无报表——运行一键生成后自动生成）")

    def _open_external(self):
        import webbrowser
        from h3d.core import paths as _p
        for cand in (_p.report_html(self.root_dir),
                     os.path.join(self.root_dir, "output", "report",
                                  "report.html")):
            if os.path.exists(cand):
                webbrowser.open(cand)
                return


__all__ = ["ReportView", "build_qc_table"]
