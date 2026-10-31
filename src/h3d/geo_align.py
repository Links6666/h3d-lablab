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

#: 相似变换成功后，源点云包围盒对角线 / 网格包围盒对角线 的允许偏离（±30%）。
#: 实机 1.1.12：坏对齐得到 2.03 倍，正是被这条拦下的。
_MAX_SCALE_DRIFT = 0.30
#: 对齐后中位距离至少要比对齐前改善 20%（比值 <= 0.8）才算"对齐有效"。
_MIN_IMPROVE = 0.80
#: 中位距离本身已小于网格对角线的 2% 时，视为本来就贴合（同源几何），
#: 此时即使没有改善也接受——否则同源数据会被误拒，白丢一次对齐。
_GOOD_NORM = 0.02


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


def _bbox_diag(points: np.ndarray) -> float:
    if len(points) == 0:
        return 0.0
    return float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))


def _robust_diag(points: np.ndarray, q: float = 0.99) -> float:
    """分位包围盒对角线（去掉两端各 (1-q)/2 的离群点）。

    **不能用 min/max**：COLMAP 稀疏点云天生带飞点，实机 1.1.12 的 13990 点里
    极少数远点把包围盒从 6.19 撑到 47.83（7.8 倍）。以它为尺度基准做粗对齐，
    相似变换从 0.12 起步、收敛到 0.25，把几何搬错 2 倍——表面看 M4 一切正常，
    实际岛内着色率从 98.9% 掉到 57.5%。用 99% 分位后该点云实测 6.19，与网格
    的 6.12 之比 1.01，尺度立刻回到正确量级。
    """
    if len(points) == 0:
        return 0.0
    lo = np.percentile(points, 100.0 * (1.0 - q) / 2.0, axis=0)
    hi = np.percentile(points, 100.0 * (1.0 + q) / 2.0, axis=0)
    return float(np.linalg.norm(hi - lo))


def alignment_quality(src_before: np.ndarray, src_after: np.ndarray,
                      mesh_verts: np.ndarray, ref_samples: int = 8000):
    """对齐质量判定：源点云到网格表面的**单向**距离，对齐前后对比。

    为什么不用 :func:`align_points_to_mesh` 自己迭代里那个 ``rmse``：

    1. 那个残差只统计 ``keep``（p90×2 以内）的对应，本身就被截断了；
    2. 它算的是**变换前**的距离（off-by-one），永远滞后一步；
    3. 最要命的是——它无法区分「收敛到正确答案」与「两边一起漂到一个
       自洽的错误位置」。2026-10-31 实机（1.1.12）：ICP 把 6.12 单位大的
       网格对齐后，源点云包围盒仍是网格的 **2.03 倍**，自报 rmse 0.0968
       看着漂亮，实际把几何推离了 50 倍（中位距离 0.036 -> 1.917），
       岛内着色率从 98.9% 掉到 57.5%。

    因此这里只用**外部可核对的量**：两个点集各自的包围盒对角线之比（相似
    变换成功后两者必须同尺度），以及源点云到网格表面的中位距离是否真的变小。

    返回 dict：``diag_ratio`` / ``median_before`` / ``median_after`` /
    ``norm_after``（相对网格对角线）/ ``improve`` / ``accepted`` / ``reason``。
    """
    from scipy.spatial import cKDTree

    diag_mesh = _robust_diag(mesh_verts)
    diag_src = _robust_diag(src_after)
    ratio = diag_src / diag_mesh if diag_mesh > 0 else float("inf")

    _seed_random(_SAMPLE_SEED)
    # 采样点足够密才能当「表面」用：n_samples=3000 时最近邻距离本身就有
    # 网格平均边长的量级，会把「没对齐」误判成「对齐得还行」。
    tree = cKDTree(_sample_surface(mesh_verts, ref_samples))
    med_before = float(np.median(tree.query(src_before, k=1)[0])) if len(src_before) else float("inf")
    med_after = float(np.median(tree.query(src_after, k=1)[0])) if len(src_after) else float("inf")

    norm_before = med_before / diag_mesh if diag_mesh > 0 else float("inf")
    norm_after = med_after / diag_mesh if diag_mesh > 0 else float("inf")
    improve = (med_after / med_before) if med_before > 0 else 1.0

    reason = ""
    accepted = True
    if diag_mesh <= 0 or diag_src <= 0:
        accepted, reason = False, "包围盒退化（空点云或全共点）"
    elif abs(ratio - 1.0) > _MAX_SCALE_DRIFT:
        accepted = False
        reason = (f"对齐后源点云包围盒是网格的 {ratio:.2f} 倍"
                  f"（允许 {1 - _MAX_SCALE_DRIFT:.2f}~{1 + _MAX_SCALE_DRIFT:.2f}）"
                  f"——相似变换没有收敛到同一尺度")
    elif improve > _MIN_IMPROVE and norm_after > _GOOD_NORM:
        accepted = False
        reason = (f"对齐后中位距离 {norm_after * 100:.1f}% 对角线"
                  f"（对齐前 {norm_before * 100:.1f}%，改善不足 "
                  f"{(1 - improve) * 100:.0f}%）——比对齐前没有实质改善")
    return {
        "diag_ratio": round(float(ratio), 4),
        "median_before": round(float(med_before), 6),
        "median_after": round(float(med_after), 6),
        "norm_before": round(float(norm_before), 5),
        "norm_after": round(float(norm_after), 5),
        "improve": round(float(improve), 4),
        "accepted": bool(accepted),
        "reason": reason,
    }


