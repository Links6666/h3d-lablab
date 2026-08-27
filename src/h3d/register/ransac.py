"""h3d.register.ransac : RANSAC 全局粗配准。

设计依据：
    《设计书》15.1 —— 用 FPFH 描述子 + RANSAC 估计初始变换，对初始位姿不敏感；
    《教学版》16.3 —— 全局粗配准 registration_ransac_based_on_feature_matching；
    《骨架分析》4.2 —— ransac.py 承担「RANSAC 粗配准」职责。

RANSAC 通过随机抽样一致，从含噪的 FPFH 特征对应中估计稳健的刚体变换，为 ICP
精配准提供良好初值。使用 mutual_filter 与边长 / 距离双重校验器过滤错误对应。
"""

from __future__ import annotations

from h3d.core import InputDataError, get_logger

log = get_logger("register.ransac")


def _o3d():
    import open3d as o3d

    return o3d


def global_registration(source, target, source_fpfh, target_fpfh,
                        max_correspondence_distance: float,
                        ransac_n: int = 4, max_iteration: int = 100000):
    """FPFH + RANSAC 全局粗配准（Open3D）。

    参数
    ----
    source / target              : 源 / 目标点云（须含法线）。
    source_fpfh / target_fpfh    : 对应的 FPFH 特征。
    max_correspondence_distance  : 最大对应距离（配置项 register.ransac_distance）。
    ransac_n                     : RANSAC 最小采样点数（默认 4）。
    max_iteration                : RANSAC 最大迭代（默认 100000）。

    返回
    ----
    RegistrationResult（含 4x4 transformation / fitness / inlier_rmse）。
    """
    for pcd, name in ((source, "source"), (target, "target")):
        if pcd is None or not pcd.has_points():
            raise InputDataError(f"{name} 点云为空（0 点），无法粗配准")
    if source_fpfh is None or target_fpfh is None:
        raise InputDataError("FPFH 特征缺失，请先调用 compute_fpfh")

    o3d = _o3d()
    reg = o3d.pipelines.registration
    result = reg.registration_ransac_based_on_feature_matching(
        source, target, source_fpfh, target_fpfh,
        mutual_filter=True,
        max_correspondence_distance=float(max_correspondence_distance),
        estimation_method=reg.TransformationEstimationPointToPoint(False),
        ransac_n=int(ransac_n),
        checkers=[
            reg.CorrespondenceCheckerBasedOnEdgeLength(0.9),
            reg.CorrespondenceCheckerBasedOnDistance(
                float(max_correspondence_distance)),
        ],
        criteria=reg.RANSACConvergenceCriteria(
            max_iteration=int(max_iteration), confidence=0.999),
    )
    log.info("RANSAC 粗配准：fitness=%.3f, inlier_rmse=%.4f",
             result.fitness, result.inlier_rmse)
    return result


__all__ = ["global_registration"]
