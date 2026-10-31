"""tests.test_archive_ref_audit : 资产里的图集引用体检（B1 第八片）。

背景（真机 2026-10-31）：B1 切片 7 之前，``migrate`` 与 runner 都会往
``CanonicalAsset.uv_atlas_ref`` 里写 ``"atlas-v1"``——而档案里**没有**对应记录。
于是资产声称自己挂在某一版图集上，继承、复测比对、过期标记全部无从判定，
而报表、耗时、日志一切正常。改掉写入口只对**新建档**生效，存量档案里那批
假引用还在。

这一组测试锁住体检与修复的对外行为（seam = ``audit_atlas_refs`` /
``repair_atlas_refs``）：

* 查无实体 → ``dangling``（修复动作 = 置空 + 留痕）
* 记录晚于资产 → ``unverified``（只报不修：实体是后来的，写引用那一刻没有）
* 跨项目限定名 ``<artifact>@atlas-vN`` → ``external``（本档案内无从判定，不动）
* 空引用 → ``empty``（迁移后新建档的正常态）

判据必须是「档案里的实体」，不是「字符串长得像版本号」——后者正是当初
手写常量能活过两个版本的原因。
"""

from __future__ import annotations

import hashlib
import json
import os

import numpy as np
import pytest

from h3d.archive import Archive, Artifact, CanonicalAsset, Epoch
from h3d.archive.atlas import AtlasRecord, AtlasRegistry, save_uv_layout


def _archive(tmp_path) -> Archive:
    """建一个只有基准期 E1 的空档案。"""
    arch = Archive.init(str(tmp_path), Artifact(artifact_id="relic_001",
                                                title="彩绘兵马俑"))
    arch.add_epoch(Epoch(epoch_id="E1", artifact_id="relic_001",
                         route="A", captured_on="2026-10-26",
                         is_baseline=True))
    return Archive.open(str(tmp_path))


def _key(tag: str) -> str:
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()


def _add_asset(arch: Archive, *, tag: str, ref: str | None,
               created_at: str = "2026-10-31T14:18:27") -> CanonicalAsset:
    """往基准期登记一条资产（不同 tag 得到不同版本，不会被内容哈希吞掉）。"""
    return arch.register(CanonicalAsset(
        epoch_id="E1", version=1, content_key=_key(tag),
        mesh_path="阶段成果/50_tex/relic_001/relic_001_L1.0.ply",
        texture_path="阶段成果/50_tex/relic_001/relic_001_L1.0_diffuse.png",
        uv_atlas_ref=ref, created_at=created_at))


def _freeze(arch: Archive, version: str = "atlas-v1", *,
            frozen_at: str = "2026-10-31T16:23:33") -> AtlasRecord:
    """登记一条图集记录（不写真 npz：体检只看记录，不看布局内容）。"""
    return AtlasRegistry(arch).register(AtlasRecord(
        atlas_version=version, artifact_id="relic_001", epoch_id="E1",
        uv_digest=hashlib.sha256(version.encode()).hexdigest(),
        item_id="relic_001", size_px=2048, frozen_at=frozen_at))


class TestRefWithoutEntityIsDangling:
    def test_ref_pointing_at_missing_atlas_record_is_reported(self, tmp_path):
        """资产写着 atlas-v1，档案里却没有 atlas/atlas-v1.json → dangling。

        这是真机 1.1.11 的原样：``migrate`` 写死 ``atlas-v1``，项目连
        ``archive/atlas/`` 目录都没有。
        """
        from h3d.archive.audit import STATE_DANGLING, audit_atlas_refs

        arch = _archive(tmp_path)
        _add_asset(arch, tag="v1", ref="atlas-v1")

        findings = audit_atlas_refs(arch)

        assert len(findings) == 1
        f = findings[0]
        assert f.state == STATE_DANGLING
        assert f.ref == "atlas-v1"
        assert (f.epoch_id, f.version) == ("E1", 1)
        assert "atlas-v1" in f.detail


class TestEveryAssetAppearsInTheReport:
    def test_four_states_are_distinguished(self, tmp_path):
        """ok / empty / external / unverified 四种都要**出现在表里**。

        只报异常（dangling）是不够的：一张「0 行」的体检表无法区分「全部合格」
        与「一条都没查」。所以每条资产都必须落一行，状态各自可辨。

        * ok         —— 记录存在，且早于资产创建（写引用那一刻实体就在）
        * empty      —— 无引用（迁移后新建档的正常态）
        * external   —— 跨项目限定名 ``relic_001@atlas-v1``，本档案内无从判定
        * unverified —— 记录晚于资产：现在有实体，但写引用时还不存在
        """
        from h3d.archive.audit import (STATE_EMPTY, STATE_EXTERNAL, STATE_OK,
                                       STATE_UNVERIFIED, audit_atlas_refs)

        arch = _archive(tmp_path)
        _freeze(arch, "atlas-v1", frozen_at="2026-10-31T10:00:00")
        _add_asset(arch, tag="ok", ref="atlas-v1",
                   created_at="2026-10-31T11:00:00")
        _add_asset(arch, tag="empty", ref="",
                   created_at="2026-10-31T11:00:00")
        _add_asset(arch, tag="ext", ref="relic_001@atlas-v1",
                   created_at="2026-10-31T11:00:00")
        _add_asset(arch, tag="late", ref="atlas-v1",
                   created_at="2026-10-31T09:00:00")

        by_state = {f.state: f for f in audit_atlas_refs(arch)}

        assert set(by_state) == {STATE_OK, STATE_EMPTY, STATE_EXTERNAL,
                                 STATE_UNVERIFIED}
        assert by_state[STATE_OK].version == 1
        assert by_state[STATE_EXTERNAL].ref == "relic_001@atlas-v1"
        # 时间倒挂的 detail 必须点出两个时间，否则用户无法判断该不该重跑
        assert "10:00" in by_state[STATE_UNVERIFIED].detail
        assert "09:00" in by_state[STATE_UNVERIFIED].detail


