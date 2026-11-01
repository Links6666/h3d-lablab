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
        if fmt in ("glb", "gltf"):
            # GLB 必须 trimesh 写（2026-11-01）。Open3D 0.19 的 glTF 写出走
            # ASSIMP，在**非 ASCII 路径**下会返回 True 却一个字节都不写——
            # 而 paths.py 把项目根定名 `阶段成果`，中文 Windows 用户天生踩中。
            # 后果不只是"没 GLB"：清单里记着不存在的路径 → `_outputs_exist()`
            # 永远 False → **断点续跑在 M4 上完全失效**（实测 3 件 × 3 次运行，
            # M1/M2/M3 都跳过了，只有 M4 每次重做）。
            _write_gltf_trimesh(mesh, path)
        else:
            ok = _o3d().io.write_triangle_mesh(path, mesh)
            if not ok:
                raise ExportError(f"带色模型导出失败: {path}")
        # 写完必须核对磁盘：返回码不是证据，文件在不在才是。
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            log.warning(
                "导出未落盘，已从产物清单剔除：%s —— 常见原因是路径含中文，"
                "Open3D 的 GLB 写入会静默失败（不抛异常也不返回 False）", path)
            continue
        outputs.append(os.path.abspath(path))
    log.info("导出带色模型：%s_%s（%s，%d 文件%s）",
             item_id, lod_tag, "vertex-color" if colored else "geometry",
             len(outputs), "" if colored else " [无纹理标注]")
    return outputs


def _write_gltf_trimesh(mesh, path: str) -> None:
    """用 trimesh 写 glTF/GLB（顶点色直通，中文路径安全）。

    Open3D 的 glTF 写出依赖 ASSIMP，非 ASCII 路径静默失败；trimesh 直接按
    Python 文件对象写，没有这个问题（ADR-0001 里「Open3D 写 GLB 会静默丢纹理，
    必须 trimesh」是同一根因的另一种表现）。
    """
    import numpy as np
    import trimesh

    kw = {}
    if mesh.has_vertex_colors():
        cols = (np.asarray(mesh.vertex_colors) * 255.0).clip(0, 255)
        kw["vertex_colors"] = cols.astype(np.uint8)
    tm = trimesh.Trimesh(vertices=np.asarray(mesh.vertices),
                         faces=np.asarray(mesh.triangles),
                         process=False, **kw)
    tm.export(path)


def _o3d():
    import open3d

    return open3d


__all__ = ["export_colored"]
