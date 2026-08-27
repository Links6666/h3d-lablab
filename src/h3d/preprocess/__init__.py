"""h3d.preprocess : 点云预处理（L2 业务层 / S1 阶段）—— 滤波、降采样、法线估计。

设计依据：
    《设计书》32 —— M1 数据接入与预处理：去噪、体素下采样、坐标归一化；
    《教学版》15 —— 降采样 / 统计离群去噪 / 法线估计与一致化，并组合成预处理流水线；
    《骨架分析》4.2 —— preprocess 子包承担「去噪 / 降采样 / 法线估计」职责。

对外暴露三个子模块的函数，并提供 :func:`run` 组合预处理流水线（配置驱动）。输入输出均为
统一数据模型（Open3D PointCloud），文件读写由 io 层负责，本层只做几何处理。

典型用法::

    from h3d.io import load_point_cloud, save_point_cloud
    from h3d.core import load
    from h3d.preprocess import run

    cfg = load("configs/example.yaml")
    pcd = load_point_cloud("00_raw/luohan_001.ply")
    pcd = run(pcd, cfg.preprocess)
    save_point_cloud(pcd, "10_clean/luohan_001.ply")
"""

from __future__ import annotations

from h3d.core import PreprocessConfig, get_logger

from .filter import statistical_outlier_removal, radius_outlier_removal
from .downsample import voxel_down_sample
from .normal_estimate import estimate_normals, orient_normals_consistent

log = get_logger("preprocess")

# 统计离群去噪的近邻点默认（《教学版》15.2）
_STAT_NB_NEIGHBORS = 20
# 法线一致化的近邻传播默认（《教学版》15.3）
_ORIENT_K = 15
# 法线搜索半径相对体素边长的倍数（经验值：法线半径约为体素的 3 倍）
_NORMAL_RADIUS_RATIO = 3.0


def run(pcd, cfg: PreprocessConfig):
    """组合预处理流水线（教学版 15.4 / 设计书 32.2）。

    顺序固定为：体素降采样 → 统计离群去噪 → 法线估计 + 一致化。参数全部取自
    ``PreprocessConfig``（配置驱动，见《设计书》7.4），各步结果为空的场景抛
    :class:`InputDataError`（E100）。

    返回
    ----
    处理后的 PointCloud（已降采样、去噪、带一致法线）。
    """
    voxel = cfg.voxel_down_sample
    pcd = voxel_down_sample(pcd, voxel)
    pcd = statistical_outlier_removal(
        pcd, nb_neighbors=_STAT_NB_NEIGHBORS, std_ratio=cfg.remove_outlier_std)
    pcd = estimate_normals(
        pcd, max_nn=cfg.normal_estimate_k, radius=voxel * _NORMAL_RADIUS_RATIO)
    pcd = orient_normals_consistent(pcd, k=_ORIENT_K)
    log.info("预处理流水线完成：%d 点（voxel=%.4f, std=%.2f, max_nn=%d）",
             len(pcd.points), voxel, cfg.remove_outlier_std, cfg.normal_estimate_k)
    return pcd


__all__ = [
    "statistical_outlier_removal",
    "radius_outlier_removal",
    "voxel_down_sample",
    "estimate_normals",
    "orient_normals_consistent",
    "run",
]
