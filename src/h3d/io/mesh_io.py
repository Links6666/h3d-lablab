"""h3d.io.mesh_io : 网格模型读写与格式适配（L1 适配层）。

设计依据：
    《设计书》10.1 —— L1 适配层隔离外部格式与工具，业务层不感知具体读写实现；
    《软著方案》2.3.1 —— 业务代码不直接 import open3d，只调用 io 层抽象接口；
    《设计书》M6（37 章）—— 标准格式导出 obj / ply / glb，供主流软件读取。

职责边界：本模块只负责「把外部网格格式读入为统一数据模型（Open3D TriangleMesh），
并把统一数据模型写回外部格式」，**不做**网格修复 / 简化 / UV 展开（那是重建与后处理
子包的事）。完整性校验在此完成：
    * 文件缺失 / 扩展名不支持 / 空网格 → :class:`InputDataError`（E100，可恢复）；
    * 写出格式不支持 / 写盘失败       → :class:`ExportError`（E601，可恢复）。

依赖方向（《管线骨架构建分析》5.2）：io → core（仅 exceptions / logging），不反向。
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np

from h3d.core import ExportError, InputDataError, get_logger

log = get_logger("io.mesh_io")

MESH_READ_EXT = {".obj", ".ply", ".stl", ".glb", ".gltf"}
MESH_WRITE_EXT = {".obj", ".ply", ".stl", ".glb"}


def _ext(path: str) -> str:
    return os.path.splitext(str(path))[1].lower()


def _o3d():
    """惰性导入 Open3D（适配层是系统中唯一直接依赖 Open3D 的位置）。"""
    import open3d as o3d

    return o3d


def load_mesh(path: str):
    """读取单个网格文件，返回 Open3D TriangleMesh（统一数据模型）。

    支持 obj / ply / stl / glb / gltf。文件缺失、扩展名不支持、空网格
    抛 :class:`InputDataError`（E100）。
    """
    path = str(path)
    if not os.path.exists(path):
        raise InputDataError(f"网格文件不存在: {path}")
    ext = _ext(path)
    if ext not in MESH_READ_EXT:
        raise InputDataError(
            f"不支持的网格格式 {ext!r}（支持 {sorted(MESH_READ_EXT)}）: {path}"
        )
    mesh = _o3d().io.read_triangle_mesh(path)
    if mesh is None or not mesh.has_vertices():
        raise InputDataError(f"空网格（0 顶点）: {path}")
    log.info("读取网格 %s：%d 顶点 / %d 三角面",
             path, len(mesh.vertices), len(mesh.triangles))
    return mesh


def save_mesh(mesh, path: str) -> None:
    """把 Open3D TriangleMesh 写回外部格式（obj / ply / stl / glb）。

    目标格式不支持或写盘失败抛 :class:`ExportError`（E601）。父目录不存在时自动创建。
    """
    path = str(path)
    ext = _ext(path)
    if ext not in MESH_WRITE_EXT:
        raise ExportError(
            f"不支持的网格写出格式 {ext!r}（支持 {sorted(MESH_WRITE_EXT)}）: {path}"
        )
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    # NaN/Inf 顶点清理：open3d 写出含非有限顶点的网格会失败（E601 常见根因）
    o3d = _o3d()
    verts = np.asarray(mesh.vertices)
    if verts.size and not np.isfinite(verts).all():
        n_bad = int((~np.isfinite(verts)).any(axis=1).sum())
        log.warning("网格含非有限顶点 %d 个（写出前清理）", n_bad)
        finite = np.isfinite(verts).all(axis=1)
        valid_idx = np.where(finite)[0]
        remap = -np.ones(len(verts), dtype=np.int64)
        remap[valid_idx] = np.arange(len(valid_idx))
        keep_tri = []
        for tri in np.asarray(mesh.triangles):
            a, b, c = remap[tri]
            if a >= 0 and b >= 0 and c >= 0:
                keep_tri.append([int(a), int(b), int(c)])
        mesh = o3d.geometry.TriangleMesh(
            o3d.utility.Vector3dVector(verts[finite]))
        if keep_tri:
            mesh.triangles = o3d.utility.Vector3iVector(
                np.asarray(keep_tri, dtype=np.int32))
    # STL 格式要求顶点法线；缺失时在副本上补算，避免污染调用方网格
    if ext == ".stl" and not mesh.has_vertex_normals():
        mesh = o3d.geometry.TriangleMesh(mesh)
        mesh.compute_vertex_normals()
    ok = o3d.io.write_triangle_mesh(path, mesh)
    if not ok:
        raise ExportError(
            f"网格写盘失败（E601）：{path}\n可能原因：路径含特殊字符/磁盘"
            "只读或空间不足/文件被占用。请尝试更换项目根目录后重试。")
    log.info("写出网格 %s：%d 顶点 / %d 三角面",
             path, len(mesh.vertices), len(mesh.triangles))


__all__ = [
    "MESH_READ_EXT",
    "MESH_WRITE_EXT",
    "load_mesh",
    "save_mesh",
]
