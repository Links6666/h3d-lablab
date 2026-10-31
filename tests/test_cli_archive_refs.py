"""CLI ``h3d archive refs``（B1 切片 8）。

只测命令行这一层：参数解析、退出码、输出是否可读。判据本身在
:mod:`h3d.archive.audit` 里已被单测覆盖，这里不重复造一套 —— 两处判据必然
漂移，漂移的后果是「命令行说没问题、档案里还挂着假引用」。

退出码口径：**有 dangling 且没修 → 1**。体检表是要被脚本调用、被写进交付
流程的，必须能当门用；全绿或修完才 0。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

ITEM = "r1"


def _project(tmp_path, name="proj", *, ref="atlas-v1"):
    """建一个已建档、有一条规范产物的项目（产物引用了不存在的图集）。"""
    from h3d.archive import Archive, CanonicalAsset
    from h3d.archive.workspace import ensure_archive

    root = tmp_path / name
    raw = root / "阶段成果" / "00_raw" / ITEM
    raw.mkdir(parents=True)
    (raw / "scan.ply").write_bytes(b"PLY\n")
    ensure_archive(str(root))
    Archive.open(str(root)).register(CanonicalAsset(
        epoch_id="E1", version=1, content_key="a" * 64,
        mesh_path=f"阶段成果/30_dense/{ITEM}/{ITEM}.ply",
        uv_atlas_ref=ref, created_at="2026-10-31T14:18:27"))
    return root


def _asset_json(root):
    path = os.path.join(str(root), "archive", "assets", "E1__v1.json")
    return json.load(open(path, encoding="utf-8")), path


class TestRefsCommand:
    def test_unarchived_project_returns_1(self, tmp_path, capsys):
        from h3d.cli import main

        rc = main(["archive", "refs", "-p", str(tmp_path / "nope")])

        assert rc == 1
        assert "档案" in capsys.readouterr().err

    def test_dangling_ref_makes_it_fail(self, tmp_path, capsys):
        """有假引用 → rc=1，且表里能看见是哪一条、为什么。"""
        from h3d.cli import main

        root = _project(tmp_path)
        rc = main(["archive", "refs", "-p", str(root)])

        out = capsys.readouterr().out
        assert rc == 1
        assert "atlas-v1" in out
        assert "dangling" in out or "查无实体" in out

    def test_json_report_is_machine_readable(self, tmp_path, capsys):
        from h3d.cli import main

        root = _project(tmp_path)
        rc = main(["archive", "refs", "-p", str(root), "--json"])

        payload = json.loads(capsys.readouterr().out)
        assert rc == 1
        assert payload["findings"][0]["state"] == "dangling"
        assert payload["findings"][0]["ref"] == "atlas-v1"
        assert payload["repaired"] == []

    def test_fix_clears_the_ref_and_then_passes(self, tmp_path, capsys):
        """``--fix`` 之后 rc=0，再跑一次也 rc=0（幂等，不会反复改）。"""
        from h3d.cli import main

        root = _project(tmp_path)
        rc = main(["archive", "refs", "-p", str(root), "--fix"])
        assert rc == 0
        data, path = _asset_json(root)
        assert data["uv_atlas_ref"] == ""
        assert data["params"]["ref_repair"]["dropped"] == "atlas-v1"
        capsys.readouterr()

        assert main(["archive", "refs", "-p", str(root)]) == 0

    def test_dry_run_writes_nothing(self, tmp_path, capsys):
        """``--dry-run`` 列出「打算改谁」但不落盘 —— 修复不可逆，先看清单。"""
        from h3d.cli import main

        root = _project(tmp_path)
        before = open(_asset_json(root)[1], "rb").read()

        rc = main(["archive", "refs", "-p", str(root), "--dry-run"])

        assert rc == 1
        assert "atlas-v1" in capsys.readouterr().out
        assert open(_asset_json(root)[1], "rb").read() == before