def _sample_surface(verts: np.ndarray, n: int) -> np.ndarray:
    """在点集上做均匀抽样（不依赖 TriangleMesh 的三角形面积采样）。

    对齐质量判定只需要一个「表面点云」，用顶点均匀抽样即可：顶点本身就是
    表面点，且不受三角形面积加权影响，代价 O(n) 且完全可复现。
    """
    if len(verts) == 0:
        return verts
    if len(verts) <= n:
        return np.asarray(verts, dtype=np.float64)
    idx = np.linspace(0, len(verts) - 1, n).astype(np.int64)
    return np.asarray(verts[idx], dtype=np.float64)


def align_points_to_mesh(src_points: np.ndarray, mesh,
                         n_samples: int = 3000, iterations: int = 6,
                         inlier_percentile: float = 90.0,
                         quality_gate: bool = True):
    """迭代 ICP 式相似变换：源点云 ↔ 网格表面点最近邻对应 + Umeyama。

    参数
    ----
    src_points:  源点云（SfM 稀疏点云，任意尺度/坐标系）(N,3)。
    mesh:        TriangleMesh（激光几何，目标坐标系基准）。
    n_samples:   网格表面采样点数（对应候选）。
    iterations:  交替「对应 + 相似变换」迭代次数。
    inlier_percentile: 最近邻距离分位阈值（剔除离群对应）。
    quality_gate: 是否做质量判定（默认开）。不通过时**返回恒等变换**并写
                 ``rejected`` + 原因——宁可不改变换，也不能把几何搬错。

    返回
    ----
    (s, R, T, metrics)：把 src_points 变换到网格坐标系的相似变换参数
    （X' = s*R@X + T）与对齐指标（残差/有效对应数/包围盒/质量判定）。
    """
    from scipy.spatial import cKDTree

    verts = np.asarray(mesh.vertices, dtype=np.float64)
    src = np.asarray(src_points, dtype=np.float64)
    s, R, T = 1.0, np.eye(3), np.zeros(3)
    work_pts = src.copy()
    final_rmse = 0.0
    n_keep = 0
    # 粗对齐初始化：质心对齐 + **鲁棒**包围盒尺度比（SfM 任意尺度，最近邻对
    # 尺度敏感；COLMAP 飞点会把 min/max 撑大数倍，必须用分位包围盒）
    # 固定采样种子：保证同一输入多次运行结果一致（可复现性，实机 2026-09-15）
    _seed_random(_SAMPLE_SEED)
    surface0 = mesh.sample_points_uniformly(n_samples)
    surf0 = np.asarray(surface0.points, dtype=np.float64)
    s0 = _robust_diag(surf0) / max(_robust_diag(work_pts), 1e-9)
    work_pts = work_pts * s0 + (surf0.mean(axis=0) - work_pts.mean(axis=0) * s0)
    s, T = s0, surf0.mean(axis=0) - src.mean(axis=0) * s0
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
    # 收敛后残差：必须用**变换后**的点重算，不能沿用循环里那个滞后一步的 d
    if len(work_pts):
        _seed_random(_SAMPLE_SEED)
        surf_final = np.asarray(mesh.sample_points_uniformly(n_samples).points,
                                dtype=np.float64)
        d_final, _ = cKDTree(surf_final).query(work_pts, k=1)
        if n_keep >= 4:
            final_rmse = float(np.sqrt(np.mean(
                np.sort(d_final)[:max(n_keep, 1)] ** 2)))

    metrics = {
        "n_correspondences": n_keep,
        "scale_s": round(float(s), 5),
        "rmse_after": round(float(final_rmse), 6),
        "bbox_src": [round(float(v), 3) for v in
                     (work_pts.max(axis=0) - work_pts.min(axis=0))],
    }
    if quality_gate:
        q = alignment_quality(src, work_pts, verts)
        metrics["quality"] = q
        metrics["accepted"] = q["accepted"]
        if not q["accepted"]:
            metrics["rejected"] = True
            metrics["reject_reason"] = q["reason"]
            log.warning("几何-位姿对齐被判无效，回退恒等变换（原因：%s）；"
                        "对齐前 s=%.5f rmse=%.4f", q["reason"], s, final_rmse)
            return 1.0, np.eye(3), np.zeros(3), metrics
    return s, R, T, metrics


