"""h3d.gui.advanced : 高级参数折叠面板（P1，主界面折叠）。

GUI.docx 需求：高级设置折叠隐藏（点云降采样/网格精度/纹理采样/泊松
开关/对齐模式/融合权重/dense 质量门）。QGroupBox checkable 折叠。
"""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QSpinBox, QVBoxLayout,
                               QWidget)

from h3d.gui.config_edit import ProjectConfig


class AdvancedPanel(QWidget):
    """高级参数面板：折叠控件 <-> ProjectConfig 双向同步。"""

    def __init__(self, cfg: ProjectConfig, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        # 文物尺度（跨尺度自适应，2026-10-09）
        g0 = QGroupBox("文物尺度")
        g0.setCheckable(True)
        g0.setChecked(False)
        f0 = QFormLayout(g0)
        self.chk_autoscale = QCheckBox("按几何尺度自动调整参数")
        self.chk_autoscale.setChecked(cfg.autoscale)
        self.chk_autoscale.setToolTip(
            "勾选后，体素 / FPFH 半径 / RANSAC 与 ICP 距离会按点云包围盒\n"
            "对角线自动线性缩放，使同一套参数覆盖大中型文物（米级）与中小型\n"
            "文物（厘米级）。取消勾选则完全沿用下方填写的绝对值。")
        f0.addRow("", self.chk_autoscale)
        self.sp_refdiag = QDoubleSpinBox()
        self.sp_refdiag.setRange(0.1, 20.0)
        self.sp_refdiag.setSingleStep(0.1)
        self.sp_refdiag.setDecimals(2)
        self.sp_refdiag.setValue(cfg.ref_diag)
        self.sp_refdiag.setToolTip(
            "基准尺度（米）：'降采样体素'等参数是在多大的文物上调出来的。\n"
            "默认 3.7 对应示例文物；换到另一套标定体系时才需修改。")
        f0.addRow("参数基准尺度(m)", self.sp_refdiag)
        v.addWidget(g0)

        g1 = QGroupBox("点云与网格")
        g1.setCheckable(True)
        g1.setChecked(False)
        f1 = QFormLayout(g1)
        self.sp_voxel = QDoubleSpinBox()
        self.sp_voxel.setRange(0.005, 0.5)
        self.sp_voxel.setSingleStep(0.005)
        self.sp_voxel.setValue(cfg.voxel)
        f1.addRow("降采样体素(m)", self.sp_voxel)
        self.sp_depth = QSpinBox()
        self.sp_depth.setRange(5, 10)
        self.sp_depth.setValue(cfg.poisson_depth)
        f1.addRow("泊松深度", self.sp_depth)
        self.cb_density = QComboBox()
        self.cb_density.addItems(["0.00(保留全部)", "0.02", "0.05"])
        cur = f"{float(cfg.density_ratio):.2f}"
        self.cb_density.setCurrentText(
            "0.00(保留全部)" if cur == "0.00" else cur)
        f1.addRow("密度过滤", self.cb_density)
        v.addWidget(g1)

        g2 = QGroupBox("纹理与融合")
        g2.setCheckable(True)
        g2.setChecked(False)
        f2 = QFormLayout(g2)
        self.sp_topk = QSpinBox()
        self.sp_topk.setRange(1, 10)
        self.sp_topk.setValue(int(cfg.top_k))
        f2.addRow("视角加权 top-k", self.sp_topk)
        self.sp_spp = QSpinBox()
        self.sp_spp.setRange(1, 8)
        self.sp_spp.setValue(int(cfg.samples_per_tri))
        f2.addRow("每面采样点", self.sp_spp)
        self.chk_blend = QCheckBox("泊松接缝融合")
        self.chk_blend.setChecked(bool(cfg.poisson_blend))
        f2.addRow("", self.chk_blend)
        self.cb_align = QComboBox()
        self.cb_align.addItems(["自动 ICP", "控制点引导"])
        self.cb_align.setCurrentText("控制点引导" if not cfg.icp_align
                                     else "自动 ICP")
        f2.addRow("坐标系对齐", self.cb_align)
        self.cb_fusion = QComboBox()
        self.cb_fusion.addItems(["自动", "偏重激光", "偏重照片"])
        f2.addRow("双源融合权重", self.cb_fusion)
        v.addWidget(g2)

        g3 = QGroupBox("重建与质量门")
        g3.setCheckable(True)
        g3.setChecked(False)
        f3 = QFormLayout(g3)
        self.chk_dense_gate = QCheckBox("稠密质量门")
        self.chk_dense_gate.setChecked(cfg.dense_gate)
        f3.addRow("", self.chk_dense_gate)
        self.chk_quality = QCheckBox("几何质检")
        self.chk_quality.setChecked(bool(cfg.quality_enabled))
        f3.addRow("", self.chk_quality)
        v.addWidget(g3)
        v.addStretch()

    def apply(self) -> None:
        """把面板值写回 cfg。

        2026-10-25：原实现只回写 4 个字段（autoscale/ref_diag/voxel/depth/
        dense_gate），top-k、每面采样、泊松融合、密度过滤、几何质检**改了
        不生效**——界面上像个开关，实际没有任何代码读它。现在全部接通到
        :meth:`ProjectConfig.to_yaml`。
        """
        self.cfg.autoscale = self.chk_autoscale.isChecked()
        self.cfg.ref_diag = float(self.sp_refdiag.value())
        self.cfg.voxel = float(self.sp_voxel.value())
        self.cfg.poisson_depth = int(self.sp_depth.value())
        self.cfg.dense_gate = self.chk_dense_gate.isChecked()
        self.cfg.top_k = int(self.sp_topk.value())
        self.cfg.samples_per_tri = int(self.sp_spp.value())
        self.cfg.poisson_blend = self.chk_blend.isChecked()
        self.cfg.icp_align = self.cb_align.currentText() == "自动 ICP"
        self.cfg.quality_enabled = self.chk_quality.isChecked()
        txt = self.cb_density.currentText()
        self.cfg.density_ratio = 0.0 if txt.startswith("0.00") else float(txt)


__all__ = ["AdvancedPanel"]
