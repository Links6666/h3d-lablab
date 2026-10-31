"""tests.test_gui_anomaly_panel : GUI「异常筛查」页（X3 可视化）。

为什么这页必须存在
------------------
X3 的结论现在只在报表 HTML 与 ``regions.csv`` 里：用户要看「第 3 处到底在哪」，
得自己拿坐标去 2048² 的图集上找。CLI 能跑、报表有数字，但**看不到位置**——
那这一层筛查就停在「跑过一次」。

本页承担三件事：摘要（面积/图斑数/σ_L/k/岛数）、完整图斑清单、点一处跳到
热力图的对应裁剪。判据落在「点第 N 行之后看到的是第 N 处」这条链路上。
"""

from __future__ import annotations

import csv
import json
import os

import numpy as np
import pytest

pytest.importorskip("PySide6")

ITEM = "relic_001"
SIZE = 400          # 夹具图集边长（比 2048 小得多，够测裁剪）


def _png(path, shape, *, seed=0):
    from PIL import Image

    os.makedirs(os.path.dirname(path), exist_ok=True)
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 255, (shape[0], shape[1], 3), dtype=np.uint8)
    Image.fromarray(arr).save(path)


def _regions():
    return [
        {"region_id": 1, "x0": 40, "y0": 60, "x1": 70, "y1": 90,
         "texels": 930, "area_ratio": 0.0058, "score_mean": 6.4,
         "score_max": 7.1},
        {"region_id": 2, "x0": 300, "y0": 250, "x1": 330, "y1": 280,
         "texels": 860, "area_ratio": 0.0054, "score_mean": 5.8,
         "score_max": 6.2},
    ]


def _project(tmp_path, *, products: bool = True):
    """造一个带 X3 产物的项目（``阶段成果/60_qc/<件>/anomaly/``）。"""
    stage = tmp_path / "阶段成果"
    # exist_ok：`test_refresh_after_run_picks_up_products` 会造两次
    # （先没产物、后有产物），第二次撞已存在目录不该炸
    (stage / "00_raw" / ITEM).mkdir(parents=True, exist_ok=True)
    (stage / "00_raw" / ITEM / "scan.ply").write_bytes(b"PLY-raw\n")
    (stage / "50_tex" / ITEM).mkdir(parents=True, exist_ok=True)
    _png(str(stage / "50_tex" / ITEM / f"{ITEM}_L1.0_diffuse.png"),
         (SIZE, SIZE))
    out = stage / "60_qc" / ITEM / "anomaly"
    out.mkdir(parents=True, exist_ok=True)
    if not products:
        (out / "metrics.json").unlink(missing_ok=True)
        return tmp_path, stage
    for name in ("anomaly_heat.png", "anomaly_mask.png"):
        _png(str(out / name), (SIZE, SIZE), seed=1)
    (out / "metrics.json").write_text(json.dumps({
        "item_id": ITEM,
        "atlas": f"50_tex/{ITEM}/{ITEM}_L1.0_diffuse.png",
        "size": [SIZE, SIZE],
        "metrics": {"anomalous_ratio": 0.002425, "n_regions": 2,
                    "island_count": 3690, "per_island": True,
                    "k": 3.5, "k_min": 3.5, "k_max": 3.5,
                    "adaptive_k": False, "sigma_median": 12.17},
    }, ensure_ascii=False), encoding="utf-8")
    with open(out / "regions.csv", "w", newline="",
              encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(_regions()[0].keys()))
        w.writeheader()
        w.writerows(_regions())
    return tmp_path, stage


def _win(tmp_path):
    from h3d.gui.app import MainWindow
    from h3d.gui.config_edit import ProjectConfig

    cfg = ProjectConfig()
    cfg.name = "p"
    cfg.root_dir = str(tmp_path)
    cfg.item_id = ITEM
    return MainWindow(cfg)


