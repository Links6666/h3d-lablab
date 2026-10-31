"""h3d.anomaly.summary : 异常筛查产物的**读模型**（不依赖 Qt / CLI）。

界面要显示「这版图集哪里可疑」，但它不该知道 ``metrics.json`` 里哪个键叫什么、
``regions.csv`` 有几列。收进这一层，界面只剩装配；CLI 与报表以后也能复用。

三条状态必须分得开
------------------
``ok`` / ``missing``（没跑过）/ ``corrupt``（跑了但读不出来）。把后两者都说成
「0 处异常」是最坏的写法——用户看到 0 会读成「查过了、很干净」，而其实是没查
或查坏了。所以 :attr:`ItemSummary.status` 与 :attr:`ItemSummary.note` 是主要
输出，数字是附带的。

产物布局（与 :func:`h3d.anomaly.atlas_io.write_outputs` 一致）::

    阶段成果/60_qc/<件>/anomaly/
        metrics.json     汇总（面积 / 图斑数 / σ_L / k / 岛数）
        regions.csv      完整图斑清单
        anomaly_heat.png 热力图（TURBO，按 3k 截顶，岛外黑）
        anomaly_mask.png 二值掩膜
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field

#: ``status`` 取值。界面/CLI 都按这三个分支走，不要新增隐含状态。
STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_CORRUPT = "corrupt"

STATUS_LABEL = {
    STATUS_OK: "已筛查",
    STATUS_MISSING: "未筛查",
    STATUS_CORRUPT: "产物异常",
}


@dataclass(frozen=True)
class RegionRow:
    """一处图斑（清单里的**一行**，不是 manifest 里被截断的前 N 条）。"""

    region_id: int
    bbox: tuple[int, int, int, int]        # (x0, y0, x1, y1)，含端点
    texels: int
    area_ratio: float
    score_mean: float
    score_max: float

    @property
    def label(self) -> str:
        return f"第 {self.region_id} 处"

    @property
    def bbox_text(self) -> str:
        # 不留空格：面板里这一列最宽，带空格会让 2048² 上的四位坐标撑爆列宽、
        # 每行被折成三行（实机截图一眼看到）。紧凑写法仍可读。
        x0, y0, x1, y1 = self.bbox
        return f"({x0},{y0})-({x1},{y1})"


@dataclass(frozen=True)
class ItemSummary:
    item_id: str
    status: str
    note: str = ""
    metrics: dict = field(default_factory=dict)
    regions: list[RegionRow] = field(default_factory=list)
    out_dir: str = ""
    heat_png: str | None = None
    mask_png: str | None = None
    #: 被筛查的那版**原图集**（扩散图）。面板拿它做「原图 | 热力图」对照——
    #: 只有热力图时用户知道坐标，却仍要自己打开发散图去找那一块。
    atlas_png: str | None = None
    #: 被筛查的那版**原图集**（扩散图）。面板拿它做「原图 | 热力图」对照——
    #: 只有热力图时用户知道坐标，却仍要自己打开发散图去找那一块。
    atlas_png: str | None = None
    #: 被筛查的那版**原图集**（扩散图）。面板拿它做「原图 | 热力图」对照——
    #: 只有热力图时用户知道坐标却仍要自己打开发散图去找。
    atlas_png: str | None = None
    size: tuple[int, int] | None = None     # (宽, 高)，与产物里的 size 同序

    @property
    def screened(self) -> bool:
        return self.status == STATUS_OK


def out_dir_of(stage_root: str, item_id: str) -> str:
    return os.path.join(stage_root, "60_qc", item_id, "anomaly")


def list_screened_items(stage_root: str) -> list[str]:
    """列出**有筛查产物**的件号（有目录但没 metrics.json 的不算）。"""
    qc = os.path.join(stage_root, "60_qc")
    if not os.path.isdir(qc):
        return []
    items = []
    for name in sorted(os.listdir(qc)):
        if os.path.isfile(os.path.join(qc, name, "anomaly", "metrics.json")):
            items.append(name)
    return items


def _read_regions(path: str) -> tuple[list[RegionRow], str]:
    """读图斑清单。返回 ``(行, 错误说明)``；错误说明为空表示读好了。"""
    if not os.path.isfile(path):
        return [], "图斑清单不可见（缺 regions.csv）"
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return [], f"图斑清单读不出来（regions.csv：{type(exc).__name__}）"
    regions: list[RegionRow] = []
    for r in rows:
        try:
            regions.append(RegionRow(
                region_id=int(r["region_id"]),
                bbox=(int(r["x0"]), int(r["y0"]),
                      int(r["x1"]), int(r["y1"])),
                texels=int(r["texels"]),
                area_ratio=float(r["area_ratio"]),
                score_mean=float(r["score_mean"]),
                score_max=float(r["score_max"]),
            ))
        except (KeyError, TypeError, ValueError):
            # 单行坏掉不该让整张表消失：跳过并累计，下面统一提示
            continue
    if rows and not regions:
        return [], "图斑清单格式不认识（regions.csv 列名对不上）"
    return regions, ""


def load_summary(stage_root: str, item_id: str) -> ItemSummary:
    """读一件的筛查产物。**任何情况都不抛异常**，靠 ``status`` 表达。"""
    out_dir = out_dir_of(stage_root, item_id)
    metrics_path = os.path.join(out_dir, "metrics.json")
    if not os.path.isfile(metrics_path):
        return ItemSummary(
            item_id=item_id, status=STATUS_MISSING, out_dir=out_dir,
            note=("本件未筛查（没有 60_qc/…/anomaly 产物）——"
                  "这不等于「0 处异常」"))

    try:
        with open(metrics_path, encoding="utf-8") as fh:
            payload = json.load(fh)
        metrics = dict(payload.get("metrics") or {})
    except (OSError, ValueError, TypeError) as exc:
        return ItemSummary(
            item_id=item_id, status=STATUS_CORRUPT, out_dir=out_dir,
            note=f"产物读不出来（metrics.json：{type(exc).__name__}: {exc}）")

    regions, region_err = _read_regions(os.path.join(out_dir, "regions.csv"))

    notes: list[str] = []
    if region_err:
        notes.append(region_err)
    elif regions and metrics:
        declared = metrics.get("n_regions")
        if isinstance(declared, int) and declared != len(regions):
            notes.append(f"产物过期或不一致：汇总说 {declared} 处，"
                         f"清单只有 {len(regions)} 条——请重跑本件筛查")

    atlas_png = None
    rel_atlas = payload.get("atlas")
    if isinstance(rel_atlas, str) and rel_atlas:
        cand = os.path.join(stage_root, rel_atlas.replace("/", os.sep))
        if os.path.isfile(cand):
            atlas_png = cand

    atlas_png = None
    rel_atlas = payload.get("atlas")
    if isinstance(rel_atlas, str) and rel_atlas:
        cand = os.path.join(stage_root, rel_atlas.replace("/", os.sep))
        if os.path.isfile(cand):
            atlas_png = cand

    atlas_png = None
    rel_atlas = payload.get("atlas")
    if isinstance(rel_atlas, str) and rel_atlas:
        cand = os.path.join(stage_root, rel_atlas.replace("/", os.sep))
        if os.path.isfile(cand):
            atlas_png = cand

    heat = os.path.join(out_dir, "anomaly_heat.png")
    mask = os.path.join(out_dir, "anomaly_mask.png")
    heat_png = heat if os.path.isfile(heat) else None
    mask_png = mask if os.path.isfile(mask) else None
    if heat_png is None:
        notes.append("热力图缺失（anomaly_heat.png）——只能看数字")

    size: tuple[int, int] | None = None
    raw_size = payload.get("size")
    if isinstance(raw_size, (list, tuple)) and len(raw_size) == 2:
        size = (int(raw_size[0]), int(raw_size[1]))       # (宽, 高)
    elif heat_png:
        size = _png_size(heat_png)

    return ItemSummary(
        item_id=item_id, status=STATUS_OK, note="；".join(notes),
        metrics=metrics, regions=regions, out_dir=out_dir,
        heat_png=heat_png, mask_png=mask_png, atlas_png=atlas_png, size=size)


def _png_size(path: str) -> tuple[int, int] | None:
    """读 PNG 尺寸（宽, 高）。坏文件返回 ``None``，不抛。"""
    try:
        from PIL import Image

        with Image.open(path) as im:
            return (int(im.width), int(im.height))
    except Exception:  # noqa: BLE001 - 尺寸拿不到就退回按坐标推
        return None


def _fmt_k(metrics: dict) -> str:
    """阈值渲染：自适应关闭时 ``k=3.5``，打开时 ``k=3.5~4``。

    只印下界的话，读者会以为全图按 3.5 判，而实际有一半区域按 4.0 判
    （见 ADR-0003 D13：自适应虽然默认关闭，但开关是留着的）。
    """
    lo = metrics.get("k")
    hi = metrics.get("k_max")
    if lo is None:
        return ""
    if isinstance(hi, (int, float)) and abs(float(hi) - float(lo)) > 1e-6:
        return f"k={float(lo):g}~{float(hi):g}"
    return f"k={float(lo):g}"


def summary_line(s: ItemSummary) -> str:
    """一行摘要（界面顶栏、CLI 首行共用）。"""
    if s.status == STATUS_MISSING:
        return "未筛查（本件还没有异常筛查产物）"
    if s.status == STATUS_CORRUPT:
        return "产物异常（读不出来，见下方说明）"
    m = s.metrics
    parts = [f"异常面积 {float(m.get('anomalous_ratio') or 0) * 100:.2f}%（岛内）"]
    n = m.get("n_regions")
    if isinstance(n, int):
        parts.append(f"{n} 处待复核")
    if m.get("sigma_median") is not None:
        parts.append(f"σ_L≈{float(m['sigma_median']):.1f}")
    k = _fmt_k(m)
    if k:
        parts.append(k)
    if isinstance(m.get("island_count"), int):
        how = "按岛比较" if m.get("per_island", True) else "跨岛比较"
        parts.append(f"图集 {m['island_count']} 个 UV 岛（{how}）")
    return " · ".join(parts)


def note_line(s: ItemSummary) -> str:
    """说明行：产物层面的问题优先，其次才是口径提醒。"""
    if s.note:
        return s.note
    if s.status == STATUS_MISSING:
        return ("跑一次「一键生成」或 h3d anomaly 之后，这里会列出可疑图斑。"
                "未筛查不等于 0 处异常。")
    if s.status == STATUS_CORRUPT:
        return "产物存在但读不出来——请重跑本件筛查。"
    # 纯文本标签（ElidedLabel）：这里**不能**写 Markdown 标记，否则界面上
    # 会原样显示星号（实机截图抓到过）。样式靠控件不靠标记。
    return ("列出的处数是「待人工复核」的量，不是病害数；"
            "点一行可看该处热力图（岛外黑色为空隙，不是结论）。")


def crop_box(bbox: tuple[int, int, int, int],
             image_shape: tuple[int, int], *,
             pad_ratio: float = 1.0, pad_min: int = 24,
             max_frac: float = 0.25) -> tuple[int, int, int, int]:
    """给「点一处跳过去」算裁剪窗口 ``(x0, y0, x1, y1)``（含端点）。

    参数
    ----
    image_shape : ``(高, 宽)``，与 ``numpy.ndarray.shape[:2]`` 同序。

    边距按斑块**外接正方形的长边**给（``pad = max(w, h)``），不是按各自方向给：
    沿裂隙的病变宽 800、高 30，若按高度给边距，裁出来是一条 30 像素高的横条，
    看不见它两端接到了哪里。上限 ``max_frac`` 防止一个小斑块把整张图都裁进来
    （2048² 上 0.25 即 512 纹素）。

    返回的窗口一定非空，且在图像内。
    """
    h_img, w_img = int(image_shape[0]), int(image_shape[1])
    x0, y0, x1, y1 = (int(v) for v in bbox)
    w = max(1, x1 - x0 + 1)
    h = max(1, y1 - y0 + 1)
    pad = max(int(pad_min), int(round(pad_ratio * max(w, h))))
    pad = min(pad, int(min(h_img, w_img) * max_frac))
    cx0 = max(0, min(x0 - pad, w_img - 1))
    cy0 = max(0, min(y0 - pad, h_img - 1))
    cx1 = min(w_img - 1, max(x1 + pad, cx0 + 1))
    cy1 = min(h_img - 1, max(y1 + pad, cy0 + 1))
    return cx0, cy0, cx1, cy1


__all__ = ["ItemSummary", "RegionRow", "STATUS_CORRUPT", "STATUS_LABEL",
           "STATUS_MISSING", "STATUS_OK", "crop_box", "list_screened_items",
           "load_summary", "note_line", "out_dir_of", "summary_line"]
