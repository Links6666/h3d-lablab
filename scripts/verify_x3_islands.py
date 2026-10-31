"""按岛归一化的 A/B 校验：同一张真机图集，开/关 ``runner.x3_enabled`` 无关，
这里开关的是 ``AnomalyConfig.per_island``。

跑法（一条命令，约 10 s；加 ``--inject`` 约 2 min）::

    python scripts/verify_x3_islands.py
    python scripts/verify_x3_islands.py --inject --atlas <project>/阶段成果/50_tex/<件>

它回答三个问题，每个都给出可核对的数字：

1. **这张图集是不是拼贴的**（岛数 / 尺寸 / 纹素离岛边界多远）—— 决定「按岛」
   是不是必须的。真机 1.1.12：3690 个岛、中位 69 texel、**没有任何纹素离岛边界
   超过 75 texel**，所以 201 窗口处处跨岛，「局部空间背景」处处不成立。
2. **关掉之后误报涨多少**（异常占比 / 图斑数）—— 这是「跨岛比较」的直接代价。
3. （``--inject``）**注入斑块还找不找得到** —— 防止用「把灵敏度调死」换取干净。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

DEFAULT_ROOT = r"E:\兵马俑照片\1.1.12"


def _load(root: str, item: str | None = None):
    from PIL import Image

    tex = os.path.join(root, "阶段成果", "50_tex") if os.path.isdir(
        os.path.join(root, "阶段成果")) else os.path.join(root, "50_tex")
    if item is None:
        cands = sorted(d for d in os.listdir(tex)
                       if os.path.isdir(os.path.join(tex, d)))
        if not cands:
            raise SystemExit(f"{tex} 下没有件目录")
        item = cands[0]
    base = os.path.join(tex, item)
    a = os.path.join(base, f"{item}_L1.0_diffuse.png")
    m = os.path.join(base, f"{item}_L1.0_mask.png")
    if not (os.path.isfile(a) and os.path.isfile(m)):
        raise SystemExit(f"缺图集：{a}")
    return (np.asarray(Image.open(a).convert("RGB")),
            np.asarray(Image.open(m).convert("L")) > 127, item)


def _island_shape(island) -> None:
    """这张图集是不是拼贴的 —— 决定「按岛」是优化还是必须。"""
    from scipy import ndimage

    labels, n = ndimage.label(island, structure=np.ones((3, 3), bool))
    sizes = np.bincount(labels.ravel())[1:]
    dist = ndimage.distance_transform_edt(island)
    print(f"  岛数 {n}   尺寸 中位 {int(np.median(sizes))} p90 "
          f"{int(np.percentile(sizes, 90))} 最大 {int(sizes.max())} texel")
    print(f"  到岛边界距离：中位 {np.median(dist[island]):.1f}  p90 "
          f"{np.percentile(dist[island], 90):.1f}  最大 {dist[island].max():.1f} texel")
    for w in (31, 81, 201):
        # 窗口半径 < 距离 才能整窗落在同一岛内
        fits = float((dist[island] > w / 2.0).mean())
        print(f"    {w:3d} 窗口能整窗落在同一岛内的纹素：{fits:6.1%}")
    print("  → 三个窗口都大面积跨岛时，「局部空间背景」在图集上不成立")


def _regions(mask, min_texels: int = 24) -> tuple[int, int]:
    from scipy import ndimage

    lm, cnt = ndimage.label(mask, np.ones((3, 3), bool))
    if not cnt:
        return 0, 0
    sz = np.bincount(lm.ravel())[1:]
    return cnt, int((sz >= min_texels).sum())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--item", default=None)
    ap.add_argument("--inject", action="store_true",
                    help="另跑 24/40/80 texel 注入（约 2 min）")
    args = ap.parse_args()

    from h3d.anomaly import AnomalyConfig, detect_atlas_anomalies

    rgb, island, item = _load(args.root, args.item)
    print(f"图集 {item}  {rgb.shape[1]}x{rgb.shape[0]}  岛内 {island.mean():.1%}")
    _island_shape(island)
    print()

    out = {}
    for tag, cfg in (("按岛（默认）", AnomalyConfig()),
                     ("关掉（旧口径）", AnomalyConfig(per_island=False))):
        best = None
        for _ in range(2):
            t = time.perf_counter()
            r = detect_atlas_anomalies(rgb, island, config=cfg)
            dt = time.perf_counter() - t
            best = (r, dt) if best is None or dt < best[1] else best
        r, dt = best
        raw_n, big_n = _regions(r.mask)
        out[tag] = (r, dt)
        print(f"{tag:14s} 异常 {r.metrics['anomalous_ratio']:7.3%}  "
              f"图斑 {r.metrics['n_regions']:3d}（原始连通域 {raw_n:4d}，≥24 texel {big_n:3d}）"
              f"  σ_L={r.metrics['sigma_median']:6.2f}  {dt*1000:5.0f} ms")

    off = out["关掉（旧口径）"][0].metrics
    on = out["按岛（默认）"][0].metrics
    if on["anomalous_ratio"] > 0:
        print(f"\n误报降幅 {off['anomalous_ratio']/on['anomalous_ratio']:.1f}×"
              f"（{off['anomalous_ratio']:.3%} → {on['anomalous_ratio']:.3%}）")

    if args.inject:
        from verify_x3_sensitivity import inject, pick_spots

        print("\n注入（dE=120，命中判据：真值覆盖 ≥0.5）")
        for tag, cfg in (("按岛（默认）", AnomalyConfig()),
                         ("关掉（旧口径）", AnomalyConfig(per_island=False))):
            hits = tot = 0
            cells = []
            for side in (24, 40, 80):
                for cx, cy in pick_spots(island, side):
                    img2, gt, _ = inject(rgb, cx, cy, side, 120.0)
                    gt = gt & island
                    r = detect_atlas_anomalies(img2, island, config=cfg)
                    cov = float((r.mask & gt).sum()) / max(1, int(gt.sum()))
                    tot += 1
                    hits += cov >= 0.5
                    cells.append(f"{side}:{cov:.2f}")
            print(f"  {tag:14s} {hits}/{tot}   [{' '.join(cells)}]")

    print("\n口径提醒：异常面积是「比本岛常见起伏更明显」的纹素占比，不是病害面积；"
          "\n          岛内病变面积超过该岛一半时，归一化会把病变当成本底而看不见。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
