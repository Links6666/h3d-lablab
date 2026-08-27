"""h3d.preprocess.filter : 点云去噪（统计 / 半径离群点去除）。

设计依据：
    《设计书》32.2 —— M1 预处理用 StatisticalOutlierRemoval 去除离群点；
    《教学版》15.2 —— 统计离群去噪 remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)；
    《骨架分析》4.2 —— filter.py 承担「去噪滤波」职责。

职责边界：本模块只对统一数据模型（Open3D PointCloud）做<b>离群点去除</b>，输入输出均为
PointCloud 对象；文件读写由 io 层负责。去噪后若点云为空，抛 InputDataError(E100)。
"""

from __future__ import annotations

from h3d.core import InputDataError, get_logger

log = get_logger("preprocess.filter")


def _o3d():
    """惰性导入 Open3D（业务层经 io 层拿到 PointCloud，此处仅做几何运算）。"""
    import open3d as o3d

    return o3d


def _check(pcd, op: str):
    """点云为空时抛 E100（去噪可能把所有点都判为离群点）。"""
    if pcd is None or not pcd.has_points():
        raise InputDataError(f"{op}后点云为空（0 点）")


def statistical_outlier_removal(pcd, nb_neighbors: int = 20, std_ratio: float = 2.0):
    """统计离群点去除（Open3D remove_statistical_outlier）。

    对每个点统计其 K 近邻距离的均值与标准差，剔除均值偏离整体超
    ``std_ratio`` 倍标准差的离群点，适用于去除扫描噪声与飞点。

    参数
    ----
    nb_neighbors : 近邻点数（默认 20）。
    std_ratio    : 标准差倍数阈值（默认 2.0，越小越激进）。

    返回
    ----
    去噪后的 PointCloud。
    """
    if pcd is None or not pcd.has_points():
        raise InputDataError("输入点云为空（0 点），无法去噪")
    before = len(pcd.points)
    filtered, _ = pcd.remove_statistical_outlier(
        nb_neighbors=int(nb_neighbors), std_ratio=float(std_ratio))
    _check(filtered, "统计离群去噪")
    log.info("统计离群去噪：%d -> %d 点（nb=%d, std=%.2f）",
             before, len(filtered.points), nb_neighbors, std_ratio)
    return filtered


def radius_outlier_removal(pcd, nb_points: int = 16, radius: float = 0.01):
    """半径离群点去除（Open3D remove_radius_outlier）。

    剔除在半径 ``radius`` 球体内近邻数少于 ``nb_points`` 的孤立点，适用于
    去除明显悬浮的飞点。

    返回
    ----
    去噪后的 PointCloud。
    """
    if pcd is None or not pcd.has_points():
        raise InputDataError("输入点云为空（0 点），无法去噪")
    before = len(pcd.points)
    filtered, _ = pcd.remove_radius_outlier(
        nb_points=int(nb_points), radius=float(radius))
    _check(filtered, "半径离群去噪")
    log.info("半径离群去噪：%d -> %d 点（nb=%d, r=%.4f）",
             before, len(filtered.points), nb_points, radius)
    return filtered


__all__ = ["statistical_outlier_removal", "radius_outlier_removal"]
