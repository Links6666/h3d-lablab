"""真机等价性：同一份 sparse + 照片，重构前 / 重构后各跑一次稠密重建，比点集。

为什么不用文件哈希
------------------
``fused.ply`` 由 COLMAP 的 ``stereo_fusion`` 多线程写出，**点的顺序不保证固定**。
直接 ``sha256(fused.ply)`` 会在两次点集完全相同时报不同（假红）。所以按坐标
字典序排序、按 1e-6 取整后再哈希——「点集相同」才是要证明的东西。

用法（两次独立进程，避免同一进程里两套 h3d 打架）::

    python scripts/verify_mvs_contract_e2e.py --src <旧代码 src> --out <ws_old>
    python scripts/verify_mvs_contract_e2e.py --src src           --out <ws_new>
    # 两次输出的 hash / n 必须一致

判定：hash 与 n 都相同 → 等价；不同 → 打印包围盒与点数差，别只看 hash。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

COLMAP = r"E:/colmap/colmap-x64-windows-cuda/bin/colmap"
PHOTO = r"D:/h3d_relic_test/阶段成果/00_photo/relic_001"
SPARSE = r"D:/h3d_relic_test/阶段成果/99_runs/sfm_tmp/sparse/0"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="要导入的 h3d 源码目录")
    ap.add_argument("--out", required=True, help="dense 工作区（会被写）")
    ap.add_argument("--photo", default=PHOTO)
    ap.add_argument("--sparse", default=SPARSE)
    ap.add_argument("--max-size", type=int, default=480)
    ap.add_argument("--geom", action="store_true",
                    help="是否做几何一致性第二遍（默认关，只为省时间）")
    args = ap.parse_args()

    sys.path.insert(0, os.path.abspath(args.src))
    import numpy as np

    from h3d.io import load_point_cloud
    from h3d.sfm_solve import run_dense_stereo

    assert os.path.isfile(os.path.join(args.src, "h3d", "sfm_solve.py")), \
        f"{args.src} 下没有 h3d/sfm_solve.py"
    os.makedirs(args.out, exist_ok=True)
    fused = run_dense_stereo(args.photo, args.sparse, args.out, COLMAP,
                             max_image_size=args.max_size,
                             geom_consistency=args.geom)
    pts = np.asarray(load_point_cloud(fused).points, dtype=np.float64)
    order = np.lexsort((pts[:, 2], pts[:, 1], pts[:, 0]))
    digest = hashlib.sha256(np.round(pts[order], 6).tobytes()).hexdigest()
    print(json.dumps({
        "src": args.src,
        "fused": fused,
        "n_points": int(len(pts)),
        "points_sha256": digest,
        "bbox_diag": float(np.linalg.norm(pts.max(0) - pts.min(0))),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
