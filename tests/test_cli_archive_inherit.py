"""CLI ``h3d archive inherit``（B1 切片 6）。

只测命令行这一层：参数解析、退出码、错误信息是否可读。
判定逻辑（有没有图集、配准是否可信）在 :mod:`h3d.archive.inherit_epoch` 里
已被单测覆盖，这里不重复造一套判据 —— 两处判据必然漂移，漂移的后果是
「命令行说成功、GUI 说失败」。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

ITEM = "r1"


def _project(tmp_path, name, with_asset=False):
    from h3d.archive import Archive, CanonicalAsset
    from h3d.archive.workspace import ensure_archive

    root = tmp_path / name
    raw = root / "阶段成果" / "00_raw" / ITEM
    raw.mkdir(parents=True)
    (raw / "scan.ply").write_bytes(b"PLY\n")
    ensure_archive(str(root))
    if with_asset:
        # 有规范产物、但没有图集记录 —— 旧版 M4（不落 UV 布局）就是这种状态
        Archive.open(str(root)).register(CanonicalAsset(
            epoch_id="E1", version=1, content_key="a" * 64,
            mesh_path=f"阶段成果/30_dense/{ITEM}/{ITEM}.ply",
            uv_atlas_ref="atlas-v1"))
    return root


class TestInheritCommand:
    def test_unknown_base_returns_1(self, tmp_path, capsys):
        from h3d.cli import main

        base = tmp_path / "nope"           # 不存在
        src = _project(tmp_path, "src")
        rc = main(["archive", "inherit", "--base", str(base),
                   "--from", str(src)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "档案" in err or "失败" in err

    def test_base_without_atlas_returns_1(self, tmp_path, capsys):
        """基准期没有图集记录 → rc=1 且提示图集，不能静默成功。"""
        from h3d.cli import main

        base = _project(tmp_path, "base", with_asset=True)
        src = _project(tmp_path, "src")
        rc = main(["archive", "inherit", "--base", str(base),
                   "--from", str(src)])
        assert rc == 1
        assert "图集" in capsys.readouterr().err

    def test_bad_matrix_file_returns_1(self, tmp_path, capsys):
        from h3d.cli import main

        base = _project(tmp_path, "base")
        src = _project(tmp_path, "src")
        bad = tmp_path / "m.json"
        bad.write_text("{not json", encoding="utf-8")
        rc = main(["archive", "inherit", "--base", str(base),
                   "--from", str(src), "--matrix", str(bad)])
        assert rc == 1
        assert "变换矩阵" in capsys.readouterr().err

    def test_json_output(self, tmp_path, capsys, monkeypatch):
        """--json 输出字段齐全（报表/脚本要消费它）。"""
        from h3d.archive import inherit_epoch as mod

        def fake(base, source, **kw):
            return mod.InheritResult(
                ok=True, state="done", message="已继承：r1@atlas-v1 → r1 v2",
                texture_path=str(tmp_path / "t.png"), atlas_version="atlas-v1",
                atlas_ref="r1@atlas-v1", base_epoch="E1", source_epoch="E1",
                metrics={"in_frame_fraction": 0.91, "inherited": True})

        monkeypatch.setattr(mod, "inherit_epoch", fake)
        from h3d.cli import main

        rc = main(["archive", "inherit", "--base", str(tmp_path / "b"),
                   "--from", str(tmp_path / "s"), "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["atlas_ref"] == "r1@atlas-v1"
        assert data["metrics"]["in_frame_fraction"] == 0.91

    def test_same_project_is_refused(self, tmp_path, capsys):
        """同一目录当基准与复测 → 明确拒绝（继承需要两个独立项目）。"""
        from h3d.cli import main

        p = _project(tmp_path, "same")
        rc = main(["archive", "inherit", "--base", str(p), "--from", str(p)])
        assert rc == 1
        assert "同一个目录" in capsys.readouterr().err
