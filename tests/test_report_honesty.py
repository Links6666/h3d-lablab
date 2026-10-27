"""tests.test_report_honesty : 「数字好看但不成立」的口径必须写进报表（2026-10-26）。

实机背景
--------
1.1.11 真机测试时**没有三维扫描仪**，把路线二（纯照片）生成的 PLY 放进
``00_raw`` 冒充激光件跑路线一。结果报表给出：

    几何来源：激光扫描　｜　坐标口径：米制（激光锚定）　｜　中误差 38.26 mm
    纹理承载：obj　｜　实际格式：ply　｜　判定：pass

五处全是"看着挺好，实际不成立"：
① 中误差 38.26 mm > 5 mm 阈值，却判 pass——因为参考点云 MPS 23.6 mm 粗于阈值，
   毫米门根本没启用；报表脚注还反着写"判门仍以毫米阈值为准"。
② 坐标被当米制，而那个"文物"对角线算出来 6.09 米（中小型文物定义 5~30 cm）。
③ 承载写 obj，交付格式只有 ply——图集 PNG 是孤立文件，打开模型是白模。
④ 00_raw 与 SfM 稠密模型 novel 仅 7.7%，几何自证，M2 必然"无需配准"。
⑤ 自动诊断对以上四条全部失声，只写「未发现指标层面的明显异常」。

本组测试把这些判据逐条钉住；任何一条回归，报表就会重新开始骗人。
"""
from __future__ import annotations

import csv
import json
import os
import re

import pytest

from h3d.core import QualityConfig
from h3d.core.metadata import RunManifest
from h3d.quality import check_quality
from h3d.quality.gate import build_report
from h3d.report.html_export import (_findings, _units_plausible,
                                   generate_html)
from h3d.thesis_tables import export_thesis_table


# --------------------------------------------------------------------- 构造 --
def _m5(metric_units=True, rmse_mm=38.2577, diag=6.086324,
        mps=0.023566, reliable=False, plausible=None):
    """一份与实机同形的 M5 指标（默认即真机 1.1.11 的值）。"""
    m = {
        "coverage": 0.9147,
        "rmse_mm": rmse_mm,
        "rmse_units": rmse_mm / 1000.0,
        "rmse_rel": rmse_mm / 1000.0 / diag,
        "rmse_unit": "mm" if metric_units else "模型单位",
        "metric_units": metric_units,
        "model_bbox_diag": diag,
        "rmse_reliable": reliable,
        "mps_m": mps,
        "boundary_ratio": 0.0025,
        "texture_sharpness": 72.9,
        "texture_seam": 2.28,
        "texture_coverage": 0.5897,
        "texture_fill_ratio": 0.932,
        "texture_ok": True,
    }
    if plausible is not None:
        m["units_plausible"] = plausible
    return m


# ------------------------------------------------------- ① 单位量级自洽检查 --
class TestUnitsPlausibility:
    def test_report_side_infers_from_bbox(self):
        """旧 manifest 没有 units_plausible 时按包围盒现推，不能漏判。"""
        assert _units_plausible({"metric_units": True,
                                 "model_bbox_diag": 6.086}) is False
        assert _units_plausible({"metric_units": True,
                                 "model_bbox_diag": 0.42}) is True

    def test_no_units_needs_no_plausibility(self):
        """已经是「任意单位」就无需判量级，否则会重复告警。"""
        assert _units_plausible({"metric_units": False,
                                 "model_bbox_diag": 6.086}) is True

    def test_gate_skips_mm_check_when_units_suspect(self):
        """单位存疑 → 毫米门不成立（哪怕 mps 很细）。"""
        cfg = QualityConfig(coverage_threshold=0.8, rmse_threshold_mm=5.0)
        assert check_quality({"coverage": 0.95, "rmse_mm": 38.0,
                              "rmse_reliable": True,
                              "units_plausible": False}, cfg) is True

    def test_gate_still_gates_when_units_plausible(self):
        cfg = QualityConfig(coverage_threshold=0.8, rmse_threshold_mm=5.0)
        assert check_quality({"coverage": 0.95, "rmse_mm": 8.0,
                              "rmse_reliable": True,
                              "units_plausible": True}, cfg) is False