class TestAnomalyTab:
    def test_tab_present_and_loaded_on_open(self, tmp_path, _qapp_session):
        """打开项目就要看到筛查结论（跑完之后才刷新是另一条路径）。"""
        root, _ = _project(tmp_path)
        w = _win(root)
        try:
            panel = w.anomaly_panel
            idx = w.tabs.indexOf(panel)
            assert idx >= 0
            assert w.tabs.tabText(idx) == "异常筛查"
            assert panel.summary.status == "ok"
        finally:
            w.close()

    def test_summary_line_carries_readable_numbers(self, tmp_path,
                                                   _qapp_session):
        """摘要行：面积（两位小数）、图斑数、σ_L、k、岛数。

        这些数字各有各的坑：面积必须两位小数（一位会把 2.18 与 2.24 抹平）、
        k 要能显示区间（自适应打开时逐岛不同）、岛数要说清「按岛比较」——
        1 个岛和 3690 个岛是两种东西。
        """
        root, _ = _project(tmp_path)
        w = _win(root)
        try:
            text = w.anomaly_panel.lbl_summary.text()
            assert "0.24%" in text, text
            assert "2 处" in text, text
            assert "σ_L≈12.2" in text, text
            assert "k=3.5" in text, text
            assert "3690" in text, text
        finally:
            w.close()

    def test_table_lists_every_region(self, tmp_path, _qapp_session):
        """清单要列**全部**图斑（不是 manifest 里被截断的前 10 条）。"""
        root, _ = _project(tmp_path)
        w = _win(root)
        try:
            t = w.anomaly_panel.table
            assert t.rowCount() == 2
            assert t.item(0, 0).text() == "1"
            assert "(40,60)-(70,90)" in t.item(0, 1).text()
            assert "930" in t.item(0, 2).text()
        finally:
            w.close()

    def test_clicking_a_row_shows_that_region(self, tmp_path, _qapp_session):
        """点第 2 行 → 预览的裁剪窗口必须罩住第 2 处。

        这是本页存在的理由。断言「窗口罩住 bbox 且图像已加载」，不测像素——
        像素值取决于热力图配色，那是对着实现写断言。
        """
        root, _ = _project(tmp_path)
        w = _win(root)
        try:
            panel = w.anomaly_panel
            panel.table.selectRow(1)
            assert panel.viewer.has_image(), "点了行却没有载入图像"
            x0, y0, x1, y1 = panel.viewer.current_bbox
            assert x0 < 300 and y0 < 250 and x1 > 330 and y1 > 280, \
                (x0, y0, x1, y1)
        finally:
            w.close()

    def test_not_screened_says_so_instead_of_zero(self, tmp_path,
                                                  _qapp_session):
        """没跑过 → 「未筛查」，不能显示成 0 处异常。"""
        root, _ = _project(tmp_path, products=False)
        w = _win(root)
        try:
            panel = w.anomaly_panel
            assert panel.summary.status == "missing"
            assert panel.table.rowCount() == 0
            assert "未筛查" in panel.lbl_note.text()
            assert "未筛查" in panel.lbl_summary.text() or \
                panel.lbl_summary.text() == ""
            assert not panel.viewer.has_image()
        finally:
            w.close()

    def test_corrupt_products_are_visible_not_silent(self, tmp_path,
                                                     _qapp_session):
        """产物坏了要看得见——不能和「未筛查」印成同一句话。"""
        root, stage = _project(tmp_path)
        (stage / "60_qc" / ITEM / "anomaly" / "metrics.json").write_text(
            "{ 坏 json", encoding="utf-8")
        w = _win(root)
        try:
            panel = w.anomaly_panel
            assert panel.summary.status == "corrupt"
            assert "未筛查" not in panel.lbl_note.text()
            assert "metrics.json" in panel.lbl_note.text()
        finally:
            w.close()

    def test_broken_heatmap_does_not_kill_the_panel(self, tmp_path,
                                                    _qapp_session):
        """热力图是坏文件：表格照常列，点行只提示「图像不可用」，不许抛。

        Qt 槽里抛出的异常不外传（windowed 版 stderr 都是 None），表现就是
        「点了没反应」，比直接报错更难查。所以槽内自己兜住并写进说明行。
        """
        root, stage = _project(tmp_path)
        (stage / "60_qc" / ITEM / "anomaly" / "anomaly_heat.png"
         ).write_bytes(b"not a png")
        w = _win(root)
        try:
            panel = w.anomaly_panel
            assert panel.table.rowCount() == 2
            panel.table.selectRow(0)                  # 不许抛
            assert not panel.viewer.has_image()
            assert panel.lbl_note.text(), "坏图之后连一句说明都没有"
        finally:
            w.close()

    def test_refresh_after_run_picks_up_products(self, tmp_path,
                                                 _qapp_session):
        """跑完一批再刷新：产物出现了，面板要跟上（只读，不重复建什么）。"""
        root, stage = _project(tmp_path, products=False)
        w = _win(root)
        try:
            panel = w.anomaly_panel
            assert panel.summary.status == "missing"
            _project(tmp_path)                        # 产物落盘
            w._refresh_anomaly()
            assert panel.summary.status == "ok"
            assert panel.table.rowCount() == 2
        finally:
            w.close()

    def test_no_root_dir_does_not_crash(self, tmp_path, _qapp_session):
        from h3d.gui.app import MainWindow
        from h3d.gui.config_edit import ProjectConfig

        cfg = ProjectConfig()
        cfg.name = "p"
        cfg.root_dir = ""
        cfg.item_id = ITEM
        w = MainWindow(cfg)
        try:
            assert w.anomaly_panel.table.rowCount() == 0
        finally:
            w.close()


