"""h3d.report.html_export : HTML 报表生成（jinja2 内嵌模板 + 图表 base64 内嵌）。

设计依据：
    《软著方案》2.6 —— M6 report 输出 HTML/PDF（TC-503：HTML 含关键指标与判定）；
    《设计书》§37.4 —— 报告随交付包一并归档，作为验收依据。

实现要点：
    1. jinja2 内嵌字符串模板（避免外部 .html 模板文件依赖，单文件可移植）。
    2. 图表（stage_timings.png）以 base64 内嵌——HTML 单文件自包含，可离线分享。
    3. 内容：项目/运行概要 → 各件阶段状态 → 几何与尺度 → 纹理质量 → M5 质检 →
       交付包索引 → 多时相形变 → 自动诊断 → 失败清单 → 阶段耗时图。

2026-10-18 实机（纯照片件"3D 预览无颜色"）暴露的报表缺陷，本版逐条修正：
    a. **未执行阶段在状态表里完全不出现**，却计入"未执行 2"——表里只有 6 行，
       用户无从知道缺的是哪两步。现缺失阶段成行并标「未执行」。
    b. 状态列直接吐 ``done``/``fail``，与未执行混排无视觉区分。现中文 + 绿/红/灰着色。
    c. 中误差/点间距缺值直接格式化 ``float('nan')`` → 表格印出 "nan"。现统一 ``N/A``。
    d. ``coverage_ratio`` 被当"网格覆盖率"读（4.6% 被误解成"95% 表面没贴图"）。
       现拆成「图集像素利用率 / UV 岛占比 / 岛内填充率」三列并附口径说明。
    e. 纯照片项目没有激光锚点、坐标是 SfM **任意单位**，报表仍写 "mm"。
       现按 ``metric_units`` 改写单位口径并给出显式提示。
    f. 状态 done 却 0 个产物（旧 M2）无任何标记。现摘要列显式提示。
    g. 失败建议只认 COLMAP 关键字（CUDA/sqlite），E1xx 输入类错误一条建议都没有。
       现按错误码/异常类型补全，并把「低填充率 / 网格碎片化 / 未标定尺度」等
       可由指标直接判定的问题汇总为独立的「自动诊断」小节。
"""

from __future__ import annotations

from h3d import __version__

import base64
import math
import os
from typing import Any, Callable, Optional

from jinja2 import Environment

from h3d.core import RunManifest, get_logger

log = get_logger("report.html")

#: 报表状态表的阶段顺序（S1 必须在列：纯照片模式下它是第一个执行的阶段；
#: P1 紧跟 M4 而不是排在最后——它是对 M4 的增强，不是独立收尾）
_STAGE_ORDER = ("S1", "M1", "M2", "M3", "M4", "P1", "M5", "M6")

#: 状态中文名 + CSS 类（**五态**，2026-10-26）
#: 完成 / 失败 / 跳过（已启用但没跑）/ 未启用（配置关闭）/ 未执行（无记录）
_STATUS_CN = {
    "done": ("完成", "pass"),
    "fail": ("失败", "fail"),
    "running": ("运行中", "running"),
    "pending": ("未执行", "skip"),
    "skipped": ("跳过", "skip"),
    "disabled": ("未启用", "skip"),
}

#: 「跳过」的细分原因（manifest 的 ``skip_kind``）。
#: 不细分的话，"条件不满足"和"降级"读起来一样，用户不知道要不要补拍/补数据。
_SKIP_KIND_CN = {
    "condition": "跳过·条件不满足",
    "degraded": "跳过·降级",
    "resume": "跳过·续跑复用",
}

#: 几何来源中文说明
_SOURCE_CN = {
    "sfm_only": "SfM 稠密（唯一几何源）",
    "laser+sfm": "激光 + SfM 融合",
    "laser": "激光扫描",
}


# --------------------------------------------------------------------------- #
# 数值格式化（核心：一切缺失 / nan / inf 统一成 N/A，绝不把 "nan" 印进表格）
# --------------------------------------------------------------------------- #
def _num(value: Any, digits: int = 1, scale: float = 1.0,
         suffix: str = "", default: str = "N/A") -> str:
    """安全数值格式化：None / 非数值 / nan / inf 一律返回 ``default``。"""
    if value is None:
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(num):
        return default
    return f"{num * scale:.{digits}f}{suffix}"


def _pct(value: Any, digits: int = 1, default: str = "N/A") -> str:
    """比例 -> 百分数文本。"""
    return _num(value, digits, 100.0, "%", default)


def _yes(value: Any, default: str = "N/A") -> str:
    """布尔 -> 是/否。"""
    if value is None:
        return default
    return "是" if value else "否"


