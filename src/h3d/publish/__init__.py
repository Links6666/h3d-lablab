"""h3d.publish : 发布归档（M6 发布与溯源，L2 业务层 / S4 阶段）—— 主线收官。

设计依据：
    《设计书》§37 —— M6 发布与溯源：将带纹理模型导出为标准开放格式，嵌入元数据
        与 manifest 快照，形成可交付包（80_export 导出 + 90_meta 元数据）；
    《软著方案》2.4 —— 交付包 = 导出模型 + 质检报告 + manifest 一并归档；
    《设计书》§6.3 —— 任一质量门不达标时该尊不进入交付包（M5 失败已隔离）。

实现要点：
    1. 交付模型取自 M4 最高精度带色模型（50_tex/<item>/<item>_L1.<fmt>），
       复制到 80_export/<item>/ 并去掉 LOD 标签（标准命名 <item>.<fmt>）。
    2. 元数据快照（90_meta/<item>_meta.json）：项目信息 + 各阶段指标 +
       软件版本 + 配置哈希，构成溯源依据（设计书「可复算、可审计」）。
    3. 交付清单（90_meta/delivery_manifest.json）：汇总本批次全部交付包，
       含文件路径 / 状态 / 关键指标，验收依据（TC-602）。
    4. 可选把质检报告（70_report/accuracy.csv）复制进交付包（copy_qc）。
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any, Optional

from h3d.core import PublishConfig, get_logger

log = get_logger("publish")


def export_delivery(tex_dir: str, out_dir: str, item_id: str,
                    formats: Optional[list[str]] = None) -> list[str]:
    """把 50_tex 的最高精度带色模型复制为交付模型（80_export/<item>/）。

    取 ``<item>_L1.<fmt>``（LOD 1.0 原始精度，M4 产物）；缺少某格式时尝试任意
    LOD 变体并告警。目标路径不可写抛 ExportError（E601）。

    参数
    ----
    tex_dir:  M4 输出目录（50_tex/<item>/）。
    out_dir:  交付输出目录（80_export/<item>/）。
    item_id:  文物编号。
    formats:  交付格式列表（默认 [ply, obj, glb]）。

    返回
    ----
    交付模型文件绝对路径列表。
    """
    from h3d.core import ExportError

    formats = formats or ["ply", "obj", "glb"]
    os.makedirs(out_dir, exist_ok=True)
    outputs: list[str] = []
    for fmt in formats:
        fmt = fmt.lstrip(".").lower()
        # 优先 L1（原始精度），回退任意 *_L* 变体
        src = os.path.join(tex_dir, f"{item_id}_L1.{fmt}")
        if not os.path.exists(src):
            candidates = [f for f in os.listdir(tex_dir)
                          if f.startswith(f"{item_id}_L") and f.endswith(f".{fmt}")]
            if candidates:
                src = os.path.join(tex_dir, sorted(candidates)[-1])
                log.warning("%s 无 L1.%s，回退 %s", item_id, fmt, os.path.basename(src))
            else:
                log.warning("%s 无 %s 格式模型，跳过（E601 语义）", item_id, fmt)
                continue
        dst = os.path.join(out_dir, f"{item_id}.{fmt}")
        try:
            shutil.copy2(src, dst)
        except OSError as exc:
            raise ExportError(f"交付模型复制失败: {dst}") from exc
        outputs.append(os.path.abspath(dst))
    log.info("交付模型导出：%s（%d 格式 -> %s）", item_id, len(outputs), out_dir)
    return outputs


def build_meta(item_id: str, stage_metrics: dict[str, Any],
               project: dict[str, Any], software: dict[str, Any],
               run_id: str, config_hash: str) -> dict[str, Any]:
    """构建元数据快照（manifest 溯源，设计书 §37.2 embed_meta）。

    参数
    ----
    item_id:      文物编号。
    stage_metrics: 该尊各阶段指标（manifest 中 <item>/<stage> 的合并）。
    project:      项目信息（name / operator 等）。
    software:     软件版本快照（core.metadata.software_versions）。
    run_id:       本次运行标识。
    config_hash:  配置哈希（可复算）。

    返回
    ----
    元数据字典（序列化到 90_meta/<item>_meta.json）。
    """
    return {
        "item_id": item_id,
        "run_id": run_id,
        "config_hash": config_hash,
        "project": project,
        "software": software,
        "stages": stage_metrics,
        "delivery": {
            "models": list(sorted(stage_metrics.get("M6", {}).get("outputs", []))),
            "qc": stage_metrics.get("M5", {}).get("status", "N/A"),
        },
    }


def write_meta(meta: dict[str, Any], meta_dir: str, item_id: str) -> str:
    """把元数据快照写为 90_meta/<item>_meta.json，返回路径。"""
    os.makedirs(meta_dir, exist_ok=True)
    path = os.path.join(meta_dir, f"{item_id}_meta.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    log.info("元数据快照：%s（%d 项）", path, len(meta))
    return os.path.abspath(path)


def copy_qc_report(task_root: str, out_dir: str, item_id: str) -> Optional[str]:
    """把质检报告 accuracy.csv 复制进交付包（可选，copy_qc=True）。"""
    src = os.path.join(task_root, "70_report", item_id, "accuracy.csv")
    if not os.path.exists(src):
        log.info("%s 无质检报告，跳过归档", item_id)
        return None
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, "accuracy.csv")
    shutil.copy2(src, dst)
    return os.path.abspath(dst)


def build_delivery_manifest(entries: list[dict[str, Any]],
                            manifest_path: str) -> str:
    """汇总全部交付包清单（90_meta/delivery_manifest.json，验收依据 TC-602）。

    参数
    ----
    entries:       每尊交付记录（item_id / status / models / qc_status / meta）。
    manifest_path: 输出路径（90_meta/delivery_manifest.json）。

    返回
    ----
    清单文件绝对路径。
    """
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    payload = {"deliveries": entries, "n_items": len(entries),
               "n_published": sum(1 for e in entries if e.get("status") == "published")}
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    log.info("交付清单：%s（%d 尊，%d 已发布）", manifest_path,
             payload["n_items"], payload["n_published"])
    return os.path.abspath(manifest_path)


__all__ = [
    "export_delivery",
    "build_meta",
    "write_meta",
    "copy_qc_report",
    "build_delivery_manifest",
]
