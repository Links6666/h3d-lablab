"""h3d.io.pointcloud_io : 点云读写与格式适配（L1 适配层）。

设计依据：
    《设计书》10.1 —— L1 适配层隔离外部格式与工具，业务层不感知具体读写实现；
    《软著方案》2.3.1 —— 业务代码不直接 import open3d，只调用 io 层提供的抽象接口；
    《教学版》14 —— Open3D(MIT) 读写 PLY/OBJ/PCD，规避 plyfile(GPL) 陷阱；
    《教学版》3.3 —— laspy(BSD-2) 读写 LAS/LAZ 激光雷达数据。

职责边界：本模块只负责「把外部点云格式读入为统一数据模型（Open3D PointCloud），并把
统一数据模型写回外部格式」，**不做**去噪 / 降采样 / 法线估计（那是 preprocess 子包的事）。
格式与完整性校验在此完成：
    * 文件缺失 / 扩展名不支持 / 空点云 → :class:`InputDataError`（E100，可恢复）；
    * 写出格式不支持 / 写盘失败       → :class:`ExportError`（E601，可恢复）。

依赖方向（《管线骨架构建分析》5.2）：io → core（仅 exceptions / logging），不反向。
"""

from __future__ import annotations

import glob
import os
from typing import List, Optional, Tuple

import numpy as np

from h3d.core import ExportError, InputDataError, get_logger

log = get_logger("io.pointcloud_io")

# 按扩展名路由的格式清单
POINTCLOUD_READ_EXT = {".ply", ".pcd", ".xyz", ".pts", ".las", ".laz"}
POINTCLOUD_WRITE_EXT = {".ply", ".pcd", ".xyz"}
_LAS_EXT = {".las", ".laz"}
_TEXT_XYZ_EXT = {".xyz", ".pts"}


def _ext(path: str) -> str:
    """取小写扩展名（含点），如 ``".ply"``。"""
    return os.path.splitext(str(path))[1].lower()


def _o3d():
    """惰性导入 Open3D。

    io 是系统中**唯一**直接依赖 Open3D 的位置（适配器模式）。惰性导入使
    ``import h3d.io`` 本身不触发 Open3D 加载，业务层单元测试可 mock 本模块。
    """
    import open3d as o3d

    return o3d


def _empty() -> "object":
    """构造一个空 Open3D PointCloud 供内部复用。"""
    return _o3d().geometry.PointCloud()


# ---------------------------------------------------------------------------
# 各格式读取
# ---------------------------------------------------------------------------
def _read_open3d(path: str):
    """经 Open3D 读取 ply / pcd 二进制点云。"""
    return _o3d().io.read_point_cloud(path)


def _read_xyz(path: str):
    """读取 ASCII XYZ 文本点云（每行 ``x y z`` 或 ``x y z r g b``）。"""
    try:
        data = np.loadtxt(path, dtype=np.float64)
    except (OSError, ValueError) as exc:
        raise InputDataError(f"XYZ 点云解析失败: {path}") from exc
    if data.ndim == 1:
        data = data.reshape(1, -1)
    if data.ndim != 2 or data.shape[1] < 3:
        raise InputDataError(f"XYZ 文件列数不足 3: {path}")
    pcd = _empty()
    pcd.points = _o3d().utility.Vector3dVector(data[:, :3])
    if data.shape[1] >= 6:
        # RGB 取值约定为 0~255
        pcd.colors = _o3d().utility.Vector3dVector(np.clip(data[:, 3:6], 0, 255) / 255.0)
    return pcd


def _read_las(path: str):
    """经 laspy 读取 LAS/LAZ，转换为统一 Open3D PointCloud。"""
    try:
        import laspy
    except ImportError as exc:
        raise InputDataError(
            f"读取 LAS/LAZ 需要 laspy（BSD-2），请先安装: {path}"
        ) from exc
    try:
        las = laspy.read(path)
    except Exception as exc:
        raise InputDataError(f"LAS/LAZ 解析失败: {path}") from exc
    xyz = np.vstack((las.x, las.y, las.z)).transpose().astype(np.float64)
    pcd = _empty()
    pcd.points = _o3d().utility.Vector3dVector(xyz)
    # LAS 颜色通道为 16-bit（0~65535）
    if all(hasattr(las, a) for a in ("red", "green", "blue")):
        rgb = np.vstack((las.red, las.green, las.blue)).transpose().astype(np.float64)
        if rgb.size:
            scale = 65535.0 if np.max(rgb) > 255 else 255.0
            pcd.colors = _o3d().utility.Vector3dVector(rgb / scale)
    return pcd


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
def load_point_cloud(path: str):
    """读取单个点云文件，返回 Open3D PointCloud（统一数据模型）。

    按扩展名路由：ply / pcd 走 Open3D，xyz / pts 走文本解析，las / laz 走 laspy。
    文件缺失、扩展名不支持、空点云均抛 :class:`InputDataError`（E100）。
    """
    path = str(path)
    if not os.path.exists(path):
        raise InputDataError(f"点云文件不存在: {path}")
    ext = _ext(path)
    if ext not in POINTCLOUD_READ_EXT:
        raise InputDataError(
            f"不支持的点云格式 {ext!r}（支持 {sorted(POINTCLOUD_READ_EXT)}）: {path}"
        )
    if ext in _LAS_EXT:
        pcd = _read_las(path)
    elif ext in _TEXT_XYZ_EXT:
        pcd = _read_xyz(path)
    else:
        pcd = _read_open3d(path)

    if pcd is None or not pcd.has_points():
        raise InputDataError(f"空点云（0 点）: {path}")
    log.info("读取点云 %s：%d 点", path, len(pcd.points))
    return pcd


def load_all(pattern: str):
    """按 glob 模式批量读取点云，返回 ``[(路径, 点云), ...]``（教学版 14.2）。

    用于多站 / 多文件点云批量接入；无任何匹配时抛 :class:`InputDataError`。
    """
    paths = sorted(glob.glob(str(pattern)))
    if not paths:
        raise InputDataError(f"未匹配到任何点云: {pattern}")
    loaded: List[Tuple[str, object]] = []
    for p in paths:
        loaded.append((p, load_point_cloud(p)))
    return loaded


# ---------------------------------------------------------------------------
# 写出
# ---------------------------------------------------------------------------
def save_point_cloud(pcd, path: str) -> None:
    """把 Open3D PointCloud 写回外部格式（ply / pcd / xyz）。

    目标格式不支持或写盘失败抛 :class:`ExportError`（E601）。父目录不存在时自动创建。
    """
    path = str(path)
    ext = _ext(path)
    if ext not in POINTCLOUD_WRITE_EXT:
        raise ExportError(
            f"不支持的点云写出格式 {ext!r}（支持 {sorted(POINTCLOUD_WRITE_EXT)}）: {path}"
        )
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    if ext in _TEXT_XYZ_EXT:
        _write_xyz(pcd, path)
    else:
        ok = _o3d().io.write_point_cloud(path, pcd)
        if not ok:
            raise ExportError(f"点云写盘失败: {path}")
    log.info("写出点云 %s：%d 点", path, len(pcd.points))


def _write_xyz(pcd, path: str) -> None:
    """写 ASCII XYZ（仅坐标列，6 位小数）。"""
    pts = np.asarray(pcd.points, dtype=np.float64)
    np.savetxt(path, pts, fmt="%.6f")


__all__ = [
    "POINTCLOUD_READ_EXT",
    "POINTCLOUD_WRITE_EXT",
    "load_point_cloud",
    "load_all",
    "save_point_cloud",
]
