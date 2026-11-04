"""报表对「数据来源声明」的表述。

只落 ``metric_units`` 一个布尔是不够的：``auto``（系统按 00_raw 里有没有文件
猜）与 ``photogrammetry``（用户声明）在报表上长得一模一样，读者分不出这个
口径是谁定的。而这两者的可信度差一个量级——前者是推断，后者是采集事实。

三种必须出现的情形：

1. 声明为照片重建件 → 说明「按声明不作米制解读」，而不是让读者以为系统漏算了；
2. 声明 laser 但证据冲突 → 必须 WARN（用户会以为自己选的没生效）；
3. 几何来源列 → 新取值 ``declared_photogrammetry`` 要有人话。
"""
from __future__ import annotations

import os

import pytest

from h3d.core import PipelineConfig, RunManifest
from h3d.report.html_export import generate_html


def _manifest(tmp_path, **m5_metrics) -> RunManifest:
    cfg = PipelineConfig()
    cfg.runner.manifest = str(tmp_path / "90_meta" / "run_manifest.json")
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
    m = RunManifest(config_sha256="abc123")
    m.record("relic_001/M1", "done", ["a.ply"], 0.5, n_scans=1,
             dense_source=m5_metrics.pop("dense_source", "laser"))
    m.record("relic_001/M3", "done", ["mesh.ply"], 3.2, n_vertices=100)
    m.record("relic_001/M5", "done", ["qc.json"], 1.1,
             coverage=0.82, rmse_mm=3.5, mps_m=0.001, **m5_metrics)
    m.save(cfg.runner.manifest)
    return RunManifest.load(cfg.runner.manifest)


def _html(tmp_path, manifest) -> str:
    path = generate_html(manifest, [], str(tmp_path / "report.html"),
                         project="测试项目")
    return open(path, encoding="utf-8").read()


class TestDeclaredSourceInReport:
    def test_declared_photogrammetry_explains_the_unit_choice(self, tmp_path):
        m = _manifest(tmp_path, dense_source="declared_photogrammetry",
                      metric_units=False,
                      data_source_declared="photogrammetry",
                      metric_units_reason="declared_photogrammetry",
                      declared_source_conflict=False, rmse_unit="模型单位")
        txt = _html(tmp_path, m)
        assert "照片重建" in txt
        # 几何来源列必须是人话，不能印裸键名
        assert "declared_photogrammetry" not in txt

    def test_conflict_is_a_warning_not_a_silent_downgrade(self, tmp_path):
        """声明 laser 与证据冲突 → 用户必须看到，否则以为选项没生效。"""
        m = _manifest(tmp_path, dense_source="raw_looks_like_sfm",
                      metric_units=False,
                      data_source_declared="laser",
                      metric_units_reason="declared_laser_conflicts_with_evidence",
                      declared_source_conflict=True, rmse_unit="模型单位")
        txt = _html(tmp_path, m)
        assert "冲突" in txt
        # 报表要能指出冲突的具体两方，不能只说「有问题」
        assert "laser" in txt

    def test_auto_does_not_add_noise(self, tmp_path):
        """默认值不该在报表里多出一段解释——老项目报表不该变长。"""
        m = _manifest(tmp_path, metric_units=True,
                      data_source_declared="auto",
                      metric_units_reason="auto_with_raw",
                      declared_source_conflict=False)
        txt = _html(tmp_path, m)
        assert "数据来源声明" not in txt
