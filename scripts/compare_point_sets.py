"""比两份点云的「点集是否相同」——不比文件哈希，比最近邻距离分布。

为什么需要它：``stereo_fusion`` 是多线程写的，点的**顺序**不保证固定；GPU 路径上
坐标也可能有末位差异。直接比 sha256 会得到「不同」但说不清差多少——而重构验证
真正要回答的是「差多少、差在哪种量级」，不是「字节是否相同」。

判据（**统计等价**，不是逐位等价）::

    点数相对差 <= 0.1%  且  双向最近邻中位距离 == 0  且  max <= 2e-3 × 对角线
        → 统计等价（差异在 COLMAP 自身的噪声水平内）
    否则 → 真的不同，看 99.9% 分位定位

为什么不能要求逐位相等：实测**同一份代码、同一份输入跑两次**，``fused.ply`` 的
点数就不一样（130,921 → 130,929），1.87% 的点位置不同、最大偏差达对角线的
1.0e-3。那是 COLMAP ``patch_match_stereo`` / ``stereo_fusion`` 的 GPU 并行
归约造成的，不是调用方的问题。拿「哈希相同」当判据只会得到一个每天都不一样的
红灯——本项目对「判据在正常输入上恒假」的态度与恒真一样：都不算判据。

用法::

    python scripts/compare_point_sets.py A.ply B.ply
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _load(path: str) -> np.ndarray:
    from h3d.io import load_point_cloud

    return np.asarray(load_point_cloud(path).points, dtype=np.float64)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    args = ap.parse_args()

    a = _load(args.a)
    b = _load(args.b)
    diag = float(np.linalg.norm(np.concatenate([a, b]).max(0)
                                - np.concatenate([a, b]).min(0)))
    dn = abs(len(a) - len(b))
    dn_rel = dn / max(1, max(len(a), len(b)))
    print(f"A: n={len(a):,}  B: n={len(b):,}  点数差={dn}（{dn_rel:.3%}）  "
          f"包围盒对角线={diag:.6f}")

    d_ab, _ = cKDTree(b).query(a)
    d_ba, _ = cKDTree(a).query(b)
    both = np.concatenate([d_ab, d_ba])
    stats = {
        "median": float(np.median(both)),
        "p99": float(np.percentile(both, 99)),
        "p99.9": float(np.percentile(both, 99.9)),
        "max": float(both.max()),
        "frac_gt_1e-9": float((both > 1e-9).mean()),
        "frac_gt_1e-6": float((both > 1e-6).mean()),
    }
    for k, v in stats.items():
        print(f"  {k:14s} {v:.6g}")
    rel = stats["max"] / diag if diag else 0.0
    print(f"  max/对角线     {rel:.3g}")
    equivalent = (dn_rel <= 1e-3 and stats["median"] == 0.0 and rel <= 2e-3)
    if equivalent:
        print("结论：统计等价（差异在 COLMAP 自身噪声水平内）")
    elif stats["median"] == 0.0 and rel <= 2e-3:
        print(f"结论：几何等价，但点数差 {dn}（{dn_rel:.3%}）超出 0.1% —— 需确认")
    else:
        print("结论：点集真的不同，需要看几何")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
