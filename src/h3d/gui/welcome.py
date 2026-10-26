"""h3d.gui.welcome : 欢迎页（新建项目 / 打开历史 / 示例演示）。

GUI.docx 欢迎页需求：新建项目（向导）、打开历史项目、运行示例项目。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

from h3d.gui.history import HistoryStore
from h3d.gui.wizard import WizardWindow


class WelcomeWindow(QWidget):
    """启动欢迎页：三入口（新建向导 / 历史项目 / 示例演示）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.history = HistoryStore()
        self.setWindowTitle("H3D 中小型文物三维建模自动化系统")
        self.resize(760, 460)
        self._build()

    def _build(self):
        v = QVBoxLayout(self)
        v.addLayout(self._build_header())
        v.addSpacing(10)
        row = QHBoxLayout()
        # 左：历史项目
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>最近项目</b>（双击打开 / 右键删除）"))
        self.list_recent = QListWidget()
        self._reload_history()
        self.list_recent.itemDoubleClicked.connect(self._open_selected)
        self.list_recent.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_recent.customContextMenuRequested.connect(
            self._context_menu)
        left.addWidget(self.list_recent, 1)
        # 删除按钮紧贴项目列表（项目旁边，便于删除不需要的项目）
        del_row = QHBoxLayout()
        self.btn_remove = QPushButton("删除选中项目")
        self.btn_remove.setStyleSheet(
            "QPushButton{color:#c0392b;border:1px solid #c0392b;"
            "border-radius:4px;padding:4px 10px;}"
            "QPushButton:hover{background:#c0392b;color:white;}")
        self.btn_remove.clicked.connect(self._remove_selected)
        del_row.addStretch()
        del_row.addWidget(self.btn_remove)
        left.addLayout(del_row)
        row.addLayout(left, 2)
        # 右：入口按钮
        right = QVBoxLayout()
        btn_new = QPushButton("＋ 新建项目（向导）")
        btn_new.setStyleSheet(
            "QPushButton{background:#2d7d46;color:white;padding:12px;"
            "font-size:14px;border-radius:6px;}")
        btn_new.clicked.connect(self._start_wizard)
        btn_demo = QPushButton("▶ 运行示例项目（一键演示）")
        btn_demo.clicked.connect(self._start_demo)
        right.addWidget(btn_new)
        right.addSpacing(8)
        right.addWidget(btn_demo)
        right.addStretch()
        row.addLayout(right, 1)
        v.addLayout(row, 1)

    def _build_header(self) -> QHBoxLayout:
        """页头：左侧应用图标 + 右侧标题副标题。

        图标缺失（源码树没 assets/ 或被裁剪的发行包）时只显示文字，
        不让欢迎页起不来 —— 装饰性资源一律降级处理。
        """
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        logo = self._logo_label(84)
        if logo is not None:
            row.addWidget(logo, 0, Qt.AlignTop)
        text = QVBoxLayout()
        text.setContentsMargins(6, 0, 0, 0)
        text.addWidget(QLabel(
            "<h2 style='margin:0'>H3D 中小型文物三维建模自动化系统</h2>"))
        # 2026-10-26：按用户要求删去旧副标题行（原为「——全程零…行」）。
        text.addStretch()
        row.addLayout(text, 1)
        return row

    @staticmethod
    def _logo_label(size: int):
        """按需缩放的应用图标 QLabel；资源缺失返回 None。"""
        try:
            from h3d.gui.resources import asset_path, ICON_PNG

            p = asset_path(ICON_PNG)
            if not p:
                return None
            pm = QPixmap(p)
            if pm.isNull():
                return None
            lab = QLabel()
            lab.setPixmap(pm.scaled(size, size, Qt.KeepAspectRatio,
                                    Qt.SmoothTransformation))
            lab.setFixedSize(size, size)
            lab.setAlignment(Qt.AlignCenter)
            return lab
        except Exception:  # noqa: BLE001 - 图标是装饰，不能阻断启动
            return None

    def _reload_history(self):
        self.list_recent.clear()
        for e in self.history.list():
            item = QListWidgetItem(
                f"{e.get('name', '?')}　{e.get('scene', '')}\n"
                f"{e.get('path', '')}（{e.get('last_opened', '')}）")
            item.setData(0x0100, e)  # Qt.UserRole
            self.list_recent.addItem(item)

    def _open_selected(self, item):
        e = item.data(0x0100)
        cfg_path = self.history.find_config(e.get("path", ""))
        if not cfg_path:
            QMessageBox.warning(self, "无法打开",
                                f"未找到项目配置：{e.get('path')}")
            return
        from h3d.gui.app import MainWindow
        from h3d.gui.config_edit import ProjectConfig
        # 必须**读回 config.yaml**：只传 root/name/scene 会得到一份默认配置，
        # 用户一点「一键生成」就把调好的参数覆盖成默认值（2026-10-25 实机）。
        cfg = ProjectConfig.from_root(e.get("path", ""),
                                      e.get("name", ""),
                                      e.get("scene", "scene1"))
        self._main = MainWindow(cfg)
        self._main.show()
        self.close()

    def _context_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        item = self.list_recent.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        act_del = menu.addAction("删除该项目记录")
        act_open = menu.addAction("打开项目")
        act = menu.exec(self.list_recent.mapToGlobal(pos))
        if act == act_del:
            self._remove_item(item)
        elif act == act_open:
            self._open_selected(item)

    def _remove_item(self, item):
        e = item.data(0x0100)
        self.history.remove(e.get("path", ""))
        self._reload_history()

    def _remove_selected(self):
        item = self.list_recent.currentItem()
        if item:
            self._remove_item(item)

    def _start_wizard(self):
        try:
            # 关键：顶层窗口必须由 self 持有引用，否则槽返回即被 Python
            # GC，C++ 窗口随之销毁——表现为「点新建项目直接闪退」
            self._wizard = WizardWindow(on_done=self._wizard_done)
            self._wizard.show()
            self.close()
        except Exception as exc:  # noqa: BLE001 - 不闪退
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "向导启动失败",
                                 f"无法打开新建向导：\n{exc}\n\n"
                                 f"详细日志：~/.workbuddy/h3d_gui_crash.log")

    def _wizard_done(self, cfg):
        try:
            self.history.add(cfg.root_dir, cfg.name, cfg.scene)
            from h3d.gui.app import MainWindow
            self._main = MainWindow(cfg)
            self._main.show()
            self._main._start_run()
        except Exception as exc:  # noqa: BLE001 - 不闪退
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "项目启动失败",
                                 f"无法启动主界面：\n{exc}\n\n"
                                 f"详细日志：~/.workbuddy/h3d_gui_crash.log")

    def _start_demo(self):
        """内置示例数据一键演示。"""
        from h3d.gui.demo import create_demo_project
        target = os.path.join(os.path.expanduser("~"), "H3D_demo")
        try:
            cfg = create_demo_project(target)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "示例启动失败", str(exc))
            return
        self.history.add(cfg.root_dir, cfg.name, cfg.scene)
        from h3d.gui.app import MainWindow
        self._main = MainWindow(cfg)
        self._main.show()
        self._main._start_run()
        self.close()


__all__ = ["WelcomeWindow"]
