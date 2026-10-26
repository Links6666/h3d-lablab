"""h3d.preprocess.normal_estimate : 法线估计与法线一致化。

设计依据：
    《设计书》32.2 —— M1 预处理做坐标归一化与朝向校正，为后续 Poisson 重建提供一致法线；
    《教学版》15.3 —— 法线估计 estimate_normals + orient_normals_consistent_tangent_plane；
    《骨架分析》4.2 —— normal_estimate.py 承担「法线估计」职责。

职责边界：只对统一数据模型（PointCloud）做<b>法线估计与定向</b>，输入输出均为 PointCloud。
法线是 Poisson 重建、ICP 精配准（point-to-plane）的必需属性，本模块保证法线方向一致。
"""

from __future__ import annotations

from typing import Optional

from h3d.core import InputDataError, get_logger

log = get_logger("preprocess.normal_estimate")


def _o3d():
    import open3d as o3d

    return o3d


def estimate_normals(pcd, max_nn: int = 30, radius: Optional[float] = None):
    """估计点云法线（Open3D estimate_normals，原地修改后返回）。

    用 Hybrid 搜索（radius + max_nn）；radius 未提供时退化为 KNN(max_nn)。
    法线朝向在下一阶段由 orient_normals_consistent 统一。

    参数
    ----
    max_nn  : 近邻点数上限（配置项 preprocess.normal_estimate_k，默认 30）。
    radius  : 搜索半径（可选；默认 None 时用 KNN）。

    返回
    ----
    已带法线的 PointCloud（原地修改）。
    """
    if pcd is None or not pcd.has_points():
        raise InputDataError("输入点云为空（0 点），无法估计法线")
    o3d = _o3d()
    if radius is not None and float(radius) > 0:
        search = o3d.geometry.KDTreeSearchParamHybrid(
            radius=float(radius), max_nn=int(max_nn))
    else:
        search = o3d.geometry.KDTreeSearchParamKNN(knn=int(max_nn))
    pcd.estimate_normals(search, fast_normal_computation=True)
    log.info("法线估计完成：%d 点（max_nn=%d, radius=%s）",
             len(pcd.points), max_nn, radius)
    return pcd


def orient_normals_consistent(pcd, k: int = 15):
    """法线一致化（Open3D orient_normals_consistent_tangent_plane，原地修改）。

    以切平面传播方式统一法线朝向，避免 Poisson 重建出现「翻面」。调用前须先
    estimate_normals。
    """
    if pcd is None or not pcd.has_points():
        raise InputDataError("输入点云为空（0 点），无法定向法线")
    if not pcd.has_normals():
        raise InputDataError("点云尚未估计法线，请先调用 estimate_normals")
    pcd.orient_normals_consistent_tangent_plane(int(k))
    log.info("法线一致化完成（k=%d）", k)
    return pcd


def orient_normals_by_cameras(pcd, cam_centers, sample: int = 20000):
    """用已知相机位置解出法线的**全局朝向**（翻面检测）。

    背景（2026-10-18 实机致命缺陷）：``orient_normals_consistent_tangent_plane``
    只保证法线**彼此一致**，全局符号（朝内 / 朝外）是任意的。这批照片重建的
    结果是：68% 的网格顶点法线与 COLMAP 融合法线**反向**。后果链条——
    Poisson 按反向法线重建 → 曲面整体翻面 → M4 的「正面 cos>0.3」判据选到
    **背面**相机 → 采样点投到器物轮廓外的白背景上 → 交付的贴图大面积泛白、
    看着像"没有颜色"。因为全程没有任何异常，问题只能靠目视发现。

    判据：若某点的法线朝向多数相机（``cos>0``），说明它朝外；对全部点取中位数，
    中位数 < 0.5 即整体翻面，一次性取反（保持切平面一致性，不做逐点翻转）。

    返回 metrics：``{"frac_towards": 朝向相机的点数比例, "flipped": 是否翻转}``。
    """
    import numpy as np

    if pcd is None or not pcd.has_points() or not pcd.has_normals():
        return {"flipped": False, "reason": "no_normals"}
    cams = np.asarray(cam_centers, dtype=np.float64)
    if cams.ndim != 2 or cams.shape[0] < 2:
        return {"flipped": False, "reason": "insufficient_cameras"}
    pts = np.asarray(pcd.points, dtype=np.float64)
    nrm = np.asarray(pcd.normals, dtype=np.float64)
    idx = np.linspace(0, len(pts) - 1, min(int(sample), len(pts))).astype(np.int64)
    P, N = pts[idx], nrm[idx]
    votes = np.zeros(len(idx), dtype=np.int32)
    for c in cams:
        v = c - P
        v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
        votes += ((v * N).sum(axis=1) > 0).astype(np.int32)
    frac = votes / float(cams.shape[0])
    flipped = float(np.median(frac)) < 0.5
    if flipped:
        # 整体反号：保持切平面一致，只改全局符号
        pcd.normals = _o3d().utility.Vector3dVector(-nrm)
        log.warning("法线整体朝内（朝向相机比例中位数 %.2f）——已整体翻转；"
                    "若不翻，Poisson 会生成翻面网格、照片贴图会采到背景",
                    float(np.median(frac)))
    else:
        log.info("法线朝向检查通过：朝向相机比例中位数 %.2f", float(np.median(frac)))
    return {"frac_towards": round(float(np.median(frac)), 4), "flipped": bool(flipped)}


__all__ = ["estimate_normals", "orient_normals_consistent",
           "orient_normals_by_cameras"]
