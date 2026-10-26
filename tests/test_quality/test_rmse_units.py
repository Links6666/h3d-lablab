"""tests.test_quality.test_rmse_units : M5 中误差口径（2026-10-26 修正）。

实机背景
--------
``E:\\兵马俑照片\\最终测试`` 是纯照片项目（场景②），报表写「中误差 41.16」，
而同一份报表里包围盒对角线只有 6.062 —— 中误差比整个模型还大 6 倍，
自相矛盾。根因：``qc_rmse`` 末尾 ``* 1000``（米 → 毫米）的语义前提是
「模型 1 个单位就是 1 米」；SfM 坐标是任意单位，乘 1000 等于把 1 单位当 1 米。
真值是 0.0412 单位（对角线的 0.68%）。

本组测试锁定的行为
------------------
1. ``qc_rmse_units`` 返回模型单位原值，``qc_rmse`` 仍是 ×1000（兼容）；
2. ``quality.run(metric_units=False)`` 报 ``rmse_rel`` 且 **不判门**；
3. ``check_quality`` 在无米制语义时只判覆盖率；
4. 报表（accuracy.csv / qc_metrics.json / 论文表格）在无锚点时 mm 列填 N/A。
"""
from __future__ import annotations

import csv
import json

import numpy as np
import open3d as o3d
import pytest

from h3d.core import QualityConfig
from h3d.quality import bbox_diag, check_quality, qc_rmse, qc_rmse_units, run
from h3d.quality.gate import build_report


def _plane_pair(offset: float = 0.05, n: int = 40, extent: float = 1.0):
    """返回 (网格, 参考点云)：网格与参考面平行、相距 ``offset``。

    用平面而不是球：RMS 距离就是 2D 网格采样点到参考面的最近邻距离，
    理论上等于 ``offset``（略小，因为采样点落在三角形内部），断言好写。
    """
    xs = np.linspace(-extent, extent, n)
    ys = np.linspace(-extent, extent, n)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.column_stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)])
    ref = o3d.geometry.PointCloud()
    ref.points = o3d.utility.Vector3dVector(pts)

    # 同一网格抬高 offset 作为"模型"，三角化后采样点距参考面恰好 offset
    verts = pts.copy()
    verts[:, 2] += offset
    idx = []
    for j in range(n - 1):
        for i in range(n - 1):
            a = j * n + i
            b = a + 1
            c = a + n
            d = c + 1
            idx.append([a, b, d])
            idx.append([a, d, c])
    mesh = o3d.geometry.TriangleMesh()
    mesh.vertices = o3d.utility.Vector3dVector(verts)
    mesh.triangles = o3d.utility.Vector3iVector(np.asarray(idx, dtype=np.int32))
    mesh.compute_vertex_normals()
    return mesh, ref


class TestRmseUnits:
    def test_units_value_is_model_unit_not_millimetre(self):
        mesh, ref = _plane_pair(offset=0.05)
        units = qc_rmse_units(mesh, ref, samples=4000)
        # 平面间距 0.05 + 网格采样点的横向偏心 → RMS 略大于 0.05，
        # 但绝不该是 50（那是把模型单位当毫米的结果）
        assert 0.05 < units < 0.06
        # sample_points_uniformly 每次随机播种，两次调用的采样点不同 → 留 1% 容差
        assert qc_rmse(mesh, ref, samples=4000) == pytest.approx(
            units * 1000.0, rel=0.01)

    def test_nan_on_empty_input(self):
        empty = o3d.geometry.TriangleMesh()
        pcd = o3d.geometry.PointCloud()
        assert np.isnan(qc_rmse_units(empty, pcd))
        assert np.isnan(qc_rmse(empty, pcd))

    def test_bbox_diag_matches_geometry(self):
        mesh, _ = _plane_pair(offset=0.0, n=3, extent=0.5)
        # 平面 y=x 方向跨度 1.0，对角线 = sqrt(1^2 + 1^2 + 0^2)
        assert bbox_diag(mesh) == pytest.approx(np.sqrt(2.0), rel=1e-6)


