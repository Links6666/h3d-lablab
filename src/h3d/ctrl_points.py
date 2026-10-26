"""h3d.ctrl_points : 控制点靶标采集规范与引导 ICP（创新点 2 完整落地）。

设计依据：
    《路线 R2》—— 创新点 2「控制点 ICP 坐标系对齐方法」。无靶标时自动
    最近邻 ICP 残差大（实证 0.17），投影未命中；控制点引导直接建立
    「SfM 系 ↔ 激光几何系」已知对应 → Umeyama 闭环解析解 → 高精度对齐。

控制点规范（00_ctrl/<item>/control_points.json）：
    {
      "item_id": "rock001",
      "control_points": [
        {"id": "c01", "laser_xyz": [x,y,z],            # 激光几何系坐标（靶标测量/手动）
         "photo_uv": [{"image": "img_01.jpg", "u": 400.5, "v": 300.2}, ...]}
      ]
    }
    激光系坐标：靶标中心在激光扫描点云中的坐标（球心/棋盘格角点）。
    照片 uv：靶标中心在各张照片中的像素坐标（ArUco 自动检测或手工标注）。

核心：
    load_control_points          —— 读规范文件
    triangulate_sfm_xyz          —— 多视图 uv + 位姿 → SfM 系 3D（线性三角化）
    solve_control_alignment      —— SfM 系 ↔ 激光系 Umeyama（≥3 点）
    align_mesh_with_controls     —— 几何变换到 SfM 系（供 P1 投影）
    detect_aruco_corners         —— ArUco 自动检测（可选，需 opencv-python）
"""

from __future__ import annotations

import json
import os
from typing import Optional

import numpy as np

from h3d.core import get_logger

log = get_logger("ctrl_points")

CONTROL_POINTS_FILE = "control_points.json"
CTRL_DIR = "00_ctrl"  # 项目根目录（与 00_raw / 00_photo 同级）


class ControlPointError(Exception):
    """控制点数据错误（数量不足/格式非法/三角化失败）。"""


