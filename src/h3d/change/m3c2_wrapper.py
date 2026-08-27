"""h3d.change.m3c2_wrapper : py4dgeo M3C2 封装与变化检测（X1 多时相，可选）。

设计依据：
    《设计书》§19 / §38 —— 基于 py4dgeo 的 M3C2 计算两时相点云的距离场，
        识别风化 / 堆积变化区域，输出变化量热力与统计；
    《教学版》19 —— M3C2 算法原理（Lague et al. 2013）、LoD 探测限概念；
    《软著方案》2.7.2 —— 尺度自适应：法线 / 圆柱 / 最大距离以 MPS 倍数表达。

实现要点：
    1. py4dgeo 1.2.0 参数化 API：``M3C2(normal_radii, cyl_radius, max_distance,
       registration_error)`` + ``m.epochs / m.corepoints`` 属性赋值 + ``run()``
       返回 ``(distances, uncertainties)`` 元组（距离场含 NaN = 无有效邻域）。
    2. 核心点网格用 ``regular_corepoint_grid``（按点云 XY 包围盒均匀铺网格）。
    3. :func:`detect_changes` 按变化阈值（mm）标记风化（负）/ 堆积（正）区域。
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from h3d.core import get_logger

log = get_logger("change.m3c2")


def corepoint_grid(cloud1, cfg) -> np.ndarray:
    """按第一时相点云 XY 包围盒生成规则核心点网格。

    参数
    ----
    cloud1: Open3D PointCloud（基准时相）。
    cfg:    ChangeConfig（grid=[nx, ny]）。

    返回
    ----
    (n_corepoints, 3) 核心点数组。
    """
    pts = np.asarray(cloud1.points, dtype=np.float64)
    lo = pts.min(axis=0)[:2]
    hi = pts.max(axis=0)[:2]
    nx, ny = int(cfg.grid[0]), int(cfg.grid[1])
    grid = _py4dgeo().regular_corepoint_grid(lo, hi, (nx, ny))
    return np.asarray(grid, dtype=np.float64)


def compute_m3c2(cloud1, cloud2, cfg) -> dict[str, Any]:
    """两时相点云 M3C2 差分，返回距离场与不确定性。

    尺度参数由 ChangeConfig 的 MPS 倍数换算：先估计参考时相的平均点间距
    （py4dgeo 内部处理），按 ``normal_radius_k × mps`` 等换算实际半径。

    参数
    ----
    cloud1: Open3D PointCloud（基准时相 t1，配准后）。
    cloud2: Open3D PointCloud（监测时相 t2，配准后）。
    cfg:    ChangeConfig（cores / normal_radius_k / cyl_radius_k /
            max_distance_k / registration_error / grid）。

    返回
    ----
    dict：corepoints / distances（米）/ uncertainties（米）/ grid 分辨率。
    """
    if cloud1 is None or not cloud1.has_points() or not cloud2.has_points():
        from h3d.core import InputDataError
        raise InputDataError("M3C2 输入点云为空")

    py4dgeo = _py4dgeo()
    from h3d.quality import estimate_mps

    mps = estimate_mps(cloud1)
    if mps <= 0:
        from h3d.core import InputDataError
        raise InputDataError(f"M3C2 参考时相 MPS 估计失败（mps={mps}）")

    normal_radius = cfg.normal_radius_k * mps
    cyl_radius = cfg.cyl_radius_k * mps
    max_distance = cfg.max_distance_k * mps

    epoch1 = py4dgeo.Epoch(np.asarray(cloud1.points, dtype=np.float64))
    epoch2 = py4dgeo.Epoch(np.asarray(cloud2.points, dtype=np.float64))
    corepoints = corepoint_grid(cloud1, cfg)

    m3c2 = py4dgeo.M3C2(
        normal_radii=[float(normal_radius)],
        cyl_radius=float(cyl_radius),
        max_distance=float(max_distance),
        registration_error=float(cfg.registration_error),
    )
    m3c2.epochs = [epoch1, epoch2]
    m3c2.corepoints = corepoints
    # py4dgeo 1.2.0：并行度由全局 set_num_threads 控制（run() 不接受 cores 参数）
    try:
        py4dgeo.set_num_threads(int(cfg.cores))
    except Exception:  # pragma: no cover - 平台兼容（如无 OpenMP 的构建）
        log.warning("set_num_threads 不可用，使用默认线程数")
    distances, uncertainties = m3c2.run()

    # py4dgeo 1.2.0：uncertainties 可能为结构化数组（LoD 探测限详情：
    # lodetection / spread1 / num_samples1 / spread2 / num_samples2）
    unc_raw = np.asarray(uncertainties)
    if unc_raw.dtype.names:
        lod = np.asarray(unc_raw["lodetection"], dtype=np.float64)
        uncertainties_flat = lod
        lod_details = unc_raw
    else:
        uncertainties_flat = np.asarray(unc_raw, dtype=np.float64)
        lod_details = None

    result = {
        "corepoints": np.asarray(corepoints, dtype=np.float64),
        "distances": np.asarray(distances, dtype=np.float64),      # 米
        "uncertainties": uncertainties_flat,                       # LoD（米）
        "lodetection": lod_details,                                # 结构化详情或 None
        "mps_m": float(mps),
        "normal_radius_m": float(normal_radius),
        "cyl_radius_m": float(cyl_radius),
        "max_distance_m": float(max_distance),
    }
    log.info("M3C2 差分完成：%d 核心点（normal=%.4f, cyl=%.4f, maxdist=%.4f）",
             len(result["corepoints"]), normal_radius, cyl_radius, max_distance)
    return result


def detect_changes(result: dict[str, Any], threshold_mm: float,
                   sample_limit: int = 5000) -> dict[str, Any]:
    """按变化阈值标记显著变化核心点（设计书 §38.2「changes = threshold(dist)」）。

    规则：``|distance| * 1000 > threshold_mm`` 的核心点视为显著变化——
    distance > 0 为堆积（材料增加），< 0 为风化 / 侵蚀（材料减少）。

    参数
    ----
    result:       compute_m3c2 的输出。
    threshold_mm: 变化阈值（毫米，设计书默认 2.0）。
    sample_limit: 返回的采样点数上限（结果过大时抽样，避免爆内存）。

    返回
    ----
    dict：mask（显著变化布尔掩码）/ indices / n_changed / changed_ratio /
          mean_change_mm / max_change_mm（含 NaN 安全统计）。
    """
    dist = np.asarray(result["distances"], dtype=np.float64) * 1000.0  # -> mm
    mask = ~np.isnan(dist) & (np.abs(dist) > threshold_mm)
    indices = np.flatnonzero(mask)

    valid = dist[~np.isnan(dist)]
    stats = {
        "mask": mask,
        "indices": indices,
        "n_changed": int(mask.sum()),
        "changed_ratio": float(mask.sum() / len(dist)) if len(dist) else 0.0,
        "mean_change_mm": float(np.mean(valid)) if len(valid) else float("nan"),
        "max_change_mm": float(np.max(np.abs(valid))) if len(valid) else float("nan"),
    }
    # 采样（结果过大时）
    if len(indices) > sample_limit:
        rng = np.random.default_rng(0)
        stats["indices"] = rng.choice(indices, sample_limit, replace=False)
    log.info("变化检测：%d/%d 核心点显著变化（%.1f%%），max=|%.2f|mm",
             stats["n_changed"], len(dist), stats["changed_ratio"] * 100,
             stats["max_change_mm"] if not np.isnan(stats["max_change_mm"]) else 0.0)
    return stats


def _py4dgeo():
    import py4dgeo

    return py4dgeo


__all__ = ["corepoint_grid", "compute_m3c2", "detect_changes"]
