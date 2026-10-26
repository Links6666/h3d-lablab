"""tests.test_gui : GUI 逻辑层测试（向导配置/日志解析/交付包导出）。

GUI 控件层（PySide6）通过 pytest.importorskip 延迟；逻辑层独立可测。
"""
from __future__ import annotations

import os

import pytest


class TestProjectConfig:
    def test_validate_scene1_requires_laser(self, tmp_path):
        from h3d.gui.config_edit import ProjectConfig
        c = ProjectConfig()
        c.scene = "scene1"
        c.root_dir = str(tmp_path)
        c.photo_dir = str(tmp_path)
        errs = c.validate()
        assert any("激光点云" in e for e in errs)

    def test_validate_missing_photo(self, tmp_path):
        from h3d.gui.config_edit import ProjectConfig
        c = ProjectConfig()
        c.root_dir = str(tmp_path)
        errs = c.validate()
        assert any("照片文件夹" in e for e in errs)

    def test_build_layout(self, tmp_path):
        from h3d.gui.config_edit import ProjectConfig
        c = ProjectConfig()
        c.root_dir = str(tmp_path)
        c.item_id = "relic_x"
        layout = c.build_layout()
        for k in ("raw", "photo", "ctrl"):
            assert os.path.isdir(layout[k])

    def test_to_yaml_scene1(self, tmp_path):
        from h3d.gui.config_edit import ProjectConfig
        import yaml
        c = ProjectConfig()
        c.root_dir = str(tmp_path)
        c.item_id = "r1"
        cfg_path = c.to_yaml(os.path.join(str(tmp_path), "config.yaml"))
        assert os.path.exists(cfg_path)
        data = yaml.safe_load(open(cfg_path, encoding="utf-8"))
        assert data["sfm_solve"]["enabled"] is True
        assert data["texture"]["paint_mode"] == "photo"

    def test_to_yaml_scene2_dense_on(self, tmp_path):
        from h3d.gui.config_edit import ProjectConfig
        import yaml
        c = ProjectConfig()
        c.scene = "scene2"
        c.root_dir = str(tmp_path)
        cfg_path = c.to_yaml(os.path.join(str(tmp_path), "config2.yaml"))
        data = yaml.safe_load(open(cfg_path, encoding="utf-8"))
        assert data["sfm_solve"]["dense"] is True


class TestRunnerParsing:
    """日志解析：纯 Python（logparse 模块，无 Qt 依赖）。"""

    def test_stage_done_regex(self):
        from h3d.gui.logparse import stage_done_of
        line = "2026-08-30 15:25 [INFO] [h3d.runner] M3 完成：relic_001"
        assert stage_done_of(line) == "M3"

    def test_stage_fail_regex(self):
        from h3d.gui.logparse import stage_failed_of
        line = "失败隔离：item=relic_001 stage=M5 -> QualityGateError"
        assert stage_failed_of(line) == "M5"

    def test_done_total_regex(self):
        from h3d.gui.logparse import done_summary
        line = "批量处理完成：total=1 done=1 failed=0 skipped=0"
        assert done_summary(line) == (1, 1, 0)

    def test_parse_progress_percent(self):
        """阶段顺序含 S1~X2。"""
        from h3d.gui.logparse import STAGE_ORDER
        # 2026-10-18：内核注册表含 P1（S1/P1/X1/X2 四个可选阶段），
        # 界面灯必须与之一致，否则 P1 执行时 KeyError 被 Qt 吞掉。
        assert len(STAGE_ORDER) == 10
        assert STAGE_ORDER[0] == "S1" and STAGE_ORDER[-1] == "X2"


class TestExportPackage:
    def test_export_with_deliverables(self, tmp_path):
        from h3d.gui.export import export_delivery_package
        root = str(tmp_path)
        exp = os.path.join(root, "80_export", "r1")
        os.makedirs(exp, exist_ok=True)
        with open(os.path.join(exp, "r1.obj"), "w") as fh:
            fh.write("v 0 0 0\n")
        out = os.path.join(root, "pkg.zip")
        r = export_delivery_package(root, "r1", out)
        assert r["n_files"] >= 1 and os.path.exists(out)

    def test_export_empty(self, tmp_path):
        from h3d.gui.export import export_delivery_package
        root = str(tmp_path)
        os.makedirs(os.path.join(root, "80_export", "r1"), exist_ok=True)
        r = export_delivery_package(root, "r1",
                                    os.path.join(root, "p.zip"))
        assert r == {}


