"""tests.test_runner.test_scene1_degrade : 场景①（带扫描件）降级与融合门控。

背景（2026-10-17 实机遍历）：照片场景（场景②）暴露的工程问题，在**扫描件场景
（场景①）会变形为另外三类缺陷**。本模块锁定修复后的行为：

    1) 只有扫描件、没有照片时，S1 不得连累主线。
       修复前实机：向导模板 ``sfm_solve.enabled=true`` + 用户只放扫描件 →
       S1 抛 ``RecoverableError`` → ``break`` → 整件 ``done=0 failed=1``，
       连激光几何都没能建模，报错路径还指向与项目无关的 ``output/00_photo``。

    2) 有扫描件时勾选「稠密重建」，SfM 稠密点云无增量信息却仍被并入。
       实测：novel=0.0（146453 点全部落在激光点邻域内），合并后
       M3 三角面 19828→33758、M5 rmse 27.52→36.78 mm（+34%）、
       M4 纹理覆盖 5.87%→2.32%。判定（novel 低）与动作（仍合并）不一致。

    3) 色卡定位的 ``00_photo`` 路径比正确路径多一层 ``dirname``，
       X2 永远定位不到色卡，静默退回「纹理随机采样」。
"""

from __future__ import annotations

import os

import pytest

from h3d.core import PipelineConfig, RecoverableError, RunManifest
from h3d.runner import STAGE_REGISTRY, _stage_preprocess
from h3d.runner.batch_processor import (
    OPTIONAL_STAGES,
    BatchProcessor,
    _degrades_on_failure,
    _stage_enabled,
)
from h3d.runner.task import StageResult, Task, TaskStatus


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _cfg(tmp_path, *, manifest="manifest.json", resume=False):
    cfg = PipelineConfig()
    cfg.project.raw_dir = str(tmp_path / "00_raw")
    cfg.project.formats = ["ply"]
    cfg.runner.resume = resume
    cfg.runner.fail_isolation = True
    cfg.runner.manifest = str(tmp_path / "90_meta" / manifest)
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
    cfg.report.enabled = False
    return cfg


def _laser_item(tmp_path, item="r1"):
    """建一个场景① 任务：有激光扫描件。"""
    raw = tmp_path / "00_raw" / item
    raw.mkdir(parents=True)
    p = raw / "a.ply"
    p.write_bytes(b"ply")
    return Task(item_id=item, raw_paths=[str(p)], root=str(tmp_path))


def _photo_item(tmp_path, item="p1"):
    """建一个场景② 任务：只有照片、无激光。"""
    return Task(item_id=item, raw_paths=[],
                photo_paths=[str(tmp_path / "00_photo" / item / "a.jpg")],
                root=str(tmp_path))


def _write_cloud(path, points):
    import numpy as np
    import open3d as o3d
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(np.asarray(points, dtype=float))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    o3d.io.write_point_cloud(path, pcd)


def _sphere(n=1500, radius=1.0, offset=(0.0, 0.0, 0.0), seed=0):
    import numpy as np
    rng = np.random.default_rng(seed)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    return v * radius + np.asarray(offset)


# ---------------------------------------------------------------------------
# 1) 降级判定：谁能降级、谁必须失败
# ---------------------------------------------------------------------------
class TestDegradeDecision:
    def test_s1_degrades_when_laser_geometry_exists(self, tmp_path):
        """场景①：S1 失败只是增强失效，主线可继续。"""
        assert _degrades_on_failure("S1", _laser_item(tmp_path)) is True

    def test_s1_must_fail_when_photo_only(self, tmp_path):
        """场景②：S1 是唯一几何来源，失败必须隔离整件。"""
        assert _degrades_on_failure("S1", _photo_item(tmp_path)) is False

    @pytest.mark.parametrize("stage", ["M1", "M2", "M3", "M4", "M5", "M6"])
    def test_mainline_stages_never_degrade(self, tmp_path, stage):
        """主线阶段失败一律整件失败（不得被降级掩盖）。"""
        assert _degrades_on_failure(stage, _laser_item(tmp_path)) is False

    @pytest.mark.parametrize("stage", ["P1", "X1", "X2"])
    def test_optional_enhancements_degrade_with_laser(self, tmp_path, stage):
        assert _degrades_on_failure(stage, _laser_item(tmp_path)) is True

    def test_optional_set_is_exactly_the_enhancements(self):
        """可降级的只能是「增强」：S1 位姿 / P1 照片纹理 / X1 形变 / X2 色差 /
        X3 单期异常。主线阶段失败必须整件失败——那是几何本身没做出来。"""
        assert OPTIONAL_STAGES == frozenset({"S1", "P1", "X1", "X2", "X3"})


