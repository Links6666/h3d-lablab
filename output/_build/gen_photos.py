# -*- coding: utf-8 -*-
"""生成合成多视角照片集（软件光栅化渲染，确定性、无 GPU 依赖）。

原理：把带颜色+法线的 bumpy 网格用 numpy 软件渲染器投影到 40 个环绕+俯仰
相机位姿，输出 JPG「照片」（含顶点色渐变 + Lambert 明暗 → 足够 SfM 特征匹配）。
相机位姿真值（球坐标环绕）同时保存为 capture_meta_gt.json，用于 SfM 精度验证。
"""
import json
import os

import numpy as np
import open3d as o3d
from PIL import Image


def _bumpy_mesh(seed=7, n_grid=64):
    rng = np.random.default_rng(seed)
    u = np.linspace(0, 2 * np.pi, n_grid)
    v = np.linspace(0, np.pi, n_grid // 2)
    U, V = np.meshgrid(u, v, indexing="ij")
    r = 1.0 + 0.18 * np.sin(3 * U) * np.cos(2 * V) + 0.12 * np.cos(5 * U) * np.sin(3 * V)
    x = r * np.sin(V) * np.cos(U)
    y = r * np.sin(V) * np.sin(U)
    z = r * np.cos(V)
    pts = np.stack([x, y, z], axis=-1).reshape(-1, 3)
    mesh = o3d.geometry.TriangleMesh()
    rows, cols = n_grid // 2, n_grid
    faces = []
    for i in range(rows - 1):
        for j in range(cols - 1):
            a = i * cols + j
            b = a + 1
            c = a + cols
            d = c + 1
            faces.append([a, b, c])
            faces.append([b, d, c])
    mesh.vertices = o3d.utility.Vector3dVector(pts)
    mesh.triangles = o3d.utility.Vector3iVector(np.array(faces, dtype=np.int32))
    mesh.compute_vertex_normals()
    pn = pts - pts.min(axis=0)
    span = pn.max(axis=0)
    colors = pn / span.max(axis=0)
    colors = 0.55 + 0.35 * colors
    # 高频纹理叠加（为 SIFT 提供可检测角点/边缘）：
    # ① u 方向条纹  ② 随机斑点噪声  ③ 细棋盘格边缘
    stripe = (np.sin(8 * U.ravel()) + 1) / 2
    rng2 = np.random.default_rng(123)
    blob = rng2.random(len(pts))  # 逐顶点随机噪声（高频）
    checker = ((np.sin(60 * U.ravel()) > 0) != (np.sin(60 * V.ravel()) > 0)).astype(float)
    texture = 0.45 * stripe + 0.30 * blob + 0.25 * checker
    colors = np.clip(colors * (0.5 + 1.0 * texture[:, None]), 0, 1)
    mesh.vertex_colors = o3d.utility.Vector3dVector(colors)
    return mesh


def _lookat(eye, center, up=np.array([0.0, 0.0, 1.0])):
    f = center - eye
    f = f / np.linalg.norm(f)
    s = np.cross(f, up)
    s = s / np.linalg.norm(s)
    u = np.cross(s, f)
    R = np.stack([s, u, -f], axis=1)
    t = -R.T @ eye
    return R.T, t


def _render(mesh, R, t, K, width=800, height=600):
    """向量化软件光栅化：透视投影 + Z-buffer + 重心插值 + Lambert 光照。"""
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    colors = np.asarray(mesh.vertex_colors, dtype=np.float64)
    normals = np.asarray(mesh.vertex_normals, dtype=np.float64)
    faces = np.asarray(mesh.triangles, dtype=np.int64)

    cam = verts @ R.T + t
    z_cam = cam[:, 2]
    proj = cam @ K.T
    x_px = proj[:, 0] / z_cam
    y_px = proj[:, 1] / z_cam
    # OpenGL/COLMAP 相机惯例：物体在相机前方 z_cam < 0
    inside = (z_cam < -0.1) & (x_px >= 0) & (x_px < width) & (y_px >= 0) & (y_px < height)

    light_dir = np.array([0.3, 0.4, 0.8])
    light_dir = light_dir / np.linalg.norm(light_dir)
    lambert = np.clip(normals @ light_dir, 0.15, 1.0)

    img = np.zeros((height, width, 3), dtype=np.float64)
    zbuf = np.full((height, width), np.inf)

    for f in faces:
        if not inside[f].all():
            continue
        p0 = np.array([x_px[f[0]], y_px[f[0]]])
        p1 = np.array([x_px[f[1]], y_px[f[1]]])
        p2 = np.array([x_px[f[2]], y_px[f[2]]])
        zz = z_cam[f]
        cc = colors[f] * lambert[f][:, None]  # (3,3)
        # bbox 内像素网格（向量化）
        xs = [p0[0], p1[0], p2[0]]
        ys = [p0[1], p1[1], p2[1]]
        xmin, xmax = max(0, int(np.floor(min(xs)))), min(width - 1, int(np.ceil(max(xs))))
        ymin, ymax = max(0, int(np.floor(min(ys)))), min(height - 1, int(np.ceil(max(ys))))
        if xmax < xmin or ymax < ymin:
            continue
        gx, gy = np.meshgrid(np.arange(xmin, xmax + 1) + 0.5,
                             np.arange(ymin, ymax + 1) + 0.5)
        px = np.stack([gx.ravel(), gy.ravel()], axis=1)  # (P,2)
        denom = (p1[1] - p2[1]) * (p0[0] - p2[0]) + (p2[0] - p1[0]) * (p0[1] - p2[1])
        if abs(denom) < 1e-12:
            continue
        w0 = ((p1[1] - p2[1]) * (px[:, 0] - p2[0]) + (p2[0] - p1[0]) * (px[:, 1] - p2[1])) / denom
        w1 = ((p2[1] - p0[1]) * (px[:, 0] - p2[0]) + (p0[0] - p2[0]) * (px[:, 1] - p2[1])) / denom
        w2 = 1.0 - w0 - w1
        in_tri = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not in_tri.any():
            continue
        w0, w1, w2 = w0[in_tri], w1[in_tri], w2[in_tri]
        sel = px[in_tri]
        z = w0 * zz[0] + w1 * zz[1] + w2 * zz[2]
        col = w0[:, None] * cc[0] + w1[:, None] * cc[1] + w2[:, None] * cc[2]
        sy = sel[:, 1].astype(np.int64)
        sx = sel[:, 0].astype(np.int64)
        closer = z < zbuf[sy, sx]
        zbuf[sy[closer], sx[closer]] = z[closer]
        img[sy[closer], sx[closer]] = col[closer]
    return np.clip(img * 255, 0, 255).astype(np.uint8)


def main():
    out_root = r"E:/h3d_lablab/output/_colmap_demo/00_photo/rock001"
    os.makedirs(out_root, exist_ok=True)
    mesh = _bumpy_mesh()
    center = np.array([0.0, 0.0, 0.0])
    K = np.array([[800.0, 0, 400.0], [0, 800.0, 300.0], [0, 0, 1.0]])
    width, height = 800, 600

    n_az, n_el = 12, 5  # 60 张：12 方位角 x 5 俯仰角（视角更密、重叠更大）
    meta_gt = {"K": K.tolist(), "images": []}
    for ia in range(n_az):
        for ie in range(n_el):
            az = (2 * np.pi * ia) / n_az
            el = (np.pi * (ie + 1)) / (n_el + 1)
            dist = 3.6
            eye = np.array([
                dist * np.cos(el) * np.cos(az),
                dist * np.cos(el) * np.sin(az),
                dist * np.sin(el),
            ])
            R, t = _lookat(eye, center)
            img = _render(mesh, R, t, K, width, height)
            name = f"img_{ia:02d}_{ie:02d}.jpg"
            Image.fromarray(img).save(os.path.join(out_root, name), quality=95)
            meta_gt["images"].append({
                "name": name, "R": R.tolist(), "t": t.tolist(),
                "azimuth": round(az, 4), "elevation": round(el, 4),
            })
    with open(os.path.join(out_root, "..", "capture_meta_gt.json"), "w", encoding="utf-8") as fh:
        json.dump(meta_gt, fh, ensure_ascii=False, indent=2)
    print(f"照片集生成完成：{n_az * n_el} 张 -> {out_root}")


if __name__ == "__main__":
    main()
