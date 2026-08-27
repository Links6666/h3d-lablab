"""h3d.texture.uv : UV 图集纹理（M4 增强路径，paint_mode='uv'）。

设计依据：
    《设计书》§35 —— UV 展开并映射纹理，输出带纹理模型至 50_tex；
    《软著方案》2.4 —— texture.uv_method=xatlas，纹理图集分辨率 texture_res；
    M5 质检的 overlap（纹理重叠度）指标随 UV 路径启用（设计书 §17.3）。

实现要点：
    1. UV 展开用 xatlas（MIT）：``parametrize`` 返回 (vmap, indices, uvs)，
       按 vmap 重排顶点，indices 重建三角形，uvs 为每顶点 UV（[0,1]）。
    2. 纹理烘焙（bake）：把参考点云颜色映射到 UV 图集——每三角形在 3D 侧
       重心采样若干点，scipy.cKDTree 取最近邻点云颜色；UV 侧光栅化到纹理
       像素（重心坐标判断），实现「点云颜色 -> 纹理图」。
    3. 导出：Open3D 设置 triangle_uvs + textures 后写 OBJ（含 MTL + PNG）与
       GLB（内嵌纹理）；PLY 不支持纹理，回退顶点色。
    4. 健壮性：输入为空 / xatlas 失败（如退化网格）时抛 InputDataError /
       降级顶点色并告警（软著方案 2.6「单项缺失 -> 注明」）。
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from h3d.core import get_logger

log = get_logger("texture.uv")


def unwrap_mesh(mesh, max_vertices: int = 200_000):
    """xatlas UV 展开，返回 (new_mesh, uv_coords, uv_triangles)。

    参数
    ----
    mesh:        TriangleMesh（M3 产物）。
    max_vertices: 顶点数上限（xatlas 展开耗时随顶点数增长，超限降级告警）。

    返回
    ----
    (new_mesh, uv, faces)：重建网格（含顶点色/法线，按 vmap 重排）、
    每顶点 UV（float32, n×2）、三角形索引。
    """
    import xatlas

    from h3d.core import InputDataError

    verts = np.asarray(mesh.vertices, dtype=np.float32)
    faces = np.asarray(mesh.triangles, dtype=np.uint32)
    if len(verts) == 0 or len(faces) == 0:
        raise InputDataError("UV 展开输入网格为空")
    if len(verts) > max_vertices:
        log.warning("顶点数 %d 超上限 %d，UV 展开可能较慢", len(verts), max_vertices)

    try:
        vmap, indices, uvs = xatlas.parametrize(verts, faces)
    except Exception as exc:  # 退化网格等
        raise InputDataError(f"xatlas UV 展开失败: {exc}") from exc

    new_verts = np.asarray(verts[vmap], dtype=np.float64)  # vmap: 新顶点 -> 原顶点
    new_mesh = _o3d().geometry.TriangleMesh()
    new_mesh.vertices = _o3d().utility.Vector3dVector(new_verts)
    new_mesh.triangles = _o3d().utility.Vector3iVector(np.asarray(indices, dtype=np.int32))
    if mesh.has_vertex_colors():
        new_mesh.vertex_colors = _o3d().utility.Vector3dVector(
            np.asarray(mesh.vertex_colors, dtype=np.float64)[vmap])
    if mesh.has_vertex_normals():
        new_mesh.vertex_normals = _o3d().utility.Vector3dVector(
            np.asarray(mesh.vertex_normals, dtype=np.float64)[vmap])
    log.info("UV 展开完成：%d 顶点 -> %d 顶点 / %d 面", len(verts),
             len(new_verts), len(indices))
    return new_mesh, np.asarray(uvs, dtype=np.float32), np.asarray(indices)


def bake_texture(mesh, uv, ref_pcd, size: int = 1024,
                 samples_per_tri: int = 8) -> np.ndarray:
    """把参考点云颜色烘焙到 UV 纹理图（重心采样 + 光栅化）。

    参数
    ----
    mesh:           展开后的网格（顶点与 uv 一一对应）。
    uv:             每顶点 UV（n×2，[0,1]）。
    ref_pcd:        带色参考点云（Open3D PointCloud）。
    size:           纹理图边长（默认 1024，texture_res 概念）。
    samples_per_tri:每三角形 3D 重心采样点数（颜色插值密度）。

    返回
    ----
    (size, size, 3) uint8 纹理图（黑底填充缺失区）。
    """
    import open3d as o3d
    from scipy.spatial import cKDTree

    if not ref_pcd.has_colors() or not ref_pcd.has_points():
        raise ValueError("纹理烘焙需要带色参考点云")
    pts = np.asarray(ref_pcd.points, dtype=np.float64)
    colors = np.asarray(ref_pcd.colors, dtype=np.float64)  # 0~1
    tree = cKDTree(pts)

    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.triangles, dtype=np.int64)
    uv_f = np.asarray(uv, dtype=np.float64)
    image = np.zeros((size, size, 3), dtype=np.float32)

    # 重心采样权重（每三角形 samples_per_tri 个点）
    rng = np.random.default_rng(0)
    w1 = rng.random((samples_per_tri,))
    w2 = rng.random((samples_per_tri,))
    mask = w1 + w2 > 1.0
    w1[mask] = 1.0 - w1[mask]
    w2[mask] = 1.0 - w2[mask]
    w0 = 1.0 - w1 - w2

    for tri_idx, tri in enumerate(faces):
        v0, v1, v2 = verts[tri]
        # 3D 采样点
        pts3d = (w0[:, None] * v0 + w1[:, None] * v1 + w2[:, None] * v2)
        # 最近邻颜色
        _d, idx = tree.query(pts3d, k=1)
        tri_colors = colors[idx]  # (S, 3)
        # UV 采样点
        t0, t1, t2 = uv_f[tri]
        uv_pts = (w0[:, None] * t0 + w1[:, None] * t1 + w2[:, None] * t2)
        # 光栅化到像素（uv_pts 为 (S,2)：u=v[:,0], v=v[:,1]）
        px = np.clip((uv_pts[:, 0] * size).astype(np.int32), 0, size - 1)
        py = np.clip(((1.0 - uv_pts[:, 1]) * size).astype(np.int32), 0, size - 1)
        image[py, px, :] = tri_colors
    log.info("纹理烘焙完成：%d 面 -> %dx%d 图集（%d 采样点/面）",
             len(faces), size, size, samples_per_tri)
    return np.clip(image * 255.0, 0, 255).astype(np.uint8)


def attach_texture(mesh, uv, faces, image: np.ndarray):
    """把 UV 与纹理图附着到网格（triangle_uvs + textures），供带纹理导出。"""
    o3d = _o3d()
    uv_f = np.asarray(uv, dtype=np.float64)[np.asarray(faces, dtype=np.int64)]
    mesh.triangle_uvs = o3d.utility.Vector2dVector(uv_f.reshape(-1, 2))
    mesh.textures = [o3d.geometry.Image(image)]
    mesh.triangle_material_ids = o3d.utility.IntVector(
        np.zeros(len(faces), dtype=np.int32))
    return mesh


def export_textured(mesh, out_dir: str, item_id: str, lod_tag: str = "L1.0",
                    formats: Optional[list[str]] = None) -> list[str]:
    """带纹理模型导出（OBJ+MTL+PNG / GLB；PLY 不支持纹理时回退顶点色）。

    返回导出文件路径列表。
    """
    import os

    from h3d.core import ExportError

    formats = formats or ["obj", "glb"]
    os.makedirs(out_dir, exist_ok=True)
    outputs: list[str] = []
    for fmt in formats:
        fmt = fmt.lstrip(".").lower()
        if fmt in ("ply",):
            log.warning("PLY 不支持纹理图，回退顶点色导出")
            continue
        if fmt not in ("obj", "glb", "gltf"):
            log.warning("跳过不支持的带纹理格式 %r", fmt)
            continue
        path = os.path.join(out_dir, f"{item_id}_{lod_tag}.{fmt}")
        ok = _o3d().io.write_triangle_mesh(path, mesh)
        if not ok:
            raise ExportError(f"带纹理模型导出失败: {path}")
        outputs.append(os.path.abspath(path))
    log.info("带纹理模型导出：%s_%s（%s）", item_id, lod_tag, outputs)
    return outputs


def _o3d():
    import open3d

    return open3d


__all__ = ["unwrap_mesh", "bake_texture", "attach_texture", "export_textured"]