# ---------------------------------------------------------------------------
# 2) 调度行为：场景① 无照片必须整件完成
# ---------------------------------------------------------------------------
class TestScene1WithoutPhotos:
    def test_laser_only_item_still_completes(self, tmp_path):
        """S1 失败 → 降级跳过 → M1/M2 照常执行，任务 DONE。"""
        cfg = _cfg(tmp_path)
        task = _laser_item(tmp_path)

        def s1(t, c):
            raise RecoverableError("S1 照片目录为空或不存在")

        def m1(t, c):
            return StageResult("M1", TaskStatus.DONE, ["a"], {}, 0.0)

        def m2(t, c):
            return StageResult("M2", TaskStatus.DONE, ["b"], {}, 0.0)

        result = BatchProcessor(
            cfg, stages={"S1": s1, "M1": m1, "M2": m2}).run([task])

        assert result.done == 1
        assert result.failed == 0
        assert task.status == TaskStatus.DONE
        # S1 记为跳过，且带 degraded 标记
        assert task.results["S1"].status == TaskStatus.SKIPPED
        # 主线照常执行
        assert task.is_done("M1") and task.is_done("M2")
        # 不计入失败清单（整件并没有失败）
        assert result.failures == []

    def test_photo_only_item_fails_when_s1_fails(self, tmp_path):
        """场景②：S1 失败必须整件失败，且后续阶段不执行。"""
        cfg = _cfg(tmp_path)
        task = _photo_item(tmp_path)
        called = []

        def s1(t, c):
            raise RecoverableError("S1 照片目录为空")

        def m1(t, c):
            called.append("M1")
            return StageResult("M1", TaskStatus.DONE, [], {}, 0.0)

        result = BatchProcessor(cfg, stages={"S1": s1, "M1": m1}).run([task])

        assert result.failed == 1
        assert result.done == 0
        assert task.status == TaskStatus.FAIL
        assert called == []                     # M1 未执行
        assert len(result.failures) == 1
        assert result.failures[0]["stage"] == "S1"

    def test_degraded_marker_written_to_manifest(self, tmp_path):
        """降级必须落 manifest（degraded=True + reason），不得静默。"""
        cfg = _cfg(tmp_path)
        task = _laser_item(tmp_path)

        def s1(t, c):
            raise RecoverableError("没有照片")

        def m1(t, c):
            return StageResult("M1", TaskStatus.DONE, [], {}, 0.0)

        BatchProcessor(cfg, stages={"S1": s1, "M1": m1}).run([task])

        m = RunManifest.load(cfg.runner.manifest)
        entry = m.stages["r1/S1"]
        assert entry["status"] == "skipped"
        assert entry["degraded"] is True
        assert "没有照片" in entry["reason"]

    def test_non_recoverable_exception_also_degrades(self, tmp_path):
        """可选阶段抛非 Recoverable 异常同样降级（COLMAP 崩溃等）。"""
        cfg = _cfg(tmp_path)
        task = _laser_item(tmp_path)

        def s1(t, c):
            raise RuntimeError("COLMAP 意外退出 0xC0000005")

        def m1(t, c):
            return StageResult("M1", TaskStatus.DONE, [], {}, 0.0)

        result = BatchProcessor(cfg, stages={"S1": s1, "M1": m1}).run([task])
        assert result.done == 1
        assert result.failures == []
        assert task.is_done("M1")


