"""tests.test_report.test_generate : 报表汇总入口测试。"""
from __future__ import annotations

import os

from h3d.core import ReportConfig, RunManifest
from h3d.report import generate


class TestGenerate:
    def _manifest(self):
        m = RunManifest()
        m.record("rock001/M1", "done", ["a.ply"], 1.2, points_out=500)
        m.record("rock001/M2", "done", ["b.ply"], 2.5, fitness=0.95)
        return m

    def test_disabled_returns_empty(self, tmp_path):
        m = self._manifest()
        cfg = ReportConfig(enabled=False)
        out = generate(m, failures=[], report_cfg=cfg)
        assert out == []
        # 不应创建目录
        assert not (tmp_path / "output").exists()

    def test_csv_and_png(self, tmp_path):
        m = self._manifest()
        cfg = ReportConfig(enabled=True, formats=["csv", "png"],
                           output_dir=str(tmp_path / "rpt"))
        out = generate(m, failures=[{
            "item_id": "rock002", "stage": "M1",
            "error_type": "InputDataError", "message": "空",
            "time": "2026-08-25T14:00:00",
        }], report_cfg=cfg)
        names = {os.path.basename(p) for p in out}
        assert "metrics_summary.csv" in names
        assert "failures.csv" in names
        assert "stage_timings.png" in names
        for p in out:
            assert os.path.exists(p)

    def test_csv_only(self, tmp_path):
        m = self._manifest()
        cfg = ReportConfig(enabled=True, formats=["csv"],
                           output_dir=str(tmp_path / "rpt"))
        out = generate(m, failures=None, report_cfg=cfg)
        assert all(p.endswith(".csv") for p in out)
        # 无失败时不写 failures.csv
        assert not any("failures" in p for p in out)


class TestGenerateEmptyManifestResilience:
    """回归（实机 E601）：manifest 无任何 elapsed_s 时报表不得抛异常。

    场景：一键演示中 S1 失败 → 无阶段耗时 → 绘图抛 E601 → 打包版 GUI 弹出
    PyInstaller「Unhandled exception」崩溃框。CSV/HTML 仍须交付。
    """

    def test_empty_manifest_skips_png_but_keeps_csv_html(self, tmp_path):
        m = RunManifest()                       # 空 manifest（无阶段记录）
        cfg = ReportConfig(enabled=True, formats=["csv", "png", "html"],
                           output_dir=str(tmp_path / "rpt"))
        out = generate(m, failures=[], report_cfg=cfg)
        names = {os.path.basename(p) for p in out}
        assert "metrics_summary.csv" in names
        assert "report.html" in names
        assert "stage_timings.png" not in names          # 无数据 -> 跳过绘图
        assert not (tmp_path / "rpt" / "stage_timings.png").exists()

    def test_failed_stage_only_manifest(self, tmp_path):
        m = RunManifest()
        m.record_fail("rock001/S1", "COLMAP 失败")        # 只有失败记录
        cfg = ReportConfig(enabled=True, formats=["csv", "png", "html"],
                           output_dir=str(tmp_path / "rpt"))
        out = generate(m, failures=[], report_cfg=cfg)     # 不应抛异常
        assert any(p.endswith("metrics_summary.csv") for p in out)