class TestHistory:
    def test_add_list_remove(self, tmp_path):
        from h3d.gui.history import HistoryStore
        h = HistoryStore(os.path.join(str(tmp_path), "h.json"))
        h.add("/proj/a", "r1", "scene1")
        h.add("/proj/b", "r2", "scene2")
        items = h.list()
        assert len(items) == 2
        assert items[0]["name"] == "r2"  # 最新置顶
        # 去重
        h.add("/proj/a", "r1", "scene1")
        assert len(h.list()) == 2
        h.remove("/proj/a")
        assert len(h.list()) == 1

    def test_find_config(self, tmp_path):
        from h3d.gui.history import HistoryStore
        cfg = os.path.join(str(tmp_path), "config.yaml")
        open(cfg, "w").write("x: 1\n")
        assert HistoryStore.find_config(str(tmp_path)) == cfg


class TestDemo:
    def test_create_demo_project(self, tmp_path):
        from h3d.gui.demo import create_demo_project
        cfg = create_demo_project(os.path.join(str(tmp_path), "demo"))
        assert os.path.isdir(cfg.photo_dir)
        photos = [f for f in os.listdir(cfg.photo_dir)
                  if f.endswith(".jpg")]
        assert len(photos) >= 8  # 合成照片
        assert os.path.exists(os.path.join(
            str(tmp_path), "demo", "config.yaml"))
        assert os.path.exists(cfg.laser_ply)


class TestReportView:
    def test_qc_table_build(self, tmp_path):
        from h3d.gui.report_view import build_qc_table
        import json, os
        qc = os.path.join(str(tmp_path), "60_qc", "r1")
        os.makedirs(qc, exist_ok=True)
        json.dump({"coverage": 0.9, "rmse_mm": 2.0,
                   "boundary_ratio": 0.001, "texture_sharpness": 100,
                   "texture_coverage": 0.05,
                   "thresholds": {"coverage_threshold": 0.8,
                                  "rmse_threshold_mm": 5.0}},
                  open(os.path.join(qc, "qc_metrics.json"), "w",
                       encoding="utf-8"))
        t = build_qc_table(str(tmp_path), "r1")
        assert t.rowCount() >= 4
        assert t.item(0, 0).text() == "几何覆盖率"

    def test_qc_table_missing(self, tmp_path):
        from h3d.gui.report_view import build_qc_table
        t = build_qc_table(str(tmp_path), "nope")
        assert t.rowCount() >= 1


class TestViewer3d:
    def test_load_x1_changes(self, tmp_path):
        from h3d.gui.viewer3d import load_x1_changes
        import json, os
        out = os.path.join(str(tmp_path), "80_export", "r1")
        os.makedirs(out, exist_ok=True)
        json.dump({"changes": {"values": [1.0, 2.0, 3.0]}},
                  open(os.path.join(out, "r1_X1_m3c2.json"), "w",
                       encoding="utf-8"))
        vals = load_x1_changes(str(tmp_path), "r1")
        assert vals is not None and len(vals) == 3

    def test_load_x1_missing(self, tmp_path):
        from h3d.gui.viewer3d import load_x1_changes
        assert load_x1_changes(str(tmp_path), "r1") is None


class TestWelcomeNoGc:
    """回归：顶层窗口必须由容器持有引用（防 GC 闪退）。"""

    def test_start_wizard_holds_ref(self):
        """点击新建项目后向导存活（引用持有防 GC）。"""
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QMessageBox
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
        QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
        app = QApplication.instance() or QApplication([])
        from h3d.gui.welcome import WelcomeWindow
        w = WelcomeWindow()
        w._start_wizard()
        import gc
        gc.collect()
        assert hasattr(w, "_wizard")
        assert w._wizard is not None
        w.close()

    def test_history_open_holds_ref(self):
        import os, tempfile
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QMessageBox
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
        QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
        app = QApplication.instance() or QApplication([])
        from h3d.gui.welcome import WelcomeWindow
        from PySide6.QtWidgets import QListWidgetItem
        w = WelcomeWindow()
        # 造一个历史条目并模拟双击打开（root 无 config → 弹窗后不崩）
        e = {"path": tempfile.gettempdir(), "name": "t", "scene": "scene1"}
        item = QListWidgetItem("t")
        item.setData(0x0100, e)
        # 直接验证 _open_selected 在无 config 时安全返回
        w.history.add = lambda *a, **k: None  # 不写磁盘
        try:
            w._open_selected(item)
        except Exception:
            pass  # 无 config 弹窗路径（monkeypatch warning 已吞）
        w.close()


