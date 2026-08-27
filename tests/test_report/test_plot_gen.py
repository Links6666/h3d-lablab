"""tests.test_report.test_plot_gen : 图表生成单元测试。"""
from __future__ import annotations

import os

import pytest

from h3d.core import ExportError, RunManifest
from h3d.report.plot_gen import plot_bar, plot_metric_series, plot_stage_timings


class TestPlotBar:
    def test_writes_png(self, tmp_path):
        path = tmp_path / "bar.png"
        out = plot_bar(["A", "B", "C"], [1.0, 2.0, 3.0], str(path),
                       title="测试", xlabel="x", ylabel="y")
        assert out == os.path.abspath(str(path))
        assert path.exists()
        assert path.stat().st_size > 1000  # 非空 PNG

    def test_mismatched_length_raises(self, tmp_path):
        with pytest.raises(ExportError):
            plot_bar(["A", "B"], [1.0], str(tmp_path / "bad.png"))

    def test_empty_raises(self, tmp_path):
        with pytest.raises(ExportError):
            plot_bar([], [], str(tmp_path / "empty.png"))

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "sub" / "dir" / "chart.png"
        plot_bar(["a"], [1.0], str(path))
        assert path.exists()


class TestPlotStageTimings:
    def test_aggregates_per_stage(self, tmp_path):
        m = RunManifest()
        m.record("rock001/M1", "done", elapsed_s=1.0)
        m.record("rock002/M1", "done", elapsed_s=2.0)
        m.record("rock001/M2", "done", elapsed_s=3.0)
        m.record("rock001/M1", "done", elapsed_s=5.0)  # 覆盖（run_id 复用场景）

        path = tmp_path / "timings.png"
        out = plot_stage_timings(m, str(path))
        assert out == os.path.abspath(str(path))
        assert path.exists()
        assert path.stat().st_size > 1000


class TestPlotMetricSeries:
    def test_plots_known_metric(self, tmp_path):
        m = RunManifest()
        m.record("rock001/M2", "done", fitness=0.95)
        m.record("rock002/M2", "done", fitness=0.88)
        m.record("rock003/M2", "done", fitness=0.92)
        m.record("rock001/M1", "done")  # 缺 fitness -> 跳过

        path = tmp_path / "fitness.png"
        out = plot_metric_series(m, "fitness", str(path), title="配准 fitness")
        assert os.path.exists(out)

    def test_missing_metric_raises(self, tmp_path):
        m = RunManifest()
        m.record("rock001/M1", "done", elapsed_s=1.0)
        with pytest.raises(ExportError):
            plot_metric_series(m, "fitness", str(tmp_path / "x.png"))
