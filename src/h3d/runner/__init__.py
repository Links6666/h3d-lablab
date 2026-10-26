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
    result = run_batch(cfg)          # 自动发现 00_raw 下的文物并批量建模
    print(result.summary())
"""

from __future__ import annotations

import json
import os
import time
from collections import OrderedDict
from typing import Callable, Optional

import numpy as np

from h3d.core import get_logger

from .task import Task, TaskStatus, StageResult
from .batch_processor import BatchProcessor, BatchResult, discover_items

log = get_logger("runner")

# 阶段注册表（保持注册顺序 = 执行顺序，见《设计书》31「按序调用业务模块」）
STAGE_REGISTRY: "OrderedDict[str, Callable[[Task, object], StageResult]]" = OrderedDict()

# 双源融合阈值：SfM 稠密点云相对激光几何的「新信息占比」低于此值时，
# 视为纯冗余（甚至纯噪声）而不并入。0.15 为在线标定的 fallback 切换点。
_DENSE_MERGE_MIN_NOVEL = 0.15


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
def _camera_centers_of(task: Task):
    """读取 S1 相机中心（``90_meta/<item>_capture_meta.json``），无则返回 None。

    ``capture_meta`` 的 ``t`` 是**相机中心**（由 :func:`h3d.sfm_solve.parse_images`
    按 ``C = -R_co^T tvec`` 换算），可直接用于法线朝外的全局判定。
    """
    meta = os.path.join(task.root, "90_meta", f"{task.item_id}_capture_meta.json")
    if not os.path.exists(meta):
        return None
    try:
        with open(meta, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        cams = np.asarray([im["t"] for im in data.get("images", [])],
                          dtype=np.float64)
    except Exception as exc:  # noqa: BLE001 - 位姿不可用只是少一道增强
        log.warning("相机位姿读取失败（跳过法线朝向校正）：%s", exc)
        return None
    return cams if cams.ndim == 2 and len(cams) >= 2 else None


@register_stage("M1")
def _stage_preprocess(task: Task, cfg) -> StageResult:
    """M1 预处理：对每个采集站点做降采样 / 去噪 / 法线，输出到 ``10_clean/``。"""
    from h3d.io import load_point_cloud, save_point_cloud
    from h3d.preprocess import run as preprocess_run

    # 法线全局朝向（S1 有相机位姿时才有依据）：tangent-plane 一致化只统一
    # 相对符号，全局朝内会让 Poisson 翻面、M4 贴图采到背景（见
    # h3d.preprocess.normal_estimate.orient_normals_by_cameras 的实机说明）。
    cam_centers = _camera_centers_of(task)
    normal_info: dict = {}

    def _finish(pcd):
        if cam_centers is None:
            return pcd
        from h3d.preprocess.normal_estimate import orient_normals_by_cameras
        info = orient_normals_by_cameras(pcd, cam_centers)
        normal_info.update(info)
        return pcd

    out_dir = os.path.join(task.root, "10_clean", task.item_id)
    os.makedirs(out_dir, exist_ok=True)
    outputs: list[str] = []
    points_in = points_out = 0
    metrics = {"dense_novel_ratio": None, "dense_merged": None}
    for i, raw in enumerate(task.raw_paths, start=1):
        pcd = load_point_cloud(raw)
        points_in += len(pcd.points)
        clean = _finish(preprocess_run(pcd, cfg.preprocess))
        points_out += len(clean.points)
        out = os.path.join(out_dir, f"{task.item_id}_s{i:02d}.ply")
        save_point_cloud(clean, out)
        outputs.append(out)
    # 场景②闭环：S1 dense 产物（10_dense/<item>/fused_dense.ply）作为补充几何
    # （P3 双源融合：激光几何 + dense 几何覆盖互补）
    dense_path = os.path.join(task.root, "10_dense", task.item_id,
                              "fused_dense.ply")
    if os.path.exists(dense_path):
        pcd = load_point_cloud(dense_path)
        # 纯照片场景（无激光锚点）：先剔掉背景衬板/台面，只留主簇。
        # 依据见 h3d/preprocess/background.py 的实机说明：一件器物被建成
        # 25 个连通块、最大块仅 27.8%，人眼看到的是"白衬板 + 碎块"，
        # 这才是"3D 预览很重要却很糟糕"的主因（贴图链路本身是好的）。
        # 只在场景② 生效：场景① 的激光几何没有背景问题，剔平面反而会削掉器物。
        if not task.raw_paths and getattr(cfg.preprocess, "remove_background", False):
            from h3d.preprocess.background import remove_background
            n_before = len(pcd.points)
            t_bg = time.perf_counter()
            pcd, bg = remove_background(
                pcd,
                max_planes=int(cfg.preprocess.bg_max_planes),
                plane_dist_frac=float(cfg.preprocess.bg_plane_dist_frac),
                cluster_eps_k=float(cfg.preprocess.bg_cluster_eps_k),
                max_cluster_points=int(cfg.preprocess.bg_max_cluster_points),
                keep_cluster_frac=float(cfg.preprocess.bg_keep_cluster_frac))
            bg["bg_points_before"] = n_before
            bg["bg_elapsed_s"] = round(time.perf_counter() - t_bg, 2)
            metrics["background"] = bg
            log.info("M1 背景剔除：%s（%d -> %d 点，eps=%.4f=%.0f×MPS，"
                     "聚类 %d 点，%.1fs）", task.item_id, n_before,
                     len(pcd.points), bg.get("bg_eps") or 0.0,
                     bg.get("bg_eps_mps_ratio") or 0.0,
                     bg.get("bg_cluster_points") or 0,
                     bg["bg_elapsed_s"])
        # 覆盖互补评估：dense 点相对既有几何的"新信息占比"
        #（距既有合并点 > 2×MPS 的 dense 点比例——dense 覆盖激光空洞区域的度量）
        novel = 0.0
        if outputs:
            base_pc = load_point_cloud(outputs[0])
            from scipy.spatial import cKDTree
            try:
                d, _ = cKDTree(np.asarray(base_pc.points)).query(
                    np.asarray(pcd.points), k=1)
                # novel 阈值在线标定（P3）：距离分布 Otsu 分割自适应切换点
                from h3d.novel_calib import (online_novel_threshold,
                                             save_novel_threshold)
                _thr_dist, novel = online_novel_threshold(
                    np.asarray(base_pc.points), np.asarray(pcd.points))
                metrics["dense_novel_thr_m"] = round(_thr_dist, 5)
                # 在线回写（P3）：跨运行指数平滑持久化。
                # 2026-10-18：原为 `except: pass` —— 写盘失败时阈值不落盘、
                # 指标里也没有任何痕迹，下次运行仍从旧值起步。按项目约定
                # 「except 兜底赋值必须同时写 metrics + WARNING」改为显式告警。
                try:
                    metrics["novel_threshold_file"] = save_novel_threshold(
                        task.root, task.item_id, novel, _thr_dist)
                except Exception as exc:  # noqa: BLE001
                    metrics["novel_threshold_error"] = str(exc)[:200]
                    log.warning("novel 阈值回写失败（本次仍按 %.3f 合并判定）：%s",
                                novel, exc)
            except Exception as exc:  # noqa: BLE001
                # 禁止静默降级：原实现只写 novel = 0.0，而本函数缺 import numpy
                # 导致 NameError 被长期吞掉——P3 在线标定**从未生效**，
                # 所有 dense 一律按 novel=0 降权合并（2026-10-17 实机发现）。
                novel = 0.0
                metrics["dense_novel_error"] = str(exc)[:200]
                log.warning("dense 新信息占比评估失败（按 0 处理）：%s", exc)
            metrics["dense_novel_ratio"] = round(novel, 4)

        # 场景①：dense 与激光几何高度重叠时**不合并**。
        #
        # 实机教训（2026-10-17，合成椭球 + 16 视角）：场景① 勾选「稠密重建」后
        # S1 产出 146453 点，全部落在激光点 2×MPS 邻域内（novel=0.0，零新信息），
        # 原实现仍以「降权体素」合并 → M3 三角面 19828→33758、
        # M5 rmse 27.52→36.78 mm（+34%）、M4 纹理覆盖 5.87%→2.32%。
        # SfM 稠密点云精度低于激光且存在尺度/坐标系差异，冗余合并只会污染
        # 精确几何。判定与动作必须一致：novel 低于阈值 → 只记指标，不合并。
        if outputs and novel < _DENSE_MERGE_MIN_NOVEL:
            metrics["dense_merged"] = False
            log.warning(
                "M1 跳过 dense 合并：%s 与激光几何重叠度 %.0f%%（新信息 %.1f%% "
                "< %.0f%% 阈值），SfM 稠密点云无增量信息，不并入以免污染几何",
                task.item_id, (1.0 - novel) * 100, novel * 100,
                _DENSE_MERGE_MIN_NOVEL * 100)
        else:
            points_in += len(pcd.points)
            # 双源融合自适应加权（P2+P3）：novel_ratio 驱动 dense 合并权重——
            #   重叠高（novel 低）→ dense 冗余为主 → 更粗体素降采样（降权）；
            #   重叠低（novel 高）→ dense 提供新信息 → 原体素（全权合并）。
            #   切换阈值由在线标定给出（固定 0.15 为 fallback）。
            voxel = cfg.preprocess.voxel_down_sample or 0.05
            # 场景②（无激光几何）：dense 是**唯一**几何来源，不存在"冗余"，
            # 必须用配置体素原值。旧实现在该分支里 novel 恒为 0 → 一律 ×2.0，
            # 等于把唯一的几何源无理由粗化一倍（细节直接损失）。
            if outputs and novel < _DENSE_MERGE_MIN_NOVEL:
                voxel_eff = voxel * 2.0  # 高重叠：dense 降权（粗降采样去冗余）
                metrics["dense_weight"] = "reduced"
            else:
                voxel_eff = voxel * (0.8 if outputs else 1.0)
                metrics["dense_weight"] = "full"
            clean = pcd.voxel_down_sample(max(voxel_eff, 1e-4))
            clean = _finish(preprocess_run(clean, cfg.preprocess))
            points_out += len(clean.points)
            metrics["dense_merged"] = True
            # 合并进第一站（不新增站点，避免触发无意义多站配准）
            if outputs:
                merged = load_point_cloud(outputs[0]) + clean
                save_point_cloud(merged, outputs[0])
            else:
                out = os.path.join(out_dir, f"{task.item_id}_dense.ply")
                save_point_cloud(clean, out)
                outputs.append(out)
            log.info("M1 合并 dense 几何：%s（+%d 点，新信息占比 %s，权重=%s）",
                     task.item_id, len(pcd.points),
                     metrics.get("dense_novel_ratio", "N/A"),
                     metrics.get("dense_weight", "full"))
    metrics.update({
        "n_scans": len(task.raw_paths),
        "points_in": points_in,
        "points_out": points_out,
    })
    if normal_info:
        metrics["normal_orientation"] = normal_info
    # 几何来源口径：场景② 没有激光基线，novel 无从计算（恒 None），
    # 报表里必须写清"这是唯一几何源"而不是留一个空指标。
    if not task.raw_paths:
        metrics["dense_source"] = "sfm_only"
    elif metrics.get("dense_merged"):
        metrics["dense_source"] = "laser+sfm"
    else:
        metrics["dense_source"] = "laser"
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
        # 2026-10-18：单站是**正常终态**（返回 DONE），但原来直接 return，
        # 不打「M2 完成」。界面靠完成行把灯从「运行中」熄灭——于是单站任务
        # 的 M2 灯一直橙色、进度条卡在 9/10，看着像卡死。
        # 中小型文物常只有一站扫描，这不是边缘情况。
        #
        # 同时按「恒等变换」落盘一份 20_registered 产物：M2 的语义是"输出统一
        # 坐标系下的点云"，返回空产物会让下游 `outputs_of("M2")` 落空、只能
        # 依赖 M1 兜底——续跑时更是直接读不到（实机 M5 报 E100 的诱因之一）。
        try:
            ref = load_point_cloud(cleaned[0])
            ref_out = os.path.join(out_dir, f"{task.item_id}_s01.ply")
            save_point_cloud(ref, ref_out)
            outputs = [ref_out]
        except Exception as exc:  # noqa: BLE001 - 落盘失败不改变"单站完成"结论
            log.warning("M2 单站产物落盘失败（继续，下游回退 M1）：%s", exc)
            outputs = []
        log.info("M2 完成：%s（单站，无需配准，跳过 M2 直接进入 M3）",
                 task.item_id)
        return StageResult("M2", TaskStatus.DONE, outputs,
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
    fallback: dict = {}
    if cfg.texture.paint_mode == "photo":
        # M4 颜色源统一开关：paint_mode='photo' → 照片投影纹理
        # （消费 S1 位姿 + 00_photo 照片；颜色源从点云 RGB 切换为照片）
        #
        # 场景① 降级（2026-10-17 实机教训）：用户只放了激光扫描件、没放照片时，
        # S1 已降级跳过，此处若继续抛错会让 M4~M6 全部作废，连纯几何模型都拿不到。
        # 按《软著方案》2.4「无纹理 → 输出几何模型」降级为顶点色，并在 metrics
        # 里留 paint_mode_effective / fallback_reason，报表可见（不静默）。
        from h3d.core import RecoverableError as _Rec
        from h3d.photo_texture import texture_mesh_from_photos
        try:
            outputs, metrics = texture_mesh_from_photos(
                mesh, task.root, task.item_id, cfg, out_dir,
                # 场景②：几何来自 SfM 稠密重建 → 与相机同坐标系，跳过 ICP 对齐
                geometry_is_sfm=not task.raw_paths)
            metrics.setdefault("paint_mode_effective", "photo")
            log.info("M4 完成：%s（paint_mode=photo，照片投影纹理，%d 文件）",
                     task.item_id, len(outputs))
            return StageResult("M4", TaskStatus.DONE, outputs, metrics)
        except (_Rec, InputDataError, ValueError, KeyError, OSError) as exc:
            fallback = {
                "paint_mode_requested": "photo",
                "paint_mode_effective": "vertex-color",
                "fallback_reason": str(exc)[:300],
            }
            log.warning(
                "M4 照片纹理不可用，降级为顶点色（几何模型照常交付）：%s", exc)
    outputs, metrics = texture_run(mesh, ref, cfg.texture, out_dir, task.item_id)
    if fallback:
        metrics.update(fallback)
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
    由编排层失败隔离（该件不进入交付包，队列其余照常处理，设计书 §6.3）。
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

    # 尺度口径（2026-10-18 / 2026-10-26）：报表里的 rmse 单位写死 "mm"，但场景②
    # （纯照片）的坐标是 SfM **任意单位**，没有激光锚点就没有"毫米"可言。
    # 现在把「是否有度量基准」**在算之前**传给 quality.run —— 它据此决定
    #   · 有锚点：rmse_mm 是真实毫米，按 rmse_threshold_mm 判门；
    #   · 无锚点：rmse_mm 只是「模型单位×1000」，不判门，改报
    #     rmse_rel = rmse_units / 包围盒对角线（无量纲，唯一可对外引用的口径）。
    metric_units = bool(task.raw_paths)
    metrics = quality_run(mesh, ref, cfg.quality, metric_units=metric_units)
    try:
        import numpy as _np
        metrics["model_bbox_diag"] = round(float(_np.linalg.norm(
            _np.asarray(mesh.get_max_bound()) - _np.asarray(mesh.get_min_bound()))), 6)
    except Exception:  # noqa: BLE001 - 仅用于报表展示，失败留空
        metrics["model_bbox_diag"] = None
    metrics["metric_units"] = metric_units
    if not metric_units:
        _rel = metrics.get("rmse_rel")
        log.warning("本件几何来自 SfM 重建（无激光锚点）：坐标是任意单位，"
                    "rmse %.4f 模型单位（对角线的 %s），报表不再按毫米展示；"
                    "要出毫米级精度必须补尺度控制点（X2）或激光扫描件",
                    float(metrics.get("rmse_units") or 0.0),
                    ("N/A" if _rel is None else f"{float(_rel) * 100:.2f}%"))

    # 纹理质量维度（M4 产物存在时评估：清晰度/接缝/填充率）
    from h3d.quality.texture_quality import texture_quality
    tex_dir = os.path.join(task.root, "50_tex", task.item_id)
    tex_png = os.path.join(tex_dir, f"{task.item_id}_L1.0_diffuse.png")
    if not os.path.exists(tex_png):
        # uv 模式产物名可能不同：回退扫描 50_tex
        if os.path.isdir(tex_dir):
            cands = [f for f in os.listdir(tex_dir)
                     if f.endswith("diffuse.png") or f.endswith("_0.png")]
            if cands:
                tex_png = os.path.join(tex_dir, sorted(cands)[0])
    # 着色掩膜与图集利用率都来自 M4 指标：覆盖率改按真实着色区算，
    # 不再用"亮度 > 20"猜（暗色器物会被误判为"没贴图"）
    m4 = task.metrics_of("M4") if hasattr(task, "metrics_of") else {}
    mask_png = m4.get("texture_mask") or os.path.join(
        tex_dir, f"{task.item_id}_L1.0_mask.png")
    tq = texture_quality(tex_png if os.path.exists(tex_png) else None,
                         mask_path=mask_png,
                         atlas_utilization=m4.get("atlas_utilization"))
    metrics.update(tq)
    if tq["texture_available"]:
        log.info("M5 纹理质量：sharpness=%.2f seam=%.2f 填充率=%s 覆盖率=%.1f%%",
                 tq["texture_sharpness"] or 0.0, tq["texture_seam"] or 0.0,
                 ("N/A" if tq.get("texture_fill_ratio") is None
                  else f"{tq['texture_fill_ratio'] * 100:.1f}%"),
                 (tq["texture_coverage"] or 0.0) * 100)
        if not tq["texture_ok"]:
            log.warning("纹理质量不达标（清晰度 %.2f / 阈值 3.0，填充率 %s）——"
                        "交付模型可能呈「有几何无颜色」或花斑；"
                        "请检查位姿/遮挡与 UV 岛填充",
                        tq["texture_sharpness"] or 0.0,
                        ("N/A" if tq.get("texture_fill_ratio") is None
                         else f"{tq['texture_fill_ratio'] * 100:.1f}%"))

    # 先落盘报告（失败也有据可查），再判定
    qc_dir = os.path.join(task.root, "60_qc", task.item_id)
    rpt_dir = os.path.join(task.root, "70_report", task.item_id)
    reports = build_report(task.item_id, metrics, cfg.quality, qc_dir, rpt_dir)

    if not metrics["ok"]:
        # 失败文案要按口径说人话：纯照片场景没有毫米，硬写 mm 会误导用户
        # 去「修精度」，而真正的问题多半是覆盖率（几何缺片 / 背景未剔除）。
        if metric_units:
            rmse_txt = (f"rmse={metrics['rmse_mm']:.2f}mm "
                        f"(阈值 {cfg.quality.rmse_threshold_mm:.1f}mm)")
        else:
            _rel = metrics.get("rmse_rel")
            rmse_txt = ("中误差不参与判定（无激光锚点）"
                        if _rel is None else
                        f"相对中误差 {float(_rel) * 100:.2f}%（不参与判定）")
        raise QualityGateError(
            f"质检不达标: coverage={metrics['coverage']:.1%} "
            f"(阈值 {cfg.quality.coverage_threshold:.0%}), {rmse_txt}",
            item_id=task.item_id, stage="M5")

    log.info("M5 完成：%s（coverage=%.1f%%, rmse=%.4f %s -> pass）",
             task.item_id, metrics["coverage"] * 100,
             float(metrics.get("rmse_units") or 0.0),
             metrics.get("rmse_unit") or "mm")

    manifest_metrics = {
        "coverage": round(float(metrics["coverage"]), 4),
        "rmse_mm": round(float(metrics["rmse_mm"]), 4),
        # 三个口径一起落盘（2026-10-26）：报表按 rmse_unit 决定展示哪一列，
        # 论文引用相对值时不必再从 rmse_mm 反推。
        "rmse_units": round(float(metrics.get("rmse_units") or 0.0), 6),
        "rmse_rel": (None if metrics.get("rmse_rel") is None
                     else round(float(metrics["rmse_rel"]), 6)),
        "rmse_unit": metrics.get("rmse_unit"),
        "boundary_ratio": round(float(metrics["boundary_ratio"]), 4),
        "mps_m": round(float(metrics["mps_m"]), 6),
        "texture_available": tq["texture_available"],
        "texture_sharpness": tq["texture_sharpness"],
        "texture_seam": tq["texture_seam"],
        "texture_coverage": tq["texture_coverage"],
        "texture_fill_ratio": tq.get("texture_fill_ratio"),
        "texture_ok": tq["texture_ok"],
        # 尺度口径必须进 manifest：报表要据此把 rmse/mps 的单位写成
        # 「任意单位」而不是 mm（2026-10-18）。原先只写进 quality 报告，
        # 结果 HTML/CSV 一律按 mm 展示，纯照片项目的精度数字全是假的。
        "model_bbox_diag": metrics.get("model_bbox_diag"),
        "metric_units": metrics.get("metric_units"),
    }
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

    # 2) 该件各阶段指标（从 task 内存状态取，manifest 批处理结束才落盘）
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

    # 4.5) 论文实验表格并入交付包（P2，M6 归档）
    thesis_table = None
    try:
        from h3d.thesis_tables import export_thesis_table
        thesis_table = export_thesis_table(
            task.root, item_ids=[task.item_id],
            out_path=os.path.join(export_dir,
                                  f"{task.item_id}_thesis_table.csv"))
    except Exception as exc:  # noqa: BLE001 - 表格归档失败不阻断 M6
        log.warning("论文表格归档失败（跳过）：%s", exc)

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
        "thesis_table": thesis_table,
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


# ---------------------------------------------------------------------------
# 可选扩展 stage 注册（场景①照片自动化闭环，默认关闭；放置于 __all__ 前，
# 保证 register_stage / STAGE_REGISTRY 已定义）
# ---------------------------------------------------------------------------
from h3d.sfm_solve import run_sfm_solve
from h3d.photo_texture import run_photo_texture


@register_stage("S1")
def _stage_sfm_solve(task, cfg):
    """S1 sfm_solve：照片相机位姿解算（可选，默认关闭）。"""
    return run_sfm_solve(task, cfg)


@register_stage("P1")
def _stage_photo_texture(task, cfg):
    """P1 photo_texture：照片投影纹理（可选，默认关闭；消费 S1 位姿）。"""
    return run_photo_texture(task, cfg)


@register_stage("X1")
def _stage_multitemporal(task, cfg):
    """X1 多时相形变检测（P3）：两期 dense 点云 M3C2 差分。

    输入：00_ref/<item>/*.ply（用户放置的上期 dense/模型，参考时相 t1）
        vs 当期 10_dense/<item>/fused_dense.ply（t2，S1 dense 产物）。
    无参考期/输入缺失时 SKIPPED。输出 80_export/<item>_X1 差分指标。
    """
    from h3d.core import RecoverableError
    from h3d.io import load_point_cloud
    from h3d.runner.task import StageResult, TaskStatus

    if not cfg.runner.x1_enabled:
        return StageResult("X1", TaskStatus.SKIPPED, [],
                           {"skip_reason": "runner.x1_enabled=false"}, 0.0)
    ref_dir = os.path.join(task.root, "00_ref", task.item_id)
    ref_files = [f for f in os.listdir(ref_dir) if f.endswith(".ply")] \
        if os.path.isdir(ref_dir) else []
    t2_path = os.path.join(task.root, "10_dense", task.item_id,
                           "fused_dense.ply")
    if not ref_files or not os.path.exists(t2_path):
        return StageResult("X1", TaskStatus.SKIPPED, [], {
            "skip_reason": f"缺参考期 00_ref/{task.item_id}/*.ply "
                           f"或当期 10_dense/{task.item_id}/fused_dense.ply"}, 0.0)
    import time
    t0 = time.time()
    from h3d.change import run as change_run
    t1 = load_point_cloud(os.path.join(ref_dir, sorted(ref_files)[0]))
    t2 = load_point_cloud(t2_path)
    result = change_run(t1, t2, cfg.change)
    out = os.path.join(task.root, "80_export", task.item_id,
                       f"{task.item_id}_X1_m3c2.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    import json
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result["metrics"], fh, ensure_ascii=False, indent=2)
    # X1 形变指标并入 accuracy.csv（P2，论文实验表格）：
    # M5 生成的 accuracy.csv 追加「最大形变/变化占比」列（容错：缺失则跳过）
    acc_csv = os.path.join(task.root, "70_report", task.item_id,
                           "accuracy.csv")
    if os.path.exists(acc_csv):
        _append_x1_to_accuracy(acc_csv, task.item_id, result["metrics"])
    log.info("X1 完成：%s（max_change=%.1fmm, changed=%.1f%%）",
             task.item_id, result["metrics"].get("max_change_mm", 0.0),
             result["metrics"].get("changed_ratio", 0.0) * 100)
    return StageResult("X1", TaskStatus.DONE, [out],
                       result["metrics"], time.time() - t0)


@register_stage("X2")
def _stage_colour_quality(task, cfg):
    """X2 色差评估（P3）：从 M4 纹理 diffuse 提取色块 → ΔE vs ColorChecker24。

    输入：50_tex/<item>/<item>_L1.0_diffuse.png（纹理图）。采样 24 个色块
    中心色 → colour.run 与 ColorChecker24 参考 ΔE。无纹理图时 SKIPPED。
    """
    from h3d.core import RecoverableError
    from h3d.runner.task import StageResult, TaskStatus

    if not cfg.runner.x2_enabled:
        return StageResult("X2", TaskStatus.SKIPPED, [],
                           {"skip_reason": "runner.x2_enabled=false"}, 0.0)
    tex_dir = os.path.join(task.root, "50_tex", task.item_id)
    diffuse = None
    if os.path.isdir(tex_dir):
        cands = [f for f in os.listdir(tex_dir)
                 if f.endswith("diffuse.png")]
        if cands:
            diffuse = os.path.join(tex_dir, sorted(cands)[0])
    if not diffuse:
        return StageResult("X2", TaskStatus.SKIPPED, [], {
            "skip_reason": f"缺纹理图 50_tex/{task.item_id}/*_diffuse.png"}, 0.0)
    import time
    t0 = time.time()
    from PIL import Image
    import numpy as np
    img = np.asarray(Image.open(diffuse), dtype=np.float64)[:, :, :3]
    # 色卡自动定位（P3）：优先在 00_photo 照片中定位 ColorChecker（ArUco
    # 四角标记 → 单应提取 24 色块）；无色卡照片时回退纹理随机采样。
    patches = None
    # 照片目录定位必须与 S1 / P1 同口径：**优先 task.root/00_photo**（task.root
    # 就是项目根，即 00_raw 的父目录），再回退上一级。
    # 缺陷修复（2026-10-17）：原实现只写 os.path.dirname(task.root)/00_photo，
    # 比正确路径**多了一层**——场景① 下色卡定位永远找不到照片，静默退回
    # 「纹理随机采样」，X2 的 ΔE 因此长期失真而无任何报错。
    photo_dir = os.path.join(task.root, "00_photo", task.item_id)
    if not os.path.isdir(photo_dir):
        photo_dir = os.path.join(
            os.path.dirname(os.path.abspath(task.root)), "00_photo", task.item_id)
    from h3d.core.media import list_images as _list_images
    checker_photos = [os.path.basename(p) for p in _list_images(photo_dir)]
    if checker_photos:
        from h3d.colour.checker_locator import locate_colorchecker
        for ph in checker_photos[:6]:
            try:
                p = locate_colorchecker(os.path.join(photo_dir, ph))
                if p is not None:
                    patches = p
                    break
            except Exception:  # noqa: BLE001
                continue
    if patches is None:
        h, w = img.shape[:2]
        rng = np.random.default_rng(0)
        patches = img[rng.integers(0, h, 24), rng.integers(0, w, 24)]
        checker_source = "texture-random"   # 未在照片中定位到色卡
    else:
        checker_source = "photo"
    from h3d.colour import run as colour_run
    result = colour_run(patches, cfg=cfg.colour)
    out = os.path.join(task.root, "80_export", task.item_id,
                       f"{task.item_id}_X2_deltaE.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    import json
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"mean_de": result["mean_de"],
                   "max_de": result["max_de"],
                   "n_patches": result["n_patches"]},
                  fh, ensure_ascii=False, indent=2)
    log.info("X2 完成：%s（mean ΔE=%.2f, max=%.2f，色卡来源=%s）",
             task.item_id, result["mean_de"], result["max_de"], checker_source)
    return StageResult("X2", TaskStatus.DONE, [out],
                       {"mean_de": result["mean_de"],
                        "max_de": result["max_de"],
                        "checker_source": checker_source}, time.time() - t0)


# 执行顺序重排：S1 位姿解算最先（M4 photo / P1 消费其产物），
# P1 纹理投影在 M5 质检之前，X1/X2 评估在 M6 交付之后；
# M1~M6 主链顺序保持不变。
def _append_x1_to_accuracy(acc_csv: str, item_id: str, metrics: dict) -> None:
    """X1 形变指标并入 accuracy.csv（追加列，论文实验表格）。"""
    import csv as _csv
    try:
        with open(acc_csv, "r", encoding="utf-8-sig", newline="") as fh:
            rows = list(_csv.reader(fh))
        if not rows:
            return
        header, data = rows[0], rows[1]
        header += ["X1最大形变(mm)", "X1变化占比"]
        max_c = metrics.get("max_change_mm")
        data += ["N/A" if max_c is None or (isinstance(max_c, float) and max_c != max_c) else f"{max_c:.2f}",
                 f"{metrics.get('changed_ratio', 0.0) * 100:.1f}%"]
        with open(acc_csv, "w", encoding="utf-8-sig", newline="") as fh:
            _csv.writer(fh).writerows([header, data])
        log.info("X1 指标已并入 accuracy.csv：%s", item_id)
    except Exception as exc:  # noqa: BLE001 - 报表追加失败不阻断 X1
        log.warning("X1 并入 accuracy.csv 失败（跳过）：%s", exc)


def _reorder_ext_stages() -> None:
    for stage_id in ("S1", "M1", "M2", "M3", "M4", "P1", "M5", "M6",
                     "X1", "X2"):
        if stage_id in STAGE_REGISTRY:
            STAGE_REGISTRY.move_to_end(stage_id, last=True)


_reorder_ext_stages()


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
