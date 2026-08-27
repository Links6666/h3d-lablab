"""h3d.texture.lod : 多分辨率 LOD 生成（M4 带色模型交付的尺度适配）。

设计依据：
    《软著方案》2.4 —— M4 texture 配置 ``lod_levels: [1.0, 0.3, 0.1]``；
    《教学版》18.1 —— QEM 二次误差简化在「保形」与「省存储 / 快渲染」间取舍。

实现要点：
    1. level=1.0 返回原始网格（最高精度，存档用）；level<1.0 按比例
       ``simplify_quadric_decimation`` 简化（保留顶点色）。
    2. ``max_vertices`` 为全局顶点上限：任意 LOD 若简化后仍超限，继续按比例压到上限
       （工程约束，避免超大网格导出失败）。
"""

from __future__ import annotations

from typing import Iterator

from h3d.core import get_logger

log = get_logger("texture.lod")


def generate_lods(mesh, levels, max_vertices: int = 0):
    """按 LOD 比例生成多分辨率网格列表（与 levels 一一对应，返回可迭代）。

    参数
    ----
    mesh:         原始网格（建议已带顶点色）。
    levels:       LOD 比例列表（如 [1.0, 0.3, 0.1]，升序或降序均可）。
    max_vertices: 顶点数上限；>0 时各 LOD 简化后若仍超限则继续压到上限。

    返回
    ----
    (lod_tag, mesh) 的可迭代对象，lod_tag 如 "L1.0" / "L0.3"。
    """
    n_faces = len(mesh.triangles)
    n_verts = len(mesh.vertices)
    for level in sorted(set(levels), reverse=True):
        tag = f"L{float(level):g}"
        if level >= 1.0 or n_faces == 0:
            yield tag, mesh
            continue
        target = max(int(n_faces * float(level)), 1)
        simplified = mesh.simplify_quadric_decimation(
            target_number_of_triangles=target)
        if max_vertices > 0 and len(simplified.vertices) > max_vertices:
            ratio = max_vertices / len(simplified.vertices)
            simplified = simplified.simplify_quadric_decimation(
                target_number_of_triangles=int(len(simplified.triangles) * ratio))
        log.info("LOD %s：%d -> %d 面（%d -> %d 顶点）",
                 tag, n_faces, len(simplified.triangles),
                 n_verts, len(simplified.vertices))
        yield tag, simplified


__all__ = ["generate_lods"]
