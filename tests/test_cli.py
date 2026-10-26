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
    # 新布局（v1.1.10）：一个综合文件夹 + 两棵子树
    stage = tmp_path / "demo" / "阶段成果"
    assert (stage / "00_raw").is_dir()
    assert (stage / "90_meta").is_dir()
    assert (stage / "output").is_dir()
    assert (tmp_path / "demo" / "交付成果" / "report").is_dir()
    assert (tmp_path / "demo" / "输出说明.txt").is_file()
    # 旧布局的平级目录不得再出现（否则用户仍要面对一屏散目录）
    assert not (tmp_path / "demo" / "00_raw").exists()


def test_validate_missing_config(tmp_path):
    from h3d.cli import main
    rc = main(["validate", "-c", str(tmp_path / "nope.yaml")])
    # 配置缺失时 ConfigError -> 退出码 1
    assert rc == 1


def test_validate_runs(tmp_path, capsys):
    """validate 扫描数据；**0 件 = 校验失败**（rc=1），有件才 rc=0。

    2026-10-23 巡检修正：原先 0 件也返回 0，拿 validate 当 CI 门禁时，
    目录写错 / 扩展名不符 / 只剩系统垃圾目录都会蒙混过关。
    """
    from h3d.cli import main

    cfg_path = tmp_path / "cfg.yaml"
    raw_dir = tmp_path / "00_raw"
    raw_dir.mkdir()
    cfg_path.write_text(
        f"project:\n  name: t\n  raw_dir: '{raw_dir.as_posix()}'\n"
        f"  formats: [ply]\nrunner:\n  manifest: '{tmp_path/'m.json'}'\n",
        encoding="utf-8")

    assert main(["validate", "-c", str(cfg_path)]) == 1
    out = capsys.readouterr()
    assert "配置合法" in out.out
    assert "0 件" in out.out
    assert "未发现任何文物" in out.err

    # 放一件进去 → 通过
    (raw_dir / "relic").mkdir()
    (raw_dir / "relic" / "a.ply").write_bytes(b"ply\nformat ascii 1.0\nend_header\n")
    assert main(["validate", "-c", str(cfg_path)]) == 0
    assert "1 件" in capsys.readouterr().out


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
