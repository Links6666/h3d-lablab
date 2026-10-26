"""tests.test_runner.test_stage_logs : 阶段终态必须可被界面观察到。

实机背景（2026-10-18）
----------------------
单站文物的 M2 返回 ``DONE``（``registered=False``）但**不打完成日志**：
界面靠「M2 完成」把灯从「运行中」熄灭，于是:

* M2 灯永远橙色；
* 进度条只到 7/10（M2 不计入已完成阶段）——即使整件已交付。

中小型文物常常只有一站扫描，这不是边缘情况。本测试锁住「单站 M2 必须有
可见的完成日志」，以及「真跳过（条件不满足）必须有 INFO 级原因」——
两者都是 GUI 状态机的唯一输入。
"""
from __future__ import annotations

import os

import pytest

from h3d.core.config import PipelineConfig
from h3d.runner import STAGE_REGISTRY
from h3d.runner.task import Task, TaskStatus


class _Recorder:
    """替代模块级 log，收集 (level, msg) —— 断言日志存在性用。"""

    def __init__(self):
        self.lines: list[str] = []

    def _mk(self, level):
        def _f(msg, *a, **k):
            try:
                self.lines.append(f"[{level}] " + (msg % a if a else str(msg)))
            except Exception:  # noqa: BLE001
                self.lines.append(f"[{level}] {msg}")
        return _f

    def __getattr__(self, name):
        return self._mk(name.upper())


def test_single_station_m2_logs_completion(tmp_path, monkeypatch):
    import h3d.runner as runner_pkg

    rec = _Recorder()
    monkeypatch.setattr(runner_pkg, "log", rec, raising=False)

    raw = tmp_path / "only_one.ply"
    raw.write_text("placeholder")
    task = Task(item_id="relic_one", raw_paths=[str(raw)], root=str(tmp_path))

    res = STAGE_REGISTRY["M2"](task, PipelineConfig())
    assert res.status is TaskStatus.DONE
    assert res.metrics["registered"] is False
    assert any("M2 完成" in s for s in rec.lines), \
        "单站 M2 必须打完成日志，否则界面那盏灯永远橙色"


def test_optional_stage_skip_carries_reason():
    """真跳过的阶段必须带 skip_reason，编排层据此打可读的「○ X 跳过」行。"""
    from h3d.runner import STAGE_REGISTRY

    task = Task(item_id="relic_one", raw_paths=[], root=".")
    cfg = PipelineConfig()          # x1_enabled 默认 False
    res = STAGE_REGISTRY["X1"](task, cfg)
    assert res.status is TaskStatus.SKIPPED
    assert res.metrics.get("skip_reason"), "SKIPPED 必须说明原因"


def test_tiny_object_voxel_warning(tmp_path, monkeypatch):
    """小件器物上「体素 < 点间距」必须 WARNING（否则降采样静默失效）。

    真实场景：5~30 cm 小件 + 界面「基准 2 cm」档 → 尺度自适应按对角线缩到
    亚毫米，而扫描点间距不随之缩小 → 每个体素最多一个点，降采样等于没做。
    用户只看到「M1 前后点数几乎一样」，无从判断是参数无效还是数据本来就好。
    """
    import numpy as np

    from h3d.core.config import PipelineConfig
    from h3d.runner.task import Task

    o3d = pytest.importorskip("open3d")
    import h3d.runner.batch_processor as bp

    rec = _Recorder()
    monkeypatch.setattr(bp, "log", rec, raising=False)

    dims = np.array([0.06, 0.06, 0.10])
    rng = np.random.default_rng(0)
    u = rng.normal(size=(20000, 3))
    u /= np.linalg.norm(u, axis=1, keepdims=True)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(u * dims / 2.0)
    raw = tmp_path / "tiny.ply"
    o3d.io.write_point_cloud(str(raw), pcd)

    task = Task(item_id="tiny", raw_paths=[str(raw)], root=str(tmp_path))
    bp.resolve_task_config(task, PipelineConfig())
    assert any("降采样实际不生效" in s for s in rec.lines), rec.lines


def test_tiny_object_voxel_warning(tmp_path, monkeypatch):
    """小件器物上「体素 < 点间距」必须 WARNING（否则降采样静默失效）。

    真实场景：5~30 cm 小件 + 界面「基准 2 cm」档 → 尺度自适应按对角线缩到
    亚毫米，而扫描点间距不随之缩小 → 每个体素最多一个点，降采样等于没做。
    用户只看到「M1 前后点数几乎一样」，无从判断是参数无效还是数据本来就好。
    """
    import numpy as np

    from h3d.core.config import PipelineConfig
    from h3d.runner.task import Task

    o3d = pytest.importorskip("open3d")
    import h3d.runner.batch_processor as bp

    rec = _Recorder()
    monkeypatch.setattr(bp, "log", rec, raising=False)

    dims = np.array([0.06, 0.06, 0.10])
    rng = np.random.default_rng(0)
    u = rng.normal(size=(20000, 3))
    u /= np.linalg.norm(u, axis=1, keepdims=True)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(u * dims / 2.0)
    raw = tmp_path / "tiny.ply"
    o3d.io.write_point_cloud(str(raw), pcd)

    task = Task(item_id="tiny", raw_paths=[str(raw)], root=str(tmp_path))
    bp.resolve_task_config(task, PipelineConfig())
    assert any("降采样实际不生效" in s for s in rec.lines), rec.lines
