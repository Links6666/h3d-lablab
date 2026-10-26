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
    ("elapsed_s", "耗时(s)"),
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
        # 实机（2026-10-17）：只报路径无法区分"目录不存在""文件被 Excel 占用"
        # "权限不足"，用户拿不到可行动的信息。带上系统原文。
        raise ExportError(f"CSV 写出失败: {path}（{exc}）") from exc

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
            "elapsed_s": f.get("elapsed_s", ""),
            "message": f.get("message", ""),
            "time": f.get("time", ""),
        }
        for f in failures
    ]
    return write_csv(rows, path, fieldnames=FAILURE_FIELDS)


#: 阶段排序（CSV 行序与报表一致，便于 Excel 里按阶段透视）
_STAGE_ORDER = ("S1", "M1", "M2", "M3", "M4", "P1", "M5", "M6", "X1", "X2")


def export_manifest_csv(manifest: RunManifest, path: str) -> str:
    """从 run_manifest.json 导出阶段汇总 CSV（每阶段一行）。

    把 manifest.stages 的 ``item_id/stage`` 复合键拆开为「文物编号 + 阶段」两列，
    附带状态、耗时与产物路径，供 Excel 直接做汇总透视。

    实机缺陷修正（2026-10-18）：
        1. 失败原因 ``reason`` 原先被当作"普通指标以外"的保留字段直接丢弃 ——
           failures.csv 里只有一行「[E100] M5 参考点云为空」，而 metrics_summary.csv
           里 M5 那行的「关键指标」是**空的**，两表拼不出因果。现单列「异常/备注」。
        2. 「产物数」显式成列：状态 done 却 0 个产物（旧 M2）必须一眼可见，
           而不是让人逐个数分号分隔的路径。
        3. 指标里与「产物路径」列重复的绝对路径不再二次展开（S1 行原先要占三屏）。
        4. 嵌套 dict 展平为 ``父.子``，Excel 里可直接筛选。
    """
    rows: list[dict[str, Any]] = []
    for stage_key, entry in manifest.stages.items():
        item_id, _, stage = str(stage_key).partition("/")
        outputs = [str(p) for p in (entry.get("outputs") or [])]
        metrics = {
            k: v for k, v in entry.items()
            if k not in ("status", "outputs", "elapsed_s", "reason")
        }
        rows.append({
            "item_id": item_id or stage_key,
            "stage": stage or item_id,
            "status": entry.get("status", ""),
            "elapsed_s": entry.get("elapsed_s", ""),
            "n_outputs": len(outputs),
            "outputs": "; ".join(outputs),
            "metrics": _fmt_metrics(metrics, outputs),
            "remark": _first_line(entry.get("reason")),
        })

    order = {s: i for i, s in enumerate(_STAGE_ORDER)}
    rows.sort(key=lambda r: (str(r["item_id"]),
                             order.get(str(r["stage"]), len(order)),
                             str(r["stage"])))

    fields = [
        ("item_id", "文物编号"),
        ("stage", "阶段"),
        ("status", "状态"),
        ("elapsed_s", "耗时(s)"),
        ("n_outputs", "产物数"),
        ("outputs", "产物路径"),
        ("metrics", "关键指标"),
        ("remark", "异常/备注"),
    ]
    return write_csv(rows, path, fieldnames=fields)


def _first_line(text: Any, limit: int = 300) -> str:
    """取文本首行（CSV 单行约束；完整堆栈仍在 failures.csv / 日志里）。"""
    if not text:
        return ""
    first = str(text).strip().splitlines()
    line = first[0] if first else ""
    return line[:limit]


def _flatten(prefix: str, value: Any, out: list[str], depth: int = 0) -> None:
    """把嵌套 dict 展平为 ``父.子=值``（最多两层，避免指标列爆炸）。"""
    if isinstance(value, dict) and value and depth < 2:
        for k, v in value.items():
            _flatten(f"{prefix}.{k}" if prefix else str(k), v, out, depth + 1)
        return
    if value is None:
        out.append(f"{prefix}=N/A" if prefix else "N/A")
        return
    if isinstance(value, bool):
        text = "是" if value else "否"
    else:
        text = str(value)
    if len(text) > 120:
        text = text[:117] + "…"
    out.append(f"{prefix}={text}" if prefix else text)


def _fmt_metrics(metrics: dict[str, Any], outputs: Any = ()) -> str:
    """把关键指标压成 ``k=v; k=v`` 的可读字符串（跳过与产物路径列重复的绝对路径）。"""
    if not metrics:
        return ""
    known = set()
    for p in outputs or ():
        try:
            known.add(os.path.normcase(os.path.abspath(str(p))))
        except (OSError, ValueError):  # pragma: no cover - 非法路径原样保留
            continue
    parts: list[str] = []
    for key, value in metrics.items():
        if isinstance(value, str) and value:
            try:
                if os.path.normcase(os.path.abspath(value)) in known:
                    continue
            except (OSError, ValueError):  # pragma: no cover
                pass
        _flatten(str(key), value, parts)
    return "; ".join(parts)


__all__ = [
    "FAILURE_FIELDS",
    "write_csv",
    "export_failures_csv",
    "export_manifest_csv",
]
