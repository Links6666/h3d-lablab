"""M4 图集复用基准：真机数据 5 件同照片集，复用开/关各跑一遍。

数据源：D:/h3d_relic_test（relic_001：30 张 3.84MP 照片 + 37k 顶点网格 +
capture_meta），复制成 5 件同几何/同位姿/同照片（照片用硬链接，0 额外占用）。

用法：python bench_atlas.py [on|off]
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

SRC = r"D:/h3d_relic_test"
ITEMS = [f"relic_b{i}" for i in range(1, 6)]


def build(bench):
    """幂等建 bench 目录：已存在的件不重复拷/删（沙箱禁止批量删除）。"""
    for it in ITEMS:
        os.makedirs(os.path.join(bench, "30_dense", it), exist_ok=True)
        os.makedirs(os.path.join(bench, "90_meta"), exist_ok=True)
        photo_dir = os.path.join(bench, "00_photo", it)
        os.makedirs(photo_dir, exist_ok=True)
        dst_ply = os.path.join(bench, "30_dense", it, f"{it}.ply")
        if not os.path.exists(dst_ply):
            shutil.copy2(os.path.join(SRC, "30_dense", "relic_001",
                                      "relic_001.ply"), dst_ply)
        dst_meta = os.path.join(bench, "90_meta", f"{it}_capture_meta.json")
        if not os.path.exists(dst_meta):
            shutil.copy2(os.path.join(SRC, "90_meta",
                                      "relic_001_capture_meta.json"), dst_meta)
        for n in os.listdir(os.path.join(SRC, "00_photo", "relic_001")):
            dst = os.path.join(photo_dir, n)
            if not os.path.exists(dst):
                os.link(os.path.join(SRC, "00_photo", "relic_001", n), dst)


def main():
    reuse = (sys.argv[1] if len(sys.argv) > 1 else "on").lower() != "off"
    bench = sys.argv[2] if len(sys.argv) > 2 else r"D:/_h3d_atlas_bench"
    sys.path.insert(0, r"E:/h3d_lablab/src")
    from h3d.core import PipelineConfig
    from h3d.io import load_mesh
    from h3d.photo_texture import texture_mesh_from_photos

    build(bench)
    BENCH = bench
    cfg = PipelineConfig()
    cfg.photo_texture.texture_size = 1024
    cfg.photo_texture.top_k = 3
    cfg.photo_texture.samples_per_tri = 4
    cfg.photo_texture.poisson_blend = True
    cfg.photo_texture.export_formats = ["obj", "glb"]
    cfg.photo_texture.reuse_atlas = reuse

    rows = []
    t_all = time.time()
    digests = {}
    for it in ITEMS:
        mesh = load_mesh(os.path.join(BENCH, "30_dense", it, f"{it}.ply"))
        t0 = time.time()
        outputs, m = texture_mesh_from_photos(
            mesh, BENCH, it, cfg, os.path.join(BENCH, "50_tex", it),
            geometry_is_sfm=True)
        el = time.time() - t0
        rows.append({"item": it, "elapsed_s": round(el, 1),
                     "uv_s": m.get("uv_s"), "texture_s": m.get("texture_s"),
                     "export_s": m.get("export_s"),
                     "atlas_cache": m.get("atlas_cache"),
                     "from": m.get("atlas_reuse", {}).get("texture_from"),
                     "coverage_ratio": m.get("coverage_ratio"),
                     "projected_cameras": m.get("projected_cameras"),
                     "island_fill_ratio": m.get("island_fill_ratio"),
                     "n_outputs": len(outputs)})
        print(rows[-1], flush=True)
        png = os.path.join(BENCH, "50_tex", it, f"{it}_L1.0_diffuse.png")
        if os.path.exists(png):
            import hashlib
            digests[it] = hashlib.sha1(open(png, "rb").read()).hexdigest()
    total = round(time.time() - t_all, 1)
    print("\n=== 汇总 ===")
    print(json.dumps({"reuse": reuse, "total_s": total, "items": rows,
                      "diffuse_sha1": digests}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
