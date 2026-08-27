"""h3d.change : M3C2 多时相形变检测（X1 可选模块，L2 业务层）。

设计依据：
    《设计书》§19 / §38 —— 基于 py4dgeo 的 M3C2 计算两时相点云的距离场，
        识别风化 / 堆积变化区域，输出变化量热力与统计；
    《教学版》19 —— M3C2 算法（Lague et al. 2013）、LoD 探测限；
    《软著方案》2.4 —— change 后端 py4dgeo + CloudCompare 交叉校验（可选）。

本模块为<b>旁路增强</b>（cfg.change.enabled 默认 false），独立于主线 M1-M6 调用：
两期配准后的点云（同一造像不同时相）作输入，输出距离场 + 变化统计。

健壮性防护（针对此前运行中断的排查结论）：
    1. 输入 sanitize：过滤 NaN / Inf 点（py4dgeo 为 C++ 扩展，非有限值可致崩溃）；
    2. 网格分辨率上限：核心点网格乘积 <= 4096（64x64），防内存暴涨；
    3. py4dgeo 1.2.0 并行由全局 set_num_threads 控制（run() 无 cores 参数，
       错误的 run(cores=...) 会抛 TypeError 中断——已修正）；
    4. 全程结构化日志，异常路径可定位。

典型用法::

    from h3d.change import run
    from h3d.core import ChangeConfig
    from h3d.io import load_point_cloud

    t1 = load_point_cloud("20_registered/t1.ply")
    t2 = load_point_cloud("20_registered/t2.ply")
    result = run(t1, t2, ChangeConfig(cores=2, change_mm=2.0))
"""

from __future__ import annotations

from typing import Any

import numpy as np

from h3d.core import ChangeConfig, get_logger

from .m3c2_wrapper import compute_m3c2, corepoint_grid, detect_changes
from .validation import find_cloudcompare, validate_with_cloudcompare

log = get_logger("change")

# 核心点网格乘积上限（防内存暴涨；py4dgeo C++ 侧构建 KDTree 的成本随网格线性增长）
_MAX_GRID_POINTS = 4096
# 输入点云数量上限（超出抽样，防 C++ 侧构建超大 KDTree 内存峰值）
_MAX_INPUT_POINTS = 300_000


def _sanitize_cloud(pcd, label: str):
    """过滤非有限点（NaN / Inf），返回净点数组；空结果抛 InputDataError。"""
    from h3d.core import InputDataError

    pts = np.asarray(pcd.points, dtype=np.float64)
    finite = np.isfinite(pts).all(axis=1)
    if not finite.all():
        log.warning("%s 点云含 %d 个非有限点，已过滤", label, int((~finite).sum()))
        pts = pts[finite]
    if len(pts) == 0:
        raise InputDataError(f"{label} 点云在过滤非有限点后为空")
    # 超大点云抽样（防内存峰值，保留空间分布）
    if len(pts) > _MAX_INPUT_POINTS:
        rng = np.random.default_rng(0)
        idx = rng.choice(len(pts), _MAX_INPUT_POINTS, replace=False)
        pts = pts[idx]
        log.warning("%s 点云 %d 点超上限，抽样至 %d 点", label,
                    len(np.asarray(pcd.points)), len(pts))
    return pts


def _clamp_grid(cfg: ChangeConfig) -> tuple[int, int]:
    """限制核心点网格分辨率（乘积 <= _MAX_GRID_POINTS），防内存暴涨。"""
    nx, ny = int(cfg.grid[0]), int(cfg.grid[1])
    nx, ny = max(nx, 2), max(ny, 2)
    if nx * ny > _MAX_GRID_POINTS:
        scale = (nx * ny / _MAX_GRID_POINTS) ** 0.5
        nx, ny = int(nx / scale), int(ny / scale)
        log.warning("核心点网格 %dx%d 超上限，自动收缩至 %dx%d",
                    int(cfg.grid[0]), int(cfg.grid[1]), nx, ny)
    return nx, ny


def run(cloud1, cloud2, cfg: ChangeConfig) -> dict[str, Any]:
    """两期点云 M3C2 差分流水线（X1 多时相形变检测）。

    流程：输入 sanitize -> 核心点网格（分辨率受限）-> M3C2 距离场 ->
    变化检测（|Δ| > change_mm 标记）-> 汇总指标。

    参数
    ----
    cloud1: Open3D PointCloud（基准时相 t1，配准后）。
    cloud2: Open3D PointCloud（监测时相 t2，配准后）。
    cfg:    ChangeConfig（cores / change_mm / *_k / grid）。

    返回
    ----
    dict：m3c2（距离场 / 不确定性 / 核心点）、changes（变化掩码 / 统计）、
    metrics（max_change_mm / changed_ratio / mean_change_mm / n_corepoints）。
    """
    # 防护 1：输入 sanitize（防 C++ 崩溃）
    pts1 = _sanitize_cloud(cloud1, "t1")
    pts2 = _sanitize_cloud(cloud2, "t2")

    # 防护 2：网格分辨率受限（防内存暴涨）
    import open3d as o3d

    c1 = o3d.geometry.PointCloud()
    c1.points = o3d.utility.Vector3dVector(pts1)
    nx, ny = _clamp_grid(cfg)

    grid_cfg = _with_grid(cfg, (nx, ny))
    result = compute_m3c2(c1, cloud2 if len(pts2) == len(cloud2.points) else _make(pts2),
                          grid_cfg)
    changes = detect_changes(result, cfg.change_mm)

    metrics = {
        "max_change_mm": round(float(changes["max_change_mm"]), 4),
        "mean_change_mm": round(float(changes["mean_change_mm"]), 4),
        "changed_ratio": round(float(changes["changed_ratio"]), 4),
        "n_changed": int(changes["n_changed"]),
        "n_corepoints": int(len(result["corepoints"])),
        "mps_m": round(float(result["mps_m"]), 6),
    }
    log.info("X1 M3C2 完成：%d 核心点，%.1f%% 显著变化，max=|%.2f|mm",
             metrics["n_corepoints"], metrics["changed_ratio"] * 100,
             metrics["max_change_mm"])
    return {"m3c2": result, "changes": changes, "metrics": metrics}


def _with_grid(cfg: ChangeConfig, grid: tuple[int, int]) -> ChangeConfig:
    """返回 grid 受限的配置副本（不修改原配置）。"""
    clone = ChangeConfig()
    for key, value in cfg.to_dict().items():
        if hasattr(clone, key):
            setattr(clone, key, value)
    clone.grid = list(grid)
    return clone


def _make(pts: np.ndarray):
    import open3d as o3d

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts)
    return pcd


__all__ = [
    "compute_m3c2",
    "corepoint_grid",
    "detect_changes",
    "find_cloudcompare",
    "validate_with_cloudcompare",
    "run",
    "_sanitize_cloud",
    "_clamp_grid",
]
