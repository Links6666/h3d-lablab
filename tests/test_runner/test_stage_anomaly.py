"""tests.test_runner.test_stage_anomaly : X3 单期异常检测接进编排层（B2 集成）。

检测算法本身在 ``tests/test_anomaly_detect.py``（合成判据 12 项）。这里只锁
**接线**，四个接缝各锁一件事：

* ``_stage_anomaly_qc`` 的返回值 —— 值不值得跑、跑出什么、跳过怎么说；
* ``STAGE_REGISTRY`` 的顺序与 ``_stage_enabled`` / ``_disabled_reason`` ——
  界面、日志、终态认不认识这个阶段；
* manifest 白名单 + HTML 报表 —— 数字**能不能被引用**（r16 手写白名单陷阱：
  上游算出的键不写进 ``manifest_metrics`` 就等于没算）；
* 端到端产物落盘 —— ``60_qc/<件>/anomaly/`` 下四件齐。

为什么这层测试值得写：X3 是**可选 QC 阶段**，它的失败绝不该让一件文物建模失败，
但它又必须**看得见**（跳过要带原因、指标要进报表）。这两条互斥的方向只能用
终态语义来锁，靠人肉 review 保不住。
"""

from __future__ import annotations

import os

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _atlas(size: int = 192, *, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """造一对图集/掩膜：平滑渐变 + 圆形岛内（沿用算法测试的口径）。"""
    y, x = np.mgrid[0:size, 0:size].astype(np.float64)
    rgb = np.clip(np.stack([120.0 + 40.0 * x / size,
                            110.0 + 30.0 * y / size,
                            100.0 + 20.0 * (x + y) / (2.0 * size)], axis=-1),
                  0, 255).astype(np.uint8)
    c = (size - 1) / 2.0
    island = (((x - c) ** 2 + (y - c) ** 2) <= (size * 0.45) ** 2)
    return rgb, island


def _write_atlas(root: str, item: str = "r1", *, size: int = 192) -> str:
    """把图集/掩膜写进 ``50_tex/<item>/``（模拟 M4 产物），返回项目根。"""
    from PIL import Image

    rgb, island = _atlas(size)
    d = os.path.join(root, "50_tex", item)
    os.makedirs(d, exist_ok=True)
    Image.fromarray(rgb).save(os.path.join(d, f"{item}_L1.0_diffuse.png"))
    Image.fromarray((island.astype(np.uint8) * 255)).save(
        os.path.join(d, f"{item}_L1.0_mask.png"))
    return root


def _cfg(tmp_path, *, enabled: bool = True):
    from h3d.core import PipelineConfig

    cfg = PipelineConfig()
    cfg.project.raw_dir = str(tmp_path / "00_raw")
    cfg.project.formats = ["ply"]
    cfg.runner.fail_isolation = True
    cfg.runner.manifest = str(tmp_path / "90_meta" / "manifest.json")
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
    cfg.report.enabled = False
    cfg.runner.x3_enabled = enabled
    return cfg


def _item(tmp_path, item: str = "r1"):
    from h3d.runner.task import Task

    return Task(item_id=item, raw_paths=[], root=str(tmp_path))


# ---------------------------------------------------------------------------
# 接缝 A：阶段函数本身
# ---------------------------------------------------------------------------
class TestStageContract:
    def test_missing_atlas_is_skipped_with_readable_reason(self, tmp_path):
        """没有图集 → SKIPPED，且原因里点明缺的是哪个文件。

        「跳过」不是失败，但**必须能区分**「没图集」与「配置关了」：用户要据此
        决定是去跑 M4 还是去改配置。只给一个 SKIPPED 等于让人猜。
        """
        from h3d.runner import _stage_anomaly_qc
        from h3d.runner.task import TaskStatus

        res = _stage_anomaly_qc(_item(tmp_path), _cfg(tmp_path))

        assert res.status == TaskStatus.SKIPPED
        reason = str(res.metrics.get("skip_reason") or "")
        assert "50_tex" in reason, reason

    def test_atlas_produces_four_artifacts_and_metrics(self, tmp_path):
        """有图集 → DONE，``60_qc/<件>/anomaly/`` 下四件齐，指标键齐。

        四个产物缺一不可：热力图与掩膜是给人复核的，CSV/JSON 是给报表和论文
        取数的。只返回内存数组等于「跑过一次」，复核还得自己写脚本。
        """
        from h3d.runner import _stage_anomaly_qc
        from h3d.runner.task import TaskStatus

        _write_atlas(str(tmp_path))
        res = _stage_anomaly_qc(_item(tmp_path), _cfg(tmp_path))

        assert res.status == TaskStatus.DONE, res.metrics
        out_dir = os.path.join(tmp_path, "60_qc", "r1", "anomaly")
        for name in ("anomaly_heat.png", "anomaly_mask.png",
                     "regions.csv", "metrics.json"):
            assert os.path.isfile(os.path.join(out_dir, name)), name
        # 报表要引用的键必须就在 metrics 里（不是只写进 json 文件）
        for key in ("anomaly_ratio", "anomaly_regions", "anomaly_k"):
            assert key in res.metrics, key
        assert res.metrics["anomaly_area_units"] == "texel_ratio"
        # 可检下限的可读形式：本件的纹理尺度 σ_L（Lab 单位）
        assert res.metrics["anomaly_sigma_median"] > 0

    def test_broken_atlas_degrades_instead_of_failing_the_item(self, tmp_path):
        """图集是坏 PNG → 仍然 SKIPPED（不抛异常）。

        这一条是**方向性**的：X3 是可选 QC，它读的图是 M4 的产物，可能因为上游
        异常、文件被占、磁盘半写而读不出来。此时若抛出去，编排层会把这件文物
        判失败——为了一份「附加筛查」而否定已经建好的模型，代价与收益完全不成
        比例。所以阶段内部自己兜住，把原因写进 skip_reason。
        """
        from h3d.runner import _stage_anomaly_qc
        from h3d.runner.task import TaskStatus

        d = os.path.join(str(tmp_path), "50_tex", "r1")
        os.makedirs(d, exist_ok=True)
        for suffix in ("_L1.0_diffuse.png", "_L1.0_mask.png"):
            with open(os.path.join(d, f"r1{suffix}"), "wb") as fh:
                fh.write(b"not a png")

        res = _stage_anomaly_qc(_item(tmp_path), _cfg(tmp_path))
        assert res.status == TaskStatus.SKIPPED
        assert "X3" in str(res.metrics.get("skip_reason") or "") or \
            "读" in str(res.metrics.get("skip_reason") or "")


# ---------------------------------------------------------------------------
# 接缝 B：编排层认不认识它
# ---------------------------------------------------------------------------
class TestPipelineWiring:
    def test_registered_between_m4_and_m5(self):
        """X3 必须排在 M4 之后、M5 之前。

        顺序不是审美：报表在 **M5** 里生成，X3 排在 M5 之后就等于「报表永远读
        不到异常指标」；而它又依赖 M4 的图集。两头夹死，位置只有这一个。
        """
        from h3d.runner import STAGE_REGISTRY

        order = list(STAGE_REGISTRY)
        assert "X3" in order, order
        assert order.index("M4") < order.index("X3") < order.index("M5"), order

    def test_disabled_by_config_reports_readable_reason(self, tmp_path):
        """``runner.x3_enabled=false`` → 不进启用集，原因要能读出「配置关闭」。"""
        from h3d.runner import _stage_anomaly_qc
        from h3d.runner.batch_processor import _disabled_reason, _stage_enabled
        from h3d.runner.task import TaskStatus

        cfg = _cfg(tmp_path, enabled=False)
        assert _stage_enabled("X3", cfg) is False
        assert "配置关闭" in _disabled_reason("X3", cfg)

        _write_atlas(str(tmp_path))
        res = _stage_anomaly_qc(_item(tmp_path), cfg)
        assert res.status == TaskStatus.SKIPPED
        assert "x3_enabled" in str(res.metrics.get("skip_reason") or "")

    def test_optional_stage_so_failure_never_fails_the_item(self, tmp_path):
        """X3 属于可选阶段：失败降级，不连坐整件；但**不进**质量门。"""
        from h3d.runner.batch_processor import OPTIONAL_STAGES

        assert "X3" in OPTIONAL_STAGES


# ---------------------------------------------------------------------------
# 接缝 B'：界面/进度认不认识它
# ---------------------------------------------------------------------------
class TestProgressAndLamps:
    def test_lamp_order_puts_x3_before_m5(self):
        """界面灯顺序与内核一致，且 X3 在 M5 之前（否则报表段永远读不到）。"""
        from h3d.gui.logparse import STAGE_ORDER
        from h3d.runner import STAGE_REGISTRY

        missing = [s for s in STAGE_REGISTRY if s not in STAGE_ORDER]
        assert not missing, f"界面缺少阶段灯：{missing}"
        assert STAGE_ORDER.index("M4") < STAGE_ORDER.index("X3") \
            < STAGE_ORDER.index("M5")

    def test_weights_still_sum_to_one(self):
        """两套权重各加一个 X3 之后必须仍归一 —— 否则进度条上限就不是 100%。"""
        from h3d.gui.logparse import W_PHOTO, W_SCAN

        for name, table in (("W_PHOTO", W_PHOTO), ("W_SCAN", W_SCAN)):
            assert "X3" in table, f"{name} 没有 X3 的权重位"
            total = sum(table.values())
            assert abs(total - 1.0) < 1e-9, f"{name} 之和 {total}"

    def test_x3_is_a_core_stage(self):
        """X3 默认开 → 属于「每件都会跑」的核心集（决定分母与完成判据）。"""
        from h3d.gui.logparse import CORE_STAGES

        assert "X3" in CORE_STAGES

    def test_log_lines_are_parsed(self):
        """开始/完成/跳过三种行都要被认出，否则灯停在上一阶段，看着像卡死。"""
        from h3d.gui.logparse import (stage_done_of, stage_skip_of,
                                      stage_start_of)

        assert stage_start_of("[INFO] ▶ X3 开始：relic_001") == "X3"
        assert stage_done_of(
            "[INFO] [h3d.runner] X3 完成：relic_001（异常 2.18% 岛内面积，"
            "35 处，k=3.50）") == "X3"
        assert stage_skip_of(
            "○ X3 跳过：relic_001（缺图集 50_tex/relic_001/…）") == "X3"


# ---------------------------------------------------------------------------
# 接缝 B'：界面/进度认不认识它
# ---------------------------------------------------------------------------
class TestProgressAndLamps:
    def test_lamp_order_puts_x3_before_m5(self):
        """界面灯顺序与内核一致，且 X3 在 M5 之前（否则报表段永远读不到）。"""
        from h3d.gui.logparse import STAGE_ORDER
        from h3d.runner import STAGE_REGISTRY

        missing = [s for s in STAGE_REGISTRY if s not in STAGE_ORDER]
        assert not missing, f"界面缺少阶段灯：{missing}"
        assert STAGE_ORDER.index("M4") < STAGE_ORDER.index("X3") \
            < STAGE_ORDER.index("M5")

    def test_weights_still_sum_to_one(self):
        """两套权重各加一个 X3 之后必须仍归一 —— 否则进度条上限就不是 100%。"""
        from h3d.gui.logparse import W_PHOTO, W_SCAN

        for name, table in (("W_PHOTO", W_PHOTO), ("W_SCAN", W_SCAN)):
            assert "X3" in table, f"{name} 没有 X3 的权重位"
            total = sum(table.values())
            assert abs(total - 1.0) < 1e-9, f"{name} 之和 {total}"

    def test_x3_is_a_core_stage(self):
        """X3 默认开 → 属于「每件都会跑」的核心集（决定分母与完成判据）。"""
        from h3d.gui.logparse import CORE_STAGES

        assert "X3" in CORE_STAGES

    def test_log_lines_are_parsed(self):
        """开始/完成/跳过三种行都要被认出，否则灯停在上一阶段，看着像卡死。"""
        from h3d.gui.logparse import (stage_done_of, stage_skip_of,
                                      stage_start_of)

        assert stage_start_of("[INFO] ▶ X3 开始：relic_001") == "X3"
        assert stage_done_of(
            "[INFO] [h3d.runner] X3 完成：relic_001（异常 2.18% 岛内面积，"
            "35 处，k=3.50）") == "X3"
        assert stage_skip_of(
            "○ X3 跳过：relic_001（缺图集 50_tex/relic_001/…）") == "X3"


# ---------------------------------------------------------------------------
# 接缝 C：数字能不能被引用（manifest 白名单 + HTML）
# ---------------------------------------------------------------------------
def _m5_project(tmp_path) -> object:
    """造一个能跑通 M5 的最小项目：网格 + 参考点云 + 图集 + 预置的 X3 结果。"""
    import open3d as o3d

    from h3d.core import PipelineConfig
    from h3d.runner.task import StageResult, Task, TaskStatus

    root = str(tmp_path)
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=1.0)
    mesh.compute_vertex_normals()
    d = os.path.join(root, "30_dense", "r1")
    os.makedirs(d, exist_ok=True)
    o3d.io.write_triangle_mesh(os.path.join(d, "r1.ply"), mesh)
    reg = os.path.join(root, "20_registered", "r1")
    os.makedirs(reg, exist_ok=True)
    ref = os.path.join(reg, "r1_s01.ply")
    o3d.io.write_point_cloud(ref, mesh.sample_points_uniformly(8000))
    _write_atlas(root)

    cfg = PipelineConfig()
    cfg.project.raw_dir = os.path.join(root, "00_raw")
    cfg.project.formats = ["ply"]
    cfg.report.enabled = False
    cfg.runner.manifest = os.path.join(root, "90_meta", "manifest.json")
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)

    task = Task(item_id="r1", root=root, raw_paths=["dummy.ply"])
    task.results["M2"] = StageResult("M2", TaskStatus.DONE, [ref], {})
    task.results["X3"] = StageResult("X3", TaskStatus.DONE, [], {
        "anomaly_ratio": 0.0218, "anomaly_regions": 35,
        "anomaly_largest_ratio": 0.0012, "anomaly_k": 3.5,
        "anomaly_area_units": "texel_ratio",
        "anomaly_atlas": "50_tex/r1/r1_L1.0_diffuse.png",
        "anomaly_atlas_digest": "0123456789ab",
        "anomaly_sigma_median": 30.3,
    })
    return task, cfg


