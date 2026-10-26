"""h3d.report.plot_gen : matplotlib 图表生成（M6 报表）。

设计依据：
    《软著方案》2.6 —— M6 report 输出「CSV + 图表 + HTML/PDF」；
    《教学版》18.3 —— 把配准精度等指标可视化，便于人工复核。

实现要点：
    1. 无头渲染：固定 ``Agg`` 后端，服务器 / 批处理环境无需显示器。
    2. 中文字体自适应：优先微软雅黑（msyh.ttc），回退黑体（simhei.ttf），
       并关闭 ``axes.unicode_minus`` 避免负号豆腐块。
    3. 图内数据均来自 ``RunManifest``，图表与 CSV 同源，保证报表一致性。
"""

from __future__ import annotations

import os
from typing import Any, Optional, Sequence

from h3d.core import ExportError, RunManifest, get_logger

log = get_logger("report.plot_gen")

# 候选中文字体路径（Windows 常见位置）
_CJK_FONTS = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/msyh.ttf",
)


def _plt():
    """惰性导入 matplotlib 并完成中文 / 无头配置（不污染全局导入）。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    registered = False
    for fp in _CJK_FONTS:
        if os.path.exists(fp):
            try:
                font_manager.fontManager.addfont(fp)
                registered = True
            except Exception:  # pragma: no cover - 字体加载失败不影响绘图
                continue
    if registered:
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _ensure_dir(path: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            raise ExportError(f"图表目录创建失败: {directory}") from exc


def plot_bar(
    labels: Sequence[str],
    values: Sequence[float],
    path: str,
    *,
    title: str = "",
    xlabel: str = "",
    ylabel: str = "",
) -> str:
    """通用柱状图原语：把 ``labels`` 对应的 ``values`` 渲染为柱状图 PNG。

    返回写出路径；数据为空时抛 :class:`ExportError`（避免生成无意义空图）。
    """
    if not labels or len(labels) != len(values):
        raise ExportError("柱状图数据为空或 labels/values 长度不一致")
    _ensure_dir(path)
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(range(len(labels)), list(values), color="#2a5a6b", alpha=0.85)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(list(labels), rotation=30, ha="right", fontsize=8)
    ax.set_ylabel(ylabel)
    ax.set_xlabel(xlabel)
    ax.set_title(title)
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    fig.tight_layout()
    try:
        fig.savefig(path, dpi=120)
    finally:
        plt.close(fig)
    log.info("写出图表：%s", path)
    return os.path.abspath(path)


#: 图表里的阶段顺序（与 HTML 报表一致：S1 在前，P1 紧跟 M4）
_STAGE_ORDER = ("S1", "M1", "M2", "M3", "M4", "P1", "M5", "M6", "X1", "X2")


def _human_seconds(v: float) -> str:
    """秒 -> 可读时长（>=120 s 附带分钟）。"""
    if v >= 120.0:
        return f"{v:.0f} s\n({v / 60.0:.1f} min)"
    if v >= 1.0:
        return f"{v:.1f} s"
    return f"{v * 1000.0:.0f} ms"


def plot_stage_timings(manifest: RunManifest, path: str) -> str:
    """各阶段平均耗时柱状图（按 ``item_id/stage`` 拆分后按阶段聚合）。

    数据为空（无任何阶段记录 elapsed_s，例如全部阶段失败/被跳过）时**不抛异常**，
    仅记警告并返回空串——报表是"交付附属物"，不应让整条批量流程因为"无内容可画"
    而中断（实机问题 E601：一键演示 S1 失败 → 无耗时数据 → 报表抛错 → 弹崩溃框）。
    调用方（:func:`h3d.report.generate`）据此跳过 PNG 产物。

    可读性（2026-10-18 实机）：S1 稠密重建动辄 1800 s，而 M2/M5 只有毫秒级，
    线性坐标下除了 S1 全挤成零高度——图上什么也读不出来。现改为：
    阶段按固定顺序排列、跨度超过 30 倍时自动切对数坐标、每根柱标注数值、
    失败阶段标红，避免"看不出哪一步慢/哪一步失败"。
    """
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    failed: set[str] = set()
    for stage_key, entry in manifest.stages.items():
        _, _, stage = str(stage_key).partition("/")
        stage = stage or str(stage_key)
        if entry.get("status") == "fail":
            failed.add(stage)
        elapsed = entry.get("elapsed_s")
        if elapsed is None:
            continue
        try:
            value = float(elapsed)
        except (TypeError, ValueError):
            continue
        sums[stage] = sums.get(stage, 0.0) + value
        counts[stage] = counts.get(stage, 0) + 1
    if not sums:
        log.warning("阶段耗时图为空（manifest 无 elapsed_s 记录），跳过绘图：%s", path)
        return ""

    order = {s: i for i, s in enumerate(_STAGE_ORDER)}
    labels = sorted(sums, key=lambda s: (order.get(s, len(order)), s))
    values = [sums[s] / max(1, counts[s]) for s in labels]
    colors = ["#c62828" if s in failed else "#2a5a6b" for s in labels]

    _ensure_dir(path)
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8, 4.2))
    positions = list(range(len(labels)))
    ax.bar(positions, values, color=colors, alpha=0.88)

    positive = [v for v in values if v > 0]
    use_log = (len(positive) == len(values) and len(positive) > 1
               and max(positive) / min(positive) > 30.0)
    if use_log:
        ax.set_yscale("log")
        ax.set_ylim(bottom=max(min(positive) / 5.0, 1e-3),
                    top=max(positive) * 5.0)  # 顶部留白，避免数值标注被裁

    for x, v in zip(positions, values):
        ax.annotate(_human_seconds(v), (x, v),
                    textcoords="offset points", xytext=(0, 4),
                    ha="center", va="bottom", fontsize=7.5, color="#333")

    ax.set_xticks(positions)
    ax.set_xticklabels(labels, rotation=0, fontsize=9)
    ax.set_xlabel("阶段")
    ax.set_ylabel("平均耗时 s" + ("（对数坐标）" if use_log else ""))
    ax.set_title("各阶段平均耗时" + ("（对数坐标，红色＝有失败）" if use_log
                                     else "（红色＝有失败）"))
    ax.grid(axis="y", linestyle="--", alpha=0.35)
    if use_log:
        ax.grid(axis="y", which="minor", linestyle=":", alpha=0.18)
    fig.tight_layout()
    try:
        fig.savefig(path, dpi=120)
    finally:
        plt.close(fig)
    log.info("写出图表：%s", path)
    return os.path.abspath(path)


def plot_metric_series(
    manifest: RunManifest,
    metric: str,
    path: str,
    *,
    title: str = "",
) -> str:
    """把某关键指标（如 fitness / inlier_rmse）按「文物编号」绘制散点 / 折线。

    指标取自 manifest 阶段条目中的同名键，缺项自动跳过（《软著方案》2.6
    「单项缺失 -> 留空」）。
    """
    points: list[tuple[str, float]] = []
    for stage_key, entry in manifest.stages.items():
        if metric not in entry:
            continue
        item_id, _, _ = str(stage_key).partition("/")
        points.append((item_id or str(stage_key), float(entry[metric])))
    if not points:
        raise ExportError(f"manifest 中无指标 {metric!r}，无法绘图")

    _ensure_dir(path)
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8, 4))
    xs = range(len(points))
    ys = [v for _, v in points]
    ax.plot(xs, ys, marker="o", linestyle="-", color="#c0392b", markersize=4)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([k for k, _ in points], rotation=30, ha="right", fontsize=8)
    ax.set_ylabel(metric)
    ax.set_xlabel("文物编号")
    ax.set_title(title or f"指标 {metric} 分布")
    ax.grid(linestyle="--", alpha=0.35)
    fig.tight_layout()
    try:
        fig.savefig(path, dpi=120)
    finally:
        plt.close(fig)
    log.info("写出图表：%s", path)
    return os.path.abspath(path)


__all__ = ["plot_bar", "plot_stage_timings", "plot_metric_series"]
