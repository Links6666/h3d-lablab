"""h3d.core.scale : 尺度自适应（跨尺度文物通用化）。

设计依据：
    《设计书》33.4 —— 配准参数应与点云尺度相称；
    《教学版》16.1 —— FPFH/RANSAC/ICP 的半径与距离阈值须按点间距量级选取。

问题背景
--------
``preprocess`` / ``register`` / ``mesh`` / ``change`` 中的距离类参数是**绝对米制
值**，现有默认值按「约 1 m 量级」的文物标本定。中小型文物（5~30 cm）沿用同一套
值会退化：5 mm 体素吃掉纹饰细节、2 cm 的 FPFH 半径在一件 10 cm 器物上等效「半个
器身」，配准无法收敛。

本模块提供纯数学的尺度解析与配置变换（**不触碰文件 IO**，保持 core 底座自洽）：
包围盒对角线 -> 缩放因子 -> 各配置段的距离类字段整体线性缩放。文件读取由上层
（runner）完成，本模块只接收点坐标数组。

用法::

    from h3d.core.scale import bbox_diagonal, resolve_factor, scaled_config

    diag = bbox_diagonal(points)                 # 包围盒对角线（米）
    factor = resolve_factor(diag, cfg.scale)     # 缩放因子
    cfg2, changes = scaled_config(cfg, factor)   # 缩放后的配置副本 + 变更摘要

设计取舍
--------
仅缩放**有长度量纲**的字段；k 近邻数 / 比率 / 迭代次数 / 八叉树深度等无量纲量保持不变
—— 体素随尺度缩放后，点密度相对物体恒定，这些量自洽。
"""

from __future__ import annotations

import numpy as np

from .config import PipelineConfig, ScaleConfig
from .logging import get_logger

log = get_logger("core.scale")

# (配置段, 字段名)：有长度量纲、需随尺度线性缩放的标量字段
_LINEAR_FIELDS: tuple[tuple[str, str], ...] = (
    ("preprocess", "voxel_down_sample"),
    ("register", "fpfh_radius"),
    ("register", "ransac_distance"),
    ("register", "icp_distance"),
    ("mesh", "alpha"),
    ("change", "core_radius"),
)

# (配置段, 字段名)：有长度量纲的列表字段（逐元素缩放）
_LINEAR_LISTS: tuple[tuple[str, str], ...] = (
    ("mesh", "ball_radii"),
)

# 合法因子的最小有效值（防止 0 或负数污染下游参数）
_EPS = 1e-12


def bbox_diagonal(points: np.ndarray) -> float:
    """点集轴对齐包围盒的对角线长度（与输入同单位，通常为米）。

    空点集返回 ``0.0``（调用方据此退化为「不缩放」）。
    """
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[0] == 0 or pts.shape[1] < 3:
        return 0.0
    pts = pts[:, :3]
    finite = np.isfinite(pts).all(axis=1)
    if not finite.any():
        return 0.0
    pts = pts[finite]
    extent = pts.max(axis=0) - pts.min(axis=0)
    return float(np.linalg.norm(extent))


def resolve_factor(diag: float, cfg: ScaleConfig) -> float:
    """由包围盒对角线解算缩放因子。

    ``factor = clip(diag / reference_diag, min_factor, max_factor)``

    - ``enabled=False`` / ``diag<=0`` / ``reference_diag<=0`` 时返回 ``1.0``
      （等价于关闭自适应，保持历史行为）。
    - 触发上下限钳制或对角线异常偏大（疑似毫米单位）时告警，便于事后追溯。
    """
    if not cfg.enabled:
        return 1.0
    if diag is None or not np.isfinite(diag) or diag <= 0.0:
        log.warning("包围盒对角线无效（%r）——尺度自适应退化为不缩放", diag)
        return 1.0
    if cfg.reference_diag <= 0.0:
        log.warning("reference_diag=%.4f 非法——尺度自适应退化为不缩放",
                    cfg.reference_diag)
        return 1.0

    raw = float(diag) / float(cfg.reference_diag)
    factor = float(np.clip(raw, cfg.min_factor, cfg.max_factor))

    if raw != factor:
        log.warning("尺度因子 %.4f 超出钳制区间 [%.4f, %.4f]，已截断为 %.4f"
                    "（对角=%.4f m）", raw, cfg.min_factor, cfg.max_factor,
                    factor, diag)
    if diag > cfg.warn_diag_gt:
        log.warning("包围盒对角线 %.3f m 偏大——若数据单位实为毫米，"
                    "请先换算为米或显式设置 scale.reference_diag", diag)
    return factor


