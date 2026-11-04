"""纹理重叠度从 M4 走到报表：计算处 → manifest 白名单 → 模板，三处都得在。

这个指标的 N/A 是**设计书里写着、代码里空着**的那种债：``quality.run`` 里有
一行 ``"overlap": None,  # 多视图纹理重叠（M4 未实现，N/A）``，一挂就是几个
版本。而它恰恰是「多视图加权采样」这条技术路线的**唯一**支撑数字——

``multiview_color_std`` 只能说明"融合进去的几台相机意见差多少"。整片纹素全靠
单相机硬投影时，色差恒等于 0（只有一台，没有分歧）：最该报警的情况恰好是
这个指标最漂亮的情况。两个数必须配对读，缺一个就会读反。
"""
from __future__ import annotations

import json
import os

import pytest

from tests._m5_fixture import m5_project


class TestReachesM5:
    def test_overlap_comes_from_m4_not_none(self, tmp_path):
        from h3d.runner import _stage_quality

        task, cfg = m5_project(tmp_path, m4_metrics={
            "texture_overlap": 2.35, "texture_multi_view_ratio": 0.71})
        m5 = _stage_quality(task, cfg).metrics
        assert m5["overlap"] == pytest.approx(2.35)
        assert m5["texture_multi_view_ratio"] == pytest.approx(0.71)

    def test_missing_m4_metric_stays_none(self, tmp_path):
        """M4 没跑 / 老项目没有这个键 → 保持 None（报表印 N/A），不能变 0。"""
        from h3d.runner import _stage_quality

        task, cfg = m5_project(tmp_path, m4_metrics={})
        m5 = _stage_quality(task, cfg).metrics
        assert m5["overlap"] is None
        assert m5["texture_multi_view_ratio"] is None


class TestReachesManifest:
    def test_manifest_whitelist_carries_both(self, tmp_path):
        """``manifest_metrics`` 是手写 dict —— 不写进去，报表一个字都读不到。"""
        from h3d.runner import _stage_quality

        task, cfg = m5_project(tmp_path, m4_metrics={
            "texture_overlap": 2.35, "texture_multi_view_ratio": 0.71})
        m5 = _stage_quality(task, cfg).metrics
        for key in ("overlap", "texture_multi_view_ratio"):
            assert key in m5, f"manifest 白名单缺 {key}"


class TestReachesReport:
    def _manifest(self, tmp_path, **m4):
        from h3d.core import PipelineConfig, RunManifest

        cfg = PipelineConfig()
        cfg.runner.manifest = str(tmp_path / "90_meta" / "run_manifest.json")
        os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
        m = RunManifest(config_sha256="abc")
        m.record("relic_001/M4", "done", ["a.png"], 1.0, **m4)
        m.record("relic_001/M5", "done", ["qc.json"], 1.0, coverage=0.82,
                 rmse_mm=3.5, mps_m=0.001)
        m.save(cfg.runner.manifest)
        return RunManifest.load(cfg.runner.manifest)

    def test_html_shows_the_column(self, tmp_path):
        from h3d.report.html_export import generate_html

        man = self._manifest(tmp_path, texture_overlap=2.35,
                             texture_multi_view_ratio=0.71)
        path = generate_html(man, [], str(tmp_path / "r.html"), project="p")
        txt = open(path, encoding="utf-8").read()
        assert "多视角重叠" in txt
        assert "2.35" in txt

    def test_html_says_na_when_missing(self, tmp_path):
        """"没量"必须是 N/A，不能印 0（0 会被读成"没有任何相机参与"）。"""
        from h3d.report.html_export import generate_html

        man = self._manifest(tmp_path)
        path = generate_html(man, [], str(tmp_path / "r2.html"), project="p")
        txt = open(path, encoding="utf-8").read()
        assert "多视角重叠" in txt
        assert "N/A" in txt
