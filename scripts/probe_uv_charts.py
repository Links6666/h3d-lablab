"""UV 展开参数扫描：岛数 / 利用率 / 形变 三者一起量。

目的：X3 的残余伪影是「岛边界」，而当前 xatlas 用默认参数展开出 2410 个岛
（73k 面）。先量「放宽 chart 生长成本」能换到多少岛数、代价是多大形变，
再决定要不要改。不改产品代码。
"""
import sys
sys.path.insert(0, r"E:\h3d_lablab\src")
import time
import numpy as np
import open3d as o3d
import xatlas

MESH = r"D:/h3d_relic_test/30_dense/relic_001/relic_001.ply"


def tri_area_3d(v, f):
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)


def tri_area_uv(uv, f):
    a, b, c = uv[f[:, 0]], uv[f[:, 1]], uv[f[:, 2]]
    cr = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) \
        - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    return 0.5 * np.abs(cr)


def run(verts, faces, label, co_kw, po_kw=None):
    co = xatlas.ChartOptions()
    for k, val in co_kw.items():
        setattr(co, k, val)
    po = xatlas.PackOptions()
    po.padding = 2
    po.resolution = 2048
    for k, val in (po_kw or {}).items():
        setattr(po, k, val)

    at = xatlas.Atlas()
    at.add_mesh(verts, faces)
    t0 = time.time()
    at.generate(co, po)
    dt = time.time() - t0

    vmap, idx, uvs = at.get_mesh(0)
    vmap = np.asarray(vmap, np.int64)
    idx = np.asarray(idx, np.int64).reshape(-1, 3)
    uvs = np.asarray(uvs, np.float64)

    a3 = tri_area_3d(np.asarray(verts, np.float64)[vmap], idx)
    au = tri_area_uv(uvs, idx)
    ok = (a3 > 1e-12) & (au > 1e-12)
    r = np.sqrt(au[ok] / a3[ok])
    r = r / np.median(r)
    spread = float(np.percentile(r, 95) / max(np.percentile(r, 5), 1e-9))
    bad = float((r > 2.0).mean() + (r < 0.5).mean())

    print(f"{label:42s} 岛 {at.chart_count:5d}  利用 {at.utilization:6.2%}  "
          f"形变p95/p5 {spread:5.2f}  形变>2x {bad:6.1%}  {dt:5.1f}s")
    return at.chart_count, at.utilization, spread, bad


def main():
    m = o3d.io.read_triangle_mesh(MESH)
    verts = np.asarray(m.vertices, dtype=np.float32)
    faces = np.asarray(m.triangles, dtype=np.uint32)
    print(f"网格 {len(verts)} 顶点 {len(faces)} 面\n")

    run(verts, faces, "默认（当前实现）", {})
    # max_cost：chart 生长成本上限，调大 → 允许更大的岛
    for mc in (3.0, 5.0, 10.0):
        run(verts, faces, f"max_cost={mc}", {"max_cost": mc})
    # normal_deviation_weight：调小 → 容忍更大法线偏差 → 岛更大
    for nd in (1.0, 0.5, 0.2):
        run(verts, faces, f"max_cost=10, normal_dev={nd}",
            {"max_cost": 10.0, "normal_deviation_weight": nd})
    # straightness_weight：调小 → 边界更少拐角 → 岛更大
    run(verts, faces, "max_cost=10, straight=1, normal_dev=0.5",
        {"max_cost": 10.0, "straightness_weight": 1.0,
         "normal_deviation_weight": 0.5})
    run(verts, faces, "max_cost=20, straight=1, normal_dev=0.5, seam=1",
        {"max_cost": 20.0, "straightness_weight": 1.0,
         "normal_deviation_weight": 0.5, "normal_seam_weight": 1.0})


if __name__ == "__main__":
    main()
