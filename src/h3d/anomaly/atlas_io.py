"""h3d.anomaly.atlas_io : 把 M4 图集接进异常检测，并把结果落成可交付文件。

约定（与 paths.py 的布局纪律一致，不自己拼路径）
------------------------------------------------
输入：``<阶段成果>/50_tex/<item>/<item>_L1.0_diffuse.png`` + ``_L1.0_mask.png``
输出：``<阶段成果>/60_qc/<item>/anomaly/`` 下四件

* ``anomaly_heat.png``   逐纹素异常分热力图（越亮越可疑）
* ``anomaly_mask.png``   二值掩膜（255 = 异常），可直接叠到纹理上看
* ``regions.csv``        图斑列表（bbox / 纹素数 / 占比 / 置信度）
* ``metrics.json``       汇总指标 + 参数快照（复现用）

为什么产物要落盘而不是只在内存里返回
------------------------------------
单期异常检测的结论是「这里可疑」，必须能被人复核：把热力图叠到原纹理上、
或把 bbox 拿去和病害照片对齐。只返回数组的话，复核就得自己写脚本——那等于
这类筛查永远停在「跑过一次」。
"""

from __future__ import annotations

import csv
import json
import os

import numpy as np

from . import AnomalyConfig, detect_atlas_anomalies

__all__ = ["find_items", "run_item", "write_outputs"]

_ATLAS_SUFFIX = "_L1.0_diffuse.png"
_MASK_SUFFIX = "_L1.0_mask.png"


def find_items(stage_root: str) -> list[str]:
    """列出 ``50_tex`` 下同时具备图集与掩膜的件号（按名称排序）。"""
    tex_root = os.path.join(stage_root, "50_tex")
    if not os.path.isdir(tex_root):
        return []
    out: list[str] = []
    for name in sorted(os.listdir(tex_root)):
        item_dir = os.path.join(tex_root, name)
        if not os.path.isdir(item_dir):
            continue
        if (os.path.isfile(os.path.join(item_dir, f"{name}{_ATLAS_SUFFIX}"))
                and os.path.isfile(os.path.join(item_dir, f"{name}{_MASK_SUFFIX}"))):
            out.append(name)
    return out


def _read_png(path: str, *, grey: bool = False) -> np.ndarray:
    from PIL import Image

    with Image.open(path) as im:
        return np.asarray(im.convert("L" if grey else "RGB"))


def run_item(stage_root: str, item_id: str, *,
             config: AnomalyConfig | None = None,
             write: bool = True) -> dict:
    """对一件文物跑检测（可选落盘），返回汇总字典。

    输入缺失直接抛 :class:`FileNotFoundError`，尺寸不一致抛 :class:`ValueError`
    —— 这两类都不是「检测失败」，而是「跑到了一半才发现数据不对」，必须报出
    具体是哪一件、哪两个尺寸，否则现场只能猜。
    """
    item_dir = os.path.join(stage_root, "50_tex", item_id)
    atlas_path = os.path.join(item_dir, f"{item_id}{_ATLAS_SUFFIX}")
    mask_path = os.path.join(item_dir, f"{item_id}{_MASK_SUFFIX}")
    if not os.path.isfile(atlas_path):
        raise FileNotFoundError(f"缺少图集纹理：{atlas_path}")
    if not os.path.isfile(mask_path):
        raise FileNotFoundError(f"缺少岛内掩膜：{mask_path}")

    image = _read_png(atlas_path)
    island = _read_png(mask_path, grey=True) > 127
    cfg = config or AnomalyConfig()
    res = detect_atlas_anomalies(image, island, config=cfg)

    summary = {
        "item_id": item_id,
        "atlas": os.path.relpath(atlas_path, stage_root).replace(os.sep, "/"),
        "mask": os.path.relpath(mask_path, stage_root).replace(os.sep, "/"),
        "size": [int(image.shape[1]), int(image.shape[0])],
        "config": {
            "scales": [int(x) for x in cfg.scales], "k": cfg.k,
            "min_scale": cfg.min_scale, "tex_scale": cfg.tex_scale,
            "scale_cap": cfg.scale_cap, "use_texture": cfg.use_texture,
            "min_region_ratio": cfg.min_region_ratio,
            "min_region_texels": cfg.min_region_texels,
        },
        "metrics": {k: (round(v, 6) if isinstance(v, float) else v)
                    for k, v in res.metrics.items()},
        "regions": [r.as_row() for r in res.regions],
    }
    if write:
        out_dir = os.path.join(stage_root, "60_qc", item_id, "anomaly")
        summary["output_dir"] = os.path.relpath(out_dir, stage_root).replace(
            os.sep, "/")
        write_outputs(out_dir, res, summary)
    return summary


def _imwrite(path: str, image: np.ndarray) -> None:
    """写 PNG，**中文路径也能写**。

    ``cv2.imwrite`` 在 Windows 上把路径按 ANSI(GBK) 传给底层，遇到
    ``阶段成果``/``交付成果`` 这类中文目录会静默返回 False——不抛异常，产物
    就这么没了（与 Open3D 读模型的中文路径问题同源）。这里自己编码再写字节，
    断言写成功：宁可报错，不能交出一份「跑完了但没有图」的结果。
    """
    import cv2

    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise OSError(f"PNG 编码失败：{path}")
    with open(path, "wb") as fh:
        fh.write(buf.tobytes())


def write_outputs(out_dir: str, res, summary: dict) -> str:
    """落四件产物，返回输出目录。"""
    import cv2

    os.makedirs(out_dir, exist_ok=True)
    k = float(summary["config"]["k"])
    # 热力图按「阈值的 3 倍」截顶：超过 3k 的差异再亮也没有区分意义，
    # 不截顶会让一处极强异常把其余全部压成黑色（图看着干净，实则漏读）。
    norm = np.clip(res.score / max(3.0 * k, 1e-6), 0.0, 1.0)
    heat = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    heat[~res.island] = 0                       # 岛外不着色：那是空隙，不是结论
    _imwrite(os.path.join(out_dir, "anomaly_heat.png"), heat)
    _imwrite(os.path.join(out_dir, "anomaly_mask.png"),
             res.mask.astype(np.uint8) * 255)

    regions_csv = os.path.join(out_dir, "regions.csv")
    rows = [r.as_row() for r in res.regions]
    with open(regions_csv, "w", encoding="utf-8-sig", newline="") as fh:
        fieldnames = ["region_id", "x0", "y0", "x1", "y1", "texels",
                      "area_ratio", "score_mean", "score_max"]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with open(os.path.join(out_dir, "metrics.json"), "w",
              encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    return out_dir
