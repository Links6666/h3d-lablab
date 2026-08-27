"""h3d.mesh.postprocess : 网格后处理与度量（教学版第 18 章）。

设计依据：
    《教学版》18.1 —— QEM 二次误差简化 ``simplify_quadric_decimation``；
    《教学版》18.2 —— Hausdorff 距离（重建网格 vs 参考网格的几何误差指标）；
    《教学版》18.3 —— 精度报告 CSV（供 M6 报表层消费）；
    《软著方案》2.4 —— M3 mesh「补洞」（trimesh 可选增强，避开 GPL 的 PyMeshLab）。

实现要点：
    1. :func:`simplify_mesh` 用 QEM 简化到目标面数，在「保形」与「省存储 / 快渲染」
       间取舍（教学版：存档建议 50 万-200 万面）。
    2. :func:`fill_holes` 惰性导入 trimesh（MIT）：已安装则补洞，未安装则降级
       警告并原样返回——符合《软著方案》2.6「单项缺失 -> 留空并注明」。
    3. :func:`hausdorff_distance` 按两向采样 + 最近邻距离取最大（教学版 18.2）。
"""

from __future__ import annotations

from typing import Any

from h3d.core import get_logger

log = get_logger("mesh.postprocess")


def simplify_mesh(mesh, target: int = 200000):
    """QEM 二次误差简化到目标三角面数。

    参数
    ----
    mesh:   TriangleMesh。
    target: 目标三角面数；>= 当前面数时原样返回。
    """
    if target <= 0 or len(mesh.triangles) <= target:
        return mesh
    simplified = mesh.simplify_quadric_decimation(
        target_number_of_triangles=int(target))
    log.info("网格简化：%d -> %d 面（target=%d）",
             len(mesh.triangles), len(simplified.triangles), target)
    return simplified


def fill_holes(mesh, max_hole_size: int = 0):
    """修补网格孔洞（可选依赖 trimesh，MIT）。

    参数
    ----
    mesh:          TriangleMesh。
    max_hole_size: 最大孔洞边数；<=0 表示修补所有孔洞。

    返回
    ----
    修补后的网格；trimesh 未安装时降级警告并原样返回。
    """
    try:
        import trimesh  # noqa: F401  MIT 许可（《软著方案》2.5 白名单）
    except ImportError:  # pragma: no cover - 降级路径
        log.warning("trimesh 未安装，跳过补洞（可选依赖，MIT 许可）")
        return mesh
    import numpy as np

    vertices = np.ascontiguousarray(np.asarray(mesh.vertices), dtype=np.float64)
    triangles = np.asarray(mesh.triangles)
    tri = _trimesh().Trimesh(vertices=vertices, faces=triangles, process=False)
    # trimesh 4.x: fill_holes(max_hole_size=None)；5.x: fill_holes() 无参数（补小洞）
    try:
        tri.fill_holes()
    except TypeError:  # pragma: no cover - 旧版 4.x 兼容
        tri.fill_holes(max_hole_size or None)
    patched = _o3d().geometry.TriangleMesh(
        vertices=_o3d().utility.Vector3dVector(
            np.ascontiguousarray(np.asarray(tri.vertices), dtype=np.float64)),
        triangles=_o3d().utility.Vector3iVector(
            np.ascontiguousarray(np.asarray(tri.faces), dtype=np.int32)))
    patched.compute_vertex_normals()
    log.info("补洞完成：%d 顶点 / %d 三角面", len(patched.vertices), len(patched.triangles))
    return patched


def hausdorff_distance(mesh_a, mesh_b, samples: int = 100000) -> float:
    """两网格间的 Hausdorff 距离（教学版 18.2，几何误差指标）。

    对两网格各均匀采样 ``samples`` 点，计算双向最近邻距离的最大值
    （``max(d_ab, d_ba)``），单位与点云坐标一致（米）。
    """
    a = mesh_a.sample_points_uniformly(int(samples))
    b = mesh_b.sample_points_uniformly(int(samples))
    d_ab = a.compute_point_cloud_distance(b)
    d_ba = b.compute_point_cloud_distance(a)
    return float(max(max(d_ab), max(d_ba)))


def mesh_metrics(mesh, **extra: Any) -> dict[str, Any]:
    """汇总网格基本指标（顶点数 / 三角面数 / 水密性），供 manifest 与报表记录。"""
    metrics = {
        "n_vertices": int(len(mesh.vertices)),
        "n_triangles": int(len(mesh.triangles)),
        "watertight": bool(mesh.is_watertight()),
    }
    metrics.update(extra)
    return metrics


def _o3d():
    import open3d

    return open3d


def _trimesh():
    import trimesh

    return trimesh


__all__ = [
    "simplify_mesh",
    "fill_holes",
    "hausdorff_distance",
    "mesh_metrics",
]
