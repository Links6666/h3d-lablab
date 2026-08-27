"""h3d.quality.gate : 质量门判定与质检报告生成（M5 质检）。

设计依据：
    《设计书》§36.2 —— ``ok = (coverage >= threshold and rmse <= threshold_mm)``，
        不达标状态 fail，阻断交付并提示参数；
    《设计书》§6.3 —— 模型表面覆盖率 >= 80%，特征点中误差 <= 5 mm（WW/T 0115/0117）；
    《教学版》18.3 —— 精度报告 accuracy.csv（object / reg_error_mm / hausdorff_mm /
        status），Excel 可直接打开。

实现要点：
    1. :func:`check_quality` 纯判定函数：覆盖率达到阈值且中误差不超过限值即达标。
    2. :func:`build_report` 同时产出两种报告：
       - ``qc_metrics.json``（60_qc 质检中间量，机器可读，失败也有据可查）；
       - ``accuracy.csv``（70_report 质检报告行，utf-8-sig 兼容 Excel）。
    3. 缺参考（rmse 为 NaN）时跳过中误差判定，仅以覆盖率判门
       （《设计书》§36.4「缺参考靶标 -> 仅输出覆盖，中误差记为 N/A」）。
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Optional

from h3d.core import QualityConfig, get_logger

log = get_logger("quality.gate")

# accuracy.csv 表头（教学版 18.3 扩展：含覆盖率 / 边界比 / 判定）
ACCURACY_HEADERS = [
    ("object", "文物编号"),
    ("reg_error_mm", "中误差(mm)"),
    ("coverage", "覆盖率"),
    ("boundary_ratio", "边界边比例"),
    ("mps_m", "平均点间距(m)"),
    ("status", "判定"),
]


def check_quality(metrics: dict[str, Any], cfg: QualityConfig) -> bool:
    """质量门判定：覆盖率 >= 阈值；中误差门仅在参考足够密时启用。

    参数
    ----
    metrics: run() 输出的指标字典（coverage / rmse_mm / rmse_reliable）。
    cfg:     QualityConfig（阈值配置）。

    返回
    ----
    True 达标；False 不达标（调用方据此抛 QualityGateError E500 或标记 fail）。

    说明：表面采样 RMSE 受参考点云密度主导（稀疏扫描的 MPS 即达几十 mm），
    若无条件判 RMSE 会把「扫描稀疏」误判为「模型不合格」。故仅当参考点云
    平均点间距 MPS <= 中误差阈值（能分辨阈值量级偏差）时启用 RMSE 门，
    否则中误差标记 N/A、仅以覆盖率判门（《设计书》§36.4「缺参考靶标 ->
    仅输出覆盖，中误差记为 N/A」的工程化扩展）。
    """
    if metrics["coverage"] < cfg.coverage_threshold:
        return False
    if metrics.get("rmse_reliable") and not math.isnan(metrics.get("rmse_mm", math.nan)) \
            and metrics["rmse_mm"] > cfg.rmse_threshold_mm:
        return False
    return True


def build_report(
    item_id: str,
    metrics: dict[str, Any],
    cfg: QualityConfig,
    qc_dir: str,
    report_dir: str,
) -> list[str]:
    """生成质检报告（60_qc 中间量 JSON + 70_report accuracy.csv）。

    参数
    ----
    item_id:    文物编号。
    metrics:    run() 输出的指标字典。
    cfg:        QualityConfig（阈值配置，写入报告供审计）。
    qc_dir:     质检中间量目录（设计书目录规范 60_qc/<item>）。
    report_dir: 质检报告目录（设计书目录规范 70_report/<item>）。

    返回
    ----
    生成的文件绝对路径列表。
    """
    ok = check_quality(metrics, cfg)
    record = {
        "item_id": item_id,
        "coverage": round(metrics["coverage"], 4),
        "rmse_mm": round(metrics["rmse_mm"], 4) if not math.isnan(metrics["rmse_mm"]) else "N/A",
        "boundary_ratio": round(metrics["boundary_ratio"], 4),
        "mps_m": round(metrics["mps_m"], 6),
        "thresholds": {
            "coverage_threshold": cfg.coverage_threshold,
            "rmse_threshold_mm": cfg.rmse_threshold_mm,
            "coverage_k_mps": cfg.coverage_k_mps,
        },
        "ok": bool(ok),
        "status": "pass" if ok else "fail",
    }

    for d in (qc_dir, report_dir):
        os.makedirs(d, exist_ok=True)

    # 60_qc：机器可读中间量（失败也有据可查）
    json_path = os.path.join(qc_dir, "qc_metrics.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)

    # 70_report：accuracy.csv（utf-8-sig 兼容 Excel）
    csv_path = os.path.join(report_dir, "accuracy.csv")
    _write_accuracy_csv(csv_path, record)

    log.info("质检报告生成：%s（status=%s）", item_id, record["status"])
    return [os.path.abspath(json_path), os.path.abspath(csv_path)]


def _write_accuracy_csv(path: str, record: dict[str, Any]) -> None:
    """把单条质检记录写为 accuracy.csv（utf-8-sig，Excel 可直接打开）。"""
    import csv

    headers = [h for _, h in ACCURACY_HEADERS]
    values = [
        record["item_id"],
        record["rmse_mm"],
        f"{record['coverage']:.1%}",
        f"{record['boundary_ratio']:.1%}",
        record["mps_m"],
        record["status"],
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        writer.writerow(values)


__all__ = ["ACCURACY_HEADERS", "check_quality", "build_report"]
