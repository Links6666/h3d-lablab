"""h3d.runner.batch_processor : 批量处理器（失败隔离 + 断点续跑 + run_manifest 溯源）。

设计依据：
    《设计书》31 —— M0 命令与编排：解析配置、初始化 manifest、遍历队列、按序调用
        业务模块、记录状态、支持断点续跑与失败隔离；
    《软著方案》2.7 —— 任务发现 / 批量调度 / 断点续跑 / 失败隔离 / run_manifest 溯源；
    《教学版》19.2 —— 编排层主循环（续跑 + 隔离）。

核心类 :class:`BatchProcessor` 是「无人值守批量处理」的执行内核：对每件文物按
注册的阶段顺序调度，:class:`RecoverableError` 只跳过该件（失败隔离），其余异常在
``fail_isolation=false`` 时快速失败。每个阶段的完成态写入 :class:`RunManifest`，
以 ``item_id/stage`` 为键，支撑断点续跑。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from h3d.core import RecoverableError, RunManifest, get_logger, sha256_text
from h3d.core.media import list_images
import json

from .task import Task, TaskStatus, StageResult

log = get_logger("runner.batch_processor")


# ---------------------------------------------------------------------------
# 可选增强阶段的失败语义（场景①/② 分流）
# ---------------------------------------------------------------------------
# S1 位姿解算 / P1 照片纹理 / X1 多时相 / X2 色差评估属于「增强」而非「主线」。
# 它们在两种场景下的地位完全不同：
#
#   场景①（带扫描件，raw_paths 非空）：几何来自激光，S1 只提供相机位姿，
#       P1/X1/X2 更是可选项。这些阶段失败**不应废掉 M1~M6**——实机教训
#       （2026-10-17）：向导模板 sfm_solve.enabled=true，用户只放了扫描件没放
#       照片，S1 抛 RecoverableError 后 break，整件 done=0 failed=1，
#       连激光几何都没能建模。
#
#   场景②（纯照片，raw_paths 为空）：S1 是**唯一几何来源**，失败必须隔离，
#       否则 M1 无输入、后续阶段全错。
#
# 因此「失败是否连累主线」按 (stage, 是否有激光几何) 联合判定。
OPTIONAL_STAGES = frozenset({"S1", "P1", "X1", "X2"})


def _degrades_on_failure(stage_id: str, task: Task) -> bool:
    """可选增强阶段失败时，是否降级为「跳过」而不是整件失败。

    返回 True 表示：该阶段属于增强，且本件有激光几何兜底 → 主线可继续。
    """
    return stage_id in OPTIONAL_STAGES and bool(task.raw_paths)


def _degraded_entry(exc: Exception, elapsed: float) -> dict:
    """降级阶段在 worker 结果里的序列化形态（skipped + degraded 标记）。"""
    return {
        "status": TaskStatus.SKIPPED.value,
        "outputs": [],
        "metrics": {"degraded": True, "reason": str(exc)[:300]},
        "elapsed_s": round(elapsed, 4),
        "error": "",
    }


# ---------------------------------------------------------------------------
# 任务发现（《软著方案》2.7.1 命名规约）
# ---------------------------------------------------------------------------
def _scan_files(directory: str, formats: list[str]) -> list[str]:
    """递归收集目录下匹配扩展名的文件，返回按路径排序的列表。"""
    out: list[str] = []
    exts = {f.lstrip(".").lower() for f in formats}
    for root, _dirs, files in os.walk(directory):
        for name in sorted(files):
            if os.path.splitext(name)[1].lstrip(".").lower() in exts:
                out.append(os.path.join(root, name))
    return out


#: 数据目录里常见的「非文物」目录：版本库、Mac 解压残留、Windows 回收站/系统卷。
#: 它们一旦含同名扩展名文件就会变成一件"文物"，随后在 M1 报"点云为空"失败，
#: 用户看不出这是垃圾目录造成的（巡检 2026-10-23 实测：``.git`` / ``__MACOSX``
#: 各自成为一件任务，任务数 1 → 3）。
_JUNK_DIRS = {
    "__macosx", "$recycle.bin", "system volume information", "lost+found",
    ".git", ".svn", ".hg", ".idea", ".vscode", "node_modules", "__pycache__",
}


def _is_junk_dir(name: str) -> bool:
    """隐藏目录（``.`` 开头）与已知系统垃圾目录都不算文物。"""
    low = name.strip().lower()
    return not low or low.startswith(".") or low in _JUNK_DIRS


def _lock_root(cfg) -> str:
    """单实例锁的落盘目录 = 数据根（``00_raw`` 的父目录）。

    选数据根而非 ``raw_dir``：多项目共用同一批 ``00_photo`` / ``99_runs``
    时，锁必须覆盖整个数据根才有意义（实机那次两个进程正是在同一根下
    起了两个项目名 yyyyyyy / rrrrr，却操作同一批文物目录）。
    """
    raw = str(getattr(cfg.project, "raw_dir", "") or "").strip()
    if raw:
        parent = os.path.dirname(os.path.abspath(raw.rstrip("\\/")))
        if parent and os.path.isdir(parent):
            return parent
    man = str(getattr(cfg.runner, "manifest", "") or "").strip()
    if man:
        return os.path.dirname(os.path.abspath(man)) or os.getcwd()
    return os.getcwd()


def discover_items(
    raw_dir: str,
    formats: Optional[list[str]] = None,
    photo_dir: Optional[str] = None,
    allow_photo_only: bool = False,
) -> list[Task]:
    """扫描数据根，按目录命名规约识别文物个体，构建任务列表。

    规则（《软著方案》2.7.1，2026-10-17 扩展纯照片模式）：
        - 若 ``raw_dir`` 下存在子目录，则每个子目录 = 一件文物（文物编号 = 子目录名），
          目录内（可嵌套：采集批次 / 设备）的所有匹配文件 = 该件的采集站点；
        - 否则平铺文件模式下，每个匹配文件 = 一件文物（文物编号 = 去扩展名文件名）；
        - **``allow_photo_only=True`` 时**，``00_photo/<item>/`` 下存在图像、而
          ``00_raw/<item>`` 无几何文件的条目同样生成任务（场景②纯照片重建）。

    参数
    ----
    raw_dir:        原始几何目录（通常为 ``00_raw``）。
    formats:        允许的点云/网格扩展名；省略时默认 ``["ply", "pcd", "obj"]``。
    photo_dir:      照片根目录（通常为 ``00_photo``）；省略时取 ``raw_dir`` 上级的
                    ``00_photo``。
    allow_photo_only:
                    是否允许"只有照片、没有激光点云"的条目成为任务。
                    必须与 ``sfm_solve.enabled`` 联动：S1 未开启时纯照片任务
                    没有几何来源，不应进入队列。

    返回
    ----
    :class:`Task` 列表（按文物编号排序，保证批处理顺序可复现）。
    """
    formats = formats or ["ply", "pcd", "obj"]
    raw_dir = os.path.abspath(raw_dir)
    raw_ok = os.path.isdir(raw_dir)

    # 照片根目录：显式传入 > raw 上级的 00_photo
    if photo_dir:
        photo_root = os.path.abspath(photo_dir)
    else:
        photo_root = os.path.join(os.path.dirname(raw_dir), "00_photo")
    photo_ok = os.path.isdir(photo_root)

    if not raw_ok and not (allow_photo_only and photo_ok):
        log.warning("原始数据目录不存在: %s", raw_dir)
        return []

    root = os.path.dirname(raw_dir)  # 项目根目录 = raw_dir 的父目录

    # 候选条目名：raw 子目录 ∪ photo 子目录（后者仅在允许纯照片时纳入）
    raw_subdirs: list[str] = []
    if raw_ok:
        raw_subdirs = [e for e in sorted(os.listdir(raw_dir))
                       if os.path.isdir(os.path.join(raw_dir, e))
                       and not _is_junk_dir(e)]
    photo_subdirs: list[str] = []
    if allow_photo_only and photo_ok:
        photo_subdirs = [e for e in sorted(os.listdir(photo_root))
                         if os.path.isdir(os.path.join(photo_root, e))
                         and not _is_junk_dir(e)
                         and list_images(os.path.join(photo_root, e))]

    names = sorted(set(raw_subdirs) | set(photo_subdirs))
    tasks: list[Task] = []

    if names:
        for name in names:
            scans = (_scan_files(os.path.join(raw_dir, name), formats)
                     if raw_ok else [])
            photos = (list_images(os.path.join(photo_root, name))
                      if allow_photo_only else [])
            # 有几何 → 常规任务；无几何但有照片 → 纯照片任务
            if not scans and not photos:
                continue
            tasks.append(Task(item_id=name, raw_paths=scans, root=root,
                              photo_paths=photos))
    else:
        # 平铺模式：raw 文件逐文件成任务（历史行为）
        for path in (_scan_files(raw_dir, formats) if raw_ok else []):
            base = os.path.basename(path)
            # ``._xxx.ply`` 是 macOS 的 AppleDouble 资源叉（与真文件成对出现），
            # 平铺模式下会被当成另一件文物，体积却是几百字节 → S1/M1 必失败。
            if base.startswith("._"):
                continue
            item_id = os.path.splitext(base)[0]
            tasks.append(Task(item_id=item_id, raw_paths=[path], root=root))
        # 平铺照片兜底：00_photo/ 根下直接放图片（无 <item> 子目录）
        if allow_photo_only and not tasks and photo_ok:
            photos = list_images(photo_root)
            if photos:
                item_id = os.path.basename(os.path.abspath(root)) or "item_001"
                log.info("检测到平铺照片目录（无 <item> 子目录），"
                         "按单条目处理：item_id=%s", item_id)
                tasks.append(Task(item_id=item_id, raw_paths=[], root=root,
                                  photo_paths=photos))

    n_photo_only = sum(1 for t in tasks if not t.raw_paths and t.photo_paths)
    log.info("任务发现完成：%d 件文物（其中纯照片 %d 件）",
             len(tasks), n_photo_only)
    return tasks


def discover_from_config(cfg) -> list[Task]:
    """按配置发现任务（唯一入口，CLI 与 BatchProcessor 共用）。

    2026-10-17 缺陷教训：``BatchProcessor`` 与 CLI dry-run 曾各自调用
    ``discover_items``，新增"纯照片"参数时只改了一处，导致 dry-run 预览为
    0 件而实跑有任务。所有调用方必须经此函数，不得再直接拼装参数。
    """
    raw_dir = cfg.project.raw_dir
    photo_root = os.path.join(
        os.path.dirname(os.path.abspath(raw_dir)), "00_photo")
    return discover_items(
        raw_dir, cfg.project.formats,
        photo_dir=photo_root,
        allow_photo_only=bool(getattr(cfg.sfm_solve, "enabled", False)),
    )


# ---------------------------------------------------------------------------
# 批量结果
# ---------------------------------------------------------------------------
@dataclass
class BatchResult:
    """一次批量调度的汇总结果（供 CLI / 报表层消费）。"""

    tasks: list[Task] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    manifest_path: str = ""

    @property
    def done(self) -> int:
        return sum(1 for t in self.tasks if t.status == TaskStatus.DONE)

    @property
    def failed(self) -> int:
        return sum(1 for t in self.tasks if t.status == TaskStatus.FAIL)

    @property
    def skipped(self) -> int:
        return sum(1 for t in self.tasks if t.status == TaskStatus.SKIPPED)

    def summary(self) -> dict[str, Any]:
        return {
            "total": len(self.tasks),
            "done": self.done,
            "failed": self.failed,
            "skipped": self.skipped,
            "manifest": self.manifest_path,
        }


# ---------------------------------------------------------------------------
# 尺度自适应（跨尺度文物通用化，2026-10-09）
# ---------------------------------------------------------------------------
def probe_geometry(task: Task) -> dict:
    """探测任务的几何尺度：读首站可读点云，返回
    ``{"diag": 包围盒对角线, "spacing": 典型点间距}``（单位：与输入同）。

    优先用原始数据（``raw_paths``），再回退各阶段已知产物（续跑场景下原始文件
    可能已离线）；场景②（纯照片）没有激光输入，几何**只在 S1 之后才存在**，
    因此必须把 ``10_dense/<item>/fused_dense.ply`` 也纳入候选，否则探针恒失败。
    全部失败返回全 0，调用方据此退化为「不缩放」。
    """
    from h3d.core.scale import bbox_diagonal, estimate_spacing
    from h3d.io import load_point_cloud
    import numpy as np

    candidates = list(task.raw_paths) + list(task.outputs_of("M2")) \
        + list(task.outputs_of("M1"))
    for rel in (os.path.join("10_dense", task.item_id, "fused_dense.ply"),
                os.path.join("30_dense", task.item_id, f"{task.item_id}.ply")):
        cand = os.path.join(task.root, rel)
        if os.path.exists(cand) and cand not in candidates:
            candidates.append(cand)
    for path in candidates:
        try:
            pcd = load_point_cloud(path)
            pts = np.asarray(pcd.points)
            diag = bbox_diagonal(pts)
            if diag > 0.0:
                return {"diag": diag, "spacing": estimate_spacing(pts),
                        "source": os.path.basename(path)}
        except Exception as exc:  # noqa: BLE001 - 探测失败不应阻断流程
            log.debug("尺度探测跳过 %s：%s", path, exc)
    if not candidates:
        # 场景②首跑：几何只在 S1 稠密重建之后才存在，"暂时探不到"是**正常状态**。
        # 原实现一律打 WARNING「尺度探测失败（无可用点云）」——5 件批处理里
        # 第 3 件还没跑 S1 就先报一条告警，用户以为出错了（实机 2026-10-24）。
        log.info("尺度探测跳过（item=%s）：尚无几何产物（S1 未完成，"
                 "跑完后再定尺度）", task.item_id)
        return {"diag": 0.0, "spacing": 0.0, "pending": True}
    log.warning("尺度探测失败（item=%s，%d 个候选点云均不可读）——退化为不缩放",
                task.item_id, len(candidates))
    return {"diag": 0.0, "spacing": 0.0}


def _has_metric_anchor(task: Task) -> bool:
    """本件是否有**度量基准**（能否把模型坐标当作米）。

    场景① 的激光扫描件是米制数据，尺度自适应（``factor = diag / reference_diag``）
    有意义；场景② 的 SfM 重建是**任意单位**，把任意单位的对角线除以米制基准
    3.7 m 得到的"因子"没有任何物理含义——实机 2026-10-18：该数据 diag=14.6
    （SfM 单位），因子 3.96 → 体素被放大 4 倍，等于把唯一的几何源无理由粗化。
    因此无度量基准时一律不做尺度缩放，并在日志与指标里写明原因。
    """
    return bool(task.raw_paths)


def probe_geometry_diagonal(task: Task) -> float:
    """兼容入口：只取包围盒对角线。"""
    return float(probe_geometry(task).get("diag", 0.0))


def _applied_voxel(cfg) -> float:
    """生效配置里的降采样体素（米）；取不到时按 0 处理。"""
    pre = getattr(cfg, "preprocess", None)
    try:
        return float(getattr(pre, "voxel_down_sample", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def resolve_task_config(task: Task, cfg) -> tuple[Any, dict]:
    """解算任务的「生效配置」：按几何尺度线性缩放距离类参数。

    两步：
        1. **尺度缩放**：``参数 × (diag / reference_diag)``；
        2. **点间距下限**：``参数 = max(上式, k × 点间距)``——防止稀疏点云
           在小尺度下 FPFH 半径小于点间距、邻域退化导致配准失败。

    返回 ``(cfg_effective, info)``。``info`` 含 ``factor`` / ``diag`` /
    ``spacing`` / ``changes`` / ``applied``，供日志、manifest 与报表溯源。

    未启用、探测失败或无需调整时**原样返回入参配置对象**（零拷贝）。
    """
    from h3d.core.scale import (apply_spacing_floor, describe, resolve_factor,
                                scaled_config)

    scale_cfg = getattr(cfg, "scale", None)
    if scale_cfg is None or not scale_cfg.enabled:
        return cfg, {"factor": 1.0, "diag": 0.0, "spacing": 0.0,
                     "changes": [], "applied": False, "reason": "disabled"}

    # 无度量基准（场景② 纯照片）：坐标是 SfM 任意单位，缩放无物理意义
    if not _has_metric_anchor(task):
        geom = probe_geometry(task)
        if geom.get("pending"):
            log.info("尺度自适应跳过（item=%s）：SfM 几何尚未生成"
                     "（任意单位，距离类参数按模型单位生效）", task.item_id)
        else:
            log.info("尺度自适应跳过（item=%s）：几何来自 SfM 重建，"
                     "坐标为任意单位，距离类参数按模型单位生效（diag=%.4f）",
                     task.item_id, geom.get("diag", 0.0))
        return cfg, {"factor": 1.0, "diag": float(geom.get("diag", 0.0)),
                     "spacing": float(geom.get("spacing", 0.0)),
                     "changes": [], "applied": False,
                     "reason": "uncalibrated_sfm_units"}

    geom = probe_geometry(task)
    diag, spacing = geom["diag"], geom["spacing"]
    if spacing > 0.0:
        log.info("  点间距估计 = %.6f m", spacing)

    factor = resolve_factor(diag, scale_cfg)
    changes: list[dict] = []
    if abs(factor - 1.0) >= 1e-12:
        cfg, changes = scaled_config(cfg, factor)

    # 点间距下限（对正常点云不产生改动）
    cfg, floor_changes = apply_spacing_floor(cfg, spacing)
    if floor_changes:
        log.info("  点间距下限生效（%d 项）：%s",
                 len(floor_changes), describe(floor_changes))

    # 体素 < 点间距时降采样是**空操作**（每个体素本来就最多一个点）。
    # 2026-10-18：转向 5~30 cm 小件后这是常见情形——界面档位是「基准器物
    # 上的米制值」，尺度自适应按对角线等比缩小，而真实扫描点间距不随之缩小，
    # 于是「中档」在小件上落到 0.2~0.7 mm、根本减不了点。不打日志的话
    # 用户只看到 M1 点数没变，无从判断是参数无效还是数据本来就好。
    if spacing > 0.0 and _applied_voxel(cfg) < spacing:
        log.warning(
            "  体素 %.3f mm < 点间距 %.3f mm —— 降采样实际不生效（等于原样输入）；"
            "如需减点请把体素调到 ≥ 2×点间距（约 %.3f mm）",
            _applied_voxel(cfg) * 1000.0, spacing * 1000.0, spacing * 2000.0)

    changes = list(changes) + list(floor_changes)

    if not changes:
        return cfg, {"factor": factor, "diag": diag, "spacing": spacing,
                     "changes": [], "applied": False, "reason": "no_change"}

    log.info("尺度自适应：item=%s 包围盒对角线=%.4f m -> 因子=%.4f（%d 项参数调整）",
             task.item_id, diag, factor, len(changes))
    log.info("  生效参数：%s", describe(changes))
    return cfg, {"factor": factor, "diag": diag, "spacing": spacing,
                 "changes": changes, "applied": True}


def _record_scale(task: Task, info: dict) -> None:
    """把尺度信息登记到任务对象（供 manifest / 报表读取）。"""
    task.scale_factor = float(info.get("factor", 1.0))
    task.scale_diag = float(info.get("diag", 0.0))
    task.scale_info = {k: v for k, v in info.items() if k != "changes"}
    task.scale_changes = list(info.get("changes") or [])


def _log_stage_start(stage_id: str, item_id: str) -> None:
    """阶段开始日志（GUI 进度页靠它把状态灯置为"运行中"）。

    实机问题（2026-10-17）：原来只有「完成」日志，S1 稠密重建要跑 50 分钟，
    界面上阶段灯一直停在"等待"、进度条不动，用户判断为"卡住"。
    """
    log.info("▶ %s 开始：%s", stage_id, item_id)


def _stage_enabled(stage_id: str, cfg) -> bool:
    """该阶段本次是否**可能真正执行**（用于决定要不要打「▶ 开始」）。

    实机问题（2026-10-17）：可选阶段默认关闭时，编排层仍无条件打印
    「▶ X1 开始」，但函数立刻返回 SKIPPED——界面会短暂亮起一盏永远不会
    完成的灯，用户误以为在跑。改为执行前预判，关闭的阶段不打开始日志。
    """
    if stage_id == "S1":
        return bool(getattr(cfg.sfm_solve, "enabled", False))
    if stage_id == "P1":
        return (bool(getattr(cfg.photo_texture, "enabled", False))
                and getattr(cfg.texture, "paint_mode", "") != "photo")
    if stage_id == "X1":
        return bool(getattr(cfg.runner, "x1_enabled", False))
    if stage_id == "X2":
        return bool(getattr(cfg.runner, "x2_enabled", False))
    return True     # 主线阶段恒执行


def _inject_scale_metrics(stage_id: str, res: StageResult, info: dict) -> None:
    """把尺度信息注入阶段指标（仅 M1 —— 任务首个几何阶段，代表任务级尺度）。

    这样下游报表 / 论文表格无需改动即可读到「本件实际生效的尺度因子」，
    实现跨尺度实验的可追溯。
    """
    if info.get("applied") and stage_id == "M1" and res is not None:
        try:
            res.metrics["scale_factor"] = round(float(info["factor"]), 6)
            res.metrics["scale_diag_m"] = round(float(info["diag"]), 6)
        except Exception:  # noqa: BLE001 - 指标注入失败不得影响主流程
            pass


# ---------------------------------------------------------------------------
# 并行 worker（进程池；Windows spawn 要求模块级函数 + 可 pickle 参数）
# ---------------------------------------------------------------------------
def _run_task_worker(cfg_dict: dict, task: Task, stages: dict,
                     fail_isolation: bool):
    """子进程 worker：独立处理一件文物的全部阶段，不写共享 manifest。

    返回 (task, results, failures)——主进程统一写 manifest 与失败清单，
    避免多进程并发写同一文件（《软著方案》2.8 workers 并行的工程约束）。

    参数
    ----
    cfg_dict:       PipelineConfig.to_dict()（worker 内 from_dict 重建）。
    task:           Task（pickle 传递）。
    stages:         阶段映射（模块级函数，可 pickle）。
    fail_isolation: 是否失败隔离（false 时异常向上传播）。

    返回
    ----
    (task, results, failures)：results 为 {stage: {status, outputs, metrics,
    elapsed_s, error}} 序列化字典；failures 与主流程同构。
    """
    from h3d.core import PipelineConfig, RecoverableError

    cfg = PipelineConfig.from_dict(cfg_dict)
    cfg, scale_info = resolve_task_config(task, cfg)
    _record_scale(task, scale_info)
    results: dict[str, dict] = {}
    failures: list[dict] = []
    for stage_id, stage_fn in stages.items():
        task.begin_stage(stage_id)
        if _stage_enabled(stage_id, cfg):
            _log_stage_start(stage_id, task.item_id)
        t0 = time.time()
        try:
            res = stage_fn(task, cfg)
            elapsed = time.time() - t0
            _inject_scale_metrics(stage_id, res, scale_info)
            if res.status == TaskStatus.SKIPPED:
                # 可选 stage 默认关闭：跳过，不写入结果（主进程不 record）
                task.mark_skipped(stage_id)
                continue
            task.finish_stage(stage_id, res.outputs, res.metrics, elapsed)
            results[stage_id] = {
                "status": res.status.value,
                "outputs": res.outputs,
                "metrics": res.metrics,
                "elapsed_s": round(elapsed, 4),
                "error": "",
            }
        except RecoverableError as exc:
            # 场景①：可选增强失败 → 降级跳过，主线继续（与主流程同构）
            if _degrades_on_failure(stage_id, task):
                results[stage_id] = _degraded_entry(exc, time.time() - t0)
                task.mark_skipped(stage_id)
                log.warning("可选阶段降级（主线继续）：item=%s stage=%s -> %s",
                            task.item_id, stage_id, str(exc)[:200])
                continue
            task.fail_stage(stage_id, str(exc))
            results[stage_id] = {"status": TaskStatus.FAIL.value,
                                 "outputs": [], "metrics": {}, "elapsed_s": 0.0,
                                 "error": str(exc)}
            failures.append({"item_id": task.item_id, "stage": stage_id,
                             "error_type": type(exc).__name__,
                             "message": str(exc),
                             "time": datetime.now().isoformat(timespec="seconds")})
            # 失败隔离保留完整栈（诊断增强：failure 记录含 traceback）
            import traceback as _tb
            failures[-1]["traceback"] = "".join(
                _tb.format_exception(type(exc), exc, exc.__traceback__))[-1500:]
            log.error("失败隔离：item=%s stage=%s -> %s\n%s",
                      task.item_id, stage_id, type(exc).__name__,
                      failures[-1]["traceback"])
            break
        except Exception as exc:  # noqa: BLE001
            if not fail_isolation:
                raise
            if _degrades_on_failure(stage_id, task):
                results[stage_id] = _degraded_entry(exc, time.time() - t0)
                task.mark_skipped(stage_id)
                log.warning("可选阶段降级（主线继续）：item=%s stage=%s -> %s",
                            task.item_id, stage_id, str(exc)[:200])
                continue
            task.fail_stage(stage_id, str(exc))
            results[stage_id] = {"status": TaskStatus.FAIL.value,
                                 "outputs": [], "metrics": {}, "elapsed_s": 0.0,
                                 "error": str(exc)}
            failures.append({"item_id": task.item_id, "stage": stage_id,
                             "error_type": type(exc).__name__,
                             "message": str(exc),
                             "time": datetime.now().isoformat(timespec="seconds")})
            # 失败隔离保留完整栈（诊断增强：failure 记录含 traceback）
            import traceback as _tb
            failures[-1]["traceback"] = "".join(
                _tb.format_exception(type(exc), exc, exc.__traceback__))[-1500:]
            log.error("失败隔离：item=%s stage=%s -> %s\n%s",
                      task.item_id, stage_id, type(exc).__name__,
                      failures[-1]["traceback"])
            break
    return task, results, failures


# ---------------------------------------------------------------------------
# 批量处理器
# ---------------------------------------------------------------------------
class BatchProcessor:
    """M0 编排内核：按注册阶段调度队列，失败隔离 + 断点续跑 + manifest 溯源。"""

    def __init__(
        self,
        cfg,
        stages: Optional[dict[str, Callable[[Task, Any], StageResult]]] = None,
        manifest: Optional[RunManifest] = None,
    ) -> None:
        self.cfg = cfg
        self.stages: dict[str, Callable[[Task, Any], StageResult]] = stages or {}
        self.manifest = manifest

    # -- 入口 --------------------------------------------------------------
    def run(self, tasks: Optional[list[Task]] = None) -> BatchResult:
        """执行批量调度并落盘 run_manifest.json（**持数据根单实例锁**）。

        同一数据根只允许一个批处理进程：两个进程会互相删除
        ``99_runs/sfm_tmp/<item>/dense``（实机 2026-10-17：旧进程
        ``patch_match_stereo`` 写最后一张深度图时报
        ``Check failed: (file).is_open()``，69 分钟计算作废），并争抢 GPU
        使单张深度图耗时翻倍。锁见 :mod:`h3d.core.lockfile`。
        """
        from h3d.core.lockfile import LockBusyError, acquire_run_lock

        # 开跑前自检（项目作用域）。必须在抢锁**之前**：acquire_run_lock 会
        # 写下自己持有的锁，之后自检就会看到"该根已被自己占用"。
        # 这里只记录结论、不阻断 —— 阻断决策留给 CLI/GUI（编排层不该擅自
        # 改变既有 API 行为，单元测试与嵌入式调用都走这条路）。
        try:
            from h3d.health import already_ran, run_preflight
            if not already_ran("project", _lock_root(self.cfg)):
                rep = run_preflight("project", cfg=self.cfg)
                if not rep.ok:
                    log.error("开跑前自检发现 %d 个阻断项，仍按调用方要求继续"
                              "（CLI/GUI 会拦，API 直调不拦）：%s",
                              len(rep.blockers),
                              "；".join(f"{r.title}：{r.detail}"
                                        for r in rep.blockers))
        except Exception as exc:  # noqa: BLE001 - 自检自身失败不得阻断管线
            log.debug("开跑前自检不可用（忽略）：%s", exc)

        try:
            lock = acquire_run_lock(_lock_root(self.cfg))
        except LockBusyError as exc:
            # 不静默等待：直接拒绝启动，并把「谁在占、为什么不能同时跑」说清楚。
            log.error("拒绝启动：%s", exc)
            raise RecoverableError(str(exc)) from exc
        try:
            return self._run_locked(tasks)
        finally:
            lock.release()

    def _run_locked(self, tasks: Optional[list[Task]] = None) -> BatchResult:
        """实际调度（调用方保证已持有数据根锁）。

        任务未显式提供时，按 ``cfg.project.raw_dir`` 自动发现；
        场景②（纯照片）下 ``00_raw`` 无几何文件，须允许 ``00_photo`` 单独成任务，
        因此 ``allow_photo_only`` 与 ``sfm_solve.enabled`` 联动。
        ``cfg.runner.workers > 1`` 时按文物并行（进程池），主进程统一写 manifest。
        """
        if tasks is not None:
            tasks = list(tasks)
        else:
            tasks = discover_from_config(self.cfg)
        manifest = self.manifest or self._init_manifest()
        failures: list[dict[str, Any]] = []

        workers = int(getattr(self.cfg.runner, "workers", 1) or 1)
        if workers > 1 and len(tasks) > 1:
            tasks, failures = self._run_parallel(tasks, manifest, failures, workers)
        else:
            for task in tasks:
                self._run_task(task, manifest, failures)

        manifest.save(self.cfg.runner.manifest)
        result = BatchResult(tasks=tasks, failures=failures,
                             manifest_path=self.cfg.runner.manifest)
        s = result.summary()
        log.info("批量处理完成：total=%d done=%d failed=%d skipped=%d（workers=%d）",
                 s["total"], s["done"], s["failed"], s["skipped"], workers)
        return result

    # -- 并行调度（《软著方案》2.8 workers） --------------------------------
    def _run_parallel(self, tasks: list[Task], manifest: RunManifest,
                      failures: list[dict], workers: int):
        """进程池按文物并行；worker 不写 manifest，主进程顺序落盘避免写冲突。"""
        from concurrent.futures import ProcessPoolExecutor, as_completed

        cfg_dict = self.cfg.to_dict()
        fail_isolation = bool(self.cfg.runner.fail_isolation)
        done_tasks: dict[str, Task] = {}
        log.info("并行调度：%d 件 / %d worker", len(tasks), workers)
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_run_task_worker, cfg_dict, t, self.stages,
                                fail_isolation): t.item_id
                for t in tasks
            }
            for future in as_completed(futures):
                item_id = futures[future]
                try:
                    task, results, task_failures = future.result()
                except Exception as exc:  # 子进程崩溃 / 序列化失败
                    log.error("worker 异常（item=%s）：%s", item_id, exc)
                    task = _find_task(tasks, item_id)
                    if task is not None:
                        task.fail_stage("M1", f"worker 异常: {exc}")
                    failures.append({"item_id": item_id, "stage": "M1",
                                     "error_type": type(exc).__name__,
                                     "message": str(exc),
                                     "time": datetime.now().isoformat(timespec="seconds")})
                    continue
                done_tasks[item_id] = task
                for stage_id, res in results.items():
                    key = f"{item_id}/{stage_id}"
                    if res["status"] == TaskStatus.DONE.value:
                        manifest.record(key, "done", res["outputs"],
                                        res["elapsed_s"],
                                        **_strip_reserved(res["metrics"]))
                    elif res["status"] == TaskStatus.FAIL.value:
                        manifest.record_fail(key, res["error"])
                failures.extend(task_failures)
        return [done_tasks.get(t.item_id, t) for t in tasks], failures

    # -- 单任务调度 --------------------------------------------------------
    def _run_task(self, task: Task, manifest: RunManifest,
                  failures: list[dict[str, Any]]) -> None:
        """按注册顺序调度某件文物的全部阶段；任一阶段失败即停止该件（失败隔离）。

        断点续跑增强：已完成阶段除 manifest 状态外，还须校验其产物文件仍存在
        （《软著方案》2.7.3 幂等续跑）。任一阶段产物缺失触发该阶段重跑，且
        后续阶段级联重跑（上游产物变化后，下游旧产物视为过期，保证一致性）。
        """
        rerun_chain = False  # 本轮已触发重跑 -> 后续阶段一律不跳过
        # 尺度自适应：按本件的几何尺度解算生效配置（距离类参数线性缩放），
        # 使「参数 / 物体尺寸」之比恒定，同一套配置可覆盖文物与大/中/小件。
        cfg, scale_info = resolve_task_config(task, self.cfg)
        _record_scale(task, scale_info)
        for stage_id, stage_fn in self.stages.items():
            key = f"{task.item_id}/{stage_id}"

            # 断点续跑：已完成 且 产物仍在 且 上游未重跑 -> 跳过
            if (self.cfg.runner.resume and not rerun_chain
                    and manifest.is_done(key) and self._outputs_exist(manifest, key)):
                # 关键：跳过 ≠ 无产物。必须把 manifest 里的产物与指标恢复到
                # task.results，否则下游 M3/M4/M5 读 outputs_of() 得到空列表
                # （实机 2026-10-18：续跑后 M5 报 [E100] 参考点云为空）。
                entry = manifest.stages.get(key) or {}
                restored = {k: v for k, v in entry.items()
                            if k not in _MANIFEST_RESERVED}
                outs = list(entry.get("outputs") or [])
                task.restore_stage(stage_id, outs, restored,
                                   entry.get("elapsed_s") or 0.0)
                log.info("○ %s 跳过：%s（断点续跑，复用 %d 个产物）",
                         stage_id, task.item_id, len(outs))
                continue

            task.begin_stage(stage_id)
            if _stage_enabled(stage_id, cfg):
                _log_stage_start(stage_id, task.item_id)
            t0 = time.time()
            try:
                res = stage_fn(task, cfg)
                elapsed = time.time() - t0
                _inject_scale_metrics(stage_id, res, scale_info)
                if res.status == TaskStatus.SKIPPED:
                    # 可选 stage 默认关闭：跳过、不设 rerun_chain、不写 manifest。
                    #
                    # 2026-10-18：原来这里只有 log.debug。凡是「已打过 ▶ 开始」
                    # （_stage_enabled 为真）却返回 SKIPPED 的阶段（S1 无照片、
                    # X1 无参考期、X2 无纹理图、P1 与 M4 重复），界面永远等不到
                    # 完成行 → 那盏灯橙色卡死、进度条少一格。现在：真跳过必须
                    # 打一条 INFO（带原因），界面据此把灯置灰并计入进度。
                    task.mark_skipped(stage_id)
                    if _stage_enabled(stage_id, cfg):
                        reason = str((res.metrics or {}).get("skip_reason")
                                     or "前置条件不满足")
                        log.info("○ %s 跳过：%s（%s）",
                                 stage_id, task.item_id, reason)
                    else:
                        log.debug("可选 stage 未启用，跳过：%s", key)
                    continue
                rerun_chain = True  # 真正执行才触发级联重跑
                task.finish_stage(stage_id, res.outputs, res.metrics, elapsed)
                manifest.record(key, "done", res.outputs, elapsed,
                                **_strip_reserved(res.metrics))
                # 增量落盘（设计书 40.3「先记 manifest 再落盘」）：
                # 既保证中断可恢复，也让同一轮内的下游阶段（M6 论文表格归档）
                # 能读到最新 manifest——否则首次运行 M6 读不到文件而跳过。
                self._save_manifest(manifest)
            except RecoverableError as exc:
                # 场景①：可选增强失败 → 降级跳过，主线继续（不 break）
                if _degrades_on_failure(stage_id, task):
                    self._degrade_optional(task, stage_id, key, exc, manifest,
                                           time.time() - t0)
                    self._save_manifest(manifest)
                    continue
                self._isolate(task, stage_id, key, exc, failures, manifest,
                              time.time() - t0)
                self._save_manifest(manifest)
                break
            except Exception as exc:  # noqa: BLE001 - 编排层统一兜底
                if not self.cfg.runner.fail_isolation:
                    raise
                if _degrades_on_failure(stage_id, task):
                    self._degrade_optional(task, stage_id, key, exc, manifest,
                                           time.time() - t0)
                    self._save_manifest(manifest)
                    continue
                self._isolate(task, stage_id, key, exc, failures, manifest,
                              time.time() - t0)
                self._save_manifest(manifest)
                break

    def _save_manifest(self, manifest: RunManifest) -> None:
        """增量落盘 manifest（失败不阻断流程——最终 run() 还会再写一次）。"""
        try:
            manifest.save(self.cfg.runner.manifest)
        except OSError as exc:  # noqa: BLE001
            log.warning("manifest 增量落盘失败（继续执行）：%s", exc)

    @staticmethod
    def _outputs_exist(manifest: RunManifest, key: str) -> bool:
        """校验某已完成阶段记录的产物文件是否仍全部存在（断点续跑幂等性）。"""
        entry = manifest.stages.get(key)
        if not entry:
            return False
        outputs = entry.get("outputs") or []
        return all(os.path.exists(p) for p in outputs)

    def _degrade_optional(self, task: Task, stage_id: str, key: str,
                          exc: Exception, manifest: RunManifest,
                          elapsed: float = 0.0) -> None:
        """把可选增强阶段的失败降级为「跳过」，主线 M1~M6 继续。

        与 :meth:`_isolate` 的区别：**不计入 failures**（整件并没有失败），
        但必须在 manifest 里留下 ``degraded=True`` + ``reason``，并在日志里
        WARNING，避免「静默降级」——用户必须能从报表看出该增强没生效。
        """
        reason = str(exc)
        task.mark_skipped(stage_id)
        manifest.record(key, "skipped", [], elapsed,
                        degraded=True, reason=reason[:300])
        log.warning(
            "可选阶段降级（主线继续）：item=%s stage=%s -> %s；"
            "该增强本次不生效，M1~M6 照常执行。"
            "若确需照片能力，请把照片放到 00_photo/%s/ 下。",
            task.item_id, stage_id, reason[:200], task.item_id)

    def _isolate(self, task: Task, stage_id: str, key: str, exc: Exception,
                 failures: list[dict[str, Any]], manifest: RunManifest,
                 elapsed: float = 0.0) -> None:
        """失败隔离：登记失败 + 写入 manifest + 收集到 failures.csv 数据源。

        ``elapsed`` 为失败阶段已消耗的秒数。原实现恒记 0，报表里"耗时"列
        对失败任务永远显示 0.00，掩盖了"卡在某阶段很久才失败"的事实。
        """
        task.fail_stage(stage_id, str(exc))
        manifest.record_fail(key, str(exc), elapsed_s=elapsed)
        failures.append({
            "item_id": task.item_id,
            "stage": stage_id,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "elapsed_s": round(elapsed, 2),
            "time": datetime.now().isoformat(timespec="seconds"),
        })
        import traceback as _tb2
        _stk = "".join(_tb2.format_exception(
            type(exc), exc, exc.__traceback__))[-1500:]
        log.error("失败隔离：item=%s stage=%s -> %s（继续下一件）\n%s",
                  task.item_id, stage_id, type(exc).__name__, _stk)

    # -- manifest 初始化 ---------------------------------------------------
    def _init_manifest(self) -> RunManifest:
        """断点续跑则加载既有 manifest，否则新建并写入配置快照哈希。

        自动创建 manifest 所在父目录，避免首次运行无 90_meta/ 目录时
        ``open(... "w")`` 报 FileNotFoundError。
        """
        path = self.cfg.runner.manifest
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        if self.cfg.runner.resume and os.path.exists(path):
            log.info("断点续跑：加载既有 manifest %s", path)
            return RunManifest.load(path)
        config_sha = sha256_text(
            json.dumps(self.cfg.to_dict(), sort_keys=True, ensure_ascii=False, default=str))
        return RunManifest(config_sha256=config_sha)


# manifest.record 的保留关键字（防 metrics 含保留键导致 TypeError）
_MANIFEST_RESERVED = {"stage", "status", "outputs", "elapsed_s", "item_id"}


def _strip_reserved(metrics: dict) -> dict:
    """过滤 metrics 中的 manifest 保留键（防 record(**metrics) 关键字冲突）。"""
    return {k: v for k, v in (metrics or {}).items() if k not in _MANIFEST_RESERVED}


def _find_task(tasks: list[Task], item_id: str) -> Optional[Task]:
    """按 item_id 查找任务（worker 崩溃兜底用）。"""
    for t in tasks:
        if t.item_id == item_id:
            return t
    return None


__all__ = ["BatchProcessor", "BatchResult", "discover_items", "_run_task_worker"]