# --------------------------------------------- ② accuracy.csv 的行内口径列 --
class TestAccuracyCsvScope:
    def _row(self, tmp_path, metrics):
        qc, rpt = tmp_path / "60_qc" / "x", tmp_path / "70_report" / "x"
        build_report("relic_001", metrics, QualityConfig(),
                     str(qc), str(rpt))
        with open(rpt / "accuracy.csv", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        return rows[0], rows[1]

    def test_column_present(self, tmp_path):
        head, _ = self._row(tmp_path, _m5())
        assert "中误差口径" in head, "不写口径，读者只会记住那个毫米数"

    def test_sparse_reference_marked_not_judged(self, tmp_path):
        head, row = self._row(tmp_path, _m5(reliable=False, plausible=True))
        assert row[head.index("中误差口径")] == "不判门（参考稀疏）"

    def test_suspect_units_marked_not_judged(self, tmp_path):
        head, row = self._row(tmp_path, _m5(reliable=False, plausible=False))
        assert row[head.index("中误差口径")] == "不判门（单位存疑）"

    def test_measured_scope_when_really_judged(self, tmp_path):
        head, row = self._row(tmp_path, _m5(rmse_mm=1.2, diag=0.42, mps=0.002,
                                            reliable=True, plausible=True))
        assert row[head.index("中误差口径")] == "实测（参与 5 mm 判门）"


# ------------------------------------------- ③ 论文表格必须带同一口径列 --
class TestThesisTableScope:
    def _export(self, tmp_path, m5):
        (tmp_path / "output").mkdir()
        manifest = {"stages": {"relic_001/M5": dict(m5, status="done"),
                               "relic_001/S1": {"n_registered": 30,
                                                "n_photos": 30}}}
        (tmp_path / "output" / "run_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8")
        path = export_thesis_table(str(tmp_path))
        with open(path, encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        return rows[0], rows[1]

    def test_scope_column(self, tmp_path):
        head, row = self._export(tmp_path, _m5(plausible=False))
        assert "M5中误差口径" in head
        assert row[head.index("M5中误差口径")] == "不判门（单位存疑）"

    def test_old_manifest_falls_back(self, tmp_path):
        """r15 之前的 manifest 没有 rmse_scope，也必须推出正确口径。"""
        old = _m5()
        old.pop("rmse_reliable", None)
        head, row = self._export(tmp_path, old)
        assert row[head.index("M5中误差口径")] == "不判门（单位存疑）"

    def test_no_anchor_is_nondimensional(self, tmp_path):
        head, row = self._export(tmp_path, _m5(metric_units=False))
        assert row[head.index("M5中误差口径")] == "无量纲（SfM 任意单位）"


# ------------------------------------------------------- ④ 报表文本与诊断 --
def _manifest(items=("relic_001",)) -> RunManifest:
    m = RunManifest(run_id="t-run", config_sha256="x")
    for it in items:
        m.stages[f"{it}/S1"] = {"status": "done", "n_registered": 30,
                                "n_photos": 30, "elapsed_s": 0.14}
        m.stages[f"{it}/M1"] = {"status": "done", "n_scans": 1,
                                "points_in": 85420, "points_out": 18589,
                                "dense_source": "raw_looks_like_sfm",
                                "dual_source_duplicate": True,
                                "dense_novel_ratio": 0.0773,
                                "dense_merged": False, "elapsed_s": 3.05}
        m.stages[f"{it}/M2"] = {"status": "done", "n_scans": 1,
                                "registered": False, "identity": True,
                                "elapsed_s": 0.016}
        m.stages[f"{it}/M3"] = {"status": "done", "n_vertices": 60423,
                                "n_triangles": 120408, "watertight": False,
                                "n_components": 17,
                                "largest_component_ratio": 0.9934,
                                "elapsed_s": 2.49}
        m.stages[f"{it}/M4"] = {"status": "done", "texture_size": 2048,
                                "projected_cameras": 30,
                                "coverage_ratio": 0.5897,
                                "atlas_utilization": 0.6327,
                                "island_fill_ratio": 0.932,
                                "texture_carrier": "obj",
                                "elapsed_s": 80.88}
        m.stages[f"{it}/M5"] = dict(_m5(plausible=False), status="done",
                                    elapsed_s=0.62)
        m.stages[f"{it}/M6"] = {
            "status": "done", "formats_delivered": ["ply"],
            "formats_missing": [], "n_models": 1,
            "atlas": "relic_001_diffuse.png", "atlas_delivered": True,
            "texture_carrier": "obj", "texture_carrier_delivered": None,
            "texture_orphan": True, "elapsed_s": 0.014}
    return m


class TestHtmlHonesty:
    @pytest.fixture(scope="class")
    @classmethod
    def html(cls, tmp_path_factory):
        d = tmp_path_factory.mktemp("rep")
        out = os.path.join(str(d), "report.html")
        generate_html(_manifest(), None, out, None, "relic_001")
        with open(out, encoding="utf-8") as fh:
            return fh.read()

    def test_rmse_marked_with_dagger(self, html):
        """38.26 后面必须有 †，否则读者会把它当实测精度。"""
        assert "38.26 †" in html

    def test_scope_column_rendered(self, html):
        assert "中误差口径" in html
        assert "不判门（单位存疑）" in html

    def test_footnote_no_longer_claims_mm_gate(self, html):
        """旧脚注写「判门仍以毫米阈值为准」，与 pass 直接矛盾。"""
        assert "报告判门仍以毫米阈值为准" not in html

    def test_geometry_source_not_called_laser(self, html):
        assert "与 SfM 稠密同源" in html

    def test_units_downgraded(self, html):
        assert "米制（存疑" in html
        assert "米制（激光锚定）" not in html

    def test_carrier_shows_orphan(self, html):
        assert "无（图集孤立）" in html

    def test_findings_are_rendered_not_escaped(self, html):
        """autoescape=True 配 {{ f.text }} 曾把 <b> 印成 &lt;b&gt;。"""
        assert "&lt;b&gt;" not in html

    def test_findings_cover_self_proof(self, html):
        for key in ("双源几何近乎重合", "单位换算必然有误",
                    "贴图图集孤立", "未发生任何配准变换"):
            assert key in html, f"自动诊断漏了：{key}"


class TestFindingsOnLegacyManifest:
    """旧 manifest（无 identity / dual_source / units_plausible）也不能失声。"""

    def test_suspect_units_still_reported(self):
        m = _manifest()
        m.stages["relic_001/M5"].pop("units_plausible")
        fs = _findings(["relic_001"], lambda i, s: m.stages.get(f"{i}/{s}") or {})
        assert any("单位换算必然有误" in f["text"] for f in fs)

    def test_sparse_reference_still_reported(self):
        m = _manifest()
        m.stages["relic_001/M5"].update({"units_plausible": True})
        fs = _findings(["relic_001"], lambda i, s: m.stages.get(f"{i}/{s}") or {})
        assert any("不参与质量门" in f["text"] for f in fs)


# ------------------------------------------------------ ⑤ 上游标记透传 --
class TestRunnerFlags:
    def _task(self, tmp_path):
        from h3d.runner.task import Task
        root = tmp_path / "阶段成果"
        for d in ("00_raw/relic_001", "10_dense/relic_001"):
            (root / d).mkdir(parents=True)
        raw = root / "00_raw" / "relic_001" / "relic_001.ply"
        (_task_prep_ply())(str(raw))
        import shutil
        shutil.copy2(str(raw), str(root / "10_dense" / "relic_001"
                                   / "fused_dense.ply"))
        return Task(item_id="relic_001", root=str(root),
                    raw_paths=[str(raw)])

    def test_dual_source_and_identity_flags(self, tmp_path):
        """同一份几何既当 00_raw 又当 SfM 稠密 → 必须落自证标记。"""
        from h3d.core import load
        from h3d.runner import _stage_preprocess, _stage_register

        task = self._task(tmp_path)
        cfg = load("configs/example.yaml")
        m1 = _stage_preprocess(task, cfg)
        assert m1.metrics.get("dual_source_duplicate") is True
        assert m1.metrics["dense_source"] == "raw_looks_like_sfm"
        task.finish_stage("M1", m1.outputs, m1.metrics, 0.0)

        m2 = _stage_register(task, cfg)
        assert m2.metrics["identity"] is True
        assert m2.metrics["registered"] is False


def _task_prep_ply():
    """写一枚球面网格 PLY，供 M1 读取。"""
    import open3d as o3d

    def write(path: str) -> None:
        m = o3d.geometry.TriangleMesh.create_sphere(radius=1.0,
                                                    resolution=40)
        m.compute_vertex_normals()
        o3d.io.write_triangle_mesh(path, m)

    return write