class TestReferenceImage:
    """原图对照：热力图说「哪里」，原图说「那是什么」。

    只有热力图时用户看到的是一团 TURBO 颜色——他知道第 2 处在 (996,31)-(1018,75)，
    却仍要自己打开发散图去找那块。并排放原图，复核这一步才闭环。
    """

    def test_panel_pairs_heatmap_with_reference(self, tmp_path, _qapp_session):
        root, _ = _project(tmp_path)
        w = _win(root)
        try:
            panel = w.anomaly_panel
            assert panel.summary.atlas_png, "读模型没给出原图路径"
            panel.table.selectRow(0)
            assert panel.viewer.has_image()
            assert panel.viewer.has_reference, "没有并排原图，只有热力图"
        finally:
            w.close()

    def test_missing_reference_degrades_gracefully(self, tmp_path,
                                                  _qapp_session):
        """原图不在（被移走/换布局）：只显示热力图，不抛、不空白。"""
        root, stage = _project(tmp_path)
        (stage / "50_tex" / ITEM / f"{ITEM}_L1.0_diffuse.png").unlink()
        w = _win(root)
        try:
            panel = w.anomaly_panel
            assert panel.summary.atlas_png is None
            panel.table.selectRow(0)
            assert panel.viewer.has_image(), "原图缺失不该让热力图也不显示"
            assert not panel.viewer.has_reference
        finally:
            w.close()

    def test_reference_and_heatmap_are_same_size(self, tmp_path,
                                                 _qapp_session):
        """并排后两半必须等高，否则一边被拉伸、位置对不上。"""
        root, _ = _project(tmp_path)
        w = _win(root)
        try:
            panel = w.anomaly_panel
            panel.table.selectRow(1)
            h, wd = panel.viewer.composite_shape
            assert h > 0 and wd > 0
            # 左侧原图 + 分隔条 + 右侧热力图：总宽明显大于高
            assert wd > h * 1.5, (h, wd)
        finally:
            w.close()


class TestReferenceImage:
    """原图对照：热力图说「哪里」，原图说「那是什么」。

    只有热力图时用户看到的是一团 TURBO 颜色——他知道第 2 处在 (996,31)-(1018,75)，
    却仍要自己打开发散图去找那块。并排放原图，复核这一步才闭环。
    """

    def test_panel_pairs_heatmap_with_reference(self, tmp_path, _qapp_session):
        root, _ = _project(tmp_path)
        w = _win(root)
        try:
            panel = w.anomaly_panel
            assert panel.summary.atlas_png, "读模型没给出原图路径"
            panel.table.selectRow(0)
            assert panel.viewer.has_image()
            assert panel.viewer.has_reference, "没有并排原图，只有热力图"
        finally:
            w.close()

    def test_missing_reference_degrades_gracefully(self, tmp_path,
                                                  _qapp_session):
        """原图不在（被移走/换布局）：只显示热力图，不抛、不空白。"""
        root, stage = _project(tmp_path)
        (stage / "50_tex" / ITEM / f"{ITEM}_L1.0_diffuse.png").unlink()
        w = _win(root)
        try:
            panel = w.anomaly_panel
            assert panel.summary.atlas_png is None
            panel.table.selectRow(0)
            assert panel.viewer.has_image(), "原图缺失不该让热力图也不显示"
            assert not panel.viewer.has_reference
        finally:
            w.close()

    def test_reference_and_heatmap_are_same_size(self, tmp_path,
                                                 _qapp_session):
        """并排后两半必须等高，否则一边被拉伸、位置对不上。"""
        root, _ = _project(tmp_path)
        w = _win(root)
        try:
            panel = w.anomaly_panel
            panel.table.selectRow(1)
            h, wd = panel.viewer.composite_shape
            assert h > 0 and wd > 0
            # 左侧原图 + 分隔条 + 右侧热力图：总宽明显大于高
            assert wd > h * 1.5, (h, wd)
        finally:
            w.close()
