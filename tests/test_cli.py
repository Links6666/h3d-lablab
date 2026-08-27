"""tests.test_cli : CLI 命令入口冒烟测试。"""
from __future__ import annotations

import json
import os

import pytest

from h3d import __version__


def test_version_flag(capsys):
    from h3d.cli import main
    with pytest.raises(SystemExit) as ei:
        main(["--version"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out


def test_init_creates_dirs(tmp_path):
    from h3d.cli import main
    rc = main(["init", "-p", str(tmp_path / "demo")])
    assert rc == 0
    # 关键目录都应存在
    assert (tmp_path / "demo" / "00_raw").is_dir()
    assert (tmp_path / "demo" / "90_meta").is_dir()
    assert (tmp_path / "demo" / "output").is_dir()


def test_validate_missing_config(tmp_path):
    from h3d.cli import main
    rc = main(["validate", "-c", str(tmp_path / "nope.yaml")])
    # 配置缺失时 ConfigError -> 退出码 1
    assert rc == 1


def test_validate_runs(tmp_path, capsys):
    """validate 读取 example.yaml 并扫描数据（空 raw_dir 时 0 尊）。"""
    from h3d.cli import main
    import shutil

    # 准备一份临时配置 + 空 raw_dir
    cfg_path = tmp_path / "cfg.yaml"
    raw_dir = tmp_path / "00_raw"
    raw_dir.mkdir()
    cfg_path.write_text(
        f"project:\n  name: t\n  raw_dir: '{raw_dir.as_posix()}'\n"
        f"  formats: [ply]\nrunner:\n  manifest: '{tmp_path/'m.json'}'\n",
        encoding="utf-8")

    rc = main(["validate", "-c", str(cfg_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "配置合法" in out
    assert "0 尊" in out


def test_batch_dry_run(tmp_path, capsys):
    """--dry-run 仅预览，不写 manifest。"""
    from h3d.cli import main
    raw = tmp_path / "00_raw" / "rock001"
    raw.mkdir(parents=True)
    (raw / "a.ply").write_bytes(b"ply")
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        f"project:\n  raw_dir: '{tmp_path/'00_raw'}'\n  formats: [ply]\n"
        f"runner:\n  manifest: '{tmp_path/'m.json'}'\n"
        f"report:\n  enabled: false\n",
        encoding="utf-8")

    rc = main(["batch", "-c", str(cfg_path), "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out
    assert "rock001" in out
    # manifest 未写
    assert not (tmp_path / "m.json").exists()


def test_help():
    from h3d.cli import _build_parser
    parser = _build_parser()
    # --help 触发 argparse SystemExit(0)，这是预期行为
    with pytest.raises(SystemExit) as ei:
        parser.parse_args(["--help"])
    assert ei.value.code == 0
