"""h3d.runner : 任务调度（M0 编排 + L3 编排层）—— 批量、失败隔离、断点续跑。

设计依据：
    《设计书》31 —— M0 命令与编排统一调度内核；
    《软著方案》2.7 —— 任务发现 / 批量调度 / 断点续跑 / 失败隔离 / run_manifest 溯源；
    《教学版》19 —— M6 批量调度（续跑 + 隔离）。

本包提供「阶段注册表 + 编排内核」：业务子包通过 :func:`register_stage` 装饰器把
阶段函数挂到 :data:`STAGE_REGISTRY`，:func:`run_batch` 交给
:class:`~h3d.runner.batch_processor.BatchProcessor` 按注册顺序驱动整个队列。

主线阶段（已接入的 M1 预处理 / M2 配准 / M3 网格重建 / M4 纹理 / M5 质检 / M6 发布）
在本模块内预注册，构成完整主线闭环；后续增强模块以同样的装饰器无缝挂入。

典型用法::

    from h3d.core import load
    from h3d.runner import run_batch

    cfg = load("configs/example.yaml")
    result = run_batch(cfg)          # 自动发现 00_raw 下的造像并批量建模
    print(result.summary())
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from typing import Callable, Optional

from h3d.core import get_logger

from .task import Task, TaskStatus, StageResult
from .batch_processor import BatchProcessor, BatchResult, discover_items

log = get_logger("runner")

# 阶段注册表（保持注册顺序 = 执行顺序，见《设计书》31「按序调用业务模块」）
STAGE_REGISTRY: "OrderedDict[str, Callable[[Task, object], StageResult]]" = OrderedDict()


def register_stage(stage_id: str):
    """阶段注册装饰器：把业务阶段函数挂入编排内核。

    用法::

        @register_stage("M3")
        def _stage_reconstruct(task, cfg):
            ...
            return StageResult("M3", TaskStatus.DONE, outputs, metrics)
    """

    def decorator(fn: Callable[[Task, object], StageResult]):
        STAGE_REGISTRY[stage_id] = fn
        return fn

    return decorator


# ---------------------------------------------------------------------------
# 主线阶段（M1 / M2 已接入；M3~M6 待业务子包实现后按同样方式挂入）
# ---------------------------------------------------------------------------
@register_stage("M1")
def _stage_preprocess(task: Task, cfg) -> StageResult:
    """M1 预处理：对每个采集站点做降采样 / 去噪 / 法线，输出到 ``10_clean/``。"""
    from h3d.io import load_point_cloud, save_point_cloud
    from h3d.preprocess import run as preprocess_run

    out_dir = os.path.join(task.root, "10_clean", task.item_id)
    os.makedirs(out_dir, exist_ok=True)
    outputs: list[str] = []
    points_in = points_out = 0
    for i, raw in enumerate(task.raw_paths, start=1):
        pcd = load_point_cloud(raw)
        points_in += len(pcd.points)
        clean = preprocess_run(pcd, cfg.preprocess)
        points_out += len(clean.points)
        out = os.path.join(out_dir, f"{task.item_id}_s{i:02d}.ply")
        save_point_cloud(clean, out)
        outputs.append(out)
    metrics = {
        "n_scans": len(task.raw_paths),
        "points_in": points_in,
        "points_out": points_out,
    }
    log.info("M1 完成：%s（%d 站，%d -> %d 点）",
             task.item_id, metrics["n_scans"], points_in, points_out)
    return StageResult("M1", TaskStatus.DONE, outputs, metrics)


@register_stage("M2")
def _stage_register(task: Task, cfg) -> StageResult:
    """M2 配准：多站扫描配准到统一坐标系，输出到 ``20_registered/``。

    单站（< 2 站）无需配准，标记 ``registered=False`` 正常结束（《教学版》16 两段式
    仅对多站有意义）。多站时以首站为参考，逐站做 FPFH+RANSAC+ICP 配准。
    """
    from h3d.io import load_point_cloud, save_point_cloud
    from h3d.register import run as register_run

    cleaned = task.outputs_of("M1") or task.raw_paths
    out_dir = os.path.join(task.root, "20_registered", task.item_id)
    os.makedirs(out_dir, exist_ok=True)
    if len(cleaned) < 2:
        return StageResult("M2", TaskStatus.DONE, [],
                           {"n_scans": len(cleaned), "registered": False})

    ref = load_point_cloud(cleaned[0])
    outputs: list[str] = []
    metrics: dict = {"n_scans": len(cleaned), "registered": True}
    # 参考站（s01）也以恒等变换落盘，保证 20_registered 是完整的多站配准结果
    # （M3 稠密重建将合并 20_registered 下全部站点）
    ref_out = os.path.join(out_dir, f"{task.item_id}_s01.ply")
    save_point_cloud(ref, ref_out)
    outputs.append(ref_out)
    for i, path in enumerate(cleaned[1:], start=2):
        src = load_point_cloud(path)
        res = register_run(src, ref, cfg.register)
        src.transform(res["transformation"])
        out = os.path.join(out_dir, f"{task.item_id}_s{i:02d}.ply")
        save_point_cloud(src, out)
        outputs.append(out)
        # 记录每站配准指标（末站写入顶层，供报表一键取用）
        metrics[f"fitness_s{i:02d}"] = round(float(res["fitness"]), 4)
        metrics[f"rmse_s{i:02d}"] = round(float(res["inlier_rmse"]), 6)
        metrics["fitness"] = metrics[f"fitness_s{i:02d}"]
        metrics["inlier_rmse"] = metrics[f"rmse_s{i:02d}"]
    log.info("M2 完成：%s（%d 站配准，末站 fitness=%.3f）",
             task.item_id, metrics["n_scans"] - 1, metrics["fitness"])
    return StageResult("M2", TaskStatus.DONE, outputs, metrics)


@register_stage("M3")
def _stage_mesh(task: Task, cfg) -> StageResult:
    """M3 稠密重建：合并配准点云 → 网格重建 → 输出到 ``30_dense/``。

    输入优先取 M2 产物（20_registered/，含完整多站配准结果），单站或无配准
    产物时回退 M1 产物 / 原始扫描。合并后交给 :func:`h3d.mesh.run` 按
    ``cfg.mesh.method``（poisson / ball_pivoting / alpha_shape）重建，
    输出水密网格并记录顶点 / 面数指标（设计书 §34.2）。
    """
    from h3d.core import InputDataError
    from h3d.io import load_point_cloud, save_mesh
    from h3d.mesh import mesh_metrics, run as mesh_run

    sources = task.outputs_of("M2") or task.outputs_of("M1") or task.raw_paths
    if not sources:
        raise InputDataError("M3 输入为空（无可用点云）")

    # 合并多站点为一片稠密点云（open3d PointCloud 支持 +）
    combined = None
    for src_path in sources:
        pc = load_point_cloud(src_path)
        combined = pc if combined is None else combined + pc

    mesh = mesh_run(combined, cfg.mesh)
    out_dir = os.path.join(task.root, "30_dense", task.item_id)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{task.item_id}.ply")
    save_mesh(mesh, out)

    metrics = mesh_metrics(mesh, n_input_points=len(combined.points))
    log.info("M3 完成：%s（%d 点 -> %d 顶点 / %d 面，watertight=%s）",
             task.item_id, metrics["n_input_points"],
             metrics["n_vertices"], metrics["n_triangles"],
             metrics["watertight"])
    return StageResult("M3", TaskStatus.DONE, [out], metrics)


@register_stage("M4")
def _stage_texture(task: Task, cfg) -> StageResult:
    """M4 网格化与纹理：顶点色映射 + LOD 多分辨率 + 多格式导出（带色模型交付）。

    输入：M3 网格产物（30_dense/<item>.ply）+ 带色参考点云（M2 配准结果）。
    流程：顶点色映射（scipy cKDTree 最近邻取色）-> 按 lod_levels 生成多分辨率
    -> 每档导出 export_formats（ply/obj/glb）到 50_tex/<item>/。参考点云无颜色时
    降级输出几何模型并标注（软著方案 2.4「无纹理 -> 输出几何模型」）。
    """
    from h3d.core import InputDataError
    from h3d.io import load_mesh, load_point_cloud
    from h3d.texture import run as texture_run

    mesh_path = os.path.join(task.root, "30_dense", task.item_id,
                             f"{task.item_id}.ply")
    if not os.path.exists(mesh_path):
        raise InputDataError(f"M4 缺少 M3 网格产物: {mesh_path}")
    mesh = load_mesh(mesh_path)

    ref_sources = task.outputs_of("M2") or task.outputs_of("M1") or task.raw_paths
    if not ref_sources:
        raise InputDataError("M4 参考点云为空（无 M2/M1 产物）")
    ref = None
    for src in ref_sources:
        pc = load_point_cloud(src)
        ref = pc if ref is None else ref + pc

    out_dir = os.path.join(task.root, "50_tex", task.item_id)
    outputs, metrics = texture_run(mesh, ref, cfg.texture, out_dir, task.item_id)
    log.info("M4 完成：%s（%s，%d LOD x %d 格式）", task.item_id,
             "vertex-color" if metrics["colored"] else "geometry",
             metrics["n_lods"], len(cfg.texture.export_formats))
    return StageResult("M4", TaskStatus.DONE, outputs, metrics)


@register_stage("M5")
def _stage_quality(task: Task, cfg) -> StageResult:
    """M5 质检质量门：对照标准阈值给出达标判定并生成报告（几何质量可报告）。

    输入：M3 网格产物（30_dense/<item>.ply）+ 参考点云（M2 配准结果）。
    流程：quality.run 计算覆盖率 / 中误差 / 边界边比例 -> 先写 60_qc 中间量 +
    70_report accuracy.csv（失败也有据可查）-> 不达标抛 QualityGateError(E500)，
    由编排层失败隔离（该尊不进入交付包，队列其余照常处理，设计书 §6.3）。
    """
    from h3d.core import InputDataError, QualityGateError
    from h3d.io import load_mesh, load_point_cloud
    from h3d.quality import build_report, run as quality_run

    mesh_path = os.path.join(task.root, "30_dense", task.item_id,
                             f"{task.item_id}.ply")
    if not os.path.exists(mesh_path):
        raise InputDataError(f"M5 缺少 M3 网格产物: {mesh_path}")
    mesh = load_mesh(mesh_path)

    ref_sources = task.outputs_of("M2") or task.outputs_of("M1") or task.raw_paths
    if not ref_sources:
        raise InputDataError("M5 参考点云为空（无 M2/M1 产物）")
    ref = None
    for src in ref_sources:
        pc = load_point_cloud(src)
        ref = pc if ref is None else ref + pc

    metrics = quality_run(mesh, ref, cfg.quality)

    # 先落盘报告（失败也有据可查），再判定
    qc_dir = os.path.join(task.root, "60_qc", task.item_id)
    rpt_dir = os.path.join(task.root, "70_report", task.item_id)
    reports = build_report(task.item_id, metrics, cfg.quality, qc_dir, rpt_dir)

    if not metrics["ok"]:
        raise QualityGateError(
            f"质检不达标: coverage={metrics['coverage']:.1%} "
            f"(阈值 {cfg.quality.coverage_threshold:.0%}), "
            f"rmse={metrics['rmse_mm']:.2f}mm "
            f"(阈值 {cfg.quality.rmse_threshold_mm:.1f}mm)",
            item_id=task.item_id, stage="M5")

    manifest_metrics = {
        "coverage": round(float(metrics["coverage"]), 4),
        "rmse_mm": round(float(metrics["rmse_mm"]), 4),
        "boundary_ratio": round(float(metrics["boundary_ratio"]), 4),
        "mps_m": round(float(metrics["mps_m"]), 6),
    }
    log.info("M5 完成：%s（coverage=%.1f%%, rmse=%.2fmm -> pass）",
             task.item_id, metrics["coverage"] * 100, metrics["rmse_mm"])
    return StageResult("M5", TaskStatus.DONE, reports, manifest_metrics)


@register_stage("M6")
def _stage_publish(task: Task, cfg) -> StageResult:
    """M6 发布与溯源：交付包归档（导出模型 + 元数据快照 + 质检报告，主线收官）。

    输入：M4 最高精度带色模型（50_tex/<item>_L1.*）。
    流程：交付模型复制到 80_export/<item>/（ply/obj/glb）-> 元数据快照
    90_meta/<item>_meta.json（manifest 溯源）-> 可选复制质检报告 -> 交付清单
    90_meta/delivery_manifest.json 汇总本批次全部交付包（TC-602）。
    """
    from h3d.core import InputDataError
    from h3d.core.metadata import generate_run_id, sha256_text, software_versions
    from h3d.publish import (
        build_delivery_manifest,
        build_meta,
        copy_qc_report,
        export_delivery,
        write_meta,
    )

    tex_dir = os.path.join(task.root, "50_tex", task.item_id)
    if not os.path.isdir(tex_dir):
        raise InputDataError(f"M6 缺少 M4 带色模型目录: {tex_dir}")

    # 1) 交付模型（80_export/<item>/）
    export_dir = os.path.join(task.root, "80_export", task.item_id)
    models = export_delivery(tex_dir, export_dir, task.item_id, cfg.publish.formats)

    # 2) 该尊各阶段指标（从 task 内存状态取，manifest 批处理结束才落盘）
    stage_metrics = {}
    for stage_id in ("M1", "M2", "M3", "M4", "M5"):
        res = task.results.get(stage_id)
        if res is not None:
            stage_metrics[stage_id] = dict(res.metrics)
            stage_metrics[stage_id]["status"] = res.status.value
    stage_metrics.setdefault("M6", {"status": "done"})

    # 3) 元数据快照（90_meta/<item>_meta.json，manifest 溯源）
    project = {"name": cfg.project.name, "operator": cfg.project.operator}
    config_hash = sha256_text(str(cfg.to_dict()))
    meta = build_meta(task.item_id, stage_metrics, project,
                      software_versions(), generate_run_id(), config_hash)
    meta["M6_outputs"] = models  # 交付模型路径入元数据
    meta_path = write_meta(meta, os.path.join(task.root, "90_meta"), task.item_id)

    # 4) 可选复制质检报告进交付包
    qc_report = None
    if cfg.publish.copy_qc:
        qc_report = copy_qc_report(task.root, export_dir, task.item_id)

    # 5) 追加到交付清单（90_meta/delivery_manifest.json）
    entries = []
    dm_path = os.path.join(task.root, "90_meta", "delivery_manifest.json")
    if os.path.exists(dm_path):
        with open(dm_path, encoding="utf-8") as fh:
            entries = json.loads(fh.read()).get("deliveries", [])
    entries.append({
        "item_id": task.item_id,
        "status": "published",
        "models": models,
        "qc_report": qc_report,
        "qc_status": stage_metrics.get("M5", {}).get("status", "N/A"),
        "meta": meta_path,
        "config_hash": config_hash,
    })
    delivery_manifest = build_delivery_manifest(entries, dm_path)

    outputs = models + [meta_path, delivery_manifest] + ([qc_report] if qc_report else [])
    metrics = {
        "formats": list(cfg.publish.formats),
        "n_models": len(models),
        "meta": os.path.basename(meta_path),
        "delivery_manifest": os.path.basename(delivery_manifest),
    }
    log.info("M6 完成：%s（%d 格式模型 + 元数据快照 + 交付清单）",
             task.item_id, len(models))
    return StageResult("M6", TaskStatus.DONE, outputs, metrics)


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------
def run_batch(
    cfg,
    tasks: Optional[list[Task]] = None,
    stages: Optional[dict] = None,
) -> BatchResult:
    """批量处理入口：发现任务 → 按注册阶段编排 → 失败隔离 / 断点续跑 → 写 manifest。

    参数
    ----
    cfg:    PipelineConfig（项目 / 预处理 / 配准 / 调度 / 报表各段）。
    tasks:  可选，显式传入任务列表（测试或自定义队列用）；省略则自动发现。
    stages: 可选，覆盖默认阶段注册表（测试注入用）。

    返回
    ----
    :class:`BatchResult`（含任务状态汇总与失败清单）。
    """
    stages = stages if stages is not None else STAGE_REGISTRY
    return BatchProcessor(cfg, stages=stages).run(tasks)


__all__ = [
    "STAGE_REGISTRY",
    "register_stage",
    "discover_items",
    "BatchProcessor",
    "BatchResult",
    "run_batch",
    "Task",
    "TaskStatus",
    "StageResult",
]
