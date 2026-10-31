"""档案层存量迁移测试（B0 切片 4）。

把「已经跑完的老项目」认成一个 Artifact + 一个 Epoch + 一个 CanonicalAsset。
迁移必须**只读现有产物**、**幂等**、**不碰不含档案痕迹的普通目录**。
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _digest(path: str) -> str:
    from h3d.archive import file_digest

    return file_digest(path)


def _fake_project(tmp_path, with_raw: bool = True, item: str = "relic_001"):
    """造一个符合现有布局（阶段成果/…）的最小项目。"""
    stage = tmp_path / "阶段成果"
    raw = stage / "00_raw" / item
    raw.mkdir(parents=True)
    if with_raw:
        (raw / "scan.ply").write_bytes(b"PLY-raw\n")
    dense = stage / "30_dense" / item
    dense.mkdir(parents=True)
    (dense / f"{item}.ply").write_bytes(b"PLY-dense\n")
    tex = stage / "50_tex" / item
    tex.mkdir(parents=True)
    (tex / f"{item}_L1.0_diffuse.png").write_bytes(b"PNG\n")
    meta = stage / "90_meta"
    meta.mkdir()
    (meta / f"{item}_capture_meta.json").write_text("{}", encoding="utf-8")
    return tmp_path


def test_migrate_creates_artifact_epoch_and_asset(tmp_path):
    from h3d.archive import Archive
    from h3d.archive.migrate import migrate

    root = _fake_project(tmp_path)
    res = migrate(str(root))

    assert res["status"] == "created"
    arch = Archive.open(str(root))
    assert arch.artifact().artifact_id == "relic_001"
    assert [e.epoch_id for e in arch.epochs()] == ["E1"]
    assert arch.epochs()[0].route == "A"
    assert arch.epochs()[0].is_baseline is True
    asset = arch.latest("E1")
    assert asset is not None and asset.version == 1
    assert asset.mesh_path.replace("\\", "/").endswith("30_dense/relic_001/relic_001.ply")
    # 迁移**不能**编造图集引用：存量项目没有图集记录，写 ``atlas-v1`` 等于让资产
    # 声称自己挂在一个从不存在的图集上（真机 2026-10-31：两个存量项目都这样，
    # inherit 报「没有 atlas-v1 的记录」时没人知道其实根本没登记过）。
    assert asset.uv_atlas_ref == ""
    assert not os.path.isdir(os.path.join(str(root), "archive", "atlas"))


def test_migrate_marks_route_b_without_raw(tmp_path):
    """00_raw 里没有几何 → 纯照片路线 B。这条判据与 runner 的 raw_paths 一致。"""
    from h3d.archive import Archive
    from h3d.archive.migrate import migrate

    root = _fake_project(tmp_path, with_raw=False)
    migrate(str(root))
    assert Archive.open(str(root)).epochs()[0].route == "B"


def test_migrate_is_idempotent(tmp_path):
    from h3d.archive import Archive
    from h3d.archive.migrate import migrate

    root = _fake_project(tmp_path)
    migrate(str(root))
    res = migrate(str(root))
    assert res["status"] == "already"
    assert len(Archive.open(str(root)).epochs()) == 1


def test_migrate_refuses_plain_directory(tmp_path):
    """普通目录（无 config.yaml / 无阶段专属目录）不得建档。

    `output` 被整棵搬走那次事故的同类风险：迁移类工具必须自带闸门。
    """
    from h3d.archive.migrate import migrate

    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "a.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="项目"):
        migrate(str(tmp_path))


def test_migrate_does_not_modify_products(tmp_path):
    """迁移只读：产物字节哈希前后必须一致（禁止「顺手规范化」）。"""
    from h3d.archive.migrate import migrate

    root = _fake_project(tmp_path)
    stage = root / "阶段成果"
    mesh = stage / "30_dense" / "relic_001" / "relic_001.ply"
    raw = stage / "00_raw" / "relic_001" / "scan.ply"
    before = (_digest(str(mesh)), _digest(str(raw)), mesh.stat().st_mtime_ns)

    migrate(str(root))

    after = (_digest(str(mesh)), _digest(str(raw)), mesh.stat().st_mtime_ns)
    assert before == after


def test_migrate_accepts_stage_root_argument(tmp_path):
    """用户可能把「阶段成果」直接拖进来（GUI 选目录时很常见）。"""
    from h3d.archive import Archive
    from h3d.archive.migrate import migrate

    root = _fake_project(tmp_path)
    migrate(str(root / "阶段成果"))
    assert Archive.open(str(root)).artifact().artifact_id == "relic_001"


def test_migrate_content_key_changes_when_mesh_changes(tmp_path):
    """产物重跑后哈希必须变 —— 否则缓存会把新网格当成旧网格复用。"""
    from h3d.archive import Archive
    from h3d.archive.migrate import migrate

    root = _fake_project(tmp_path)
    migrate(str(root))
    k1 = Archive.open(str(root)).latest("E1").content_key

    (root / "阶段成果" / "30_dense" / "relic_001" /
     "relic_001.ply").write_bytes(b"PLY-dense-v2\n")
    res = migrate(str(root), epoch_id="E2", force_new_epoch=True)
    assert res["status"] == "created"
    k2 = Archive.open(str(root)).latest("E2").content_key
    assert k1 != k2


def test_migrate_reports_missing_products(tmp_path):
    """缺产物不该崩，但要如实报告缺了什么（静默跳过 = 报表后来一片 N/A）。"""
    from h3d.archive.migrate import migrate

    root = _fake_project(tmp_path)
    os.remove(root / "阶段成果" / "50_tex" / "relic_001" /
              "relic_001_L1.0_diffuse.png")
    res = migrate(str(root))
    assert "texture" in res["missing"]


# --------------------------------------------------------------------------- #
# CLI 接线（h3d archive migrate / status）
# --------------------------------------------------------------------------- #
def test_cli_archive_migrate_then_status(tmp_path, capsys):
    from h3d.cli import main

    root = _fake_project(tmp_path)
    assert main(["archive", "migrate", "-p", str(root)]) == 0
    out = capsys.readouterr().out
    assert "已建档" in out
    assert "relic_001" in out

    assert main(["archive", "status", "-p", str(root)]) == 0
    out = capsys.readouterr().out
    assert "基准期" in out and "E1" in out


def test_cli_archive_status_without_archive_returns_1(tmp_path, capsys):
    from h3d.cli import main

    assert main(["archive", "status", "-p", str(tmp_path)]) == 1
    assert "还没有文物档案" in capsys.readouterr().err


def test_cli_archive_migrate_on_plain_dir_returns_1(tmp_path, capsys):
    """闸门在 CLI 层也要生效：普通目录必须 rc=1，不能"成功"建一个空档案。"""
    from h3d.cli import main

    (tmp_path / "output").mkdir()
    assert main(["archive", "migrate", "-p", str(tmp_path)]) == 1
