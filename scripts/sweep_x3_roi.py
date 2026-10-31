"""X3 参数扫描：同时量「整图误报」与「注入命中」，找平衡点。

判据冲突是这类检测器的常态：调灵敏 → 误报涨；调保守 → 漏检。所以这两个数
必须**一起看**，只看一个就会得出「调到最灵敏」或「调到最保守」的结论。

    python scripts/sweep_x3_roi.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from h3d.anomaly import AnomalyConfig, detect_atlas_anomalies  # noqa: E402
from verify_x3_sensitivity import inject, iou, load, pick_spots  # noqa: E402


def main() -> int:
    rgb, island = load()
    n_isle = int(island.sum())
    print(f"图集 {rgb.shape[1]}x{rgb.shape[0]}  岛内 {n_isle}")
    print(f"{'cap':>4} {'k':>5} | {'误报%':>7} {'处':>3} | "
          f"{'dE80 40':>8} {'dE120 40':>9} {'dE120 80':>9}")

    spots40 = pick_spots(island, 40)
    spots80 = pick_spots(island, 80)
    cache: dict[tuple, tuple] = {}
    for side in (40, 80):
        for delta in (80.0, 120.0):
            for cx, cy in (spots40 if side == 40 else spots80):
                img, gt, _ = inject(rgb, cx, cy, side, delta)
                cache[(side, delta, cx, cy)] = (img, gt & island)

    for cap in (2.0, 3.0, 4.0):
        for k in (3.5, 4.5):
            cfg = AnomalyConfig(k=k, scale_cap=cap)
            base = detect_atlas_anomalies(rgb, island, config=cfg)
            fp_ratio = base.metrics["anomalous_ratio"]
            n_reg = base.metrics["n_regions"]

            def hit_rate(side: int, delta: float) -> str:
                spots = spots40 if side == 40 else spots80
                hit = 0
                for cx, cy in spots:
                    img, gt = cache[(side, delta, cx, cy)]
                    res = detect_atlas_anomalies(img, island, config=cfg)
                    if (res.mask & gt).sum() / max(1, gt.sum()) >= 0.5:
                        hit += 1
                return f"{hit}/{len(spots)}"

            print(f"{cap:>4.1f} {k:>5.1f} | {fp_ratio:>6.2%} {n_reg:>3d} | "
                  f"{hit_rate(40, 80.0):>8s} {hit_rate(40, 120.0):>9s} "
                  f"{hit_rate(80, 120.0):>9s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
