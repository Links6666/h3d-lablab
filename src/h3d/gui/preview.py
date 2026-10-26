"""h3d.gui.preview : 3D 预览（matplotlib 内嵌）+ 纹理查看。

matplotlib FigureCanvasQTAgg 内嵌 3D 散点/网格视图；纹理用 QLabel 显示
diffuse 贴图（附带缩放适配）。open3d 数据读取复用 h3d.io。
"""

from __future__ import annotations

import os

import numpy as np

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

# 中文字体（失败提示、坐标轴标签）：未配置时 matplotlib 会把中文渲染成方块
import matplotlib as _mpl

_mpl.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]
_mpl.rcParams["axes.unicode_minus"] = False

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel


class MeshPreview(FigureCanvasQTAgg):
    """3D 网格/点云预览（matplotlib 3D 内嵌，可旋转缩放平移）。"""

    def __init__(self, parent=None, width: int = 6, height: int = 5,
                 dpi: int = 100):
        self.fig = Figure(figsize=(width, height), dpi=dpi)
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111, projection="3d")
        self.ax.set_xlabel("X"); self.ax.set_ylabel("Y")
        self.ax.set_zlabel("Z")
        self.fig.tight_layout()

    def load_mesh(self, mesh_path: str) -> bool:
        """加载 PLY/OBJ 网格并显示（open3d 读取）。"""
        import open3d as o3d
        if not os.path.exists(mesh_path):
            return False
        mesh = o3d.io.read_triangle_mesh(mesh_path)
        if not mesh.has_triangles():
            pcd = o3d.io.read_point_cloud(mesh_path)
            return self._plot_points(np.asarray(pcd.points))
        verts = np.asarray(mesh.vertices)
        tris = np.asarray(mesh.triangles)
        return self._plot_mesh(verts, tris)

    def show_text(self, message: str) -> None:
        """在预览区显示纯文本提示（无模型时替代空白画面）。

        实机问题（2026-10-17）：S1 失败时 ``80_export`` 不存在，预览面板
        保持初始空白，用户无法判断"是没跑完还是没产物"。
        """
        self.ax.clear()
        self.ax.set_axis_off()
        self.ax.text2D(0.5, 0.5, message, transform=self.ax.transAxes,
                       ha="center", va="center", fontsize=11,
                       color="#c62828", wrap=True)
        self.draw_idle()

    def _plot_mesh(self, verts, tris) -> bool:
        if len(verts) == 0:
            return False
        self.ax.clear()
        self.ax.plot_trisurf(verts[:, 0], verts[:, 1], verts[:, 2],
                             triangles=tris, shade=True, alpha=0.95)
        self._style_axes()
        self.draw_idle()
        return True

    def _plot_points(self, pts) -> bool:
        if len(pts) == 0:
            return False
        self.ax.clear()
        step = max(1, len(pts) // 20000)
        p = pts[::step]
        self.ax.scatter(p[:, 0], p[:, 1], p[:, 2], s=0.4, c="r", alpha=0.6)
        self._style_axes()
        self.draw_idle()
        return True

    def _style_axes(self) -> None:
        self.ax.set_xlabel("X"); self.ax.set_ylabel("Y")
        self.ax.set_zlabel("Z")
        self.ax.set_title("3D 模型预览（拖拽旋转 / 滚轮缩放）")
        self.fig.tight_layout()


class TextureView(QLabel):
    """纹理贴图查看（diffuse PNG/纹理文件）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setText("（无纹理）")
        self.setMinimumSize(320, 240)
        self.setStyleSheet("border: 1px solid #555; background: #222;")

    def show_texture(self, path: str) -> bool:
        if not path or not os.path.exists(path):
            self.setText("（无纹理）")
            return False
        pix = QPixmap(path)
        if pix.isNull():
            self.setText("（纹理无法加载）")
            return False
        self.setPixmap(pix.scaled(self.size(), Qt.KeepAspectRatio,
                                  Qt.SmoothTransformation))
        self.setScaledContents(False)
        return True


__all__ = ["MeshPreview", "TextureView"]
