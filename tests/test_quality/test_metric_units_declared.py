"""数据来源声明 → 米制语义（``runner.data_source``）。

为什么需要它（2026-11-04）
--------------------------
原来只有一句 ``metric_units = bool(task.raw_paths)``：只要 ``00_raw`` 里有个
文件，M5 就按米制判门、报表就印「激光扫描 / 中误差 3.21 mm」。可那个文件
完全可能是**别的软件照片重建**出来的模型（Metashape / RealityCapture 导出，
坐标是任意单位）。

r17 补了 ``laser_unverified`` / ``dual_source_duplicate`` 两个自证检测——都是
**事后推断**：必须同时存在 SfM 稠密模型才判得出。而用户自己明明知道
00_raw 里放的是什么，却没有地方说。于是同一个项目里：

* 系统猜错了 → 报表印出「激光锚定 / 3.21 mm」，一路被引用进论文；
* 用户知道错了 → 没有任何开关能纠正。

这个模块把「来源」变成**显式声明**，并钉死一条判据：
**证据压过声明**（声明 laser 而 M1 判出 raw 本身就是照片重建 → 不主张米制）。
声明是把判断权交回给用户，不是给一个绕过诚实性检查的后门。
"""
from __future__ import annotations

import pytest


class TestResolveMetricUnits:
    def test_auto_with_raw_keeps_legacy_behaviour(self):
        """默认值与旧行为逐字相同——老项目重跑数字不能变。"""
        from h3d.quality.gate import resolve_metric_units

        assert resolve_metric_units("auto", True) == (True, "auto_with_raw", False)

    def test_auto_without_raw_is_not_metric(self):
        from h3d.quality.gate import resolve_metric_units

        assert resolve_metric_units("auto", False) == (
            False, "auto_without_raw", False)

    def test_declared_photogrammetry_overrides_raw_presence(self):
        """核心新能力：00_raw 有文件，但用户说那是照片重建件。"""
        from h3d.quality.gate import resolve_metric_units

        assert resolve_metric_units("photogrammetry", True) == (
            False, "declared_photogrammetry", False)
        assert resolve_metric_units("photogrammetry", False)[0] is False

    def test_declared_laser_needs_no_conflict(self):
        from h3d.quality.gate import resolve_metric_units

        assert resolve_metric_units("laser", True, "laser") == (
            True, "declared_laser", False)

    def test_evidence_beats_declaration(self):
        """M1 判出「raw 与 SfM 稠密近乎重合」→ 声明 laser 不成立。

        这是本模块唯一的硬红线：如果声明能压过证据，用户只要勾一下「激光件」
        就能把自证数据包装成米制结果，那比没有这个功能更糟。
        """
        from h3d.quality.gate import resolve_metric_units

        units, reason, conflict = resolve_metric_units(
            "laser", True, "raw_looks_like_sfm")
        assert units is False
        assert reason == "declared_laser_conflicts_with_evidence"
        assert conflict is True

    def test_declared_laser_without_raw_is_a_conflict(self):
        """声明「激光件」却没有 00_raw：自相矛盾，不能主张米制。

        GUI 的 ``validate()`` 会拦住它，但 CLI 手写 yaml 能绕过去；
        绕过去之后也不许悄悄按米制出数。
        """
        from h3d.quality.gate import resolve_metric_units

        units, reason, conflict = resolve_metric_units("laser", False)
        assert units is False
        assert conflict is True
        assert "laser" in reason

    def test_unknown_declaration_falls_back_to_auto(self):
        """schema 已挡非法值，这里只防「绕过 schema 的内部调用」崩掉。"""
        from h3d.quality.gate import resolve_metric_units

        assert resolve_metric_units("nonsense", True) == (
            True, "auto_with_raw", False)
        assert resolve_metric_units("", True) == (True, "auto_with_raw", False)


