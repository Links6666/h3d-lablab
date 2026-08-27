"""tests.test_quality.test_gate : 质量门判定与报告生成单元测试。"""
from __future__ import annotations

import json
import math
import os

import numpy as np
import open3d as o3d
import pytest

from h3d.core import QualityConfig
from h3d.quality import (
    build_report,
    check_quality,
    run,
)


def _sphere_mesh(resolution=60):
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=1.0, resolution=resolution)
    mesh.compute_vertex_normals()
    return mesh


class TestCheckQuality:
    def test_pass_when_coverage_ok_and_rmse_unreliable(self):
        """稀疏参考：仅判覆盖率门，coverage 达标 -> pass（设计书 §36.4 N/A 语义）。"""
        cfg = QualityConfig(coverage_threshold=0.80)
        metrics = {"coverage": 0.95, "rmse_mm": 40.0, "rmse_reliable": False}
        assert check_quality(metrics, cfg) is True

    def test_fail_when_coverage_low(self):
        cfg = QualityConfig(coverage_threshold=0.80)
        metrics = {"coverage": 0.50, "rmse_mm": 2.0, "rmse_reliable": True}
        assert check_quality(metrics, cfg) is False

    def test_fail_when_rmse_exceeds_and_reliable(self):
        """参考足够密（rmse_reliable=True）且中误差超阈值 -> fail（WW/T 5mm）。"""
        cfg = QualityConfig(coverage_threshold=0.80, rmse_threshold_mm=5.0)
        metrics = {"coverage": 0.99, "rmse_mm": 7.5, "rmse_reliable": True}
        assert check_quality(metrics, cfg) is False

    def test_pass_when_rmse_within_and_reliable(self):
        cfg = QualityConfig(coverage_threshold=0.80, rmse_threshold_mm=5.0)
        metrics = {"coverage": 0.99, "rmse_mm": 3.0, "rmse_reliable": True}
        assert check_quality(metrics, cfg) is True


class TestRunQuality:
    def test_dense_ref_small_scale_rmse_gate_active(self):
        """小尺度球面 + 密参考：MPS <= 阈值，rmse 判门启用。"""
        # 半径 0.03m 小球（30mm），8 万点采样 -> MPS ~0.4mm <= 5mm 阈值
        mesh = o3d.geometry.TriangleMesh.create_sphere(radius=0.03, resolution=40)
        ref = mesh.sample_points_uniformly(80000)
        cfg = QualityConfig(samples=8000)
        m = run(mesh, ref, cfg)
        assert m["rmse_reliable"] is True
        assert m["coverage"] > 0.9
        assert m["ok"] is True

    def test_far_reference_fails(self):
        mesh = _sphere_mesh()
        rng = np.random.default_rng(0)
        far = o3d.geometry.PointCloud()
        far.points = o3d.utility.Vector3dVector(rng.uniform(-5, 5, (2000, 3)))
        m = run(mesh, far, QualityConfig(samples=5000, coverage_k_mps=1.0))
        assert m["coverage"] < 0.5
        assert m["ok"] is False

    def test_empty_mesh_raises_e100(self):
        from h3d.core import InputDataError
        ref = _sphere_mesh().sample_points_uniformly(1000)
        with pytest.raises(InputDataError) as ei:
            run(o3d.geometry.TriangleMesh(), ref, QualityConfig())
        assert ei.value.code == "E100"

    def test_empty_ref_raises_e100(self):
        from h3d.core import InputDataError
        with pytest.raises(InputDataError):
            run(_sphere_mesh(), o3d.geometry.PointCloud(), QualityConfig())


class TestBuildReport:
    def _metrics(self, tmp_path, ok=True):
        mesh = _sphere_mesh()
        ref = mesh.sample_points_uniformly(3000)
        m = run(mesh, ref, QualityConfig(samples=4000))
        assert m["ok"] is ok
        return m

    def test_report_files_written(self, tmp_path):
        mesh = _sphere_mesh()
        ref = mesh.sample_points_uniformly(3000)
        metrics = run(mesh, ref, QualityConfig(samples=4000))
        qc_dir = tmp_path / "60_qc" / "rock001"
        rpt_dir = tmp_path / "70_report" / "rock001"
        files = build_report("rock001", metrics, QualityConfig(),
                             str(qc_dir), str(rpt_dir))
        assert len(files) == 2
        # qc_metrics.json
        with open(files[0], encoding="utf-8") as fh:
            record = json.load(fh)
        assert record["item_id"] == "rock001"
        assert record["status"] == "pass"
        assert record["coverage"] > 0.5
        assert "thresholds" in record
        # accuracy.csv（utf-8-sig）
        with open(files[1], encoding="utf-8-sig") as fh:
            csv_text = fh.read()
        assert "文物编号" in csv_text
        assert "rock001" in csv_text
        assert "pass" in csv_text

    def test_fail_record_status(self, tmp_path):
        """不达标记录 status=fail（供审计，不抛异常）。"""
        mesh = _sphere_mesh()
        rng = np.random.default_rng(0)
        far = o3d.geometry.PointCloud()
        far.points = o3d.utility.Vector3dVector(rng.uniform(-5, 5, (2000, 3)))
        metrics = run(mesh, far, QualityConfig(samples=4000, coverage_k_mps=1.0))
        assert metrics["ok"] is False
        files = build_report("rock002", metrics, QualityConfig(),
                             str(tmp_path / "qc"), str(tmp_path / "rpt"))
        with open(files[0], encoding="utf-8") as fh:
            record = json.load(fh)
        assert record["status"] == "fail"
        assert record["ok"] is False
