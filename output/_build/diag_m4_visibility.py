"""M4 可见性口径诊断：区分「几何夹角可见」与「真的投影进图内」。

背景：1.1.12 真机 M4 报 normal_visible_fraction=0.8704 但 island_fill_ratio 只有
0.5749——两者差 30 个百分点。怀疑 normal_visible_fraction 只判了 (C-X)·n>0.3，
没有判「投影是否落在图像范围内」，属于高估。

用法：python diag_m4_visibility.py <项目根> <item_id> [--align]
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import open3d as o3d


def load_meta(task_root: str, item: str):
    p = os.path.join(task_root, "90_meta", f"{item}_capture_meta.json")
    return json.load(open(p, encoding="utf-8"))


def cam_pose(im, K):
    R = np.array(im["R"], float)
    # capture_meta 的 t 已经是**相机中心世界坐标**（sfm_solve 写入时转过），
    # 不能再按 COLMAP tvec 做 -R^T t —— 转了反而引入 0.4~1.2 单位的偏移。
    C = np.array(im["t"], float)
    return R, C


def main():
    root, item = os.path.join(sys.argv[1], "阶段成果"), sys.argv[2]
    do_align = "--align" in sys.argv

    from h3d.io import load_mesh
    mesh_path = os.path.join(root, "30_dense", item, f"{item}.ply")
    mesh = load_mesh(mesh_path)
    V = np.asarray(mesh.vertices, float)
    print(f"网格：{len(V)} 顶点，{len(mesh.triangles)} 面")

    meta = load_meta(root, item)
    cams = meta["cameras"]
    imgs = meta["images"]
    print(f"相机：{len(cams)} 内参 / {len(imgs)} 视图")

    if do_align:
        from h3d.geo_align import align_points_to_mesh
        from h3d.sfm_solve import read_points3d
        txt = os.path.join(root, "99_runs", "sfm_tmp", item, "sparse", "0_txt")
        pts = read_points3d(os.path.join(txt, "points3D.txt"))
        s, R, T, am = align_points_to_mesh(pts, mesh)
        print(f"ICP 对齐：s={s:.5f} R行0={R[0]} T={T} metrics={am}")
        V = (R.T @ (V - T).T).T / s

    nrm = np.asarray(mesh.vertex_normals, float)
    if len(nrm) != len(V):
        mesh.compute_vertex_normals()
        nrm = np.asarray(mesh.vertex_normals, float)

    sub = np.linspace(0, len(V) - 1, min(20000, len(V))).astype(int)
    P, N = V[sub], nrm[sub]

    best_cos = np.full(len(sub), -1.0)
    in_view = np.zeros(len(sub), bool)
    per_cam_inview = []

    for im in imgs:
        cam = cams[str(im["cam_id"])]
        R, C = cam_pose(im, cam)
        v = C - P
        d = np.maximum(np.linalg.norm(v, axis=1), 1e-12)
        cos = (v / d[:, None] * N).sum(1)
        best_cos = np.maximum(best_cos, cos)

        pc = (P - C) @ R.T
        z = pc[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = cam["fx"] * pc[:, 0] / z + cam["cx"]
            w = cam["fy"] * pc[:, 1] / z + cam["cy"]
        ok = (z < -1e-9) & (u >= 0) & (u < cam["w"]) & (w >= 0) & (w < cam["h"])
        per_cam_inview.append(int(ok.sum()))
        in_view |= ok & (cos > 0.3)

    frac_cos = float((best_cos > 0.3).mean())
    # 与 photo_texture 同口径：正/反取大
    frac_cos_rev = float((-best_cos > 0.3).mean())
    frac_view = float(in_view.mean())
    print(f"\nA 几何夹角可正面看到            : {max(frac_cos, frac_cos_rev)*100:6.2f}%"
          f"  (fwd {frac_cos*100:.2f}% / rev {frac_cos_rev*100:.2f}%)")
    print(f"B 夹角可见 且 投影落在图内       : {frac_view*100:6.2f}%")
    print(f"  差值（视野损失）               : {(max(frac_cos,frac_cos_rev)-frac_view)*100:6.2f} 个百分点")
    pc_arr = np.asarray(per_cam_inview)
    print(f"\n每相机平均覆盖顶点数：{pc_arr.mean():.0f} / {len(sub)}"
          f"（最少 {pc_arr.min()}，最多 {pc_arr.max()}）")
    never = int((pc_arr == 0).sum())
    print(f"任何相机都投不到的顶点：{never} ({never/len(sub)*100:.2f}%)")


if __name__ == "__main__":
    main()
