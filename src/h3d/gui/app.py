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
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMainWindow, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from h3d.core.media import is_image_file
from h3d.gui.config_edit import ProjectConfig
from h3d.gui.export import export_delivery_package
from h3d.gui.items import discover_items, primary_item
from h3d.gui.runner import STAGE_ORDER, BatchRunner
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
        self.resize(1280, 800)
        self._build()

    # ------------------------------------------------------------ UI
    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        top = QSplitter(Qt.Horizontal)
        top.addWidget(self._build_config_panel())
        top.addWidget(self._build_preview_panel())
        top.setSizes([420, 860])
        root.addWidget(top, 3)
        root.addWidget(self._build_status_panel(), 2)

    def _build_config_panel(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("<b>功能配置</b>"))
        # 几何重建
        v.addWidget(QLabel("几何重建设置"))
        grid = QGridLayout()
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
        v.addLayout(grid)
        # 纹理
        v.addWidget(QLabel("纹理烘焙设置"))
        grid2 = QGridLayout()
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
        v.addLayout(grid2)
        # 质检评估
        v.addWidget(QLabel("质检与评估"))
        grid3 = QGridLayout()
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
        v.addLayout(grid3)
        v.addWidget(QLabel("项目：<i>%s</i>　场景：<i>%s</i>" % (
            self.cfg.name, "①激光+照片" if self.cfg.scene == "scene1"
            else "②纯照片")))
        # 高级参数折叠面板（P1）
        from h3d.gui.advanced import AdvancedPanel
        self.advanced = AdvancedPanel(self.cfg)
        v.addWidget(self.advanced)
        # 操作按钮
        self.btn_run = QPushButton("一键生成交付物")
        self.btn_run.setStyleSheet(
            "QPushButton{background:#2d7d46;color:white;font-weight:bold;"
            "padding:10px;font-size:15px;border-radius:6px;}"
            "QPushButton:disabled{background:#666;}")
        self.btn_run.clicked.connect(self._start_run)
        v.addWidget(self.btn_run)
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
        v.addWidget(self.chk_force)
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
        self.tabs = tabs
        return tabs

    def _build_status_panel(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        # 总进度条
        row = QHBoxLayout()
        row.addWidget(QLabel("总进度"))
        self.progress = QProgressBar()
        self.progress.setValue(0)
        row.addWidget(self.progress, 1)
        v.addLayout(row)
        # 实时状态行：正在做什么 / 已运行多久 / 深度图计数
        state_row = QHBoxLayout()
        self.lbl_state = QLabel("● 空闲")
        self.lbl_state.setStyleSheet(
            "QLabel{color:#9aa4b2;font-weight:bold;font-size:13px;}")
        self.lbl_elapsed = QLabel("")
        self.lbl_elapsed.setStyleSheet("QLabel{color:#9aa4b2;}")
        self.lbl_elapsed.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        state_row.addWidget(self.lbl_state, 1)
        state_row.addWidget(self.lbl_elapsed)
        v.addLayout(state_row)
        # 阶段状态灯
        lamps = QHBoxLayout()
        self.lamps: dict[str, QLabel] = {}
        for sid in STAGE_ORDER:
            lb = QLabel(sid)
            lb.setAlignment(Qt.AlignCenter)
            lb.setStyleSheet(_lamp_style("waiting"))
            self.lamps[sid] = lb
            lamps.addWidget(lb)
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
        return w

    # ------------------------------------------------------------ 运行
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
        self.lbl_result = QLabel("")
        self.lbl_result.setWordWrap(True)
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
        self.lbl_result.setText(f"<b style='color:{color}'>{title}</b>　{summary}")
        self.lbl_result.setStyleSheet(
            f"QLabel{{font-size:13px;color:{color};}}")
        # 只暴露**当前真的存在**的动作：没有产物时「打开输出目录」点了也是空的
        has_out = os.path.isdir(os.path.join(self.cfg.root_dir, "80_export")) \
            or os.path.isdir(os.path.join(self.cfg.root_dir, "50_tex"))
        self.btn_result_open.setEnabled(has_out)
        self.btn_result_open.setToolTip(
            "" if has_out else "本次运行未生成产物目录（80_export / 50_tex）")
        self.btn_result_report.setEnabled(os.path.isfile(
            os.path.join(self.cfg.root_dir, "report", "report.html")))
        self.result_bar.setVisible(True)

    def _open_output_dir(self) -> None:
        """打开本次运行的输出根（优先 80_export，没有就退回项目根）。"""
        for cand in (os.path.join(self.cfg.root_dir, "80_export"),
                     os.path.join(self.cfg.root_dir, "report"),
                     self.cfg.root_dir):
            if cand and os.path.isdir(cand):
                QDesktopServices.openUrl(QUrl.fromLocalFile(cand))
                return
        self._log("[界面] 未找到可打开的输出目录")

    def _open_report(self) -> None:
        """用系统默认程序打开 report.html。"""
        path = os.path.join(self.cfg.root_dir, "report", "report.html")
        if not os.path.isfile(path):
            self._log(f"[界面] 报表不存在：{path}")
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
        # 开跑前自检（第一步，早于任何阶段）：拦不住的问题在这里拦下来
        if not self._preflight_before_run(cfg_path):
            self._log("▶ 已按用户选择中止（未启动管线）")
            self.lbl_state.setText("● 已取消")
            return
        for sid in STAGE_ORDER:
            self.lamps[sid].setStyleSheet(_lamp_style("waiting"))
        self.result_bar.setVisible(False)   # 新一轮开始：上一轮结论作废
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_export.setEnabled(False)
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
            if sid == "S1":
                self._in_dense = False   # 新文物的 S1 从稀疏开始
            self.lbl_state.setText(f"● {sid} 运行中…")
            self.lbl_state.setStyleSheet(
                "QLabel{color:#f0a020;font-weight:bold;font-size:13px;}")
            self._refresh_progress_text()
        elif status == "skipped":
            # 条件不满足而真跳过（单站无 M2、无参考期无 X1…）：
            # 灰色而非橙色，且要清掉"运行中"——否则界面永远停在那一阶段。
            if self._cur_stage == sid:
                self._cur_stage = ""
                self._cur_substep = ""
            self.lbl_state.setText(f"○ {sid} 已跳过（条件不满足）")
            self.lbl_state.setStyleSheet(
                "QLabel{color:#9aa4b2;font-weight:bold;font-size:13px;}")
        elif status == "done":
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
            self.lbl_state.setText(f"✘ {sid} 失败")
            self.lbl_state.setStyleSheet(
                "QLabel{color:#ff6b6b;font-weight:bold;font-size:13px;}")

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
        parts = [f"{self.progress.value()}%"]
        if self._n_items:
            k = len(self._items_seen) or 1
            parts.append(f"第 {k}/{self._n_items} 件")
        if self._cur_stage:
            parts.append(self._cur_stage)
        self.progress.setFormat(" · ".join(parts))

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
        parts = [f"已运行 {sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"]
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
        """
        root, name = self.cfg.root_dir, self._item_id()
        return [
            os.path.join(root, "80_export", name, f"{name}.obj"),
            os.path.join(root, "80_export", name, f"{name}.ply"),
            os.path.join(root, "30_dense", name, f"{name}.ply"),
            os.path.join(root, "10_dense", name, "fused_dense.ply"),
            os.path.join(root, "99_runs", "sfm_tmp", name, "dense",
                         "fused.ply"),
        ]

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

    def _failure_text(self) -> str:
        """从 manifest 提取失败阶段与原因首行（供预览区/日志展示）。"""
        import json
        path = os.path.join(self.cfg.root_dir, "output", "run_manifest.json")
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
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_export.setEnabled(ok or bool(self._all_items()))
        self._tick.stop()
        self._cur_substep = ""
        self._in_dense = False
        self._last_log_ts = 0.0
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

        # 预览：成品缺失时回退到中间产物
        shown = ""
        for cand in self._preview_candidates():
            if os.path.exists(cand) and self.mesh_preview.load_mesh(cand):
                shown = cand
                break
        if shown:
            self._preview_path = shown
            self.btn_open3d.setEnabled(True)
            if not ok:
                self._log(f"▶ 已加载中间产物预览：{shown}")
        else:
            self._preview_path = ""
            self.btn_open3d.setEnabled(False)
            reason = self._failure_text() if not ok else "本次运行未生成网格"
            self.mesh_preview.show_text(
                "未生成可预览的三维模型\n" + (reason or ""))

        tex = os.path.join(self.cfg.root_dir, "50_tex", self._item_id())
        if os.path.isdir(tex):
            for f in sorted(os.listdir(tex)):
                if f.endswith("diffuse.png"):
                    self.tex_view.show_texture(os.path.join(tex, f))
                    break

        if not ok:
            # 失败时把"去哪看"写清楚，而不是只弹一句"失败"
            report = os.path.join(self.cfg.root_dir, "report", "report.html")
            logs = os.path.join(self.cfg.root_dir, "99_runs", "sfm_tmp",
                                self._item_id(), "logs")
            self._log(f"✗ 失败原因：{self._failure_text() or '见日志'}")
            if os.path.exists(report):
                self._log(f"✗ 详细报表：{report}")
            if os.path.isdir(logs):
                self._log(f"✗ COLMAP 完整日志：{logs}")

        # X1 结果存在才启用热力图
        from h3d.gui.viewer3d import load_x1_changes
        if load_x1_changes(self.cfg.root_dir, self._item_id()) is not None:
            self.btn_x1.setEnabled(True)
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
                f"报表：{os.path.join(self.cfg.root_dir, 'report', 'report.html')}")

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
                                "未找到交付产物（80_export），请先运行一键生成。")
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
            model = os.path.join(self.cfg.root_dir, "80_export",
                                 item, f"{item}.obj")
        if not os.path.exists(model):
            QMessageBox.warning(self, "暂无可视化模型",
                                "本次运行未生成可打开的三维模型。\n"
                                "请先查看 report/report.html 中的失败原因。")
            return
        tex = os.path.join(self.cfg.root_dir, "50_tex", item)
        tex_png = None
        if os.path.isdir(tex):
            for f in sorted(os.listdir(tex)):
                if f.endswith("diffuse.png"):
                    tex_png = os.path.join(tex, f)
                    break
        show_open3d_window(model, texture_path=tex_png)

    def _show_x1(self):
        from h3d.gui.viewer3d import show_x1_heatmap
        item = self._item_id()
        model = os.path.join(self.cfg.root_dir, "80_export", item,
                             f"{item}.obj")
        if not os.path.exists(model):
            model = os.path.join(self.cfg.root_dir, "80_export",
                                 item, f"{item}.ply")
        show_x1_heatmap(model, self.cfg.root_dir, item)

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
