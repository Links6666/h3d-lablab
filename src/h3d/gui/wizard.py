"""h3d.gui.wizard : 新建项目三步向导（场景选择/数据拖拽导入/参数确认）。"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox,
                               QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QMessageBox,
                               QPushButton, QRadioButton, QSpinBox,
                               QVBoxLayout, QWidget)

from h3d.core.media import image_dialog_filter
from h3d.gui.config_edit import ProjectConfig


def infer_root_from_photo(photo_dir: str) -> str:
    """由照片位置推断项目根目录（唯一定义，向导与兜底共用）。

    规则
    ----
    - ``.../00_photo/<item>`` → 上两级（标准布局的项目根）
    - ``.../00_photo``        → 上一级
    - 其他普通目录            → 上一级（产物与源素材并列，不污染照片目录）

    例外：若上一级是盘符根/文件系统根（如 ``D:\\``），退回照片目录本身，
    避免在盘根创建 ``00_raw`` / ``00_photo``。

    历史：2026-10-17 曾一律取照片目录本身，导致产物写进用户素材目录；
    本函数把该口径收敛到一处，避免 ``_auto_root_from_photo`` 与
    ``_finish`` 的兜底逻辑再次分裂。
    """
    p = os.path.abspath(photo_dir)
    # 传入的是文件（而非目录）时上提一级；用扩展名辅助判断，
    # 避免把尚不存在的目录误当成文件。
    if os.path.isfile(p) or (not os.path.isdir(p)
                             and os.path.splitext(p)[1]):
        p = os.path.dirname(p)
    name = os.path.basename(p)
    parent = os.path.dirname(p)
    if name == "00_photo":
        return parent
    if os.path.basename(parent) == "00_photo":
        return os.path.dirname(parent)
    if os.path.dirname(parent) == parent:      # parent 为盘符根 / 根目录
        return p
    return parent


class DropArea(QListWidget):
    """拖拽导入区域：接受文件/文件夹拖放，记录路径。"""

    def __init__(self, hint: str, accept_dirs: bool = True,
                 accept_files: bool = True, parent=None,
                 on_change=None, collapse_to_dir: bool = False,
                 name_filter: str = "", browse_mode: str = ""):
        super().__init__(parent)
        self.on_change = on_change
        self.accept_dirs = accept_dirs
        self.accept_files = accept_files
        # 照片区专用：拖入多张图片时自动收敛为公共父目录（2026-10-17）
        self.collapse_to_dir = collapse_to_dir
        self.name_filter = name_filter
        # 浏览对话框行为（2026-10-17 实机缺陷修复）
        # 原实现用 accept_dirs 单一开关分流：照片区 accept_dirs 默认 True，
        # 双击后进入 getExistingDirectory —— 该对话框**只列目录、不列文件**，
        # 表现为"JPG/PNG 等照片文件根本无法选择"。
        # 现改为显式模式：dirs=只选目录，files=多选文件（带 name_filter）。
        if browse_mode in ("dirs", "files"):
            self.browse_mode = browse_mode
        elif accept_files:
            self.browse_mode = "files"
        else:
            self.browse_mode = "dirs"
        self.setAcceptDrops(True)
        self.setMinimumHeight(70)
        self.addItem(hint)
        self.setToolTip("双击选择文件（照片可多选）／右键可改选文件夹／也可直接拖入")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._ctx_menu)
        self._paths: list[str] = []

    # ---- 删除已选数据（实机需求）----
    def _ctx_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        item = self.itemAt(pos)
        menu = QMenu(self)
        if self.accept_files:
            menu.addAction("选择照片/文件…", self.browse)
        if self.accept_dirs:
            menu.addAction("选择文件夹…", self.browse_dir)
        menu.addSeparator()
        if item is not None and self._paths:
            menu.addAction("移除该项", self._remove_selected)
        menu.addAction("清空全部", self.clear_paths)
        menu.exec(self.mapToGlobal(pos))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self._paths:
            self._remove_selected()
            return
        super().keyPressEvent(e)

    def _remove_selected(self):
        row = self.currentRow()
        if 0 <= row < len(self._paths):
            del self._paths[row]
            self._refresh()

    def clear_paths(self):
        self._paths.clear()
        self._refresh()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        picked = [u.toLocalFile() for u in e.mimeData().urls()
                  if u.toLocalFile()]
        self._add_many(picked)

    def _collapse(self, picked: list[str]) -> list[str]:
        """照片区容错：把「只拖入一批文件」收敛为它们的公共父目录。

        用户通常全选一批 JPG 直接拖入，原实现取 ``paths()[0]`` 当"照片
        文件夹"，校验时报「照片位置不是文件夹：xxx.jpg」——表现为
        "符合规范的 JPG 不被识别"。此处统一在落盘前收敛。
        """
        if not (self.collapse_to_dir and picked):
            return picked
        files = [p for p in picked if not os.path.isdir(p)]
        dirs = [p for p in picked if os.path.isdir(p)]
        if files and not dirs:
            try:
                common = os.path.commonpath(files)
            except ValueError:      # 跨盘符（Windows）
                common = os.path.dirname(files[0])
            # 只选一张照片时 commonpath 返回的就是该文件本身，
            # 必须再取一级目录，否则校验报"照片位置不是文件夹"。
            if not os.path.isdir(common):
                common = os.path.dirname(common)
            return [common]
        return picked

    def _add_many(self, picked: list[str]):
        """统一入口：收敛 → 过滤 → 去重 → 刷新（拖拽与对话框共用）。"""
        items = self._collapse(list(picked))
        if self.collapse_to_dir and items:
            # 照片目录是单值语义（cfg.photo_dir 只取 paths()[0]）：
            # 改选即替换，避免新旧目录并存导致实际用的是第一次选的目录。
            self._paths.clear()
            items = items[:1]
        for p in items:
            is_dir = os.path.isdir(p)
            if is_dir and not self.accept_dirs:
                continue
            if not is_dir and not self.accept_files:
                continue
            if p not in self._paths:
                self._paths.append(p)
        self._refresh()

    def _refresh(self):
        self.clear()
        if not self._paths:
            self.addItem("（拖拽到此处或双击选择）")
        for p in self._paths:
            self.addItem(p)
        if self.on_change:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001
                pass

    def add_path(self, p: str):
        if p and p not in self._paths:
            self._paths.append(p)
            self._refresh()

    def mouseDoubleClickEvent(self, e):
        self.browse()

    # ---- 浏览对话框（2026-10-17 修复：照片必须能通过文件框选中）----
    def browse(self):
        """按 ``browse_mode`` 打开对话框：files=多选文件，dirs=选目录。"""
        if self.browse_mode == "dirs":
            self.browse_dir()
            return
        start = ""
        if self._paths:
            first = self._paths[-1]
            start = first if os.path.isdir(first) else os.path.dirname(first)
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择文件", start,
            self.name_filter or "所有文件 (*.*)")
        if paths:
            self._add_many(paths)

    def browse_dir(self):
        """显式选择文件夹（照片按目录导入时使用）。"""
        start = self._paths[-1] if self._paths else ""
        p = QFileDialog.getExistingDirectory(self, "选择文件夹", start)
        if p:
            self._add_many([p])

    # 兼容旧调用名
    def _browse(self):
        self.browse()

    def paths(self) -> list:
        return list(self._paths)


class WizardWindow(QWidget):
    """三步向导：场景 → 数据导入 → 参数确认。完成时回调 on_done(cfg)。"""

    def __init__(self, on_done, parent=None):
        super().__init__(parent)
        self.on_done = on_done
        self.cfg = ProjectConfig()
        self.setWindowTitle("H3D 中小型文物三维建模自动化系统 - 新建项目向导")
        self.resize(680, 540)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        self.stack = QVBoxLayout()
        layout.addLayout(self.stack)
        # 底部按钮
        btns = QHBoxLayout()
        self.btn_prev = QPushButton("上一步")
        self.btn_next = QPushButton("下一步")
        self.btn_prev.setEnabled(False)
        self.btn_next.clicked.connect(self._next)
        self.btn_prev.clicked.connect(self._prev)
        btns.addStretch()
        btns.addWidget(self.btn_prev)
        btns.addWidget(self.btn_next)
        layout.addLayout(btns)
        self._step = 0
        self._build_step1()
        self._build_step2()
        self._build_step3()
        # 实机修复：三个步骤页必须挂进 stack 布局，否则它们是**独立顶层窗口**
        # （parent=None），主窗口内容区全白——必须先点「下一步」再点「上一步」
        # 才会因为窗口重新布局而"意外"显示出来。
        for w in (self.step1, self.step2, self.step3):
            self.stack.addWidget(w)
        self._show_step()

    # ---- 第一步：场景选择 ----
    def _build_step1(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<h3>第一步：选择重建场景</h3>"))
        self.radio1 = QRadioButton("场景①：激光几何 + 照片纹理（推荐）")
        self.radio2 = QRadioButton("场景②：纯照片三维重建（自动稠密重建）")
        self.radio1.setChecked(True)
        g = QButtonGroup(self)
        g.addButton(self.radio1)
        g.addButton(self.radio2)
        v.addWidget(self.radio1)
        v.addWidget(QLabel("　　高精度激光点云做几何，独立照片烘焙纹理——文保标准流程"))
        v.addWidget(self.radio2)
        v.addWidget(QLabel("　　仅输入照片，自动稀疏+稠密重建，无激光设备的快速建档"))
        v.addStretch()
        self.step1 = w

    # ---- 第二步：数据导入（拖拽） ----
    def _build_step2(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<h3>第二步：导入数据 + 选择项目根目录</h3>"))
        v.addWidget(QLabel(
            "　① 拖拽数据到对应区域；② 确认项目根目录（重建产物输出位置，"
            "将自动建立 00_raw/00_photo/00_ctrl 规范布局）"))
        f = QFormLayout()
        self.drop_laser = DropArea(
            "激光点云（.ply/.pcd/.obj，必填；双击选择文件或直接拖入）",
            accept_dirs=False, name_filter="点云/网格 (*.ply *.pcd *.obj);;所有文件 (*.*)",
            browse_mode="files")
        self.drop_laser.setEnabled(False)
        self.drop_photo = DropArea(
            "照片（必填，支持 .jpg/.jpeg/.jpe/.jfif/.png/.tif/.tiff；"
            "双击可多选照片，也可拖入文件夹）",
            on_change=self._auto_root_from_photo, collapse_to_dir=True,
            name_filter=image_dialog_filter(), browse_mode="files")
        self.drop_ctrl = DropArea(
            "控制点 control_points.json（选填，不填自动 ICP）",
            accept_dirs=False, name_filter="JSON (*.json);;所有文件 (*.*)",
            browse_mode="files")
        f.addRow("激光点云", self.drop_laser)
        # 照片行：拖拽区 + 两个显式按钮，消除「双击只能选目录」的歧义
        photo_box = QWidget()
        pv = QVBoxLayout(photo_box)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(4)
        pv.addWidget(self.drop_photo)
        prow = QHBoxLayout()
        prow.setContentsMargins(0, 0, 0, 0)
        self.btn_pick_photos = QPushButton("选择照片…")
        self.btn_pick_photos.setToolTip(
            "打开文件选择框，可按住 Ctrl/Shift 多选照片（按扩展名过滤）")
        self.btn_pick_photos.clicked.connect(self.drop_photo.browse)
        self.btn_pick_dir = QPushButton("选择文件夹…")
        self.btn_pick_dir.setToolTip("整目录导入：选择包含全部照片的文件夹")
        self.btn_pick_dir.clicked.connect(self.drop_photo.browse_dir)
        prow.addWidget(self.btn_pick_photos)
        prow.addWidget(self.btn_pick_dir)
        prow.addStretch()
        pv.addLayout(prow)
        f.addRow("照片", photo_box)
        f.addRow("控制点(选填)", self.drop_ctrl)
        v.addLayout(f)
        # 项目根目录行（关键：修复无法下一步——root_dir 必须在第二步确认）
        root_row = QHBoxLayout()
        root_row.addWidget(QLabel("项目根目录"))
        self.edit_root = QLineEdit()
        self.edit_root.setPlaceholderText(
            "照片文件夹的上一级将自动填入；也可手动选择")
        self.edit_root.setReadOnly(True)
        btn_root = QPushButton("选择…")
        btn_root.clicked.connect(self._browse_root)
        root_row.addWidget(self.edit_root, 1)
        root_row.addWidget(btn_root)
        v.addLayout(root_row)
        self.lbl_root_hint = QLabel()
        self.lbl_root_hint.setStyleSheet("color:#d89000;")
        v.addWidget(self.lbl_root_hint)
        v.addStretch()
        self.step2 = w

    def _auto_root_from_photo(self):
        """照片位置变化后自动推断项目根目录（口径见 infer_root_from_photo）。"""
        photos = self.drop_photo.paths()
        if not photos:
            return
        root = infer_root_from_photo(photos[0])
        if self.cfg.root_dir:
            root = self.cfg.root_dir  # 保留用户手选
        self.edit_root.setText(root)

    def _browse_root(self):
        d = QFileDialog.getExistingDirectory(self, "选择项目根目录",
                                             self.edit_root.text() or "")
        if d:
            self.edit_root.setText(d)

    # ---- 第三步：参数确认 ----
    def _build_step3(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<h3>第三步：参数确认（极简模式，高级设置可后续微调）</h3>"))
        f = QFormLayout()
        self.edit_name = QLineEdit("relic_001")
        f.addRow("项目名称", self.edit_name)
        self.edit_tex = QComboBox()
        self.edit_tex.addItems(["1024", "2048", "4096"])
        self.edit_tex.setCurrentText("2048")
        f.addRow("纹理分辨率", self.edit_tex)
        self.chk_obj = QCheckBox("OBJ"); self.chk_obj.setChecked(True)
        self.chk_glb = QCheckBox("GLB"); self.chk_glb.setChecked(True)
        self.chk_ply = QCheckBox("PLY")
        fmt = QHBoxLayout(); fmt.addWidget(self.chk_obj)
        fmt.addWidget(self.chk_glb); fmt.addWidget(self.chk_ply)
        f.addRow("输出格式", fmt)
        v.addLayout(f)
        v.addWidget(QLabel("　✅ 高级设置（降采样/网格精度/融合权重等）将在主界面折叠面板提供"))
        v.addStretch()
        self.btn_finish = QPushButton("创建并开始重建")
        self.btn_finish.clicked.connect(self._finish)
        v.addWidget(self.btn_finish)
        self.step3 = w

    # ---- 步骤切换 ----
    def _show_step(self):
        for i in range(3):
            w = [self.step1, self.step2, self.step3][i]
            w.setVisible(i == self._step)
        self.btn_prev.setEnabled(self._step > 0)
        self.btn_next.setText("创建并开始重建" if self._step == 2 else "下一步")
        self.btn_next.setVisible(self._step < 2)
        self.btn_finish.setVisible(self._step == 2)

    def _next(self):
        if self._step == 0:
            self.cfg.scene = "scene2" if self.radio2.isChecked() else "scene1"
            self.drop_laser.setEnabled(self.cfg.scene == "scene1")
            self._step = 1
        elif self._step == 1:
            self._collect_step2()
            # 项目根目录（第二步已确认）→ 回写 cfg
            rt = self.edit_root.text().strip()
            if rt:
                self.cfg.root_dir = rt
            errs = self.cfg.validate()
            if errs:
                QMessageBox.warning(self, "数据不完整",
                                    "\n".join(errs))
                return
            self._step = 2
        self._show_step()

    def _prev(self):
        self._step = max(0, self._step - 1)
        self._show_step()

    def _collect_step2(self):
        if self.cfg.scene == "scene1" and self.drop_laser.paths():
            self.cfg.laser_ply = self.drop_laser.paths()[0]
        if self.drop_photo.paths():
            self.cfg.photo_dir = self.drop_photo.paths()[0]
        if self.drop_ctrl.paths():
            self.cfg.ctrl_json = self.drop_ctrl.paths()[0]

    def _finish(self):
        self.cfg.name = self.edit_name.text().strip() or "relic_001"
        self.cfg.item_id = self.cfg.name
        self.cfg.texture_size = int(self.edit_tex.currentText())
        self.cfg.export_formats = [f for f, c in
                                   (("obj", self.chk_obj),
                                    ("glb", self.chk_glb),
                                    ("ply", self.chk_ply)) if c.isChecked()]
        # 项目根目录：优先保留第二步确认值；未设时按统一口径兜底
        if not self.cfg.root_dir and self.cfg.photo_dir:
            self.cfg.root_dir = infer_root_from_photo(self.cfg.photo_dir)
        self.cfg.dense = self.cfg.scene == "scene2"
        errs = self.cfg.validate()
        if errs:
            QMessageBox.warning(self, "参数不完整", "\n".join(errs))
            return
        self.on_done(self.cfg)


__all__ = ["WizardWindow", "DropArea"]
