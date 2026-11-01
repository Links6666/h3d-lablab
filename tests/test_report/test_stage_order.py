"""tests.test_report.test_stage_order : 报表与编排的**阶段清单一致性**。

为什么单独一个文件
------------------
阶段清单在仓库里有五份手写副本（runner 注册顺序、GUI 展示顺序、csv/plot/html 三套
`_STAGE_ORDER`），它们不一致时**不会报错**，只会让数字悄悄错位：

* `html_export._STAGE_ORDER` 曾经只有 8 项、缺 X3/X1/X2，而它是完成率分母
  `len(items) * len(_STAGE_ORDER)` 的唯一来源 → 「件数 × 阶段数」的跨层契约对不上，
  且状态表里根本没有 X3 那一行，用户看不出 X3 跑没跑（r16 手写清单同构，已踩两次）。
* `runner._reorder_ext_stages` 把 X1/X2 排在 M6 之后，而报表在 M5 生成 → 本轮跑完
  X1/X2，报表读到的还是上一轮（实机：`accuracy.csv` 的 X1 列第一次仍是 177.33）。
"""
from __future__ import annotations

import os

import pytest

from h3d.core import PipelineConfig, RunManifest


def _manifest_with_ext_stages(tmp_path) -> RunManifest:
    """一件，主线走到 M6，外加 X3（默认开）+ X1/X2（可选）。"""
    cfg = PipelineConfig()
    cfg.runner.manifest = str(tmp_path / "90_meta" / "run_manifest.json")
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
    m = RunManifest(config_sha256="abc123")
    for stage in ("M1", "M3", "M4", "P1", "X3", "M5", "M6"):
        m.record(f"relic_001/{stage}", "done", [], 0.1)
    # X1/X2 是可选阶段：已启用但条件不满足 → skipped（必须成行，不能消失）
    m.record("relic_001/X1", "skipped", [], 0.0,
             skip_reason="两期几何不同源", skip_kind="input")
    m.record("relic_001/X2", "skipped", [], 0.0,
             skip_reason="缺纹理图", skip_kind="input")
    m.save(cfg.runner.manifest)
    return RunManifest.load(cfg.runner.manifest)


class TestHtmlListsEveryRegisteredStage:
    """X3/X1/X2 必须在状态表里成行——否则用户无法判断它们跑了没。"""

    def test_ext_stage_rows_appear(self, tmp_path):
        from h3d.report.html_export import generate_html

        path = generate_html(_manifest_with_ext_stages(tmp_path), None,
                             str(tmp_path / "r.html"))
        txt = open(path, encoding="utf-8").read()
        for stage in ("X3", "X1", "X2"):
            assert f">{stage}<" in txt, f"状态表缺 {stage} 行"

    def test_denominator_counts_all_stages(self, tmp_path):
        """分母 = 件数 × 阶段数，阶段数必须等于实际注册的阶段数。

        这条锁的是 r16 那类缺陷：分母用一个手写清单，清单漏了阶段 → 「完成 7 ·
        未执行 3」这种读数看起来正常，实际分母少算了。
        """
        from h3d.report.html_export import _STAGE_ORDER, generate_html
        from h3d.runner import STAGE_REGISTRY

        assert set(_STAGE_ORDER) == set(STAGE_REGISTRY), (
            f"报表阶段清单与注册表不一致：报表多 {set(_STAGE_ORDER) - set(STAGE_REGISTRY)}、"
            f"少 {set(STAGE_REGISTRY) - set(_STAGE_ORDER)}")

        path = generate_html(_manifest_with_ext_stages(tmp_path), None,
                             str(tmp_path / "r.html"))
        txt = open(path, encoding="utf-8").read()
        assert f"1 件 × {len(STAGE_REGISTRY)} 阶段" in txt, (
            "分母没有按注册表阶段数计")


class TestRunnerOrderPutsExtStagesBeforeReport:
    """X1/X2 的输入只到 M3/M4，不依赖 M5；排在 M5 之后报表就永远读不到本轮结果。"""

    def test_x1_x2_run_before_m5(self):
        from h3d.runner import STAGE_REGISTRY

        order = list(STAGE_REGISTRY)
        for stage in ("X1", "X2"):
            assert order.index(stage) < order.index("M5"), \
                f"{stage} 排在 M5 之后，报表读不到本轮结果"
            assert order.index(stage) > order.index("M4"), \
                f"{stage} 排在 M4 之前，输入还不存在"

    def test_x3_still_before_m5(self):
        """X3 的位置不能在这一轮被挪坏（ADR-0003 D8）。"""
        from h3d.runner import STAGE_REGISTRY

        order = list(STAGE_REGISTRY)
        assert order.index("X3") > order.index("P1")
        assert order.index("X3") < order.index("M5")

    def test_m6_is_last(self):
        """M6 是发布收尾，必须最后——它复制交付物，后面再跑阶段就进不了交付。"""
        from h3d.runner import STAGE_REGISTRY

        assert list(STAGE_REGISTRY)[-1] == "M6"


class TestGuiOrderMatchesRunner:
    def test_same_stage_set_and_order(self):
        """GUI 的阶段条与备份进度按这个顺序画，错位会让进度条跳到错误的阶段。"""
        from h3d.gui.logparse import STAGE_ORDER as GUI_ORDER
        from h3d.runner import STAGE_REGISTRY

        assert list(GUI_ORDER) == list(STAGE_REGISTRY), (
            f"GUI 顺序 {list(GUI_ORDER)} 与 runner {list(STAGE_REGISTRY)} 不一致")
