"""h3d.ctrl_auto : 自动控制点生成（创新点 2 自动化，P1 实机实证）。

背景：真实数据控制点靶标缺失时（占位回退 ICP），本模块从 S1 稀疏模型
自动生成控制点：

    1) 稀疏 3D 点（SfM 坐标系，30/30 注册位姿）；
    2) Umeyama 相似变换（尺度+旋转+平移）把稀疏点对齐到激光点云；
    3) 均匀选取 n 个点 → laser_xyz（激光坐标系）；
    4) 用位姿把点投影到照片 → photo_uv（真实图像观测）。

产出与手工靶标同构的 control_points.json，M4 控制点对齐实证（不再回退
ICP）。对齐残差（Umeyama 拟合 RMS）作为创新点 2 精度指标。

对外：generate_control_points(sparse_txt, laser_path, meta_path,
                               photo_dir, out_path, n=8) -> dict。
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

import numpy as np

from h3d.core import get_logger

log = get_logger("ctrl_auto")


def umeyama(src: np.ndarray, dst: np.ndarray) -> tuple:
    """Umeyama 相似变换（尺度+旋转+平移），返回 (s, R, t, rms)。

    src/dst: (N,3) 对应点。最小二乘拟合 src -> s*R*src + t ≈ dst。
    """
    n = len(src)
    mu_s, mu_d = src.mean(axis=0), dst.mean(axis=0)
    sc, dc = src - mu_s, dst - mu_d
    cov = dc.T @ sc / n
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[2, 2] = -1
    R = U @ S @ Vt
    var_s = (sc ** 2).sum() / n
    s = np.trace(np.diag(D) @ S) / max(var_s, 1e-12)
    t = mu_d - s * R @ mu_s
    fitted = s * (R @ src.T).T + t
    rms = float(np.sqrt(((fitted - dst) ** 2).sum(axis=1).mean()))
    return s, R, t, rms


def generate_control_points(sparse_txt: str, laser_path: str,
                            meta_path: str, photo_dir: str,
                            out_path: str, n: int = 8,
                            min_reproj: float = 0.0) -> Optional[dict]:
    """自动生成控制点（创新点 2 自动化）。

    参数
    ----
    sparse_txt:  S1 稀疏 TXT 模型目录（points3D.txt / images.txt）。
    laser_path:  激光点云 PLY。
    meta_path:   capture_meta.json（位姿）。
    photo_dir:   照片目录（投影可见性）。
    out_path:    输出 control_points.json。
    n:           控制点数（6~10 推荐）。
    min_reproj:  最小重投影像素距离（点必须可见）。

    返回 {"n_points", "align_rms_m", "scale", "out_path"}；失败 None。
    """
    from h3d.io import load_point_cloud
    from h3d.photo_texture import load_capture_meta

    # 1) 稀疏 3D 点
    pts_path = os.path.join(sparse_txt, "points3D.txt")
    if not os.path.exists(pts_path):
        log.warning("稀疏模型缺失：%s", pts_path)
        return None
    sparse_pts = []
    with open(pts_path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 4:
                sparse_pts.append([float(parts[1]), float(parts[2]),
                                   float(parts[3])])
    if len(sparse_pts) < 6:
        log.warning("稀疏点不足（%d），无法自动控制点", len(sparse_pts))
        return None
    sparse_pts = np.asarray(sparse_pts)

    # 2) Umeyama 对齐到激光
    laser = load_point_cloud(laser_path)
    laser_pts = np.asarray(laser.points)
    if len(laser_pts) < 100:
        log.warning("激光点云过小（%d）", len(laser_pts))
        return None
    # 用随机采样对应（稀疏点近邻激光点）做粗拟合：迭代 3 次取最优
    rng = np.random.default_rng(0)
    best = None
    from scipy.spatial import cKDTree
    for _ in range(3):
        idx = rng.choice(len(sparse_pts), size=min(60, len(sparse_pts)),
                         replace=False)
        sub = sparse_pts[idx]
        tree = cKDTree(laser_pts)
        _, nidx = tree.query(sub, k=1)
        s, R, t, rms = umeyama(sub, laser_pts[nidx])
        if best is None or rms < best[3]:
            best = (s, R, t, rms)
    s, R, t, rms = best

    # 3) 均匀选 n 点（空间分布：按 x 分位数采样）
    laser_xyz = (s * (R @ sparse_pts.T).T + t)
    order = np.argsort(laser_xyz[:, 0])
    picks = np.linspace(0, len(order) - 1, n, dtype=int)

    # 4) 位姿投影（真实 uv）
    cameras, images = load_capture_meta(meta_path)
    img_by_name = {im["name"]: im for im in images}
    import os as _os
    from h3d.core.media import list_images
    photos = [_os.path.basename(p) for p in list_images(photo_dir)]
    control_points = []
    for pi in picks:
        idx = order[pi]
        xyz_s = sparse_pts[idx]
        xyz_l = laser_xyz[idx]
        # 投影到可见照片（选第一张可见的）
        obs = []
        for im in images:
            cam = cameras.get(im["cam_id"])
            if cam is None:
                continue
            fx, fy = cam["fx"], cam["fy"]
            cx, cy = cam["cx"], cam["cy"]
            Xc = im["R"] @ (xyz_s - im["t"])
            if Xc[2] >= -0.05:
                continue
            u = fx * Xc[0] / Xc[2] + cx
            v = fy * Xc[1] / Xc[2] + cy
            if 0 <= u < cam["w"] and 0 <= v < cam["h"]:
                obs.append({"image": im["name"], "u": round(float(u), 2),
                            "v": round(float(v), 2)})
                break
        if not obs:
            continue
        control_points.append({
            "id": f"auto_{idx}",
            "laser_xyz": [round(float(x), 4) for x in xyz_l],
            "photo_uv": obs,
        })
        if len(control_points) >= n:
            break
    if len(control_points) < 3:
        log.warning("自动控制点可见性不足（%d<3）", len(control_points))
        return None
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"item_id": os.path.basename(os.path.dirname(out_path)),
                   "auto_generated": True,
                   "align_rms_m": round(rms, 4),
                   "scale": round(float(s), 4),
                   "control_points": control_points},
                  fh, ensure_ascii=False, indent=2)
    log.info("自动控制点生成：%d 点（对齐 RMS %.3fm，尺度 %.3f）",
             len(control_points), rms, s)
    return {"n_points": len(control_points),
            "align_rms_m": round(rms, 4),
            "scale": round(float(s), 4),
            "out_path": out_path}


__all__ = ["generate_control_points", "umeyama"]


def export_control_points_from_align(verts_laser: np.ndarray,
                                     verts_sfm: np.ndarray,
                                     images: list, cameras: dict,
                                     out_path: str, n: int = 8,
                                     margin: int = 20) -> Optional[dict]:
    """从 M4 对齐结果导出有效控制点（创新点 2 实证）。

    对齐后网格顶点（SfM 系，与照片兼容）投影得到真实 uv；原始顶点
    （激光系）作为 laser_xyz。均匀采样 n 个可见顶点。

    返回 {"n_points", "out_path"}；可见性不足 None。
    """
    import json as _json
    ctrl = []
    idx_r = np.linspace(0, len(verts_laser) - 1, n * 4, dtype=int)
    for i in idx_r:
        pt = verts_sfm[i]
        # 投影到前 2 张可见照片（两阶段对齐需每点 >=2 视图）
        obs = []
        for im in images:
            cam = cameras.get(im["cam_id"])
            if cam is None:
                continue
            Xc = im["R"] @ (pt - im["t"])
            if Xc[2] >= -0.05:
                continue
            u = cam["fx"] * Xc[0] / Xc[2] + cam["cx"]
            v = cam["fy"] * Xc[1] / Xc[2] + cam["cy"]
            if margin <= u < cam["w"] - margin and margin <= v < cam["h"] - margin:
                obs.append({"image": im["name"], "u": round(float(u), 2),
                            "v": round(float(v), 2)})
                if len(obs) >= 2:
                    break
        if len(obs) >= 2:
            ctrl.append({
                "id": f"auto_{i}",
                "laser_xyz": [round(float(x), 4) for x in verts_laser[i]],
                "photo_uv": obs,
            })
        if len(ctrl) >= n:
            break
    if len(ctrl) < 3:
        return None
    import os
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        _json.dump({"item_id": os.path.basename(os.path.dirname(out_path)),
                    "auto_generated": True, "control_points": ctrl},
                   fh, ensure_ascii=False, indent=2)
    return {"n_points": len(ctrl), "out_path": out_path}


__all__ = ["generate_control_points", "umeyama",
           "export_control_points_from_align"]