def already_aligned(mesh_verts: np.ndarray, src_points: np.ndarray,
                    tol: float = 0.01) -> dict:
    """同源预检：几何与源点云是否**本来就贴合在同一坐标系**。

    判据 = 网格顶点到源点云的单向最近邻中位距离 / 网格鲁棒对角线 <= ``tol``。

    为什么要这一步（而非只靠质量闸门）：实机 1.1.12 里 00_raw 放的是本系统照片
    重建的稠密产物，它与 SfM 稀疏点云同系同尺度（实测中位距离 0.59% 对角线）。
    此时任何相似变换都是净损失——跳过对齐得 98.87% 岛内着色率，走「合格」的
    控制点两阶段对齐只有 93.15%，走自动 ICP 则崩到 57.5%。

    宁可不动，也不要为对齐而对齐。
    """
    from scipy.spatial import cKDTree

    verts = np.asarray(mesh_verts, dtype=np.float64)
    src = np.asarray(src_points, dtype=np.float64)
    diag = _robust_diag(verts)
    if len(verts) == 0 or len(src) == 0 or diag <= 0:
        return {"aligned": False, "norm_median": None, "tolerance": tol}
    # 网格顶点可能远多于源点云：抽到与源点云同量级即可，结果稳定
    sub = verts if len(verts) <= 20000 else verts[
        np.linspace(0, len(verts) - 1, 20000).astype(np.int64)]
    d = cKDTree(src).query(sub, k=1)[0]
    norm = float(np.median(d)) / diag
    return {"aligned": norm <= tol, "norm_median": round(norm, 5),
            "tolerance": tol}


def apply_similarity(points: np.ndarray, s: float, R: np.ndarray,
                     T: np.ndarray) -> np.ndarray:
    """应用相似变换 X' = s*R@X + T。"""
    return s * (points @ R.T) + T


__all__ = ["umeyama_similarity", "align_points_to_mesh", "apply_similarity",
           "alignment_quality", "already_aligned"]
