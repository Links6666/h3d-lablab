"""h3d.dense_gate : dense 产物质量门（M1 合并前筛选，保证闭环稳健）。

设计依据：
    《路线 R2》场景②闭环——COLMAP dense（fused_dense.ply）可能含离群点
    （深度外点）与低密度噪声区；直接合并进 M1 会污染几何。质量门在合并前：
    ① 统计离群剔除（neighborhood 统计）；② 密度过滤（体素密度低于阈值
    的低密度孤点剔除）；③ 可选 voxel 降采样控制规模。

对外：
    run_dense_gate(pcd, cfg) -> (filtered, metrics)
"""

from __future__ import annotations

import numpy as np

from h3d.core import get_logger

log = get_logger("dense_gate")


def run_dense_gate(pcd, std_ratio: float = 2.0, nb_neighbors: int = 20,
                   min_density_ratio: float = 0.01,
                   voxel_size: float = 0.0) -> tuple:
    """dense 产物质量过滤。

    参数
    ----
    pcd:            稠密点云（fused_dense）。
    std_ratio:      统计离群剔除阈值（k 邻域距离 > mean + std_ratio*std 剔除）。
    nb_neighbors:   k 邻域大小。
    min_density_ratio: 密度过滤：体素密度低于峰值密度 * ratio 的体素内点剔除
                     （0 = 关闭）。
    voxel_size:     合并前体素降采样（0 = 关闭）。

    返回
    ----
    (filtered_pcd, metrics)；点云过小返回原样并标注。
    """
    import open3d as o3d

    n_in = len(pcd.points)
    metrics = {"n_in": n_in}
    if n_in < 100:
        log.warning("dense 点云过小（%d），跳过质量门", n_in)
        metrics.update({"n_out": n_in, "removed_ratio": 0.0,
                        "removed_stat": 0, "removed_density": 0})
        return pcd, metrics

    removed_stat = 0
    if std_ratio > 0:
        filtered, ind = pcd.remove_statistical_outlier(
            nb_neighbors=nb_neighbors, std_ratio=std_ratio)
        removed_stat = n_in - len(filtered.points)
        pcd = filtered
        metrics["removed_stat"] = removed_stat

    removed_density = 0
    if min_density_ratio > 0 and len(pcd.points) > 0:
        pts = np.asarray(pcd.points, dtype=np.float64)
        # 颜色 / 法线必须随点一起筛：旧实现重建 PointCloud 时只赋 points，
        # 把 COLMAP 融合出来的 RGB 与法线**全部丢掉**——实机 2026-10-18：
        # fused_dense.ply 头只有 x/y/z，M4 顶点色路径拿不到任何颜色、
        # 交付的 PLY 通体灰黑，而日志一句警告都没有（"表面成功、实际丢信息"）。
        colors = np.asarray(pcd.colors, dtype=np.float64) if pcd.has_colors() else None
        normals = (np.asarray(pcd.normals, dtype=np.float64)
                   if pcd.has_normals() else None)
        # 体素密度：按固定体素网格统计每体素点数（峰值密度参照）
        scale = _estimate_voxel_scale(pts)
        if scale > 0:
            keys = np.floor(pts / scale).astype(np.int64)
            # 用 inverse 索引回查每点所属体素的计数，避免 python 端 set 推导
            # （原实现对 67 万点做 tuple 集合查找，另一处隐蔽耗时）。
            _uniq, inverse, counts = np.unique(
                keys, axis=0, return_inverse=True, return_counts=True)
            inverse = np.asarray(inverse).ravel()
            peak = counts.max()
            keep = counts[inverse] >= peak * min_density_ratio
            if keep.any() and not keep.all():
                removed_density = int((~keep).sum())
                filtered = o3d.geometry.PointCloud()
                filtered.points = o3d.utility.Vector3dVector(pts[keep])
                if colors is not None:
                    filtered.colors = o3d.utility.Vector3dVector(colors[keep])
                if normals is not None:
                    filtered.normals = o3d.utility.Vector3dVector(normals[keep])
                pcd = filtered
        metrics["removed_density"] = removed_density

    # 属性自检：没有颜色的点云会让下游顶点色 / PLY 交付变成"全黑模型"，
    # 属于静默数据丢失，必须在指标与日志里留痕（禁止静默降级）。
    metrics["has_colors_out"] = bool(pcd.has_colors())
    if not pcd.has_colors():
        log.warning("dense 质量门输出无颜色（下游顶点色与 PLY 交付将无色彩）")

    if voxel_size > 0 and len(pcd.points) > 0:
        pcd = pcd.voxel_down_sample(voxel_size)

    n_out = len(pcd.points)
    metrics.update({
        "n_out": n_out,
        "removed_ratio": round((n_in - n_out) / n_in, 4),
        "dense_gate": True,
    })
    log.info("dense 质量门：%d -> %d 点（统计离群 %d + 密度过滤 %d）",
             n_in, n_out, removed_stat, removed_density)
    return pcd, metrics


def _estimate_voxel_scale(pts: np.ndarray) -> float:
    """体素尺度启发式：最近邻平均距离 * 4（保证每体素多点）。"""
    from scipy.spatial import cKDTree
    if len(pts) < 10:
        return 0.0
    tree = cKDTree(pts)
    d, _ = tree.query(pts, k=min(5, len(pts)))
    nn = d[:, 1:].mean() if d.shape[1] > 1 else d[:, 0].mean()
    return float(max(nn * 4.0, 1e-6))


__all__ = ["run_dense_gate"]
