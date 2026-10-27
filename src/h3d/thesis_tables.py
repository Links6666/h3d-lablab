"""h3d.thesis_tables : 论文实验表格导出（P2 定稿）。

把 M5 质检 + X1 形变 + X2 色差指标汇总为论文表格式 CSV（中文表头、
一行一件文物，可直接粘贴进论文实验章）。

对外：export_thesis_table(task_root, item_ids=None) -> CSV 路径。
"""

from __future__ import annotations

import csv
import json
import os
from typing import List, Optional

from h3d.core import get_logger

log = get_logger("thesis_tables")

THESIS_HEADERS = [
    "文物编号", "S1注册率", "M5覆盖率", "M5中误差(mm)", "M5中误差口径",
    "M5相对中误差(%对角线)", "纹理清晰度",
    "纹理接缝", "X1最大形变(mm)", "X1变化占比", "X2平均ΔE", "判定",
]


def _locate_manifest(task_root: str) -> Optional[str]:
    """在项目根下定位 run_manifest.json（兼容 GUI 与 CLI 两种落盘位置）。

    - GUI 生成的配置把 manifest 放在 ``<root>/output/run_manifest.json``；
    - CLI ``h3d init`` 的规范布局放在 ``<root>/90_meta/run_manifest.json``。

    历史上这里只认 ``90_meta``，导致 GUI 项目在 M6 阶段总是打印
    「无 manifest，无法导出论文表格」。现在按优先级依次探测。
    """
    for rel in (os.path.join("output", "run_manifest.json"),
                os.path.join("90_meta", "run_manifest.json"),
                "run_manifest.json"):
        cand = os.path.join(task_root, rel)
        if os.path.exists(cand):
            return cand
    return None


def export_thesis_table(task_root: str, item_ids: Optional[List[str]] = None,
                        out_path: Optional[str] = None) -> str:
    """汇总 run_manifest.json 各 stage 指标 → 论文表格 CSV。

    返回 CSV 路径（无 manifest 时返回空串，不抛异常）。
    """
    manifest_path = _locate_manifest(task_root)
    if not manifest_path:
        log.warning("无 manifest（%s 下未找到 output/ 或 90_meta/ 的 "
                    "run_manifest.json），无法导出论文表格", task_root)
        return ""
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    stages = manifest.get("stages", {})
    items = item_ids or sorted({k.split("/")[0] for k in stages})
    rows = []
    for item in items:
        s1 = stages.get(f"{item}/S1", {})
        m5 = stages.get(f"{item}/M5", {})
        x1 = stages.get(f"{item}/X1", {})
        x2 = stages.get(f"{item}/X2", {})
        reg = s1.get("n_registered", 0)
        nph = s1.get("n_photos", 0)
        # 中误差口径（2026-10-26）：无激光锚点时 rmse_mm 是「模型单位×1000」，
        # 不是毫米——论文表格里直接印出来就是错误数据。此时 mm 列填 N/A，
        # 只给相对中误差（中误差 / 包围盒对角线），后者与尺度无关、可跨批比较。
        metric_units = bool(m5.get("metric_units", True)) if m5 else True
        rows.append([
            item,
            f"{reg / nph * 100:.0f}%" if nph else "N/A",
            f"{m5.get('coverage', 0.0) * 100:.1f}%" if m5 else "N/A",
            _fmt(m5.get("rmse_mm")) if (m5 and metric_units) else "N/A",
            _rmse_scope(m5, metric_units),
            _fmt_pct(m5.get("rmse_rel")),
            _fmt(m5.get("texture_sharpness")),
            _fmt(m5.get("texture_seam")),
            _fmt_x1(x1.get("max_change_mm")),
            f"{x1.get('changed_ratio', 0.0) * 100:.1f}%" if x1 else "N/A",
            _fmt(x2.get("mean_de")),
            "通过" if m5.get("status") == "done" else ("N/A" if not m5 else "未通过"),
        ])
    out = out_path or os.path.join(task_root, "70_report",
                                   "thesis_experiment_table.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(THESIS_HEADERS)
        w.writerows(rows)
    log.info("论文实验表格导出：%s（%d 行）", out, len(rows))
    return out


def _fmt(v):
    return "N/A" if v is None else f"{float(v):.2f}"


def _rmse_scope(m5: dict, metric_units: bool) -> str:
    """中误差口径（三态）：这个数字到底能不能当"实测精度"引用。

    论文表格里「M5中误差(mm)」是一个会被直接搬进正文的数字。2026-10-26 真机
    出现过 ``38.26`` + 「通过」的组合——因为参考点云 MPS 23.6 mm 远粗于 5 mm
    阈值，毫米门根本没启用，38.26 只是「网格到自身降采样点云」的重采样损失。
    只印数字、不印口径，等于把「未判定」读成「已达标」。

    取值：实测（参与 5 mm 判门）/ 不判门（参考稀疏）/ 不判门（单位存疑）/
    无量纲（SfM 任意单位）。老 manifest 没有 ``rmse_scope`` 时按布尔现推。
    """
    if not m5:
        return "N/A"
    scope = m5.get("rmse_scope")
    if scope:
        return str(scope)
    if not metric_units:
        return "无量纲（SfM 任意单位）"
    plausible = m5.get("units_plausible")
    if plausible is None:
        # 旧 manifest 没这个键：按包围盒现推（与报表同一判据、同一量级区间）
        diag = m5.get("model_bbox_diag")
        if isinstance(diag, (int, float)) and diag > 0:
            plausible = 0.02 <= float(diag) <= 3.0
    if plausible is False:
        return "不判门（单位存疑）"
    return ("实测（参与 5 mm 判门）" if m5.get("rmse_reliable")
            else "不判门（参考稀疏）")


def _fmt_pct(v):
    """比例 -> 百分数文本（None 显示 N/A）。"""
    return "N/A" if v is None else f"{float(v) * 100:.2f}%"


def _fmt_x1(v):
    if v is None:
        return "N/A"
    fv = float(v)
    return "N/A" if fv != fv else f"{fv:.2f}"


__all__ = ["export_thesis_table", "THESIS_HEADERS"]
