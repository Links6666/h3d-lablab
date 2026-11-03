"""`h3d verify` 命令行入口。

交付核验原来只在 `scripts/verify_delivery.py` 里，用户和 GUI 都够不着。
接进 CLI 之后，「交付包有没有坏」变成一条命令，而不是找人跑仓库脚本。

接缝：``h3d.cli.main(["verify", ...])`` 的返回码。
"""
import os
import sys

TESTS = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(TESTS, "..", "src"))
sys.path.insert(0, TESTS)

from _delivery_fixture import build_project, write  # noqa: E402

from h3d.cli import main  # noqa: E402


def test_verify_passes_on_complete_project(tmp_path):
    root = build_project(tmp_path, m6_done=["a"], delivered=["a"])

    assert main(["verify", root]) == 0


def test_verify_fails_when_an_item_was_not_delivered(tmp_path):
    root = build_project(tmp_path, m6_done=["a", "b"], delivered=["a"])

    assert main(["verify", root]) == 1


def test_verify_fails_when_manifest_is_missing(tmp_path):
    root = str(tmp_path)
    write(os.path.join(root, "80_export", "a", "a.ply"), b"model")

    assert main(["verify", root]) == 1


def test_verify_can_take_project_root_from_config(tmp_path):
    """不传目录时从 `-c` 推出项目根（raw_dir 的上一级，或上两级）。"""
    root = build_project(tmp_path, m6_done=["a"], delivered=["a"])
    cfg = os.path.join(root, "cfg.yaml")
    write(cfg, "project:\n"
               "  name: t\n"
               f"  raw_dir: {os.path.join(root, '00_raw')}\n")

    assert main(["verify", "-c", cfg]) == 0