def scaled_config(cfg: PipelineConfig,
                  factor: float) -> tuple[PipelineConfig, list[dict]]:
    """返回距离类参数按 ``factor`` 线性缩放后的配置副本与变更摘要。

    - ``factor`` 与 1.0 的差小于 ``_EPS`` 时**直接返回原对象**（零拷贝、零风险）。
    - 原配置对象不被修改（走 ``to_dict`` -> ``from_dict`` 深拷贝再覆盖）。
    - 变更摘要为 ``[{"path": "register.fpfh_radius", "before": .., "after": ..}]``，
      供日志与 manifest 溯源。
    """
    if factor is None or not np.isfinite(factor) or abs(factor - 1.0) < _EPS:
        return cfg, []

    data = cfg.to_dict()
    changes: list[dict] = []

    for section, field in _LINEAR_FIELDS:
        before = data[section][field]
        after = round(float(before) * float(factor), 12)
        if after != before:
            data[section][field] = after
            changes.append({"path": f"{section}.{field}",
                            "before": before, "after": after})

    for section, field in _LINEAR_LISTS:
        before = list(data[section][field])
        after = [round(float(v) * float(factor), 12) for v in before]
        if after != before:
            data[section][field] = after
            changes.append({"path": f"{section}.{field}",
                            "before": before, "after": after})

    scaled = PipelineConfig.from_dict(data)
    return scaled, changes


def describe(changes: list[dict]) -> str:
    """把变更摘要压成单行日志文本。"""
    if not changes:
        return "无字段变化"
    return "；".join(f"{c['path']}: {c['before']} -> {c['after']}"
                     for c in changes)


# ---------------------------------------------------------------------------
# 点间距下限保护
# ---------------------------------------------------------------------------
# 各字段相对「点间距」的最小倍数：低于此值邻域过空、特征退化或找不到对应点。
_SPACING_FLOOR_RATIO: tuple[tuple[str, str, float], ...] = (
    ("register", "fpfh_radius", 4.0),      # 邻域需含足够支撑点
    ("register", "ransac_distance", 4.0),  # 粗配准对应距离
    ("register", "icp_distance", 2.0),     # 精配准对应距离
)


def estimate_spacing(points: np.ndarray, sample: int = 4000) -> float:
    """估计点云的典型**点间距**（最近邻距离的中位数）。

    采样 ``sample`` 个点后建 KDTree 求 1-NN 距离，取中位数——比均值稳健
    （不受离群点与孔洞影响）。点数不足或退化为 0 时返回 ``0.0``。
    """
    from scipy.spatial import cKDTree

    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[0] < 2:
        return 0.0
    pts = pts[:, :3]
    finite = np.isfinite(pts).all(axis=1)
    pts = pts[finite]
    if pts.shape[0] < 2:
        return 0.0
    if pts.shape[0] > sample:
        idx = np.linspace(0, pts.shape[0] - 1, sample).astype(np.int64)
        pts = pts[idx]
    try:
        tree = cKDTree(pts)
        dist, _ = tree.query(pts, k=2)
        nn = dist[:, 1]
        nn = nn[np.isfinite(nn) & (nn > 0)]
        if nn.size == 0:
            return 0.0
        return float(np.median(nn))
    except Exception:  # noqa: BLE001 - 估计失败不应阻断流程
        return 0.0


def apply_spacing_floor(cfg: PipelineConfig, spacing: float
                        ) -> tuple[PipelineConfig, list[dict]]:
    """对距离类参数施加「点间距下限」，返回修正后的配置副本与变更摘要。

    动机（2026-10-09 实验发现）：尺度缩放是**乘法**的，若输入点云的点间距
    不随物体尺寸同比变化（如从网格均匀采样、或多精度设备混用），小尺度下
    ``fpfh_radius × factor`` 可能**小于点间距**——邻域内只剩自身，FPFH 描述子
    退化，粗配准直接失败。

    修正：``参数 = max(参数 × factor, k × 点间距)``。k 取 4.0（FPFH / RANSAC）
    或 2.0（ICP）。对正常点云（点间距远小于参数）**不产生任何改动**，因此
    不影响既有行为。
    """
    if spacing is None or not np.isfinite(spacing) or spacing <= 0.0:
        return cfg, []

    data = cfg.to_dict()
    changes: list[dict] = []
    for section, field, ratio in _SPACING_FLOOR_RATIO:
        floor = ratio * float(spacing)
        before = data[section][field]
        if float(before) < floor:
            after = round(floor, 12)
            data[section][field] = after
            changes.append({"path": f"{section}.{field}", "before": before,
                            "after": after, "reason": "spacing_floor"})
    if not changes:
        return cfg, []
    return PipelineConfig.from_dict(data), changes


__all__ = [
    "bbox_diagonal",
    "resolve_factor",
    "scaled_config",
    "estimate_spacing",
    "apply_spacing_floor",
    "describe",
]
