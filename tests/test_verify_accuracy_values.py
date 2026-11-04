"""accuracy.csv 的**数值**要与 manifest 对得上。

现在核验只查结构（列数一致、有数据行），数值对不上照样 PASS。而这张表是论文
直接引用的那张——手改一个数、或者哪次重算后 CSV 没重新生成，都会静默过去。

接缝：``h3d.delivery_verify.main(项目根)``。
"""
import json
import os
import sys

TESTS = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(TESTS, "..", "src"))
sys.path.insert(0, TESTS)

from _delivery_fixture import add_accuracy_numbers, build_project  # noqa: E402

import h3d.delivery_verify as vd  # noqa: E402

def _project_with_values(tmp_path, items, **kw):
    """夹具只有一份：`_delivery_fixture.add_accuracy_numbers`。

    这份测试原来自己写 CSV（列名还是缩略版），结果 `纹理清晰度` 这些列根本
    不存在——测试绿着，但它测的是另一张表。夹具写两份必然漂移。
    """
    root = build_project(tmp_path, m6_done=items, delivered=items)
    return add_accuracy_numbers(root, items, **kw)


def _csv_path(root, item):
    return os.path.join(root, "80_export", item, "accuracy.csv")


def _rewrite_cell(path, old, new):
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    assert old in text, f"{old} 不在 CSV 里：{text}"
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(text.replace(old, new, 1))


def _run(root):
    code = vd.main(root)
    return code, "\n".join(vd.FAILS)


class TestAccuracyNumbersMatchManifest:
    def test_coverage_mismatch_is_a_failure(self, tmp_path):
        """CSV 的覆盖率被改成 99.0%，manifest 说 61.5% → 必须 FAIL。"""
        root = _project_with_values(tmp_path, ["a"])
        _rewrite_cell(_csv_path(root, "a"), "61.5%", "99.0%")

        code, fails = _run(root)

        assert code == 1, f"数值对不上竟然通过：{fails}"
        assert "覆盖率" in fails, fails

    def test_matching_numbers_pass(self, tmp_path):
        """数值一致 → 不该报 FAIL，且要有一条 PASS 说明核过几列。"""
        root = _project_with_values(tmp_path, ["a"])

        code, fails = _run(root)

        assert code == 0, fails
        assert any("数值" in m for m in vd.OKS), vd.OKS

    def test_texture_coverage_mismatch_is_a_failure(self, tmp_path):
        """不只是覆盖率——纹理覆盖率（来自 M4）也要核。"""
        root = _project_with_values(tmp_path, ["a"])
        _rewrite_cell(_csv_path(root, "a"), "0.5865", "0.7777")

        code, fails = _run(root)

        assert code == 1, f"纹理覆盖率对不上竟然通过：{fails}"
        assert "纹理覆盖率" in fails, fails

    def test_non_numeric_cells_are_not_failures(self, tmp_path):
        """`未筛查` / `N/A` 是合法状态，不是「对不上」。

        把「没数」当「数错了」会逼着人去凑数——而那正是伪造数据的起点。
        """
        root = _project_with_values(tmp_path, ["a"], anomaly=None)

        code, fails = _run(root)

        assert code == 0, fails
        assert "异常面积" not in fails, fails

    def test_second_item_mismatch_names_the_item(self, tmp_path):
        """两件里第二件的数错了 → FAIL 必须点出是哪件。"""
        root = _project_with_values(tmp_path, ["ping_a", "ping_b"])
        _rewrite_cell(_csv_path(root, "ping_b"), "61.5%", "12.3%")

        code, fails = _run(root)

        assert code == 1, fails
        assert "ping_b" in fails, fails
        assert "ping_a" not in fails, fails

    def test_texture_sharpness_mismatch_is_a_failure(self, tmp_path):
        """纹理清晰度也要核——它直接来自 M5.texture_sharpness。"""
        root = _project_with_values(tmp_path, ["a"])
        _rewrite_cell(_csv_path(root, "a"), "79.9149", "12.3456")

        code, fails = _run(root)

        assert code == 1, f"纹理清晰度对不上竟然通过：{fails}"
        assert "纹理清晰度" in fails, fails

    def test_texture_seam_mismatch_is_a_failure(self, tmp_path):
        root = _project_with_values(tmp_path, ["a"])
        _rewrite_cell(_csv_path(root, "a"), "1.9684", "9.8765")

        code, fails = _run(root)

        assert code == 1, fails
        assert "纹理接缝" in fails, fails
