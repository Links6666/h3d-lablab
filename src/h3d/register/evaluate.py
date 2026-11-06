"""h3d.register.evaluate : RMSE / fitness 配准误差评估。

设计依据：
    《设计书》15.3 —— 输出配准误差矩阵，供 M5 汇总；残差 RMS 作为配准质量指标；
    《骨架分析》4.2 —— evaluate.py 承担「RMSE / fitness 误差评估」职责。

从 Open3D RegistrationResult 提取质量指标：fitness（内点占比）、inlier_rmse
（内点均方根误差）、对应点数量。配准误差是后续质检（M5）与 WW/T 0117 精度
对标的关键输入。
"""

from __future__ import annotations

from typing import Any


def fitness(result: Any) -> float:
    """配准内点占比（0~1，越高越好）。"""
    return float(result.fitness)


def inlier_rmse(result: Any) -> float:
    """配准内点均方根误差（单位同输入点云，越高越差）。"""
    return float(result.inlier_rmse)


def correspondence_count(result: Any) -> int:
    """对应点数量（correspondence_set 长度）。"""
    cs = getattr(result, "correspondence_set", None)
    return len(cs) if cs is not None else 0


def evaluate_registration(result: Any) -> dict:
    """汇总配准质量指标，返回结构化 dict。

    形如 ``{"fitness": 0.98, "inlier_rmse": 0.0012, "correspondence_count": 2048}``。
    """
    return {
        "fitness": fitness(result),
        "inlier_rmse": inlier_rmse(result),
        "correspondence_count": correspondence_count(result),
    }


__all__ = ["fitness", "inlier_rmse", "correspondence_count",
           "evaluate_registration"]


# --------------------------------------------------------------------------- #
# 朝向判据（2026-11-05）——「方向对不对」，不是「贴不贴」
# --------------------------------------------------------------------------- #
def outward_sign(points, normals) -> float:
    """点云法线的**整体**符号约定：+1 外向、-1 内向。

    为什么需要：`orient_normals_consistent_tangent_plane` 只保证**各自内部**
    一致，不保证两朵云之间一致。实测同一件文物的 source/target 一个 +1、一个
    -1，直接比点积会把「正确配准」算成 −0.98（判据反号）。

    判据：法线与（点 − 质心）同向的比例过半即认为外向。对闭合/环扫件成立；
    单面扫描时这个量本来就弱，所以它只用来**归约符号**，不单独作为质量指标。
    """
    import numpy as np

    p = np.asarray(points, dtype=np.float64)
    n = np.asarray(normals, dtype=np.float64)
    if len(p) == 0 or len(p) != len(n):
        return 1.0
    d = np.einsum("ij,ij->i", n, p - p.mean(axis=0))
    return 1.0 if float((d > 0).mean()) > 0.5 else -1.0


def _oriented_normals(pcd, radius: float, max_nn: int = 30):
    """估计并**定向**法线（内部一致 + 外向归一），返回单位法线数组。"""
    import numpy as np
    import open3d as o3d

    if not pcd.has_normals():
        pcd.estimate_normals(
            o3d.geometry.KDTreeSearchParamHybrid(radius=radius, max_nn=max_nn))
    try:
        o3d.geometry.PointCloud.orient_normals_consistent_tangent_plane(
            pcd, max_nn)
    except Exception:  # noqa: BLE001 - 定向失败就退回原法线，不阻断配准
        pass
    n = np.asarray(pcd.normals, dtype=np.float64)
    s = outward_sign(np.asarray(pcd.points, dtype=np.float64), n)
    return n * s


def normal_agreement(source, target, transformation, max_dist: float,
                     radius: float, max_nn: int = 30):
    """配准后的**法线一致性**（−1~+1）：判断方向对不对。

    做法：把 source 点按 ``transformation`` 变换，在 target 里找最近邻，
    **只取内点**（``d <= max_dist``，与 fitness 同口径），逐点比较两边法线
    （各自先归一到外向约定）取点积均值。

    为什么这个量而不是 fitness：实测（真机 `test111.ply`）
      * 正确解、target 只剩 60%  → fitness **0.23**、一致性 **+0.85**
      * 翻转解（ICP 到收敛）    → fitness 0.10~0.21、一致性 **−0.52~−0.60**
    fitness 在两者之间**重叠**，分不开；方向分得开，且以 0 为天然分界。

    内点少于 50 个时返回 ``None``——那不是"方向一致"，是"没量出来"，
    报表必须印 N/A 而不是 0。
    """
    import numpy as np

    from scipy.spatial import cKDTree

    pts_s = np.asarray(source.points, dtype=np.float64)
    pts_t = np.asarray(target.points, dtype=np.float64)
    if len(pts_s) < 50 or len(pts_t) < 50:
        return None
    T = np.asarray(transformation, dtype=np.float64)
    n_s = _oriented_normals(source, radius, max_nn)
    n_t = _oriented_normals(target, radius, max_nn)
    moved = pts_s @ T[:3, :3].T + T[:3, 3]
    d, idx = cKDTree(pts_t).query(moved, k=1)
    m = d <= float(max_dist)
    if int(m.sum()) < 50:
        return None
    cos = np.einsum("ij,ij->i", n_s[m] @ T[:3, :3].T, n_t[idx[m]])
    return float(np.clip(cos.mean(), -1.0, 1.0))


