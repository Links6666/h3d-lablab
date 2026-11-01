"""tests.test_quality.test_accuracy_x1 : X1 两列必须由 build_report 直接写。

为什么改（2026-11-01）
---------------------
X1 的形变指标原先靠 ``runner._append_x1_to_accuracy`` **事后追加**列——因为 X1
排在 M6 之后，跑完时 ``accuracy.csv`` 早就由 M5 生成好了。

X1/X2 移到 M5 **之前**后，这个前提反了：X1 跑完时 ``accuracy.csv`` 还不存在，
补丁 ``if os.path.exists(acc_csv)`` 直接跳过 → **X1 数字反而进不了表**。所以改成
正规路径：M5 从 manifest 读到 X1 的指标，``build_report`` 一次写全。

这和 r16 那次是同一课：数据流要单向（manifest 是唯一来源，报表读它），
不要靠「谁先跑完谁追加」的时序耦合。
"""
from __future__ import annotations

import csv
import os

import open3d as o3d
import pytest

from h3d.core import QualityConfig
from h3d.quality import build_report, run as quality_run

X1_MAX = "X1最大形变(mm)"
X1_RATIO = "X1变化占比"


def _sphere_mesh(radius=0.05):
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=radius, resolution=8)
    mesh.compute_vertex_normals()
    return mesh


@pytest.fixture(scope="module")
def base_metrics():
    """一份真实跑出来的 M5 指标（而不是手捏 dict——手捏的 key 会和实现脱节）。"""
    mesh = _sphere_mesh()
    ref = mesh.sample_points_uniformly(3000)
    return quality_run(mesh, ref, QualityConfig(samples=4000))


def _rows(tmp_path, base_metrics, extra: dict | None):
    metrics = dict(base_metrics)
    metrics.update(extra or {})
    qc = tmp_path / "60_qc" / "r1"
    rpt = tmp_path / "70_report" / "r1"
    build_report("r1", metrics, QualityConfig(), str(qc), str(rpt))
    with open(os.path.join(str(rpt), "accuracy.csv"),
              encoding="utf-8-sig", newline="") as fh:
        return list(csv.reader(fh))


class TestX1Columns:
    def test_columns_exist(self, tmp_path, base_metrics):
        header = _rows(tmp_path, base_metrics, None)[0]
        assert X1_MAX in header and X1_RATIO in header

    def test_calibrated_prints_numbers(self, tmp_path, base_metrics):
        rows = _rows(tmp_path, base_metrics,
                     {"x1_max_change_mm": 12.5, "x1_changed_ratio": 0.03,
                      "x1_calibrated": True})
        header, data = rows[0], rows[1]
        assert data[header.index(X1_MAX)] == "12.50"
        assert data[header.index(X1_RATIO)] == "3.0%"

    def test_uncalibrated_prints_words_not_numbers(self, tmp_path, base_metrics):
        """未标定必须印「未标定」——论文表取自这里，177mm 写进去就是硬伤。"""
        rows = _rows(tmp_path, base_metrics,
                     {"x1_max_change_mm": 177.3, "x1_changed_ratio": 0.159,
                      "x1_calibrated": False})
        header, data = rows[0], rows[1]
        assert data[header.index(X1_MAX)] == "未标定"
        assert data[header.index(X1_RATIO)] == "未标定"

    def test_missing_x1_says_na_not_zero(self, tmp_path, base_metrics):
        """X1 没跑（可选阶段关闭/条件不满足）→ N/A，不能印 0.00。

        印 0 会被读成「查过了、没有形变」，与「没查」是两件事
        （与 X3 的「未筛查 ≠ 0.00%」同一条纪律）。
        """
        rows = _rows(tmp_path, base_metrics, None)
        header, data = rows[0], rows[1]
        assert data[header.index(X1_MAX)] == "N/A"
        assert data[header.index(X1_RATIO)] not in ("0.00", "0.0%")

    def test_nan_is_na(self, tmp_path, base_metrics):
        rows = _rows(tmp_path, base_metrics,
                     {"x1_max_change_mm": float("nan"),
                      "x1_changed_ratio": 0.0, "x1_calibrated": True})
        header, data = rows[0], rows[1]
        assert data[header.index(X1_MAX)] == "N/A"
