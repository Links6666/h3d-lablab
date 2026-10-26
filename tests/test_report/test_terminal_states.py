"""tests.test_report.test_terminal_states : 阶段终态必须是五态（不是四态）。

实机背景（2026-10-26）
----------------------
报表写「完成 7 · 未执行 1 → **部分完成**」。那个「未执行」其实是
``photo_texture.enabled=false`` 的 P1 —— 用户主动关掉的可选阶段。
旧实现里两类完全不同的东西被折进同一格：

* **配置关闭**：用户的意图（``*.enabled=false``、P1 与 M4 互斥）；
* **未执行**：上游失败，阶段从未到达。

于是「我关掉了 P1」和「P1 被上游拖死了」印成同一句话，据此排查必然走错方向。
这是 1.1.7「跳过≠失败」的同类复现，本文件把它锁死：

    完成 / 失败 / 跳过·条件不满足（或降级）/ 未启用 / 未执行 = 五态
    报表分母 = 件数 × 阶段数 − 未启用（配置关闭的不计入评估范围）
"""
from __future__ import annotations

import os

import pytest

from h3d.core import RunManifest
from h3d.report.html_export import generate_html
from h3d.runner.task import Task, TaskStatus


# ------------------------------------------------------- 1) 任务级状态行为
class TestTaskStatusSemantics:
    def test_mark_disabled_does_not_touch_task_status(self):
        """配置关闭是用户的意图：一件器物不该因此变成"跳过"。"""
        task = Task(item_id="r1", root=".")
        task.finish_stage("M1", ["a.ply"], {})
        assert task.status is TaskStatus.DONE
        task.mark_disabled("P1")
        assert task.results["P1"].status is TaskStatus.DISABLED
        assert task.status is TaskStatus.DONE, "关闭可选阶段不得污染任务级状态"

    def test_mark_skipped_still_marks_task(self):
        """条件不满足是运行时事实：整件都没跑时要能在统计里看见。"""
        task = Task(item_id="r1", root=".")
        task.mark_skipped("S1")
        assert task.results["S1"].status is TaskStatus.SKIPPED
        assert task.status is TaskStatus.SKIPPED

    def test_five_states_are_distinct(self):
        vals = {s.value for s in TaskStatus}
        assert {"done", "fail", "skipped", "disabled", "pending"} <= vals


# ------------------------------------------------- 2) 编排层写 manifest
class TestManifestRecordsDisabled:
    def _cfg(self, tmp_path):
        from h3d.core import PipelineConfig

        cfg = PipelineConfig()
        cfg.runner.manifest = str(tmp_path / "90_meta" / "run_manifest.json")
        cfg.report.enabled = False
        os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
        return cfg

    def test_disabled_stages_recorded_not_dropped(self, tmp_path):
        """配置关闭的阶段必须写 manifest（status=disabled），不能"没有记录"。"""
        from h3d.runner import BatchProcessor
        from h3d.runner.task import StageResult

        cfg = self._cfg(tmp_path)
        tasks = [Task(item_id="r1", root=str(tmp_path))]

        def mock_m1(task, cfg):
            return StageResult("M1", TaskStatus.DONE, ["a.ply"], {}, 0.0)

        def mock_s1(task, cfg):
            # sfm_solve.enabled 默认 False -> 走"配置关闭"分支
            return StageResult("S1", TaskStatus.SKIPPED, [], {}, 0.0)

        def mock_p1(task, cfg):
            return StageResult("P1", TaskStatus.SKIPPED, [],
                               {"skip_reason": "photo_texture.enabled=false"}, 0.0)

        BatchProcessor(cfg, stages={"S1": mock_s1, "M1": mock_m1,
                                    "P1": mock_p1}).run(tasks)
        m = RunManifest.load(cfg.runner.manifest)
        for sid in ("S1", "P1"):
            entry = m.stages[f"r1/{sid}"]
            assert entry["status"] == TaskStatus.DISABLED.value
            assert "配置关闭" in entry["skip_reason"], "未启用必须写明是哪一项开关"
        assert m.stages["r1/M1"]["status"] == "done"
        # 任务级状态不受可选阶段影响
        assert tasks[0].status is TaskStatus.DONE

    def test_condition_skip_recorded_with_kind(self, tmp_path):
        """已启用但条件不满足 -> status=skipped + skip_kind=condition。"""
        from h3d.runner import BatchProcessor
        from h3d.runner.task import StageResult

        cfg = self._cfg(tmp_path)
        cfg.sfm_solve.enabled = True     # 启用后没有照片 -> 条件不满足
        tasks = [Task(item_id="r1", root=str(tmp_path))]

        def mock_s1(task, cfg):
            return StageResult("S1", TaskStatus.SKIPPED, [],
                               {"skip_reason": "00_photo 下无照片"}, 0.0)

        def mock_m1(task, cfg):
            return StageResult("M1", TaskStatus.DONE, ["a.ply"], {}, 0.0)

        BatchProcessor(cfg, stages={"S1": mock_s1, "M1": mock_m1}).run(tasks)
        m = RunManifest.load(cfg.runner.manifest)
        entry = m.stages["r1/S1"]
        assert entry["status"] == TaskStatus.SKIPPED.value
        assert entry["skip_kind"] == "condition"
        assert "无照片" in entry["skip_reason"]


