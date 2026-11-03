"""M3 泊松参数：面数 / 岛数 / 对激光点的贴合度，三者一起量。

判据不能只有岛数——上一轮的教训是「把文物磨平也能让岛数好看」。
所以每档都量「原始激光点到网格表面的距离」：真贴合才允许岛数下降。
不改产品代码。
"""
import sys
sys.path.insert(0, r"E:\h3d_lablab\src")
import time
import numpy as np
import open3d as o3d
import xatlas

from h3d.mesh.poisson import poisson_reconstruct
from h3d.geo_align import _robust_diag

PTS = r"D:/h3d_relic_test/10_clean/relic_001/relic_001_s01.ply"


def islands(mesh):
    m = o3d.geometry.TriangleMesh(mesh.vertices, mesh.triangles)
    m.compute_vertex_normals()
    v = np.asarray(m.vertices, dtype=np.float32)
    f = np.asarray(m.triangles, dtype=np.uint32)
    co = xatlas.ChartOptions()
    po = xatlas.PackOptions()
    po.padding = 2
    po.resolution = 2048
    at = xatlas.Atlas()
    at.add_mesh(v, f)
    at.generate(co, po)
    return at.chart_count, at.utilization


def fidelity(mesh, pts):
    """激光点到网格表面的单向距离（中位 / p95），按网格对角归一。"""
    surf = np.asarray(mesh.sample_points_uniformly(60000).points)
    from scipy.spatial import cKDTree
    d = cKDTree(surf).query(pts, k=1)[0]
    diag = _robust_diag(np.asarray(mesh.vertices))
    return float(np.median(d)) / diag, float(np.percentile(d, 95)) / diag


def main():
    pcd = o3d.io.read_point_cloud(PTS)
    pts = np.asarray(pcd.points)
    print(f"输入 {len(pts)} 点，稳健对角 {_robust_diag(pts):.3f}\n")
    print(f"{'参数':28s} {'面数':>8s} {'岛数':>6s} {'利用':>7s} "
          f"{'点->面中位':>10s} {'p95':>8s} {'耗时':>6s}")

    for depth, dr in ((6, 0.05), (7, 0.05), (8, 0.05), (9, 0.05), (8, 0.20)):
        t0 = time.time()
        m = poisson_reconstruct(pcd, depth=depth, density_ratio=dr,
                                n_threads=1)
        t_rec = time.time() - t0
        nf = len(m.triangles)
        ni, util = islands(m)
        med, p95 = fidelity(m, pts)
        print(f"depth={depth} density={dr:<8.2f} {nf:8d} {ni:6d} {util:6.1%} "
              f"{med:10.5f} {p95:8.5f} {t_rec:5.0f}s")


if __name__ == "__main__":
    main()
