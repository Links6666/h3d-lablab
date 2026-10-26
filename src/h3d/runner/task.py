"""h3d.runner.task : 任务定义与状态机（M0 编排的基础数据结构）。

设计依据：
    《设计书》23.1 —— 运行状态机（未开始 / 运行中 / 完成 / 失败）；
    《教学版》19 —— M6 批量调度的续跑与失败隔离，本质是「任务级状态机 + 阶段级状态机」。

一个 :class:`Task` 对应一件文物（文物个体），内部以 ``results`` 字典按阶段记录
:class:`StageResult`。任务级状态由各阶段状态推导：任一阶段失败则任务失败，全部阶段
完成（或跳过）则任务完成。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class TaskStatus(str, Enum):
    """任务 / 阶段状态机（《设计书》23.1）。"""

    PENDING = "pending"      # 未开始
    RUNNING = "running"      # 运行中
    DONE = "done"            # 完成
    FAIL = "fail"            # 失败
    SKIPPED = "skipped"      # 断点续跑时跳过（已完成）


@dataclass
class StageResult:
    """单阶段运行结果（产出物 + 指标 + 耗时 + 错误）。"""

    stage: str
    status: TaskStatus = TaskStatus.PENDING
    outputs: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    elapsed_s: float = 0.0
    error: str = ""


@dataclass
class Task:
    """一件文物的建模任务（发现于 00_raw，贯穿 M1~M6 各阶段）。"""

    item_id: str
    raw_paths: list[str] = field(default_factory=list)   # 采集站点原始文件
    root: str = ""                                       # 项目根目录（00_raw 的父目录）
    # -- 纯照片模式（场景②，2026-10-17）-------------------------------------
    # 任务发现阶段记录 00_photo/<item>/ 下的图像；为空的 raw_paths 表示该条目
    # 没有激光几何，M1 的几何来源完全依赖 S1 dense 产物。
    photo_paths: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    results: dict[str, StageResult] = field(default_factory=dict)

    # -- 尺度自适应（2026-10-09）------------------------------------------
    # 由 runner 在调度前探测几何尺度并解算，供 manifest / 报表溯源。
    scale_factor: float = 1.0            # 生效缩放因子（1.0 = 未缩放）
    scale_diag: float = 0.0              # 探测到的包围盒对角线（米）
    scale_info: dict[str, Any] = field(default_factory=dict)
    scale_changes: list[dict] = field(default_factory=list)

    # -- 阶段状态机 --------------------------------------------------------
    def begin_stage(self, stage: str) -> None:
        """标记某阶段进入运行态。

        仅在任务尚未完成（PENDING/SKIPPED/RUNNING）时置为 RUNNING——
        已完成（DONE）的任务不得被后续可选 stage（S1/P1 默认跳过）覆盖状态。
        """
        if self.status in (TaskStatus.PENDING, TaskStatus.SKIPPED,
                           TaskStatus.RUNNING):
            self.status = TaskStatus.RUNNING
        self.results[stage] = StageResult(stage=stage, status=TaskStatus.RUNNING)

    def finish_stage(
        self,
        stage: str,
        outputs: Optional[list[str]] = None,
        metrics: Optional[dict[str, Any]] = None,
        elapsed_s: float = 0.0,
    ) -> None:
        """登记某阶段完成：产物、指标与耗时。"""
        self.results[stage] = StageResult(
            stage=stage, status=TaskStatus.DONE,
            outputs=outputs or [], metrics=metrics or {}, elapsed_s=elapsed_s,
        )
        if self.status != TaskStatus.FAIL:
            self.status = TaskStatus.DONE

    def fail_stage(self, stage: str, error: str) -> None:
        """登记某阶段失败，并联动任务级状态为失败（失败隔离据此跳过后续阶段）。"""
        self.results[stage] = StageResult(stage=stage, status=TaskStatus.FAIL, error=error)
        self.status = TaskStatus.FAIL

    def mark_skipped(self, stage: str) -> None:
        """断点续跑/可选 stage：阶段标记为跳过，不重复计算。

        整件全部阶段都被跳过时，任务级状态置为 SKIPPED（统计可见）；
        后续若有阶段实际执行完成，:meth:`finish_stage` 会覆盖为 DONE。
        允许从 RUNNING 回退（begin_stage 后立即 SKIPPED 的可选 stage：
        任务从未真正执行，应回退为 SKIPPED 而非卡在 RUNNING）。
        """
        self.results[stage] = StageResult(stage=stage, status=TaskStatus.SKIPPED)
        if self.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            self.status = TaskStatus.SKIPPED

    # -- 查询 --------------------------------------------------------------
    def restore_stage(
        self,
        stage: str,
        outputs: Optional[list[str]] = None,
        metrics: Optional[dict[str, Any]] = None,
        elapsed_s: float = 0.0,
    ) -> None:
        """断点续跑：恢复既有阶段结果（状态 SKIPPED，但产物 / 指标完整）。

        实机缺陷（2026-10-18）：续跑时旧实现只调 :meth:`mark_skipped`，产物列表
        留空 → 下游阶段 ``task.outputs_of("M2") or task.outputs_of("M1")`` 全为空，
        M5 报 ``[E100] M5 参考点云为空（无 M2/M1 产物）``、尺度探测也退化为不缩放。
        阶段"跳过"只表示不重算，**不等于没有产物**：产物与指标必须从 manifest
        原样恢复，否则续跑语义与全量运行不等价。
        """
        self.results[stage] = StageResult(
            stage=stage, status=TaskStatus.SKIPPED,
            outputs=list(outputs or []), metrics=dict(metrics or {}),
            elapsed_s=float(elapsed_s or 0.0),
        )
        if self.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            self.status = TaskStatus.SKIPPED

    def is_done(self, stage: str) -> bool:
        r = self.results.get(stage)
        return bool(r and r.status == TaskStatus.DONE)

    def outputs_of(self, stage: str) -> list[str]:
        r = self.results.get(stage)
        return list(r.outputs) if r else []

    def metrics_of(self, stage: str) -> dict[str, Any]:
        r = self.results.get(stage)
        return dict(r.metrics) if r else {}


__all__ = ["TaskStatus", "StageResult", "Task"]
