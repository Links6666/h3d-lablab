"""tests.test_runner.test_force_rerun : 「强制重算」入口（2026-10-26）。

背景
----
断点续跑是默认行为（``manifest`` 里状态 done + 产物仍在 → 跳过）。但有几类
情况**产物存在也不可信**：

* 位姿改过而深度图残留 —— ``patch_match_stereo`` 见到 ``photometric.bin`` 就
  跳过，rc=0 但点云错乱（2026-10-17 实机踩过）；
* 手工改过中间文件；
* 论文需要复现一次完整耗时。

此前只能让用户自己去删 ``10_dense`` / ``30_dense``，路径写错还会留下半残项目。
现在 ``runner.force_rerun=true``（或 CLI ``--force``）即忽略跳过判定。

本组测试锁定的行为
------------------
1. force_rerun 后**已完成的阶段也要重跑**；
2. 只影响跳过判定，不改共享配置对象（不能把标志泄漏到下一件/下一次运行）；
3. ``sfm_solve.force_redense`` 在强制重算时打开（深度图工作区重建）；
4. ``resume=false`` 与 ``force_rerun`` 的优先级：force_rerun 更彻底。
"""
from __future__ import annotations

import os

from h3d.core import PipelineConfig, RunManifest
from h3d.runner.batch_processor import BatchProcessor, _force_dense_restart
from h3d.runner.task import StageResult, Task, TaskStatus


def _make_cfg(tmp_path, *, force=False, resume=True):
    cfg = PipelineConfig()
    cfg.project.raw_dir = str(tmp_path / "00_raw")
    cfg.runner.resume = resume
    cfg.runner.force_rerun = force
    cfg.runner.manifest = str(tmp_path / "90_meta" / "manifest.json")
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
    cfg.report.enabled = False
    return cfg


def _ok_stage(stage_id):
    def fn(task, cfg):
        path = os.path.join(os.path.dirname(cfg.runner.manifest) or ".",
                            f"{task.item_id}_{stage_id}.out")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("ok")
        return StageResult(stage_id, TaskStatus.DONE, [path], {"n": 1}, 0.0)
    return fn


class TestForceRerun:
    def test_second_run_actually_reruns(self, tmp_path):
        """先跑一遍让 manifest 全部 done，再开强制重算 —— 必须真的重跑。"""
        cfg = _make_cfg(tmp_path)
        tasks = [Task(item_id="r1")]
        BatchProcessor(cfg, stages={"M1": _ok_stage("M1")}).run(tasks)

        # 正常续跑：应该跳过
        again = BatchProcessor(cfg, stages={"M1": _ok_stage("M1")}).run(
            [Task(item_id="r1")])
        assert again.tasks[0].results["M1"].status == TaskStatus.SKIPPED

        # 强制重算：必须 done
        cfg.runner.force_rerun = True
        forced = BatchProcessor(cfg, stages={"M1": _ok_stage("M1")}).run(
            [Task(item_id="r1")])
        assert forced.tasks[0].results["M1"].status == TaskStatus.DONE
        assert forced.done == 1

    def test_force_rerun_overrides_resume_false(self, tmp_path):
        """resume=false 本来就全跑；force_rerun 不能把它变回跳过（互不干扰）。"""
        cfg = _make_cfg(tmp_path, force=True, resume=False)
        BatchProcessor(cfg, stages={"M1": _ok_stage("M1")}).run(
            [Task(item_id="r1")])
        r = BatchProcessor(cfg, stages={"M1": _ok_stage("M1")}).run(
            [Task(item_id="r1")])
        assert r.tasks[0].results["M1"].status == TaskStatus.DONE

    def test_manifest_still_records_outputs(self, tmp_path):
        cfg = _make_cfg(tmp_path, force=True)
        BatchProcessor(cfg, stages={"M1": _ok_stage("M1")}).run(
            [Task(item_id="r1")])
        BatchProcessor(cfg, stages={"M1": _ok_stage("M1")}).run(
            [Task(item_id="r1")])
        m = RunManifest.load(cfg.runner.manifest)
        assert m.stages["r1/M1"]["status"] == "done"
        assert m.stages["r1/M1"]["outputs"]

    def test_flag_does_not_leak_into_shared_config(self, tmp_path):
        """强制重算只改「生效配置」，不能把共享 cfg 写脏。"""
        cfg = _make_cfg(tmp_path, force=True)
        seen = {}

        def m1(task, cfg_eff):
            seen["force_redense"] = cfg_eff.sfm_solve.force_redense
            return _ok_stage("M1")(task, cfg_eff)

        BatchProcessor(cfg, stages={"M1": m1}).run([Task(item_id="r1")])
        assert seen["force_redense"] is True, "S1 深度图应被强制重建"
        assert cfg.sfm_solve.force_redense is False, "共享配置被写脏"


class TestForceDenseRestart:
    def test_returns_copy_with_flag(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        out = _force_dense_restart(cfg)
        assert out is not cfg
        assert out.sfm_solve.force_redense is True
        assert cfg.sfm_solve.force_redense is False

    def test_keeps_photo_reuse_enabled(self, tmp_path):
        """缓存不删：参数没变时复用得到的就是同一次解算结果。"""
        cfg = _make_cfg(tmp_path)
        cfg.sfm_solve.reuse_identical_photos = True
        assert _force_dense_restart(cfg).sfm_solve.reuse_identical_photos is True
