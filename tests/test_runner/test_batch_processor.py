"""tests.test_runner.test_batch_processor : 批量调度单元测试。

涵盖任务发现、失败隔离、断点续跑、manifest 记录；用 mock 阶段函数隔离 open3d 依赖，
让调度逻辑在 CI 环境下也能完整测试。
"""
from __future__ import annotations

import json
import os

import pytest

from h3d.core import ConfigError, PipelineConfig, RecoverableError, RunManifest
from h3d.runner.batch_processor import discover_items
from h3d.runner.batch_processor import BatchProcessor, BatchResult
from h3d.runner.task import StageResult, Task, TaskStatus


# ---------------------------------------------------------------------------
# 辅助：用真实 PipelineConfig（runner 需要 to_dict 算 config_sha256）
# ---------------------------------------------------------------------------
def _make_cfg(tmp_path, *, fail_isolation=True, resume=True, manifest_name="manifest.json"):
    cfg = PipelineConfig()
    cfg.project.raw_dir = str(tmp_path / "00_raw")
    cfg.project.formats = ["ply", "pcd", "obj"]
    cfg.runner.fail_isolation = fail_isolation
    cfg.runner.resume = resume
    cfg.runner.manifest = str(tmp_path / "90_meta" / manifest_name)
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
    cfg.report.enabled = False
    return cfg


# ---------------------------------------------------------------------------
# 任务发现
# ---------------------------------------------------------------------------
class TestDiscoverItems:
    def test_nested_dirs_each_become_a_task(self, tmp_path):
        """00_raw/<item>/*.ply：每个子目录 = 一件文物（《软著方案》2.7.1）。"""
        raw = tmp_path / "00_raw"
        (raw / "rock001").mkdir(parents=True)
        (raw / "rock001" / "a.ply").write_bytes(b"ply")
        (raw / "rock001" / "b.ply").write_bytes(b"ply")
        (raw / "rock002").mkdir()
        (raw / "rock002" / "a.ply").write_bytes(b"ply")

        tasks = discover_items(str(raw), ["ply"])
        ids = [t.item_id for t in tasks]
        assert ids == ["rock001", "rock002"]
        assert len(tasks[0].raw_paths) == 2
        assert len(tasks[1].raw_paths) == 1
        # 项目根 = raw_dir 的父目录
        assert tasks[0].root == str(tmp_path)

    def test_flat_layout_each_file_becomes_a_task(self, tmp_path):
        """00_raw/*.ply 平铺：每个文件 = 一件文物。"""
        raw = tmp_path / "00_raw"
        raw.mkdir(parents=True)
        (raw / "a.ply").write_bytes(b"ply")
        (raw / "b.ply").write_bytes(b"ply")
        tasks = discover_items(str(raw), ["ply"])
        assert [t.item_id for t in tasks] == ["a", "b"]
        assert all(len(t.raw_paths) == 1 for t in tasks)

    def test_format_filter(self, tmp_path):
        raw = tmp_path / "00_raw"
        raw.mkdir(parents=True)
        (raw / "a.ply").write_bytes(b"ply")
        (raw / "b.txt").write_text("not a point cloud")
        tasks = discover_items(str(raw), ["ply"])
        assert len(tasks) == 1

    def test_missing_dir_returns_empty(self, tmp_path):
        tasks = discover_items(str(tmp_path / "nope"), ["ply"])
        assert tasks == []

    def test_subdir_with_no_matches_skipped(self, tmp_path):
        raw = tmp_path / "00_raw"
        (raw / "rock001").mkdir(parents=True)
        (raw / "rock001" / "readme.txt").write_text("nope")
        tasks = discover_items(str(raw), ["ply"])
        assert tasks == []


# ---------------------------------------------------------------------------
# BatchProcessor：编排主循环
# ---------------------------------------------------------------------------
def _ok_stage(stage_id):
    """总是成功的 mock 阶段（输出为真实文件，供断点续跑产物校验）。"""
    def fn(task, cfg):
        path = os.path.join(os.path.dirname(cfg.runner.manifest) or ".",
                            f"{task.item_id}_{stage_id}.out")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("ok")
        return StageResult(stage_id, TaskStatus.DONE, [path],
                           {"n": 1}, 0.0)
    return fn


