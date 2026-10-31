"""X3 真机灵敏度校验：在真实图集上注入对比度受控的斑块，量命中率与增量 IoU。

用法
----
    python output/_build/diag_x3_sensitivity.py                # 默认参数
    python output/_build/diag_x3_sensitivity.py --k 4.0 --window 9
    python output/_build/diag_x3_sensitivity.py --tw 61          # 纹素级基线窗口

判据（用户在 AskUserQuestion 里拍定）：最小可检 ≤ 2% 边长（2048 → 41 texel）、
IoU ≥ 0.7。这个脚只负责把数字打出来，不做断言。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from h3d.anomaly import AnomalyConfig, detect_atlas_anomalies  # noqa: E402

ATLAS = r"E:\兵马俑照片\1.1.12\阶段成果\50_tex\relic_001\relic_001_L1.0_diffuse.png"
MASK = r"E:\兵马俑照片\1.1.12\阶段成果\50_tex\relic_001\relic_001_L1.0_mask.png"


def load():
    """用 PIL 读（cv2.imread 在中文路径下静默返回 None，已在 atlas_io 里踩过）。"""
    from PIL import Image

    if not (os.path.isfile(ATLAS) and os.path.isfile(MASK)):
        raise SystemExit(f"读不到图集：{ATLAS}")
    with Image.open(ATLAS) as im:
        img = np.asarray(im.convert("RGB"))
    with Image.open(MASK) as im:
        msk = np.asarray(im.convert("L"))
    return img, msk > 127


def inject(rgb: np.ndarray, cx: int, cy: int, side: int,
           delta: float = 80.0) -> tuple[np.ndarray, np.ndarray, float]:
    """在 (cx, cy) 放一个 side×side 的斑块，**对比度受控**。

    为什么不用 ``255 - pixel``：反色的对比度取决于原内容离中间调有多远。中间调
    区域（RGB≈130）反色后 ΔE 只有 14，斑块本身几乎不可见，测出来的是「人造一个
    不可见的异常然后怪检测器漏检」——真机第一次跑就踩了这个坑（3 处失败点全部
    落在中间调，ΔE 14/18/44，而成功点 ΔE 都在 125 以上）。
    这里把 L 推离中间调 ``delta``、a/b 取反色，逐点对比度基本恒定，返回实际
    达到的 ΔE 中位数，结论才可核对。
    """
    import cv2

    out = rgb.copy()
    h, w = rgb.shape[:2]
    half = side // 2
    y0, y1 = max(0, cy - half), min(h, cy - half + side)
    x0, x1 = max(0, cx - half), min(w, cx - half + side)
    patch = out[y0:y1, x0:x1]
    lab = cv2.cvtColor(patch, cv2.COLOR_RGB2LAB).astype(np.float32)
    tgt = lab.copy()
    tgt[..., 0] = np.where(lab[..., 0] > 128, lab[..., 0] - delta,
                           lab[..., 0] + delta)
    tgt[..., 1] = 255.0 - lab[..., 1]
    tgt[..., 2] = 255.0 - lab[..., 2]
    tgt = np.clip(tgt, 0, 255).astype(np.uint8)
    new_rgb = cv2.cvtColor(tgt, cv2.COLOR_LAB2RGB)
    out[y0:y1, x0:x1] = new_rgb
    back = cv2.cvtColor(new_rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    dE = float(np.median(np.sqrt(((back - lab) ** 2).sum(axis=-1))))
    gt = np.zeros((h, w), bool)
    gt[y0:y1, x0:x1] = True
    return out, gt, dE


def iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter) / float(union) if union else 0.0


def pick_spots(island: np.ndarray, side: int, want: int = 4) -> list[tuple[int, int]]:
    """挑**完整落在岛内**的注入点，且在 cand[i,x,y] 三通道都取中间调。

    为什么必须整块在岛内：掩膜被 ``mask &= island`` 限定，若真值里混进岛外纹素，
    覆盖率的分子永远追不上分母，量出来的是「岛外空隙有多少」而不是灵敏度。
    早期版本就吃过这个亏：4/12 的命中率里有一半是注入点落在 UV 空隙上。
    """
    from scipy import ndimage

    h, w = island.shape
    half = side // 2
    # 腐蚀掉边界：保证 side×side 的方块整体在岛内
    ok = ndimage.binary_erosion(island, np.ones((side, side), bool))
    ys, xs = np.nonzero(ok)
    if len(ys) == 0:
        return []
    # 均匀撒点：按 4×4 网格分箱，每箱取最靠近箱中心的一个候选
    picks: list[tuple[int, int]] = []
    for gy in range(2):
        for gx in range(2):
            y0, y1 = gy * h // 2, (gy + 1) * h // 2
            x0, x1 = gx * w // 2, (gx + 1) * w // 2
            sel = (ys >= y0 + half) & (ys < y1 - half) & (xs >= x0 + half) & (xs < x1 - half)
            if not sel.any():
                continue
            cyy, cxx = (y0 + y1) // 2, (x0 + x1) // 2
            d = (ys[sel] - cyy) ** 2 + (xs[sel] - cxx) ** 2
            i = int(np.argmin(d))
            picks.append((int(xs[sel][i]), int(ys[sel][i])))
            if len(picks) >= want:
                return picks
    return picks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=float, default=3.5)
    ap.add_argument("--scales", default="31,81,201", help="背景窗口，逗号分隔")
    ap.add_argument("--min-scale", type=float, default=2.0)
    ap.add_argument("--tex-scale", type=float, default=0.05)
    ap.add_argument("--sides", default="24,40,80")
    ap.add_argument("--deltas", default="40,80,120,200",
                    help="注入对比度（Lab 单位）列表")
    ap.add_argument("--no-texture", action="store_true")
    args = ap.parse_args()

    rgb, island = load()
    h, w = rgb.shape[:2]
    scales = tuple(int(s) for s in args.scales.split(","))
    print(f"图集 {w}x{h}  岛内 {island.sum() / island.size:.1%}  "
          f"k={args.k} scales={scales}")

    cfg = AnomalyConfig(scales=scales, k=args.k, min_scale=args.min_scale,
                        tex_scale=args.tex_scale,
                        use_texture=not args.no_texture)

    t0 = time.perf_counter()
    base = detect_atlas_anomalies(rgb, island, config=cfg)
    dt = time.perf_counter() - t0
    print(f"未注入：异常 {base.metrics['anomalous_ratio']:.3%}  "
          f"图斑 {base.metrics['n_regions']}  max={base.metrics['score_max']:.2f} "
          f"p99={base.metrics['score_p99']:.2f}  ({dt:.2f}s)")
    for r in base.regions[:5]:
        print(f"   {r.as_row()}")

    sides = [int(s) for s in args.sides.split(",")]
    deltas = [float(d) for d in args.deltas.split(",")]
    print("\n对比度扫描（每格 = 命中数/总数，命中判据：斑块真值覆盖率 ≥ 0.5）")
    head = "  边长 " + "".join(f"  dE={d:>5.0f}" for d in deltas)
    print(head)
    for side in sides:
        spots = pick_spots(island, side)
        if not spots:
            print(f"  {side:4d} 找不到整块落在岛内的注入点")
            continue
        cells = []
        for delta in deltas:
            hit = 0
            for cx, cy in spots:
                img2, gt, _ = inject(rgb, cx, cy, side, delta)
                gt = gt & island
                res = detect_atlas_anomalies(img2, island, config=cfg)
                if (res.mask & gt).sum() / max(1, gt.sum()) >= 0.5:
                    hit += 1
            cells.append(f"{hit:>4d}/{len(spots)}")
        print(f"  {side:4d} " + "".join(f"{c:>9s}" for c in cells))

    print("\n逐点明细（dE = 120）")
    sides_hit = total = 0
    for side in sides:
        for cx, cy in pick_spots(island, side):
            img2, gt, dE = inject(rgb, cx, cy, side, 120.0)
            gt = gt & island
            res = detect_atlas_anomalies(img2, island, config=cfg)
            found = (res.mask & gt).sum() / max(1, gt.sum())
            r = 4 * side
            win = np.zeros_like(gt)
            win[max(0, cy - r):cy + r, max(0, cx - r):cx + r] = True
            val = iou(res.mask & win, gt)
            # 增量 IoU：只算**注入后新增**的掩膜（扣掉未注入时就有的报警）。
            # 局部 IoU 会被窗口里既有的基线报警拖低——问「边界精度」时那是
            # 别人家的账，不该算在这一处头上。
            delta = res.mask & ~base.mask
            val_d = iou(delta & win, gt)
            total += 1
            sides_hit += bool(found >= 0.5)
            print(f"   边长 {side:3d} @({cx},{cy})  dE={dE:5.1f}  "
                  f"覆盖 {found:.2f}  局部 IoU={val:.2f}  "
                  f"增量 IoU={val_d:.2f}  "
                  f"{'OK' if val_d >= 0.7 else ''}")
    print(f"  dE=120 命中（覆盖≥0.5）{sides_hit}/{total}")
    print(f"\n基线（未注入）占岛内 {base.metrics['anomalous_ratio']:.3%}，"
          f"{base.metrics['n_regions']} 处 —— 整图误报率，与局部 IoU 是两件事")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
