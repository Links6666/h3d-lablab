"""2026-10-23 端到端巡检（bug-bash）确认的缺陷 —— 复现测试。

每个用例对应一条实机可复现的缺陷；修复前红、修复后绿，之后作为回归锁。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from h3d.core import ConfigError, RunManifest, load
from h3d.core.metadata import RunManifest as _RM  # noqa: F401 - 明示来源
from h3d.runner.batch_processor import discover_items


# ---------------------------------------------------------------------------
# BUG-1 打包版只认 --batch/--report：--version / doctor / validate 全拉起 GUI
# ---------------------------------------------------------------------------
def test_launcher_dispatches_cli_subcommands(monkeypatch):
    """exe 的 CLI 契约：脚本能用的子命令必须走 CLI，不能开窗口。

    ``H3D.exe --version`` 曾经直接进入 GUI 模式（自动化里表现为永久挂起），
    ``doctor`` / ``validate`` 同理。
    """
    from h3d.gui import launcher

    calls: list[list[str]] = []

    def fake_cli(argv):
        calls.append(list(argv))
        return 0

    def boom():  # 走到 GUI 分支即失败
        raise AssertionError("不该进入 GUI")

    import h3d.cli
    monkeypatch.setattr(h3d.cli, "main", fake_cli)
    import h3d.gui.app
    monkeypatch.setattr(h3d.gui.app, "run_gui", boom)

    launcher.main(["--version"])
    launcher.main(["doctor", "--json"])
    launcher.main(["validate", "-c", "x.yaml"])

    assert calls == [["--version"], ["doctor", "--json"], ["validate", "-c", "x.yaml"]]


def test_launcher_still_opens_gui_without_args(monkeypatch):
    """无参数仍必须是 GUI（不能因为上面放宽了分发规则就吞掉正常启动）。"""
    from h3d.gui import launcher
    from h3d.core import get_logger  # noqa: F401

    hit = {"gui": False}

    def fake_gui():
        hit["gui"] = True
        return 0

    import h3d.gui.app
    monkeypatch.setattr(h3d.gui.app, "run_gui", fake_gui)
    # 启动自检是 GUI 分支的前置，测试里不需要真跑
    import h3d.health
    monkeypatch.setattr(h3d.health, "run_preflight", lambda *a, **k: None)

    assert launcher.main([]) == 0
    assert hit["gui"] is True


# ---------------------------------------------------------------------------
# BUG-2 manifest 非原子写 + 零容错：一次硬杀就让项目永久跑不起来
# ---------------------------------------------------------------------------
def test_manifest_save_is_atomic(tmp_path, monkeypatch):
    """写 manifest 中途失败，原文件必须完好（tmp+replace，不是就地覆盖）。"""
    path = tmp_path / "run_manifest.json"
    m = RunManifest(run_id="r1")
    m.record("a/M1", outputs=["x.ply"])
    m.save(str(path))
    before = path.read_text(encoding="utf-8")

    import h3d.core.metadata as meta

    def boom(*a, **k):
        raise OSError("No space left on device")

    monkeypatch.setattr(meta.json, "dump", boom)
    with pytest.raises(OSError):
        m.save(str(path))
    monkeypatch.undo()

    assert path.read_text(encoding="utf-8") == before
    assert json.loads(before)["stages"]["a/M1"]["status"] == "done"


def test_manifest_load_survives_truncated_file(tmp_path, caplog):
    """被 kill 时写了一半的 manifest 不能让项目再也跑不起来。"""
    path = tmp_path / "run_manifest.json"
    path.write_text('{"run_id": "r1", "stages": {"a/M1": {"status": "do',
                   encoding="utf-8")

    m = RunManifest.load_or_create(str(path))

    assert m.stages == {} or "a/M1" in m.stages
    assert m.is_done("a/M1") is False
    # 原始坏文件必须留证（用户可据此判断丢了什么），不能静默删除
    assert list(tmp_path.glob("*corrupt*")) or list(tmp_path.glob("*.bak"))


def test_manifest_load_tolerates_bad_stages_type(tmp_path):
    """``stages`` 被写成 list 时 load 不能"成功"，否则后面 .get 才崩。"""
    path = tmp_path / "run_manifest.json"
    path.write_text('{"run_id":"r1","stages":[]}', encoding="utf-8")

    m = RunManifest.load(str(path))

    assert isinstance(m.stages, dict)
    assert m.is_done("a/M1") is False


# ---------------------------------------------------------------------------
# BUG-3 系统目录被当成文物
# ---------------------------------------------------------------------------
def test_discovery_skips_system_dirs(tmp_path):
    raw = tmp_path / "00_raw"
    (raw / "relic").mkdir(parents=True)
    (raw / "relic" / "a.ply").write_bytes(b"ply\n")
    for junk in (".git", "__MACOSX", "$RECYCLE.BIN", "System Volume Information"):
        (raw / junk).mkdir()
        (raw / junk / "b.ply").write_bytes(b"ply\n")

    tasks = discover_items(str(raw), ["ply"])
    assert [t.item_id for t in tasks] == ["relic"]


# ---------------------------------------------------------------------------
# BUG-4 记事本存成 ANSI/GBK 的配置：给 Python 栈而不是人话
# ---------------------------------------------------------------------------
def test_gbk_config_raises_config_error(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes("# 文物三维建模配置\nproject:\n  name: 测试\n".encode("gbk"))

    with pytest.raises(ConfigError) as ei:
        load(str(p))
    msg = str(ei.value)
    assert "UTF-8" in msg.upper() or "编码" in msg


def test_bom_config_is_accepted(tmp_path):
    """Windows 记事本默认存 UTF-8 BOM —— 必须能读，不能报错。"""
    p = tmp_path / "config.yaml"
    p.write_bytes("\ufeffproject:\n  name: bom_ok\n".encode("utf-8"))
    assert load(str(p)).project.name == "bom_ok"


# ---------------------------------------------------------------------------
# BUG-5 validate 在 0 件文物时 rc=0（脚本/CI 会当成通过）
# ---------------------------------------------------------------------------
def test_validate_fails_when_no_items(tmp_path, monkeypatch):
    from h3d import cli

    cfg = tmp_path / "config.yaml"
    (tmp_path / "00_raw").mkdir()
    cfg.write_text(f"project:\n  name: empty\n  raw_dir: {tmp_path / '00_raw'}\n",
                   encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["h3d", "validate", "-c", str(cfg)])
    assert cli.main(["validate", "-c", str(cfg)]) == 1
