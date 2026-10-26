"""h3d.mesh.poisson : 泊松表面重建（M3 稠密重建，教学版第 17 章 17.1）。

设计依据：
    《设计书》§34 —— M3 稠密重建：``poisson_reconstruct(pcd, depth=cfg.depth)``
    + 离群面剔除（TC-303「漂浮面被剔除」），输出水密表面网格；
    《教学版》17.1 —— ``create_from_point_cloud_poisson`` + 退化面 / 重复顶点清理 +
    ``compute_vertex_normals``；depth 8-10 对千万级点云较稳妥。

实现要点：
    1. 输入点云必须带一致法线（无则调用方先估计），否则会翻面 / 破洞。
    2. 泊松输出附带逐顶点 ``densities``（采样密度场），低于分位数的区域多为
       离群碎面 / 噪声空洞，按 ``density_ratio`` 裁剪（对应 TC-303）。
    3. 清理链固定：退化三角面 -> 重复顶点 -> 重复三角面 -> 重算法线。
"""

from __future__ import annotations

from h3d.core import get_logger

log = get_logger("mesh.poisson")


def poisson_reconstruct(pcd, depth: int = 8, density_ratio: float = 0.05,
                        n_threads: int = 1):
    """泊松表面重建。

    参数
    ----
    pcd:           带一致法线的点云（Open3D PointCloud）。
    depth:         八叉树深度（细节与性能的平衡，设计书默认 8）。
    density_ratio: 密度裁剪分位数（0~1；0 表示不裁剪）。
    n_threads:     求解线程数，**默认 1 = 可复现**。

    返回
    ----
    清理后的水密表面网格（TriangleMesh，已重算法线）。

    可复现性（2026-10-24 实测，与 COLMAP 多线程那条同源问题）
    --------------------------------------------------------
    Open3D 的 ``create_from_point_cloud_poisson`` 在多线程下**不是逐位可复现**：
    同一份点云连跑三次，顶点/面数相同（10244 / 20484）但坐标与面表哈希全不同
    （80492b8f / 812b5fa5 / c9602497）。这不是精度噪声，而是并行归约顺序不同
    导致线性方程组解在末位分叉，再被三角化放大成不同的面表。

    后果有两层：① 论文的「同输入同输出」无法成立；② M4 的跨件图集复用靠
    几何指纹判等，网格逐位不同就永远不命中——打包版端到端实测正是卡在这里
    （M1/M2 逐字节一致，M3 之后两件的 30_dense 面数 19830 vs 19828）。

    实测 ``n_threads=1`` 两次运行哈希完全一致（00989fb5 / 515caafe）。因此默认
    单线程，把「可复现」放在「快一点」之前；确需速度可用 ``mesh.poisson_threads``
    显式调大，并在报表里接受不可复现的后果。
    """
    import numpy as np

    mesh, densities = _o3d().geometry.TriangleMesh.create_from_point_cloud_poisson(
        pcd, depth=int(depth), n_threads=int(n_threads))

    # 密度裁剪：剔除低采样密度区域的漂浮面（设计书 TC-303）
    if density_ratio and density_ratio > 0 and len(densities):
        dens = np.asarray(densities)
        threshold = float(np.quantile(dens, density_ratio))
        mesh.remove_vertices_by_mask(dens < threshold)

    clean_mesh(mesh)
    log.info("泊松重建完成：%d 顶点 / %d 三角面（depth=%d, density_ratio=%.2f）",
             len(mesh.vertices), len(mesh.triangles), depth, density_ratio)
    return mesh


def clean_mesh(mesh) -> None:
    """网格清理链（原地修改）：退化三角面 -> 重复顶点 -> 重复三角面 -> 法线。"""
    mesh.remove_degenerate_triangles()
    mesh.remove_duplicated_vertices()
    mesh.remove_duplicated_triangles()
    mesh.compute_vertex_normals()


def _o3d():
    """惰性导入 open3d（隔离重型库，import h3d.mesh 不加载）。"""
    import open3d

    return open3d


__all__ = ["poisson_reconstruct", "clean_mesh"]