# ---------------------------------------------------------------------------
# 3) M1 双源融合门控（真实点云）
# ---------------------------------------------------------------------------
class TestDenseMergeGate:
    def test_overlapping_dense_is_not_merged(self, tmp_path):
        """场景①：dense 与激光重合（零新信息）→ 不合并，点数不变。"""
        task = _laser_item(tmp_path)
        base = _sphere(1500, radius=1.0)
        _write_cloud(task.raw_paths[0], base)
        # 同一片几何（数值上重合）→ novel ≈ 0
        _write_cloud(os.path.join(str(tmp_path), "10_dense", "r1",
                                  "fused_dense.ply"), base + 1e-6)

        cfg = _cfg(tmp_path)
        cfg.preprocess.voxel_down_sample = 0.05
        res = _stage_preprocess(task, cfg)

        assert res.metrics["dense_merged"] is False
        # dense 点未计入输入统计
        assert res.metrics["points_in"] == len(base)
        assert res.metrics.get("dense_weight") is None

    def test_disjoint_dense_is_merged(self, tmp_path):
        """dense 提供新区域（远距离）→ 照常合并。"""
        task = _laser_item(tmp_path)
        _write_cloud(task.raw_paths[0], _sphere(1500, radius=1.0))
        # 平移 20 m：完全在激光几何之外 → novel 高
        _write_cloud(os.path.join(str(tmp_path), "10_dense", "r1",
                                  "fused_dense.ply"),
                     _sphere(1500, radius=1.0, offset=(20.0, 0.0, 0.0)))

        cfg = _cfg(tmp_path)
        cfg.preprocess.voxel_down_sample = 0.05
        res = _stage_preprocess(task, cfg)

        assert res.metrics["dense_merged"] is True
        assert res.metrics["points_in"] > 1500     # dense 点计入

    def test_photo_only_item_always_merges_dense(self, tmp_path):
        """场景②：无激光几何时 dense 是唯一几何来源，必须合并。"""
        task = Task(item_id="p1", raw_paths=[], root=str(tmp_path))
        _write_cloud(os.path.join(str(tmp_path), "10_dense", "p1",
                                  "fused_dense.ply"), _sphere(1200))

        cfg = _cfg(tmp_path)
        cfg.preprocess.voxel_down_sample = 0.05
        res = _stage_preprocess(task, cfg)

        assert res.metrics["dense_merged"] is True
        assert len(res.outputs) == 1
        assert os.path.exists(res.outputs[0])


# ---------------------------------------------------------------------------
# 6) 关闭的可选阶段不应打印「▶ 开始」（界面会亮一盏永不完成的灯）
# ---------------------------------------------------------------------------
class TestStageStartLog:
    def test_closed_optional_stages_do_not_log_start(self, tmp_path, caplog):
        """X1/X2 关闭时不得打「▶ X1 开始」。"""
        import logging

        cfg = _cfg(tmp_path)
        cfg.runner.x1_enabled = False
        cfg.runner.x2_enabled = False
        task = _laser_item(tmp_path)

        def x1(t, c):
            return StageResult("X1", TaskStatus.SKIPPED, [], {}, 0.0)

        with caplog.at_level(logging.INFO):
            BatchProcessor(cfg, stages={"X1": x1}).run([task])

        assert "▶ X1 开始" not in caplog.text

    def test_p1_not_logged_when_paint_mode_is_photo(self, tmp_path, caplog):
        """paint_mode=photo 时 P1 必然跳过，不应亮灯（M4 已接管）。"""
        import logging

        cfg = _cfg(tmp_path)
        cfg.photo_texture.enabled = True
        cfg.texture.paint_mode = "photo"
        task = _laser_item(tmp_path)

        with caplog.at_level(logging.INFO):
            BatchProcessor(cfg, stages={"P1": lambda t, c: StageResult(
                "P1", TaskStatus.SKIPPED, [], {}, 0.0)}).run([task])

        assert "▶ P1 开始" not in caplog.text

    def test_enabled_s1_still_logs_start(self, tmp_path, caplog):
        """S1 开启时必须照常打「▶ S1 开始：<item>」——界面靠它解析当前文物。"""
        import logging

        cfg = _cfg(tmp_path)
        cfg.sfm_solve.enabled = True
        task = _laser_item(tmp_path)

        with caplog.at_level(logging.INFO):
            BatchProcessor(cfg, stages={"S1": lambda t, c: StageResult(
                "S1", TaskStatus.DONE, [], {}, 0.0)}).run([task])

        assert "▶ S1 开始：r1" in caplog.text

    def test_mainline_always_enabled(self, tmp_path):
        cfg = _cfg(tmp_path)
        for sid in ("M1", "M2", "M3", "M4", "M5", "M6"):
            assert _stage_enabled(sid, cfg) is True


