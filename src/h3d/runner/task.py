"""h3d.runner.task : 任务定义与状态机（M0 编排的基础数据结构）。

设计依据：
    《设计书》23.1 —— 运行状态机（未开始 / 运行中 / 完成 / 失败）；
    《教学版》19 —— M6 批量调度的续跑与失败隔离，本质是「任务级状态机 + 阶段级状态机」。

一个 :class:`Task` 对应一尊造像（文物个体），内部以 ``results`` 字典按阶段记录
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
    """一尊造像的建模任务（发现于 00_raw，贯穿 M1~M6 各阶段）。"""

    item_id: str
    raw_paths: list[str] = field(default_factory=list)   # 采集站点原始文件
    root: str = ""                                       # 项目根目录（00_raw 的父目录）
    status: TaskStatus = TaskStatus.PENDING
    results: dict[str, StageResult] = field(default_factory=dict)

    # -- 阶段状态机 --------------------------------------------------------
    def begin_stage(self, stage: str) -> None:
        """标记某阶段进入运行态。"""
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
        """断点续跑：已完成阶段标记为跳过，不重复计算。

        整尊全部阶段都被跳过时，任务级状态置为 SKIPPED（统计可见）；
        后续若有阶段实际执行完成，:meth:`finish_stage` 会覆盖为 DONE。
        """
        self.results[stage] = StageResult(stage=stage, status=TaskStatus.SKIPPED)
        if self.status == TaskStatus.PENDING:
            self.status = TaskStatus.SKIPPED

    # -- 查询 --------------------------------------------------------------
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
