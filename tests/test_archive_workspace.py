"""档案层 GUI 入口（B0 切片 6）：打开项目时自动建档 + 时相列表数据。

背景
----
切片 5 让 runner 在**已建档**的项目上登记产物，但建档只能靠
``h3d archive migrate`` 命令 —— GUI 用户永远走不到那条路，于是界面上
「档案登记」等于不存在。本切片把建档接到「打开项目」这个动作上。

三条必须成立的纪律
------------------
1. **不误伤**：普通目录绝不能被建出一个 ``archive/``（2026-10-26 的
   ``output/`` 事故就是这么来的），闸门仍是 ``looks_like_h3d_project``。
2. **幂等**：GUI 每次打开项目都会调一次，重复调用**不得**重写任何文件。
3. **空时相要允许**：新建项目在跑之前只有采集数据、没有产物。若此时不建
   时相，切片 5 的登记会因为「无基准期」永久跳过 —— 跑完一次也补不上。
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

ITEM = "relic_001"


def _fake_project(tmp_path, *, item: str = ITEM, with_raw: bool = True,
                  with_outputs: bool = True):
    """造一个符合现有布局（``阶段成果/…``）的项目。"""
    stage = tmp_path / "阶段成果"
    raw = stage / "00_raw" / item
    raw.mkdir(parents=True)
    if with_raw:
        (raw / "scan.ply").write_bytes(b"PLY-raw\n")
    if with_outputs:
        dense = stage / "30_dense" / item
        dense.mkdir(parents=True)
        (dense / f"{item}.ply").write_bytes(b"PLY-dense\n")
        tex = stage / "50_tex" / item
        tex.mkdir(parents=True)
        (tex / f"{item}_L1.0_diffuse.png").write_bytes(b"PNG\n")
        meta = stage / "90_meta"
        meta.mkdir(parents=True)
        (meta / f"{item}_capture_meta.json").write_text("{}", encoding="utf-8")
    else:
        # 没有产物，但仍要有件目录证据：采集数据已经放进来了
        (stage / "00_photo" / item).mkdir(parents=True)
    return tmp_path


class TestEnsureArchive:
    def test_plain_directory_is_skipped_and_leaves_no_trace(self, tmp_path):
        """通用目录不许冒出 archive/ —— 与 migrate 同一条闸门。"""
        from h3d.archive.workspace import ensure_archive

        d = tmp_path / "我的文档"
        d.mkdir()
        (d / "output").mkdir()          # 通用名不算项目证据
        st = ensure_archive(str(d))
        assert st.state == "skipped"
        assert not (d / "archive").exists()
        assert "H3D 项目" in st.message
        assert st.is_open() is False

    def test_project_without_item_dir_is_skipped(self, tmp_path):
        """只有 config.yaml、没有任何件目录 → 不建档（不知道该给谁建）。"""
        from h3d.archive.workspace import ensure_archive

        (tmp_path / "config.yaml").write_text("project: {}\n", encoding="utf-8")
        st = ensure_archive(str(tmp_path))
        assert st.state == "skipped"
        assert not (tmp_path / "archive").exists()

    def test_creates_artifact_and_empty_epoch_for_fresh_project(self, tmp_path):
        """刚建的项目：有采集数据、无产物 → 建档 + 首个空时相。

        必须建时相，否则 runner 的登记会因「无基准期」永久跳过。
        """
        from h3d.archive import Archive
        from h3d.archive.workspace import ensure_archive

        root = _fake_project(tmp_path, with_outputs=False)
        st = ensure_archive(str(root))

        assert st.state == "created"
        assert st.artifact_id == ITEM
        arch = Archive.open(str(root))
        assert [e.epoch_id for e in arch.epochs()] == ["E1"]
        assert arch.baseline() is not None, "首个时相必须自动成为基准期"
        assert arch.latest("E1") is None, "还没有产物，不该凭空造一个 v1"
        assert st.rows[0].versions == 0

    def test_registers_first_asset_when_outputs_exist(self, tmp_path):
        """已跑完的老项目：顺手登记 v1（与 migrate 行为一致）。"""
        from h3d.archive import Archive
        from h3d.archive.workspace import ensure_archive

        root = _fake_project(tmp_path)
        st = ensure_archive(str(root))

        assert st.state == "created"
        asset = Archive.open(str(root)).latest("E1")
        assert asset is not None and asset.version == 1
        assert st.rows[0].versions == 1

    def test_second_call_is_existing_and_rewrites_nothing(self, tmp_path):
        """GUI 每次打开都会调 —— 幂等是硬要求。"""
        from h3d.archive.workspace import ensure_archive

        root = _fake_project(tmp_path)
        assert ensure_archive(str(root)).state == "created"
        art = root / "archive" / "artifact.json"
        before = art.read_bytes()
        st = ensure_archive(str(root))
        assert st.state == "existing"
        assert art.read_bytes() == before

    def test_accepts_stage_dir_path(self, tmp_path):
        """用户可能选中 ``阶段成果`` 目录，也要认。"""
        from h3d.archive.workspace import ensure_archive

        root = _fake_project(tmp_path)
        st = ensure_archive(str(root / "阶段成果"))
        assert st.state == "created"
        assert (root / "archive" / "artifact.json").is_file()

    def test_message_is_human_readable(self, tmp_path):
        from h3d.archive.workspace import ensure_archive

        root = _fake_project(tmp_path)
        st = ensure_archive(str(root))
        assert ITEM in st.message
        assert "E1" in st.message


class TestEpochRows:
    def test_rows_carry_route_baseline_and_versions(self, tmp_path):
        from h3d.archive import Archive
        from h3d.archive.workspace import epoch_rows

        root = _fake_project(tmp_path)
        from h3d.archive.workspace import ensure_archive

        ensure_archive(str(root))
        arch = Archive.open(str(root))
        rows = epoch_rows(arch)
        assert len(rows) == 1
        row = rows[0]
        assert row.epoch_id == "E1"
        assert row.route == "A"
        assert row.is_baseline is True
        assert row.versions == 1
        assert row.latest_version == 1
        assert row.captured_on, "采集日期必须有值，界面上不能空着"

    def test_new_version_bumps_row_count(self, tmp_path):
        """同一时相再登记一次不同内容 → 版本数 2，界面要能看出累加。"""
        import datetime as _dt

        from h3d.archive import Archive, CanonicalAsset
        from h3d.archive.workspace import ensure_archive, epoch_rows

        root = _fake_project(tmp_path)
        ensure_archive(str(root))
        arch = Archive.open(str(root))
        arch.register(CanonicalAsset(
            epoch_id="E1", version=1, content_key="a" * 64,
            mesh_path=f"阶段成果/30_dense/{ITEM}/{ITEM}.ply",
            cloud_path=None, texture_path=None, uv_atlas_ref="atlas-v1",
            cameras_path=None, params={"pipeline": "runner"},
            created_at=_dt.datetime.now().isoformat(timespec="seconds")))
        rows = epoch_rows(arch)
        assert rows[0].versions == 2
        assert rows[0].latest_version == 2

    def test_rows_sorted_by_capture_date(self, tmp_path):
        from h3d.archive import Archive, Epoch
        from h3d.archive.workspace import ensure_archive, epoch_rows

        root = _fake_project(tmp_path)
        ensure_archive(str(root))
        arch = Archive.open(str(root))
        arch.add_epoch(Epoch(
            epoch_id="E2", artifact_id=ITEM, captured_on="2020-01-01",
            route="A", device="", raw_paths=[], colour_protocol={},
            scale_ref={"kind": "unknown"}, qc_status="unknown",
            is_baseline=False))
        rows = epoch_rows(arch)
        assert [r.epoch_id for r in rows] == ["E2", "E1"], "按采集日期升序"

    def test_status_rows_match_archive(self, tmp_path):
        from h3d.archive.workspace import ensure_archive

        root = _fake_project(tmp_path)
        st = ensure_archive(str(root))
        assert isinstance(st.rows, tuple)
        assert st.rows[0].epoch_id == "E1"


class TestReadStatus:
    def test_read_status_is_none_without_archive(self, tmp_path):
        from h3d.archive.workspace import read_status

        root = _fake_project(tmp_path)
        assert read_status(str(root)) is None
        assert not (root / "archive").exists(), "只读查询不得建档"

    def test_read_status_after_ensure(self, tmp_path):
        from h3d.archive.workspace import ensure_archive, read_status

        root = _fake_project(tmp_path)
        ensure_archive(str(root))
        st = read_status(str(root))
        assert st is not None and st.state == "existing"
        assert st.rows[0].epoch_id == "E1"