# ---------------------------------------------------------------------------
# 7) S1 报错必须给出正确的预期布局
# ---------------------------------------------------------------------------
class TestS1ErrorMessage:
    def test_message_shows_correct_expected_layout(self, tmp_path):
        """报错不得只显示回退路径（修复前会指向项目外的 output/00_photo）。"""
        from h3d.sfm_solve import run_sfm_solve

        cfg = _cfg(tmp_path)
        cfg.sfm_solve.enabled = True
        cfg.sfm_solve.colmap_path = "colmap"
        task = _laser_item(tmp_path)
        (tmp_path / "00_photo").mkdir()          # 存在但为空

        with pytest.raises(RecoverableError) as ei:
            run_sfm_solve(task, cfg)

        msg = str(ei.value)
        expect = os.path.join(str(tmp_path), "00_photo", "r1")
        assert expect in msg                     # 正确布局
        assert "sfm_solve.enabled" in msg        # 给出关闭开关的指引


# ---------------------------------------------------------------------------
# 5) X2 照片目录定位（与 S1/P1 同口径）
# ---------------------------------------------------------------------------
class TestX2PhotoDir:
    def test_x2_locates_photos_under_task_root(self, tmp_path):
        """色卡定位必须能找到 <root>/00_photo/<item>/ 下的照片。

        修复前用 dirname(task.root)/00_photo（多一层），永远定位不到，
        静默退回纹理随机采样且无任何指标体现。
        """
        import numpy as np
        from PIL import Image

        from h3d.runner import _stage_colour_quality

        task = _laser_item(tmp_path)
        tex = tmp_path / "50_tex" / "r1"
        tex.mkdir(parents=True)
        Image.fromarray(
            np.full((64, 64, 3), 128, dtype=np.uint8)).save(
                tex / "r1_L1.0_diffuse.png")
        photo = tmp_path / "00_photo" / "r1"
        photo.mkdir(parents=True)
        Image.fromarray(
            np.full((32, 32, 3), 200, dtype=np.uint8)).save(photo / "a.jpg")

        cfg = _cfg(tmp_path)
        cfg.runner.x2_enabled = True

        # 打桩：色卡定位必然命中 → 若目录找错了，桩不会被调用
        hits = []
        import h3d.colour.checker_locator as cl
        orig = cl.locate_colorchecker
        cl.locate_colorchecker = lambda p: (hits.append(p), None)[1]
        try:
            res = _stage_colour_quality(task, cfg)
        finally:
            cl.locate_colorchecker = orig

        assert hits, "X2 没有遍历到任何照片 —— 照片目录定位错误"
        assert os.path.basename(os.path.dirname(hits[0])) == "r1"
        assert res.metrics["checker_source"] == "texture-random"  # 桩返回 None