# --------------------------------------------------------------------------- #
# 阶段行 / 诊断
# --------------------------------------------------------------------------- #
def _geom_source(m1: dict) -> str:
    """几何来源判定：优先用 M1 显式落盘的 ``dense_source``。

    旧 manifest 没有该键时按指标反推（``n_scans=0`` 且有融合 = 只有 SfM 稠密），
    避免报表在「几何来源」列直接印 N/A——这是判断"坐标有没有米制语义"的第一依据。
    """
    src = m1.get("dense_source")
    if src:
        return _SOURCE_CN.get(src, str(src))
    n_scans = m1.get("n_scans")
    if n_scans == 0 and m1.get("dense_merged"):
        return "SfM 稠密（唯一几何源，据指标推断）"
    if isinstance(n_scans, int) and n_scans > 0:
        return "激光扫描"
    return "N/A"


def _stage_note(entry: dict) -> str:
    """阶段行的"产物 / 异常摘要"。

    失败时取 reason 首行（用户最需要的是一句话原因，而不是完整堆栈）；
    成功时显示产物个数，便于确认该阶段确实落盘；
    **done 但 0 个产物必须显式提示**——旧 M2 就是这个状态，报表里看起来一切正常。
    **跳过/未启用必须带原因**——否则「跳过」二字无法区分"我关掉了"与"没数据"。
    """
    status = entry.get("status")
    if status == "fail":
        reason = (entry.get("reason") or "").strip()
        first = reason.splitlines()[0] if reason else "（无异常信息）"
        return first[:200]
    if status in ("skipped", "disabled"):
        return str(entry.get("skip_reason") or entry.get("reason") or "")[:200]
    outputs = entry.get("outputs") or []
    if outputs:
        return f"{len(outputs)} 个产物"
    if status == "done":
        return "0 个产物（该阶段未落盘，请检查日志）"
    return ""


def _failure_hints(failures: list[dict[str, Any]]) -> list[str]:
    """按失败特征生成可执行诊断建议（去重、最多 5 条）。"""
    hints: list[str] = []
    blob = " ".join(str(f.get("message", "")) for f in failures)
    low = blob.lower()
    types = " ".join(str(f.get("error_type", "")) for f in failures).lower()

    if "e100" in low or "参考点云为空" in blob:
        hints.append(
            "M5「参考点云为空」＝上游 M2/M1 的产物列表丢了：断点续跑时若只把阶段标成"
            "「跳过」而不恢复该阶段的 outputs，下游就看不到任何参考点云。"
            "确认版本已含 restore_stage 修复（复用既有产物），否则删掉项目的 "
            "run_manifest.json 后重跑该件。")
    elif "indataerror" in types or "e1" in low or "缺少" in blob or "为空" in blob:
        hints.append(
            "输入类错误（E1xx）：报错行里的路径就是缺的东西。请确认上游阶段产物目录"
            "（10_clean / 20_registered / 30_dense / 50_tex）确实有文件，"
            "并确认该件不是被「断点续跑」或「冗余清理」误删。")
    if "qualitygate" in types or "质检不达标" in blob or "e3" in low:
        hints.append(
            "质检门限未通过：coverage / rmse 是对「网格与参考点云」的一致性质检，"
            "不是贴图质量。若确实要放宽，调 configs 中 quality.coverage_threshold / "
            "rmse_threshold_mm；纯照片件还应先确认坐标是任意单位（此时 mm 阈值无意义）。")
    if "constraint failed" in low or "sqlite error" in low:
        hints.append(
            "S1 数据库约束冲突：通常是上一次运行残留的 database.db / 半写入状态导致。"
            "本版本每次全量重建前会自动清理旧库；若仍复现，请确认没有多个 H3D / COLMAP "
            "进程同时写同一工作区。")
    if "c0000409" in low or "3221226505" in low:
        hints.append(
            "COLMAP 进程异常终止（0xC0000409）：多为数据库半写入或并发写入，"
            "也可能与超大图特征提取有关。可将 sfm_solve.max_image_size 降到 2400 或更低后重试。")
    if "cuda" in low:
        hints.append(
            "稠密重建依赖 CUDA：请确认 NVIDIA 驱动可用、且 configs 中 "
            "sfm_solve.colmap_path 指向 CUDA 构建的 colmap.exe。")
    if "no such file" in low or "not found" in low or "找不到" in blob:
        hints.append(
            "有外部程序或路径不存在：检查 configs 中 sfm_solve.colmap_path、"
            "照片目录与项目根目录是否有效。")
    if "timeout" in low or "超时" in blob:
        hints.append(
            "阶段超时：稠密重建需覆盖 photometric + geom_consistency 两遍，"
            "超时预算应 ≥ 张数 × 百万像素 × 45 s × 2。也可降 dense_max_image_size。")
    if not hints and failures:
        hints.append(
            "查看项目根目录下 99_runs/sfm_tmp/<文物编号>/logs/ 内的 "
            "colmap_stepN.log 获取完整输出。")
    return hints[:5]


