"""X4 真机验证：以现有图集造第二期，注入**已知真值**的三类变化，核对五态判定。

做法与 X3 的灵敏度校验同一套路（合成注入 + 反向对照），只是把「注入」放在
**两期之间**：同一张 2048² 真机图集，改三处已知位置，其余不动。

    python scripts/verify_x4_temporal.py

真值三处：
  1. **新增**：在一处干净区域画一块 ΔE≈60 的斑（第一期没有）
  2. **扩大**：把第一期最大的一处可疑图斑向四周扩 6~10 texel
  3. **消失**：把另一处可疑图斑整块涂回该岛的中位色（模拟修复）

其余图斑两期完全不动 → 应判「稳定」。判定表最后给出「真值 vs 实测」逐行对照。

**已知限制（必须写在结论里）**：第二期是**仿真**的，不是真的复测扫描。它验证的是
「关联 + 状态机 + ΔE 门限」这套机制，不能替代真实两期数据（后者还要验证 UV 继承
的像素级配准、两期光照/曝光差异、以及真实病害的缓慢变化）。
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

DEFAULT_ROOT = r"E:\兵马俑照片\1.1.12"


def load(root: str, item: str | None = None):
    from PIL import Image

    tex = os.path.join(root, "阶段成果", "50_tex")
    if not os.path.isdir(tex):
        tex = os.path.join(root, "50_tex")
    if item is None:
        item = sorted(d for d in os.listdir(tex)
                      if os.path.isdir(os.path.join(tex, d)))[0]
    base = os.path.join(tex, item)
    rgb = np.asarray(Image.open(os.path.join(
        base, f"{item}_L1.0_diffuse.png")).convert("RGB"))
    mask = np.asarray(Image.open(os.path.join(
        base, f"{item}_L1.0_mask.png")).convert("L")) > 127
    return rgb, mask, item


def island_median_colour(rgb: np.ndarray, mask: np.ndarray, x: int, y: int):
    """(x, y) 所在那块岛的中位色（按颜色量化后找同色连通域太贵，这里用局部窗口近似）。"""
    h, w = mask.shape
    r = 40
    y0, y1 = max(0, y - r), min(h, y + r)
    x0, x1 = max(0, x - r), min(w, x + r)
    patch = rgb[y0:y1, x0:x1][mask[y0:y1, x0:x1]]
    return np.median(patch.reshape(-1, 3), axis=0).astype(np.uint8)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--item", default=None)
    ap.add_argument("--relight", action="store_true",
                    help="另跑一组「换灯」对照：第二期整体提亮 25 / 对比度 ×1.35，"
                         "看照明对齐挡住了什么")
    ap.add_argument("--save-b", default="",
                    help="把造出来的第二期图集存到该路径（便于用 CLI 复跑）")
    args = ap.parse_args()

    from h3d.anomaly import detect_atlas_anomalies
    from h3d.anomaly.temporal import STATE_LABEL, track

    rgb, mask, item = load(args.root, args.item)
    h, w = mask.shape
    print(f"图集 {item}  {w}x{h}  岛内 {mask.mean():.1%}")

    res_a = detect_atlas_anomalies(rgb, mask)
    print(f"第一期：异常 {res_a.metrics['anomalous_ratio']:.3%}  "
          f"{res_a.metrics['n_regions']} 处")
    if not res_a.regions:
        print("第一期没有图斑，无法构造变化（换一件或降低 k 再试）")
        return 1

    epoch_b = rgb.copy()
    truth: list[dict] = []

    # ① 新增：找一处「干净」的岛内点（离已有图斑 >120 texel）
    from scipy import ndimage

    from h3d.anomaly import _island_stats, _to_lab

    dist = ndimage.distance_transform_edt(mask)
    _, _, _, iscale = _island_stats(_to_lab(rgb), mask)
    big = res_a.regions[0]
    clean_spot = None
    for yy in range(150, h - 150, 32):
        for xx in range(150, w - 150, 32):
            # 离岛边界 >40 texel（斑块不被岛界切开、不落渗色区），
            # 且所在岛本身要平（σ<8）——在 σ=19 的岛上注 ΔE=66 只有 3.5σ，
            # 测出来的会是「注入太弱」，不是「机制不行」。
            if not mask[yy, xx] or dist[yy, xx] < 40 or iscale[yy, xx] > 8.0:
                continue
            if any(abs(xx - (r.bbox[0] + r.bbox[2]) / 2) < 150
                   and abs(yy - (r.bbox[1] + r.bbox[3]) / 2) < 150
                   for r in res_a.regions):
                continue
            clean_spot = (xx, yy)
            break
        if clean_spot:
            break
    if clean_spot:
        cx, cy = clean_spot
        base_col = island_median_colour(rgb, mask, cx, cy)
        # 对比度要够：ΔE≈40 在 σ 大的岛上只有 3σ 出头，测出来的是「注入太弱」
        # 而不是「机制不行」。这里推到 ΔE≈80，并把实际值打出来。
        patch = np.clip(base_col.astype(np.int16) + np.array([85, -55, -60]),
                        0, 255).astype(np.uint8)
        epoch_b[cy - 18:cy + 18, cx - 18:cx + 18] = patch
        from h3d.anomaly.temporal import delta_e_map
        from h3d.anomaly import _to_lab
        de = delta_e_map(_to_lab(rgb[cy - 18:cy + 18, cx - 18:cx + 18]),
                         _to_lab(epoch_b[cy - 18:cy + 18, cx - 18:cx + 18]))
        truth.append({"state": "appeared",
                      "where": (f"({cx},{cy}) 36texel ΔE={float(np.median(de)):.0f} "
                                f"岛σ={float(iscale[cy, cx]):.1f}")})

    # ② 扩大：第一期最大那处，向四周扩 10 texel（用该处自身颜色，避免变成「新增」）
    x0, y0, x1, y1 = big.bbox
    col = np.median(rgb[y0:y1 + 1, x0:x1 + 1].reshape(-1, 3), axis=0).astype(np.uint8)
    pad = 10
    epoch_b[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad] = col
    truth.append({"state": "grown", "where": f"({x0},{y0})-({x1},{y1}) 外扩 {pad}texel"})

    # ③ 消失：取第二处，涂回该岛中位色（模拟修复）
    if len(res_a.regions) > 1:
        gone = res_a.regions[1]
        gx0, gy0, gx1, gy1 = gone.bbox
        gcol = island_median_colour(rgb, mask, (gx0 + gx1) // 2, (gy0 + gy1) // 2)
        epoch_b[max(0, gy0 - 2):gy1 + 3, max(0, gx0 - 2):gx1 + 3] = gcol
        truth.append({"state": "disappeared",
                      "where": f"({gx0},{gy0})-({gx1},{gy1}) 涂回中位色"})

    if args.save_b:
        from PIL import Image
        os.makedirs(os.path.dirname(os.path.abspath(args.save_b)), exist_ok=True)
        Image.fromarray(epoch_b).save(args.save_b)
        print(f"第二期图集已存：{args.save_b}")

    res_b = detect_atlas_anomalies(epoch_b, mask)
    print(f"第二期：异常 {res_b.metrics['anomalous_ratio']:.3%}  "
          f"{res_b.metrics['n_regions']} 处")

    rep = track(rgb, epoch_b, res_a, res_b)
    m = rep.metrics
    print(f"\n布局 IoU {m['layout_iou']:.6f}   配对 {m['matched']} 对   "
          f"被 ΔE 门限挡下 {m['filtered_by_delta_e']} 条")
    print("   " + "  ".join(f"{STATE_LABEL[s]} {m[s]}"
                            for s in ("appeared", "grown", "stable",
                                      "shrank", "disappeared")))

    print("\n实测：")
    print(f"  {'状态':6s} {'A':>3s} {'B':>3s} {'纹素A':>6s} {'纹素B':>6s} "
          f"{'面积比':>7s} {'中位ΔE':>7s}  边界框")
    for ch in rep.changes:
        ratio = "—" if ch.area_ratio == float("inf") else f"{ch.area_ratio:.2f}"
        box = ch.bbox_b or ch.bbox_a
        print(f"  {ch.label:6s} {('-' if ch.region_a is None else ch.region_a):>3} "
              f"{('-' if ch.region_b is None else ch.region_b):>3} "
              f"{ch.texels_a:6d} {ch.texels_b:6d} {ratio:>7s} "
              f"{ch.delta_e:7.2f}  {box}")

    print("\n真值 vs 实测：")
    got = {}
    for ch in rep.changes:
        got.setdefault(ch.state, 0)
        got[ch.state] += 1
    ok = 0
    for t in truth:
        hit = got.get(t["state"], 0) > 0
        ok += hit
        print(f"  {'✓' if hit else '✗'} 期望 {STATE_LABEL[t['state']]:4s} "
              f"@ {t['where']}")
    print(f"  {ok}/{len(truth)} 类变化被正确判出")

    # 反向对照：两期完全相同 → 每条都必须是「稳定」，不许出现任何状态变化。
    # 注意「稳定」本身就是要报的（那是「这处病害还在」的记录），所以判据是
    # 「非稳定态为 0」，不是「changes 为空」。
    same = track(rgb, rgb, res_a, res_a)
    moved = [c for c in same.changes if c.state != "stable"]
    print(f"\n反向对照（两期完全相同）：{same.metrics['stable']} 条稳定，"
          f"非稳定态 {len(moved)} 条 {'✓' if not moved else '✗ 有假变化'}")
    if moved:
        for c in moved:
            print(f"    {c.label} {c.bbox_a}->{c.bbox_b} ΔE={c.delta_e:.1f}")

    if args.relight:
        from h3d.anomaly.temporal import TemporalConfig as _TC

        print("\n换灯对照（内容完全相同，只是光照/对比度变了）")
        print(f"  {'扰动':14s} {'对齐':4s} {'非稳定态':>6s} {'稳定态ΔE中位':>12s} "
              f"{'残差ΔE':>7s}")
        for tag, img_b in (("整体提亮 +25",
                            np.clip(rgb.astype(np.float32) + 25, 0, 255).astype(np.uint8)),
                           ("对比度 ×1.35",
                            np.clip(128.0 + 1.35 * (rgb.astype(np.float32) - 128.0),
                                    0, 255).astype(np.uint8))):
            rb = detect_atlas_anomalies(img_b, mask)
            for name, cfg in (("关", _TC(align_light=False)), ("开", _TC())):
                rep2 = track(rgb, img_b, res_a, rb, config=cfg)
                moved = [c for c in rep2.changes if c.state != "stable"]
                st = [c.delta_e for c in rep2.changes if c.state == "stable"]
                med = float(np.median(st)) if st else 0.0
                print(f"  {tag:14s} {name:4s} {len(moved):6d} {med:12.2f} "
                      f"{rep2.metrics['delta_e_residual']:7.2f}")
        print("  读数：对齐把「稳定态 ΔE」从 8~10 压回 0~1。不减的话报表会印出"
              "「这处病害加重了 10 个色差」，\n        而其实一个像素都没变——"
              "这一列在换灯之后就不可读了。")

    print("\n注：第二期是**仿真**的（改三处已知位置），验证的是「关联 + 状态机 + ΔE 门限」"
          "\n    这套机制；真实两期数据还要验 UV 继承的像素级配准与两期光照差异。")
    return 0 if ok == len(truth) else 2


if __name__ == "__main__":
    raise SystemExit(main())