# ------------------------------------------------------- 3) 报表五态渲染
def _html(tmp_path, stages: dict) -> str:
    m = RunManifest(config_sha256="abc")
    for key, entry in stages.items():
        m.stages[key] = entry
    path = generate_html(m, None, str(tmp_path / "r.html"), project="五态")
    return open(path, encoding="utf-8").read()


class TestReportFiveStates:
    #: 报表状态表的全部阶段（与 html_export._STAGE_ORDER 一致）
    _ALL = ("S1", "M1", "M2", "M3", "M4", "P1", "M5", "M6")

    @staticmethod
    def _stages(done=(), extra=None) -> dict:
        st = {f"r1/{s}": {"status": "done", "outputs": ["x"], "elapsed_s": 1.0}
              for s in done}
        st.update(extra or {})
        return st

    def test_disabled_is_not_counted_in_denominator(self, tmp_path):
        """「完成 7 · 未执行 1 → 部分完成」的正确形态：通过，且分母扣掉未启用。"""
        txt = _html(tmp_path, self._stages(
            done=("M1", "M2", "M3", "M4", "M5", "M6"),
            extra={"r1/S1": {"status": "disabled",
                             "skip_reason": "配置关闭：sfm_solve.enabled=false"},
                   "r1/P1": {"status": "disabled",
                             "skip_reason": "配置关闭：photo_texture.enabled=false"}}))
        assert "整体判定：通过" in txt
        assert "部分完成" not in txt, "配置关闭不得把结论拉成『部分完成』"
        assert "未启用 2" in txt
        assert "未执行 0" in txt
        # 分母 = 1 件 × 8 阶段 − 2 未启用 = 6
        assert "分母 6" in txt

    def test_condition_skip_labelled(self, tmp_path):
        """条件不满足必须与"未启用"显示成不同文字。"""
        txt = _html(tmp_path, self._stages(
            done=("S1", "M1", "M2", "M3", "M5", "M6"),
            extra={"r1/M4": {"status": "skipped", "skip_kind": "condition",
                             "skip_reason": "无位姿元数据"},
                   "r1/P1": {"status": "disabled",
                             "skip_reason": "配置关闭：photo_texture.enabled=false"}}))
        assert "跳过·条件不满足" in txt
        assert "未启用 1" in txt
        assert "跳过 1" in txt
        assert "整体判定：通过（含跳过）" in txt
        assert "无位姿元数据" in txt   # 原因必须出现在备注列

    def test_degraded_labelled(self, tmp_path):
        txt = _html(tmp_path, self._stages(
            done=("M1", "M2", "M3", "M4", "M5", "M6"),
            extra={"r1/S1": {"status": "skipped", "skip_kind": "degraded",
                             "skip_reason": "COLMAP 未安装"},
                   "r1/P1": {"status": "disabled", "skip_reason": "配置关闭"}}))
        assert "跳过·降级" in txt
        assert "COLMAP 未安装" in txt

    def test_missing_entry_is_pending(self, tmp_path):
        """完全没有记录的阶段仍是「未执行」，结论为部分完成——与未启用区分。"""
        txt = _html(tmp_path, self._stages(
            done=("S1", "M1", "M2", "M3", "M4", "M5"),   # M6 无记录
            extra={"r1/P1": {"status": "disabled", "skip_reason": "配置关闭"}}))
        assert "未执行 1" in txt
        assert "整体判定：部分完成" in txt
        assert "未启用 1" in txt

    def test_fail_still_wins(self, tmp_path):
        txt = _html(tmp_path, self._stages(
            done=("S1", "M1", "M2", "M3", "M5", "M6"),
            extra={"r1/M4": {"status": "fail", "reason": "纹理烘焙失败",
                             "outputs": []},
                   "r1/P1": {"status": "disabled", "skip_reason": "配置关闭"}}))
        assert "整体判定：失败" in txt
        assert "纹理烘焙失败" in txt
