"""h3d.register.feature : FPFH 特征提取。

设计依据：
    《设计书》15.1 —— 粗配准用 FPFH 描述子提取关键点特征，对初始位姿不敏感；
    《教学版》16.2 —— 计算 FPFH 特征 compute_fpfh_feature；
    《骨架分析》4.2 —— feature.py 承担「FPFH 特征提取」职责。

FPFH（Fast Point Feature Histogram）以法线为基础，编码点云局部邻域的几何分布，
用于 RANSAC 全局粗配准的特征匹配。本模块在输入点云缺失法线时自动补齐（正常由
preprocess 层提供，此处兜底）。
"""

from __future__ import annotations

from h3d.core import InputDataError, get_logger

log = get_logger("register.feature")


def _o3d():
    import open3d as o3d

    return o3d


def _check(pcd):
    if pcd is None or not pcd.has_points():
        raise InputDataError("输入点云为空（0 点），无法计算特征")


def ensure_normals(pcd, radius: float, max_nn: int = 30):
    """法线缺失时自动补齐（FPFH 前提）。原地修改后返回。"""
    _check(pcd)
    if not pcd.has_normals():
        o3d = _o3d()
        pcd.estimate_normals(
            o3d.geometry.KDTreeSearchParamHybrid(radius=float(radius),
                                                 max_nn=int(max_nn)))
        log.info("点云缺法线，已补齐（radius=%.4f, max_nn=%d）", radius, max_nn)
    return pcd


def compute_fpfh(pcd, radius: float, max_nn: int = 100):
    """计算 FPFH 特征（Open3D compute_fpfh_feature）。

    参数
    ----
    radius : FPFH 特征半径（配置项 register.fpfh_radius）。
    max_nn : 近邻点上限（默认 100）。

    返回
    ----
    Open3D Feature（FPFH 描述子，33 维直方图）。
    """
    _check(pcd)
    ensure_normals(pcd, radius * 2.0, 30)
    o3d = _o3d()
    feature = o3d.pipelines.registration.compute_fpfh_feature(
        pcd, o3d.geometry.KDTreeSearchParamHybrid(radius=float(radius),
                                                  max_nn=int(max_nn)))
    log.info("FPFH 特征计算完成：%d 点（radius=%.4f）", len(pcd.points), radius)
    return feature


__all__ = ["ensure_normals", "compute_fpfh"]
