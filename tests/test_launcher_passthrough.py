"""打包版必须叫得动**每一个** CLI 子命令。

`h3d.gui.launcher._CLI_PASSTHROUGH` 是又一份手写清单（本项目"多份副本"的第 6 种）：
它决定 `H3D.exe <第一个参数>` 是交给 CLI 还是起 GUI。2026-11-04 实测已漂：

    白名单有  init validate batch report doctor
    实际还有  verify  archive  anomaly  anomaly-track

后果：打包版 `H3D.exe verify D:/proj` 不起 CLI，而是**当成无参启动 GUI**——
用户以为什么都没发生。`verify` 恰恰是这一版新加的命令。

结构性修法：从 CLI 的 parser 直接派生，不再手写。
"""
import pytest

pytest.importorskip("PySide6")


def _subcommands():
    from h3d.cli import _build_parser

    parser = _build_parser()
    for act in parser._actions:
        if act.__class__.__name__ in ("_SubParsersAction", "SubParsersAction"):
            return set(act.choices)
    raise AssertionError("parser 里没有 subparsers，测试自己写错了")


class TestPackagedCliPassthrough:
    def test_every_subcommand_is_passthrough(self):
        from h3d.gui.launcher import cli_passthrough

        missing = _subcommands() - cli_passthrough()
        assert not missing, (
            "这些子命令在打包版里叫不动（会被当成无参启动 GUI）："
            + "、".join(sorted(missing)))

    def test_help_and_version_still_pass(self):
        from h3d.gui.launcher import cli_passthrough

        for flag in ("-h", "--help", "--version"):
            assert flag in cli_passthrough(), flag

    def test_verify_specifically(self):
        """新加的那个命令单独点名——它是这次漂移的起因。"""
        from h3d.gui.launcher import cli_passthrough

        assert "verify" in cli_passthrough()
