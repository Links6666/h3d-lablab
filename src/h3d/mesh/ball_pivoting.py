"""h3d.mesh.ball_pivoting : 球旋转表面重建（教学版第 17 章 17.2）。

设计依据：
    《教学版》17.2 —— ``create_from_point_cloud_ball_pivoting``，多个半径尝试，
    适合法线良好、分布均匀的点云；速度通常快于泊松但对点云质量要求更高。

实现要点：
    1. 多球半径列表：先小半径补细节，再大半径连接大区域（教学版示例 [2,4,6]）。
    2. 半径取值需与点云尺度匹配，本模块不缩放——由调用方按 MPS（平均点间距）
       倍数配置（《软著方案》2.7.2 尺度自适应）。
    3. 输出沿用泊松同款清理链（退化面 / 重复顶点 / 法线），保证全管线网格口径一致。
"""

from __future__ import annotations

from h3d.core import get_logger

log = get_logger("mesh.ball_pivoting")


def ball_pivoting_reconstruct(pcd, radii=None):
    """球旋转表面重建。

    参数
    ----
    pcd:   点云（Open3D PointCloud；需法线，分布均匀）。
    radii: 球半径列表（与点云尺度同单位）；缺省 [2.0, 4.0, 6.0]。

    返回
    ----
    清理后的表面网格（TriangleMesh）。
    """
    radii = list(radii) if radii else [2.0, 4.0, 6.0]
    o3d = _o3d()
    mesh = o3d.geometry.TriangleMesh.create_from_point_cloud_ball_pivoting(
        pcd, o3d.utility.DoubleVector([float(r) for r in radii]))
    from .poisson import clean_mesh

    clean_mesh(mesh)
    log.info("球旋转重建完成：%d 顶点 / %d 三角面（radii=%s）",
             len(mesh.vertices), len(mesh.triangles), radii)
    return mesh


def _o3d():
    import open3d

    return open3d


__all__ = ["ball_pivoting_reconstruct"]
