"""h3d.preprocess.normal_estimate : 法线估计与法线一致化。

设计依据：
    《设计书》32.2 —— M1 预处理做坐标归一化与朝向校正，为后续 Poisson 重建提供一致法线；
    《教学版》15.3 —— 法线估计 estimate_normals + orient_normals_consistent_tangent_plane；
    《骨架分析》4.2 —— normal_estimate.py 承担「法线估计」职责。

职责边界：只对统一数据模型（PointCloud）做<b>法线估计与定向</b>，输入输出均为 PointCloud。
法线是 Poisson 重建、ICP 精配准（point-to-plane）的必需属性，本模块保证法线方向一致。
"""

from __future__ import annotations

from typing import Optional

from h3d.core import InputDataError, get_logger

log = get_logger("preprocess.normal_estimate")


def _o3d():
    import open3d as o3d

    return o3d


def estimate_normals(pcd, max_nn: int = 30, radius: Optional[float] = None):
    """估计点云法线（Open3D estimate_normals，原地修改后返回）。

    用 Hybrid 搜索（radius + max_nn）；radius 未提供时退化为 KNN(max_nn)。
    法线朝向在下一阶段由 orient_normals_consistent 统一。

    参数
    ----
    max_nn  : 近邻点数上限（配置项 preprocess.normal_estimate_k，默认 30）。
    radius  : 搜索半径（可选；默认 None 时用 KNN）。

    返回
    ----
    已带法线的 PointCloud（原地修改）。
    """
    if pcd is None or not pcd.has_points():
        raise InputDataError("输入点云为空（0 点），无法估计法线")
    o3d = _o3d()
    if radius is not None and float(radius) > 0:
        search = o3d.geometry.KDTreeSearchParamHybrid(
            radius=float(radius), max_nn=int(max_nn))
    else:
        search = o3d.geometry.KDTreeSearchParamKNN(knn=int(max_nn))
    pcd.estimate_normals(search, fast_normal_computation=True)
    log.info("法线估计完成：%d 点（max_nn=%d, radius=%s）",
             len(pcd.points), max_nn, radius)
    return pcd


def orient_normals_consistent(pcd, k: int = 15):
    """法线一致化（Open3D orient_normals_consistent_tangent_plane，原地修改）。

    以切平面传播方式统一法线朝向，避免 Poisson 重建出现「翻面」。调用前须先
    estimate_normals。
    """
    if pcd is None or not pcd.has_points():
        raise InputDataError("输入点云为空（0 点），无法定向法线")
    if not pcd.has_normals():
        raise InputDataError("点云尚未估计法线，请先调用 estimate_normals")
    pcd.orient_normals_consistent_tangent_plane(int(k))
    log.info("法线一致化完成（k=%d）", k)
    return pcd


__all__ = ["estimate_normals", "orient_normals_consistent"]