def _fail_stage(stage_id, exc=None):
    """总是失败的 mock 阶段（默认抛 RecoverableError，触发失败隔离）。"""
    def fn(task, cfg):
        raise (exc or RecoverableError(f"{stage_id} 故意失败"))
    return fn


class TestBatchProcessorHappyPath:
    def test_all_done_writes_manifest(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        tasks = [Task(item_id="r1", raw_paths=["a.ply"]),
                 Task(item_id="r2", raw_paths=["b.ply"])]
        stages = {"M1": _ok_stage("M1"), "M2": _ok_stage("M2")}
        result = BatchProcessor(cfg, stages=stages).run(tasks)

        assert result.done == 2
        assert result.failed == 0
        assert result.manifest_path == cfg.runner.manifest
        assert os.path.exists(cfg.runner.manifest)

        manifest = RunManifest.load(cfg.runner.manifest)
        assert manifest.is_done("r1/M1")
        assert manifest.is_done("r2/M2")

    def test_discover_then_run(self, tmp_path):
        """真实任务发现 + 调度（无 open3d 依赖，用 mock 阶段）。"""
        raw = tmp_path / "00_raw"
        (raw / "r1").mkdir(parents=True)
        (raw / "r1" / "a.ply").write_bytes(b"ply")
        cfg = _make_cfg(tmp_path)
        result = BatchProcessor(cfg, stages={"M1": _ok_stage("M1")}).run()
        assert result.done == 1


class TestFailIsolation:
    def test_recoverable_error_skips_remaining_stages_for_item(self, tmp_path):
        cfg = _make_cfg(tmp_path, fail_isolation=True)
        tasks = [Task(item_id="r1"), Task(item_id="r2")]

        def m1(task, cfg):
            if task.item_id == "r1":
                raise RecoverableError("M1 失败")
            return StageResult("M1", TaskStatus.DONE, ["a"], {"ok": True}, 0.0)

        def m2(task, cfg):
            return StageResult("M2", TaskStatus.DONE, ["b"], {}, 0.0)

        result = BatchProcessor(cfg, stages={"M1": m1, "M2": m2}).run(tasks)

        # r1 M1 失败 -> r1 任务失败，M2 未执行；r2 正常
        assert result.failed == 1
        assert result.done == 1
        assert tasks[0].status == TaskStatus.FAIL
        assert tasks[0].results["M1"].status == TaskStatus.FAIL
        assert "M2" not in tasks[0].results  # M2 未执行
        # r2 M1 + M2 正常
        assert tasks[1].status == TaskStatus.DONE
        assert tasks[1].is_done("M1") and tasks[1].is_done("M2")
        # 失败清单
        assert len(result.failures) == 1
        f = result.failures[0]
        assert f["item_id"] == "r1" and f["stage"] == "M1"
        assert f["error_type"] == "RecoverableError"
        assert "M1 失败" in f["message"]

    def test_non_recoverable_isolated_when_fail_isolation_true(self, tmp_path):
        """fail_isolation=True 时非可恢复异常也被隔离（不崩溃）。"""
        cfg = _make_cfg(tmp_path, fail_isolation=True)
        tasks = [Task(item_id="r1"), Task(item_id="r2")]

        def m1(task, cfg):
            raise ValueError("unexpected")

        result = BatchProcessor(cfg, stages={"M1": m1}).run(tasks)
        # 两个任务都失败，但都被隔离（程序未崩溃），且失败清单齐全
        assert result.failed == 2
        assert result.done == 0
        assert len(result.failures) == 2
        assert all(f["error_type"] == "ValueError" for f in result.failures)

    def test_non_recoverable_raises_when_fail_isolation_false(self, tmp_path):
        cfg = _make_cfg(tmp_path, fail_isolation=False)
        tasks = [Task(item_id="r1")]

        def m1(task, cfg):
            raise ValueError("boom")

        with pytest.raises(ValueError):
            BatchProcessor(cfg, stages={"M1": m1}).run(tasks)


class TestResume:
    def test_resume_skips_done_stages(self, tmp_path):
        cfg = _make_cfg(tmp_path, resume=True)
        tasks = [Task(item_id="r1"), Task(item_id="r2")]

        # 第一次运行：使用 _ok_stage("M1")，输出真实文件 r{ID}_M1.out
        first = BatchProcessor(cfg, stages={"M1": _ok_stage("M1")}).run(tasks)
        assert first.done == 2

        # 第二次运行：换 stage 验证已完成阶段被跳过（若真被重跑，输出名会变）
        def overwriting_m1(task, cfg):
            return StageResult("M1", TaskStatus.DONE,
                               [task.item_id + "_v2"], {"v": 2}, 0.0)

        second = BatchProcessor(cfg, stages={"M1": overwriting_m1}).run(tasks)
        # 全部标记 SKIPPED（断点续跑生效：产物仍在则跳过）
        assert all(t.results["M1"].status == TaskStatus.SKIPPED for t in second.tasks)
        # 产物仍是第一次的（r1_M1.out），未被第二次的 overwriting_m1 覆盖
        m = RunManifest.load(cfg.runner.manifest)
        assert m.stages["r1/M1"]["outputs"] == [
            os.path.join(os.path.dirname(cfg.runner.manifest), "r1_M1.out")]
        assert m.stages["r2/M1"]["outputs"] == [
            os.path.join(os.path.dirname(cfg.runner.manifest), "r2_M1.out")]

    def test_resume_reruns_when_outputs_missing(self, tmp_path):
        """断点续跑：产物缺失时该阶段重跑（幂等性校验，修复的关键行为）。"""
        cfg = _make_cfg(tmp_path, resume=True)
        tasks = [Task(item_id="r1")]
        first = BatchProcessor(cfg, stages={"M1": _ok_stage("M1")}).run(tasks)
        assert first.done == 1

        # 删除产物文件 -> 重跑应触发（不再是跳过）
        out = os.path.join(os.path.dirname(cfg.runner.manifest), "r1_M1.out")
        os.remove(out)

        def stamp_m1(task, cfg):
            path = os.path.join(os.path.dirname(cfg.runner.manifest),
                                f"{task.item_id}_M1.stamp")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("stamp")
            return StageResult("M1", TaskStatus.DONE, [path], {"n": 2}, 0.0)

        second = BatchProcessor(cfg, stages={"M1": stamp_m1}).run(tasks)
        # 重跑生效：结果状态为 DONE（非 SKIPPED），产物已更新
        assert second.tasks[0].results["M1"].status == TaskStatus.DONE
        assert os.path.exists(os.path.join(
            os.path.dirname(cfg.runner.manifest), "r1_M1.stamp"))

    def test_no_resume_re_runs_everything(self, tmp_path):
        cfg = _make_cfg(tmp_path, resume=True)
        tasks = [Task(item_id="r1")]
        BatchProcessor(cfg, stages={"M1": _ok_stage("M1")}).run(tasks)
        # 第二次：resume=False + 替换 manifest -> 全新
        cfg.runner.resume = False
        cfg.runner.manifest = str(tmp_path / "fresh_manifest.json")
        os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
        result = BatchProcessor(cfg, stages={"M1": _ok_stage("M1")}).run(tasks)
        assert result.done == 1
        assert tasks[0].results["M1"].status == TaskStatus.DONE  # 非 SKIPPED


class TestManifestContent:
    def test_config_sha256_populated(self, tmp_path):
        cfg = _make_cfg(tmp_path, resume=False)
        tasks = [Task(item_id="r1")]
        BatchProcessor(cfg, stages={"M1": _ok_stage("M1")}).run(tasks)
        m = RunManifest.load(cfg.runner.manifest)
        assert len(m.config_sha256) == 64  # sha256 hex

    def test_metrics_propagate_to_manifest(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        tasks = [Task(item_id="r1")]

        def m1(task, cfg):
            return StageResult("M1", TaskStatus.DONE, ["a"], {"points_out": 1234}, 0.1)

        BatchProcessor(cfg, stages={"M1": m1}).run(tasks)
        m = RunManifest.load(cfg.runner.manifest)
        # 业务指标透传
        assert m.stages["r1/M1"]["points_out"] == 1234
        # 编排器测得的 wall time >= 0
        assert m.stages["r1/M1"]["elapsed_s"] >= 0.0
