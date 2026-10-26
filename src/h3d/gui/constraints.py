"""h3d.gui.constraints : 生成约束的「先确认后运行」闸门。

为什么要有这一步（2026-10-26 实机反馈）
--------------------------------------
原来界面上的参数是**随时可改、改了立即生效**的：用户点了「一键生成交付物」，
管线按当时控件的值跑；跑到一半想改"其实我想要 4096 图集"，只能取消重来。更
常见的是**根本没意识到自己选了什么**——默认关着稠密重建的纯照片项目跑完
30 分钟，才发现几何是稀疏点云插值出来的。

约束确认解决两件事：

1. **开跑前一次性把"这次要什么"说清楚**：稠密与否、精度档、纹理分辨率、
   交付格式、是否强制重算。不确认就不启动（防误点，也防"默认值静默生效"）。
2. **运行期只读**：确认后这些选项在界面里**展示但不可改**（:class:`ConstraintSummary`
   + 主界面禁用输入区），避免"看起来改了其实没进配置"的假控件。

与配置的关系
------------
确认结果写回 :class:`~h3d.gui.config_edit.ProjectConfig` 并落进 ``config.yaml``
的 ``runner.constraints`` / ``runner.constraints_confirmed``：子进程只认 yaml，
界面里选了不落盘就等于没选（「假控件」的老毛病，1.1.7 踩过一次）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QVBoxLayout, QWidget)

from h3d.gui.widgets import ElidedLabel

#: 交付格式（模型文件）。``ply`` 只能存顶点色，**无法引用图像纹理**——
#: 只勾 ply 时界面必须提示"图集会作为独立文件交付，模型里看不到贴图"。
FORMAT_CHOICES = ("ply", "obj", "glb")

#: 精度档位 → 泊松深度
DEPTH_CHOICES = (("标准（快，面数少）", 7), ("精细（推荐）", 8),
                 ("超精细（慢，面数多）", 9))

#: 降采样档位 → 基准器物上的体素（米）
VOXEL_CHOICES = (("粗（基准 5 cm）", 0.05), ("中（基准 2 cm）", 0.02),
                 ("细（基准 1 cm）", 0.01))
#: 纹理图集边长
TEX_CHOICES = (1024, 2048, 4096)


@dataclass
class Constraints:
    """一次生成的全部约束（界面确认后即冻结）。"""

    scene: str = "scene2"
    dense: bool = True
    poisson_depth: int = 8
    voxel: float = 0.02
    texture_size: int = 2048
    export_formats: list[str] = field(default_factory=lambda: ["ply", "obj", "glb"])
    quality: bool = True
    force_rerun: bool = False

    # ---------------------------------------------------------- 序列化
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["export_formats"] = list(self.export_formats)
        return d

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "Constraints":
        data = dict(data or {})
        known = {f for f in cls.__dataclass_fields__}          # noqa: SLF001
        clean = {k: v for k, v in data.items() if k in known}
        try:
            return cls(**clean)
        except TypeError:
            return cls()

    def validate(self) -> list[str]:
        """返回问题列表（空 = 可以开跑）。

        ``ply`` 单格式不是错误，是**需要用户知情的取舍**：管线会自动补导
        OBJ 让图集有模型可引用（``photo_texture.auto_textured_format``），
        但交付包里会多一份 obj/mtl/png。这里只拦真正的矛盾项。
        """
        errs: list[str] = []
        if not self.export_formats:
            errs.append("至少选择一种交付格式（ply / obj / glb）")
        if self.texture_size not in TEX_CHOICES:
            errs.append(f"纹理分辨率只能是 {TEX_CHOICES}")
        if int(self.poisson_depth) not in (7, 8, 9):
            errs.append("网格精度只能是 7 / 8 / 9")
        if self.scene == "scene2" and not self.dense:
            # 纯照片场景的几何**只能**来自稠密重建：关掉就没有几何来源。
            errs.append("纯照片场景必须开启稠密重建（否则没有几何来源）")
        return errs

    # ---------------------------------------------------------- 展示
    def summary_rows(self) -> list[tuple[str, str]]:
        """只读展示用：``[(项目, 取值)]``，顺序固定。"""
        depth_cn = {7: "标准(7)", 8: "精细(8)", 9: "超精细(9)"}
        voxel_cn = {0.05: "粗（基准 5 cm）", 0.02: "中（基准 2 cm）",
                    0.01: "细（基准 1 cm）"}
        rows = [
            ("场景", "①激光+照片" if self.scene == "scene1" else "②纯照片"),
            ("稠密重建", "开启" if self.dense else "关闭"),
            ("网格精度", depth_cn.get(int(self.poisson_depth),
                                      str(self.poisson_depth))),
            ("降采样", voxel_cn.get(round(float(self.voxel), 3),
                                    f"{self.voxel}")),
            ("纹理分辨率", f"{self.texture_size}²"),
            ("交付格式", " / ".join(self.export_formats) or "（空）"),
            ("几何质检", "开启" if self.quality else "关闭"),
            ("强制重算", "是（忽略断点续跑）" if self.force_rerun else "否"),
        ]
        if self.warns():
            rows.append(("注意", "；".join(self.warns())))
        return rows

    def warns(self) -> list[str]:
        """非阻断但必须说清楚的取舍。"""
        out: list[str] = []
        if self.export_formats and "ply" in self.export_formats \
                and not ({"obj", "glb"} & set(self.export_formats)):
            out.append("只交付 .ply：模型里看不到图像贴图，管线会自动补导 OBJ 承载图集")
        if self.texture_size >= 4096:
            out.append("4096² 图集显存/内存占用大，且烘焙时间约为 2048² 的 4 倍")
        if int(self.poisson_depth) >= 9:
            out.append("超精细面数可能到百万级，导出与预览都会明显变慢")
        return out


# ---------------------------------------------------------------------------
# 从界面/配置取约束
# ---------------------------------------------------------------------------
def from_project_config(cfg: Any) -> Constraints:
    """从 :class:`ProjectConfig` 读当前约束（作为对话框初值）。

    若 ``cfg.constraints`` 里已有上次确认的快照，则以**快照**为准——用户上次
    确认的是"4096 + obj"，这次打开项目不该因为控件默认值变回 2048。
    """
    snap = Constraints.from_dict(getattr(cfg, "constraints", None))
    if getattr(cfg, "constraints_confirmed", False):
        return snap
    return Constraints(
        scene=getattr(cfg, "scene", "scene2"),
        dense=bool(getattr(cfg, "dense", True)),
        poisson_depth=int(getattr(cfg, "poisson_depth", 8)),
        voxel=float(getattr(cfg, "voxel", 0.02)),
        texture_size=int(getattr(cfg, "texture_size", 2048)),
        export_formats=list(getattr(cfg, "export_formats", ["ply", "obj", "glb"])),
        quality=bool(getattr(cfg, "quality_enabled", True)),
        force_rerun=bool(getattr(cfg, "force_rerun", False)),
    )


def apply_to_project_config(cfg: Any, c: Constraints) -> None:
    """把约束写回 :class:`ProjectConfig`（随后由 ``to_yaml`` 落盘）。"""
    cfg.scene = c.scene
    cfg.dense = bool(c.dense)
    cfg.poisson_depth = int(c.poisson_depth)
    cfg.voxel = float(c.voxel)
    cfg.texture_size = int(c.texture_size)
    cfg.export_formats = list(c.export_formats)
    cfg.quality_enabled = bool(c.quality)
    cfg.force_rerun = bool(c.force_rerun)
    cfg.constraints = c.to_dict()
    cfg.constraints_confirmed = True


def estimate_minutes(c: Constraints, n_photos: int = 0) -> str:
    """粗略耗时区间（仅用于让用户有心理预期，不追求精确）。

    系数来自 2026-10-26 的 30 张 4.5MP 实机：稠密两遍 6.8 ks ≈ 114 min 是
    ``dense_geom_consistency=true`` 的那次；关掉几何一致性后 842 s 省掉，
    折算约 17 min。此处按"每 30 张 ≈ 17 min"给区间，并随图集尺寸放大纹理段。
    """
    if c.scene == "scene2" or c.dense:
        base = 17.0 * max(1.0, (n_photos or 30) / 30.0)
    else:
        base = 3.0
    tex = {1024: 0.4, 2048: 1.0, 4096: 4.0}.get(int(c.texture_size), 1.0)
    total = base + 6.0 * tex
    if c.force_rerun:
        total *= 1.05
    lo, hi = total * 0.75, total * 1.6
    return f"约 {int(lo)}~{int(hi)} 分钟（{n_photos or '?'} 张照片估算）"


# ---------------------------------------------------------------------------
# 对话框
# ---------------------------------------------------------------------------
class ConstraintsDialog(QDialog):
    """「本次生成约束」确认框。

    三颗按钮的语义区别很清楚：

    * **开始生成**：按当前选择跑；
    * **全部用默认值**：把选择重置为推荐值再跑（用户不想纠结时的快路径）；
    * **取消**：不进管线（"不确认就不生成"）。
    """

    def __init__(self, base: Constraints, n_photos: int = 0,
                 n_items: int = 1, parent=None):
        super().__init__(parent)
        self.setWindowTitle("确认本次生成约束")
        self.setModal(True)
        self._n_photos = n_photos
        self._n_items = n_items
        self._defaults = Constraints(scene=base.scene)   # 推荐值
        self._build(base)

    # ---------------------------------------------------------- UI
    def _build(self, base: Constraints) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)

        tip = QLabel(
            "确认后管线按下列约束执行，运行期间不可修改。\n"
            "「全部用默认值」= 稠密开 / 精细(8) / 2048² / ply+obj+glb / 质检开。")
        tip.setWordWrap(True)
        tip.setStyleSheet("QLabel{color:#9aa4b2;}")
        v.addWidget(tip)

        src = ElidedLabel("", min_chars=30)
        src.setText(f"数据源：{self._n_items} 件文物 · {self._n_photos} 张照片"
                    if self._n_photos else f"数据源：{self._n_items} 件文物")
        v.addWidget(src)

        # 几何
        g1 = QGroupBox("几何重建")
        f1 = QGridLayout(g1)
        f1.addWidget(QLabel("稠密重建"), 0, 0)
        self.cb_dense = QComboBox()
        self.cb_dense.addItems(["开启（质量高，耗时主要来源）", "关闭（快，稀疏点云插值）"])
        self.cb_dense.setCurrentIndex(0 if base.dense else 1)
        f1.addWidget(self.cb_dense, 0, 1)

        f1.addWidget(QLabel("网格精度"), 1, 0)
        self.cb_depth = QComboBox()
        for label, _ in DEPTH_CHOICES:
            self.cb_depth.addItem(label)
        self.cb_depth.setCurrentIndex(
            max(0, [d for _, d in DEPTH_CHOICES].index(
                int(base.poisson_depth)) if int(base.poisson_depth) in
                [d for _, d in DEPTH_CHOICES] else 1))
        f1.addWidget(self.cb_depth, 1, 1)

        f1.addWidget(QLabel("降采样"), 2, 0)
        self.cb_voxel = QComboBox()
        for label, _ in VOXEL_CHOICES:
            self.cb_voxel.addItem(label)
        self.cb_voxel.setCurrentIndex(
            min(range(len(VOXEL_CHOICES)),
                key=lambda i: abs(VOXEL_CHOICES[i][1] - float(base.voxel))))
        f1.addWidget(self.cb_voxel, 2, 1)
        v.addWidget(g1)

        # 纹理与交付
        g2 = QGroupBox("纹理与交付")
        f2 = QGridLayout(g2)
        f2.addWidget(QLabel("纹理分辨率"), 0, 0)
        self.cb_tex = QComboBox()
        for n in TEX_CHOICES:
            self.cb_tex.addItem(f"{n}²")
        self.cb_tex.setCurrentIndex(
            TEX_CHOICES.index(base.texture_size)
            if base.texture_size in TEX_CHOICES else 1)
        f2.addWidget(self.cb_tex, 0, 1)

        f2.addWidget(QLabel("交付格式"), 1, 0)
        fmt_row = QHBoxLayout()
        self.chk_fmt: dict[str, QCheckBox] = {}
        for name in FORMAT_CHOICES:
            cb = QCheckBox(name)
            cb.setChecked(name in (base.export_formats or []))
            self.chk_fmt[name] = cb
            fmt_row.addWidget(cb)
        fmt_row.addStretch()
        f2.addLayout(fmt_row, 1, 1)

        f2.addWidget(QLabel("几何质检"), 2, 0)
        self.cb_qc = QComboBox()
        self.cb_qc.addItems(["开启", "关闭"])
        self.cb_qc.setCurrentIndex(0 if base.quality else 1)
        f2.addWidget(self.cb_qc, 2, 1)

        f2.addWidget(QLabel("强制重算"), 3, 0)
        self.cb_force = QComboBox()
        self.cb_force.addItems(["否（命中已完成阶段就跳过）", "是（全部重跑）"])
        self.cb_force.setCurrentIndex(1 if base.force_rerun else 0)
        f2.addWidget(self.cb_force, 3, 1)
        v.addWidget(g2)

        # 实时提示
        self.lbl_note = QLabel("")
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setStyleSheet("QLabel{color:#f0a020;}")
        v.addWidget(self.lbl_note)
        self.lbl_eta = QLabel("")
        self.lbl_eta.setStyleSheet("QLabel{color:#9aa4b2;}")
        v.addWidget(self.lbl_eta)

        bb = QDialogButtonBox()
        self.btn_ok = QPushButton("开始生成")
        self.btn_ok.setDefault(True)
        self.btn_ok.setStyleSheet(
            "QPushButton{background:#2d7d46;color:white;font-weight:bold;"
            "padding:6px 16px;border-radius:5px;}")
        self.btn_default = QPushButton("全部用默认值")
        self.btn_cancel = QPushButton("取消")
        bb.addButton(self.btn_ok, QDialogButtonBox.AcceptRole)
        bb.addButton(self.btn_default, QDialogButtonBox.ActionRole)
        bb.addButton(self.btn_cancel, QDialogButtonBox.RejectRole)
        v.addWidget(bb)

        self.btn_ok.clicked.connect(self._on_ok)
        self.btn_default.clicked.connect(self._on_defaults)
        self.btn_cancel.clicked.connect(self.reject)
        for w in (self.cb_dense, self.cb_tex, self.cb_qc, self.cb_force,
                  self.cb_depth):
            w.currentIndexChanged.connect(self._refresh_note)
        for cb in self.chk_fmt.values():
            cb.toggled.connect(self._refresh_note)
        self._refresh_note()

    # ---------------------------------------------------------- 取值
    def constraints(self) -> Constraints:
        c = Constraints(
            scene=self._defaults.scene,
            dense=self.cb_dense.currentIndex() == 0,
            poisson_depth=DEPTH_CHOICES[self.cb_depth.currentIndex()][1],
            voxel=VOXEL_CHOICES[self.cb_voxel.currentIndex()][1],
            texture_size=TEX_CHOICES[self.cb_tex.currentIndex()],
            export_formats=[n for n, cb in self.chk_fmt.items() if cb.isChecked()],
            quality=self.cb_qc.currentIndex() == 0,
            force_rerun=self.cb_force.currentIndex() == 1,
        )
        return c

    def _refresh_note(self) -> None:
        c = self.constraints()
        warns = c.warns()
        errs = c.validate()
        if errs:
            self.lbl_note.setText("✘ " + "；".join(errs))
            self.btn_ok.setEnabled(False)
        elif warns:
            self.lbl_note.setText("⚠ " + "；".join(warns))
            self.btn_ok.setEnabled(True)
        else:
            self.lbl_note.setText("")
            self.btn_ok.setEnabled(True)
        self.lbl_eta.setText("预计：" + estimate_minutes(c, self._n_photos))

    # ---------------------------------------------------------- 事件
    def _on_ok(self) -> None:
        if self.constraints().validate():
            self._refresh_note()
            return
        self.accept()

    def _on_defaults(self) -> None:
        d = self._defaults
        self.cb_dense.setCurrentIndex(0)
        self.cb_depth.setCurrentIndex(1)
        self.cb_voxel.setCurrentIndex(1)
        self.cb_tex.setCurrentIndex(1)
        for name, cb in self.chk_fmt.items():
            cb.setChecked(name in d.export_formats)
        self.cb_qc.setCurrentIndex(0)
        self.cb_force.setCurrentIndex(0)
        self._refresh_note()
        self.accept()


class ConstraintSummary(QWidget):
    """运行期只读约束展示（无任何输入控件）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        box = QFrame(self)
        box.setObjectName("constraintBox")
        box.setStyleSheet(
            "QFrame#constraintBox{background:#1b2430;border:1px solid #33415a;"
            "border-radius:6px;}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)
        v = QVBoxLayout(box)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(3)
        head = ElidedLabel("本次生成约束（已锁定，运行期不可修改）", min_chars=20)
        head.setStyleSheet("QLabel{color:#8fb3d9;font-weight:bold;font-size:12px;}")
        v.addWidget(head)
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(6)
        self._grid.setVerticalSpacing(2)
        v.addLayout(self._grid)
        self._rows: list[tuple[ElidedLabel, ElidedLabel]] = []
        self.setVisible(False)

    def set_constraints(self, c: Constraints) -> None:
        # 清空旧行（重新确认时行数可能变）
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._rows.clear()
        for i, (k, val) in enumerate(c.summary_rows()):
            lk = ElidedLabel(k, min_chars=6)
            lk.setStyleSheet("QLabel{color:#9aa4b2;font-size:11px;}")
            lv = ElidedLabel(val, min_chars=14)
            lv.setStyleSheet("QLabel{color:#e6edf3;font-size:11px;}")
            self._grid.addWidget(lk, i, 0)
            self._grid.addWidget(lv, i, 1)
            self._rows.append((lk, lv))
        self.setVisible(True)

    def clear(self) -> None:
        self.setVisible(False)


__all__ = ["Constraints", "ConstraintsDialog", "ConstraintSummary",
           "FORMAT_CHOICES", "DEPTH_CHOICES", "VOXEL_CHOICES", "TEX_CHOICES",
           "from_project_config", "apply_to_project_config", "estimate_minutes"]
