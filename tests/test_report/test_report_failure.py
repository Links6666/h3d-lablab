"""tests.test_report.test_report_failure : 失败报表可读性回归。

实机背景（2026-10-17）
----------------------
S1 失败后用户打开 report.html，看到的是：
- "各件处理状态"表格**完全空白**（渲染器只遍历 M1~M6，不含 S1）；
- 章节编号出现两个"五、"；
- 失败信息被挤成一行，COLMAP 输出的换行全部丢失；
- 耗时列对失败任务恒为 0.00。

本测试锁四件事：
1. S1 失败必须出现在状态表里，并带一句可读原因；
2. 章节编号唯一且连续；
3. 失败信息保留换行（white-space: pre-wrap）；
4. 失败阶段记录真实耗时，CSV 同步增加耗时列。
"""
from __future__ import annotations

import os

import pytest

from h3d.core import RunManifest
from h3d.report.html_export import _failure_hints, generate_html


def _manifest_with_s1_fail(item: str = "relic_x") -> RunManifest:
    m = RunManifest(run_id="run-test", config_sha256="deadbeef" * 8)
    m.record_fail(f"{item}/S1",
                  "COLMAP 步骤 1 失败 (exit=3221226505): SQLite error: "
                  "constraint failed", elapsed_s=42.5)
    return m


class TestStageTableIncludesS1:
    def test_s1_failure_row_rendered(self, tmp_path):
        out = str(tmp_path / "r.html")
        generate_html(_manifest_with_s1_fail(), failures=[{
            "item_id": "relic_x", "stage": "S1", "error_type": "SfmError",
            "message": "COLMAP 步骤 1 失败", "elapsed_s": 42.5,
        }], out_path=out)

        html = open(out, encoding="utf-8").read()
        # 状态表必须出现 S1 行（原实现完全没有）
        assert "<td>relic_x</td><td>S1</td>" in html
        assert "constraint failed" in html
        # 失败计数进入摘要
        assert "失败 1" in html

    def test_elapsed_is_real_not_zero(self, tmp_path):
        out = str(tmp_path / "r.html")
        generate_html(_manifest_with_s1_fail(), failures=[{
            "item_id": "relic_x", "stage": "S1", "error_type": "SfmError",
            "message": "x", "elapsed_s": 42.5,
        }], out_path=out)
        html = open(out, encoding="utf-8").read()
        assert "42.50" in html, "失败阶段应显示真实耗时"


class TestSectionNumbering:
    def test_no_duplicate_section_numbers(self, tmp_path):
        out = str(tmp_path / "r.html")
        generate_html(_manifest_with_s1_fail(), failures=[], out_path=out)
        html = open(out, encoding="utf-8").read()
        for num in ("一、", "二、", "三、", "四、", "五、", "六、"):
            assert html.count(num) == 1, f"章节 {num} 重复或缺失"
        assert "五、阶段耗时图" not in html


class TestFailureRendering:
    def test_multiline_message_preserved(self, tmp_path):
        out = str(tmp_path / "r.html")
        msg = ("COLMAP 步骤 1 失败\n"
               "SQLite error: constraint failed\n"
               "诊断：数据库约束冲突")
        generate_html(_manifest_with_s1_fail(), failures=[{
            "item_id": "relic_x", "stage": "S1", "error_type": "SfmError",
            "message": msg, "elapsed_s": 1.0,
        }], out_path=out)
        html = open(out, encoding="utf-8").read()
        # 换行必须保留，且用 pre-wrap 渲染
        assert "COLMAP 步骤 1 失败\nSQLite error" in html
        assert "pre-wrap" in html

    def test_diagnosis_hint_for_sqlite_conflict(self):
        hints = _failure_hints([{"message": "SQLite error: constraint failed"}])
        assert any("数据库约束冲突" in h for h in hints)

    def test_diagnosis_hint_for_cuda(self):
        hints = _failure_hints([{"message": "dense 需要可用 CUDA GPU"}])
        assert any("CUDA" in h for h in hints)

    def test_generic_fallback_points_to_logs(self):
        hints = _failure_hints([{"message": "something odd"}])
        assert any("colmap_stepN.log" in h for h in hints)

    def test_no_hints_without_failures(self):
        assert _failure_hints([]) == []


class TestFailureCsvColumns:
    def test_elapsed_column_present(self, tmp_path):
        from h3d.report.csv_export import export_failures_csv
        out = str(tmp_path / "failures.csv")
        export_failures_csv([{
            "item_id": "a", "stage": "S1", "error_type": "SfmError",
            "message": "boom", "elapsed_s": 12.3, "time": "t",
        }], out)
        text = open(out, encoding="utf-8-sig").read()
        assert "耗时(s)" in text
        assert "12.3" in text


class TestManifestRecordFailElapsed:
    def test_record_fail_keeps_elapsed(self):
        m = RunManifest(run_id="r")
        m.record_fail("a/S1", "boom", elapsed_s=7.25)
        assert m.stages["a/S1"]["elapsed_s"] == 7.25
        assert m.stages["a/S1"]["status"] == "fail"

    def test_record_fail_without_elapsed_is_backward_compatible(self):
        m = RunManifest(run_id="r")
        m.record_fail("a/S1", "boom")
        assert "elapsed_s" not in m.stages["a/S1"]
