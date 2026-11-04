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
#: 2026-10-26：新增「相对中误差(%对角线)」列。原表只有「中误差(mm)」，而纯照片
#: 场景没有米制语义，那一列填的是「模型单位 ×1000」——数字自洽性都不成立
#: （实机 41.16「mm」 vs 对角线 6.06 单位）。现在：
#:   · 有激光锚点 → 中误差(mm) 填真实毫米，相对值并列供交叉核对；
#:   · 无激光锚点 → 中误差(mm) 填 N/A，只给相对值（唯一可对外引用的口径）。
#: 2026-10-26 二次修订（真机 r15 反馈）：新增「中误差口径」列。
#: 上一版只解决「无锚点→N/A」，漏了另一半：**有锚点但判不了门**。
#: 实机 mps=23.6 mm（> 5 mm 阈值）→ ``rmse_reliable=False`` → 毫米门根本没启用，
#: 报表却仍印「38.26 mm」并把脚注写成「判门仍以毫米阈值为准」。读者（答辩委员）
#: 只会记住「38.26 mm」和「pass」。这一列把「这个数字到底参不参与判门」写在行内。
ACCURACY_HEADERS = [
    ("object", "文物编号"),
    ("reg_error_mm", "中误差(mm)"),
    ("rmse_scope", "中误差口径"),
    ("reg_error_rel", "相对中误差(%对角线)"),
    ("coverage", "覆盖率"),
    ("boundary_ratio", "边界边比例"),
    ("mps_m", "平均点间距(m)"),
    ("texture_sharpness", "纹理清晰度"),
    ("texture_seam", "纹理接缝"),
    ("texture_coverage", "纹理覆盖率"),
    ("texture_ok", "纹理达标"),
    ("anomaly_ratio", "异常面积(岛内)"),
    ("anomaly_regions", "图斑数"),
    ("anomaly_sigma_median", "纹理尺度σ_L(Lab)"),
    ("anomaly_paint_ratio", "残存彩绘占比"),
    ("anomaly_regions_paint", "彩绘区图斑数"),
    # X1 多时相形变（可选阶段）。2026-11-01 起由本表直接写，不再事后追加——
    # X1 已移到 M5 之前，那时 accuracy.csv 还不存在，追加等于不写。
    ("x1_max_change_mm", "X1最大形变(mm)"),
    ("x1_changed_ratio", "X1变化占比"),
    ("status", "判定"),
]


#: ``data_source`` 声明 → 米制语义。``auto`` 必须与旧实现**逐字等价**，否则
#: 老项目重跑会出现「同一份数据换了数字」。
_DECLARED_ALIASES = {"auto": "auto", "laser": "laser",
                     "photogrammetry": "photogrammetry"}

#: 判出「00_raw 本身就是照片重建产物」时 M1 落的 dense_source 取值。
#: 声明 laser 撞上它 = 声明与证据冲突（见 :func:`resolve_metric_units`）。
EVIDENCE_RAW_IS_SFM = "raw_looks_like_sfm"


def resolve_metric_units(declared: str, has_raw: bool,
                         dense_source: Optional[str] = None
                         ) -> tuple[bool, str, bool]:
    """把「数据来源声明 + 证据」解析成米制语义。

    返回 ``(metric_units, reason, conflict)``：

    * ``metric_units`` — 本件坐标能不能当米用（决定是否进 5 mm 毫米门、
      报表印 mm 还是「模型单位」）；
    * ``reason`` — 落进 metrics 的判据名，报表据此写因果，不用字符串猜；
    * ``conflict`` — 声明与证据/输入矛盾（必须 WARN，且**已经**按保守侧降级）。

    判据顺序（2026-11-04）：

    1. 未知声明 → 按 ``auto``（schema 已挡非法值，这里只防内部调用）。
    2. ``auto`` → 旧行为：``has_raw``。
    3. ``photogrammetry`` → **一律不主张米制**，与 00_raw 有没有文件无关。
       这是本函数存在的理由：用户知道自己放的是别的软件重建件。
    4. ``laser`` → 要两个条件同时成立：
       a. 真的有 00_raw（声明激光件却没输入 = 自相矛盾）；
       b. M1 没有把它判成照片重建（``dense_source != raw_looks_like_sfm``）。

    **证据压过声明**是硬红线。若声明能压过证据，用户勾一下「激光件」就能把
    自证数据包装成米制结果——那比没有这个功能更糟。
    """
    key = _DECLARED_ALIASES.get(str(declared or "").strip().lower(), "auto")
    src = str(dense_source or "")

    if key == "photogrammetry":
        return False, "declared_photogrammetry", False

    if key == "laser":
        if not has_raw:
            return False, "declared_laser_without_raw", True
        if src == EVIDENCE_RAW_IS_SFM:
            return False, "declared_laser_conflicts_with_evidence", True
        return True, "declared_laser", False

    # auto
    if has_raw:
        return True, "auto_with_raw", False
    return False, "auto_without_raw", False


