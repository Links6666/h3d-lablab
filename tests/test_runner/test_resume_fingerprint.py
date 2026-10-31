"""tests.test_runner.test_resume_fingerprint : 内容指纹驱动的续跑（B0 切片 5）。

与 ``test_fingerprint.py`` 的分工：那一个测纯函数，这一个测**真实调度**——
两次 ``BatchProcessor.run()`` 之间改了什么，阶段函数到底有没有被再调用一次。

这里要钉死的三件事：

1. 输入没变 → 一次都不重跑（省算力）；
2. 输入变了（换数据 / 改参数）→ 必须重跑，且日志说明原因（不再静默跳过）；
3. 旧 manifest（无指纹）→ 按旧规则跳过，不能把存量项目全部重算一遍。
"""

from __future__ import annotations

import json
import os

import pytest

from h3d.core import PipelineConfig, RunManifest
from h3d.runner.batch_processor import BatchProcessor
from h3d.runner.task import StageResult, Task, TaskStatus


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _cfg(tmp_path, *, workers_ctrl=1):
    cfg = PipelineConfig()
    cfg.project.raw_dir = str(tmp_path / "00_raw")
    cfg.runner.resume = True
    cfg.runner.fail_isolation = True
    cfg.runner.manifest = str(tmp_path / "90_meta" / "manifest.json")
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
    cfg.report.enabled = False
    return cfg


def _project(tmp_path, content=b"ply-v1", photos=()):
    """造一件文物：00_raw/r1/a.ply（+ 可选照片）。"""
    raw = tmp_path / "00_raw" / "r1"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "a.ply").write_bytes(content)
    photo_dir = tmp_path / "00_photo" / "r1"
    photo_paths = []
    if photos:
        photo_dir.mkdir(parents=True, exist_ok=True)
        for i, data in enumerate(photos):
            p = photo_dir / f"{i}.jpg"
            p.write_bytes(data)
            photo_paths.append(str(p))
    return Task(item_id="r1", raw_paths=[str(raw / "a.ply")],
                photo_paths=photo_paths, root=str(tmp_path))


def _counting_stages(calls):
    """两个总是成功的 mock 阶段，把调用次数记进 ``calls``。"""
    def make(stage_id):
        def fn(task, cfg):
            calls[stage_id] = calls.get(stage_id, 0) + 1
            path = os.path.join(os.path.dirname(cfg.runner.manifest),
                                f"{task.item_id}_{stage_id}.out")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"{stage_id}:{calls[stage_id]}")
            return StageResult(stage_id, TaskStatus.DONE, [path], {"n": 1}, 0.0)
        return fn

    return {"M1": make("M1"), "M3": make("M3")}


def _run(tmp_path, task, calls, cfg=None):
    cfg = cfg or _cfg(tmp_path)
    proc = BatchProcessor(cfg, stages=_counting_stages(calls))
    return proc.run([task])


# ---------------------------------------------------------------------------
# 主行为
# ---------------------------------------------------------------------------
def test_second_run_skips_everything(tmp_path):
    """同参数重跑：两个阶段都不再被调用（这才是「省算力」）。"""
    task = _project(tmp_path)
    calls: dict[str, int] = {}
    _run(tmp_path, task, calls)
    assert calls == {"M1": 1, "M3": 1}

    calls.clear()
    _run(tmp_path, task, calls)
    assert calls == {}


def test_param_change_triggers_rerun(tmp_path):
    """改一个**参与指纹**的参数 → 阶段必须重跑，而不是被静默跳过。"""
    task = _project(tmp_path)
    calls: dict[str, int] = {}
    _run(tmp_path, task, calls)
    calls.clear()

    cfg = _cfg(tmp_path)
    cfg.mesh.poisson_depth = int(cfg.mesh.poisson_depth) + 2
    _run(tmp_path, task, calls, cfg)
    assert calls == {"M1": 1, "M3": 1}