class TestRepairClearsOnlyDangling:
    def test_repair_clears_dangling_and_leaves_a_trail(self, tmp_path):
        """修复只动「查无实体」那一条，其余文件字节级不变。

        判据不能只看「ref 变空了」：修复如果顺手重写了别的资产，会让
        ``content_key``/时间戳/参数在无提示的情况下漂移，历史结论就不可复现了。
        所以拿字节比对——没被修的资产文件一个字节都不该变。

        同时把原值留在 ``params["ref_repair"]`` 里：置空是「不再声称挂在某版
        图集上」，不是「抹掉曾经有过一条引用」。丢掉证据等于下次没人知道该
        重跑哪一件。
        """
        from h3d.archive.audit import (STATE_DANGLING, audit_atlas_refs,
                                       repair_atlas_refs)

        arch = _archive(tmp_path)
        _freeze(arch, "atlas-v1", frozen_at="2026-10-31T10:00:00")
        _add_asset(arch, tag="bad", ref="atlas-v9")    # v1 引用查无实体的 v9
        _add_asset(arch, tag="ok", ref="atlas-v1",
                   created_at="2026-10-31T11:00:00")   # v2 正常
        _add_asset(arch, tag="ext", ref="relic_001@atlas-v1")  # v3 跨项目

        assets_dir = os.path.join(str(tmp_path), "archive", "assets")
        before = {n: open(os.path.join(assets_dir, n), "rb").read()
                  for n in sorted(os.listdir(assets_dir))}

        report = repair_atlas_refs(arch)

        assert [f.version for f in report.repaired] == [1]
        assert report.repaired[0].ref == "atlas-v9"
        # v1 被清空，痕迹保留
        v1 = json.load(open(os.path.join(assets_dir, "E1__v1.json"),
                            encoding="utf-8"))
        assert v1["uv_atlas_ref"] == ""
        assert v1["params"]["ref_repair"]["dropped"] == "atlas-v9"
        assert v1["params"]["ref_repair"]["reason"] == STATE_DANGLING
        # 其它资产一个字节都不能动
        for name in ("E1__v2.json", "E1__v3.json"):
            assert open(os.path.join(assets_dir, name), "rb").read() == \
                before[name], f"{name} 被顺手改了"
        assert not audit_atlas_refs(arch) or all(
            f.state != STATE_DANGLING for f in audit_atlas_refs(arch))

    def test_dry_run_reports_but_writes_nothing(self, tmp_path):
        """``dry_run`` 只出报告不落盘 —— 修复是不可逆动作，得先能看见清单。"""
        from h3d.archive.audit import repair_atlas_refs

        arch = _archive(tmp_path)
        _add_asset(arch, tag="bad", ref="atlas-v1")
        assets_dir = os.path.join(str(tmp_path), "archive", "assets")
        before = open(os.path.join(assets_dir, "E1__v1.json"), "rb").read()

        report = repair_atlas_refs(arch, dry_run=True)

        assert [f.version for f in report.repaired] == [1]
        assert report.dry_run is True
        assert open(os.path.join(assets_dir, "E1__v1.json"), "rb").read() == before


class TestRewriteAssetRefusesToTamper:
    def test_changing_artifact_path_is_refused(self, tmp_path):
        """``rewrite_asset`` 只能改引用元数据：动产物路径必须抛。

        修复接口天生危险——它绕过 ``register`` 的版本机制直接覆盖文件。如果不
        把「可改字段」收窄，这个接口就成了篡改历史的通用后门：改掉
        ``texture_path`` 之后，一条 2026-10 的资产会指向 2027 年的纹理，而所有
        报表、耗时、日志都正常。
        """
        import dataclasses

        arch = _archive(tmp_path)
        asset = _add_asset(arch, tag="v1", ref="atlas-v9")

        tampered = dataclasses.replace(
            asset, texture_path="阶段成果/50_tex/relic_001/别处的纹理.png")
        with pytest.raises(ValueError, match="只有引用类字段可改"):
            arch.rewrite_asset(tampered)

        # 文件没被写坏，原值还在
        path = os.path.join(str(tmp_path), "archive", "assets", "E1__v1.json")
        assert json.load(open(path, encoding="utf-8"))["texture_path"] == \
            "阶段成果/50_tex/relic_001/relic_001_L1.0_diffuse.png"

    def test_rewriting_missing_asset_is_refused(self, tmp_path):
        """档案里没有这条资产 → 抛 ``FileNotFoundError``，不凭空造文件。"""
        import dataclasses

        arch = _archive(tmp_path)
        ghost = CanonicalAsset(
            epoch_id="E1", version=7, content_key=_key("ghost"),
            uv_atlas_ref="")
        with pytest.raises(FileNotFoundError):
            arch.rewrite_asset(ghost)
        assert not os.path.exists(
            os.path.join(str(tmp_path), "archive", "assets", "E1__v7.json"))
