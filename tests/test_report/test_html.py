"""tests.test_report.test_html : HTML 报表单元测试。"""
from __future__ import annotations

import os

import numpy as np
import open3d as o3d
import pytest

from h3d.core import PipelineConfig, RunManifest
from h3d.report import generate
from h3d.report.html_export import generate_html


def _make_manifest(tmp_path) -> RunManifest:
    cfg = PipelineConfig()
    cfg.runner.manifest = str(tmp_path / "90_meta" / "run_manifest.json")
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
    m = RunManifest(config_sha256="abc123")
    m.record("rock001/M1", "done", ["a.ply"], 0.5, n_cleaned=2)
    m.record("rock001/M3", "done", ["mesh.ply"], 3.2, n_vertices=100)
    m.record("rock001/M5", "done", ["qc.json"], 1.1,
             coverage=0.82, rmse_mm=3.5, mps_m=0.01)
    m.record("rock001/M6", "done", ["m.ply"], 0.4, n_models=3)
    m.record("rock002/M5", "fail", [], 0.0, message="质检不达标")
    m.save(cfg.runner.manifest)
    return RunManifest.load(cfg.runner.manifest)


class TestGenerateHtml:
    def test_html_contains_sections(self, tmp_path):
        manifest = _make_manifest(tmp_path)
        failures = [{"item_id": "rock002", "stage": "M5",
                     "error_type": "QualityGateError",
                     "message": "coverage=10%", "time": "2026-01-01"}]
        path = generate_html(manifest, failures,
                             str(tmp_path / "report.html"),
                             project="测试项目")
        assert os.path.exists(path)
        txt = open(path, encoding="utf-8").read()
        assert "处理报表" in txt
        assert "测试项目" in txt
        assert "覆盖率" in txt and "82.0%" in txt  # M5 指标渲染
        assert "已发布" in txt and "3" in txt      # M6 交付行
        assert "QualityGateError" in txt            # 失败清单
        assert "rock002" in txt
        # 未执行阶段必须成行：旧报表只渲染已执行阶段，却统计"未执行 N"，
        # 表中找不到对应行 → 用户无法判断缺的是哪一步（2026-10-18 实机）。
        assert "未执行" in txt
        assert ">S1<" in txt
        # 缺失指标的缺值必须是 N/A，绝不把 nan/inf 印进表格
        assert ">nan<" not in txt
        assert ">N/A<" in txt

    def test_html_embeds_chart(self, tmp_path):
        """PNG 图表以 base64 内嵌（自包含可离线分享）。"""
        manifest = _make_manifest(tmp_path)
        chart = tmp_path / "chart.png"
        # 生成一个 2x2 合法 PNG
        import base64
        png_b64 = ("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0k"
                   "AAAAEklEQVR42mP8z8Dwn4GBgQEAF0gB/f7+XJ8AAAAASUVORK5CYII=")
        chart.write_bytes(base64.b64decode(png_b64))
        path = generate_html(manifest, None, str(tmp_path / "r.html"),
                             chart_png=str(chart))
        txt = open(path, encoding="utf-8").read()
        assert "data:image/png;base64" in txt

    def test_generate_with_html_format(self, tmp_path):
        """report.generate 支持 formats=['csv','png','html'] 全量输出。"""
        manifest = _make_manifest(tmp_path)
        from h3d.core import ReportConfig
        cfg = ReportConfig(output_dir=str(tmp_path / "report"),
                           formats=["csv", "png", "html"],
                           project_name="demo")
        files = generate(manifest, failures=None, report_cfg=cfg)
        names = {os.path.basename(f) for f in files}
        assert "report.html" in names
        assert "metrics_summary.csv" in names
        assert "stage_timings.png" in names

    def test_generate_html_only(self, tmp_path):
        manifest = _make_manifest(tmp_path)
        from h3d.core import ReportConfig
        cfg = ReportConfig(output_dir=str(tmp_path / "r2"),
                           formats=["html"])
        files = generate(manifest, failures=None, report_cfg=cfg)
        assert len(files) == 1 and files[0].endswith("report.html")