class TestRunRespectsMetricUnits:
    def _cfg(self, rmse_mm: float = 5.0, k_mps: float = 3.0):
        return QualityConfig(coverage_threshold=0.5, samples=4000,
                             rmse_threshold_mm=rmse_mm, coverage_k_mps=k_mps)

    def test_metric_units_true_reports_mm_and_gates(self):
        """有锚点：rmse_mm 是真实毫米，且按阈值判门（能判 fail 也能判 pass）。

        参考点云的平均点间距约 0.05（50「mm」），要让中误差门真正生效，
        阈值必须比 MPS 大——这正是 ``rmse_reliable`` 的含义：参考太疏时
        中误差无法分辨阈值量级，不判门。
        """
        mesh, ref = _plane_pair(offset=0.05)
        cfg = self._cfg(rmse_mm=100.0)          # 100mm > MPS≈50mm → 可判
        m = run(mesh, ref, cfg, metric_units=True)
        assert m["rmse_unit"] == "mm"
        assert m["rmse_mm"] == pytest.approx(m["rmse_units"] * 1000.0)
        assert m["metric_units"] is True
        assert m["rmse_reliable"] is True
        assert m["ok"] is True, "54mm < 100mm，应通过"

        # 同一阈值下把模型抬到 0.2（200mm）→ 必须判 fail
        mesh2, ref2 = _plane_pair(offset=0.2)
        m2 = run(mesh2, ref2, self._cfg(rmse_mm=100.0, k_mps=10.0),
                 metric_units=True)
        assert m2["coverage"] > 0.9
        assert m2["ok"] is False, "200mm > 100mm，有锚点时中误差门必须生效"

    def test_metric_units_false_does_not_gate_on_rmse(self):
        """无锚点：rmse 再大也不能判 fail —— 那个数不是毫米。"""
        mesh, ref = _plane_pair(offset=0.05)
        cfg = self._cfg(rmse_mm=100.0)
        m = run(mesh, ref, cfg, metric_units=False)
        assert m["rmse_unit"] == "模型单位"
        assert m["rmse_reliable"] is False
        assert m["ok"] is True, "覆盖率达标即通过，中误差不参与判定"
        assert m["rmse_rel"] == pytest.approx(
            m["rmse_units"] / m["model_bbox_diag"], rel=1e-6)

    def test_no_anchor_never_fails_on_rmse_even_when_huge(self):
        """抬到 0.2（同参数下按 mm 口径会 fail）→ 无锚点时仍须通过。

        ``k_mps`` 放大到 10 让覆盖率这一关先过，剩下的唯一差异就是中误差门：
        有锚点会 fail、无锚点必须 pass。
        """
        mesh, ref = _plane_pair(offset=0.2)
        m = run(mesh, ref, self._cfg(rmse_mm=100.0, k_mps=10.0),
                metric_units=False)
        assert m["coverage"] > 0.9, "前提：覆盖率已达标"
        assert m["ok"] is True

    def test_coverage_still_gates_without_metric_units(self):
        """不判中误差 ≠ 不判质量：覆盖率不足照样 fail。"""
        mesh, ref = _plane_pair(offset=0.05)
        cfg = QualityConfig(coverage_threshold=0.999, samples=2000,
                            coverage_k_mps=0.0001)
        m = run(mesh, ref, cfg, metric_units=False)
        assert m["ok"] is False

    def test_rmse_rel_is_scale_invariant(self):
        """同一形状放大 10 倍，rmse_rel 不变（这才是可跨尺度引用的口径）。"""
        m1, r1 = _plane_pair(offset=0.05, extent=1.0)
        m2, r2 = _plane_pair(offset=0.5, extent=10.0)
        cfg = QualityConfig(coverage_threshold=0.0, samples=3000)
        a = run(m1, r1, cfg, metric_units=False)
        b = run(m2, r2, cfg, metric_units=False)
        assert a["rmse_rel"] == pytest.approx(b["rmse_rel"], rel=0.05)


class TestCheckQualityGate:
    def _caps(self, coverage=0.9, rmse_mm=999.0, reliable=True, units=True):
        return {"coverage": coverage, "rmse_mm": rmse_mm,
                "rmse_reliable": reliable, "metric_units": units}

    def test_no_metric_units_ignores_rmse(self):
        cfg = QualityConfig(coverage_threshold=0.8, rmse_threshold_mm=5.0)
        assert check_quality(self._caps(units=False), cfg) is True

    def test_metric_units_still_gates_rmse(self):
        cfg = QualityConfig(coverage_threshold=0.8, rmse_threshold_mm=5.0)
        assert check_quality(self._caps(rmse_mm=6.0), cfg) is False

    def test_coverage_always_gates(self):
        cfg = QualityConfig(coverage_threshold=0.8)
        assert check_quality(self._caps(coverage=0.5, units=False), cfg) is False


class TestReportsCarryCorrectUnit:
    def _metrics(self, metric_units: bool):
        mesh, ref = _plane_pair(offset=0.05)
        cfg = QualityConfig(coverage_threshold=0.0, samples=3000)
        return run(mesh, ref, cfg, metric_units=metric_units)

    def test_csv_mm_is_na_without_anchor(self, tmp_path):
        m = self._metrics(metric_units=False)
        qc, rpt = tmp_path / "60_qc" / "x", tmp_path / "70_report" / "x"
        build_report("relic", m, QualityConfig(), str(qc), str(rpt))
        with open(rpt / "accuracy.csv", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        head, row = rows[0], rows[1]
        assert "相对中误差(%对角线)" in head
        i_mm = head.index("中误差(mm)")
        i_rel = head.index("相对中误差(%对角线)")
        assert row[i_mm] == "N/A", "无锚点时不能印出伪毫米"
        assert row[i_rel].endswith("%")

        rec = json.loads((qc / "qc_metrics.json").read_text(encoding="utf-8"))
        assert rec["metric_units"] is False
        assert rec["rmse_mm"] == "N/A"
        assert isinstance(rec["rmse_units"], float) and rec["rmse_units"] > 0
        assert isinstance(rec["rmse_rel"], float) and rec["rmse_rel"] > 0

    def test_csv_mm_is_real_mm_with_anchor(self, tmp_path):
        m = self._metrics(metric_units=True)
        qc, rpt = tmp_path / "60_qc" / "y", tmp_path / "70_report" / "y"
        build_report("relic", m, QualityConfig(), str(qc), str(rpt))
        with open(rpt / "accuracy.csv", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        head, row = rows[0], rows[1]
        assert row[head.index("中误差(mm)")] != "N/A"


class TestThesisTableUnit:
    def test_no_anchor_writes_na_and_relative(self, tmp_path):
        from h3d.thesis_tables import export_thesis_table

        manifest = {
            "stages": {
                "relic/M5": {"status": "done", "coverage": 0.95,
                             "rmse_mm": 41.16, "rmse_units": 0.0412,
                             "rmse_rel": 0.0068, "metric_units": False},
                "relic/S1": {"n_registered": 30, "n_photos": 30},
            }
        }
        out = tmp_path / "output"
        out.mkdir()
        (out / "run_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8")
        path = export_thesis_table(str(tmp_path))
        with open(path, encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        head, row = rows[0], rows[1]
        assert "M5相对中误差(%对角线)" in head
        assert row[head.index("M5中误差(mm)")] == "N/A"
        assert row[head.index("M5相对中误差(%对角线)")] == "0.68%"
