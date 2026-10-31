"""tests.test_anomaly_summary : 异常筛查产物的**读模型**（无 Qt）。

为什么单独一个模块
------------------
界面要显示「这版图集哪里可疑」，但它自己不该知道 ``metrics.json`` 里哪个键
叫什么、``regions.csv`` 有几列。把这些收进一个只读、可单测的入口，界面只剩
装配 —— 也让 CLI／报表以后能复用同一份读法。

三条状态必须分得开（r16 那类缺陷的同构检查）
--------------------------------------------
``ok`` / ``missing``（没跑过）/ ``corrupt``（跑了但读不出来）。把后两者都当
「0 处异常」是最坏的写法：用户看到「0」，会以为「查过了、很干净」，而其实是
没查或查坏了。所以状态与说明文字是本模块的主要输出，数字反而是附带的。
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

ITEM = "relic_001"


def _write_png(path, shape=(64, 64), value=128):
    from PIL import Image

    os.makedirs(os.path.dirname(path), exist_ok=True)
    arr = np.full((shape[0], shape[1], 3), value, np.uint8)
    Image.fromarray(arr).save(path)


def _summary_json(**over) -> dict:
    base = {
        "item_id": ITEM,
        "atlas": f"50_tex/{ITEM}/{ITEM}_L1.0_diffuse.png",
        "mask": f"50_tex/{ITEM}/{ITEM}_L1.0_mask.png",
        "size": [2048, 2048],
        "config": {"scales": [31, 81, 201], "k": 3.5, "per_island": True},
        "metrics": {
            "island_ratio": 0.615,
            "island_count": 3690,
            "per_island": True,
            "anomalous_ratio": 0.002425,
            "k": 3.5,
            "k_min": 3.5,
            "k_max": 3.5,
            "adaptive_k": False,
            "n_regions": 2,
            "sigma_median": 12.17,
            "score_p99": 3.54,
        },
    }
    base["metrics"].update(over.pop("metrics", {}))
    base.update(over)
    return base


REGIONS = [
    {"region_id": 1, "x0": 939, "y0": 1746, "x1": 983, "y1": 1816,
     "texels": 1103, "area_ratio": 0.000428, "score_mean": 4.06,
     "score_max": 5.439},
    {"region_id": 2, "x0": 996, "y0": 31, "x1": 1018, "y1": 75,
     "texels": 529, "area_ratio": 0.000205, "score_mean": 4.253,
     "score_max": 6.222},
]


def _project(tmp_path, *, metrics=True, regions=True, heat=True):
    """造一个只有 X3 产物的项目（``阶段成果/60_qc/<件>/anomaly/``）。"""
    stage = tmp_path / "阶段成果"
    out = stage / "60_qc" / ITEM / "anomaly"
    out.mkdir(parents=True)
    if metrics:
        (out / "metrics.json").write_text(
            json.dumps(_summary_json(), ensure_ascii=False), encoding="utf-8")
    if regions:
        import csv

        with open(out / "regions.csv", "w", newline="",
                  encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(REGIONS[0].keys()))
            w.writeheader()
            w.writerows(REGIONS)
    if heat:
        _write_png(str(out / "anomaly_heat.png"))
        _write_png(str(out / "anomaly_mask.png"), value=0)
    return stage


class TestReadModelStates:
    def test_ok_reads_metrics_and_all_regions(self, tmp_path):
        from h3d.anomaly.summary import load_summary

        s = load_summary(str(_project(tmp_path)), ITEM)
        assert s.status == "ok", s.note
        assert s.metrics["anomalous_ratio"] == pytest.approx(0.002425)
        assert s.metrics["n_regions"] == 2
        assert [r.region_id for r in s.regions] == [1, 2]
        # 面板要能翻**全部**图斑，不是 manifest 里的前 10 条
        assert s.regions[0].bbox == (939, 1746, 983, 1816)
        assert s.regions[1].texels == 529
        # 热力图与掩膜的绝对路径要能给出（面板拿它裁图）
        assert s.heat_png and os.path.isfile(s.heat_png)
        assert s.size == (2048, 2048)

    def test_missing_says_not_screened_not_zero(self, tmp_path):
        """没跑过 → 「未筛查」，**绝不能**表现成「0 处异常」。"""
        from h3d.anomaly.summary import load_summary

        stage = tmp_path / "阶段成果"
        (stage / "60_qc" / ITEM).mkdir(parents=True)
        s = load_summary(str(stage), ITEM)
        assert s.status == "missing"
        assert "未筛查" in s.note
        assert s.metrics == {}
        assert s.regions == []

    def test_corrupt_metrics_is_reported_loudly(self, tmp_path):
        """有产物但读不出来 → 必须**报出来**，不许静默降级成「未筛查」。"""
        from h3d.anomaly.summary import load_summary

        stage = _project(tmp_path)
        (stage / "60_qc" / ITEM / "anomaly" / "metrics.json").write_text(
            "{ 这不是 json", encoding="utf-8")
        s = load_summary(str(stage), ITEM)
        assert s.status == "corrupt"
        assert s.note, "坏产物必须有可读的原因"
        assert "metrics.json" in s.note

    def test_missing_regions_keeps_metrics_but_warns(self, tmp_path):
        """清单缺了但汇总还在：数字可用，**必须**说明清单不可见。

        这两份产物独立写，一份坏不该让另一份也变成「未筛查」——但也不能假装
        清单是空的（那就是 r16 的「0 行无法区分全部合格与一条都没查」）。
        """
        from h3d.anomaly.summary import load_summary

        s = load_summary(str(_project(tmp_path, regions=False)), ITEM)
        assert s.status == "ok"
        assert s.metrics["n_regions"] == 2
        assert s.regions == []
        assert "清单" in s.note and "regions.csv" in s.note

    def test_region_count_mismatch_is_flagged(self, tmp_path):
        """清单条数 ≠ 汇总里的图斑数 → 产物过期，必须提示重跑。"""
        from h3d.anomaly.summary import load_summary

        stage = _project(tmp_path)
        p = stage / "60_qc" / ITEM / "anomaly" / "metrics.json"
        p.write_text(json.dumps(_summary_json(metrics={"n_regions": 7}),
                                ensure_ascii=False), encoding="utf-8")
        s = load_summary(str(stage), ITEM)
        assert s.status == "ok"
        assert "过期" in s.note or "不一致" in s.note, s.note

    def test_list_screened_items(self, tmp_path):
        from h3d.anomaly.summary import list_screened_items

        stage = _project(tmp_path)
        (stage / "60_qc" / "relic_002").mkdir(parents=True)   # 有目录没产物
        assert list_screened_items(str(stage)) == [ITEM]


class TestPanelText:
    """界面文案：纯文本标签，不能夹 Markdown 标记。

    实机截图抓到过：`note_line` 里写了 `**待人工复核**`，而 ``ElidedLabel`` 是纯文本
    标签，用户在界面上看到的是带星号的 ``**待人工复核**``。测试断言文案内容、却
    从没断言「没有标记」，所以全绿而界面难看。这类问题只能靠「渲染出来看」或
    这条断言兜住。
    """

    def test_note_and_summary_have_no_markup(self, tmp_path):
        from h3d.anomaly.summary import load_summary, note_line, summary_line

        for status_dir in ("ok", "missing"):
            stage = (_project(tmp_path / status_dir) if status_dir == "ok"
                     else tmp_path / status_dir / "阶段成果")
            if status_dir == "missing":
                (stage / "60_qc" / ITEM).mkdir(parents=True)
            sm = load_summary(str(stage), ITEM)
            for text in (summary_line(sm), note_line(sm)):
                assert "**" not in text, text
                assert "`" not in text, text

    def test_note_mentions_not_screened_for_missing(self, tmp_path):
        from h3d.anomaly.summary import load_summary, note_line

        stage = tmp_path / "阶段成果"
        (stage / "60_qc" / ITEM).mkdir(parents=True)
        text = note_line(load_summary(str(stage), ITEM))
        assert "未筛查" in text and "不等于" in text, text


class TestPanelText:
    """界面文案：纯文本标签，不能夹 Markdown 标记。

    实机截图抓到过：`note_line` 里写了 `**待人工复核**`，而 ``ElidedLabel`` 是纯文本
    标签，用户在界面上看到的是带星号的 ``**待人工复核**``。测试断言文案内容、却
    从没断言「没有标记」，所以全绿而界面难看。这类问题只能靠「渲染出来看」或
    这条断言兜住。
    """

    def test_note_and_summary_have_no_markup(self, tmp_path):
        from h3d.anomaly.summary import load_summary, note_line, summary_line

        for status_dir in ("ok", "missing"):
            stage = (_project(tmp_path / status_dir) if status_dir == "ok"
                     else tmp_path / status_dir / "阶段成果")
            if status_dir == "missing":
                (stage / "60_qc" / ITEM).mkdir(parents=True)
            sm = load_summary(str(stage), ITEM)
            for text in (summary_line(sm), note_line(sm)):
                assert "**" not in text, text
                assert "`" not in text, text

    def test_note_mentions_not_screened_for_missing(self, tmp_path):
        from h3d.anomaly.summary import load_summary, note_line

        stage = tmp_path / "阶段成果"
        (stage / "60_qc" / ITEM).mkdir(parents=True)
        text = note_line(load_summary(str(stage), ITEM))
        assert "未筛查" in text and "不等于" in text, text


class TestCropBox:
    """「点一处跳过去」要裁哪一块 —— 纯几何，与界面无关。"""

    def test_crop_includes_region_with_margin(self):
        from h3d.anomaly.summary import crop_box

        x0, y0, x1, y1 = crop_box((900, 1700, 1000, 1800), (2048, 2048))
        assert x0 < 900 and y0 < 1700 and x1 > 1000 and y1 > 1800
        # 边距按斑块尺寸给：太小看不出周边，太大整张图都进来了
        assert 900 - x0 == pytest.approx(100, abs=40)

    def test_crop_is_clamped_to_image(self):
        from h3d.anomaly.summary import crop_box

        x0, y0, x1, y1 = crop_box((0, 0, 10, 10), (2048, 2048))
        assert (x0, y0) == (0, 0)
        x0, y0, x1, y1 = crop_box((2038, 2028, 2047, 2047), (2048, 2048))
        assert x1 <= 2048 and y1 <= 2048
        assert x0 > 1900 and y0 > 1900, "裁窗口不能被边距挤成空"

    def test_tall_and_wide_regions_stay_visible(self):
        """长条形病变（沿裂隙）不能因为一维大就把另一维裁成一条线。"""
        from h3d.anomaly.summary import crop_box

        x0, y0, x1, y1 = crop_box((100, 100, 900, 130), (2048, 2048))
        assert (x1 - x0) >= 800 and (y1 - y0) >= 200
