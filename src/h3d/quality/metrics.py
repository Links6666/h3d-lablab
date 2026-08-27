"""h3d.quality.metrics : 几何质量指标计算（M5 质检）。

设计依据：
    《设计书》§18 / §36 —— M5 质检：覆盖率（模型表面被有效点云覆盖的占比）、
        中误差（特征点与参照比较）、质量门判定；
    《教学版》18.2 —— Hausdorff / 采样点最近邻距离作为几何误差指标；
    《软著方案》2.7.2 —— 尺度自适应：判定阈值 = k × MPS（平均点间距），不写死绝对值。

实现要点：
    1. ``estimate_mps`` 用 Open3D 内置 ``compute_nearest_neighbor_distance``
       批量求每点最近邻距离，取平均得到平均点间距（尺度自适应的基准）。
    2. ``surface_coverage`` 网格均匀采样 N 点，统计距参考点云最近邻距离
       <= max_dist 的占比（被有效点云覆盖的表面积比例，设计书 18.1）。
    3. ``qc_rmse`` 同源距离数组的均方根（米 -> 毫米），对照 WW/T 中误差限值。
    4. ``boundary_ratio`` 统计只被一个三角面引用的边（边界边）占总边数比例，
       作为孔洞率 / 网格闭合性的量化指标。
"""

from __future__ import annotations

from typing import Optional

from h3d.core import get_logger

log = get_logger("quality.metrics")


def estimate_mps(pcd, sample_limit: int = 20000) -> float:
    """估计平均点间距 MPS（米）。

    用 Open3D ``compute_nearest_neighbor_distance`` 批量求最近邻距离；
    点过多时先均匀抽样，避免大点云全量计算。完全重合（重复点）退化时
    回退为包围盒对角线的 1%，保证调用方得到有效尺度。返回 0 表示空点云。
    """
    import numpy as np

    n = len(pcd.points)
    if n == 0:
        return 0.0
    if n > sample_limit:  # 抽样降规模
        rng = np.random.default_rng(0)
        idx = rng.choice(n, sample_limit, replace=False)
        pcd = pcd.select_by_index(idx)
    dists = np.asarray(pcd.compute_nearest_neighbor_distance())
    if len(dists) == 0:
        return 0.0
    mps = float(np.mean(dists))
    if mps <= 0:  # 完全重合 / 重复点退化（如两片相同点云合并）
        bbox = pcd.get_axis_aligned_bounding_box()
        diag = float(np.linalg.norm(
            np.asarray(bbox.get_max_bound()) - np.asarray(bbox.get_min_bound())))
        mps = max(diag * 0.01, 1e-6)
        log.warning("MPS 估计退化（最近邻距离全为 0），回退包围盒对角线的 1%%：%.6f", mps)
    log.debug("MPS 估计：%.6f m（%d 点）", mps, len(dists))
    return mps


def surface_coverage(mesh, ref_pcd, max_dist: float, samples: int = 50000) -> float:
    """模型表面覆盖率（0~1）。

    网格均匀采样 ``samples`` 点，统计到参考点云最近邻距离 <= ``max_dist``
    的比例——即被有效点云覆盖的模型表面积占比（《设计书》18.1）。
    """
    import numpy as np

    if len(mesh.vertices) == 0 or len(ref_pcd.points) == 0:
        return 0.0
    pts = mesh.sample_points_uniformly(int(samples))
    dists = np.asarray(pts.compute_point_cloud_distance(ref_pcd))
    return float(np.mean(dists <= max_dist))


def qc_rmse(mesh, ref_pcd, samples: int = 50000) -> float:
    """表面中误差（毫米）：网格采样点到参考点云最近邻距离的均方根。

    反映重建网格对参考（扫描 / 靶标）表面的贴合程度，对照 WW/T 0115/0117
    中误差限值（精细级 <= 5 mm）。缺参考时调用方负责标记 N/A。
    """
    import numpy as np

    if len(mesh.vertices) == 0 or len(ref_pcd.points) == 0:
        return float("nan")
    pts = mesh.sample_points_uniformly(int(samples))
    dists = np.asarray(pts.compute_point_cloud_distance(ref_pcd))
    return float(np.sqrt(np.mean(np.square(dists))) * 1000.0)  # 米 -> 毫米


def boundary_ratio(mesh) -> float:
    """边界边比例（0~1）：只被一个三角面引用的边占总边数之比。

    水密网格边界边为 0；重建 / 裁剪产生的开口越大该值越高，
    是孔洞率 / 网格闭合性的量化指标（补充指标，供报告参考）。
    """
    import numpy as np

    triangles = np.asarray(mesh.triangles)
    if len(triangles) == 0:
        return 1.0
    edge_count: dict[tuple[int, int], int] = {}
    for a, b, c in triangles:
        for u, v in ((a, b), (b, c), (c, a)):
            key = (int(min(u, v)), int(max(u, v)))
            edge_count[key] = edge_count.get(key, 0) + 1
    n_boundary = sum(1 for cnt in edge_count.values() if cnt == 1)
    return float(n_boundary / len(edge_count))


__all__ = ["estimate_mps", "surface_coverage", "qc_rmse", "boundary_ratio"]