def _findings(items: list[str],
              get: Callable[[str, str], dict]) -> list[dict[str, str]]:
    """由指标直接推导的「自动诊断」条目（不依赖失败清单）。

    报表不能只罗列数字：用户看 4.6%"覆盖率"会以为 95% 表面没贴图，
    看 25 个连通块也未必意识到"模型碎成一把碎片"。这里把判据固化下来。
    """
    out: list[dict[str, str]] = []
    for item in items:
        m1, m3, m4, m5 = (get(item, "M1"), get(item, "M3"),
                          get(item, "M4"), get(item, "M5"))

        fill = m4.get("island_fill_ratio")
        if isinstance(fill, (int, float)) and fill < 0.85:
            out.append({"level": "warn", "text": (
                f"{item}：UV 岛内仅 {_pct(fill)} 纹素采到颜色（阈值 85%）——"
                "大面积无可见相机。优先检查法线朝向是否整体翻转、位姿与遮挡判据，"
                "其次看 UV 岛是否过碎。")})
        flipped = m4.get("normal_flipped")
        if flipped:
            out.append({"level": "warn", "text": (
                f"{item}：法线整体朝向已被自动校正（原始网格法线半数以上指向背面）——"
                "这说明上游点云法线定向不可靠，贴图前必须依赖相机投票兜底。")})
        cov = m4.get("coverage_ratio")
        if isinstance(cov, (int, float)) and cov < 0.20:
            out.append({"level": "warn", "text": (
                f"{item}：图集仅 {_pct(cov)} 像素有颜色——交付模型会呈「有几何无颜色」"
                "或大面积黑斑。这是贴图链路问题，不是几何问题。")})

        n_comp = m3.get("n_components")
        ratio = m3.get("largest_component_ratio")
        if isinstance(n_comp, int) and n_comp > 3 and isinstance(ratio, (int, float)) \
                and ratio < 0.60:
            out.append({"level": "warn", "text": (
                f"{item}：网格碎成 {n_comp} 个连通块，最大块仅占 {_pct(ratio)} 面积——"
                "多为背景/台面被一并重建。建议在 M1/M3 增加平面剔除（RANSAC 主平面）"
                "后重新三角化。")})

        mu = m5.get("metric_units")
        if mu is False:
            out.append({"level": "info", "text": (
                f"{item}：无激光锚点，几何来自 SfM 稠密重建——坐标是**任意单位**，"
                f"包围盒对角线 {_num(m5.get('model_bbox_diag'), 3)}（非米）。"
                f"引精度请用<b>相对中误差 {_pct(m5.get('rmse_rel'), 2)}</b>"
                "（中误差 / 对角线），它与尺度无关；"
                "要出毫米级报告必须补尺度控制点（X2）或激光扫描件。")})

        novel = m1.get("dense_novel_ratio")
        if isinstance(novel, (int, float)) and novel < 0.15 and m1.get("dense_merged"):
            out.append({"level": "info", "text": (
                f"{item}：SfM 稠密点云相对激光几何的新增信息仅 {_pct(novel)}"
                "（阈值 15%）却仍被并入——会降低精度并增加面数。")})
    return out


