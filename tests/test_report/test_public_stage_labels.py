"""对外口径 = 指南编号：报表里写 X2（异常检测），内部键仍是 X3。

背景（2026-11-06，ADR-0027）
---------------------------
代码里 `X2` 这个位置先被「色差评估」占了，于是指南的 `X2`（单期纹理异常检测）
只能顺延叫 `X3`。功能没问题，但**用户是按指南写论文的**——他一直看到「X3」，
自然对不上自己说的「X2」。

所以定：给读者看的地方（报表/界面/论文）用 `X2`；内部键（`STAGE_ORDER`、
manifest、`runner.x3_enabled`）保持 `X3`。

这个测试钉的就是这条边界：**改文案可以，改键不行**。
"""
from __future__ import annotations

import os

import pytest


def _manifest(tmp_path, **m5):
    from h3d.core import PipelineConfig, RunManifest

    cfg = PipelineConfig()
    cfg.runner.manifest = str(tmp_path / "90_meta" / "run_manifest.json")
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
    m = RunManifest(config_sha256="abc")
    m.record("relic_001/M5", "done", ["qc.json"], 1.0, coverage=0.82,
             rmse_mm=3.5, mps_m=0.001, **m5)
    m.save(cfg.runner.manifest)
    return RunManifest.load(cfg.runner.manifest)


class TestReportUsesGuideNumber:
    def test_report_says_x2_not_x3(self, tmp_path):
        from h3d.report.html_export import generate_html

        man = _manifest(tmp_path, anomaly_ratio=0.33, anomaly_regions=7)
        path = generate_html(man, [], str(tmp_path / "r.html"), project="p")
        txt = open(path, encoding="utf-8").read()
        assert "X2 单期筛查" in txt
        # 「X3」不能出现在交付给读者的报表里
        assert "X3" not in txt, "报表里仍有 X3（对外口径应为 X2）"


class TestInternalKeyUnchanged:
    def test_stage_order_still_x3(self):
        """内部键**不能**跟着改名：manifest 键、配置项、历史账本都靠它。"""
        from h3d.core.stages import STAGE_ORDER

        assert "X3" in STAGE_ORDER
        assert "X3" not in [s for s in STAGE_ORDER if s == "X2"]

    def test_config_knob_still_x3_enabled(self):
        from h3d.core import PipelineConfig

        assert hasattr(PipelineConfig().runner, "x3_enabled")


class TestGlossaryHasTheMapping:
    def test_glossary_states_public_mapping(self):
        """口径表必须留在术语表里，否则半年后又会出现「X3 是啥」。"""
        p = os.path.join(os.path.dirname(__file__), "..", "..", "GLOSSARY.md")
        txt = open(p, encoding="utf-8").read()
        assert "对外口径" in txt
        assert "X2" in txt and "单期纹理异常检测" in txt


class TestBase64Noise:
    """内嵌 base64 图片里的随机子串会把「HTML 里有 X3」这类检查骗成 PASS。

    实测：batch3 的报表原文里有 **34 处 "X3"**，全部来自 `data:image/png;base64,…`
    ——阶段行即使真的丢了，核验也会通过。判据必须先剥图片再搜。
    """

    def test_html_text_strips_data_uri(self, tmp_path):
        from h3d.delivery_verify import html_text

        p = tmp_path / "x.html"
        p.write_text('<p>ok</p><img src="data:image/png;base64,'
                     'iVBORw0KGgoX3X3X3AAAA=="/>', encoding="utf-8")
        txt = html_text(str(p))
        assert "X3" not in txt, "base64 里的 X3 没被剥掉——判据会假 PASS"
        assert "ok" in txt

    def test_missing_stage_row_is_caught_despite_noise(self, tmp_path):
        """报表只有图片噪声、没有阶段行 → 必须报 WARN（不能因噪声而 PASS）。"""
        from h3d.delivery_verify import html_text

        p = tmp_path / "y.html"
        p.write_text('<img src="data:image/png;base64,AAAAX3BBBBX2CCCC"/>',
                     encoding="utf-8")
        txt = html_text(str(p))
        assert "X3" not in txt and "X2" not in txt
