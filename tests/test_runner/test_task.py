"""tests.test_runner.test_task : Task 状态机单元测试。"""
from __future__ import annotations

import pytest

from h3d.runner.task import StageResult, Task, TaskStatus


class TestTaskStatus:
    def test_string_values(self):
        """状态值稳定为字符串，可直接序列化到 manifest。"""
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.DONE.value == "done"
        assert TaskStatus.FAIL.value == "fail"
        assert TaskStatus.SKIPPED.value == "skipped"


class TestTaskStateMachine:
    def test_initial_pending(self):
        t = Task(item_id="rock001", raw_paths=["a.ply"])
        assert t.status == TaskStatus.PENDING
        assert t.results == {}

    def test_begin_stage_sets_running(self):
        t = Task(item_id="x")
        t.begin_stage("M1")
        assert t.status == TaskStatus.RUNNING
        assert t.results["M1"].status == TaskStatus.RUNNING

    def test_finish_stage_sets_done(self):
        t = Task(item_id="x")
        t.begin_stage("M1")
        t.finish_stage("M1", outputs=["a.ply"], metrics={"n": 1}, elapsed_s=0.5)
        assert t.status == TaskStatus.DONE
        assert t.is_done("M1")
        assert t.outputs_of("M1") == ["a.ply"]
        assert t.metrics_of("M1") == {"n": 1}

    def test_fail_stage_sets_fail(self):
        t = Task(item_id="x")
        t.begin_stage("M1")
        t.fail_stage("M1", "空点云")
        assert t.status == TaskStatus.FAIL
        assert t.results["M1"].error == "空点云"
        # 失败后 finish 不会回退任务状态
        t.finish_stage("M2", [], {}, 0.0)
        assert t.status == TaskStatus.FAIL

    def test_mark_skipped(self):
        t = Task(item_id="x")
        t.mark_skipped("M1")
        assert t.results["M1"].status == TaskStatus.SKIPPED
        # 整件全跳过 -> 任务级状态 SKIPPED（统计可见）；后续 DONE 会覆盖
        assert t.status == TaskStatus.SKIPPED

    def test_mark_skipped_does_not_override_done(self):
        t = Task(item_id="x")
        t.finish_stage("M1", [], {"n": 1}, 0.0)
        t.mark_skipped("M2")  # 部分跳过：不改变已有 DONE 状态
        assert t.status == TaskStatus.DONE

    def test_is_done_false_when_pending(self):
        t = Task(item_id="x")
        assert not t.is_done("M1")

    def test_outputs_of_unknown_stage(self):
        t = Task(item_id="x")
        assert t.outputs_of("M1") == []
        assert t.metrics_of("M1") == {}


class TestStageResult:
    def test_defaults(self):
        r = StageResult(stage="M1")
        assert r.status == TaskStatus.PENDING
        assert r.outputs == []
        assert r.metrics == {}
        assert r.elapsed_s == 0.0
        assert r.error == ""
