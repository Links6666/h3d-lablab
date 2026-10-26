"""h3d.report : 报表输出（M6）—— CSV 指标汇总 + matplotlib 图表。

设计依据：
    《软著方案》2.6 —— M6 report 把各阶段 JSON 汇总为 CSV + 图表 + HTML/PDF；
    《软著方案》2.9 —— ``h3d report -c cfg.yaml`` 仅重新生成报表（与批量处理解耦）。

对外暴露三个子模块的导出函数，并提供 :func:`generate` 汇总入口（配置驱动）。输入为
:class:`RunManifest` 溯源元数据与失败清单，输出 CSV / 图表到报表目录。

典型用法::

    from h3d.core import RunManifest, load
    from h3d.report import generate

    cfg = load("configs/example.yaml")
    manifest = RunManifest.load(cfg.runner.manifest)
    generate(manifest, failures=[...], report_cfg=cfg.report)
"""

from __future__ import annotations

import os
from typing import Any, Optional

from h3d.core import ExportError, ReportConfig, RunManifest, get_logger

from .csv_export import export_failures_csv, export_manifest_csv, write_csv
from .plot_gen import plot_bar, plot_metric_series, plot_stage_timings
from .html_export import generate_html

log = get_logger("report")


def generate(
    manifest: RunManifest,
    failures: Optional[list[dict[str, Any]]] = None,
    report_cfg: Optional[ReportConfig] = None,
) -> list[str]:
    """报表汇总入口：把 manifest 与失败清单落地为 CSV / 图表。

    参数
    ----
    manifest:   RunManifest 溯源元数据（M0 编排后落盘的 run_manifest.json）。
    failures:   失败清单（failures.csv 的数据源），可为 None 表示无失败。
    report_cfg: ReportConfig；默认启用并输出到 ``report``（相对项目根）。

    返回
    ----
    生成的文件绝对路径列表（csv + png）。
    """
    report_cfg = report_cfg or ReportConfig()
    if not report_cfg.enabled:
        log.info("报表输出被禁用（report.enabled=false）")
        return []

    out_dir = report_cfg.output_dir
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise ExportError(f"报表目录创建失败: {out_dir}") from exc

    outputs: list[str] = []
    chart_png = None
    if "csv" in report_cfg.formats:
        outputs.append(
            export_manifest_csv(manifest, os.path.join(out_dir, "metrics_summary.csv")))
        if failures:
            outputs.append(
                export_failures_csv(failures, os.path.join(out_dir, "failures.csv")))
    if "png" in report_cfg.formats:
        # 图表失败不应中断报表（实机 E601）：CSV/HTML 仍要交付。
        try:
            chart_png = plot_stage_timings(
                manifest, os.path.join(out_dir, "stage_timings.png"))
        except ExportError as exc:
            log.warning("阶段耗时图生成失败，已跳过：%s", exc)
            chart_png = None
        if chart_png:
            outputs.append(chart_png)
    if "html" in report_cfg.formats:
        try:
            outputs.append(
                generate_html(manifest, failures,
                              os.path.join(out_dir, "report.html"),
                              chart_png=chart_png,
                              project=report_cfg.project_name))
        except Exception as exc:  # noqa: BLE001 - HTML 失败不阻断其余产物
            log.warning("HTML 报表生成失败，已跳过：%s", exc)

    log.info("报表生成完成：%d 个文件 -> %s", len(outputs), out_dir)
    return outputs


__all__ = [
    "write_csv",
    "export_failures_csv",
    "export_manifest_csv",
    "plot_bar",
    "plot_stage_timings",
    "plot_metric_series",
    "generate",
]
