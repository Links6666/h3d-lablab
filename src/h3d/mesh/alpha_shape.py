"""h3d.mesh.alpha_shape : Alpha-shape 表面重建（《软著方案》2.6 提及的备选方法）。

设计依据：
    《软著方案》2.4 主线模块 —— M3 mesh 支持「泊松重建 / Alpha-shape、孔洞修复」；
    Open3D ``create_from_point_cloud_alpha_shape`` 对噪声敏感度高于泊松，
    适合外包络清晰的文物轮廓，与泊松互为兜底。

实现要点：
    1. ``alpha`` 为球半径（与点云尺度同单位），控制表面贴合程度；
       过小出现孔洞，过大丢失细节。
    2. 无一致法线要求（几何法），但调用方统一先估计法线以保持管线一致性。
    3. 输出沿用统一清理链。
"""

from __future__ import annotations

from h3d.core import get_logger

log = get_logger("mesh.alpha_shape")


def alpha_shape_reconstruct(pcd, alpha: float = 0.05):
    """Alpha-shape 表面重建。

    参数
    ----
    pcd:   点云（Open3D PointCloud）。
    alpha: 球半径（与点云尺度同单位）。

    返回
    ----
    清理后的表面网格（TriangleMesh）。
    """
    mesh = _o3d().geometry.TriangleMesh.create_from_point_cloud_alpha_shape(
        pcd, float(alpha))
    from .poisson import clean_mesh

    clean_mesh(mesh)
    log.info("Alpha-shape 重建完成：%d 顶点 / %d 三角面（alpha=%.4f）",
             len(mesh.vertices), len(mesh.triangles), alpha)
    return mesh


def _o3d():
    import open3d

    return open3d


__all__ = ["alpha_shape_reconstruct"]