def test_execution_control_change_does_not_rerun(tmp_path):
    """改并发数 / 续跑开关等执行控制段，不该让几何白算一遍。"""
    task = _project(tmp_path)
    calls: dict[str, int] = {}
    _run(tmp_path, task, calls)
    calls.clear()

    cfg = _cfg(tmp_path)
    cfg.runner.workers = 4          # runner 段：只影响怎么跑
    cfg.project.name = "换个名字"    # project 段：只影响去哪找数据
    _run(tmp_path, task, calls, cfg)
    assert calls == {}


def test_photo_change_triggers_rerun(tmp_path):
    """换照片 → 必须重跑（旧判据只看产物在不在，会拿旧纹理当新结果）。"""
    task = _project(tmp_path, photos=[b"jpg-1"])
    calls: dict[str, int] = {}
    _run(tmp_path, task, calls)
    calls.clear()

    from h3d.runner.fingerprint import clear_digest_cache

    (tmp_path / "00_photo" / "r1" / "0.jpg").write_bytes(b"jpg-1-different")
    clear_digest_cache()
    _run(tmp_path, task, calls)
    assert calls == {"M1": 1, "M3": 1}


def test_third_run_still_skips(tmp_path):
    """连跑三次，第三次仍应全跳过。

    这条钉的是「乐观登记上游键」的正确性：若某阶段跳过时没把**记录里的键**
    放进上游，下游键就会每次都不一样，表现为「每次都重跑下游」。
    """
    task = _project(tmp_path)
    calls: dict[str, int] = {}
    for _ in range(3):
        _run(tmp_path, task, calls)
    assert calls == {"M1": 1, "M3": 1}


# ---------------------------------------------------------------------------
# 旧 manifest 兼容
# ---------------------------------------------------------------------------
def test_legacy_manifest_without_key_still_resumes(tmp_path):
    """抹掉 input_key 模拟 1.1.13 及以前的 manifest → 仍按旧规则跳过。

    若这里改成「无指纹就重跑」，用户升级一次就要把存量项目全部重算
    （单件 M4 约 80 秒，30 件就是 40 分钟）。
    """
    task = _project(tmp_path)
    calls: dict[str, int] = {}
    _run(tmp_path, task, calls)

    man_path = tmp_path / "90_meta" / "manifest.json"
    data = json.loads(man_path.read_text(encoding="utf-8"))
    for entry in data["stages"].values():
        entry.pop("input_key", None)
    man_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    calls.clear()
    _run(tmp_path, task, calls)
    assert calls == {}


def test_legacy_record_gets_key_on_rerun(tmp_path):
    """旧记录在一次重跑后应补上指纹，此后就按内容判定。"""
    task = _project(tmp_path)
    calls: dict[str, int] = {}
    _run(tmp_path, task, calls)

    cfg = _cfg(tmp_path)
    cfg.runner.force_rerun = True
    _run(tmp_path, task, calls, cfg)

    data = json.loads((tmp_path / "90_meta" / "manifest.json")
                      .read_text(encoding="utf-8"))
    assert all(e.get("input_key") for e in data["stages"].values())


# ---------------------------------------------------------------------------
# 日志必须能看出「为什么跳过 / 为什么重跑」
# ---------------------------------------------------------------------------
def test_log_distinguishes_skip_reason(tmp_path, caplog):
    task = _project(tmp_path)
    calls: dict[str, int] = {}
    _run(tmp_path, task, calls)

    with caplog.at_level("INFO"):
        _run(tmp_path, task, calls)
    texts = " ".join(r.message for r in caplog.records)
    assert "输入未变化" in texts

    cfg = _cfg(tmp_path)
    cfg.mesh.poisson_depth = int(cfg.mesh.poisson_depth) + 1
    caplog.clear()
    with caplog.at_level("INFO"):
        _run(tmp_path, task, calls, cfg)
    texts = " ".join(r.message for r in caplog.records)
    assert "输入已变化" in texts