# --------------------------------------------------------------------------- #
# 朝向判据（2026-11-05）——「方向对不对」，不是「贴不贴」
# --------------------------------------------------------------------------- #
def outward_sign(points, normals) -> float:
    """点云法线的**整体**符号约定：+1 外向、-1 内向。

    为什么需要：`orient_normals_consistent_tangent_plane` 只保证**各自内部**
    一致，不保证两朵云之间一致。实测同一件文物的 source/target 一个 +1、一个
    -1，直接比点积会把「正确配准」算成 −0.98（判据反号）。

    判据：法线与（点 − 质心）同向的比例过半即认为外向。对闭合/环扫件成立；
    单面扫描时这个量本来就弱，所以它只用来**归约符号**，不单独作为质量指标。
    """
    import numpy as np

    p = np.asarray(points, dtype=np.float64)
    n = np.asarray(normals, dtype=np.float64)
    if len(p) == 0 or len(p) != len(n):
        return 1.0
    d = np.einsum("ij,ij->i", n, p - p.mean(axis=0))
    return 1.0 if float((d > 0).mean()) > 0.5 else -1.0


def _oriented_normals(pcd, radius: float, max_nn: int = 30):
    """估计并**定向**法线（内部一致 + 外向归一），返回单位法线数组。"""
    import numpy as np
    import open3d as o3d

    if not pcd.has_normals():
        pcd.estimate_normals(
            o3d.geometry.KDTreeSearchParamHybrid(radius=radius, max_nn=max_nn))
    try:
        o3d.geometry.PointCloud.orient_normals_consistent_tangent_plane(
            pcd, max_nn)
    except Exception:  # noqa: BLE001 - 定向失败就退回原法线，不阻断配准
        pass
    n = np.asarray(pcd.normals, dtype=np.float64)
    s = outward_sign(np.asarray(pcd.points, dtype=np.float64), n)
    return n * s


def normal_agreement(source, target, transformation, max_dist: float,
                     radius: float, max_nn: int = 30):
    """配准后的**法线一致性**（−1~+1）：判断方向对不对。

    做法：把 source 点按 ``transformation`` 变换，在 target 里找最近邻，
    **只取内点**（``d <= max_dist``，与 fitness 同口径），逐点比较两边法线
    （各自先归一到外向约定）取点积均值。

    为什么这个量而不是 fitness：实测（真机 `test111.ply`）
      * 正确解、target 只剩 60%  → fitness **0.23**、一致性 **+0.85**
      * 翻转解（ICP 到收敛）    → fitness 0.10~0.21、一致性 **−0.52~−0.60**
    fitness 在两者之间**重叠**，分不开；方向分得开，且以 0 为天然分界。

    内点少于 50 个时返回 ``None``——那不是"方向一致"，是"没量出来"，
    报表必须印 N/A 而不是 0。
    """
    import numpy as np

    from scipy.spatial import cKDTree

    pts_s = np.asarray(source.points, dtype=np.float64)
    pts_t = np.asarray(target.points, dtype=np.float64)
    if len(pts_s) < 50 or len(pts_t) < 50:
        return None
    T = np.asarray(transformation, dtype=np.float64)
    n_s = _oriented_normals(source, radius, max_nn)
    n_t = _oriented_normals(target, radius, max_nn)
    moved = pts_s @ T[:3, :3].T + T[:3, 3]
    d, idx = cKDTree(pts_t).query(moved, k=1)
    m = d <= float(max_dist)
    if int(m.sum()) < 50:
        return None
    cos = np.einsum("ij,ij->i", n_s[m] @ T[:3, :3].T, n_t[idx[m]])
    return float(np.clip(cos.mean(), -1.0, 1.0))
