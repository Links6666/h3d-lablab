"""`verify_report(root)` —— 核验结果的结构化出口（GUI 用）。

GUI 不能靠读 stdout：打包版是 windowed 应用，`stderr=None`，打印出去没人看得见。
所以核验要能给一个**结构**：几项通过、几项警告、几条失败、以及可展示的文本。

接缝：``h3d.delivery_verify.verify_report(项目根)``。
"""
import os
import sys

TESTS = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(TESTS, "..", "src"))
sys.path.insert(0, TESTS)

from _delivery_fixture import build_project  # noqa: E402

import h3d.delivery_verify as vd  # noqa: E402


def test_report_on_good_project(tmp_path):
    root = build_project(tmp_path, m6_done=["a"], delivered=["a"])

    rep = vd.verify_report(root)

    assert rep["ok"] is True
    assert rep["n_fail"] == 0
    assert rep["n_pass"] > 0
    assert rep["n_pass"] == len(rep["pass"])
    assert rep["n_warn"] == len(rep["warn"])


def test_report_on_broken_project_carries_the_reason(tmp_path):
    root = build_project(tmp_path, m6_done=["a", "b"], delivered=["a"])

    rep = vd.verify_report(root)

    assert rep["ok"] is False
    assert rep["n_fail"] == 1
    assert any("b" in f for f in rep["fail"])


def test_report_text_is_showable(tmp_path):
    """`text` 要能直接塞进对话框：三段分明、带合计行。"""
    root = build_project(tmp_path, m6_done=["a"], delivered=["a"])

    rep = vd.verify_report(root)
    text = rep["text"]

    assert "合计" in text
    assert "PASS" in text and "FAIL" in text
    assert "\n" in text


def test_report_does_not_write_to_stdout(tmp_path, capsys):
    """GUI 里打印出去没人看得见，还会污染日志——结构化出口应当静默。"""
    root = build_project(tmp_path, m6_done=["a"], delivered=["a"])

    vd.verify_report(root)

    captured = capsys.readouterr()
    assert captured.out == "", captured.out
