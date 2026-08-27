"""h3d.register.icp : point-to-plane ICP 精配准。

设计依据：
    《设计书》15.2 —— point-to-plane ICP 多尺度迭代精化，输出配准残差 RMS；
    《教学版》16.4 —— ICP 精配准 registration_icp；
    《骨架分析》4.2 —— icp.py 承担「ICP 精配准」职责。

ICP（Iterative Closest Point）在粗配准初值上迭代优化，point-to-plane 变体最小化
点到目标面切平面的距离平方和，比 point-to-point 更贴合曲面，收敛更快、精度更高。
"""

from __future__ import annotations

from h3d.core import InputDataError, get_logger

log = get_logger("register.icp")


def _o3d():
    import open3d as o3d

    return o3d


def icp_refine(source, target, init, max_correspondence_distance: float,
               max_iteration: int = 2000, point_to_plane: bool = True):
    """ICP 精配准（Open3D registration_icp）。

    参数
    ----
    source / target             : 源 / 目标点云（point-to-plane 需目标含法线）。
    init                        : 初始变换（4x4，通常来自 RANSAC 粗配准）。
    max_correspondence_distance : 最大对应距离（配置项 register.icp_distance）。
    max_iteration               : 最大迭代（配置项 register.max_iter，默认 2000）。
    point_to_plane              : True 用 point-to-plane，False 用 point-to-point。

    返回
    ----
    RegistrationResult（含精化后 transformation / fitness / inlier_rmse）。
    """
    for pcd, name in ((source, "source"), (target, "target")):
        if pcd is None or not pcd.has_points():
            raise InputDataError(f"{name} 点云为空（0 点），无法精配准")
    if init is None:
        raise InputDataError("初始变换 init 缺失，请先做粗配准")

    o3d = _o3d()
    reg = o3d.pipelines.registration
    if point_to_plane and not target.has_normals():
        log.warning("目标点云缺法线，point-to-plane 将退化为 point-to-point")
        estimation = reg.TransformationEstimationPointToPoint()
    else:
        estimation = (
            reg.TransformationEstimationPointToPlane() if point_to_plane
            else reg.TransformationEstimationPointToPoint()
        )
    result = reg.registration_icp(
        source, target, float(max_correspondence_distance), init, estimation,
        reg.ICPConvergenceCriteria(max_iteration=int(max_iteration),
                                   relative_fitness=1e-6, relative_rmse=1e-6))
    log.info("ICP 精配准：fitness=%.3f, inlier_rmse=%.5f",
             result.fitness, result.inlier_rmse)
    return result


__all__ = ["icp_refine"]
