"""h3d.texture.export : 带色模型多格式导出（M4 带色模型交付）。

设计依据：
    《软著方案》2.4 —— M4 texture 输出 ``export_formats: [ply, obj, glb]``，
        多格式适配下游（展示 / 打印 / 存档 / 轻量化 Web）；
    《设计书》§35 —— 输出带纹理模型至 50_tex。

实现要点：
    1. PLY / OBJ / GLB 三格式均支持顶点色：OBJ 写 ``v x y z r g b`` 扩展行，
       GLB 内嵌顶点色缓冲，PLY 写 vertex red/green/blue 属性。
    2. 命名约定：``<item>_<LOD>.ply``（如 rock001_L1.0.ply），同目录平铺便于工具链
       按通配符批量消费。
    3. 网格无顶点色时也允许导出（几何模型，软著方案 2.4「无纹理 -> 输出几何模型」），
       由调用方决定是否标注。
"""

from __future__ import annotations

import os
from typing import Optional

from h3d.core import ExportError, get_logger

log = get_logger("texture.export")


def export_colored(
    mesh,
    out_dir: str,
    item_id: str,
    lod_tag: str = "L1.0",
    formats: Optional[list[str]] = None,
) -> list[str]:
    """把（带色）网格导出为多格式文件。

    参数
    ----
    mesh:      TriangleMesh（可带顶点色）。
    out_dir:   输出目录（自动创建，通常为 50_tex/<item>/）。
    item_id:   文物编号（文件名前缀）。
    lod_tag:   LOD 标签（如 L1.0 / L0.3）。
    formats:   导出格式列表（默认 ["ply", "obj", "glb"]）。

    返回
    ----
    写出文件绝对路径列表。
    """
    formats = formats or ["ply", "obj", "glb"]
    os.makedirs(out_dir, exist_ok=True)
    colored = bool(mesh.has_vertex_colors())
    outputs: list[str] = []
    for fmt in formats:
        fmt = fmt.lstrip(".").lower()
        if fmt not in ("ply", "obj", "glb", "gltf"):
            log.warning("跳过不支持的导出格式 %r（支持 ply/obj/glb/gltf）", fmt)
            continue
        path = os.path.join(out_dir, f"{item_id}_{lod_tag}.{fmt}")
        ok = _o3d().io.write_triangle_mesh(path, mesh)
        if not ok:
            raise ExportError(f"带色模型导出失败: {path}")
        outputs.append(os.path.abspath(path))
    log.info("导出带色模型：%s_%s（%s，%d 文件%s）",
             item_id, lod_tag, "vertex-color" if colored else "geometry",
             len(outputs), "" if colored else " [无纹理标注]")
    return outputs


def _o3d():
    import open3d

    return open3d


__all__ = ["export_colored"]
