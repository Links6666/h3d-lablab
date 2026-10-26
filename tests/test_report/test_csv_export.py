"""tests.test_report.test_csv_export : CSV 导出单元测试。"""
from __future__ import annotations

import csv
import os
from datetime import datetime

import pytest

from h3d.core import RunManifest
from h3d.report.csv_export import (
    FAILURE_FIELDS,
    export_failures_csv,
    export_manifest_csv,
    write_csv,
)


# ---------------------------------------------------------------------------
# write_csv
# ---------------------------------------------------------------------------
class TestWriteCsv:
    def test_basic_rows(self, tmp_path):
        path = tmp_path / "out.csv"
        rows = [
            {"item_id": "rock001", "stage": "M1", "elapsed": 1.2},
            {"item_id": "rock002", "stage": "M1", "elapsed": 2.3},
        ]
        result = write_csv(rows, str(path))
        assert result == os.path.abspath(str(path))
        assert path.exists()
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            content = list(reader)
        assert content[0] == ["item_id", "stage", "elapsed"]
        assert content[1] == ["rock001", "M1", "1.2"]
        assert content[2] == ["rock002", "M1", "2.3"]

    def test_bom_for_excel(self, tmp_path):
        """utf-8-sig 编码确保 Excel 双击直接打开中文不乱码。"""
        path = tmp_path / "cn.csv"
        write_csv([{"k": "中文"}], str(path))
        with open(path, "rb") as fh:
            head = fh.read(3)
        assert head == b"\xef\xbb\xbf"  # UTF-8 BOM

    def test_fieldnames_uses_chinese_headers(self, tmp_path):
        path = tmp_path / "fail.csv"
        write_csv([{"item_id": "x", "stage": "M1", "error_type": "E100",
                    "message": "err", "time": "2026"}], str(path),
                  fieldnames=FAILURE_FIELDS)
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            content = csv.reader(fh)
            first = next(content)
        # 中文表头（2026-10-17：新增「耗时(s)」列，失败任务也要能看出卡了多久）
        assert first == ["文物编号", "阶段", "异常类型", "耗时(s)",
                         "异常信息", "时间"]

    def test_empty_rows_still_writes_header(self, tmp_path):
        path = tmp_path / "empty.csv"
        write_csv([], str(path), fieldnames=[("a", "甲"), ("b", "乙")])
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            first = next(csv.reader(fh))
        assert first == ["甲", "乙"]

    def test_missing_keys_become_empty(self, tmp_path):
        path = tmp_path / "miss.csv"
        write_csv([{"a": "1"}, {"a": "2", "b": "x"}], str(path),
                  fieldnames=[("a", "A"), ("b", "B")])
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            content = list(csv.reader(fh))
        assert content[0] == ["A", "B"]
        assert content[1] == ["1", ""]    # 缺 b -> 留空（《软著方案》2.6）
        assert content[2] == ["2", "x"]

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "sub" / "nested" / "out.csv"
        write_csv([{"k": 1}], str(path))
        assert path.exists()

    def test_unwritable_raises_export_error(self, tmp_path):
        # 路径指向已存在的「文件」而非目录，强行在其下写会失败
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        with pytest.raises(Exception) as ei:
            write_csv([{"k": 1}], str(blocker / "out.csv"))
        assert "E601" in str(ei.value) or "写出失败" in str(ei.value) or "目录" in str(ei.value)


# ---------------------------------------------------------------------------
# export_failures_csv
# ---------------------------------------------------------------------------
class TestExportFailuresCsv:
    def test_chinese_headers_and_order(self, tmp_path):
        path = tmp_path / "failures.csv"
        failures = [{
            "item_id": "rock001", "stage": "M1", "error_type": "InputDataError",
            "message": "空点云", "time": "2026-08-25T14:00:00",
        }]
        export_failures_csv(failures, str(path))
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            content = list(csv.reader(fh))
        assert content[0] == ["文物编号", "阶段", "异常类型", "耗时(s)",
                              "异常信息", "时间"]
        assert content[1] == ["rock001", "M1", "InputDataError", "",
                              "空点云", "2026-08-25T14:00:00"]

    def test_empty_failures(self, tmp_path):
        path = tmp_path / "failures.csv"
        export_failures_csv([], str(path))
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            content = list(csv.reader(fh))
        # 仅表头，无数据行
        assert len(content) == 1


# ---------------------------------------------------------------------------
# export_manifest_csv
# ---------------------------------------------------------------------------
class TestExportManifestCsv:
    def test_split_item_id_and_stage(self, tmp_path):
        m = RunManifest(config_sha256="abc")
        m.record("rock001/M1", "done", ["a.ply"], 1.2, points_out=500)
        m.record("rock001/M2", "done", ["b.ply"], 2.5, fitness=0.95)
        m.record_fail("rock002/M1", "InputDataError: 空点云")

        path = tmp_path / "summary.csv"
        export_manifest_csv(m, str(path))
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            content = list(csv.reader(fh))
        assert content[0][0] == "文物编号"
        assert content[0][1] == "阶段"

        rows_by_key = {(r[0], r[1]): r for r in content[1:]}
        assert ("rock001", "M1") in rows_by_key
        assert ("rock001", "M2") in rows_by_key
        assert ("rock002", "M1") in rows_by_key

        # 状态列：五态终态的中文口径（与 HTML 报表同一套词，
        # 两处不一致用户就得对照着猜「skipped 到底是关掉了还是没数据」）
        assert rows_by_key[("rock001", "M1")][2] == "完成"
        assert rows_by_key[("rock002", "M1")][2] == "失败"
        # 关键指标列包含 fitness（列序：编号/阶段/状态/耗时/产物数/产物路径/指标/备注）
        header = content[0]
        col = {name: i for i, name in enumerate(header)}
        assert "fitness=0.95" in rows_by_key[("rock001", "M2")][col["关键指标"]]
        # 产物数必须独立成列：「状态 done 但 0 个产物」要一眼可见（旧 M2 即此）
        assert rows_by_key[("rock001", "M2")][col["产物数"]] == "1"
        assert rows_by_key[("rock002", "M1")][col["产物数"]] == "0"
        # 失败原因必须落在「异常/备注」列，否则两表拼不出因果
        assert "空点云" in rows_by_key[("rock002", "M1")][col["异常/备注"]]

    def test_empty_manifest(self, tmp_path):
        m = RunManifest()
        path = tmp_path / "empty.csv"
        export_manifest_csv(m, str(path))
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            content = list(csv.reader(fh))
        assert len(content) == 1  # 仅表头
