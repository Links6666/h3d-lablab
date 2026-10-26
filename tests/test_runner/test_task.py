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

    # ---------------------------------------------------- r14 终态回归
    def test_mark_disabled_restores_status(self):
        """可选阶段先 begin、后判未启用：任务状态必须回退，不许停在 RUNNING。

        实机（2026-10-26 r14）：全阶段断点续跑跳过的件，因 P1/X1/X2 的
        begin_stage 把状态置成 RUNNING 且 mark_disabled 不动，最终停在 RUNNING，
        汇总 done=0 skipped=0 → 界面判「运行未完成」。
        """
        t = Task(item_id="x")
        t.mark_skipped("S1")                 # 断点续跑命中 → SKIPPED
        t.begin_stage("P1")                  # 打「P1 开始」日志
        assert t.status == TaskStatus.RUNNING
        t.mark_disabled("P1")                # 随后判定：配置里没启用
        assert t.status == TaskStatus.SKIPPED, "未启用阶段不得把任务留在 RUNNING"

    def test_mark_disabled_keeps_done(self):
        """全部跑完后关掉 P1：任务仍是 DONE（未启用阶段不该拉低终态）。"""
        t = Task(item_id="x")
        t.finish_stage("M6", ["o.obj"], {}, 1.0)
        t.begin_stage("P1")
        t.mark_disabled("P1")
        assert t.status == TaskStatus.DONE

    def test_finalize_all_skipped(self):
        """全部跳过 → SKIPPED（批量汇总 skipped=1，界面据此判「无需重跑」）。"""
        from h3d.runner.batch_processor import _finalize_task_status
        t = Task(item_id="x")
        t.mark_skipped("S1")
        t.mark_skipped("M1")
        t.begin_stage("P1")
        t.mark_disabled("P1")
        _finalize_task_status(t)
        assert t.status == TaskStatus.SKIPPED

    def test_finalize_prefers_done_over_skipped(self):
        from h3d.runner.batch_processor import _finalize_task_status
        t = Task(item_id="x")
        t.mark_skipped("S1")
        t.finish_stage("M6", ["o.obj"], {}, 1.0)
        t.begin_stage("X1")
        t.mark_disabled("X1")
        _finalize_task_status(t)
        assert t.status == TaskStatus.DONE

    def test_finalize_never_leaks_running(self):
        """编排异常导致的 RUNNING 残留必须被收敛掉（宁可报失败也不假绿）。"""
        from h3d.runner.batch_processor import _finalize_task_status
        t = Task(item_id="x")
        t.begin_stage("M3")
        assert t.status == TaskStatus.RUNNING
        _finalize_task_status(t)
        assert t.status == TaskStatus.FAIL

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
