"""h3d.runner.batch_processor : 批量处理器（失败隔离 + 断点续跑 + run_manifest 溯源）。

设计依据：
    《设计书》31 —— M0 命令与编排：解析配置、初始化 manifest、遍历队列、按序调用
        业务模块、记录状态、支持断点续跑与失败隔离；
    《软著方案》2.7 —— 任务发现 / 批量调度 / 断点续跑 / 失败隔离 / run_manifest 溯源；
    《教学版》19.2 —— 编排层主循环（续跑 + 隔离）。

核心类 :class:`BatchProcessor` 是「无人值守批量处理」的执行内核：对每尊造像按
注册的阶段顺序调度，:class:`RecoverableError` 只跳过该尊（失败隔离），其余异常在
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
import json

from .task import Task, TaskStatus, StageResult

log = get_logger("runner.batch_processor")


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


def discover_items(raw_dir: str, formats: Optional[list[str]] = None) -> list[Task]:
    """扫描 ``raw_dir``，按目录命名规约识别文物个体，构建任务列表。

    规则（《软著方案》2.7.1）：
        - 若 ``raw_dir`` 下存在子目录，则每个子目录 = 一尊造像（文物编号 = 子目录名），
          目录内（可嵌套：采集批次 / 设备）的所有匹配文件 = 该尊的采集站点；
        - 否则平铺文件模式下，每个匹配文件 = 一尊造像（文物编号 = 去扩展名文件名）。

    参数
    ----
    raw_dir:  原始数据目录（通常为 ``00_raw``）。
    formats:  允许的文件扩展名列表；省略时默认 ``["ply", "pcd", "obj"]``。

    返回
    ----
    :class:`Task` 列表（按文物编号排序，保证批处理顺序可复现）。
    """
    formats = formats or ["ply", "pcd", "obj"]
    raw_dir = os.path.abspath(raw_dir)
    if not os.path.isdir(raw_dir):
        log.warning("原始数据目录不存在: %s", raw_dir)
        return []

    root = os.path.dirname(raw_dir)  # 项目根目录 = raw_dir 的父目录
    tasks: list[Task] = []
    entries = sorted(os.listdir(raw_dir))
    subdirs = [e for e in entries if os.path.isdir(os.path.join(raw_dir, e))]

    if subdirs:
        for sub in subdirs:
            scans = _scan_files(os.path.join(raw_dir, sub), formats)
            if scans:
                tasks.append(Task(item_id=sub, raw_paths=scans, root=root))
    else:
        for path in _scan_files(raw_dir, formats):
            item_id = os.path.splitext(os.path.basename(path))[0]
            tasks.append(Task(item_id=item_id, raw_paths=[path], root=root))

    log.info("任务发现完成：%d 尊造像", len(tasks))
    return tasks


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
# 并行 worker（进程池；Windows spawn 要求模块级函数 + 可 pickle 参数）
# ---------------------------------------------------------------------------
def _run_task_worker(cfg_dict: dict, task: Task, stages: dict,
                     fail_isolation: bool):
    """子进程 worker：独立处理一尊造像的全部阶段，不写共享 manifest。

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
    results: dict[str, dict] = {}
    failures: list[dict] = []
    for stage_id, stage_fn in stages.items():
        task.begin_stage(stage_id)
        t0 = time.time()
        try:
            res = stage_fn(task, cfg)
            elapsed = time.time() - t0
            task.finish_stage(stage_id, res.outputs, res.metrics, elapsed)
            results[stage_id] = {
                "status": res.status.value,
                "outputs": res.outputs,
                "metrics": res.metrics,
                "elapsed_s": round(elapsed, 4),
                "error": "",
            }
        except RecoverableError as exc:
            task.fail_stage(stage_id, str(exc))
            results[stage_id] = {"status": TaskStatus.FAIL.value,
                                 "outputs": [], "metrics": {}, "elapsed_s": 0.0,
                                 "error": str(exc)}
            failures.append({"item_id": task.item_id, "stage": stage_id,
                             "error_type": type(exc).__name__,
                             "message": str(exc),
                             "time": datetime.now().isoformat(timespec="seconds")})
            break
        except Exception as exc:  # noqa: BLE001
            if not fail_isolation:
                raise
            task.fail_stage(stage_id, str(exc))
            results[stage_id] = {"status": TaskStatus.FAIL.value,
                                 "outputs": [], "metrics": {}, "elapsed_s": 0.0,
                                 "error": str(exc)}
            failures.append({"item_id": task.item_id, "stage": stage_id,
                             "error_type": type(exc).__name__,
                             "message": str(exc),
                             "time": datetime.now().isoformat(timespec="seconds")})
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
        """执行批量调度并落盘 run_manifest.json。

        任务未显式提供时，按 ``cfg.project.raw_dir`` 自动发现。
        ``cfg.runner.workers > 1`` 时按造像并行（进程池），主进程统一写 manifest。
        """
        tasks = list(tasks) if tasks is not None else discover_items(
            self.cfg.project.raw_dir, self.cfg.project.formats)
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
        """进程池按造像并行；worker 不写 manifest，主进程顺序落盘避免写冲突。"""
        from concurrent.futures import ProcessPoolExecutor, as_completed

        cfg_dict = self.cfg.to_dict()
        fail_isolation = bool(self.cfg.runner.fail_isolation)
        done_tasks: dict[str, Task] = {}
        log.info("并行调度：%d 尊 / %d worker", len(tasks), workers)
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
        """按注册顺序调度某尊造像的全部阶段；任一阶段失败即停止该尊（失败隔离）。

        断点续跑增强：已完成阶段除 manifest 状态外，还须校验其产物文件仍存在
        （《软著方案》2.7.3 幂等续跑）。任一阶段产物缺失触发该阶段重跑，且
        后续阶段级联重跑（上游产物变化后，下游旧产物视为过期，保证一致性）。
        """
        rerun_chain = False  # 本轮已触发重跑 -> 后续阶段一律不跳过
        for stage_id, stage_fn in self.stages.items():
            key = f"{task.item_id}/{stage_id}"

            # 断点续跑：已完成 且 产物仍在 且 上游未重跑 -> 跳过
            if (self.cfg.runner.resume and not rerun_chain
                    and manifest.is_done(key) and self._outputs_exist(manifest, key)):
                task.mark_skipped(stage_id)
                log.debug("断点续跑：跳过 %s", key)
                continue

            rerun_chain = True
            task.begin_stage(stage_id)
            t0 = time.time()
            try:
                res = stage_fn(task, self.cfg)
                elapsed = time.time() - t0
                task.finish_stage(stage_id, res.outputs, res.metrics, elapsed)
                manifest.record(key, "done", res.outputs, elapsed,
                                **_strip_reserved(res.metrics))
            except RecoverableError as exc:
                self._isolate(task, stage_id, key, exc, failures, manifest)
                break
            except Exception as exc:  # noqa: BLE001 - 编排层统一兜底
                if not self.cfg.runner.fail_isolation:
                    raise
                self._isolate(task, stage_id, key, exc, failures, manifest)
                break

    @staticmethod
    def _outputs_exist(manifest: RunManifest, key: str) -> bool:
        """校验某已完成阶段记录的产物文件是否仍全部存在（断点续跑幂等性）。"""
        entry = manifest.stages.get(key)
        if not entry:
            return False
        outputs = entry.get("outputs") or []
        return all(os.path.exists(p) for p in outputs)

    def _isolate(self, task: Task, stage_id: str, key: str, exc: Exception,
                 failures: list[dict[str, Any]], manifest: RunManifest) -> None:
        """失败隔离：登记失败 + 写入 manifest + 收集到 failures.csv 数据源。"""
        task.fail_stage(stage_id, str(exc))
        manifest.record_fail(key, str(exc))
        failures.append({
            "item_id": task.item_id,
            "stage": stage_id,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "time": datetime.now().isoformat(timespec="seconds"),
        })
        log.warning("失败隔离：item=%s stage=%s -> %s（继续下一尊）",
                    task.item_id, stage_id, type(exc).__name__)

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
