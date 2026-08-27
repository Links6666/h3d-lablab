"""h3d.register : 异源点云配准（L2 业务层 / S2 阶段）—— FPFH+RANSAC 粗配准 + ICP 精配准。

设计依据：
    《设计书》15 / 33 —— M2 配准：FPFH+RANSAC 全局粗配准 + point-to-plane ICP 精配准，
        输出配准残差 RMS 作为质量指标；
    《教学版》16 —— 两段式配准思路（全局粗配准 + ICP 精配准）；
    《骨架分析》4.2 —— register 子包承担「FPFH 特征 / RANSAC / ICP / 误差评估」职责。

对外暴露四个子模块的函数，并提供 :func:`run` 两段式配准流水线（配置驱动）。输入为两片
异源点云（Open3D PointCloud），输出统一配准结果字典（变换矩阵 + fitness + inlier_rmse）。
文件读写由 io 层负责，本层只做几何配准。

典型用法::

    from h3d.io import load_point_cloud
    from h3d.core import load
    from h3d.register import run

    cfg = load("configs/example.yaml")
    src = load_point_cloud("10_clean/luohan_001_s01.ply")
    tgt = load_point_cloud("10_clean/luohan_001_s02.ply")
    res = run(src, tgt, cfg.register)          # {"transformation": T, "fitness": ..., ...}
"""

from __future__ import annotations

import numpy as np

from h3d.core import RegistrationError, RegisterConfig, get_logger

from .feature import compute_fpfh, ensure_normals
from .ransac import global_registration
from .icp import icp_refine
from .evaluate import (
    evaluate_registration,
    fitness,
    inlier_rmse,
    correspondence_count,
)

log = get_logger("register")

# 最低 fitness 阈值（内点占比）。低于此视为「重叠不足 / 配准失败」，见《设计书》33.4。
# 正确配准 ICP fitness 通常接近 1.0，无重叠时 < 0.2，阈值取 0.2 可稳健区分二者
# （并与 WW/T 0117「相邻站重叠度 ≥ 30%」的精度基线同量级）。
_MIN_FITNESS = 0.2
# 粗配准失败回退时，对应距离放大倍数（《设计书》33.4「回退更粗半径重试一次」）
_FALLBACK_DISTANCE_RATIO = 2.0


def _ransac_with_fallback(source, target, source_fpfh, target_fpfh,
                          max_correspondence_distance: float):
    """RANSAC 粗配准，失败时回退更粗对应距离重试一次。"""
    result = global_registration(source, target, source_fpfh, target_fpfh,
                                 max_correspondence_distance)
    if result.fitness < _MIN_FITNESS:
        coarser = max_correspondence_distance * _FALLBACK_DISTANCE_RATIO
        log.warning("粗配准 fitness=%.3f 低于阈值，回退更粗距离 %.4f 重试",
                    result.fitness, coarser)
        result = global_registration(source, target, source_fpfh, target_fpfh,
                                     coarser)
    return result


def run(source, target, cfg: RegisterConfig) -> dict:
    """两段式异源点云配准（FPFH+RANSAC 粗配准 + ICP 精配准）。

    流程：法线补齐 + FPFH 特征 → RANSAC 全局粗配准（失败回退更粗距离）→ ICP
    精配准 → 质量评估。粗配准 fitness 仍低于阈值时抛
    :class:`RegistrationError`（E200，重叠不足）。

    参数
    ----
    source / target : 源 / 目标点云（Open3D PointCloud）。
    cfg             : RegisterConfig（fpfh_radius / ransac_distance /
                      icp_distance / max_iter）。

    返回
    ----
    dict，含 ``transformation``（4x4 刚体变换）、``fitness``、``inlier_rmse``、
    ``correspondence_count`` 与 ``ransac_fitness``。
    """
    if source is None or target is None or not source.has_points() \
            or not target.has_points():
        from h3d.core import InputDataError
        raise InputDataError("配准输入点云为空（0 点）")

    # 1) FPFH 特征（内部自动补齐法线）
    f_src = compute_fpfh(source, cfg.fpfh_radius)
    f_tgt = compute_fpfh(target, cfg.fpfh_radius)

    # 2) RANSAC 全局粗配准（失败回退更粗距离，best-effort）
    ransac = _ransac_with_fallback(source, target, f_src, f_tgt,
                                   cfg.ransac_distance)

    # 3) ICP 精配准（point-to-plane）
    icp = icp_refine(source, target, ransac.transformation,
                     cfg.icp_distance, cfg.max_iter, point_to_plane=True)

    # 4) 质量判定：精配准后 fitness 过低 = 重叠不足 / 配准失败（《设计书》33.4）
    if icp.fitness < _MIN_FITNESS:
        raise RegistrationError(
            f"配准失败（重叠不足，ICP fitness={icp.fitness:.3f} < "
            f"{_MIN_FITNESS}）")

    # 5) 质量评估
    metrics = evaluate_registration(icp)
    metrics["transformation"] = np.asarray(icp.transformation, dtype=np.float64)
    metrics["ransac_fitness"] = float(ransac.fitness)
    log.info("配准完成：fitness=%.3f, inlier_rmse=%.5f, 对应点=%d",
             metrics["fitness"], metrics["inlier_rmse"],
             metrics["correspondence_count"])
    return metrics


__all__ = [
    "compute_fpfh",
    "ensure_normals",
    "global_registration",
    "icp_refine",
    "evaluate_registration",
    "fitness",
    "inlier_rmse",
    "correspondence_count",
    "run",
]
