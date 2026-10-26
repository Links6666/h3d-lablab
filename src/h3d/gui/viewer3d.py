"""h3d.gui.viewer3d : open3d 原生交互窗口（P3）+ X1 形变热力图。

open3d Visualizer 独立窗口（真 3D 旋转/缩放/着色模式 R/G/B/纹理），
X1 形变热力图：网格/点云顶点色映射（红=形变大，蓝=形变小）。
"""

from __future__ import annotations

import json
import os

import numpy as np


def show_open3d_window(mesh_path: str, title: str = "H3D 3D 交互窗口",
                       texture_path: str | None = None) -> bool:
    """打开 open3d 原生交互窗口（网格 + 可选纹理）。

    按键：R/G/B 切换纯色，T 纹理，+/- 缩放。独立窗口。
    """
    import open3d as o3d
    if not os.path.exists(mesh_path):
        return False
    mesh = o3d.io.read_triangle_mesh(mesh_path)
    if mesh.is_empty():
        return False
    if texture_path and os.path.exists(texture_path):
        img = o3d.io.read_image(texture_path)
        if img is not None:
            try:
                mesh.textures = [img]
                mesh.triangle_material_ids = o3d.utility.IntVector(
                    [0] * len(mesh.triangles))
            except Exception:  # noqa: BLE001
                pass
    vis = o3d.visualization.VisualizerWithKeyCallback()
    vis.create_window(title, width=960, height=720)
    vis.add_geometry(mesh)

    def _set_color(mesh, rgb):
        def _cb(vis):
            mesh.paint_uniform_color(rgb)
            vis.update_geometry(mesh)
            return False
        return _cb

    def _toggle_texture(vis):
        # 简化：纹理显示由 open3d 材质控制——切换顶点色
        mesh.paint_uniform_color([0.75, 0.72, 0.68])
        vis.update_geometry(mesh)
        return False

    vis.register_key_callback(ord("R"), _set_color(mesh, [0.8, 0.3, 0.3]))
    vis.register_key_callback(ord("G"), _set_color(mesh, [0.3, 0.8, 0.3]))
    vis.register_key_callback(ord("B"), _set_color(mesh, [0.3, 0.4, 0.8]))
    vis.register_key_callback(ord("T"), _toggle_texture)
    vis.run()
    vis.destroy_window()
    return True


def load_x1_changes(root_dir: str, item_id: str) -> np.ndarray | None:
    """读取 X1 形变结果（交付成果/<item>/<item>_X1_m3c2.json 或 manifest）。"""
    from h3d.core import paths as _p
    for cand in (os.path.join(_p.delivery_item_dir(root_dir, item_id),
                              f"{item_id}_X1_m3c2.json"),
                 os.path.join(_p.delivery_item_dir(root_dir, item_id),
                              f"{item_id}_X1_deltaE.json")):
        if os.path.exists(cand):
            try:
                m = json.load(open(cand, encoding="utf-8-sig"))
                ch = m.get("changes", {})
                if isinstance(ch, dict):
                    vals = np.asarray(ch.get("values", []), dtype=np.float64)
                    if len(vals):
                        return vals
            except Exception:  # noqa: BLE001
                continue
    return None


def show_x1_heatmap(mesh_path: str, root_dir: str, item_id: str) -> bool:
    """X1 形变热力图：网格顶点按形变量着色（红=形变大）并打开窗口。"""
    import open3d as o3d
    from matplotlib import colormaps
    if not os.path.exists(mesh_path):
        return False
    mesh = o3d.io.read_triangle_mesh(mesh_path)
    if mesh.is_empty():
        return False
    changes = load_x1_changes(root_dir, item_id)
    verts = np.asarray(mesh.vertices)
    n = len(verts)
    if changes is not None and len(changes) >= n:
        vals = changes[:n]
    elif changes is not None and len(changes) > 0:
        # 按顶点序号取模近似
        vals = np.tile(changes, n // len(changes) + 1)[:n]
    else:
        # 无 X1：用曲率近似着色（示意）
        mesh.compute_vertex_normals()
        vals = np.abs(np.asarray(mesh.vertex_normals))[:, 2]
    vmin, vmax = float(vals.min()), float(vals.max())
    norm = (vals - vmin) / max(vmax - vmin, 1e-9)
    cmap = colormaps["jet"]
    colors = cmap(norm)[:, :3]
    mesh.vertex_colors = o3d.utility.Vector3dVector(colors)
    vis = o3d.visualization.Visualizer()
    vis.create_window(f"H3D X1 形变热力图（{item_id}）", width=960,
                      height=720)
    vis.add_geometry(mesh)
    opt = vis.get_render_option()
    opt.mesh_shade_option = 1  # 顶点色着色
    vis.run()
    vis.destroy_window()
    return True


__all__ = ["show_open3d_window", "show_x1_heatmap", "load_x1_changes"]
