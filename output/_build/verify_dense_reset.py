"""实机验证：全量重建是否同时清掉残留 database.db / sparse / dense。

用 30 张兵马俑照片的 photos_small（上次成功那次的输入）跑一遍 S1 sparse，
并在跑之前手工制造三种脏状态：
  1. database.db（半写入的残留库）
  2. sparse/0/images.bin（旧模型）
  3. dense/stereo/depth_maps/*.photometric.bin（旧位姿深度图）

通过标准：
  - dense 目录被清空（最近发现的静默错误源头）
  - photos_small 保留（预处理成果不得被误删）
  - sparse 重建后 30/30 全注册
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, r"E:\h3d_lablab\src")

from h3d.sfm_solve import (  # noqa: E402
    parse_cameras, parse_images, run_colmap_sparse,
)

PHOTOS = r"E:\兵马俑照片\99_runs\sfm_tmp\relic_001BMYyyyy\photos_small"
WS = r"E:\兵马俑照片\99_runs\verify_reset"
COLMAP = r"E:/colmap/colmap-x64-windows-cuda/bin/colmap.exe"


def _touch(path: str, data: bytes = b"dirty") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def main() -> int:
    n_photos = len([f for f in os.listdir(PHOTOS)
                    if f.lower().endswith((".jpg", ".jpeg", ".png"))])
    print(f"[photos] {n_photos} 张 -> {PHOTOS}", flush=True)
    assert n_photos == 30, f"期望 30 张，实际 {n_photos}"

    # ---- 制造脏状态 -------------------------------------------------------
    _touch(os.path.join(WS, "database.db"))
    _touch(os.path.join(WS, "database.db-wal"))
    _touch(os.path.join(WS, "sparse", "0", "images.bin"))
    for i in range(5):
        _touch(os.path.join(WS, "dense", "stereo", "depth_maps",
                            f"old{i}.JPG.photometric.bin"))
        _touch(os.path.join(WS, "dense", "stereo", "depth_maps",
                            f"old{i}.JPG.geometric.bin"))
    _touch(os.path.join(WS, "dense", "images", "old0.JPG"))
    print("[dirty] db + sparse/0 + dense/stereo(5 photometric/5 geometric) 已就位",
          flush=True)

    t0 = time.time()
    txt = run_colmap_sparse(PHOTOS, WS, COLMAP, matcher="exhaustive",
                            max_features=8192, peak_threshold=0.002,
                            deterministic=True)
    dt = time.time() - t0

    # ---- 断言 -------------------------------------------------------------
    dense = os.path.join(WS, "dense")
    dense_left = sorted(os.listdir(dense)) if os.path.isdir(dense) else []
    photos_still = os.path.isdir(PHOTOS) and len(os.listdir(PHOTOS)) == 30
    imgs = parse_images(os.path.join(txt, "images.txt"))
    cams = parse_cameras(os.path.join(txt, "cameras.txt"))

    print(f"\n[elapsed] {dt:.1f}s ({dt/60:.1f} min)", flush=True)
    print(f"[dense ] 残留内容 = {dense_left or '空'}", flush=True)
    print(f"[photos] photos_small 保留 = {photos_still}", flush=True)
    print(f"[sparse] registered = {len(imgs)}/{n_photos}", flush=True)
    print(f"[camera] 相机数 = {len(cams)} -> {sorted(cams)}", flush=True)

    ok = (not dense_left) and photos_still and len(imgs) == 30 and len(cams) == 1
    print(f"\nRESULT = {'PASS' if ok else 'FAIL'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
