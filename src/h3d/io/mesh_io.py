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


def make_mesh(vertices, triangles, normals=None):
    """从数组构造网格（统一数据模型）。

    为什么需要它：UV 布局里的 ``uv``/``faces`` 属于**展开后**的拓扑（接缝处
    顶点被拆分，顶点数 ≥ 原始网格）。图集继承要用同一套拓扑重建几何，
    直接读原始 PLY 会让面索引越界。由适配层提供构造函数，业务层就不必
    import Open3D（分层纪律）。
    """
    import numpy as np

    o3d = _o3d()
    verts = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
    tris = np.asarray(triangles, dtype=np.int32).reshape(-1, 3)
    if not len(verts):
        raise InputDataError("构造网格失败：顶点为空")
    if not len(tris):
        raise InputDataError("构造网格失败：三角面为空")
    if int(tris.max()) >= len(verts):
        raise InputDataError(
            f"构造网格失败：面索引 {int(tris.max())} 越界（仅 {len(verts)} 顶点）")
    mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(verts),
                                     o3d.utility.Vector3iVector(tris))
    if normals is not None:
        n = np.asarray(normals, dtype=np.float64).reshape(-1, 3)
        if len(n) != len(verts):
            raise InputDataError(
                f"构造网格失败：法线数 {len(n)} 与顶点数 {len(verts)} 不符")
        mesh.vertex_normals = o3d.utility.Vector3dVector(n)
    else:
        mesh.compute_vertex_normals()
    return mesh


#: 表面采样的默认种子。固定值是**判据的一部分**：凡是会写进报表被引用的
#: 采样都必须可复现（见 :func:`sample_surface`）。
SAMPLE_SEED = 20261104


def seed_sampling(seed: int = SAMPLE_SEED) -> None:
    """固定 Open3D 的全局随机源（供「先定种子、再自己调采样」的老调用点用）。

    新代码应该用 :func:`sample_surface`——它把「定种子 + 采样」绑在一起，
    没法只做一半。这个函数存在只是为了不毁掉已经在用的调用点（对齐精化）。
    """
    try:
        _o3d().utility.random.seed(int(seed))
    except Exception:  # noqa: BLE001 - 不影响主流程
        pass


def sample_surface(mesh, n_samples: int, seed: int = SAMPLE_SEED):
    """**可复现的**网格表面均匀采样，返回 ``PointCloud``。

    为什么必须定种子（2026-11-04 真机）
    -----------------------------------
    ``TriangleMesh.sample_points_uniformly`` 没有 ``seed`` 参数，用的是 Open3D
    进程级随机源。同一份输入跑两次，采到的表面点不同，最近邻距离分布就变：

    | 运行 | ``runner.data_source`` | M5 中误差（mm） |
    |---|---|---|
    | 1 | auto | 86.7811 |
    | 2 | auto | 87.4155 |
    | 3 | laser | 87.0148 |

    三者等价，差异全部来自采样。**小于 1% 的漂移最危险**：小到不会触发任何
    断言，又大到让「昨天那张表和今天这张表对不上」。这两个数字正是
    ``accuracy.csv`` / HTML / 论文表里要被人引用并追问「怎么重跑出来」的。

    同一个坑 2026-11-03 在**对齐精化**（``ctrl_points.refine_mesh_to_points``）
    已经踩过一次并修好了；当时只修了那一条路径。所以采样入口收敛到这里，
    新代码不要再直接调 ``mesh.sample_points_uniformly``。

    参数
    ----
    mesh:      Open3D TriangleMesh（此函数是适配层，允许直接持有它）。
    n_samples: 采样点数。
    seed:      随机种子；换种子会换样本，**同种子必须同结果**。
    """
    o3d = _o3d()
    if mesh is None or len(mesh.triangles) == 0 and len(mesh.vertices) == 0:
        raise InputDataError("采样失败：网格为空")
    o3d.utility.random.seed(int(seed))
    return mesh.sample_points_uniformly(int(n_samples))


__all__ = [
    "MESH_READ_EXT",
    "MESH_WRITE_EXT",
    "load_mesh",
    "save_mesh",
    "make_mesh",
    "sample_surface",
    "seed_sampling",
    "SAMPLE_SEED",
]
