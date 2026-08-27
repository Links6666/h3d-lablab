"""h3d.report.html_export : HTML 报表生成（jinja2 内嵌模板 + 图表 base64 内嵌）。

设计依据：
    《软著方案》2.6 —— M6 report 输出 HTML/PDF（TC-503：HTML 含关键指标与判定）；
    《设计书》§37.4 —— 报告随交付包一并归档，作为验收依据。

实现要点：
    1. jinja2 内嵌字符串模板（避免外部 .html 模板文件依赖，单文件可移植）。
    2. 图表（stage_timings.png）以 base64 内嵌——HTML 单文件自包含，可离线分享。
    3. 内容：项目/运行概要 + 各尊阶段状态表 + 关键指标表（M5 覆盖率/中误差 +
       判定）+ 失败清单（有失败时）+ 交付包索引（有 M6 时）。
"""

from __future__ import annotations

import base64
import os
from typing import Any, Optional

from jinja2 import Environment

from h3d.core import RunManifest, get_logger

log = get_logger("report.html")

_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>造像类文物三维建模自动化系统 — 处理报表</title>
<style>
  body { font-family: "Microsoft YaHei", "Segoe UI", sans-serif; margin: 24px; color: #222; }
  h1 { border-bottom: 3px solid #1a6bb5; padding-bottom: 8px; }
  h2 { color: #1a6bb5; margin-top: 28px; }
  table { border-collapse: collapse; margin: 10px 0 20px; width: 100%; }
  th, td { border: 1px solid #bbb; padding: 6px 10px; text-align: left; }
  th { background: #eef3f8; }
  .pass { color: #2e7d32; font-weight: bold; }
  .fail { color: #c62828; font-weight: bold; }
  .meta { color: #666; font-size: 13px; }
  img { max-width: 100%; border: 1px solid #ddd; }
  .summary { background: #f7fafc; padding: 12px; border-radius: 6px; }
</style>
</head>
<body>
<h1>造像类文物三维建模自动化系统 — 处理报表</h1>
<div class="summary">
  <b>项目</b>：{{ project }}　<b>运行标识</b>：<span class="meta">{{ run_id }}</span><br>
  <b>处理造像</b>：{{ n_items }} 尊　<b>配置哈希</b>：<span class="meta">{{ config_sha }}</span>
</div>

<h2>一、各尊处理状态</h2>
<table>
  <tr><th>文物编号</th><th>阶段</th><th>状态</th><th>耗时(s)</th></tr>
  {% for row in stage_rows %}
  <tr>
    <td>{{ row.item }}</td><td>{{ row.stage }}</td>
    <td class="{{ 'pass' if row.status == 'done' else ('fail' if row.status == 'fail' else '') }}">
      {{ row.status }}</td>
    <td>{{ row.elapsed }}</td>
  </tr>
  {% endfor %}
</table>

<h2>二、关键指标（M5 质检）</h2>
<table>
  <tr><th>文物编号</th><th>覆盖率</th><th>中误差(mm)</th><th>平均点间距(m)</th><th>判定</th></tr>
  {% for row in qc_rows %}
  <tr>
    <td>{{ row.item }}</td>
    <td>{{ row.coverage }}</td><td>{{ row.rmse }}</td><td>{{ row.mps }}</td>
    <td class="{{ 'pass' if row.qc == 'pass' else 'fail' }}">{{ row.qc }}</td>
  </tr>
  {% else %}
  <tr><td colspan="5">无质检数据（M5 未执行）</td></tr>
  {% endfor %}
</table>

<h2>三、交付包索引（M6 发布）</h2>
<table>
  <tr><th>文物编号</th><th>状态</th><th>交付模型数</th></tr>
  {% for row in publish_rows %}
  <tr><td>{{ row.item }}</td><td class="pass">{{ row.status }}</td><td>{{ row.n_models }}</td></tr>
  {% else %}
  <tr><td colspan="3">无交付记录（M6 未执行）</td></tr>
  {% endfor %}
</table>

<h2>四、失败清单</h2>
<table>
  <tr><th>文物编号</th><th>阶段</th><th>异常类型</th><th>异常信息</th></tr>
  {% for row in failure_rows %}
  <tr>
    <td>{{ row.item }}</td><td>{{ row.stage }}</td>
    <td>{{ row.error_type }}</td><td>{{ row.message }}</td>
  </tr>
  {% else %}
  <tr><td colspan="4">无失败（全部通过或未运行）</td></tr>
  {% endfor %}
</table>

<h2>五、阶段耗时图</h2>
{% if chart_b64 %}
<img src="data:image/png;base64,{{ chart_b64 }}" alt="stage timings">
{% else %}
<p class="meta">无图表（需 report.formats 含 png）</p>
{% endif %}

<p class="meta">生成时间：{{ generated_at }}｜系统版本 h3d 0.1.0｜<i>本报告由系统自动生成，随交付包一并归档</i></p>
</body>
</html>
"""


def generate_html(manifest: RunManifest,
                  failures: Optional[list[dict[str, Any]]] = None,
                  out_path: str = "report.html",
                  chart_png: Optional[str] = None,
                  project: str = "") -> str:
    """生成自包含 HTML 报表（指标/状态/失败/图表全内嵌）。

    参数
    ----
    manifest:  RunManifest 溯源元数据。
    failures:  失败清单（可为 None / 空）。
    out_path:  输出 HTML 路径。
    chart_png: 阶段耗时图 PNG 路径（可选，base64 内嵌）。
    project:   项目名（概要显示）。

    返回
    ----
    HTML 文件绝对路径。
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    env = Environment(autoescape=True)

    # 阶段行（按 item 分组，M1~M6 顺序）
    stage_rows: list[dict] = []
    items = sorted({key.split("/")[0] for key in manifest.stages})
    for item in items:
        for stage_id in ("M1", "M2", "M3", "M4", "M5", "M6"):
            entry = manifest.stages.get(f"{item}/{stage_id}")
            if entry:
                stage_rows.append({
                    "item": item, "stage": stage_id,
                    "status": entry.get("status", "?"),
                    "elapsed": f"{entry.get('elapsed_s', 0.0):.2f}",
                })

    # M5 质检行
    qc_rows: list[dict] = []
    for item in items:
        m5 = manifest.stages.get(f"{item}/M5")
        if m5:
            coverage = m5.get("coverage")
            qc_rows.append({
                "item": item,
                "coverage": f"{coverage * 100:.1f}%" if coverage is not None else "N/A",
                "rmse": f"{m5.get('rmse_mm', float('nan')):.1f}",
                "mps": f"{m5.get('mps_m', float('nan')):.4f}",
                "qc": "pass" if m5.get("status") == "done" else "fail",
            })

    # M6 交付行
    publish_rows: list[dict] = []
    for item in items:
        m6 = manifest.stages.get(f"{item}/M6")
        if m6 and m6.get("status") == "done":
            publish_rows.append({
                "item": item, "status": "published",
                "n_models": m6.get("n_models", "?"),
            })

    failure_rows = [
        {"item": f.get("item_id", "?"), "stage": f.get("stage", "?"),
         "error_type": f.get("error_type", "?"), "message": f.get("message", "")}
        for f in (failures or [])
    ]

    chart_b64 = ""
    if chart_png and os.path.exists(chart_png):
        with open(chart_png, "rb") as fh:
            chart_b64 = base64.b64encode(fh.read()).decode("ascii")

    html = env.from_string(_TEMPLATE).render(
        project=project or "（未命名）",
        run_id=manifest.run_id or "N/A",
        config_sha=(manifest.config_sha256 or "N/A")[:16],
        n_items=len(items),
        stage_rows=stage_rows,
        qc_rows=qc_rows,
        publish_rows=publish_rows,
        failure_rows=failure_rows,
        chart_b64=chart_b64,
        generated_at=_now(),
    )
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    log.info("HTML 报表：%s（%d 尊，%d 阶段行）", out_path, len(items), len(stage_rows))
    return os.path.abspath(out_path)


def _now() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


__all__ = ["generate_html"]
