"""h3d.mesh : 网格重建（M3 稠密重建，L2 业务层 / S4 阶段）—— 泊松 / 球旋转 / Alpha-shape。

设计依据：
    《设计书》§16 / §34 —— M3 稠密重建：由配准点云生成水密表面网格；
    《教学版》第 17 章 —— 泊松重建（深度平衡细节与性能）、球旋转重建、常见修复；
    《教学版》第 18 章 —— 简化 / 修补 / Hausdorff 度量；
    《软著方案》2.4 —— M3 mesh「泊松重建 / Alpha-shape、孔洞修复」。

对外暴露四个子模块的函数，并提供 :func:`run` 组合重建流水线（配置驱动）。输入为
带一致法线的配准点云（Open3D PointCloud），输出水密表面网格（TriangleMesh）。
文件读写由 io 层负责，本层只做几何重建与后处理。

典型用法::

    from h3d.io import load_point_cloud, save_mesh
    from h3d.core import load
    from h3d.mesh import run

    cfg = load("configs/example.yaml")
    pcd = load_point_cloud("20_registered/rock001_s01.ply")
    mesh = run(pcd, cfg.mesh)
    save_mesh(mesh, "30_dense/rock001.ply")
"""

from __future__ import annotations

from h3d.core import InputDataError, MeshConfig, get_logger

from .poisson import poisson_reconstruct, clean_mesh
from .ball_pivoting import ball_pivoting_reconstruct
from .alpha_shape import alpha_shape_reconstruct
from .postprocess import (
    simplify_mesh,
    fill_holes,
    hausdorff_distance,
    mesh_metrics,
)

log = get_logger("mesh")

# 法线兜底参数（设计书 §34.4「法向缺失 -> 先估计法向再重建」）
_NORMAL_RADIUS_RATIO = 5.0
_NORMAL_MAX_NN = 30
_ORIENT_K = 15


def ensure_normals(pcd) -> None:
    """法线缺失时自动估计并一致化（原地修改，为泊松重建铺路）。

    法线搜索半径取包围盒对角线的 1% 经验值（自适应点云尺度），一致化近邻 k=15
    与 preprocess 层口径一致（《教学版》17「没有一致法线，泊松会翻面/破洞」）。
    """
    if pcd.has_normals():
        return
    import numpy as np

    o3d = _o3d()
    bbox = pcd.get_axis_aligned_bounding_box()
    radius = float(np.linalg.norm(np.asarray(bbox.get_max_bound()) -
                                  np.asarray(bbox.get_min_bound()))) * 0.01
    pcd.estimate_normals(
        o3d.geometry.KDTreeSearchParamHybrid(radius=radius, max_nn=_NORMAL_MAX_NN))
    pcd.orient_normals_consistent_tangent_plane(_ORIENT_K)
    log.info("法线兜底：估计 + 一致化（radius=%.4f, k=%d）", radius, _ORIENT_K)


def run(pcd, cfg: MeshConfig):
    """组合重建流水线（设计书 §34.2 / 教学版 17-18，配置驱动）。

    流程：法线兜底 -> 按 ``cfg.method`` 分发重建 -> 密度裁剪（泊松）-> 清理链 ->
    可选 QEM 简化 -> 可选补洞。

    参数
    ----
    pcd: 配准点云（Open3D PointCloud，可多站合并后传入）。
    cfg: MeshConfig（method / poisson_depth / density_ratio / ball_radii /
         alpha / simplify_target / fill_holes）。

    返回
    ----
    重建并后处理后的 TriangleMesh（带法线）。
    """
    if pcd is None or not pcd.has_points():
        raise InputDataError("网格重建输入点云为空（0 点）")

    ensure_normals(pcd)

    method = cfg.method
    if method == "poisson":
        mesh = poisson_reconstruct(pcd, cfg.poisson_depth, cfg.density_ratio)
    elif method == "ball_pivoting":
        mesh = ball_pivoting_reconstruct(pcd, cfg.ball_radii)
    elif method == "alpha_shape":
        mesh = alpha_shape_reconstruct(pcd, cfg.alpha)
    else:  # pragma: no cover - choices 校验已拦截
        raise InputDataError(f"未知重建方法: {method!r}")

    if cfg.simplify_target and len(mesh.triangles) > cfg.simplify_target:
        mesh = simplify_mesh(mesh, cfg.simplify_target)
    if cfg.fill_holes:
        mesh = fill_holes(mesh)

    log.info("重建流水线完成：%d 顶点 / %d 三角面（method=%s）",
             len(mesh.vertices), len(mesh.triangles), method)
    return mesh


def _o3d():
    import open3d

    return open3d


__all__ = [
    "poisson_reconstruct",
    "clean_mesh",
    "ball_pivoting_reconstruct",
    "alpha_shape_reconstruct",
    "simplify_mesh",
    "fill_holes",
    "hausdorff_distance",
    "mesh_metrics",
    "ensure_normals",
    "run",
]
