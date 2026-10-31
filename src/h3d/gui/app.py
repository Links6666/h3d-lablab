"""h3d.gui.app : 主窗口（功能配置区 + 预览区 + 一键生成 + 进度/状态灯/日志）。

进度表达（2026-10-17 实机优化）
-------------------------------
用户反馈"生成页面卡住"：稠密重建单步 50 分钟，界面上只有一根进度条和 9 个
阶段灯，而阶段灯**只在完成时变色**——期间没有任何活动迹象。本次补齐：

1. 阶段灯置「运行中」（橙），靠 ``▶ S1 开始`` 日志驱动；
2. 状态行实时显示「正在做什么 + 已运行多久 + 深度图 12/30」；
3. 日志按语义着色，关键加载/进度行加粗突出；
4. 关闭窗口时若仍在运行，先确认再退出（否则会连带杀死共享解包目录，
   导致子进程在最后一步 import open3d 时崩溃）。
"""

from __future__ import annotations

import html
import os
import shutil
import sys

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMainWindow, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QSplitter, QTabWidget, QVBoxLayout,
                               QWidget)

from h3d.core import paths as _paths
from h3d.core.media import is_image_file
from h3d.gui.config_edit import ProjectConfig
from h3d.gui.constraints import (ConstraintSummary, ConstraintsDialog,
                                 apply_to_project_config,
                                 from_project_config)
from h3d.gui.export import export_delivery_package
from h3d.gui.items import discover_items, primary_item
from h3d.gui.runner import STAGE_ORDER, BatchRunner
from h3d.gui.timing import StageTimingStrip, fmt_duration
from h3d.gui.widgets import ElidedLabel
from h3d.gui.wizard import WizardWindow


#: 降采样体素预设：显示名 → 米制基准值。
#:
#: 这里的米是**基准器物**（包围盒对角线 3.7 m）上的取值；开启尺度自适应后，
#: 内核按「实际对角线 / 3.7」等比缩放。2026-10-18：界面原来只写 "低(0.05)"，
#: 让做 5~30 cm 小件的用户以为自己要选「5 cm 体素」——那是器物尺寸的一半。
VOXEL_PRESETS: tuple[tuple[str, float], ...] = (
    ("粗（基准 5 cm）", 0.05),
    ("中（基准 2 cm）", 0.02),
    ("细（基准 1 cm）", 0.01),
)


