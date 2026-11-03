"""X3 的检出到底落在哪：岛边界 / 未着色边缘 / 岛内部。

判据不是「图斑里有多少纹素靠近边界」（那要看图斑多大），而是**与全图集基线
比富集倍数**：如果补丁是随机的，它的边界占比应该和整体一样。

同时按「岛的大小」分层，看小碎岛是否贡献了不成比例的检出。
"""
import sys
sys.path.insert(0, r"E:\h3d_lablab\src")
import csv
import numpy as np
from PIL import Image
from scipy import ndimage as ndi

D = r"D:/h3d_relic_test/50_tex/relic_001"
REG = r"D:/h3d_relic_test/60_qc/relic_001/anomaly/regions.csv"


def main():
    _mk = np.asarray(Image.open(f"{D}/relic_001_L1.0_mask.png"))
    mask = (_mk[:, :, 0] if _mk.ndim == 3 else _mk) > 0
    H, W = mask.shape
    print(f"图集 {W}x{H}  着色 {mask.mean():.2%}")

    # 到最近**未着色**纹素的距离（着色区内部该值大）
    dist_unpainted = ndi.distance_transform_edt(mask)
    # 岛屿连通域（8 邻接）
    lab, n_island = ndi.label(mask, structure=np.ones((3, 3)))
    sizes = ndi.sum(mask, lab, range(1, n_island + 1))
    print(f"着色连通域 {n_island} 个，中位面积 {np.median(sizes):.0f} 纹素，"
          f"<100 纹素的 {int((sizes < 100).sum())} 个")

    rows = list(csv.DictReader(open(REG, encoding="utf-8-sig")))
    print(f"\n图斑 {len(rows)} 处")

    # ---- 基线：全体着色纹素的边界接近度分布 ----
    base_d = dist_unpainted[mask]
    lab_all = lab[mask]
    size_of = np.zeros(n_island + 1)
    size_of[1:] = sizes
    base_size = size_of[lab_all]
    print(f"\n基线（全体着色纹素）：")
    for k in (1, 2, 3, 5):
        print(f"   距未着色 <= {k}px: {(base_d <= k).mean():6.2%}")
    print(f"   所在岛 <100 纹素: {(base_size < 100).mean():6.2%}")
    print(f"   所在岛 <1000 纹素: {(base_size < 1000).mean():6.2%}")

    # ---- 每处图斑 ----
    print(f"\n{'图斑':4s} {'纹素':>6s} {'<=3px占比':>10s} {'富集':>7s} "
          f"{'所在岛<1000':>11s} {'富集':>7s} {'中位距':>7s}")
    agg = []
    for r in rows:
        x0, y0, x1, y1 = (int(r[k]) for k in ("x0", "y0", "x1", "y1"))
        sl = np.s_[max(0, y0):y1 + 1, max(0, x0):x1 + 1]
        m = mask[sl]
        if not m.any():
            continue
        d = dist_unpainted[sl][m]
        sz = size_of[lab[sl][m]]
        f3 = float((d <= 3).mean())
        fs = float((sz < 1000).mean())
        enrich3 = f3 / max((base_d <= 3).mean(), 1e-9)
        enrichs = fs / max((base_size < 1000).mean(), 1e-9)
        agg.append((f3, fs, sz, d))
        print(f"{r['region_id']:>4s} {int(m.sum()):6d} {f3:10.1%} {enrich3:6.1f}x "
              f"{fs:11.1%} {enrichs:6.1f}x {np.median(d):7.1f}")

    if agg:
        f3 = np.mean([a[0] for a in agg])
        fs = np.mean([a[1] for a in agg])
        print(f"\n合计：<=3px 边界占比 {f3:.1%}（基线 {(base_d <= 3).mean():.1%}，"
              f"富集 {f3 / max((base_d <= 3).mean(), 1e-9):.1f}x）")
        print(f"      小岛(<1000) 占比 {fs:.1%}（基线 {(base_size < 1000).mean():.1%}，"
              f"富集 {fs / max((base_size < 1000).mean(), 1e-9):.1f}x）")


if __name__ == "__main__":
    main()
