"""tests.test_runner.test_parallel : 并发调度（workers>1 进程池）单元测试。"""
from __future__ import annotations

import os

import pytest

from h3d.core import PipelineConfig, RunManifest
from h3d.runner import BatchProcessor
from h3d.runner.task import StageResult, Task, TaskStatus


def _make_cfg(tmp_path, workers=2):
    cfg = PipelineConfig()
    cfg.project.raw_dir = str(tmp_path / "00_raw")
    cfg.project.formats = ["ply"]
    cfg.runner.manifest = str(tmp_path / "90_meta" / "run_manifest.json")
    cfg.runner.workers = workers
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
    return cfg


# 模块级 mock 阶段（进程池 pickle 要求：worker 与 stage 必须可序列化）
def _ok_m1(task, cfg):
    path = os.path.join(os.path.dirname(cfg.runner.manifest) or ".",
                        f"{task.item_id}_M1.out")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("ok")
    return StageResult("M1", TaskStatus.DONE, [path], {"n": 1}, 0.0)


def _ok_m2(task, cfg):
    path = os.path.join(os.path.dirname(cfg.runner.manifest) or ".",
                        f"{task.item_id}_M2.out")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("ok")
    return StageResult("M2", TaskStatus.DONE, [path], {"n": 2}, 0.0)


def _fail_m2(task, cfg):
    raise ValueError("M2 boom")


class TestParallelScheduling:
    def test_parallel_runs_all_tasks(self, tmp_path):
        cfg = _make_cfg(tmp_path, workers=2)
        tasks = [Task(item_id="r1"), Task(item_id="r2"), Task(item_id="r3")]
        stages = {"M1": _ok_m1, "M2": _ok_m2}
        result = BatchProcessor(cfg, stages=stages).run(tasks)
        assert result.done == 3
        assert result.failed == 0
        for t in result.tasks:
            assert t.results["M1"].status == TaskStatus.DONE
            assert t.results["M2"].status == TaskStatus.DONE

    def test_parallel_writes_manifest(self, tmp_path):
        cfg = _make_cfg(tmp_path, workers=2)
        tasks = [Task(item_id="r1"), Task(item_id="r2")]
        stages = {"M1": _ok_m1}
        BatchProcessor(cfg, stages=stages).run(tasks)
        m = RunManifest.load(cfg.runner.manifest)
        assert m.stages["r1/M1"]["status"] == "done"
        assert m.stages["r2/M1"]["status"] == "done"

    def test_parallel_failure_isolation(self, tmp_path):
        cfg = _make_cfg(tmp_path, workers=2)
        tasks = [Task(item_id="r1"), Task(item_id="r2")]
        stages = {"M1": _ok_m1,
                  "M2": _fail_m2}  # M2 全部失败 -> 隔离
        result = BatchProcessor(cfg, stages=stages).run(tasks)
        assert result.done == 0
        assert result.failed == 2
        assert len(result.failures) == 2
        m = RunManifest.load(cfg.runner.manifest)
        assert m.stages["r1/M2"]["status"] == "fail"
        assert "boom" in m.stages["r1/M2"].get("reason", "")  # record_fail 存 reason

    def test_single_task_falls_back_to_serial(self, tmp_path):
        """单件任务：即使 workers>1 也走串行（len(tasks)<=1 不建池）。"""
        cfg = _make_cfg(tmp_path, workers=2)
        tasks = [Task(item_id="only")]
        stages = {"M1": _ok_m1}
        result = BatchProcessor(cfg, stages=stages).run(tasks)
        assert result.done == 1
        assert result.tasks[0].results["M1"].status == TaskStatus.DONE

    def test_parallel_workers_one_serial(self, tmp_path):
        """workers=1 显式串行（不建进程池）。"""
        cfg = _make_cfg(tmp_path, workers=1)
        tasks = [Task(item_id="r1"), Task(item_id="r2")]
        stages = {"M1": _ok_m1}
        result = BatchProcessor(cfg, stages=stages).run(tasks)
        assert result.done == 2
