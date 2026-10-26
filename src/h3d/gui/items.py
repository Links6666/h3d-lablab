"""h3d.gui.items : 从项目根反查「文物编号」（item_id）列表。

背景（2026-10-18 实机）：GUI 的预览 / 贴图 / 导出 / 热力图全部把
``cfg.name``（**项目名**，如 ``yyyyyyy``）当成文物编号去拼路径：

    80_export/<name>/<name>.obj
    50_tex/<name>/*_diffuse.png
    80_export/<name>/...             ← 导出交付包

而管线用的是 **item_id**——``00_raw`` 下的子目录名（如 ``relic_001BMYyyyy``）。
两者不同名时：

  * 预览面板永远显示「未生成可预览的三维模型」（即使 M6 已交付）；
  * 「打开 3D」找不到模型，提示本次未生成；
  * 「导出交付包」返回空 dict → 弹「未找到交付产物（80_export）」，而实际
    交付物好好地躺在 ``80_export/relic_001BMYyyyy/`` 里。

项目名与文物编号同名只是**示例项目的巧合**（``demo_relic``），真实项目几乎
必然不同名。因此所有 GUI 路径都必须先反查 item_id，不能拿项目名顶替。

反查口径（按可信度排序）：
    1. ``output/run_manifest.json`` 的 ``stages`` 键前缀（``<item>/<stage>``）
       —— 这是**本次运行真实处理过**的编号，最权威；
    2. ``00_raw`` 下的子目录名（任务发现的同一口径）；
    3. ``00_raw`` 下平铺文件的去扩展名文件名；
    4. 都查不到时返回空列表，调用方退回项目名（保持历史行为，不抛异常）。
"""

from __future__ import annotations

import json
import os

#: 与 ``h3d.core.media`` 保持一致的点云/照片扩展名（仅用于平铺文件模式识别）
_RAW_EXTS = {".ply", ".pcd", ".xyz", ".pts", ".las", ".laz",
             ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


def _manifest_paths(root_dir: str) -> list[str]:
    return [os.path.join(root_dir, "output", "run_manifest.json"),
            os.path.join(root_dir, "90_meta", "run_manifest.json")]


def items_from_manifest(root_dir: str) -> list[str]:
    """从 run_manifest 的 ``stages`` 键前缀提取文物编号（保序去重）。"""
    for path in _manifest_paths(root_dir):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        stages = data.get("stages") or {}
        out: list[str] = []
        for key in stages:
            if "/" not in key:
                continue
            item = key.split("/", 1)[0]
            if item and item not in out:
                out.append(item)
        if out:
            return out
    return []


def items_from_raw(root_dir: str) -> list[str]:
    """从 ``00_raw`` 目录结构推断文物编号（与任务发现同口径）。"""
    raw = os.path.join(root_dir, "00_raw")
    if not os.path.isdir(raw):
        return []
    try:
        entries = sorted(os.listdir(raw))
    except OSError:
        return []
    dirs = [e for e in entries if os.path.isdir(os.path.join(raw, e))]
    if dirs:
        # 只保留真有文件的子目录（空目录是伪任务）
        keep = []
        for d in dirs:
            full = os.path.join(raw, d)
            try:
                if any(os.path.isfile(os.path.join(full, f))
                       for f in os.listdir(full)):
                    keep.append(d)
            except OSError:
                continue
        return keep
    return [os.path.splitext(e)[0] for e in entries
            if os.path.isfile(os.path.join(raw, e))
            and os.path.splitext(e)[1].lower() in _RAW_EXTS]


def discover_items(root_dir: str) -> list[str]:
    """项目下所有文物编号；找不到返回 ``[]``（不抛异常）。"""
    return items_from_manifest(root_dir) or items_from_raw(root_dir)


def is_delivered(root_dir: str, item_id: str) -> bool:
    """该文物是否已有交付目录（``80_export/<item>``）。"""
    return os.path.isdir(os.path.join(root_dir, "80_export", item_id))


def primary_item(root_dir: str, fallback: str = "") -> str:
    """选一个「最值得展示」的文物编号。

    优先级：已交付 → manifest 中登记 → 00_raw 首个 → ``fallback``（项目名）。
    多文物项目里，用户最想先看到的是**已经出成品的那一件**。
    """
    items = discover_items(root_dir)
    for it in items:
        if is_delivered(root_dir, it):
            return it
    if items:
        return items[0]
    return fallback


__all__ = ["discover_items", "items_from_manifest", "items_from_raw",
           "is_delivered", "primary_item"]