def scope_text(metric_units: bool, units_plausible: bool, rmse_reliable: bool,
               reason: str = "") -> str:
    """中误差口径的**唯一**文字结论（报表 / CSV / 论文表共用）。

    「无量纲」的**原因**必须分开写（2026-11-04）：默认那句「（SfM 任意单位）」
    说的是「几何来自 SfM」，而用户声明为照片重建件时几何其实来自 ``00_raw``，
    只是那个 ``00_raw`` 也不是米制。两种情况下读者该做的事完全不同——

    * SfM 任意单位 → 该去跑 SfM 稠密 / 补尺度控制点；
    * 用户声明照片重建件 → 该换激光件（跑多少次 SfM 都不会变米制）。

    写成同一句话，等于把「你少跑了一步」和「你本来就没有这个东西」混为一谈。
    """
    if not metric_units:
        if reason == "declared_photogrammetry":
            return "无量纲（用户声明照片重建件）"
        return "无量纲（SfM 任意单位）"
    if not units_plausible:
        return "不判门（单位存疑）"
    if not rmse_reliable:
        return "不判门（参考稀疏）"
    return "实测（参与 5 mm 判门）"


def check_quality(metrics: dict[str, Any], cfg: QualityConfig) -> bool:
    """质量门判定：覆盖率 >= 阈值；中误差门仅在「有米制语义且参考足够密」时启用。

    参数
    ----
    metrics: run() 输出的指标字典（coverage / rmse_mm / rmse_reliable）。
    cfg:     QualityConfig（阈值配置）。

    返回
    ----
    True 达标；False 不达标（调用方据此抛 QualityGateError E500 或标记 fail）。

    中误差门的两个前提（缺一不判）：

    1. **米制语义**（``metric_units``）：场景② 纯照片的坐标是 SfM 任意单位，
       ``rmse_mm`` 只是「模型单位 ×1000」，拿它去比 5 mm 没有意义
       （2026-10-25 实机：rmse 报 41.16「mm」，而模型对角线只有 6.06）。
    2. **参考足够密**（``rmse_reliable``）：表面采样 RMSE 受参考点云密度主导，
       稀疏扫描的 MPS 即达几十 mm；无条件判 RMSE 会把「扫描稀疏」误判成
       「模型不合格」（《设计书》§36.4 的工程化扩展）。
    """
    if metrics["coverage"] < cfg.coverage_threshold:
        return False
    if not metrics.get("metric_units", True):
        return True          # 无度量基准：只判覆盖率
    # 单位存疑（声称米制却算出 6 米级的"中小型文物"）→ 毫米门同样不成立。
    # 与 rmse_reliable 是两把独立的锁：前者管"数字是不是毫米"，后者管"参考够不够密"。
    if not metrics.get("units_plausible", True):
        return True
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
    metric_units = bool(metrics.get("metric_units", True))
    rmse_mm = metrics["rmse_mm"]
    rmse_rel = metrics.get("rmse_rel")
    # 口径三态（见 ACCURACY_HEADERS 注释）：无量纲 / 不判门（参考稀疏 / 单位存疑）
    # / 实测判门。老 manifest 没有 rmse_scope 时按两个布尔现推。
    scope = metrics.get("rmse_scope")
    if not scope:
        scope = scope_text(metric_units,
                           bool(metrics.get("units_plausible", True)),
                           bool(metrics.get("rmse_reliable")),
                           str(metrics.get("metric_units_reason") or ""))
    record = {
        "item_id": item_id,
        "coverage": round(metrics["coverage"], 4),
        # rmse_mm 只在有米制语义时才是毫米；纯照片下它等于「模型单位 ×1000」，
        # 报表一律不展示，只在 qc_metrics.json 里留档以便追溯旧口径。
        "rmse_mm": (round(rmse_mm, 4)
                    if (metric_units and not math.isnan(rmse_mm)) else "N/A"),
        "rmse_scope": scope,
        "rmse_reliable": bool(metrics.get("rmse_reliable")),
        "units_plausible": bool(metrics.get("units_plausible", True)),
        "rmse_units": (round(metrics["rmse_units"], 6)
                       if metrics.get("rmse_units") is not None
                       and not math.isnan(metrics["rmse_units"]) else "N/A"),
        "rmse_rel": (round(float(rmse_rel), 6)
                     if isinstance(rmse_rel, (int, float))
                     and not math.isnan(float(rmse_rel)) else "N/A"),
        "rmse_unit": metrics.get("rmse_unit") or ("mm" if metric_units
                                                  else "模型单位"),
        "metric_units": metric_units,
        # 「为什么是/不是米制」必须留档（2026-11-04）。判门失败时 manifest 不写，
        # qc_metrics.json 是**唯一**能回答「这个 N/A 是没测还是按声明不主张米制」
        # 的地方。只落 metric_units 布尔的话，auto 与 photogrammetry 事后无法区分。
        "data_source_declared": metrics.get("data_source_declared"),
        "metric_units_reason": metrics.get("metric_units_reason"),
        "declared_source_conflict": bool(
            metrics.get("declared_source_conflict", False)),
        "model_bbox_diag": metrics.get("model_bbox_diag"),
        "boundary_ratio": round(metrics["boundary_ratio"], 4),
        "mps_m": round(metrics["mps_m"], 6),
        "texture_sharpness": metrics.get("texture_sharpness"),
        "texture_seam": metrics.get("texture_seam"),
        "texture_coverage": metrics.get("texture_coverage"),
        "texture_ok": metrics.get("texture_ok"),
        # 纹理重叠度（设计书 §17.3）也进审计留档：判门失败时 manifest 不写，
        # qc_metrics.json 是唯一能回答「这件是不是单视角硬投影」的地方。
        "overlap": metrics.get("overlap"),
        "texture_multi_view_ratio": metrics.get("texture_multi_view_ratio"),
        # X3 单期异常筛查（参考信息；缺键时保持 None，报表印 N/A——不能印 0）
        "anomaly_ratio": metrics.get("anomaly_ratio"),
        "anomaly_regions": metrics.get("anomaly_regions"),
        "anomaly_sigma_median": metrics.get("anomaly_sigma_median"),
        "anomaly_paint_ratio": metrics.get("anomaly_paint_ratio"),
        "anomaly_regions_paint": metrics.get("anomaly_regions_paint"),
        # X1 多时相形变（参考信息；缺键时 None → 报表印 N/A，不能印 0）
        "x1_max_change_mm": metrics.get("x1_max_change_mm"),
        "x1_changed_ratio": metrics.get("x1_changed_ratio"),
        "x1_calibrated": metrics.get("x1_calibrated"),
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
    rel = record.get("rmse_rel")
    rel_text = (f"{float(rel) * 100:.2f}%" if isinstance(rel, (int, float))
                else "N/A")
    values = [
        record["item_id"],
        record["rmse_mm"],
        record.get("rmse_scope") or "N/A",
        rel_text,
        f"{record['coverage']:.1%}",
        f"{record['boundary_ratio']:.1%}",
        record["mps_m"],
        _fmt_tex(record.get("texture_sharpness")),
        _fmt_tex(record.get("texture_seam")),
        _fmt_tex(record.get("texture_coverage")),
        "Y" if record.get("texture_ok") else ("N/A" if record.get("texture_sharpness") is None else "N"),
        # 未筛查就得看见「未筛查」：0.00% 会被读成「查过了、很干净」。
        (f"{float(record['anomaly_ratio']) * 100:.2f}%"
         if isinstance(record.get("anomaly_ratio"), (int, float)) else "未筛查"),
        record.get("anomaly_regions") if record.get("anomaly_regions") is not None
        else "未筛查",
        (record["anomaly_sigma_median"]
         if record.get("anomaly_sigma_median") is not None else "未筛查"),
        (f"{float(record['anomaly_paint_ratio']) * 100:.2f}%"
         if isinstance(record.get("anomaly_paint_ratio"), (int, float))
         else "未筛查"),
        (record.get("anomaly_regions_paint")
         if record.get("anomaly_regions_paint") is not None else "未筛查"),
        _fmt_x1(record.get("x1_max_change_mm"), record.get("x1_calibrated")),
        _fmt_x1_ratio(record.get("x1_changed_ratio"), record.get("x1_calibrated")),
        record["status"],
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        writer.writerow(values)


def _fmt_tex(v):
    """纹理指标格式化（None=未评估显示 N/A）。"""
    if v is None:
        return "N/A"
    return round(float(v), 4)


def _fmt_x1(v, calibrated) -> str:
    """X1 形变量（mm）。

    三种「不给数字」的理由必须分开印，否则读者会把它们当成同一件事：
    * ``未标定`` —— 尺度没标定过，数字不可引用（ADR-0005/0006 口径）；
    * ``N/A``    —— X1 没跑（可选阶段关闭 / 两期不同源被闸门挡下）；
    * 数字       —— 可引用。
    """
    if calibrated is False:
        return "未标定"
    if not isinstance(v, (int, float)) or math.isnan(float(v)):
        return "N/A"
    return f"{float(v):.2f}"


def _fmt_x1_ratio(v, calibrated) -> str:
    if calibrated is False:
        return "未标定"
    if not isinstance(v, (int, float)) or math.isnan(float(v)):
        return "N/A"
    return f"{float(v) * 100:.1f}%"


__all__ = ["ACCURACY_HEADERS", "check_quality", "build_report"]