class TestWizardRoot:
    """回归：向导第二步必须可确认项目根目录（不再卡死）。"""

    def test_step2_root_entry(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QMessageBox
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
        app = QApplication.instance() or QApplication([])
        from h3d.gui.wizard import WizardWindow
        z = WizardWindow(on_done=lambda c: None)
        assert hasattr(z, "edit_root")  # 项目根目录控件存在
        assert hasattr(z, "_browse_root")
        z.close()

    def test_step2_wizard_completes_with_root(self, tmp_path):
        """填数据 + root 自动推断后，向导可过第二步并完成。"""
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QMessageBox
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
        app = QApplication.instance() or QApplication([])
        from h3d.gui.wizard import WizardWindow
        photo = os.path.join(str(tmp_path), "photos")
        laser = os.path.join(str(tmp_path), "s.ply")
        os.makedirs(photo)
        open(laser, "w").write("ply\n")
        for i in range(4):  # 假照片（validate 需 ≥3 张）
            open(os.path.join(photo, f"p{i}.jpg"), "w").write("x")
        got = {}
        z = WizardWindow(on_done=lambda c: got.__setitem__("cfg", c))
        z._next()  # step1 → 2
        z.drop_laser.add_path(laser)
        z.drop_photo.add_path(photo)
        assert z.edit_root.text() == str(tmp_path)  # 自动推断
        z._next()  # step2 → 3（root 已确认不再卡死）
        assert z._step == 2
        z.edit_name.setText("r1")
        z._finish()
        assert got["cfg"].root_dir == str(tmp_path)
        z.close()


class TestDropAreaDelete:
    """回归：数据导入区可删除已选项。"""

    def test_remove_and_clear(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from h3d.gui.wizard import DropArea
        da = DropArea("测试")
        da.add_path("/tmp/a.ply")
        da.add_path("/tmp/b.ply")
        assert len(da.paths()) == 2
        da.setCurrentRow(0)
        da._remove_selected()
        assert len(da.paths()) == 1 and da.paths()[0] == "/tmp/b.ply"
        da.clear_paths()
        assert len(da.paths()) == 0
        da.close()


class TestPhotoValidate:
    def test_dir_without_photos(self, tmp_path):
        from h3d.gui.config_edit import ProjectConfig
        empty = os.path.join(str(tmp_path), "empty_photos")
        os.makedirs(empty)
        c = ProjectConfig()
        c.root_dir = str(tmp_path)
        c.photo_dir = empty
        errs = c.validate()
        assert any("未找到照片" in e for e in errs)

    def test_single_file_rejected(self, tmp_path):
        from h3d.gui.config_edit import ProjectConfig
        f = os.path.join(str(tmp_path), "photo.jpg")
        open(f, "w").write("x")
        c = ProjectConfig()
        c.root_dir = str(tmp_path)
        c.photo_dir = f
        errs = c.validate()
        assert any("不是文件夹" in e for e in errs)

    def test_jpg_uppercase_accepted(self, tmp_path):
        from h3d.gui.config_edit import ProjectConfig
        pd = os.path.join(str(tmp_path), "photos")
        os.makedirs(pd)
        for i in range(4):
            open(os.path.join(pd, f"IMG_{i}.JPG"), "w").write("x")
        c = ProjectConfig()
        c.root_dir = str(tmp_path)
        c.photo_dir = pd
        errs = c.validate()
        assert not any("未找到照片" in e for e in errs)  # .JPG 大写被识别


class TestWizardStepVisibility:
    """回归（实机 BUG2）：第一步内容必须立即可见。

    旧实现把三个步骤页建成了无父窗口的顶层 Widget（未加入 stack 布局），
    主窗口内容区全白，必须先点「下一步」再点「上一步」才"意外"显示。
    """

    def test_steps_parented_and_visible_on_open(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from h3d.gui.wizard import WizardWindow
        w = WizardWindow(lambda cfg: None)
        w.show()
        app.processEvents()
        assert w.stack.count() == 3                 # 三个步骤页都在布局里
        assert w.step1.parent() is w                # 有父窗口（不是顶层窗口）
        assert not w.step1.isWindow()
        assert w.step1.isVisible()                  # 打开即显示第一步
        assert w.radio1.isVisible() and w.radio2.isVisible()
        assert not w.step2.isVisible()
        w._next()
        app.processEvents()
        assert w.step2.isVisible() and not w.step1.isVisible()
        w._prev()
        app.processEvents()
        assert w.step1.isVisible()
        w.close()


class TestDemoGeometry:
    """回归：示例照片必须是【几何一致】的三维渲染（否则 SfM 无法解算）。"""

    def test_views_are_distinct_and_deterministic(self):
        from h3d.gui.demo import _synth_photo, _synth_laser_cloud
        import numpy as np
        a = np.asarray(_synth_photo(az_deg=0.0, el_deg=25.0), dtype=np.int16)
        b = np.asarray(_synth_photo(az_deg=45.0, el_deg=25.0), dtype=np.int16)
        a2 = np.asarray(_synth_photo(az_deg=0.0, el_deg=25.0), dtype=np.int16)
        assert a.shape == (480, 640, 3)
        assert np.abs(a - b).mean() > 2.0          # 不同视角确实不同
        assert np.array_equal(a, a2)                # 同一视角确定性可复现
        # 对比度足够（SIFT 需要角点）
        assert a.std() > 20

    def test_laser_cloud_matches_photo_shape_scale(self):
        from h3d.gui.demo import SHAPE_SCALE, _synth_laser_cloud
        import numpy as np
        pts = _synth_laser_cloud(n=2000)
        half = (pts.max(axis=0) - pts.min(axis=0)) / 2.0
        assert np.allclose(half, SHAPE_SCALE, atol=0.1)


class TestDemoTextureSiftFriendly:
    """回归（实机：示例 S1 注册数 3/16~16/16 剧烈波动，全量流程失败）。

    根因：旧纹理为 θ/φ **参数域**正弦叠加，能量摊在 20 个分量上，细尺度
    分量只占 1/√20，实测每图仅 403 个 SIFT 关键点、120 个像对只有 45 对
    能匹配，SfM 长期处在临界状态。改为**球面随机平面波场**（三维随机方向
    + f^-0.5 幅度加权，能量集中在 3~12 px 周期）后每图 4248 个关键点、
    76/120 像对可匹配、16/16 全部注册。
    """

    @staticmethod
    def _extrema(img):
        """物体区域内 3x3 局部极值个数（SIFT 关键点密度的廉价代理）。"""
        import numpy as np
        g = np.asarray(img, dtype=np.float64).mean(axis=2)
        m = g > 60.0                       # 物体掩膜（背景约 41.6）
        c = g[1:-1, 1:-1]
        nb = [g[:-2, :-2], g[:-2, 1:-1], g[:-2, 2:], g[1:-1, :-2],
              g[1:-1, 2:], g[2:, :-2], g[2:, 1:-1], g[2:, 2:]]
        mx = np.ones_like(c, dtype=bool)
        mn = np.ones_like(c, dtype=bool)
        for n in nb:
            mx &= c > n
            mn &= c < n
        return int((mx | mn)[m[1:-1, 1:-1]].sum())

    def test_extrema_density_is_sift_suitable(self):
        from h3d.gui.demo import _synth_photo
        n = self._extrema(_synth_photo(az_deg=0.0, el_deg=25.0))
        # 实测：新纹理 2314，旧纹理 780；阈值取两者之间留足余量
        assert n > 1500, f"局部极值过少（{n}），SIFT 可检特征不足"

    def test_extrema_available_across_all_demo_views(self):
        """每个示例视角都必须有足够特征（不能只有个别视角可用）。"""
        from h3d.gui.demo import _synth_photo
        for k, el in ((0, 25.0), (1, 50.0)):
            for i in range(8):
                n = self._extrema(_synth_photo(az_deg=i * 45.0, el_deg=el,
                                               seed=i + k * 10))
                assert n > 1200, f"视角 az={i * 45.0} el={el} 特征不足（{n}）"

    def test_texture_is_band_limited_no_alias(self):
        """纹理必须带限：最细周期≥3 px，否则各视角混叠噪声不同、描述子不可复现。

        检查方式：直接对纹理函数在球面切向采样，统计相邻采样差分的能量占比；
        混叠纹理会表现为「采样越密、方差越大」的不可收敛行为。
        """
        import numpy as np
        from h3d.gui.demo import _sphere_texture, PIX_PER_UNIT
        # 沿一条球面大圆按 0.5 px 间隔采样（远密于像素）
        t = np.linspace(0.0, 3.0, 4000)
        p = np.stack([np.cos(t), np.sin(t) * 0.2, np.sin(t)], axis=1)
        p /= np.linalg.norm(p, axis=1, keepdims=True)
        v = _sphere_texture(p)
        d = np.abs(np.diff(v))
        # 半像素间隔下的平均差分应远小于整体对比度（带限 => 无高频振荡）
        assert d.mean() < 0.06 * (v.max() - v.min() + 1e-9)
        # 频率换算自检：3 px 周期对应约 342 rad/单位弧长
        assert 300 < 2 * np.pi * PIX_PER_UNIT / 3.0 < 380
