"""h3d.gui.export : 交付包一键导出（zip）。

打包 80_export/<item> + 质检报告 + 论文表格 + manifest 摘要。
"""

from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime


def export_delivery_package(root_dir: str, item_id, out_path: str) -> dict:
    """导出交付包 zip：模型/报告/表格/manifest 摘要。

    ``item_id`` 支持单件（str）与多件（list[str]）：
      * 单件 → ``交付模型/<file>``（保持历史布局，老调用方无感）；
      * 多件 → ``交付模型/<item>/<file>``（同名 ``*.obj`` 不会互相覆盖）。

    背景（2026-10-18 实机）：GUI 曾把**项目名**当文物编号传进来，而产物在
    ``80_export/<item>/`` 下 → 一次成功的运行也会弹「未找到交付产物」。
    GUI 侧现在传的是反查出来的真实编号（可能多件）。

    返回 {"out_path", "n_files", "size_kb", "items"}；无交付物时返回空 dict。
    """
    items = [item_id] if isinstance(item_id, str) else list(item_id)
    items = [i for i in items if i]
    nests = len(items) > 1

    present = [i for i in items
               if os.path.isdir(os.path.join(root_dir, "80_export", i))]
    report_dir = os.path.join(root_dir, "report")
    meta_dir = os.path.join(root_dir, "90_meta")
    # 没有一件已交付就返回空：只打包报表会误导用户「模型已导出」。
    if not present:
        return {}

    n_files = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in present:
            export_dir = os.path.join(root_dir, "80_export", item)
            for f in sorted(os.listdir(export_dir)):
                fp = os.path.join(export_dir, f)
                if not os.path.isfile(fp):
                    continue
                arc = (os.path.join("交付模型", item, f) if nests
                       else os.path.join("交付模型", f))
                zf.write(fp, arc)
                n_files += 1
        for f in ("accuracy.csv", "thesis_experiment_table.csv",
                  "delivery_manifest.json"):
            for base, sub in ((report_dir, "report"), (meta_dir, "90_meta")):
                fp = os.path.join(base, f)
                if os.path.exists(fp):
                    zf.write(fp, os.path.join(sub, f))
                    n_files += 1
        for item in (present or items[:1]):
            summary = _manifest_summary(root_dir, item)
            if not summary:
                continue
            name = (f"manifest_summary_{item}.json" if nests
                    else "manifest_summary.json")
            zf.writestr(name,
                        json.dumps(summary, ensure_ascii=False, indent=2))
    if n_files == 0:
        if os.path.exists(out_path):
            os.remove(out_path)
        return {}
    return {"out_path": out_path, "n_files": n_files,
            "size_kb": round(os.path.getsize(out_path) / 1024, 1),
            "items": present}


def _manifest_summary(root_dir: str, item_id: str) -> dict:
    """从 run_manifest.json 提取指标摘要（供交付包内嵌）。"""
    for cand in (os.path.join(root_dir, "90_meta", "run_manifest.json"),
                 os.path.join(root_dir, "output", "run_manifest.json")):
        if not os.path.exists(cand):
            continue
        try:
            m = json.load(open(cand, encoding="utf-8"))
            stages = m.get("stages", {})
            item = {}
            for sid in ("S1", "M1", "M2", "M3", "M4", "M5", "M6", "X1", "X2"):
                s = stages.get(f"{item_id}/{sid}")
                if s:
                    item[sid] = {"status": s.get("status"),
                                 **{k: s[k] for k in
                                    ("coverage", "rmse_mm",
                                     "texture_sharpness", "texture_seam",
                                     "n_registered", "mean_de",
                                     "max_change_mm")
                                    if k in s}}
            return {"item_id": item_id, "exported_at":
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "stages": item}
        except Exception:  # noqa: BLE001
            return {}
    return {}


__all__ = ["export_delivery_package"]