class MainWindow(QMainWindow):
    """主界面：左侧配置区 + 右侧预览区 + 底部进度/状态灯/日志。"""

    def __init__(self, cfg: ProjectConfig | None = None,
                 root_dir: str | None = None, name: str = "h3d_project",
                 scene: str = "scene1"):
        super().__init__()
        if cfg is None:
            cfg = ProjectConfig()
            if root_dir:
                cfg.root_dir = root_dir
            cfg.name = name
            cfg.scene = scene
            cfg.item_id = name
        self.cfg = cfg
        self.runner: BatchRunner | None = None
        #: 正在走「启动流程」（含自检弹窗）时的重入闸门
        self._starting = False
        self.setWindowTitle("H3D 中小型文物三维建模自动化系统")
        self._build()
        self._migrate_layout_if_needed()
        # 打开项目 = 建档（幂等）。放在布局迁移之后：迁移会改目录形态，
        # 先迁再建档才能判断出正确的项目根。
        self._ensure_archive()
        self._fit_to_screen()
        # 打开已有项目时立刻显示上次的成果（旧实现只在运行结束后才载入，
        # 打开一个跑完的项目预览永远空白）。
        try:
            if self.cfg.root_dir and os.path.isdir(self.cfg.root_dir):
                self._refresh_preview(ok=True, show_placeholder=False)
        except Exception as exc:  # noqa: BLE001 - 打开项目不能因预览失败中断
            self._log(f"[界面] 初始预览加载失败（不影响运行）：{exc}")

    def _migrate_layout_if_needed(self) -> bool:
        """打开历史项目时把旧布局就地迁到「综合文件夹 + 两棵子树」。

        为什么要在 GUI 里做而不是 CLI 里强制：命令行用户可能用脚本按旧路径
        取产物（``80_export/...``），强制改名会直接打断他们的流程。而 GUI 用户
        是"打开项目继续干活"，迁移对他只有好处（文件还在原位、只是归了类）。

        迁移失败不影响打开：捕获异常写日志，按旧布局继续用。
        """
        root = self.cfg.root_dir
        if not root or not os.path.isdir(root):
            return False
        try:
            legacy = _paths.legacy_dirs_present(root)
            if not legacy:
                return False
            self._log("▶ 检测到旧版目录布局（%s），正在迁移到「阶段成果 / 交付成果」…"
                      % "、".join(legacy[:6]))
            res = _paths.migrate_legacy(root)
            if res.get("refused"):
                self._log(f"[WARNING] 未迁移：{res['refused']}")
                return False
            for src, dst in res.get("moved", []):
                self._log(f"    迁移：{os.path.basename(src)} → {dst}")
            for name, why in res.get("skipped", []):
                self._log(f"    [WARNING] 跳过 {name}：{why}")
            self._log("▶ 目录迁移完成（下一次运行按新布局写入）")
            return True
        except Exception as exc:  # noqa: BLE001 - 迁移失败按旧布局继续
            self._log(f"[WARNING] 目录迁移失败（沿用旧布局）：{exc}")
            return False

    # ------------------------------------------------------------ 档案
    def _ensure_archive(self) -> None:
        """打开项目时建档，并把时相列表刷到「文物档案」页。

        为什么接在这里而不是 runner 里：建档会往项目根写 ``archive/``，
        改变存量项目的目录形态。runner 擅自做这件事等于「跑一次管线，
        项目里多出一棵树」，而 CLI 用户可能用脚本按旧路径取产物。GUI 是
        用户已经打开项目、正在「继续干活」的场所，建档在这里是显式意图。

        失败只记日志：档案是增量能力，不能反过来让项目打不开。
        """
        root = getattr(self.cfg, "root_dir", "") or ""
        if not root:
            return
        try:
            from h3d.archive.workspace import ensure_archive

            status = ensure_archive(root)
        except Exception as exc:  # noqa: BLE001 - 建档失败不得影响界面
            self._log(f"[WARNING] 文物档案建档失败（不影响建模）：{exc}")
            return
        try:
            self.archive_panel.apply(status)
        except Exception as exc:  # noqa: BLE001 - 面板失败不影响建档结果
            self._log(f"[WARNING] 档案面板刷新失败：{exc}")
        if status.is_open():
            self._log(f"▶ {status.message}（{status.epoch_count()} 个时相）")
        elif status.message:
            self._log(f"· 档案：{status.message}")

    def _refresh_archive(self) -> None:
        """只读刷新档案页（跑完一批后调用）。不会建档。"""
        root = getattr(self.cfg, "root_dir", "") or ""
        if not root:
            return
        try:
            from h3d.archive.workspace import read_status

            self.archive_panel.apply(read_status(root))
        except Exception as exc:  # noqa: BLE001 - 展示性功能
            self._log(f"[WARNING] 档案页刷新失败：{exc}")

    # ------------------------------------------------------------ 窗口尺寸
    def _fit_to_screen(self):
        """按屏幕可用区定初值并居中，且**给最小尺寸封顶**。

        实机缺陷（2026-10-26）：``resize(1280, 800)`` 是写死的，而笔记本常见
        1366×768 / 150% 缩放后可用区只有 1280×656 —— 窗口初始就比屏幕高，
        状态行与日志被挤到屏幕外，"看不到进度"其实是**窗口放不下**。
        另外最小尺寸不封顶时，布局会反过来把窗口撑大（见 gui/widgets 说明）。
        """
        # 取屏幕：**不要**在构造期调 self.screen()。
        # 实测（2026-10-26）PySide6 6.11 下，窗口尚无 windowHandle 时
        # ``QWidget.screen()`` 会在进程堆里触发 ``0xc0000374``（堆损坏）硬崩，
        # 现场特征：单跑绿、多文件混跑必崩、无 Python traceback。改为
        # "有 windowHandle 才问它，否则直接问 QGuiApplication"。
        wh = self.windowHandle()
        screen = wh.screen() if wh is not None else QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        max_w = max(800, int(geo.width() * 0.94))
        max_h = max(600, int(geo.height() * 0.92))
        w = min(1360, max_w)
        h = min(880, max_h)
        # 最小尺寸必须 ≤ 屏幕可用区，否则用户拉不动窗口、底部永远出屏
        self.setMinimumSize(min(1000, max_w), min(620, max_h))
        self.resize(w, h)
        self.move(geo.x() + (geo.width() - w) // 2,
                  geo.y() + (geo.height() - h) // 2)

    def showEvent(self, event):  # noqa: N802 - Qt 命名
        # 首次显示时屏幕信息才可靠（多显示器/DPI 缩放在构造期未必就绪）
        if not getattr(self, "_fitted", False):
            self._fitted = True
            self._fit_to_screen()
        super().showEvent(event)

    # ------------------------------------------------------------ UI
    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)
        top = QSplitter(Qt.Horizontal)
        top.setChildrenCollapsible(False)
        # 左侧配置区放进滚动区：控件总高超过窗口时**滚动**，而不是把窗口顶大。
        # 这是"窗口自己变高"的另一半根因（高级参数面板一展开就超出屏幕高度）。
        self.config_scroll = QScrollArea()
        self.config_scroll.setWidgetResizable(True)
        self.config_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.config_scroll.setFrameShape(QFrame.NoFrame)
        self.config_scroll.setMinimumWidth(340)
        panel = self._build_config_panel()
        self.config_scroll.setWidget(panel)
        top.addWidget(self.config_scroll)
        top.addWidget(self._build_preview_panel())
        top.setStretchFactor(0, 0)
        top.setStretchFactor(1, 1)
        top.setSizes([430, 900])
        root.addWidget(top, 3)
        root.addWidget(self._build_status_panel(), 2)
        # 打开已有项目：直接把上次运行的阶段耗时摆出来（"结束后一直存续"）。
        # 失败不提示——没有 manifest 是正常的首次运行状态。
        try:
            self.stage_timing.apply_manifest(self._manifest_path())
        except Exception:  # noqa: BLE001 - 展示性功能，不得影响启动
            pass

    def _build_config_panel(self) -> QWidget:
        """左侧配置区：输入参数（运行期隐藏）→ 约束摘要（运行期显示）→ 操作按钮。

        两层结构是「确认后只读」的落地方式：``inputs_host`` 里全是输入控件，
        运行一开始就整块隐藏、换成 :class:`ConstraintSummary` 的只读展示。
        不用 ``setEnabled(False)`` 灰掉，是因为灰掉的控件仍然占位、仍然可以
        被误读成"能改只是暂时不能点"；直接换成只读卡片，语义没有歧义。
        """
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(6)
        v.addWidget(self._section_title("功能配置"))

        self.inputs_host = QWidget()
        ih = QVBoxLayout(self.inputs_host)
        ih.setContentsMargins(0, 0, 0, 0)
        ih.setSpacing(6)

        # ---- 几何重建
        ih.addWidget(self._section_title("几何重建设置"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        grid.addWidget(QLabel("降采样强度"), 0, 0)
        self.cb_voxel = QComboBox()
        self.cb_voxel.addItems([n for n, _ in VOXEL_PRESETS])
        # 按项目已存的体素值选中最近一档（原实现硬编码「中档」，打开历史
        # 项目后控件显示与实际配置不一致，用户改别的参数时会顺手把体素改回中档）
        self.cb_voxel.setCurrentText(min(
            VOXEL_PRESETS, key=lambda kv: abs(kv[1] - float(self.cfg.voxel)))[0])
        self.cb_voxel.setToolTip(
            "降采样体素。数值是**体素在基准器物（对角线 3.7 m）上的取值**；\n"
            "开启「按几何尺度自动调整参数」后，内核按实际器物尺寸等比缩放：\n"
            "10 cm 小件的中档约 0.2~0.7 mm，米级器物约 2 cm。\n"
            "若缩放后小于扫描点间距，降采样不会真正减点（日志会 WARNING 提示），\n"
            "此时把档位调粗一档即可。")
        grid.addWidget(self.cb_voxel, 0, 1)
        grid.addWidget(QLabel("网格精度"), 1, 0)
        self.cb_depth = QComboBox()
        self.cb_depth.addItems(["标准(7)", "精细(8)", "超精细(9)"])
        self.cb_depth.setCurrentText({7: "标准(7)", 8: "精细(8)",
                                      9: "超精细(9)"}.get(
            int(self.cfg.poisson_depth), "精细(8)"))
        grid.addWidget(self.cb_depth, 1, 1)
        grid.addWidget(QLabel("稠密重建"), 2, 0)
        self.cb_dense = QComboBox()
        self.cb_dense.addItems(["关闭", "开启"])
        self.cb_dense.setCurrentText(
            "开启" if self.cfg.dense or self.cfg.scene == "scene2" else "关闭")
        grid.addWidget(self.cb_dense, 2, 1)
        grid.setColumnStretch(1, 1)
        ih.addLayout(grid)

        # ---- 纹理
        ih.addWidget(self._section_title("纹理烘焙设置"))
        grid2 = QGridLayout()
        grid2.setHorizontalSpacing(6)
        grid2.setVerticalSpacing(4)
        grid2.addWidget(QLabel("纹理分辨率"), 0, 0)
        self.cb_tex = QComboBox()
        self.cb_tex.addItems(["1024", "2048", "4096"])
        # 非标尺寸（如 1536）无法用下拉框表达，落到 2048 而不是静默变成 1024
        _tex = str(self.cfg.texture_size)
        self.cb_tex.setCurrentText(_tex if _tex in ("1024", "2048", "4096")
                                   else "2048")
        grid2.addWidget(self.cb_tex, 0, 1)
        grid2.addWidget(QLabel("接缝平滑"), 1, 0)
        self.cb_blend = QComboBox()
        self.cb_blend.addItems(["开启", "关闭"])
        # 这一项此前是纯装饰：既不读配置也不写配置（2026-10-25 修）
        self.cb_blend.setCurrentText("开启" if self.cfg.poisson_blend else "关闭")
        self.cb_blend.setToolTip("泊松接缝融合 photo_texture.poisson_blend。\n"
                                "关闭后图集接缝会留下可见拼接线，仅用于排查用。")
        grid2.addWidget(self.cb_blend, 1, 1)
        grid2.setColumnStretch(1, 1)
        ih.addLayout(grid2)

        # ---- 质检
        ih.addWidget(self._section_title("质检与评估"))
        grid3 = QGridLayout()
        grid3.setHorizontalSpacing(6)
        grid3.setVerticalSpacing(4)
        grid3.addWidget(QLabel("几何质检"), 0, 0)
        self.cb_qc = QComboBox(); self.cb_qc.addItems(["开启", "关闭"])
        self.cb_qc.setCurrentText("开启" if self.cfg.quality_enabled else "关闭")
        grid3.addWidget(self.cb_qc, 0, 1)
        grid3.addWidget(QLabel("CUDA 加速"), 1, 0)
        self.cb_cuda = QComboBox()
        self.cb_cuda.addItems(["自动检测", "强制 CPU"])
        # 后端没有对应开关：COLMAP 自己按编译选项与 GPU 可用性决定，
        # 界面上给个能选的框会让人以为「选了就生效」（假控件）。
        self.cb_cuda.setEnabled(False)
        self.cb_cuda.setToolTip(
            "COLMAP 启动时自动检测 CUDA（当前版本无强制 CPU 开关）。\n"
            "要强制 CPU 请直接改 config.yaml 的 sfm_solve 段后命令行运行。")
        grid3.addWidget(self.cb_cuda, 1, 1)
        grid3.setColumnStretch(1, 1)
        ih.addLayout(grid3)

        ih.addWidget(ElidedLabel(
            "项目：%s　场景：%s" % (
                self.cfg.name, "①激光+照片" if self.cfg.scene == "scene1"
                else "②纯照片"), min_chars=24))
        # 高级参数折叠面板（P1）
        from h3d.gui.advanced import AdvancedPanel
        self.advanced = AdvancedPanel(self.cfg)
        ih.addWidget(self.advanced)
        # 强制重算（2026-10-26）：此前想重算只能自己去删 10_dense/30_dense，
        # 路径写错还会留下半残项目。勾选后忽略断点续跑、全部阶段重跑，
        # 并让 S1 重建稠密工作区（位姿变了但深度图残留是典型场景）。
        self.chk_force = QCheckBox("强制重算（忽略断点续跑）")
        self.chk_force.setChecked(bool(getattr(self.cfg, "force_rerun", False)))
        self.chk_force.setToolTip(
            "勾选后：不再跳过「已完成」的阶段，M1~M6（含 S1）全部重跑，\n"
            "S1 的稠密工作区（含深度图）一并重建。\n\n"
            "什么时候需要：① 改过相机位姿或稠密参数后怀疑残留；\n"
            "② 手动改过中间产物；③ 要复现一次完整耗时。\n"
            "什么时候不必：只是想重出报表/重贴图——那属于缓存命中，不花时间。\n\n"
            "注意：S1 照片指纹复用与 M4 图集缓存按参数判定，参数没变仍会命中\n"
            "（否则每次强制重算都要白等 30 分钟）。")
        ih.addWidget(self.chk_force)
        v.addWidget(self.inputs_host)

        # ---- 运行期只读约束（默认隐藏，开跑后替换 inputs_host）
        self.constraint_box = ConstraintSummary()
        v.addWidget(self.constraint_box)

        # ---- 操作按钮（始终可用，不受 inputs_host 隐藏影响）
        self.btn_run = QPushButton("一键生成交付物")
        self.btn_run.setStyleSheet(
            "QPushButton{background:#2d7d46;color:white;font-weight:bold;"
            "padding:10px;font-size:15px;border-radius:6px;}"
            "QPushButton:disabled{background:#666;}")
        self.btn_run.clicked.connect(self._start_run)
        v.addWidget(self.btn_run)
        self.btn_edit_constraints = QPushButton("修改生成约束…")
        self.btn_edit_constraints.setToolTip(
            "重新打开「确认本次生成约束」对话框。运行期间不可用。")
        self.btn_edit_constraints.clicked.connect(self._edit_constraints)
        v.addWidget(self.btn_edit_constraints)
        self.btn_cancel = QPushButton("暂停 / 取消")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_run)
        v.addWidget(self.btn_cancel)
        self.btn_export = QPushButton("导出完整交付包")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._export_package)
        v.addWidget(self.btn_export)
        v.addStretch()
        return w

    @staticmethod
    def _section_title(text: str) -> QLabel:
        lb = QLabel(text)
        lb.setStyleSheet("QLabel{color:#8fb3d9;font-weight:bold;font-size:12px;"
                         "padding-top:2px;}")
        return lb

    def _build_preview_panel(self) -> QWidget:
        # matplotlib 后端延迟导入：仅在进入主界面时加载（桌面环境隔离）
        from h3d.gui.preview import MeshPreview, TextureView
        tabs = QTabWidget()
        self.mesh_preview = MeshPreview()
        self.tex_view = TextureView()
        # 注意：这里**不能**先把 mesh_preview 加成一个独立 tab——下面的
        # wrap 会重新 addWidget 同一个对象（Qt 会把它从上一个父容器移走），
        # 结果是第一个「3D 模型预览」标签页变成永远空白的死页（旧实现如此）。
        self.btn_open3d = QPushButton("打开 open3d 交互窗口（真 3D）")
        self.btn_open3d.setEnabled(False)
        self.btn_open3d.clicked.connect(self._open3d_window)
        self.btn_x1 = QPushButton("X1 形变热力图")
        self.btn_x1.setEnabled(False)
        self.btn_x1.clicked.connect(self._show_x1)
        bar = QHBoxLayout()
        bar.addWidget(self.btn_open3d)
        bar.addWidget(self.btn_x1)
        wrap = QWidget()
        wv = QVBoxLayout(wrap)
        wv.addLayout(bar)
        wv.addWidget(self.mesh_preview, 1)
        tabs.addTab(wrap, "3D 模型预览")
        # 文物档案页（B0 切片 6）：时相 / 路线 / 基准期 / 产物版本。
        # 放在预览旁边而不是配置区里——它是「看结果」的一类，不是输入。
        from h3d.gui.archive_view import ArchivePanel

        self.archive_panel = ArchivePanel()
        tabs.addTab(self.archive_panel, "文物档案")
        self.tabs = tabs
        return tabs

    def _build_status_panel(self) -> QWidget:
        """底部状态区：总进度 → 状态行（省略标签）→ 阶段耗时条 → 阶段灯 → 结果条 → 日志。

        排版原则（2026-10-26 实机）：**所有会变的文字都不能撑宽窗口**。
        状态行用 :class:`ElidedLabel`（最小宽度 0 + 全文 tooltip），进度条只显示
        百分比（``setFormat("%p%")``——``QProgressBar`` 的格式串也会算进
        sizeHint，原来写 ``"42% · 第 1/2 件 · S1"`` 就是窗口跳宽的元凶之一）。
        件数与当前阶段改放在进度条右侧的定宽标签里。
        """
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(3)

        # 总进度
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("总进度"))
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setFormat("%p%")          # 只显示百分比，不塞长文本
        self.progress.setMinimumWidth(160)
        self.progress.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row.addWidget(self.progress, 1)
        self.lbl_count = ElidedLabel("", min_chars=10)
        self.lbl_count.setMinimumWidth(0)
        self.lbl_count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_count.setStyleSheet("QLabel{color:#9aa4b2;font-size:11px;}")
        row.addWidget(self.lbl_count)
        v.addLayout(row)

        # 实时状态行：正在做什么 | 已运行多久 + 本阶段
        state_row = QHBoxLayout()
        state_row.setSpacing(8)
        self.lbl_state = ElidedLabel("● 空闲", min_chars=16)
        self.lbl_state.setStyleSheet(
            "QLabel{color:#9aa4b2;font-weight:bold;font-size:13px;}")
        state_row.addWidget(self.lbl_state, 3)
        self.lbl_elapsed = ElidedLabel("", min_chars=14)
        self.lbl_elapsed.setStyleSheet("QLabel{color:#9aa4b2;}")
        self.lbl_elapsed.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        state_row.addWidget(self.lbl_elapsed, 2)
        v.addLayout(state_row)

        # 阶段耗时条（常驻：运行中与结束后都在，见 gui/timing）
        self.stage_timing = StageTimingStrip(STAGE_ORDER)
        v.addWidget(self.stage_timing)

        # 阶段状态灯：5 列 × 2 行。原来 10 个灯横排，1366 像素宽的屏上每个
        # 只剩 40 px，代号看得见、状态看不清；分两行后间距正常且不影响高度稳定
        # （灯是 Fixed 高度，文字固定为阶段代号）。
        lamps = QGridLayout()
        lamps.setHorizontalSpacing(4)
        lamps.setVerticalSpacing(3)
        self.lamps: dict[str, QLabel] = {}
        per_row = 5
        for i, sid in enumerate(STAGE_ORDER):
            lb = QLabel(sid)
            lb.setAlignment(Qt.AlignCenter)
            lb.setFixedHeight(22)
            lb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            lb.setStyleSheet(_lamp_style("waiting"))
            self.lamps[sid] = lb
            lamps.addWidget(lb, i // per_row, i % per_row)
        for col in range(per_row):
            lamps.setColumnStretch(col, 1)
        self.lamps_grid = lamps          # 供测试断言排布（也便于将来调列数）
        v.addLayout(lamps)

        # 结果条（2026-10-26）：跑完不再弹模态框。
        # 模态框的问题不是"丑"，而是**必须点掉才能继续操作**：用户看到
        # 「完成」后的下一个动作通常是去看模型/导出，而弹窗挡在前面；
        # 更糟的是失败弹窗，点掉之后想「打开报表」还得自己去文件管理器里找。
        # 结果条把「结论 + 后续动作」放在界面里常驻，直到用户主动关闭。
        v.addWidget(self._build_result_bar())

        # 实时日志
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        self.log.setMinimumHeight(110)
        self.log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        v.addWidget(self.log, 1)

        # 运行时钟：每秒刷新"已运行"，每 3 秒扫一次深度图产物（显示真实进度）
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._on_tick)
        self._t0: float | None = None
        self._cur_stage = ""
        self._cur_substep = ""
        self._depth_seen = (0, 0)
        #: 当前正在处理的文物编号（进度页据此定位产物目录，见 _depth_progress）
        self._cur_item = ""
        #: 是否已进入 S1 稠密阶段（深度图计数只在此阶段有意义）
        self._in_dense = False
        #: 稠密相位：1 = photometric（第一遍），2 = geometric（几何校验）
        self._depth_phase = 1
        #: 最后一条子进程日志的时间戳（用于"最后输出 Ns 前"心跳）
        self._last_log_ts = 0.0
        #: 阶段墙钟计时：sid -> 本次进入运行态的时间戳
        self._stage_t0: dict[str, float] = {}
        #: 本批文物总数与「已出现过的文物顺序」（进度条显示 第 k/N 件）
        self._n_items = 0
        self._items_seen: list[str] = []
        return w

    def _build_result_bar(self) -> QWidget:
        """结果条：结论文字 + 三个后续动作，默认隐藏。"""
        bar = QFrame()
        bar.setObjectName("resultBar")
        bar.setFrameShape(QFrame.StyledPanel)
        bar.setStyleSheet(
            "QFrame#resultBar{border-radius:6px;padding:8px 12px;"
            "background:#1f2a37;border:1px solid #33415a;}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 6, 10, 6)
        # 用 ElidedLabel 而非 QLabel+wordWrap：长结论（含失败原因首行）会把
        # 结果条撑高/撑宽，进而牵动整个底部布局。全文挂在 tooltip 上。
        self.lbl_result = ElidedLabel("", min_chars=30)
        self.lbl_result.setStyleSheet("QLabel{font-size:13px;}")
        h.addWidget(self.lbl_result, 1)
        self.btn_result_open = QPushButton("打开输出目录")
        self.btn_result_open.clicked.connect(self._open_output_dir)
        self.btn_result_report = QPushButton("查看报表")
        self.btn_result_report.clicked.connect(self._open_report)
        self.btn_result_rerun = QPushButton("重跑")
        self.btn_result_rerun.clicked.connect(self._start_run)
        self.btn_result_close = QPushButton("关闭")
        self.btn_result_close.clicked.connect(lambda: bar.setVisible(False))
        for b in (self.btn_result_open, self.btn_result_report,
                  self.btn_result_rerun, self.btn_result_close):
            b.setStyleSheet("QPushButton{padding:4px 10px;border-radius:4px;}")
            h.addWidget(b)
        self.result_bar = bar
        bar.setVisible(False)
        return bar

    def _show_result(self, ok: bool, summary: str,
                     skipped_only: bool = False) -> None:
        """在界面里展示运行结论（替代模态弹窗）。

        三态配色：完成=绿 / 无需重跑=蓝灰 / 未完成=红。失败时**额外**弹一次
        模态框——那确实需要用户确认（结果与预期不符，不看会继续误操作）。
        """
        if ok and skipped_only:
            color, title = "#8fb3d9", "已是最新（无需重跑）"
        elif ok:
            color, title = "#3fb950", "生成完成"
        else:
            color, title = "#ff6b6b", "运行未完成"
        # ElidedLabel 走纯文本绘制（不支持富文本），结论用 "标题｜摘要" 表达
        self.lbl_result.setText(f"{title}　{summary}")
        self.lbl_result.setStyleSheet(
            f"QLabel{{font-size:13px;color:{color};}}")
        # 只暴露**当前真的存在**的动作：没有产物时「打开输出目录」点了也是空的
        # 「有产物」= 交付目录里**真有东西**，而不是"目录存在"。
        # 判据分新老布局取不同的根，但都用 any(scandir) —— 空目录（上一次运行
        # 中途失败留下的）不该让按钮亮起来。
        root_dir = self.cfg.root_dir
        droot = _paths.delivery_root_of(root_dir)
        if os.path.basename(droot) == _paths.DELIVERY_DIR:
            cands = [droot]
        else:                       # 旧布局：交付根就是项目根，只能看 80_export
            cands = [os.path.join(root_dir, _paths.LEGACY_EXPORT_DIR)]
        has_out = False
        for d in cands:
            try:
                if os.path.isdir(d) and any(os.scandir(d)):
                    has_out = True
                    break
            except OSError:
                continue
        has_out = has_out or os.path.isdir(
            os.path.join(_paths.stage_root(root_dir), "50_tex"))
        self.btn_result_open.setEnabled(has_out)
        self.btn_result_open.setToolTip(
            "" if has_out else "本次运行未生成产物目录（交付成果 / 阶段成果）")
        self.btn_result_report.setEnabled(
            bool(_paths.find_report(self.cfg.root_dir)))
        self.result_bar.setVisible(True)

    def _open_output_dir(self) -> None:
        """打开本次运行的输出根（优先 交付成果，没有就退回项目根）。"""
        for cand in (_paths.delivery_root_of(self.cfg.root_dir),
                     _paths.report_dir(self.cfg.root_dir),
                     _paths.stage_root(self.cfg.root_dir),
                     self.cfg.root_dir):
            if cand and os.path.isdir(cand):
                QDesktopServices.openUrl(QUrl.fromLocalFile(cand))
                return
        self._log("[界面] 未找到可打开的输出目录")

    def _open_report(self) -> None:
        """用系统默认程序打开 report.html（新老布局都找）。"""
        path = _paths.find_report(self.cfg.root_dir)
        if not path:
            self._log(f"[界面] 报表不存在：{_paths.report_html(self.cfg.root_dir)}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _busy(self) -> bool:
        """是否正有一次运行在进行（按钮状态之外的**第二道**闸门）。

        实机缺陷（2026-10-25）：运行完成后按钮恢复可用，用户误点第二次
        「一键生成交付物」→ ``self.runner`` 被新对象覆盖，旧 QThread 引用
        归零被 GC → ``QThread: Destroyed while thread is still running``
        （Qt 直接 abort，界面上就是一句崩溃报错）。按钮禁用只是 UI 约定，
        函数入口必须自己判，不能依赖调用顺序。
        """
        return self.runner is not None and self.runner.isRunning()

    def _start_run(self):
        if self._busy():
            self._log("[界面] 已有运行在进行中，已忽略本次点击")
            QMessageBox.information(self, "正在运行",
                                    "当前已有一次生成在进行中。\n"
                                    "如需重跑，请先点「暂停 / 取消」等待本次结束。")
            return
        if getattr(self, "_starting", False):
            return                     # 自检弹窗期间重复点击
        self._starting = True
        try:
            self._start_run_inner()
        finally:
            self._starting = False

    # ------------------------------------------------------------ 约束确认
    def _n_photos(self) -> int:
        """照片张数（用于耗时估算；读不到就返回 0）。"""
        try:
            from h3d.core.media import count_images
            if self.cfg.photo_dir and os.path.isdir(self.cfg.photo_dir):
                return int(count_images(self.cfg.photo_dir))
        except Exception:  # noqa: BLE001 - 估算用，失败不影响启动
            pass
        return 0

    def _confirm_constraints(self, force_dialog: bool = False) -> bool:
        """确保本次生成有**用户确认过**的约束。返回 False 表示用户放弃。

        两道闸门：
        1. 配置里已有 ``constraints_confirmed``（上次确认过）→ 直接用，
           但界面上的控件要按快照同步（否则控件显示 2048、实际跑 4096）；
        2. 没有 → 弹 :class:`ConstraintsDialog`，用户点「开始生成」或
           「全部用默认值」才算确认；点「取消」则**不启动管线**。
        """
        confirmed = bool(getattr(self.cfg, "constraints_confirmed", False))
        if confirmed and not force_dialog:
            c = from_project_config(self.cfg)
            self._sync_widgets(c, include_force=False)
            self.constraint_box.set_constraints(c)
            return True

        dlg = ConstraintsDialog(from_project_config(self.cfg),
                                n_photos=self._n_photos(),
                                n_items=max(1, len(self._all_items())),
                                parent=self)
        if not dlg.exec():
            self._log("▶ 已取消：未确认生成约束，未启动管线")
            self.lbl_state.setText("● 已取消（未确认约束）")
            return False
        self._apply_constraints(dlg.constraints())
        return True

    def _edit_constraints(self) -> None:
        """「修改生成约束…」按钮：强制重开确认框（运行中不可用）。"""
        if self._busy():
            QMessageBox.information(self, "正在运行",
                                    "运行期间不能修改生成约束。\n"
                                    "如需更换参数，请先取消本次运行。")
            return
        dlg = ConstraintsDialog(from_project_config(self.cfg),
                                n_photos=self._n_photos(),
                                n_items=max(1, len(self._all_items())),
                                parent=self)
        if dlg.exec():
            self._apply_constraints(dlg.constraints())

    def _apply_constraints(self, c) -> None:
        """把确认过的约束同时写到配置与控件上（两处必须一致）。"""
        apply_to_project_config(self.cfg, c)
        self._sync_widgets(c)
        self.constraint_box.set_constraints(c)
        self._log("▶ 生成约束已确认：" + "；".join(
            f"{k}={v}" for k, v in c.summary_rows() if k != "注意"))

    def _sync_widgets(self, c, include_force: bool = True) -> None:
        """控件回显约束（不改控件信号，用 blockSignals 防递归刷新）。

        ``include_force=False`` 用于「上次已确认过约束」的重开项目路径：
        那个勾选是**一次性意图**，用户刚在界面上打勾就被快照重置回去，
        等于"点了没反应"（假控件）。对话框确认路径才同步它。
        """
        voxel_text = min(VOXEL_PRESETS,
                         key=lambda kv: abs(kv[1] - float(c.voxel)))[0]
        depth_text = {7: "标准(7)", 8: "精细(8)", 9: "超精细(9)"}.get(
            int(c.poisson_depth), "精细(8)")
        pairs = ((self.cb_voxel, voxel_text), (self.cb_depth, depth_text),
                 (self.cb_dense, "开启" if c.dense else "关闭"),
                 (self.cb_tex, str(int(c.texture_size))),
                 (self.cb_qc, "开启" if c.quality else "关闭"))
        for widget, text in pairs:
            widget.blockSignals(True)
            if widget.findText(text) >= 0:
                widget.setCurrentText(text)
            widget.blockSignals(False)
        if include_force:
            self.chk_force.blockSignals(True)
            self.chk_force.setChecked(bool(c.force_rerun))
            self.chk_force.blockSignals(False)

    def _set_running_ui(self, running: bool) -> None:
        """运行期把输入区换成只读约束卡（不是"灰掉"，是整块替换）。"""
        self.inputs_host.setVisible(not running)
        if running:
            c = from_project_config(self.cfg)
            self.constraint_box.set_constraints(c)
        else:
            self.constraint_box.setVisible(False)
        self.btn_edit_constraints.setEnabled(not running)
        if running:
            self.btn_run.setEnabled(False)
            self.btn_cancel.setEnabled(True)
        else:
            self.btn_run.setEnabled(True)
            self.btn_cancel.setEnabled(False)

    def _confirm_force_rerun(self) -> bool:
        """强制重算的二次确认。返回 False 表示用户放弃。"""
        msg = ("强制重算会忽略「已完成」的判定，把本项目的全部阶段重新跑一遍。\n\n"
               "· 纯照片项目：S1 稠密重建从零开始，单件约 20~40 分钟；\n"
               "· 场景①：M1~M6 全链重算，S1 若开启也会重建稠密工作区；\n"
               "· S1 照片指纹复用与 M4 图集缓存照常生效（参数没变仍会命中）。")
        ret = QMessageBox.question(
            self, "确认强制重算", msg,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return ret == QMessageBox.Yes

    def _start_run_inner(self):
        # 第 0 步：约束确认（不确认不跑）。必须在回写配置之前——约束决定
        # 稠密/精度/纹理/格式，回写完再改会漏掉某些字段。
        if not self._confirm_constraints():
            return
        # 由界面控件回写配置
        voxel_map = dict(VOXEL_PRESETS)
        depth_map = {"标准(7)": 7, "精细(8)": 8, "超精细(9)": 9}
        self.cfg.voxel = voxel_map[self.cb_voxel.currentText()]
        self.cfg.poisson_depth = depth_map[self.cb_depth.currentText()]
        self.cfg.dense = self.cb_dense.currentText() == "开启"
        self.cfg.texture_size = int(self.cb_tex.currentText())
        self.cfg.poisson_blend = self.cb_blend.currentText() == "开启"
        self.cfg.quality_enabled = self.cb_qc.currentText() == "开启"
        self.advanced.apply()  # 高级面板写回
        # 强制重算：勾选是**一次性意图**，写进本次配置；二次确认防误触
        # （纯照片项目一次强制重算 = 30 分钟起的 SfM，不能靠一次误点触发）。
        self.cfg.force_rerun = bool(self.chk_force.isChecked())
        if self.cfg.force_rerun and not self._confirm_force_rerun():
            self._log("▶ 已取消强制重算（未启动管线）")
            return
        # 建立目录 + 复制数据
        try:
            layout = self.cfg.build_layout()
            self._stage_inputs(layout)
            cfg_path = os.path.join(self.cfg.root_dir, "config.yaml")
            self.cfg.to_yaml(cfg_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "启动失败", str(exc))
            return
        # 不清空日志：重复点击「一键生成」时，上一轮的失败行恰恰是用户要回看
        # 的东西（原实现第一件事就是 self.log.clear()）。用分隔线切分即可，
        # 控件本身有 maximumBlockCount(3000) 限制长度。
        if self.log.toPlainText().strip():
            self._log("─" * 24 + " 新一次运行 " + "─" * 24)
        self._log(f"▶ 项目：{self.cfg.name}　根目录：{self.cfg.root_dir}")
        self._log(f"▶ 配置已写入：{cfg_path}")
        # 数据刚落位（``_stage_inputs`` 刚复制完），此时才可能识别出件号。
        # 打开项目时若还没放数据，那一次建档会被跳过 —— 这里补一次，
        # 否则这个项目永远没有档案，runner 的产物登记也永远不生效。
        self._ensure_archive()
        # 开跑前自检（第一步，早于任何阶段）：拦不住的问题在这里拦下来
        if not self._preflight_before_run(cfg_path):
            self._log("▶ 已按用户选择中止（未启动管线）")
            self.lbl_state.setText("● 已取消")
            return
        for sid in STAGE_ORDER:
            self.lamps[sid].setStyleSheet(_lamp_style("waiting"))
        self.result_bar.setVisible(False)   # 新一轮开始：上一轮结论作废
        self.btn_export.setEnabled(False)
        # 阶段耗时条清零重计；输入区换成只读约束卡（运行期不可改）
        self.stage_timing.reset()
        self._stage_t0.clear()
        self._set_running_ui(True)
        # 运行时钟
        import time as _time
        self._t0 = _time.time()
        self._cur_stage = ""
        self._cur_substep = ""
        self._depth_seen = (0, 0)
        self._cur_item = ""
        self._in_dense = False
        self._depth_phase = 1
        self._last_log_ts = 0.0
        #: 本批文物总数与「已出现过的文物顺序」（进度条显示 第 k/N 件）
        self._n_items = 0
        self._items_seen: list[str] = []
        self.lbl_state.setText("● 正在启动子进程…")
        self.lbl_elapsed.setText("已运行 00:00")
        self._tick.start()
        # 旧 runner 必须**继续被持有**：QThread 在线程仍 running 时被 GC 会
        # 让 Qt 直接 abort（实机报错来源）。运行结束且线程退出后自动清理。
        old = self.runner
        if old is not None:
            if old.isRunning():
                old.wait(3000)
            old.deleteLater()
        self.runner = BatchRunner(cfg_path)
        self.runner.stage_updated.connect(self._on_stage)
        self.runner.substep.connect(self._on_substep)
        self.runner.item_changed.connect(self._on_item)
        self.runner.items_total.connect(self._on_items_total)
        self.runner.progress.connect(self._on_progress)
        self.runner.log_line.connect(self._log)
        self.runner.finished_ok.connect(self._on_finished)
        self.runner.start()

    def _preflight_before_run(self, cfg_path: str) -> bool:
        """开跑前自检（项目作用域）。返回 ``False`` 表示用户选择中止。

        侧重「**这个项目现在能不能跑**」：数据根是否可写、输入是否为空、数据根
        是否被别的进程锁着、manifest 里 done 的阶段产物是否还在、稠密残留会不会
        导致点云错乱、照片与位姿尺寸是否同源、跨件是否同名照片……

        这些都在开跑前 0.6 秒内可查；一旦漏过去，代价是几分钟到几小时（S1 稠密
        单次 30 min 起）。所以它是「开始运行」的第一道工序，不是可选项。
        """
        try:
            from h3d.core import load as _load
            from h3d.health import format_report, run_preflight
            rep = run_preflight("project", cfg=_load(cfg_path), fix=True)
        except Exception as exc:  # noqa: BLE001 - 自检不可用不能拦住运行
            self._log(f"[自检] 不可用（已忽略）：{exc}")
            return True

        self._log("▶ " + rep.summary_line())
        tag_cn = {"blocker": "阻断", "warning": "警告", "info": "提示"}
        for row in rep.results:
            if row.severity == "ok":
                continue
            self._log(f"[自检·{tag_cn.get(row.severity, row.severity)}] "
                      f"{row.title}：{row.detail}")
            if row.fix:
                self._log(f"    已自动修复：{row.fix}")
        if rep.ok:
            return True

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("开跑前自检")
        box.setText("发现 %d 个阻断项，继续运行很可能白跑（甚至破坏已有产物）。"
                    % len(rep.blockers))
        box.setInformativeText("建议先按提示处理后重试；确实要跑可以选「仍然运行」。")
        box.setDetailedText(format_report(rep))
        cancel = box.addButton("返回修改", QMessageBox.RejectRole)
        box.addButton("仍然运行", QMessageBox.AcceptRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is not cancel

    def _stage_inputs(self, layout: dict) -> None:
        """把拖入数据复制到标准目录布局（同路径源自动跳过）。"""
        def _copy(src: str, dst: str) -> bool:
            if not src or not os.path.exists(src):
                return False
            if os.path.abspath(src) == os.path.abspath(dst):
                return False  # 已在目标目录
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            return True

        if self.cfg.scene == "scene1" and self.cfg.laser_ply:
            _copy(self.cfg.laser_ply,
                  os.path.join(layout["raw"],
                               os.path.basename(self.cfg.laser_ply)))
        if self.cfg.photo_dir and os.path.isdir(self.cfg.photo_dir):
            # 统一图像口径：含 .jfif/.jpe/.tiff 等 JPEG/TIFF 变体
            from h3d.core.media import list_images
            for src in list_images(self.cfg.photo_dir):
                _copy(src, os.path.join(layout["photo"],
                                        os.path.basename(src)))
        if self.cfg.ctrl_json and os.path.exists(self.cfg.ctrl_json):
            _copy(self.cfg.ctrl_json,
                  os.path.join(layout["ctrl"], "control_points.json"))

    def _cancel_run(self):
        if self.runner and self.runner.isRunning():
            # 按钮点一次就禁用：taskkill /T 反复执行会打到已死的 PID，
            # 用户看到的是"点了没反应"，容易以为界面卡住而反复点。
            self.btn_cancel.setEnabled(False)
            self.runner.cancel()
            self._log("■ 已请求取消……（等待子进程树退出）")

    def _on_stage(self, sid: str, status: str):
        # 未知阶段不得抛 KeyError：槽里的异常只会打给 stderr（打包版不可见），
        # 表现是"状态行与灯停在上一阶段"= 看着像卡死（2026-10-18 P1 实例）。
        lamp = self.lamps.get(sid)
        if lamp is None:
            self._log(f"[界面] 未登记的阶段 {sid}（{status}），已忽略灯光更新")
        else:
            lamp.setStyleSheet(_lamp_style(status))
        if status == "running":
            self._cur_stage = sid
            self._cur_substep = ""
            # 计时起点：同一阶段可能因多件文物被多次置 running（每件各跑一遍），
            # 上一段已经结算过就重新起表，累计值不清零。
            import time as _time
            self._stage_t0[sid] = _time.time()
            self.stage_timing.set_status(sid, "running")
            if sid == "S1":
                self._in_dense = False   # 新文物的 S1 从稀疏开始
            self.lbl_state.setText(f"● {sid} 运行中…")
            self.lbl_state.setStyleSheet(
                "QLabel{color:#f0a020;font-weight:bold;font-size:13px;}")
            self._refresh_progress_text()
        elif status == "skipped":
            # 条件不满足而真跳过（单站无 M2、无参考期无 X1…）：
            # 灰色而非橙色，且要清掉"运行中"——否则界面永远停在那一阶段。
            self._settle_stage_time(sid)
            self.stage_timing.set_status(sid, "skipped")
            if self._cur_stage == sid:
                self._cur_stage = ""
                self._cur_substep = ""
            self.lbl_state.setText(f"○ {sid} 已跳过（条件不满足）")
            self.lbl_state.setStyleSheet(
                "QLabel{color:#9aa4b2;font-weight:bold;font-size:13px;}")
        elif status == "done":
            self._settle_stage_time(sid)
            self.stage_timing.set_status(sid, "done")
            if self._cur_stage == sid:
                self._cur_stage = ""
                self._cur_substep = ""
            if sid == "S1":
                # S1 结束后深度图计数失去意义，不清会一直挂在状态行上
                self._in_dense = False
                self._depth_phase = 1
                self._depth_seen = (0, 0)
            self.lbl_state.setText(f"✔ {sid} 已完成")
            self.lbl_state.setStyleSheet(
                "QLabel{color:#3fb950;font-weight:bold;font-size:13px;}")
        elif status == "failed":
            self._settle_stage_time(sid)
            self.stage_timing.set_status(sid, "failed")
            self.lbl_state.setText(f"✘ {sid} 失败")
            self.lbl_state.setStyleSheet(
                "QLabel{color:#ff6b6b;font-weight:bold;font-size:13px;}")

    def _settle_stage_time(self, sid: str) -> None:
        """结算一个阶段的墙钟耗时并累加进耗时条。

        「结算」而不是「置为」：批处理里同一阶段会为每件文物各跑一次，累计
        才是用户问的"这个阶段一共花了多久"。没有起点（例如界面是在阶段中途
        才接到事件）时直接忽略，不写 0 也不猜。
        """
        import time as _time
        t0 = self._stage_t0.pop(sid, None)
        if t0 is None:
            return
        self.stage_timing.add_seconds(sid, max(0.0, _time.time() - t0))

    def _on_item(self, item: str):
        """切换文物：深度图计数必须归零重算，否则会显示上一个文物的进度。"""
        if item != self._cur_item:
            self._cur_item = item
            self._depth_seen = (0, 0)
            self._in_dense = False
            self._depth_phase = 1
        if item and item not in self._items_seen:
            self._items_seen.append(item)

    def _on_items_total(self, n: int):
        """本批总件数（``任务发现完成：N 件``）。进度条据此显示 第 k/N 件。"""
        self._n_items = int(n or 0)
        self._refresh_progress_text()

    def _refresh_progress_text(self):
        """件数/当前阶段进右侧**定宽**标签（不塞进 QProgressBar 的格式串）。

        ``QProgressBar.setFormat`` 的文字参与 ``sizeHint``：原来写
        ``"42% · 第 1/2 件 · S1"``，文字一变长进度条就要求更多宽度，
        布局再把顶层窗口撑大 —— 实机"窗口自己变宽"的直接来源。
        """
        parts = []
        if self._n_items:
            k = len(self._items_seen) or 1
            parts.append(f"第 {k}/{self._n_items} 件")
        if self._cur_stage:
            parts.append(self._cur_stage)
        self.lbl_count.setText(" · ".join(parts))

    def _on_progress(self, value: int):
        self.progress.setValue(value)
        self._refresh_progress_text()

    def _on_substep(self, sid: str, text: str):
        """子步骤变化（COLMAP 1/3、深度图 12/30 等）。"""
        self._cur_substep = text
        self._cur_stage = sid
        if "几何校验" in text:
            # patch_match_stereo 第二遍（geometric.bin），计数需换扩展名
            self._in_dense = True
            if self._depth_phase != 2:
                self._depth_phase = 2
                self._depth_seen = (0, 0)
        elif "深度图" in text:
            self._in_dense = True
            self._depth_phase = 1
        elif "稠密" in text or "去畸变" in text or "融合" in text:
            self._in_dense = True
        self.lbl_state.setText(f"● {sid} 运行中 · {text}")
        self.lbl_state.setStyleSheet(
            "QLabel{color:#f0a020;font-weight:bold;font-size:13px;}")

    def _on_tick(self):
        """每秒刷新计时；每 3 秒扫一次深度图产物，给出可验证的真实进度。"""
        import time as _time
        if self._t0 is None:
            return
        sec = int(_time.time() - self._t0)
        parts = [f"总 {sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"]
        # 当前阶段耗时：累计值 + 本段（两次结算之间），与耗时条上的数字同源
        if self._cur_stage:
            done = self.stage_timing.seconds(self._cur_stage)
            t0 = self._stage_t0.get(self._cur_stage)
            live = (_time.time() - t0) if t0 else 0.0
            parts.append(f"{self._cur_stage} {fmt_duration(done + live)}")
        if self._cur_stage and self._cur_substep:
            parts.append(self._cur_substep)
        # 深度图计数：只在 S1 稠密阶段显示，且只统计**当前文物**
        if self._in_dense and sec % 3 == 0:
            self._depth_seen = self._depth_progress()
        done, total = self._depth_seen
        if total and not any(k in (self._cur_substep or "")
                             for k in ("深度图", "几何校验")):
            parts.append(f"深度图 {done}/{total}")
        # 心跳：COLMAP 单张深度图约 47 秒、sparse BA 十几分钟内可能零输出。
        # 不显示"最后输出"时，界面和真卡死无法区分（实机反馈）。
        idle = int(_time.time() - self._last_log_ts) if self._last_log_ts else -1
        if idle >= 30:
            parts.append(f"最后输出 {idle}s 前（计算中）")
        self.lbl_elapsed.setText(" · ".join(parts))

    def _depth_progress(self) -> tuple[int, int]:
        """统计**当前文物**的深度图完成数（按 photometric / geometric 相位）。

        实机缺陷（2026-10-17）：原实现遍历 ``99_runs/sfm_tmp`` 下所有文物并取
        最大值。上一次运行残留的旧目录（``relic_666`` 有 30 张）会让界面在
        稀疏阶段刚起步时就显示「深度图 30/30」，一直不动 —— 用户据此判断卡死。
        现在只认 ``self._cur_item``（由 ``▶ S1 开始：<编号>`` 日志行给出）。
        """
        return depth_progress(self.cfg.root_dir, self._cur_item,
                              self._depth_phase)

    def _preview_candidates(self) -> list[str]:
        """按"成品 → 中间产物"的优先级列出可预览文件。

        原实现只查找 ``80_export``。S1/M1~M3 任一阶段失败时该目录不存在，
        预览面板永远空白——用户误以为"系统没有任何可视化产物"。
        实际上 M3 网格（30_dense）或 S1 稠密点云（10_dense）往往已经生成，
        完全可以先给用户看一眼中间结果。

        **顺序改成 PLY 优先**（2026-10-26 实机）：OBJ/GLB 在 Open3D 里走
        ASSIMP，路径含中文时会抛 ``UnicodeDecodeError``（``0xb1`` 是「兵」的
        GBK 首字节）；PLY 走原生 reader，不受路径编码影响，且预览本来就不显示
        贴图，两者几何完全一致。
        """
        name = self._item_id()
        return _paths.model_candidates(self.cfg.root_dir, name,
                                       exts=("ply", "obj", "glb"))

    def _refresh_preview(self, ok: bool = True,
                         show_placeholder: bool = False) -> str:
        """把当前文物的模型/贴图/热力图按钮刷新到界面；返回载入的路径。

        打开项目时（构造完窗口）与每次运行结束后都会调一次——旧实现只在
        ``_on_finished`` 里载入，**打开一个已完成的项目预览永远是空的**。
        """
        shown = ""
        for cand in self._preview_candidates():
            if not os.path.exists(cand):
                continue
            if self.mesh_preview.load_mesh(cand):
                shown = cand
                break
            self._log(f"[界面] 预览跳过 {os.path.basename(cand)}（无法读取）")
        if shown:
            self._preview_path = shown
            self.btn_open3d.setEnabled(True)
            if not ok:
                self._log(f"▶ 已加载中间产物预览：{shown}")
        else:
            self._preview_path = ""
            self.btn_open3d.setEnabled(False)
            if show_placeholder:
                reason = self._failure_text() if not ok else "本次运行未生成网格"
                self.mesh_preview.show_text(
                    "未生成可预览的三维模型\n" + (reason or ""))

        tex = os.path.join(_paths.stage_root(self.cfg.root_dir), "50_tex",
                           self._item_id())
        if os.path.isdir(tex):
            for f in sorted(os.listdir(tex)):
                if f.endswith("diffuse.png"):
                    self.tex_view.show_texture(os.path.join(tex, f))
                    break
        return shown

    def _item_id(self) -> str:
        """当前展示的文物编号（**不是**项目名）。

        项目名与 ``00_raw`` 子目录名不同名是常态（如项目 ``yyyyyyy`` 里放
        ``relic_001BMYyyyy``）；拿项目名拼 ``80_export/<name>/`` 会一个都找不到，
        预览空白、导出报「无交付物」。反查逻辑见 :mod:`h3d.gui.items`。
        """
        return primary_item(self.cfg.root_dir, self.cfg.name)

    def _all_items(self) -> list[str]:
        """项目内全部文物编号（多文物批量导出用）。"""
        return discover_items(self.cfg.root_dir)

    def _manifest_path(self) -> str:
        """本项目的 run_manifest 路径（新布局在 阶段成果/output 下）。"""
        from h3d.gui.items import _manifest_paths
        for cand in _manifest_paths(self.cfg.root_dir):
            if os.path.isfile(cand):
                return cand
        return os.path.join(_paths.stage_root(self.cfg.root_dir), "output",
                            "run_manifest.json")

    def _failure_text(self) -> str:
        """从 manifest 提取失败阶段与原因首行（供预览区/日志展示）。"""
        import json
        path = self._manifest_path()
        try:
            with open(path, encoding="utf-8") as fh:
                stages = json.load(fh).get("stages", {})
        except (OSError, ValueError):
            return ""
        for key, entry in stages.items():
            if entry.get("status") != "fail":
                continue
            stage = key.rsplit("/", 1)[-1]
            reason = (entry.get("reason") or "").strip().splitlines()
            first = reason[0] if reason else "（无异常信息）"
            return f"{stage}：{first[:160]}"
        return ""

    def _on_finished(self, ok: bool, summary: str):
        # 输入区恢复（约束卡片收起）。耗时条**不清空**——用户看报表前先看界面，
        # "刚才哪一步慢"就在这里。
        self._set_running_ui(False)
        self.btn_export.setEnabled(ok or bool(self._all_items()))
        self._tick.stop()
        self._cur_substep = ""
        self._cur_stage = ""
        self._in_dense = False
        self._last_log_ts = 0.0
        # 结算最后一次计时，然后用 manifest 的权威值覆盖（与报表「耗时」同源）
        for sid in list(self._stage_t0):
            self._settle_stage_time(sid)
        wall = None
        if self._t0 is not None:
            import time as _time
            wall = max(0.0, _time.time() - self._t0)
        if self.stage_timing.apply_manifest(self._manifest_path(), wall):
            self._log("● 阶段耗时已按 run_manifest 校准（与报表同源）")
        elif wall:
            self.stage_timing.set_footer(f"总时长 {fmt_duration(wall)}（界面计时）")
        # 进度条**不归零**：原实现在 ok=False 时 setValue(0)，导致「跑到 100%
        # 却被判失败」时用户看到的是 0%，比不停在 100% 更让人困惑。失败时
        # 保留最后位置，由红字状态说明「未完成」就够了。
        self.progress.setFormat("%p%")
        # 「无需重跑」（断点续跑整批跳过）是**正确终态**，不能染成红色失败态
        rerun_skipped = summary.startswith("无需重跑")
        if ok:
            self.lbl_state.setText("✔ 生成完成" if not rerun_skipped
                                   else "✔ 已是最新（无需重跑）")
            self.lbl_state.setStyleSheet(
                "QLabel{color:#3fb950;font-weight:bold;font-size:13px;}")
        else:
            self.lbl_state.setText("✘ 运行未完成（详见日志与报表）")
            self.lbl_state.setStyleSheet(
                "QLabel{color:#ff6b6b;font-weight:bold;font-size:13px;}")
        self._log(f"● {summary}")
        # 跑完刷新档案页的版本数（只读，不建档）。放在这里是因为登记发生在
        # runner 里，界面只有等到结束才知道有没有新版本。
        self._refresh_archive()
        # 强制重算是一次性意图：跑完立刻把勾选与 yaml 里的标记清零。
        # 不清的话，用户下次打开项目点一次生成又会重跑全链（而且要 30 分钟）。
        if getattr(self.cfg, "force_rerun", False):
            self.cfg.force_rerun = False
            self.chk_force.setChecked(False)
            try:
                if not os.path.isdir(self.cfg.root_dir):
                    raise OSError(f"项目根不存在：{self.cfg.root_dir}")
                self.cfg.to_yaml(os.path.join(self.cfg.root_dir, "config.yaml"))
                self._log("● 强制重算标记已复位（下次运行为正常断点续跑）")
            except Exception as exc:  # noqa: BLE001 - 复位失败不阻断
                self._log(f"[界面] 强制重算标记复位失败（请手动取消勾选）：{exc}")

        # 预览：成品缺失时回退到中间产物。
        # 单独 try/except 包住——预览读模型失败（中文路径下的 ASSIMP、损坏文件）
        # 绝不能让后面的结果条 / 导出按钮 / X1 按钮一起停摆。实机缺陷
        # （2026-10-26）：异常从本槽逃走，PySide6 只打 stderr，windowed 打包版
        # stderr 为 None → 界面表现为"跑完了但预览空白，结果条也不出现"。
        try:
            self._refresh_preview(ok=ok, show_placeholder=True)
        except Exception as exc:  # noqa: BLE001 - 预览失败不影响主流程
            self._preview_path = ""
            self.btn_open3d.setEnabled(False)
            self._log(f"[界面] 预览加载失败（不影响交付物）：{exc}")

        if not ok:
            # 失败时把"去哪看"写清楚，而不是只弹一句"失败"
            report = _paths.report_html(self.cfg.root_dir)
            logs = os.path.join(_paths.stage_root(self.cfg.root_dir), "99_runs",
                                "sfm_tmp", self._item_id(), "logs")
            self._log(f"✗ 失败原因：{self._failure_text() or '见日志'}")
            if os.path.exists(report):
                self._log(f"✗ 详细报表：{report}")
            if os.path.isdir(logs):
                self._log(f"✗ COLMAP 完整日志：{logs}")

        # X1 结果存在才启用热力图（同样不能因读取失败连累结果条）
        try:
            from h3d.gui.viewer3d import load_x1_changes
            if load_x1_changes(self.cfg.root_dir, self._item_id()) is not None:
                self.btn_x1.setEnabled(True)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[界面] X1 结果读取失败（热力图按钮保持禁用）：{exc}")
        # 结论进结果条（常驻、可继续操作），不再让用户先点掉一个弹窗。
        # 失败仍弹模态框：结果与预期不符，不看就直接接着操作更危险。
        self._show_result(ok, summary, skipped_only=rerun_skipped)
        if ok and rerun_skipped:
            self.lbl_result.setText(
                self.lbl_result.text()
                + "　（如需重算：勾选左侧「强制重算」后再点一次）")
        if not ok:
            QMessageBox.warning(
                self, "运行未完成",
                f"{summary}\n\n失败原因：{self._failure_text() or '见日志'}\n"
                f"报表：{_paths.report_html(self.cfg.root_dir)}")

    def _export_package(self):
        default = os.path.join(self.cfg.root_dir,
                               f"{self.cfg.name}_交付包.zip")
        out, _ = QFileDialog.getSaveFileName(self, "导出交付包", default,
                                             "ZIP 压缩包 (*.zip)")
        if not out:
            return
        # 导出**全部**已交付文物：批量项目里逐件导出会得到一堆同名 zip
        items = self._all_items() or self.cfg.name
        r = export_delivery_package(self.cfg.root_dir, items, out)
        if not r:
            QMessageBox.warning(self, "无交付物",
                                "未找到交付产物（交付成果/<文物编号>/），请先运行一键生成。")
            return
        got = r.get("items") or []
        detail = (f"（{len(got)} 件：{', '.join(got[:3])}"
                  f"{'…' if len(got) > 3 else ''}）") if len(got) > 1 else ""
        QMessageBox.information(self, "导出完成",
                                f"交付包：{out}\n{r['n_files']} 个文件，"
                                f"{r['size_kb']} KB{detail}")

    def _open3d_window(self):
        from h3d.gui.viewer3d import show_open3d_window
        item = self._item_id()
        model = getattr(self, "_preview_path", "")
        if not model or not os.path.exists(model):
            for cand in self._preview_candidates():
                if os.path.exists(cand):
                    model = cand
                    break
        if not os.path.exists(model):
            QMessageBox.warning(self, "暂无可视化模型",
                                "本次运行未生成可打开的三维模型。\n"
                                "请先查看 交付成果/report/report.html 的失败原因。")
            return
        tex = os.path.join(_paths.stage_root(self.cfg.root_dir), "50_tex", item)
        tex_png = None
        if os.path.isdir(tex):
            for f in sorted(os.listdir(tex)):
                if f.endswith("diffuse.png"):
                    tex_png = os.path.join(tex, f)
                    break
        try:
            ok = show_open3d_window(model, texture_path=tex_png)
        except Exception as exc:  # noqa: BLE001 - 交互窗口失败要给可见提示
            self._log(f"[界面] 打开 3D 交互窗口失败：{exc}")
            QMessageBox.warning(self, "无法打开 3D 窗口",
                                f"{type(exc).__name__}: {exc}\n\n模型：{model}")
            return
        if not ok:
            QMessageBox.warning(self, "无法打开 3D 窗口",
                                f"模型读取失败或为空：\n{model}\n\n"
                                "可改用「3D 模型预览」面板查看，交付物本身不受影响。")

    def _show_x1(self):
        from h3d.gui.viewer3d import show_x1_heatmap
        item = self._item_id()
        ddir = _paths.delivery_item_dir(self.cfg.root_dir, item)
        model = os.path.join(ddir, f"{item}.ply")
        if not os.path.exists(model):
            model = os.path.join(ddir, f"{item}.obj")
        try:
            ok = show_x1_heatmap(model, self.cfg.root_dir, item)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[界面] 打开 X1 热力图失败：{exc}")
            QMessageBox.warning(self, "无法打开 X1 热力图",
                                f"{type(exc).__name__}: {exc}")
            return
        if not ok:
            QMessageBox.information(self, "X1 热力图",
                                    "未找到可用的模型或 X1 差分结果（需至少两期数据）。")

    def _log(self, line: str):
        """日志着色：把"加载/进度"行突出出来，失败行一眼可见。

        原实现全部单色，50 分钟的稠密重建输出与普通信息混在一起，用户既
        看不出进度也看不出异常。分类规则见 :func:`_line_style`。
        同时记录最后一条日志时间，供状态行做「最后输出 Ns 前」心跳 ——
        COLMAP 单张深度图约 47 秒，期间零输出，不提示就与卡死无法区分。
        """
        import time as _time
        self._last_log_ts = _time.time()
        color, bold = _line_style(line)
        if not color:
            self.log.appendPlainText(line)
            return
        weight = "bold" if bold else "normal"
        self.log.appendHtml(
            f'<span style="color:{color};font-weight:{weight};">'
            f"{html.escape(line)}</span>")

    def closeEvent(self, event):  # noqa: N802 - Qt 命名
        """关闭窗口时若仍在运行，先确认并终止子进程。

        实机问题（2026-10-17）：打包版子进程复用 GUI 的解包目录。用户直接
        关窗口 → 该目录被删 → 仍在跑的 --batch 在最后 import open3d 时崩，
        表现为"跑满 56 分钟后失败、无任何产物"。
        现在：确认后主动终止子进程并等待退出，避免留下孤儿进程。
        """
        if self.runner is not None and self.runner.isRunning():
            ret = QMessageBox.question(
                self, "仍在生成中",
                "当前仍在生成交付物。\n"
                "直接退出会中断子进程，已完成的阶段产物不会被破坏，"
                "但本次不会产出成品。\n\n确定要退出吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            self.runner.cancel()
            self.runner.wait(8000)
        self._tick.stop()
        event.accept()


def _lamp_style(status: str) -> str:
    colors = {"waiting": "#666", "running": "#d89000", "done": "#2d7d46",
              "failed": "#c0392b", "skipped": "#7a7f87"}
    return (f"QLabel{{background:{colors[status]};color:white;"
            f"border-radius:10px;padding:6px;font-weight:bold;}}")


def depth_progress(root_dir: str, item: str, phase: int = 1) -> tuple[int, int]:
    """返回 (已完成, 总数) 的深度图计数；只统计**指定文物**。

    实机缺陷（2026-10-17）：原实现遍历 ``99_runs/sfm_tmp`` 下所有文物取最大
    值。上一轮残留的旧目录（``relic_666`` 有 30 张）会让界面在稀疏阶段刚起步
    就显示「深度图 30/30」，之后 18 分钟纹丝不动 —— 用户判断为卡死，实际
    子进程正在算第 2 张。

    ``phase``：1 = photometric（第一遍），2 = geometric（几何一致性校验）。
    ``patch_match_stereo`` 会跑两遍，两遍各 30 张；若不分相位取最大值，
    第二遍开始时计数会一直停在 30/30。

    分母取 ``photos_small`` 的照片数，与 COLMAP 的 ``Processing view n / N``
    同源；缓存缺失时退回已生成数，避免显示 ``12/0``。
    """
    if not item:
        return (0, 0)
    base = os.path.join(root_dir, "99_runs", "sfm_tmp", item)
    dm = os.path.join(base, "dense", "stereo", "depth_maps")
    if not os.path.isdir(dm):
        return (0, 0)
    try:
        names = os.listdir(dm)
    except OSError:
        return (0, 0)
    ext = ".geometric.bin" if phase >= 2 else ".photometric.bin"
    done = sum(1 for f in names if f.endswith(ext))
    total = 0
    photos = os.path.join(base, "photos_small")
    if os.path.isdir(photos):
        try:
            total = sum(1 for f in os.listdir(photos) if is_image_file(f))
        except OSError:
            total = 0
    return (done, total or done)


#: 日志着色关键字（顺序即优先级，先命中先返回）
_LOG_RULES: tuple[tuple[tuple[str, ...], str, bool], ...] = (
    (("Traceback", "[ERROR]", "失败隔离", "✘", "[H3D-ERROR]"), "#ff6b6b", True),
    (("[WARNING]", "警告"), "#ffb454", False),
    # 加载进度：用户最关心"正在处理哪一步、哪张照片"，优先级高于普通信息
    (("深度图", "去畸变", "融合 ", "特征提取", "匹配块", "注册图像",
      "Processing view", "Processing file", "Undistorting", "Fusing"),
     "#a5d6ff", True),
    (("▶ ",), "#67e8f9", True),                      # 阶段切换
    ((" 完成", "✔"), "#7ee787", False),
    (("COLMAP[", "dense[", "照片预处理"), "#a5d6ff", True),
    ((".JPG", ".jpg", ".png", ".tif", ".NEF"), "#9fd6ff", False),
)


def _line_style(line: str) -> tuple[str, bool]:
    """返回 (颜色, 是否加粗)；空颜色表示用默认样式。

    目的是让"正在加载哪张照片 / 跑到第几张深度图"这类行在长日志里能一眼看到。
    """
    for keys, color, bold in _LOG_RULES:
        if any(k in line for k in keys):
            return color, bold
    return "", False


def _show_boot_preflight() -> None:
    """展示软件启动自检结论（只在**存在阻断项**时弹窗）。

    启动自检本身在 ``launcher.main`` 里已跑过（早于 QApplication），这里只负责
    把结论呈现给用户。刻意不因为阻断项阻止进入界面：用户往往需要先进来改配置
    或换数据目录才能修复，硬拦会把软件变成"打不开"。
    """
    try:
        from h3d.health import format_report, last_report
        rep = last_report("boot")
        if rep is None or rep.ok:
            return
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox()
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("启动自检")
        box.setText("发现 %d 个可能影响运行的问题" % len(rep.blockers))
        box.setInformativeText("软件仍可使用；开跑前还会再做一次项目自检。")
        box.setDetailedText(format_report(rep))
        box.exec()
    except Exception:  # noqa: BLE001 - 自检展示失败不影响启动
        pass


def run_gui() -> int:
    """GUI 入口：欢迎页（新建向导/历史项目/示例演示）。"""
    app = QApplication(sys.argv)
    # 图标设为 QApplication 级：欢迎页/向导/主窗口/所有对话框自动继承，
    # 无需每个窗口单独 setWindowIcon（漏一个就会出现「一个窗口一个图标」）。
    from h3d.gui.resources import apply_app_icon
    apply_app_icon(app)
    _show_boot_preflight()
    from h3d.gui.welcome import WelcomeWindow
    welcome = WelcomeWindow()
    welcome.show()
    return app.exec()


__all__ = ["MainWindow", "run_gui"]
