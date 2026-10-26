"""h3d.quality : 质检质量门（M5 质检，L2 业务层 / S4 阶段）—— 几何质量可报告。

设计依据：
    《设计书》§18 / §36 —— M5 质检：量化评估模型质量、对照标准阈值给出达标判定、
        生成报告；不达标状态 fail，阻断该件交付但队列其余照常处理；
    《教学版》第 18 章 —— Hausdorff / 采样距离度量 + 精度报告 accuracy.csv；
    《软著方案》2.7.2 —— 尺度自适应：判定阈值 = k × MPS（平均点间距）。

对外暴露两个子模块的函数，并提供 :func:`run` 质检流水线（配置驱动）。输入为 M3
网格产物与参考点云（M2 配准结果），输出指标字典（覆盖率 / 中误差 / 边界边比例 /
MPS / 达标判定），不达标判定由编排层抛 :class:`QualityGateError`（E500，可恢复）。

典型用法::

    from h3d.io import load_mesh, load_point_cloud
    from h3d.core import load
    from h3d.quality import run, build_report

    cfg = load("configs/example.yaml")
    mesh = load_mesh("30_dense/rock001.ply")
    ref = load_point_cloud("20_registered/rock001_s01.ply")
    metrics = run(mesh, ref, cfg.quality)   # {"coverage": ..., "rmse_mm": ..., "ok": ...}
    build_report("rock001", metrics, cfg.quality, "60_qc/rock001", "70_report/rock001")
"""

from __future__ import annotations

import math
from typing import Any

from h3d.core import InputDataError, QualityConfig, get_logger

from .metrics import boundary_ratio, estimate_mps, qc_rmse, surface_coverage
from .gate import ACCURACY_HEADERS, build_report, check_quality

log = get_logger("quality")


def run(mesh, ref_pcd, cfg: QualityConfig) -> dict[str, Any]:
    """质检流水线：计算几何质量指标并给出达标判定（不抛异常，判定留给调用方）。

    流程（设计书 §36.2）：估算参考点云 MPS（尺度自适应）-> 覆盖率（阈值 = k × MPS）
    -> 表面中误差（mm）-> 边界边比例 -> 判定 ok = 覆盖率达标 && 中误差达标
    （参考缺失时中误差为 NaN，仅判覆盖率，设计书 §36.4）。

    参数
    ----
    mesh:    M3 网格产物（TriangleMesh）。
    ref_pcd: 参考点云（M2 配准结果 / 扫描数据 / 靶标）。
    cfg:     QualityConfig（阈值与采样配置）。

    返回
    ----
    dict：coverage / rmse_mm / boundary_ratio / mps_m / overlap / ok。
    """
    if mesh is None or len(mesh.vertices) == 0:
        raise InputDataError("质检输入网格为空（0 顶点）")
    if ref_pcd is None or len(ref_pcd.points) == 0:
        raise InputDataError("质检参考点云为空（0 点）")

    mps = estimate_mps(ref_pcd)
    if mps <= 0:
        raise InputDataError("质检参考点云 MPS 估计失败（点间距为 0）")

    max_dist = cfg.coverage_k_mps * mps  # 尺度自适应（软著方案 2.7.2）
    coverage = surface_coverage(mesh, ref_pcd, max_dist, cfg.samples)
    rmse = qc_rmse(mesh, ref_pcd, cfg.samples)
    boundary = boundary_ratio(mesh)
    # 中误差判定仅在参考足够密时可靠：MPS(mm) <= 中误差阈值（能分辨阈值量级偏差）
    rmse_reliable = mps * 1000.0 <= cfg.rmse_threshold_mm

    metrics: dict[str, Any] = {
        "coverage": coverage,
        "rmse_mm": rmse,
        "rmse_reliable": rmse_reliable,
        "boundary_ratio": boundary,
        "mps_m": mps,
        "overlap": None,  # 多视角纹理重叠（M4 未实现，N/A）
        "max_distance_m": max_dist,
        "ok": check_quality(
            {"coverage": coverage, "rmse_mm": rmse, "rmse_reliable": rmse_reliable},
            cfg),
    }
    log.info("质检完成：coverage=%.1f%% rmse=%smm%s boundary=%.1f%% ok=%s",
             metrics["coverage"] * 100,
             ("N/A" if math.isnan(rmse) else f"{rmse:.2f}"),
             ("" if rmse_reliable else "（参考过疏，中误差仅参考不判门）"),
             metrics["boundary_ratio"] * 100, metrics["ok"])
    return metrics


__all__ = [
    "estimate_mps",
    "surface_coverage",
    "qc_rmse",
    "boundary_ratio",
    "check_quality",
    "build_report",
    "ACCURACY_HEADERS",
    "run",
]