class TestMetricsReachTheReport:
    def test_m5_manifest_carries_anomaly_metrics(self, tmp_path):
        """M5 的白名单必须带上异常指标。

        这是 r16 那个坑的同构检查：X3 把数字算出来了，但 ``manifest_metrics``
        是**手写 dict**，不写进去 → manifest 没有 → HTML/CSV/论文表全读不到，
        而界面上一切正常。所以断言点选在「M5 返回的 metrics」上，而不是
        「X3 有没有算」。
        """
        from h3d.runner import _stage_quality

        task, cfg = _m5_project(tmp_path)
        res = _stage_quality(task, cfg)
        m5 = res.metrics
        assert m5["anomaly_ratio"] == pytest.approx(0.0218)
        assert m5["anomaly_regions"] == 35
        assert m5["anomaly_area_units"] == "texel_ratio"
        # 结论要能对上是「基于哪一版图集」
        assert m5["anomaly_atlas"].endswith("_L1.0_diffuse.png")
        assert m5["anomaly_atlas_digest"] == "0123456789ab"
        # 「可检下限」必须跟着走：没有它，漏检只能被读成「没有病害」
        assert m5["anomaly_sigma_median"] == pytest.approx(30.3)

    def test_anomaly_does_not_touch_the_quality_gate(self, tmp_path):
        """异常面积是参考信息，**不得**参与达标判定。

        一件有好几处真实病害的文物，建模本身完全合格。把病害多算成建模失败，
        等于让 QC 反过来惩罚被检对象。
        """
        from h3d.runner import _stage_quality

        task, cfg = _m5_project(tmp_path)
        ok_res = _stage_quality(task, cfg)
        # 把异常面积拉到 90% 再跑一次，判定必须不变
        task.results["X3"].metrics["anomaly_ratio"] = 0.9
        task.results["X3"].metrics["anomaly_regions"] = 999
        bad_res = _stage_quality(task, cfg)
        assert bad_res.metrics["anomaly_ratio"] > 0.5
        assert bad_res.status == ok_res.status

    def test_html_shows_anomaly_columns_and_caveat(self, tmp_path):
        """HTML 纹理段落要能读出异常面积/图斑数，并写明岛边缘不可靠。"""
        from h3d.core import PipelineConfig, RunManifest
        from h3d.report.html_export import generate_html

        cfg = PipelineConfig()
        cfg.runner.manifest = os.path.join(str(tmp_path), "90_meta",
                                          "run_manifest.json")
        os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
        m = RunManifest(config_sha256="abc123")
        m.record("r1/M4", "done", [], 70.0, texture_size=2048,
                 atlas_utilization=0.615, texture_coverage=0.55)
        m.record("r1/X3", "done", [], 3.3, anomaly_ratio=0.0218,
                 anomaly_regions=35, anomaly_area_units="texel_ratio",
                 anomaly_atlas_digest="0123456789ab")
        m.record("r1/M5", "done", [], 1.0, coverage=0.9, rmse_mm=2.0,
                 mps_m=0.001, anomaly_ratio=0.0218, anomaly_regions=35,
                 anomaly_sigma_median=30.3)
        m.save(cfg.runner.manifest)

        out = os.path.join(str(tmp_path), "report.html")
        generate_html(RunManifest.load(cfg.runner.manifest),
                      out_path=out, project="t")
        with open(out, encoding="utf-8") as fh:
            html = fh.read()

        assert "异常面积" in html and "图斑" in html
        assert "2.18%" in html, "异常面积没渲染出来"
        assert "35" in html
        assert "岛边缘" in html, "缺少「岛边缘 1~2 纹素不可靠」的口径说明"
        # 纹理尺度要能在同一格里读出来（不加列，避免 16 列的表再变宽）
        assert "σ_L" in html, "报出了异常面积却没给「这件的纹理尺度有多大」"

    def test_accuracy_csv_has_anomaly_columns(self, tmp_path):
        """``accuracy.csv``（论文表的取数口）要带异常两列。

        没有 X3 时**必须印「未筛查」**：印成 0.00% 会被读成「查过了、很干净」，
        这是两件完全不同的事。
        """
        import csv

        from h3d.runner import _stage_quality

        task, cfg = _m5_project(tmp_path)
        _stage_quality(task, cfg)
        path = os.path.join(str(tmp_path), "70_report", "r1", "accuracy.csv")
        with open(path, encoding="utf-8-sig") as fh:
            header, row = list(csv.reader(fh))[:2]
        assert "异常面积(岛内)" in header
        assert "图斑数" in header
        assert row[header.index("异常面积(岛内)")] == "2.18%"
        assert row[header.index("图斑数")] == "35"
        assert row[header.index("纹理尺度σ_L(Lab)")] == "30.3"

        # 再来一遍但不带 X3 结果：同一列必须写「未筛查」
        task2, cfg2 = _m5_project(tmp_path / "no_x3")
        task2.results.pop("X3")
        _stage_quality(task2, cfg2)
        p2 = os.path.join(str(tmp_path / "no_x3"), "70_report", "r1",
                          "accuracy.csv")
        with open(p2, encoding="utf-8-sig") as fh:
            header2, row2 = list(csv.reader(fh))[:2]
        assert row2[header2.index("异常面积(岛内)")] == "未筛查"
