"""M4 对齐 A/B 对照：跳过 ICP 对齐 vs 启用 ICP 对齐，比较岛内填充率。

1.1.12 真机：icp_align 把网格推离 sparse 点云（中位距离 0.036 -> 1.917，相对
对角线 0.6% -> 31.3%），island_fill_ratio 只有 57.5%。本脚本直接跑两遍
photo_texture，用同一几何/照片，只差 geometry_is_sfm 一个开关。

用法：python diag_m4_align_ab.py <项目根>
"""
from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def main():
    project = sys.argv[1]
    root = os.path.join(project, "阶段成果")
    item = "relic_001"

    from h3d.core import config as _config
    from h3d.io import load_mesh
    from h3d.photo_texture import texture_mesh_from_photos

    cfg = _config.load(os.path.join(project, "config.yaml"))
    cfg.photo_texture.reuse_atlas = False

    mesh = load_mesh(os.path.join(root, "30_dense", item, f"{item}.ply"))
    print(f"网格：{len(mesh.vertices)} 顶点 / {len(mesh.triangles)} 面")

    results = {}
    for tag, is_sfm in (("skip_align", True), ("icp_align", False)):
        m = load_mesh(os.path.join(root, "30_dense", item, f"{item}.ply"))
        out_dir = os.path.join(project, "_diag_ab", tag)
        shutil.rmtree(out_dir, ignore_errors=True)
        os.makedirs(out_dir, exist_ok=True)
        t0 = time.time()
        try:
            outputs, metrics = texture_mesh_from_photos(
                m, root, item, cfg, out_dir, geometry_is_sfm=is_sfm)
            key = ("coverage_ratio", "atlas_utilization", "island_fill_ratio",
                   "normal_visible_fraction", "in_frame_fraction",
                   "back_facing_texels", "occluded_texels",
                   "back_facing_filled", "multiview_color_std",
                   "texture_mean_luma")
            row = {k: metrics.get(k) for k in key}
            row["icp_align"] = metrics.get("icp_align")
            row["elapsed_s"] = round(time.time() - t0, 1)
            results[tag] = row
            print(f"\n[{tag}] " + str(row))
        except Exception as exc:  # noqa: BLE001
            print(f"[{tag}] 失败：{exc}")
            results[tag] = {"error": str(exc)}

    print("\n=== 对照 ===")
    for tag, row in results.items():
        print(f"{tag:12s} coverage={row.get('coverage_ratio')}"
              f" fill={row.get('island_fill_ratio')}"
              f" util={row.get('atlas_utilization')}")


if __name__ == "__main__":
    main()
