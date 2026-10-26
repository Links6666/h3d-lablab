"""h3d.geo_align : 坐标系对齐（创新点 2 雏形）—— SfM 位姿 ↔ 激光几何。

设计依据：
    《照片导入方案》—— SfM 输出任意尺度/坐标系，与激光扫描几何不重合，
    需控制点 ICP 对齐，否则纹理完全错位（已实证：无靶标时残差大）。
    《路线 R2》—— 创新点 2「控制点 ICP 坐标系对齐方法」。

实现：
    umeyama_similarity —— Kabsch 带尺度相似变换（闭环解析解）；
    align_points_to_mesh —— 迭代「最近邻对应 + Umeyama」ICP 式对齐，
    把源点云（SfM 稀疏点云）变换到目标网格（激光几何）坐标系，
    返回 (s, R, T) 与残差；供 photo_texture 把相机位姿/几何统一起坐标系。
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from h3d.core import get_logger

log = get_logger("geo_align")

#: 网格表面采样的固定种子（保证 ICP 对齐结果可复现）
_SAMPLE_SEED = 20260915


def _seed_random(seed: int) -> None:
    """固定 Open3D 全局随机种子（让网格表面采样可复现；API 缺失时静默跳过）。"""
    try:
        import open3d as o3d
        o3d.utility.random.seed(int(seed))
    except Exception:  # noqa: BLE001 - 不影响对齐功能
        pass


def umeyama_similarity(src: np.ndarray, dst: np.ndarray):
    """Umeyama 相似变换（Kabsch 带尺度）：拟合 dst ≈ s * R @ src + T。

    返回 (s, R, T)：src 经 X' = s*R@X + T 变换后与 dst 最小二乘对齐。
    """
    mu_s = src.mean(axis=0)
    mu_d = dst.mean(axis=0)
    src_c = src - mu_s
    dst_c = dst - mu_d
    H = src_c.T @ dst_c
    U, _S, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    if np.linalg.det(R) < 0:
        Vt[-1] *= -1
        R = Vt.T @ U.T
    s = np.sum(dst_c * (src_c @ R.T)) / np.sum(src_c**2)
    T = mu_d - s * R @ mu_s
    return float(s), R, T


def align_points_to_mesh(src_points: np.ndarray, mesh,
                         n_samples: int = 3000, iterations: int = 6,
                         inlier_percentile: float = 90.0):
    """迭代 ICP 式相似变换：源点云 ↔ 网格表面点最近邻对应 + Umeyama。

    参数
    ----
    src_points:  源点云（SfM 稀疏点云，任意尺度/坐标系）(N,3)。
    mesh:        TriangleMesh（激光几何，目标坐标系基准）。
    n_samples:   网格表面采样点数（对应候选）。
    iterations:  交替「对应 + 相似变换」迭代次数。
    inlier_percentile: 最近邻距离分位阈值（剔除离群对应）。

    返回
    ----
    (s, R, T, metrics)：把 src_points 变换到网格坐标系的相似变换参数
    （X' = s*R@X + T）与对齐指标（残差/有效对应数/包围盒）。
    """
    from scipy.spatial import cKDTree

    verts = np.asarray(mesh.vertices, dtype=np.float64)
    s, R, T = 1.0, np.eye(3), np.zeros(3)
    work_pts = src_points.copy()
    final_rmse = None
    n_keep = 0
    # 粗对齐初始化：质心对齐 + 包围盒尺度比（SfM 任意尺度，最近邻对尺度敏感）
    # 固定采样种子：保证同一输入多次运行结果一致（可复现性，实机 2026-09-15）
    _seed_random(_SAMPLE_SEED)
    surface0 = mesh.sample_points_uniformly(n_samples)
    surf0 = np.asarray(surface0.points, dtype=np.float64)
    s0 = (surf0.max(axis=0) - surf0.min(axis=0)).max() / max(
        (work_pts.max(axis=0) - work_pts.min(axis=0)).max(), 1e-9)
    work_pts = work_pts * s0 + (surf0.mean(axis=0) - work_pts.mean(axis=0) * s0)
    s, T = s0, surf0.mean(axis=0) - src_points.mean(axis=0) * s0
    for _it in range(iterations):
        _seed_random(_SAMPLE_SEED + _it)
        surface = mesh.sample_points_uniformly(n_samples)
        surf_pts = np.asarray(surface.points, dtype=np.float64)
        tree = cKDTree(surf_pts)
        d, idx = tree.query(work_pts, k=1)
        thr = np.percentile(d, inlier_percentile) * 2.0
        keep = d < thr
        n_keep = int(keep.sum())
        if n_keep < 4:
            log.warning("ICP 对齐有效对应不足（%d），跳过迭代", n_keep)
            break
        ds, dR, dT = umeyama_similarity(work_pts[keep], surf_pts[idx[keep]])
        # 累积变换：work = ds*dR@work + dT；总变换作用在原始点上
        work_pts = ds * (work_pts @ dR.T) + dT
        s = s * ds
        R = dR @ R
        T = ds * dR @ T + dT
        final_rmse = np.sqrt(np.mean(d[keep] ** 2))
    metrics = {
        "n_correspondences": n_keep,
        "scale_s": round(float(s), 5),
        "rmse_after": round(float(final_rmse) if final_rmse else 0.0, 6),
        "bbox_src": [round(float(v), 3) for v in
                     (work_pts.max(axis=0) - work_pts.min(axis=0))],
    }
    return s, R, T, metrics


def apply_similarity(points: np.ndarray, s: float, R: np.ndarray,
                     T: np.ndarray) -> np.ndarray:
    """应用相似变换 X' = s*R@X + T。"""
    return s * (points @ R.T) + T


__all__ = ["umeyama_similarity", "align_points_to_mesh", "apply_similarity"]
