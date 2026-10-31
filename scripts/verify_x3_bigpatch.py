"""大斑块漏检的诊断回路（X3，80 texel）。

一条命令、确定性、秒级。它只做一件事：在**真机图集**的指定位置注入 80 texel
斑块，把每个尺度上的「背景 / 尺度 / z」全部打出来——把「z 到底被谁吃掉了」
从猜测变成数字。

    python scripts/verify_x3_bigpatch.py

判据：patch 中心 z ≥ k=3.5 且覆盖率 ≥ 0.5 才算命中。
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from verify_x3_sensitivity import inject, load, pick_spots  # noqa: E402
from h3d.anomaly import (AnomalyConfig, _block_median, _fill_outside,  # noqa: E402
                         _median_background, _to_lab, detect_atlas_anomalies)

SIDE = 80
DELTA = 120.0


def scale_report(lab, y, x, cfg, island, tag=""):
    """逐尺度打印：背景 / 尺度 q75 / z（三个 Lab 通道）。"""
    for size in cfg.scales:
        s = min(int(size) | 1, 255)
        row = []
        for c in range(3):
            ch = lab[..., c]
            bg = _median_background(ch, s)
            resid = np.abs(ch - bg)
            q75 = _block_median(resid, max(16, s // 4))
            sig = np.maximum(q75 / 1.1503, cfg.min_scale)
            row.append((resid[y, x], sig[y, x], resid[y, x] / sig[y, x]))
        txt = "  ".join(f"ch{c}: r={r:5.1f} σ={sg:5.1f} z={z:5.2f}"
                        for c, (r, sg, z) in enumerate(row))
        print(f"    {tag}W={s:<4d} {txt}")


def island_count(mask, y, x, half):
    from scipy import ndimage

    _, n = ndimage.label(mask[np.ix_(range(max(0, y - half), y + half),
                                     range(max(0, x - half), x + half))])
    return n


def main() -> int:
    rgb, island = load()
    cfg = AnomalyConfig()
    base = detect_atlas_anomalies(rgb, island, config=cfg)
    print(f"图集 {rgb.shape[1]}x{rgb.shape[0]}  基线异常 "
          f"{base.metrics['anomalous_ratio']:.2%} / {base.metrics['n_regions']} 处")

    spots = pick_spots(island, SIDE)
    print(f"注入点：{spots}（{SIDE} texel，ΔE≈{DELTA:.0f}）\n")
    for cx, cy in spots:
        img, gt, dE = inject(rgb, cx, cy, SIDE, DELTA)
        gt = gt & island
        half = SIDE // 2
        lab = _fill_outside(_to_lab(img), island)
        res = detect_atlas_anomalies(img, island, config=cfg)
        cov = (res.mask & gt).sum() / max(1, gt.sum())
        n_isl = island_count(island, cy, cx, SIDE)
        print(f"({cx},{cy}) dE={dE:.0f}  覆盖率={cov:.2f}  "
              f"窗口内岛数={n_isl}  全图 z_max={res.score[cy, cx]:.2f}")
        scale_report(lab, cy, cx, cfg, island, tag="中心 ")
        # 斑块内偏移 20 纹素取一点，看是不是「只有中心低」
        scale_report(lab, cy + 20, cx + 20, cfg, island, tag="偏移 ")
        # 背景（远离斑块）的尺度，作为对照
        by, bx = cy + 220, cx + 220
        if 0 < by < img.shape[0] and 0 < bx < img.shape[1] and island[by, bx]:
            scale_report(lab, by, bx, cfg, island, tag=f"背景@({bx},{by}) ")
        print()

    # 反向对照：同一位置、同样大小，但放在**单个岛内部**（离岛边界远）
    from scipy import ndimage
    core = ndimage.binary_erosion(island, np.ones((SIDE, SIDE), bool))
    ys, xs = np.nonzero(core)
    print(f"单岛内可注入点 {len(ys)} 个（{SIDE}×{SIDE} 完全落在岛内的位置）")
    if len(ys):
        for i in range(0, min(len(ys), 3)):
            cy, cx = int(ys[i * max(1, len(ys) // 3)]), int(xs[i * max(1, len(xs) // 3)])
            img, gt, dE = inject(rgb, cx, cy, SIDE, DELTA)
            gt = gt & island
            res = detect_atlas_anomalies(img, island, config=cfg)
            cov = (res.mask & gt).sum() / max(1, gt.sum())
            print(f"  @({cx},{cy}) dE={dE:.0f} 岛内 2 邻域岛数={island_count(island, cy, cx, SIDE)}"
                  f" 覆盖率={cov:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