# --------------------------------------------------------------------------- #
# 模板
# --------------------------------------------------------------------------- #
_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>中小型文物三维建模自动化系统 — 处理报表</title>
<style>
  body { font-family: "Microsoft YaHei", "Segoe UI", sans-serif; margin: 24px; color: #222; }
  h1 { border-bottom: 3px solid #1a6bb5; padding-bottom: 8px; }
  h2 { color: #1a6bb5; margin-top: 28px; border-left: 4px solid #1a6bb5;
       padding-left: 8px; }
  table { border-collapse: collapse; margin: 10px 0 20px; width: 100%; }
  th, td { border: 1px solid #bbb; padding: 6px 10px; text-align: left; }
  th { background: #eef3f8; font-weight: 600; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .pass { color: #2e7d32; font-weight: bold; }
  .fail { color: #c62828; font-weight: bold; }
  .skip { color: #888; }
  .running { color: #ef6c00; font-weight: bold; }
  .warn { color: #a1440b; font-weight: 600; }
  .meta { color: #666; font-size: 13px; }
  img { max-width: 100%; border: 1px solid #ddd; }
  .summary { background: #f7fafc; padding: 12px; border-radius: 6px; }
  .banner-fail { background: #fdecea; border-left: 4px solid #c62828; padding: 10px 14px;
                 margin: 12px 0; border-radius: 4px; }
  .banner-ok { background: #e8f5e9; border-left: 4px solid #2e7d32; padding: 10px 14px;
               margin: 12px 0; border-radius: 4px; }
  .banner-info { background: #e8f1fb; border-left: 4px solid #1a6bb5; padding: 10px 14px;
                 margin: 12px 0; border-radius: 4px; font-size: 13px; }
  td.msg { white-space: pre-wrap; word-break: break-word; font-size: 12px;
           font-family: Consolas, "Courier New", monospace; max-width: 720px; }
  td.note { white-space: pre-wrap; word-break: break-word; font-size: 12px;
            color: #555; max-width: 320px; }
  .hint { color: #8a6d3b; background: #fcf8e3; padding: 10px 14px; border-radius: 4px;
          font-size: 13px; }
  ul.findings { padding-left: 20px; font-size: 13px; }
  ul.findings li.warn { color: #a1440b; margin-bottom: 6px; }
  ul.findings li.info { color: #1a4f7a; margin-bottom: 6px; }
  .footnote { color: #666; font-size: 12px; line-height: 1.7; }
  .footnote code { background: #f2f2f2; padding: 1px 4px; border-radius: 3px; }
</style>
</head>
<body>
<h1>中小型文物三维建模自动化系统 — 处理报表</h1>
<div class="summary">
  <b>项目</b>：{{ project }}　<b>运行标识</b>：<span class="meta">{{ run_id }}</span><br>
  <b>处理文物</b>：{{ n_items }} 件　<b>配置哈希</b>：<span class="meta">{{ config_sha }}</span><br>
  <b>阶段统计</b>：完成 {{ n_done }}　失败 {{ n_fail }}　跳过 {{ n_skipped }}
  　未启用 {{ n_disabled }}　未执行 {{ n_pending }}
  <br><span class="meta">分母 {{ n_evaluated }} = {{ n_items }} 件 × {{ n_stages }} 阶段
  − 未启用 {{ n_disabled }}（配置关闭的阶段不计入评估范围）</span>
</div>
{% if n_fail %}
<div class="banner-fail">
  <b>整体判定：失败</b>　共 {{ n_fail }} 个阶段失败，下游阶段未执行。
  失败原因见「失败清单」，可执行的处置建议见「自动诊断」与「诊断建议」。
</div>
{% elif n_done and not n_pending and not n_skipped %}
<div class="banner-ok"><b>整体判定：通过</b>　已启用的 {{ n_done }} 个阶段全部完成
  {%- if n_disabled %}（另有 {{ n_disabled }} 个阶段被配置关闭，与本次结论无关）{% endif %}。</div>
{% elif n_done and not n_pending %}
<div class="banner-ok"><b>整体判定：通过（含跳过）</b>　完成 {{ n_done }} 个阶段，
  {{ n_skipped }} 个阶段按条件跳过（前置条件不满足或降级），无阶段处于未执行状态。</div>
{% elif n_done %}
<div class="banner-ok"><b>整体判定：部分完成</b>　完成 {{ n_done }} 个阶段，
  另有 {{ n_pending }} 个阶段<b>未执行</b>（上游失败导致从未到达），
  {{ n_skipped }} 个阶段按条件跳过。</div>
{% else %}
<div class="hint">本次运行未产生任何阶段结果（可能尚未开始，或任务队列为空）。</div>
{% endif %}
{% if no_metric_units %}
<div class="banner-info">
  <b>坐标口径提示</b>：本批存在<b>无激光锚点</b>的件，其几何来自 SfM 稀疏/稠密重建，
  坐标是<b>任意单位</b>（无米制语义）。表中「中误差 / 点间距 / 包围盒对角线」只反映
  模型内部自洽性，<b>不能作为真实精度对外引用</b>。
</div>
{% endif %}

<h2>一、各件处理状态</h2>
<table>
  <tr><th>文物编号</th><th>阶段</th><th>状态</th><th>耗时(s)</th><th>产物 / 异常摘要</th></tr>
  {% for row in stage_rows %}
  <tr>
    <td>{{ row.item }}</td><td>{{ row.stage }}</td>
    <td class="{{ row.cls }}">{{ row.status }}</td>
    <td class="num">{{ row.elapsed }}</td>
    <td class="note">{{ row.note }}</td>
  </tr>
  {% else %}
  <tr><td colspan="5">无阶段记录</td></tr>
  {% endfor %}
</table>
<p class="footnote">
  <b>五态终态口径</b>（2026-10-26 起）：
  <code>完成</code>＝本次跑完；
  <code>跳过·条件不满足</code>＝阶段已启用，但前置条件不成立（无照片 / 无参考期 / 与 M4 互斥）；
  <code>跳过·降级</code>＝增强阶段失败但主线可继续（原始原因见备注列）；
  <code>未启用</code>＝配置里根本关掉了（<code>*.enabled=false</code>），<b>不是异常</b>，
  也不计入评估分母；
  <code>未执行</code>＝本次运行没有该阶段记录（通常是上游失败导致从未到达）。
  均不是"执行了但没产物"——后者会显示为「0 个产物」。</p>

<h2>二、几何与尺度</h2>
<table>
  <tr><th>文物编号</th><th>几何来源</th><th>背景剔除</th><th>输入点</th><th>清理后点</th>
      <th>顶点</th><th>三角面</th><th>水密</th><th>连通块</th><th>最大块占比</th>
      <th>包围盒对角线</th><th>坐标口径</th></tr>
  {% for row in geom_rows %}
  <tr>
    <td>{{ row.item }}</td><td>{{ row.source }}</td><td>{{ row.bg }}</td>
    <td class="num">{{ row.points_in }}</td><td class="num">{{ row.points_out }}</td>
    <td class="num">{{ row.vertices }}</td><td class="num">{{ row.triangles }}</td>
    <td>{{ row.watertight }}</td><td class="num">{{ row.n_comp }}</td>
    <td class="num">{{ row.largest }}</td><td class="num">{{ row.bbox_diag }}</td>
    <td class="{{ 'warn' if row.units == 'SfM 任意单位' else '' }}">{{ row.units }}</td>
  </tr>
  {% else %}
  <tr><td colspan="12">无几何数据（M1/M3 未执行）</td></tr>
  {% endfor %}
</table>

<h2>三、纹理质量（M4 贴图管线）</h2>
<table>
  <tr><th>文物编号</th><th>图集</th><th>参与相机</th><th>图集像素利用率</th>
      <th>UV 岛占比</th><th>岛内填充率</th><th>平均亮度</th><th>多视角色差</th>
      <th>法线校正</th><th>像素换算</th><th>判定</th></tr>
  {% for row in tex_rows %}
  <tr>
    <td>{{ row.item }}</td><td>{{ row.size }}</td><td class="num">{{ row.cameras }}</td>
    <td class="num">{{ row.coverage }}</td><td class="num">{{ row.utilization }}</td>
    <td class="num">{{ row.fill }}</td><td class="num">{{ row.luma }}</td>
    <td class="num">{{ row.consistency }}</td><td>{{ row.flipped }}</td>
    <td class="num">{{ row.pixel_scale }}</td>
    <td class="{{ row.cls }}">{{ row.verdict }}</td>
  </tr>
  {% else %}
  <tr><td colspan="11">无贴图数据（M4 未执行）</td></tr>
  {% endfor %}
</table>
<p class="footnote">
  <b>三个比例的含义完全不同，勿混读</b>：
  <code>图集像素利用率</code> = 有颜色的图集像素 / 图集总面积（1024² 图集做到 5%~10% 也可能完全正常）；
  <code>UV 岛占比</code> = UV 岛覆盖面积 / 图集面积（由网格三角面积与 UV 展开决定）；
  <code>岛内填充率</code> = 图集像素利用率 / UV 岛占比，<b>这才是"贴图有没有贴上"的真口径</b>，
  低于 85% 说明岛内大量纹素没采到颜色（法线朝向 / 遮挡 / 位姿问题）。
</p>

<h2>四、质检指标（M5）</h2>
<table>
  <tr><th>文物编号</th><th>表面覆盖率</th><th>中误差（{{ rmse_unit }}）</th>
      <th>相对中误差（%对角线）</th>
      <th>平均点间距（{{ mps_unit }}）</th><th>边界占比</th>
      <th>纹理清晰度</th><th>纹理接缝</th><th>纹理图集覆盖率</th><th>岛内填充率</th>
      <th>纹理达标</th><th>判定</th></tr>
  {% for row in qc_rows %}
  <tr>
    <td>{{ row.item }}</td>
    <td class="num">{{ row.coverage }}</td><td class="num">{{ row.rmse }}</td>
    <td class="num">{{ row.rmse_rel }}</td>
    <td class="num">{{ row.mps }}</td><td class="num">{{ row.boundary }}</td>
    <td class="num">{{ row.tex_sharpness }}</td><td class="num">{{ row.tex_seam }}</td>
    <td class="num">{{ row.tex_coverage }}</td><td class="num">{{ row.tex_fill }}</td>
    <td>{{ row.tex_ok }}</td>
    <td class="{{ 'pass' if row.qc == 'pass' else 'fail' }}">{{ row.qc }}</td>
  </tr>
  {% else %}
  <tr><td colspan="12">无质检数据（M5 未执行{% if n_fail %}——上游阶段失败{% endif %}）</td></tr>
  {% endfor %}
</table>
<p class="footnote">
  <b>表面覆盖率</b>＝网格表面点中能在参考点云中找到近邻的比例（几何一致性），
  与上一节的「贴图」比例无关；<b>中误差</b>＝网格到参考点云的 RMS 距离。
  {% if rmse_unit != 'mm' %}本批<b>无激光锚点</b>，坐标是 SfM 任意单位：
  中误差按<b>模型单位</b>给出（不是毫米），且不参与质量门判定；
  <b>请引用「相对中误差」</b>——即中误差占模型包围盒对角线的百分比，
  它与尺度无关，是同批/跨批可比的口径。
  {% else %}「相对中误差」＝ 中误差 / 包围盒对角线，供跨尺度比对；
  报告判门仍以毫米阈值为准。{% endif %}
</p>

<h2>五、交付包索引（M6 发布）</h2>
<table>
  <tr><th>文物编号</th><th>状态</th><th>交付模型数</th><th>实际格式</th>
      <th>缺失格式</th><th>纹理图集</th><th>纹理承载</th></tr>
  {% for row in publish_rows %}
  <tr><td>{{ row.item }}</td><td class="pass">{{ row.status }}</td>
      <td class="num">{{ row.n_models }}</td>
      <td>{{ row.formats }}</td>
      <td class="{{ 'warn' if row.missing != '—' else '' }}">{{ row.missing }}</td>
      <td>{{ row.atlas }}</td><td>{{ row.carrier }}</td></tr>
  {% else %}
  <tr><td colspan="7">无交付记录（M6 未执行{% if n_fail %}——上游阶段失败{% endif %}）</td></tr>
  {% endfor %}
</table>
<p class="footnote">
  <b>「实际格式」与「缺失格式」必须分开看</b>：前者取自交付目录里真实存在的文件，
  后者＝<code>publish.formats</code> 请求了但 M4 没导出的格式
  （多因 <code>texture.export_formats</code> 未勾选，属配置不一致而非运行故障）。
  <b>「纹理图集」</b>是照片纹理链路的核心产物（UV 图集 PNG），
  无论导出哪些模型格式都随交付包归档；<b>「纹理承载」</b>说明哪个模型真正引用了它——
  显示 <code>无</code> 时交付包里的图集是孤立文件（PLY 只能存顶点色，
  无法引用图像纹理），需要 OBJ/GLB 才能"打开就带颜色"。
</p>

<h2>六、多时相形变（X1，P2 指标入报表）</h2>
<table>
  <tr><th>文物编号</th><th>最大形变(mm)</th><th>变化区域占比</th><th>平均形变(mm)</th><th>核心点数</th></tr>
  {% for row in x1_rows %}
  <tr>
    <td>{{ row.item }}</td><td class="num">{{ row.max_change }}</td>
    <td class="num">{{ row.changed_ratio }}</td>
    <td class="num">{{ row.mean_change }}</td><td class="num">{{ row.n_corepoints }}</td>
  </tr>
  {% else %}
  <tr><td colspan="5">无多时相数据（X1 未执行 / 无参考期）</td></tr>
  {% endfor %}
</table>

<h2>七、自动诊断</h2>
{% if findings %}
<ul class="findings">
  {% for f in findings %}<li class="{{ f.level }}">{{ f.text }}</li>
  {% endfor %}
</ul>
{% else %}
<p class="meta">未发现指标层面的明显异常。</p>
{% endif %}

<h2>八、失败清单</h2>
<table>
  <tr><th>文物编号</th><th>阶段</th><th>异常类型</th><th>耗时(s)</th><th>异常信息</th></tr>
  {% for row in failure_rows %}
  <tr>
    <td>{{ row.item }}</td><td>{{ row.stage }}</td>
    <td>{{ row.error_type }}</td><td class="num">{{ row.elapsed }}</td>
    <td class="msg">{{ row.message }}</td>
  </tr>
  {% else %}
  <tr><td colspan="5">无失败（全部通过或未运行）</td></tr>
  {% endfor %}
</table>
{% if failure_hints %}
<div class="hint">
  <b>诊断建议</b>
  <ol>
  {% for h in failure_hints %}<li>{{ h }}</li>
  {% endfor %}
  </ol>
</div>
{% endif %}

<h2>九、阶段耗时图</h2>
{% if chart_b64 %}
<img src="data:image/png;base64,{{ chart_b64 }}" alt="阶段耗时">
{% else %}
<p class="meta">无图表（需 report.formats 含 png，且至少有一个阶段有 elapsed_s 记录）</p>
{% endif %}

<p class="meta">生成时间：{{ generated_at }}｜系统版本 h3d {{ version }}｜
  <i>本报告由系统自动生成，随交付包一并归档</i></p>
</body>
</html>
"""


def _now() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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

    items = sorted({str(key).split("/")[0] for key in manifest.stages})

    def get(item: str, stage_id: str) -> dict:
        """取某件某阶段的 manifest 条目（不存在返回空 dict，便于统一 .get()）。"""
        return manifest.stages.get(f"{item}/{stage_id}") or {}

    # ---------------- 一、阶段状态行（缺失阶段也要成行） ----------------
    stage_rows: list[dict] = []
    n_done = n_fail = n_pending = n_skipped = n_disabled = 0
    for item in items:
        item_failed = any(get(item, s).get("status") == "fail" for s in _STAGE_ORDER)
        for stage_id in _STAGE_ORDER:
            entry = get(item, stage_id)
            if not entry:
                # 缺失阶段：显式成行。原实现直接 continue，导致"未执行 2"
                # 在表里根本找不到对应两行（2026-10-18 实机）。
                n_pending += 1
                stage_rows.append({
                    "item": item, "stage": stage_id,
                    "status": "未执行", "cls": "skip",
                    "elapsed": "—",
                    "note": ("上游阶段失败，未运行" if item_failed
                             else "本次运行无该阶段记录"),
                })
                continue
            status = entry.get("status", "?")
            label, cls = _STATUS_CN.get(status, (status, ""))
            if status == "done":
                n_done += 1
            elif status == "fail":
                n_fail += 1
            elif status == "disabled":
                # 配置关闭：是用户的意图，**不计入分母**（下面 n_evaluated）
                n_disabled += 1
            elif status == "skipped":
                # 已启用但本次没跑：条件不满足 / 降级 / 续跑复用
                n_skipped += 1
                label = _SKIP_KIND_CN.get(entry.get("skip_kind") or "", label)
            else:
                n_pending += 1
            stage_rows.append({
                "item": item, "stage": stage_id,
                "status": label, "cls": cls,
                "elapsed": _num(entry.get("elapsed_s"), 2, default="—"),
                "note": _stage_note(entry),
            })
    # 分母 = 实际参与评估的阶段格数（件数 × 阶段数 − 配置关闭的格数）。
    # 「完成 7 · 未执行 1 → 部分完成」之所以荒谬，就是因为分母把用户主动
    # 关掉的 P1 也算了进去（2026-10-26 实机）。
    n_evaluated = max(0, len(items) * len(_STAGE_ORDER) - n_disabled)

    # ---------------- 二、几何与尺度 ----------------
    geom_rows: list[dict] = []
    no_metric_units = False
    for item in items:
        m1, m3, m5 = get(item, "M1"), get(item, "M3"), get(item, "M5")
        if not (m1 or m3):
            continue
        units_flag = m5.get("metric_units")
        if units_flag is False:
            no_metric_units = True
        bg = m1.get("background")
        if not isinstance(bg, dict):
            bg_text = "未启用"
        elif bg.get("bg_rollback"):
            bg_text = f"已回滚（剩 {_pct(bg.get('bg_kept_ratio'))}）"
        else:
            bg_text = (f"保留 {bg.get('bg_kept_clusters', '?')}"
                       f"/{bg.get('bg_clusters', '?')} 簇"
                       f"（{_pct(bg.get('bg_kept_ratio'))}）")
        geom_rows.append({
            "item": item,
            "source": _geom_source(m1),
            "bg": bg_text,
            "points_in": _num(m1.get("points_in"), 0, default="N/A"),
            "points_out": _num(m1.get("points_out"), 0, default="N/A"),
            "vertices": _num(m3.get("n_vertices"), 0, default="N/A"),
            "triangles": _num(m3.get("n_triangles"), 0, default="N/A"),
            "watertight": _yes(m3.get("watertight")),
            "n_comp": _num(m3.get("n_components"), 0, default="N/A"),
            "largest": _pct(m3.get("largest_component_ratio")),
            "bbox_diag": _num(m5.get("model_bbox_diag"), 3, default="N/A"),
            "units": ("N/A" if units_flag is None
                      else ("米制（激光锚定）" if units_flag else "SfM 任意单位")),
        })

    # ---------------- 三、纹理质量（M4） ----------------
    tex_rows: list[dict] = []
    for item in items:
        m4 = get(item, "M4")
        if not m4:
            continue
        fill = m4.get("island_fill_ratio")
        cov = m4.get("coverage_ratio")
        if isinstance(fill, (int, float)) and fill < 0.85:
            verdict, cls = "岛内偏空", "fail"
        elif isinstance(cov, (int, float)) and cov < 0.20:
            verdict, cls = "大面积未着色", "fail"
        elif fill is None:
            verdict, cls = "未评估", "skip"
        else:
            verdict, cls = "正常", "pass"
        tex_rows.append({
            "item": item,
            "size": _num(m4.get("texture_size"), 0, default="N/A"),
            "cameras": _num(m4.get("projected_cameras"), 0, default="N/A"),
            "coverage": _pct(cov),
            "utilization": _pct(m4.get("atlas_utilization")),
            "fill": _pct(fill),
            "luma": _num(m4.get("texture_mean_luma"), 1),
            "consistency": _num(m4.get("multiview_color_std"), 2),
            "flipped": _yes(m4.get("normal_flipped")),
            "pixel_scale": _num((m4.get("pixel_scale") or [None])[0], 4),
            "verdict": verdict, "cls": cls,
        })

    # ---------------- 四、M5 质检 ----------------
    qc_rows: list[dict] = []
    unit_flags: list[bool] = []
    #: 先定全表口径（批内混排时以「是否有件缺锚点」为准），再逐行按自身口径取值
    for item in items:
        m5 = get(item, "M5")
        if not m5:
            continue
        if m5.get("metric_units") is not None:
            unit_flags.append(bool(m5.get("metric_units")))
    metric_ok = bool(unit_flags) and all(unit_flags)
    for item in items:
        m5 = get(item, "M5")
        if not m5:
            continue
        row_metric = (metric_ok if m5.get("metric_units") is None
                      else bool(m5.get("metric_units")))
        # 有米制语义 → 展示毫米；否则展示模型单位原值（×1000 的 rmse_mm 是伪毫米，
        # 2026-10-26 起不再出现在报表里）
        if row_metric:
            rmse_text = _num(m5.get("rmse_mm"), 2)
        else:
            rmse_text = _num(m5.get("rmse_units"), 4)
            if metric_ok and rmse_text != "N/A":   # 全表按 mm 排，本行须标注
                rmse_text += " (模型单位)"
        qc_rows.append({
            "item": item,
            "coverage": _pct(m5.get("coverage")),
            "rmse": rmse_text,
            "rmse_rel": _pct(m5.get("rmse_rel")),
            "mps": _num(m5.get("mps_m"), 4),
            "boundary": _pct(m5.get("boundary_ratio")),
            "tex_sharpness": _num(m5.get("texture_sharpness"), 2),
            "tex_seam": _num(m5.get("texture_seam"), 2),
            "tex_coverage": _pct(m5.get("texture_coverage")),
            "tex_fill": _pct(m5.get("texture_fill_ratio")),
            "tex_ok": ("是" if m5.get("texture_ok")
                       else ("N/A" if m5.get("texture_sharpness") is None else "否")),
            "qc": "pass" if m5.get("status") == "done" else "fail",
        })
    rmse_unit = "mm" if metric_ok else "模型单位"
    mps_unit = "m" if metric_ok else "模型单位"

    # ---------------- 五、M6 交付 ----------------
    publish_rows: list[dict] = []
    for item in items:
        m6 = get(item, "M6")
        if m6 and m6.get("status") == "done":
            fmt_done = m6.get("formats_delivered") or []
            fmt_missing = m6.get("formats_missing") or []
            carrier = m6.get("texture_carrier")
            publish_rows.append({
                "item": item, "status": "已发布",
                "n_models": _num(m6.get("n_models"), 0, default="?"),
                "formats": "、".join(fmt_done) if fmt_done else "—",
                "missing": "、".join(fmt_missing) if fmt_missing else "—",
                "atlas": (m6.get("atlas") or "无"),
                "carrier": (carrier or "无"),
            })

    # ---------------- 六、X1 多时相 ----------------
    x1_rows: list[dict] = []
    for item in items:
        x1 = get(item, "X1")
        if x1 and x1.get("status") == "done":
            x1_rows.append({
                "item": item,
                "max_change": _num(x1.get("max_change_mm"), 2),
                "changed_ratio": _pct(x1.get("changed_ratio"), default="N/A"),
                "mean_change": _num(x1.get("mean_change_mm"), 2),
                "n_corepoints": _num(x1.get("n_corepoints"), 0, default="N/A"),
            })

    # ---------------- 八、失败清单 ----------------
    failure_rows = [
        {"item": f.get("item_id", "?"), "stage": f.get("stage", "?"),
         "error_type": f.get("error_type", "?"), "message": f.get("message", ""),
         "elapsed": _num(f.get("elapsed_s"), 2, default="—")}
        for f in (failures or [])
    ]
    failure_hints = _failure_hints(failures or [])

    chart_b64 = ""
    if chart_png and os.path.exists(chart_png):
        with open(chart_png, "rb") as fh:
            chart_b64 = base64.b64encode(fh.read()).decode("ascii")

    html = env.from_string(_TEMPLATE).render(
        project=project or "（未命名）",
        run_id=manifest.run_id or "N/A",
        config_sha=(manifest.config_sha256 or "N/A")[:16],
        n_items=len(items),
        n_stages=len(_STAGE_ORDER),
        n_done=n_done,
        n_fail=n_fail,
        n_pending=n_pending,
        n_skipped=n_skipped,
        n_disabled=n_disabled,
        n_evaluated=n_evaluated,
        no_metric_units=no_metric_units,
        rmse_unit=rmse_unit,
        mps_unit=mps_unit,
        stage_rows=stage_rows,
        geom_rows=geom_rows,
        tex_rows=tex_rows,
        qc_rows=qc_rows,
        publish_rows=publish_rows,
        failure_rows=failure_rows,
        failure_hints=failure_hints,
        findings=_findings(items, get),
        x1_rows=x1_rows,
        chart_b64=chart_b64,
        generated_at=_now(),
        version=__version__,
    )
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    log.info("HTML 报表：%s（%d 件，%d 阶段行）", out_path, len(items), len(stage_rows))
    return os.path.abspath(out_path)


__all__ = ["generate_html"]