class TestConfigField:
    def test_default_is_auto(self):
        from h3d.core import PipelineConfig

        assert PipelineConfig().runner.data_source == "auto"

    def test_yaml_roundtrip(self):
        from h3d.core import loads

        cfg = loads("runner:\n  data_source: photogrammetry\n")
        assert cfg.runner.data_source == "photogrammetry"
        assert cfg.to_dict()["runner"]["data_source"] == "photogrammetry"

    def test_illegal_value_is_rejected_loudly(self):
        """禁静默降级：拼错成 ``photo`` 必须报错，不能悄悄当 auto 跑。"""
        from h3d.core import ConfigError, loads

        with pytest.raises(ConfigError):
            loads("runner:\n  data_source: photo\n")


class TestQcJsonKeepsTheReason:
    """判门失败时 manifest 不写，``qc_metrics.json`` 是唯一的留档。

    只落一个 ``metric_units=False`` 布尔，事后没人能分辨「这台机器没测」
    与「用户声明了这是照片重建件，按声明不主张米制」——两种情况的处理方式
    完全不同（前者该补数据，后者本就该如此）。
    """

    def _metrics(self, **extra):
        m = {"coverage": 0.12, "rmse_mm": 1.5, "rmse_units": 0.0015,
             "rmse_rel": 0.01, "rmse_reliable": True, "boundary_ratio": 0.02,
             "mps_m": 0.001, "metric_units": False, "ok": False,
             "rmse_unit": "模型单位", "rmse_scope": "无量纲（SfM 任意单位）"}
        m.update(extra)
        return m

    def test_qc_metrics_json_carries_reason_and_declaration(self, tmp_path):
        import json
        import os

        from h3d.core import QualityConfig
        from h3d.quality.gate import build_report

        qc = str(tmp_path / "60_qc" / "r1")
        rpt = str(tmp_path / "70_report" / "r1")
        build_report("r1", self._metrics(
            data_source_declared="photogrammetry",
            metric_units_reason="declared_photogrammetry",
            declared_source_conflict=False), QualityConfig(), qc, rpt)

        data = json.load(open(os.path.join(qc, "qc_metrics.json"),
                              encoding="utf-8"))
        assert data["metric_units_reason"] == "declared_photogrammetry"
        assert data["data_source_declared"] == "photogrammetry"
        assert data["declared_source_conflict"] is False

    def test_missing_keys_do_not_crash(self, tmp_path):
        """老 manifest 重放（没有这三个键）时不能 KeyError。"""
        from h3d.core import QualityConfig
        from h3d.quality.gate import build_report

        build_report("r1", self._metrics(), QualityConfig(),
                     str(tmp_path / "qc" / "r1"), str(tmp_path / "rpt" / "r1"))

    def test_scope_wording_distinguishes_declared_from_sfm(self, tmp_path):
        """「无量纲」的**原因**要分开写：读者该做的动作完全不同。

        · SfM 任意单位 → 该去跑 SfM / 补控制点
        · 用户声明照片重建件 → 该换激光件（跑多少次 SfM 都不会变米制）
        """
        import csv
        import os

        from h3d.core import QualityConfig
        from h3d.quality.gate import build_report

        m = self._metrics(metric_units_reason="declared_photogrammetry")
        m.pop("rmse_scope")            # 让 build_report 自己推（本用例查的就是推导）
        rpt = str(tmp_path / "rpt")
        build_report("r1", m, QualityConfig(), str(tmp_path / "qc"), rpt)
        row = next(iter(csv.DictReader(open(
            os.path.join(rpt, "accuracy.csv"), encoding="utf-8-sig"))))
        assert "声明" in row["中误差口径"], row["中误差口径"]

    def test_auto_path_keeps_the_old_wording(self, tmp_path):
        """反方向：默认口径一字不改，老报表读起来不能变。"""
        import csv
        import os

        from h3d.core import QualityConfig
        from h3d.quality.gate import build_report

        m = self._metrics(metric_units_reason="auto_without_raw")
        m.pop("rmse_scope")
        rpt = str(tmp_path / "rpt2")
        build_report("r1", m, QualityConfig(), str(tmp_path / "qc2"), rpt)
        row = next(iter(csv.DictReader(open(
            os.path.join(rpt, "accuracy.csv"), encoding="utf-8-sig"))))
        assert row["中误差口径"] == "无量纲（SfM 任意单位）"
