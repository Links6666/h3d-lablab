"""h3d.report.csv_export : CSV 指标导出（M6 报表）。

设计依据：
    《软著方案》2.6 ——「所有中间结果元数据用 JSON，所有统计结果用 CSV（便于 Excel
    打开给导师看）」；M6 report 输出 CSV + 图表 + HTML/PDF；
    《软著方案》2.7.4 —— failures.csv 字段（文物编号、阶段、异常类型、异常信息、时间）；
    《教学版》18 —— 精度报告与指标写入。

实现要点：
    1. 统一 ``utf-8-sig`` 编码（带 BOM），Excel 双击直接打开中文不乱码。
    2. ``write_csv`` 是通用导出原语；``export_failures_csv`` / ``export_manifest_csv``
       是面向 M0 编排结果的两个专用导出（字段与设计书对齐）。
    3. 空数据仍写出表头，保证「单项缺失 -> 该列留空」约定（《软著方案》2.6）。
"""

from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Any, Optional

from h3d.core import ExportError, RunManifest, get_logger

log = get_logger("report.csv_export")

# failures.csv 字段（顺序即列序，与《软著方案》2.7.4 对齐）
FAILURE_FIELDS: list[tuple[str, str]] = [
    ("item_id", "文物编号"),
    ("stage", "阶段"),
    ("error_type", "异常类型"),
    ("message", "异常信息"),
    ("time", "时间"),
]


def _ensure_dir(path: str) -> None:
    """创建写出目录；目录不可用时抛 ExportError（E601）。"""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            raise ExportError(f"报表目录创建失败: {directory}") from exc


def write_csv(
    rows: list[dict[str, Any]],
    path: str,
    fieldnames: Optional[list[tuple[str, str]]] = None,
) -> str:
    """通用 CSV 写出原语。

    参数
    ----
    rows:       字典行列表；键即列名。
    path:       目标 CSV 路径（自动建父目录）。
    fieldnames: 可选 ``[(key, 中文列名), ...]`` 序列，控制列序与表头文本；
                省略时按 rows 首次出现的键序输出。

    返回
    ----
    写出后的绝对路径。
    """
    _ensure_dir(path)
    if fieldnames is not None:
        keys = [k for k, _ in fieldnames]
        headers = [h for _, h in fieldnames]
    else:
        keys = list(dict.fromkeys(k for r in rows for k in r))
        headers = keys

    try:
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(headers)
            for r in rows:
                writer.writerow([r.get(k, "") for k in keys])
    except OSError as exc:
        raise ExportError(f"CSV 写出失败: {path}") from exc

    log.info("写出 CSV：%s（%d 行）", path, len(rows))
    return os.path.abspath(path)


def export_failures_csv(failures: list[dict[str, Any]], path: str) -> str:
    """导出失败清单 failures.csv（《软著方案》2.7.4）。

    failures 元素需含 item_id / stage / error_type / message / time 键；缺省列留空。
    """
    rows = [
        {
            "item_id": f.get("item_id", ""),
            "stage": f.get("stage", ""),
            "error_type": f.get("error_type", ""),
            "message": f.get("message", ""),
            "time": f.get("time", ""),
        }
        for f in failures
    ]
    return write_csv(rows, path, fieldnames=FAILURE_FIELDS)


def export_manifest_csv(manifest: RunManifest, path: str) -> str:
    """从 run_manifest.json 导出阶段汇总 CSV（每阶段一行）。

    把 manifest.stages 的 ``item_id/stage`` 复合键拆开为「文物编号 + 阶段」两列，
    附带状态、耗时与产物路径，供 Excel 直接做汇总透视。
    """
    rows: list[dict[str, Any]] = []
    for stage_key, entry in manifest.stages.items():
        item_id, _, stage = str(stage_key).partition("/")
        metrics = {
            k: v for k, v in entry.items()
            if k not in ("status", "outputs", "elapsed_s", "reason")
        }
        rows.append({
            "item_id": item_id or stage_key,
            "stage": stage or item_id,
            "status": entry.get("status", ""),
            "elapsed_s": entry.get("elapsed_s", ""),
            "outputs": "; ".join(entry.get("outputs", [])),
            "metrics": _fmt_metrics(metrics),
        })
    fields = [
        ("item_id", "文物编号"),
        ("stage", "阶段"),
        ("status", "状态"),
        ("elapsed_s", "耗时(s)"),
        ("outputs", "产物路径"),
        ("metrics", "关键指标"),
    ]
    return write_csv(rows, path, fieldnames=fields)


def _fmt_metrics(metrics: dict[str, Any]) -> str:
    """把任意关键指标压成 ``k=v; k=v`` 的可读字符串。"""
    if not metrics:
        return ""
    return "; ".join(f"{k}={v}" for k, v in metrics.items())


__all__ = [
    "FAILURE_FIELDS",
    "write_csv",
    "export_failures_csv",
    "export_manifest_csv",
]
