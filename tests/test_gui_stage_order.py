"""tests.test_gui_stage_order : 界面阶段灯必须与内核注册表一一对齐。

实机背景（2026-10-18）
----------------------
内核 ``STAGE_REGISTRY`` 有 10 个阶段（M1~M6 + S1/P1/X1/X2），而界面
``STAGE_ORDER`` 只有 9 个——漏了 **P1**（photo_texture）。P1 一旦被配置启用：

* ``▶ P1 开始：x`` 的正则不匹配 → 没有灯变「运行中」；
* ``_on_stage("P1", ...)`` 里 ``self.lamps["P1"]`` 抛 KeyError；
* KeyError 发生在 Qt 槽里 → 只打到 stderr（打包版 console=False 看不见），
  Qt 继续跑 → 界面停在上一阶段，与「卡死」无法区分。

本测试锁住：① 注册表每个阶段都有灯；② P1 的开始/完成/失败行都能解析；
③ 未登记阶段不抛异常。
"""
from __future__ import annotations

import pytest

from h3d.gui.logparse import (STAGE_ORDER, stage_done_of, stage_failed_of,
                              stage_start_of)


class TestRegistryAlignment:
    def test_every_registered_stage_has_lamp(self):
        from h3d.runner import STAGE_REGISTRY
        missing = [s for s in STAGE_REGISTRY if s not in STAGE_ORDER]
        assert not missing, f"界面缺少阶段灯：{missing}"

    def test_stage_order_has_no_phantom_stage(self):
        from h3d.runner import STAGE_REGISTRY
        extra = [s for s in STAGE_ORDER if s not in STAGE_REGISTRY]
        assert not extra, f"界面有内核不存在的阶段：{extra}"


class TestP1Parsing:
    def test_p1_start(self):
        line = "[INFO] ▶ P1 开始：relic_001"
        assert stage_start_of(line) == "P1"

    def test_p1_done(self):
        line = ("[INFO] [h3d.photo_texture] P1 完成：relic_001"
                "（{'n_views': 16}，12 文件，8.1s）")
        assert stage_done_of(line) == "P1"

    def test_p1_failure(self):
        line = "失败隔离：item=relic_001 stage=P1 -> boom"
        assert stage_failed_of(line) == "P1"


class TestUnknownStageSafe:
    def test_on_stage_tolerates_unknown_id(self, _qapp_session):
        """未登记的阶段 id 只记一行日志，不得抛 KeyError。"""
        from h3d.gui.app import MainWindow
        from h3d.gui.config_edit import ProjectConfig
        cfg = ProjectConfig()
        cfg.name = "p"
        cfg.root_dir = "."
        w = MainWindow(cfg)
        try:
            w._on_stage("ZZ", "done")     # 不应抛异常
            assert "ZZ" not in w.lamps
        finally:
            w.close()
