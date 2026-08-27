"""h3d.texture : 网格化与纹理（M4 带色模型交付，L2 业务层 / S4 阶段）。

设计依据：
    《设计书》§17 / §35 —— M4 网格化与纹理：修复网格、UV 展开并映射纹理，
        输出带纹理模型至 50_tex；
    《软著方案》2.4 —— M4 texture 多格式（ply / obj / glb）+ 多分辨率 LOD，
        无纹理时降级输出几何模型并标注。

对外暴露三个子模块的函数，并提供 :func:`run` 纹理化流水线（配置驱动）。输入为 M3
网格产物与带色参考点云（M2 配准结果），输出<b>带色模型</b>（顶点色纹理 + LOD 多分辨率
+ 多格式）到 50_tex。

纹理路径说明：设计书 §35 的理想流程为 UV 展开 + 纹理图集（uv_method=xatlas）。
本子包以<b>顶点色纹理</b>为核心工程路径（Open3D 原生读写顶点色，零重依赖，且
PLY/OBJ/GLB 全格式支持）；``paint_mode='uv'`` 作为增强预留，未实装时降级为
顶点色并警告（软著方案 2.6「单项缺失 -> 注明」）。UV 图集烘焙列入后续增强。

典型用法::

    from h3d.io import load_mesh, load_point_cloud
    from h3d.core import load
    from h3d.texture import run

    cfg = load("configs/example.yaml")
    mesh = load_mesh("30_dense/rock001.ply")
    ref = load_point_cloud("20_registered/rock001_s01.ply")
    outputs, metrics = run(mesh, ref, cfg.texture, "50_tex/rock001", "rock001")
"""

from __future__ import annotations

import os
from typing import Any

from h3d.core import InputDataError, TextureConfig, get_logger

from .paint import has_colors, paint_vertex_colors
from .lod import generate_lods
from .export import export_colored

log = get_logger("texture")


def run(mesh, ref_pcd, cfg: TextureConfig, out_dir: str, item_id: str):
    """纹理化流水线：顶点色映射 -> LOD 多分辨率 -> 多格式导出。

    流程（设计书 §35.2 工程化）：读网格 -> 纹理路径（paint_mode='uv' 时 xatlas 展开 +
    图集烘焙；否则顶点色映射）-> 按 lod_levels 生成多分辨率 -> 每档导出
    export_formats 全部格式到 50_tex/<item>/。

    参数
    ----
    mesh:    M3 网格产物（TriangleMesh）。
    ref_pcd: 带色参考点云（M2 配准结果；无颜色时降级输出几何模型并标注）。
    cfg:     TextureConfig（paint_mode / export_formats / lod_levels / max_vertices）。
    out_dir: 输出目录（50_tex/<item>）。
    item_id: 文物编号（文件名前缀）。

    返回
    ----
    (outputs, metrics)：导出文件绝对路径列表 + 指标字典
    （n_vertices / colored / uv_baked / paint_mode / formats / n_lods / n_outputs）。
    """
    if mesh is None or len(mesh.vertices) == 0:
        raise InputDataError("纹理化输入网格为空（0 顶点）")

    # 1) 纹理路径选择：uv（xatlas 图集烘焙）或 vertex（顶点色）
    outputs: list[str] = []
    colored = False
    uv_baked = False
    if cfg.paint_mode == "uv":
        uv_baked = _run_uv_path(mesh, ref_pcd, cfg, out_dir, item_id, outputs)
        colored = uv_baked
        if uv_baked:
            log.info("UV 图集纹理完成：%s", item_id)
    if not uv_baked:
        # 顶点色纹理（核心路径 / uv 降级路径）
        if ref_pcd is not None and has_colors(ref_pcd):
            colored = paint_vertex_colors(mesh, ref_pcd)
        elif cfg.paint_mode == "vertex":
            log.info("参考点云无颜色：降级输出几何模型并标注（软著方案 2.4）")

        # 2) LOD 多分辨率 + 多格式导出
        for lod_tag, lod_mesh in generate_lods(mesh, cfg.lod_levels, cfg.max_vertices):
            outputs.extend(export_colored(lod_mesh, out_dir, item_id,
                                          lod_tag, cfg.export_formats))

    metrics: dict[str, Any] = {
        "n_vertices": int(len(mesh.vertices)),
        "n_triangles": int(len(mesh.triangles)),
        "colored": bool(colored),
        "uv_baked": bool(uv_baked),
        "paint_mode": cfg.paint_mode,
        "formats": list(cfg.export_formats),
        "lod_levels": list(cfg.lod_levels),
        "n_lods": len(cfg.lod_levels),
        "n_outputs": len(outputs),
    }
    log.info("纹理化完成：%s（%s，%d LOD x %d 格式 = %d 文件）",
             item_id, "uv-texture" if uv_baked else ("vertex-color" if colored else "geometry"),
             metrics["n_lods"], len(cfg.export_formats), len(outputs))
    return outputs, metrics


def _run_uv_path(mesh, ref_pcd, cfg, out_dir: str, item_id: str,
                 outputs: list[str]) -> bool:
    """UV 图集纹理路径（xatlas 展开 + 图集烘焙 + 带纹理导出）。

    返回是否成功完成 UV 路径（失败时降级顶点色路径，由调用方继续）。
    """
    if ref_pcd is None or not has_colors(ref_pcd):
        log.warning("paint_mode='uv' 需要带色参考点云，降级顶点色路径")
        return False
    try:
        from .uv import attach_texture, bake_texture, export_textured, unwrap_mesh

        # 纹理图集分辨率（TextureConfig 无 texture_res 字段，取 lod 数无关的固定 1024）
        size = 1024
        new_mesh, uv, faces = unwrap_mesh(mesh, cfg.max_vertices or 200_000)
        image = bake_texture(new_mesh, uv, ref_pcd, size=size,
                             samples_per_tri=8)
        attach_texture(new_mesh, uv, faces, image)
        # 导出带纹理模型（obj/glb），PLY 由顶点色回退路径补齐
        uv_outputs = export_textured(new_mesh, out_dir, item_id, "L1.0",
                                     ["obj", "glb"])
        outputs.extend(uv_outputs)
        # 顶点色 PLY（回退格式，保证交付包完整性）
        if "ply" in cfg.export_formats:
            paint_vertex_colors(mesh, ref_pcd)
            outputs.extend(export_colored(mesh, out_dir, item_id, "L1.0", ["ply"]))
        return True
    except Exception as exc:  # xatlas 失败 / 退化网格等 -> 降级
        log.warning("UV 图集路径失败（%s），降级顶点色纹理", exc)
        return False


__all__ = [
    "has_colors",
    "paint_vertex_colors",
    "generate_lods",
    "export_colored",
    "run",
]
