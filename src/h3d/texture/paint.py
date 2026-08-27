"""h3d.texture.paint : 顶点色纹理映射（M4 网格化与纹理，带色模型交付）。

设计依据：
    《设计书》§35 —— M4 网格化与纹理：UV 展开并映射纹理，输出带纹理模型至 50_tex；
    《软著方案》2.4 —— M4 texture 输出带色模型（ply / obj / glb），无纹理时降级
        输出几何模型并标注。

实现要点：
    1. 顶点色映射是本子包的核心工程路径（Open3D 原生支持顶点色读写，零重依赖）：
       网格每个顶点取参考点云中最近邻点的颜色（RGB），生成带色网格。
    2. 用 scipy.cKDTree 批量最近邻查询，1 万顶点级网格毫秒级完成。
    3. 点云无颜色时返回 False（由调用方降级输出几何模型并标注，软著方案 2.4）。
"""

from __future__ import annotations

import numpy as np

from h3d.core import get_logger

log = get_logger("texture.paint")


def has_colors(pcd) -> bool:
    """参考点云是否带 RGB 颜色。"""
    return bool(pcd.has_colors())


def paint_vertex_colors(mesh, pcd) -> bool:
    """把参考点云的颜色映射到网格顶点（原地修改），返回是否成功上色。

    对网格每个顶点用 scipy.cKDTree 查询参考点云的最近邻点，取其 RGB 颜色。
    点云无颜色时返回 False（调用方降级输出几何模型）；网格无顶点时直接返回 False。

    参数
    ----
    mesh: TriangleMesh（M3 重建产物，将被写入顶点色）。
    pcd:  参考点云（M2 配准结果，需带 RGB）。

    返回
    ----
    True 已上色；False 未上色（点云缺颜色 / 网格空）。
    """
    if not has_colors(pcd) or len(mesh.vertices) == 0:
        return False

    from scipy.spatial import cKDTree

    verts = np.asarray(mesh.vertices, dtype=np.float64)
    pts = np.asarray(pcd.points, dtype=np.float64)
    colors = np.asarray(pcd.colors, dtype=np.float64)  # 0~1

    tree = cKDTree(pts)
    _dist, idx = tree.query(verts, k=1)
    mesh.vertex_colors = _o3d().utility.Vector3dVector(colors[idx])
    log.info("顶点色映射完成：%d 顶点 <- %d 参考点", len(verts), len(pts))
    return True


def _o3d():
    import open3d

    return open3d


__all__ = ["has_colors", "paint_vertex_colors"]
