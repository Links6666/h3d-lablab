"""h3d.temporal : X4 病害时序追踪（两期图斑关联 + 五态判定）。

为什么需要这一层
----------------
X3 只回答「这版图集哪里可疑」。单期数据无法区分「新出现的病害」与「一直就在
的正常起伏」——而阈值那条路已经量到尽头：全局 k 从 3.5 提到 4.5，图斑 7→4 但
ΔE=120 的 80 texel 注入覆盖率从 0.92 掉到 0（D12）；按岛自适应 k 图斑 7→2 但
召回 9/11→4/11（D13）。两者都是零和，因为**误报与真检在同一强度尺度上不可分**。

X4 换的不是阈值，是**证据类型**：同一个物理位置在两期之间到底变没变。噪声不会
两期都变、真病害会——这是 X3 的单张图里根本不存在的信息。

前提：两期必须共用同一套 UV 布局
--------------------------------
B1 的图集继承（基准 mesh/uv/faces + 复测相机走同一采样器、不重新展开）保证了
「同一物理点落在同一纹素」。**这条前提由代码校验**（:func:`track` 比对两期的岛
掩膜，IoU < 0.99 直接抛错），不靠文档提醒——布局一变，「同一纹素」就不成立，
所有跨期结论连带作废。

五态
----
``appeared`` 新增 / ``grown`` 扩大 / ``shrank`` 缩小 / ``stable`` 稳定 /
``disappeared`` 消失。它们直接对应保护处置：新增要干预、扩大要加密监测、缩小
说明干预有效、稳定可继续观察、消失可结案。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: 五态（顺序即报表排列顺序：先说「变严重」，再说「没变」，最后说「变好」）。
STATE_APPEARED = "appeared"
STATE_GROWN = "grown"
STATE_SHRANK = "shrank"
STATE_STABLE = "stable"
STATE_DISAPPEARED = "disappeared"
STATES = (STATE_APPEARED, STATE_GROWN, STATE_STABLE, STATE_SHRANK,
          STATE_DISAPPEARED)

#: 状态 → 中文（报表与 CLI 用，避免各处自己写）。
STATE_LABEL = {
    STATE_APPEARED: "新增",
    STATE_GROWN: "扩大",
    STATE_STABLE: "稳定",
    STATE_SHRANK: "缩小",
    STATE_DISAPPEARED: "消失",
}


@dataclass(frozen=True)
class TemporalConfig:
    """时序追踪参数。默认值由合成两期注入标定（见 ``tests/test_anomaly_temporal.py``）。"""

    #: 两期岛掩膜的 IoU 下限。低于它说明不是同一套 UV 布局，跨期比较无意义。
    layout_min_iou: float = 0.99
    #: 配对的 bbox IoU 下限。
    min_iou: float = 0.10
    #: 配对门限：两图斑质心距离不得超过「平均等效半径 × 该倍数」。
    #: 没有这道门，相隔半个图集的两处病害会被配成一条「稳定」记录。
    max_centroid_ratio: float = 1.5
    #: 面积比 ≥ 它判「扩大」，≤ 1/它判「缩小」。1.3 对应线尺度约 14% 的变化，
    #: 与 UV 采样误差同量级的下限。
    grow_ratio: float = 1.3
    #: 变化是否**可见**的门限（CIE76 ΔE）。低于它说明两期像素几乎没动，
    #: 「变没变」这件事本身不成立——报出来只会是阈值抖动。ΔE≈5 是「一眼能看出」。
    delta_e_min: float = 5.0


@dataclass(frozen=True)
class TemporalChange:
    """一处跨期变化。"""

    state: str
    #: 两期的图斑序号（``AnomalyRegion.region_id``）；缺失的一侧为 ``None``。
    region_a: int | None
    region_b: int | None
    bbox_a: tuple[int, int, int, int] | None
    bbox_b: tuple[int, int, int, int] | None
    texels_a: int
    texels_b: int
    #: 面积比 ``texels_b / texels_a``；A 侧没有图斑时为 ``inf``。
    area_ratio: float
    #: 两处图斑并集内的**中位** ΔE（CIE76）；没有共同区域时为 0。
    delta_e: float

    @property
    def label(self) -> str:
        return STATE_LABEL.get(self.state, self.state)

    def as_row(self) -> dict:
        """报表行（CSV/JSON 用，键名稳定）。"""
        return {
            "state": self.state, "label": self.label,
            "region_a": self.region_a, "region_b": self.region_b,
            "x0_a": (self.bbox_a or (None,) * 4)[0],
            "y0_a": (self.bbox_a or (None,) * 4)[1],
            "x1_a": (self.bbox_a or (None,) * 4)[2],
            "y1_a": (self.bbox_a or (None,) * 4)[3],
            "x0_b": (self.bbox_b or (None,) * 4)[0],
            "y0_b": (self.bbox_b or (None,) * 4)[1],
            "x1_b": (self.bbox_b or (None,) * 4)[2],
            "y1_b": (self.bbox_b or (None,) * 4)[3],
            "texels_a": self.texels_a, "texels_b": self.texels_b,
            "area_ratio": (None if not np.isfinite(self.area_ratio)
                           else round(self.area_ratio, 3)),
            "delta_e": round(self.delta_e, 2),
        }


@dataclass(frozen=True)
class TemporalReport:
    changes: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def delta_e_map(lab_a: np.ndarray, lab_b: np.ndarray) -> np.ndarray:
    """CIE76 色差图。

    **必须换算 L 的尺度**：OpenCV 的 8 位 Lab 里 L 是 0~255（= L\\* 的 2.55 倍），
    直接拿三通道算欧氏距离会把亮度差高估 2.55 倍——那样「ΔE > 5」这条标准门限
    就变成了「ΔE > 2」，语义全丢。a/b 本来就是 ±128 的偏移量，不需要换算。
    """
    a = np.asarray(lab_a, np.float32)
    b = np.asarray(lab_b, np.float32)
    if a.shape != b.shape:
        raise ValueError(f"两期图集尺寸不一致：{a.shape} vs {b.shape}")
    d_l = (a[..., 0] - b[..., 0]) * (100.0 / 255.0)
    d_a = a[..., 1] - b[..., 1]
    d_b = a[..., 2] - b[..., 2]
    return np.sqrt(d_l * d_l + d_a * d_a + d_b * d_b).astype(np.float32)


def _centroid(bbox: tuple[int, int, int, int]) -> tuple[float, float]:
    x0, y0, x1, y1 = bbox
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def _radius(bbox: tuple[int, int, int, int]) -> float:
    x0, y0, x1, y1 = bbox
    return max(1.0, ((x1 - x0 + 1) * (y1 - y0 + 1)) ** 0.5 / 2.0)


def _bbox_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix = min(ax1, bx1) - max(ax0, bx0) + 1
    iy = min(ay1, by1) - max(ay0, by0) + 1
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = float(ix * iy)
    area_a = float((ax1 - ax0 + 1) * (ay1 - ay0 + 1))
    area_b = float((bx1 - bx0 + 1) * (by1 - by0 + 1))
    return inter / max(1e-6, area_a + area_b - inter)


def _match(regions_a: list, regions_b: list,
           cfg: TemporalConfig) -> list[tuple[int, int]]:
    """匈牙利指派。返回 ``[(i, j), ...]``（下标对应两侧 regions 列表）。

    代价用 ``1 - bbox IoU``：区域数量很少（真机 7 处、注入后十几处），
    ``linear_sum_assignment`` 的开销可以忽略。距离门限单独判——两个相隔很远的
    斑块即使面积一样也不能配成「稳定」，否则两个独立病害会被粘成一条记录。
    """
    from scipy.optimize import linear_sum_assignment

    if not regions_a or not regions_b:
        return []
    big = 1e6
    cost = np.full((len(regions_a), len(regions_b)), big, np.float32)
    for i, ra in enumerate(regions_a):
        ca, rad_a = _centroid(ra.bbox), _radius(ra.bbox)
        for j, rb in enumerate(regions_b):
            cb, rad_b = _centroid(rb.bbox), _radius(rb.bbox)
            dist = float(np.hypot(ca[0] - cb[0], ca[1] - cb[1]))
            limit = cfg.max_centroid_ratio * (rad_a + rad_b)
            iou = _bbox_iou(ra.bbox, rb.bbox)
            if iou < cfg.min_iou and dist > limit:
                continue                      # 两道门都过不了 → 不许配对
            cost[i, j] = 1.0 - iou + 0.25 * min(dist / max(limit, 1e-6), 2.0)
    rows, cols = linear_sum_assignment(cost)
    return [(int(i), int(j)) for i, j in zip(rows, cols) if cost[i, j] < big]


def _state(texels_a: int, texels_b: int, cfg: TemporalConfig) -> str:
    if texels_a <= 0:
        return STATE_APPEARED
    if texels_b <= 0:
        return STATE_DISAPPEARED
    ratio = texels_b / float(texels_a)
    if ratio >= cfg.grow_ratio:
        return STATE_GROWN
    if ratio <= 1.0 / cfg.grow_ratio:
        return STATE_SHRANK
    return STATE_STABLE


def _median_delta_e(de: np.ndarray, island: np.ndarray,
                    bbox_a, bbox_b) -> float:
    """两处图斑并集内的中位 ΔE（只在岛内取）。"""
    boxes = [b for b in (bbox_a, bbox_b) if b is not None]
    if not boxes:
        return 0.0
    sel = np.zeros(island.shape, bool)
    for x0, y0, x1, y1 in boxes:
        sel[y0:y1 + 1, x0:x1 + 1] = True
    sel &= island
    if not sel.any():
        return 0.0
    return float(np.median(de[sel]))


def track(image_a: np.ndarray, image_b: np.ndarray, res_a, res_b,
          *, config: TemporalConfig | None = None) -> TemporalReport:
    """比对两期的 X3 结果，输出五态变化清单。

    参数
    ----
    image_a, image_b : (H, W, 3) uint8
        两期的扩散图集（``_L1.0_diffuse.png``），用于算 ΔE。
    res_a, res_b : :class:`h3d.anomaly.AnomalyResult`
        两期的 X3 结果。**必须共用同一套 UV 布局**——由岛掩膜 IoU 校验。
    """
    from h3d.anomaly import _to_lab

    cfg = config or TemporalConfig()
    isle_a = np.asarray(res_a.island, bool)
    isle_b = np.asarray(res_b.island, bool)
    inter = float((isle_a & isle_b).sum())
    union = float((isle_a | isle_b).sum()) or 1.0
    layout_iou = inter / union
    if layout_iou < cfg.layout_min_iou:
        raise ValueError(
            f"两期 UV 布局不一致（岛掩膜 IoU={layout_iou:.3f} < "
            f"{cfg.layout_min_iou}）：跨期比较要求同一物理点落在同一纹素上，"
            f"布局一变所有结论作废。请走图集继承（epochs 复测）而不是重新展开。")

    de = delta_e_map(_to_lab(image_a), _to_lab(image_b))
    regions_a = list(res_a.regions)
    regions_b = list(res_b.regions)
    pairs = _match(regions_a, regions_b, cfg)

    used_a = {i for i, _ in pairs}
    used_b = {j for _, j in pairs}
    changes: list[TemporalChange] = []

    def _add(state: str, ra, rb) -> None:
        texels_a = int(ra.texels) if ra is not None else 0
        texels_b = int(rb.texels) if rb is not None else 0
        delta = _median_delta_e(de, isle_a if ra is not None else isle_b,
                                ra.bbox if ra is not None else None,
                                rb.bbox if rb is not None else None)
        changes.append(TemporalChange(
            state=state,
            region_a=(ra.region_id if ra is not None else None),
            region_b=(rb.region_id if rb is not None else None),
            bbox_a=(ra.bbox if ra is not None else None),
            bbox_b=(rb.bbox if rb is not None else None),
            texels_a=texels_a, texels_b=texels_b,
            area_ratio=(texels_b / float(texels_a) if texels_a else float("inf")),
            delta_e=delta))

    for i, j in pairs:
        ra, rb = regions_a[i], regions_b[j]
        _add(_state(ra.texels, rb.texels, cfg), ra, rb)
    for i, ra in enumerate(regions_a):
        if i not in used_a:
            _add(STATE_DISAPPEARED, ra, None)
    for j, rb in enumerate(regions_b):
        if j not in used_b:
            _add(STATE_APPEARED, None, rb)

    # 显著性门限：**变化**必须可见，否则是阈值抖动而不是病害变化。
    # 稳定态不过这道门——它就是「两期都在」，ΔE 小是正常的（同一处病害没动）。
    kept, dropped = [], 0
    for ch in changes:
        if ch.state == STATE_STABLE or ch.delta_e >= cfg.delta_e_min:
            kept.append(ch)
        else:
            dropped += 1

    order = {s: i for i, s in enumerate(STATES)}
    kept.sort(key=lambda c: (order.get(c.state, 99), -c.texels_b, -c.texels_a))

    counts = {s: 0 for s in STATES}
    for ch in kept:
        counts[ch.state] += 1
    metrics = {
        **counts,
        "n_changes": len(kept),
        "matched": len(pairs),
        "filtered_by_delta_e": dropped,
        "layout_iou": round(layout_iou, 6),
        "delta_e_min": float(cfg.delta_e_min),
        "grow_ratio": float(cfg.grow_ratio),
    }
    return TemporalReport(changes=kept, metrics=metrics)