def load_control_points(path: str) -> dict:
    """读取控制点规范文件 -> {id: {"laser_xyz": np.ndarray,
    "photo_uv": [{"image", "u", "v"}]}}；数量 <3 抛 ControlPointError。"""
    if not os.path.exists(path):
        raise ControlPointError(f"控制点文件不存在: {path}")
    with open(path, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    points = {}
    for cp in data.get("control_points", []):
        pts = np.asarray(cp["laser_xyz"], dtype=np.float64)
        if pts.shape != (3,):
            raise ControlPointError(f"控制点 {cp.get('id')} laser_xyz 非法")
        points[cp["id"]] = {
            "laser_xyz": pts,
            "photo_uv": cp.get("photo_uv", []),
        }
    if len(points) < 3:
        raise ControlPointError(
            f"控制点不足（需 >=3，实际 {len(points)}）——空间分布需避免共线")
    return points


def triangulate_sfm_xyz(photo_uv: list, images: dict, cameras: dict,
                        min_views: int = 2) -> np.ndarray:
    """多视图线性三角化：照片像素坐标 + 位姿(R,t 相机中心) + K -> SfM 系 3D。

    photo_uv: [{"image": "img_01.jpg", "u": ..., "v": ...}, ...]
    images:   {name: {"R": (3,3) 世界->相机, "t": 相机中心}}
    cameras:  {cam_id: {"fx","fy","cx","cy"}}
    返回 (3,) SfM 系 3D 点；视图不足/退化抛 ControlPointError。
    """
    A = []
    for pv in photo_uv:
        im = images.get(pv["image"])
        if im is None:
            continue
        cam = cameras.get(im["cam_id"])
        if cam is None:
            continue
        R = np.asarray(im["R"], dtype=np.float64)
        t = np.asarray(im["t"], dtype=np.float64)
        fx, fy, cx, cy = cam["fx"], cam["fy"], cam["cx"], cam["cy"]
        # 像素 -> 归一化射线（相机系）：方向 = (Xc,Yc,Zc) ∝ (nx, ny, 1)
        # 注：相机前方 Zc<0，方向向量 (nx,ny,1) 经负标量 Zc 缩放即 (Xc,Yc,Zc)。
        u, v = pv["u"], pv["v"]
        nx = (u - cx) / fx
        ny = (v - cy) / fy
        d_cam = np.array([nx, ny, 1.0])
        d_world = R.T @ d_cam          # 世界系方向（相机系 -> 世界系）
        # 点在世界系：X = t + λ*d（t 为相机中心）
        # 约束 d × (X - t) = 0 -> 两行线性方程
        c = np.cross(d_world, np.array([1.0, 0.0, 0.0]))
        A.append(np.array([c[0], c[1], c[2], -(c @ t)]))
        c = np.cross(d_world, np.array([0.0, 1.0, 0.0]))
        A.append(np.array([c[0], c[1], c[2], -(c @ t)]))
    A = np.array(A)
    # 每视图贡献 2 行线性方程；>=2 视图（4 行）即可三角化
    if len(A) < 2 * max(min_views, 2):
        raise ControlPointError("控制点照片观测不足（需 >=2 视图）")
    try:
        _, _, Vt = np.linalg.svd(A)
        X = Vt[-1]
        X = X[:3] / X[3]
    except np.linalg.LinAlgError as exc:  # pragma: no cover
        raise ControlPointError(f"三角化奇异: {exc}") from exc
    return X


def _project_uv(xyz: np.ndarray, im: dict, cam: dict):
    """3D 点 → 像素坐标（OpenGL/COLMAP 惯例：相机前方 z<0）。"""
    R = np.asarray(im["R"], dtype=np.float64)
    t = np.asarray(im["t"], dtype=np.float64)
    pc = R @ (xyz - t)
    if pc[2] >= -0.05:
        return None
    u = cam["fx"] * pc[0] / pc[2] + cam["cx"]
    v = cam["fy"] * pc[1] / pc[2] + cam["cy"]
    return np.array([u, v])


def calibrate_ransac_params(photo_uv: list, images: dict, cameras: dict):
    """RANSAC 参数自适应标定（P2）：依视图数与噪声水平确定 n_trials / threshold_px。

    策略：
      n_trials      视图越多采样越充分（64~192，指数级保证外点覆盖概率）；
      threshold_px  用「全量最小二乘三角化」的重投影误差中位数估计噪声，
                    噪声小收紧（≥2px）、噪声大放宽（≤6px），下限 2px 防退化。
    返回 (n_trials, threshold_px, noise_est_px)。
    """
    views = [(pv["image"], float(pv["u"]), float(pv["v"])) for pv in photo_uv]
    views = [v for v in views if v[0] in images]
    n = len(views)
    n_trials = max(64, min(192, 8 * n))
    if n < 2:
        raise ControlPointError("控制点照片观测不足（需 >=2 视图）")
    try:
        x = triangulate_sfm_xyz(
            [{"image": v[0], "u": v[1], "v": v[2]} for v in views],
            images, cameras)
    except ControlPointError:
        return n_trials, 4.0, 4.0  # 全量退化时用默认宽松阈值
    errs = []
    for name, u, v in views:
        im = images.get(name)
        if im is None:
            continue
        cam = cameras.get(im["cam_id"])
        if cam is None:
            continue
        uv = _project_uv(x, im, cam)
        if uv is not None:
            errs.append(np.hypot(uv[0] - u, uv[1] - v))
    noise = float(np.median(errs)) if errs else 0.0
    threshold = float(np.clip(noise * 2.5, 2.0, 6.0))
    return n_trials, threshold, round(noise, 3)


def triangulate_sfm_xyz_ransac(photo_uv: list, images: dict, cameras: dict,
                               min_views: int = 2, n_trials: int = 0,
                               threshold_px: float = 0.0,
                               seed: int = 0) -> tuple:
    """RANSAC 抗噪三角化（P1）：随机采 2 视图三角化 → 其余视图重投影内点
    计数 → 最优模型 → 全部内点最小二乘重估。

    对控制点观测中的外点（手工标注误差 / ArUco 检测错误）鲁棒。
    n_trials<=0 或 threshold_px<=0 时自动调用 :func:`calibrate_ransac_params`
    自适应标定（P2）。
    返回 (xyz, n_inliers, n_total_views)。
    """
    if n_trials <= 0 or threshold_px <= 0:
        n_trials, threshold_px, _noise = calibrate_ransac_params(
            photo_uv, images, cameras)
    rng = np.random.default_rng(seed)
    views = [(pv["image"], float(pv["u"]), float(pv["v"])) for pv in photo_uv]
    views = [v for v in views if v[0] in images]
    if len(views) < 2:
        raise ControlPointError("控制点照片观测不足（需 >=2 视图）")

    def _inliers(x):
        cnt = 0
        for name, u, v in views:
            im = images[name]
            cam = cameras.get(im["cam_id"])
            if cam is None:
                continue
            uv = _project_uv(x, im, cam)
            if uv is None:
                continue
            if np.hypot(uv[0] - u, uv[1] - v) < threshold_px:
                cnt += 1
        return cnt

    best_x, best_n = None, -1
    for _t in range(n_trials):
        idx = rng.choice(len(views), size=2, replace=False)
        sub = [{"image": views[i][0], "u": views[i][1], "v": views[i][2]}
               for i in idx]
        try:
            x = triangulate_sfm_xyz(sub, images, cameras)
        except ControlPointError:
            continue
        n = _inliers(x)
        if n > best_n:
            best_n, best_x = n, x
    if best_x is None or best_n < min_views:
        raise ControlPointError(
            f"RANSAC 三角化失败（内点 {best_n} < {min_views}）——观测噪声过大")
    inl = [{"image": v[0], "u": v[1], "v": v[2]} for v in views
           if _reproj_ok(best_x, v, images, cameras, threshold_px)]
    x_final = triangulate_sfm_xyz(inl, images, cameras)
    return x_final, best_n, len(views)


def _reproj_ok(xyz, view, images, cameras, thr):
    name, u, v = view
    im = images.get(name)
    if im is None:
        return False
    cam = cameras.get(im["cam_id"])
    if cam is None:
        return False
    uv = _project_uv(xyz, im, cam)
    return uv is not None and np.hypot(uv[0] - u, uv[1] - v) < thr


def solve_control_alignment(sfm_xyz: np.ndarray, laser_xyz: np.ndarray):
    """SfM 系 ↔ 激光系 Umeyama：拟合 laser ≈ s*R@sfm + T。

    返回 (s, R, T, rmse)：把 SfM 系点变换到激光几何系。
    需要 >=3 对不共线对应（精度检查：非零 SVD 最小奇异值）。
    """
    if len(sfm_xyz) < 3:
        raise ControlPointError("对齐需要 >=3 对控制点")
    mu_s = sfm_xyz.mean(axis=0)
    mu_d = laser_xyz.mean(axis=0)
    src_c = sfm_xyz - mu_s
    dst_c = laser_xyz - mu_d
    H = src_c.T @ dst_c
    U, S, Vt = np.linalg.svd(H)
    if S[1] < 1e-9 * max(S[0], 1e-12):
        raise ControlPointError("控制点近似共线，无法求解对齐（请增加非共线分布）")
    R = Vt.T @ U.T
    if np.linalg.det(R) < 0:
        Vt[-1] *= -1
        R = Vt.T @ U.T
    s = np.sum(dst_c * (src_c @ R.T)) / np.sum(src_c**2)
    T = mu_d - s * R @ mu_s
    rmse = np.sqrt(np.mean(np.sum(
        ((src_c @ R.T) * s - dst_c) ** 2, axis=1)))
    return float(s), R, T, float(rmse)


def align_mesh_with_controls(mesh, controls: dict, images: dict,
                             cameras: dict):
    """控制点引导对齐：三角化 SfM 系坐标 + Umeyama -> 几何变换到 SfM 系。

    返回 (new_mesh, metrics)；控制点不足/三角化失败抛 ControlPointError。
    几何变换：X_sfm = R^T (X_laser - T) / s（保持位姿不变，仅移动几何）。
    """
    from h3d.geo_align import apply_similarity

    sfm_list, laser_list = [], []
    used = 0
    for cid, cp in controls.items():
        if len(cp["photo_uv"]) < 2:
            continue
        try:
            x_sfm, n_inl, _n_views = triangulate_sfm_xyz_ransac(
                cp["photo_uv"], images, cameras)
        except ControlPointError:
            continue
        sfm_list.append(x_sfm)
        laser_list.append(cp["laser_xyz"])
        used += 1
    if used < 3:
        raise ControlPointError(
            f"可用控制点（>=2 视图）不足 3 个（实际 {used}）")
    s, R, T, rmse = solve_control_alignment(np.array(sfm_list),
                                            np.array(laser_list))
    import open3d as o3d
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    new_verts = (R.T @ (verts - T).T).T / s  # 激光系 -> SfM 系
    new_mesh = o3d.geometry.TriangleMesh()
    new_mesh.vertices = o3d.utility.Vector3dVector(new_verts)
    new_mesh.triangles = mesh.triangles
    if mesh.has_vertex_colors():
        new_mesh.vertex_colors = mesh.vertex_colors
    if mesh.has_vertex_normals():
        new_mesh.vertex_normals = mesh.vertex_normals
    metrics = {
        "method": "control_points",
        "n_control_points": used,
        "scale_s": round(float(s), 5),
        "rmse_after": round(float(rmse), 6),
    }
    return new_mesh, metrics


def refine_mesh_to_points(mesh, src_points: np.ndarray,
                          iterations: int = 5, n_samples: int = 2000,
                          inlier_percentile: float = 90.0):
    """最近邻精化（P2 第二阶段）：控制点粗对齐后，用 SfM 稀疏点云精化网格。

    迭代「网格表面采样 ↔ src_points 最近邻对应 → Umeyama 相似变换」
    （变换作用在网格上，src 不动），把网格微调到贴合稀疏点云。
    返回 (new_mesh, metrics)。
    """
    from scipy.spatial import cKDTree

    import open3d as o3d
    from h3d.geo_align import umeyama_similarity

    verts = np.asarray(mesh.vertices, dtype=np.float64)
    new_verts = verts.copy()
    final_rmse = None
    for _it in range(iterations):
        work = o3d.geometry.TriangleMesh()
        work.vertices = o3d.utility.Vector3dVector(new_verts)
        work.triangles = mesh.triangles
        surf = work.sample_points_uniformly(n_samples)
        surf_pts = np.asarray(surf.points, dtype=np.float64)
        tree = cKDTree(src_points)
        d, idx = tree.query(surf_pts, k=1)
        thr = np.percentile(d, inlier_percentile) * 2.0
        keep = d < thr
        if keep.sum() < 8:
            break
        s, R, T = umeyama_similarity(surf_pts[keep], src_points[idx[keep]])
        new_verts = s * (new_verts @ R.T) + T
        final_rmse = np.sqrt(np.mean(d[keep] ** 2))
    new_mesh = o3d.geometry.TriangleMesh()
    new_mesh.vertices = o3d.utility.Vector3dVector(new_verts)
    new_mesh.triangles = mesh.triangles
    if mesh.has_vertex_colors():
        new_mesh.vertex_colors = mesh.vertex_colors
    if mesh.has_vertex_normals():
        new_mesh.vertex_normals = mesh.vertex_normals
    metrics = {
        "method": "control_points + nearest_refine",
        "iterations": iterations,
        "rmse_after": round(float(final_rmse) if final_rmse else 0.0, 6),
    }
    return new_mesh, metrics


def align_two_stage(mesh, controls: dict, images: dict, cameras: dict,
                    sparse_points: np.ndarray):
    """两阶段对齐（P2）：①控制点粗对齐（Umeyama 闭环解）→ ②最近邻精化
    （稀疏点云贴合）。返回 (new_mesh, metrics)。
    """
    mesh, m1 = align_mesh_with_controls(mesh, controls, images, cameras)
    if sparse_points is not None and len(sparse_points) >= 8:
        mesh, m2 = refine_mesh_to_points(mesh, sparse_points)
        m1.update(m2)
    return mesh, m1


def find_ctrl_file(task_root: str, item_id: str) -> Optional[str]:
    """查找 00_ctrl/<item>/control_points.json（项目根目录）。"""
    path = os.path.join(task_root, CTRL_DIR, item_id, CONTROL_POINTS_FILE)
    return path if os.path.exists(path) else None


def detect_aruco_uv(image_path: str, dictionary: str = "DICT_4X4_50",
                    marker_length_m: float = 0.05):
    """ArUco 靶标自动检测（可选增强，需 opencv-python）。

    返回 [{marker_id, corners(4x2 像素), center_uv}]；未安装 opencv 抛
    ControlPointError 提示安装 opencv-python-headless。
    """
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover
        raise ControlPointError(
            "ArUco 自动检测需要 opencv-python-headless（pip 安装）；"
            "或使用手工标注 photo_uv") from exc
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ControlPointError(f"无法读取图像: {image_path}")
    aruco = cv2.aruco
    dict_map = {f"DICT_{n}X{n}_{m}" for n in (4, 5, 6, 7) for m in (50, 250, 1000)}
    if not hasattr(aruco, dictionary):
        dictionary = "DICT_4X4_50"
    aruco_dict = aruco.getPredefinedDictionary(getattr(aruco, dictionary))
    params = aruco.DetectorParameters()
    detector = aruco.ArucoDetector(aruco_dict, params)
    corners, ids, _rejected = detector.detectMarkers(img)
    out = []
    if ids is not None:
        for i, mid in enumerate(ids.flatten()):
            c = corners[i].reshape(-1, 2)
            out.append({"marker_id": int(mid), "center_uv": c.mean(axis=0).tolist(),
                        "corners": c.tolist()})
    return out


__all__ = [
    "CONTROL_POINTS_FILE", "CTRL_DIR", "ControlPointError",
    "load_control_points", "triangulate_sfm_xyz",
    "triangulate_sfm_xyz_ransac", "calibrate_ransac_params",
    "solve_control_alignment",
    "align_mesh_with_controls", "refine_mesh_to_points", "align_two_stage",
    "find_ctrl_file", "detect_aruco_uv",
]