# ---------------------------------------------------------------------------
# 档案登记（只在已建档项目上发生）
# ---------------------------------------------------------------------------
def _make_archive(tmp_path, task):
    from h3d.archive import Archive, Artifact, Epoch

    arch = Archive.init(str(tmp_path), Artifact(artifact_id="r1", title="测试件"))
    arch.add_epoch(Epoch(epoch_id="E1", artifact_id="r1",
                         captured_on="2026-10-31", route="A", device="",
                         raw_paths=[], colour_protocol={}, scale_ref={},
                         qc_status="unknown", is_baseline=True))
    return arch


def _real_outputs_stage(stage_id, out_rel):
    """产出真实文件的 mock 阶段（路径与真实布局一致，供档案定位）。"""
    def fn(task, cfg):
        path = os.path.join(task.root, out_rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"data-" + stage_id.encode())
        return StageResult(stage_id, TaskStatus.DONE, [path], {}, 0.0)
    return fn


def test_registers_asset_when_archive_exists(tmp_path):
    task = _project(tmp_path)
    # 真实布局：task.root 是「阶段成果」，档案里的相对路径必须带这一层
    task.root = str(tmp_path / "阶段成果")
    os.makedirs(task.root, exist_ok=True)
    _make_archive(tmp_path, task)
    stages = {
        "M1": _real_outputs_stage("M1", "10_clean/r1/r1_s01.ply"),
        "M3": _real_outputs_stage("M3", "30_dense/r1/r1.ply"),
        "M4": _real_outputs_stage("M4", "50_tex/r1/r1_L1.0_diffuse.png"),
    }
    BatchProcessor(_cfg(tmp_path), stages=stages).run([task])

    from h3d.archive import Archive

    arch = Archive.open(str(tmp_path))
    assets = arch.assets("E1")
    assert len(assets) == 1
    assert assets[0].mesh_path == "阶段成果/30_dense/r1/r1.ply"
    assert assets[0].texture_path == "阶段成果/50_tex/r1/r1_L1.0_diffuse.png"
    assert len(assets[0].content_key) == 64


def test_second_run_with_same_content_does_not_add_version(tmp_path):
    """同内容重复登记 → 命中已有版本（档案不因「又跑了一次」膨胀）。"""
    task = _project(tmp_path)
    _make_archive(tmp_path, task)
    stages = {
        "M3": _real_outputs_stage("M3", "30_dense/r1/r1.ply"),
        "M4": _real_outputs_stage("M4", "50_tex/r1/r1_L1.0_diffuse.png"),
    }
    cfg = _cfg(tmp_path)
    cfg.runner.force_rerun = True        # 强制重算，产物内容不变
    BatchProcessor(cfg, stages=stages).run([task])
    BatchProcessor(cfg, stages=stages).run([task])

    from h3d.archive import Archive

    assert len(Archive.open(str(tmp_path)).assets("E1")) == 1


def test_no_archive_dir_created_when_absent(tmp_path):
    """未建档项目：runner **不得**擅自建 archive/（存量项目目录形态不变）。"""
    task = _project(tmp_path)
    stages = {"M3": _real_outputs_stage("M3", "30_dense/r1/r1.ply")}
    BatchProcessor(_cfg(tmp_path), stages=stages).run([task])
    assert not (tmp_path / "archive").exists()


def test_archive_failure_does_not_break_run(tmp_path, caplog):
    """档案损坏时建模仍须成功（档案是增量能力，不能反过来拖垮主线）。"""
    task = _project(tmp_path)
    arch_dir = tmp_path / "archive"
    (arch_dir / "assets").mkdir(parents=True)
    (arch_dir / "epochs").mkdir()
    (arch_dir / "artifact.json").write_text("{坏掉的 json", encoding="utf-8")

    stages = {"M3": _real_outputs_stage("M3", "30_dense/r1/r1.ply")}
    with caplog.at_level("WARNING"):
        res = BatchProcessor(_cfg(tmp_path), stages=stages).run([task])
    assert res.tasks[0].status == TaskStatus.DONE
