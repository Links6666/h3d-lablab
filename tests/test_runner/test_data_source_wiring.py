"""``runner.data_source`` 的接线：声明 → M1 几何来源 → M5 米制判据 → manifest。

三件事必须同时成立，缺一条用户就会「选了但没生效」：

1. M1 认得声明（``dense_source`` 不再是「有文件就是激光」）；
2. M5 按声明决定米制（退出毫米门、报表按模型单位）；
3. manifest 手写白名单带上判据名（否则报表读不到——r16 的老坑，
   上游算得再对也没用）。

第 3 条是这里最容易被漏掉的一条：``manifest_metrics`` 是手写 dict，
新增指标必须三处同步（计算处 → 白名单 → 模板）。
"""
from __future__ import annotations

import os

import numpy as np
import pytest

from tests._m5_fixture import m5_cfg as _cfg
from tests._m5_fixture import m5_project as _m5_project


class TestM5MetricUnits:
    def test_auto_keeps_legacy_behaviour(self, tmp_path):
        from h3d.runner import _stage_quality

        task, cfg = _m5_project(tmp_path)
        m5 = _stage_quality(task, cfg).metrics
        assert m5["metric_units"] is True
        assert m5["metric_units_reason"] == "auto_with_raw"
        assert m5["data_source_declared"] == "auto"
        assert m5["declared_source_conflict"] is False

    def test_declared_photogrammetry_leaves_the_mm_gate(self, tmp_path):
        """核心新能力：00_raw 有文件，但声明是照片重建件 → 不按毫米判门。"""
        from h3d.runner import _stage_quality

        task, cfg = _m5_project(tmp_path, data_source="photogrammetry")
        m5 = _stage_quality(task, cfg).metrics
        assert m5["metric_units"] is False
        assert m5["metric_units_reason"] == "declared_photogrammetry"
        assert m5["rmse_unit"] == "模型单位"
        # 口径文字必须点明「是声明」，不能混成"SfM 任意单位"（那是另一回事：
        # 读者据此该去跑 SfM，而这里跑多少次 SfM 都不会变米制）
        assert "声明" in m5["rmse_scope"]

    def test_evidence_beats_declared_laser(self, tmp_path):
        """声明 laser 但 M1 判出同源 → 仍不主张米制，并标冲突。"""
        from h3d.runner import _stage_quality

        task, cfg = _m5_project(tmp_path, data_source="laser",
                                m1_dense_source="raw_looks_like_sfm")
        m5 = _stage_quality(task, cfg).metrics
        assert m5["metric_units"] is False
        assert m5["declared_source_conflict"] is True
        assert m5["metric_units_reason"] == "declared_laser_conflicts_with_evidence"

    def test_declared_laser_with_clean_evidence_is_metric(self, tmp_path):
        from h3d.runner import _stage_quality

        task, cfg = _m5_project(tmp_path, data_source="laser",
                                m1_dense_source="laser")
        m5 = _stage_quality(task, cfg).metrics
        assert m5["metric_units"] is True
        assert m5["declared_source_conflict"] is False


class TestManifestWhitelist:
    def test_manifest_carries_the_reason_not_just_the_boolean(self, tmp_path):
        """manifest 是手写白名单：漏写 = 报表读不到（r16 同构）。

        断言点选在「M5 返回的 metrics」上——那个 dict 就是写进 manifest 的内容。
        文件由批量编排落盘，绕开它测才不会把路径/时序问题混进来。
        """
        from h3d.runner import _stage_quality

        task, cfg = _m5_project(tmp_path, data_source="photogrammetry")
        m5 = _stage_quality(task, cfg).metrics
        for key in ("data_source_declared", "metric_units_reason",
                    "metric_units", "declared_source_conflict"):
            assert key in m5, f"manifest 缺 {key}：报表将无从判断口径来源"


class TestM1DenseSource:
    def test_declared_photogrammetry_wins_over_file_presence(self, tmp_path):
        """00_raw 里有文件 ≠ 激光件。声明优先，且不必等 SfM 稠密才判得出。"""
        import open3d as o3d

        from h3d.runner import _stage_preprocess
        from h3d.runner.task import Task

        root = tmp_path / "阶段成果"
        d = root / "00_raw" / "r1"
        os.makedirs(str(d), exist_ok=True)
        rng = np.random.default_rng(0)
        v = rng.normal(size=(3000, 3))
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        pc = o3d.geometry.PointCloud()
        pc.points = o3d.utility.Vector3dVector(v * 0.05)
        pc.paint_uniform_color([0.6, 0.5, 0.4])
        o3d.io.write_point_cloud(str(d / "scan.ply"), pc)

        cfg = _cfg(str(root), "photogrammetry")
        task = Task(item_id="r1", raw_paths=[str(d / "scan.ply")],
                    root=str(root))
        res = _stage_preprocess(task, cfg)
        assert res.metrics["dense_source"] == "declared_photogrammetry"
        assert res.metrics["data_source_declared"] == "photogrammetry"

    def test_auto_still_reports_laser_unverified(self, tmp_path):
        """反方向也要成立：默认值下旧推断链不动（否则这次改的是行为）。"""
        import open3d as o3d

        from h3d.runner import _stage_preprocess
        from h3d.runner.task import Task

        root = tmp_path / "阶段成果"
        d = root / "00_raw" / "r1"
        os.makedirs(str(d), exist_ok=True)
        rng = np.random.default_rng(0)
        v = rng.normal(size=(3000, 3))
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        pc = o3d.geometry.PointCloud()
        pc.points = o3d.utility.Vector3dVector(v * 0.05)
        pc.paint_uniform_color([0.6, 0.5, 0.4])
        o3d.io.write_point_cloud(str(d / "scan.ply"), pc)

        cfg = _cfg(str(root), "auto")
        task = Task(item_id="r1", raw_paths=[str(d / "scan.ply")],
                    root=str(root))
        res = _stage_preprocess(task, cfg)
        assert res.metrics["dense_source"] == "laser_unverified"
        assert res.metrics["data_source_declared"] == "auto"
