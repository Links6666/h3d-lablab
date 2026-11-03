"""报表输出目录必须落在**项目根下**，不能跟着当前工作目录跑。

2026-11-03 实机：`report.output_dir` 默认 `"report"`，`h3d.report.generate`
直接 `os.makedirs("report")` —— 按 CWD 解析。从仓库根跑
`python -m h3d.cli batch -c D:/proj/cfg.yaml`，三个报表全写进了代码仓
（`E:/h3d_lablab/report/`），而 `generate` 的 docstring 写的是「相对项目根」。
"""
import os

import pytest


class _Cfg:
    def __init__(self, raw_dir, output_dir="report"):
        self.project = type("P", (), {"raw_dir": raw_dir})()
        self.report = type("R", (), {"output_dir": output_dir})()


class TestResolveReportDir:
    def test_relative_dir_is_pinned_under_project_root(self, tmp_path):
        from h3d.cli import resolve_report_dir

        raw = str(tmp_path / "00_raw")
        os.makedirs(raw, exist_ok=True)
        got = resolve_report_dir(_Cfg(raw))
        assert os.path.isabs(got)
        assert os.path.dirname(got) == str(tmp_path)
        assert os.path.basename(got) == "report"

    def test_absolute_dir_is_untouched(self, tmp_path):
        from h3d.cli import resolve_report_dir

        ab = str(tmp_path / "anywhere" / "rep")
        assert resolve_report_dir(_Cfg(str(tmp_path / "00_raw"), ab)) == ab

    def test_new_layout_uses_delivery_dir(self, tmp_path):
        """新布局（raw_dir 在 <proj>/阶段成果/00_raw）→ <proj>/交付成果/report。

        走 `h3d.core.paths.report_dir`，不自己拼——布局只有一个出口。
        """
        from h3d.cli import resolve_report_dir

        raw = str(tmp_path / "阶段成果" / "00_raw")
        os.makedirs(raw, exist_ok=True)
        got = resolve_report_dir(_Cfg(raw))
        assert got == str(tmp_path / "交付成果" / "report")

    def test_missing_raw_dir_degrades_to_original(self):
        from h3d.cli import resolve_report_dir

        assert resolve_report_dir(_Cfg("", "out")) == "out"

    def test_trailing_separator_is_tolerated(self, tmp_path):
        from h3d.cli import resolve_report_dir

        raw = str(tmp_path / "00_raw") + os.sep
        os.makedirs(str(tmp_path / "00_raw"), exist_ok=True)
        assert resolve_report_dir(_Cfg(raw)) == str(tmp_path / "report")
