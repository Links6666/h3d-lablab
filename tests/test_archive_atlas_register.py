"""tests.test_archive_atlas_register : 图集登记（B1 第七片）。

背景（真机 2026-10-31）：B1 只做了「冻结机制 + 继承读取端」，**没有任何代码
写 ``archive/atlas/atlas-vN.json``**。于是 ``h3d archive inherit`` 永远报「基准
项目没有 atlas-v1 的图集记录」——而且重跑 M4 也补不上（写 uv.npz 与登记是两
件事）。同一处还留着 ``uv_atlas_ref="atlas-v1"`` 的手写常量，等于资产声称自己
挂在一个从不存在的图集上。

这一组测试锁住登记的对外行为（seam = ``register_layout``）：
创建 v1 / 同布局幂等 / 布局变了自动换 v2（旧版不覆盖）。
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest

from h3d.archive import Archive, Artifact, CanonicalAsset, Epoch
from h3d.archive.atlas import ATLAS_DIR, AtlasRegistry, register_layout, uv_digest
from h3d.archive.atlas import save_uv_layout


def _archive(tmp_path) -> Archive:
    """建一个只有基准期 E1 的空档案。"""
    arch = Archive.init(str(tmp_path), Artifact(artifact_id="relic_001",
                                                title="彩绘兵马俑"))
    arch.add_epoch(Epoch(epoch_id="E1", artifact_id="relic_001",
                         route="A", captured_on="2026-10-26",
                         is_baseline=True))
    return Archive.open(str(tmp_path))


def _layout(uv, faces):
    return uv, faces


def _uv_square(scale: float = 1.0):
    """两个三角形铺满 UV 方形的简单布局（顶点数 4 / 面数 2）。"""
    uv = np.array([[0.0, 0.0], [scale, 0.0], [scale, scale], [0.0, scale]],
                  dtype=np.float64)
    faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    return uv, faces


class TestRegisterLayoutCreatesV1:
    def test_first_registration_creates_atlas_v1(self, tmp_path):
        """基准期首次登记 → atlas-v1，记录落在 archive/atlas/atlas-v1.json。"""
        arch = _archive(tmp_path)
        uv, faces = _uv_square()
        uv_path = str(tmp_path / "阶段成果" / "50_tex" / "relic_001"
                      / "relic_001_uv.npz")
        os.makedirs(os.path.dirname(uv_path), exist_ok=True)
        save_uv_layout(uv_path, uv, faces, verts=np.zeros((4, 3)),
                       normals=np.zeros((4, 3)))

        rec = register_layout(arch, uv_path, item_id="relic_001", size_px=2048)

        assert rec is not None
        assert rec.atlas_version == "atlas-v1"
        # 与 npz 里的指纹必须一致：继承端拿 digest 比对，算错就等于换了图集
        assert rec.uv_digest == uv_digest(uv, faces)
        assert rec.artifact_id == "relic_001"
        assert rec.epoch_id == "E1"
        assert rec.size_px == 2048
        assert rec.texels == 2048 * 2048
        # 路径必须相对项目根（换盘符/换机器不断，与 CanonicalAsset 同纪律）
        assert rec.uv_rel_path == "阶段成果/50_tex/relic_001/relic_001_uv.npz"
        assert not os.path.isabs(rec.uv_rel_path)

        on_disk = json.load(open(os.path.join(str(tmp_path), "archive", ATLAS_DIR,
                                              "atlas-v1.json"),
                                 encoding="utf-8"))
        assert on_disk["uv_digest"] == rec.uv_digest
        assert on_disk["frozen_at"], "登记必须落冻结时间，否则无法判「何时换的版」"


class TestRegisterLayoutIsIdempotent:
    def test_same_layout_twice_keeps_v1(self, tmp_path):
        """同一份布局再登记 → 仍是 v1，不新增记录、不改 mtime。

        判据不能只看返回值：``register`` 内部若不比对 digest，第二次就会造出
        atlas-v2，于是每跑一次 M4 版本号涨一格，复测期引用的到底是哪一版再也
        说不清。
        """
        arch = _archive(tmp_path)
        uv, faces = _uv_square()
        uv_path = str(tmp_path / "阶段成果" / "50_tex" / "relic_001"
                      / "relic_001_uv.npz")
        os.makedirs(os.path.dirname(uv_path), exist_ok=True)
        save_uv_layout(uv_path, uv, faces)

        first = register_layout(arch, uv_path, item_id="relic_001", size_px=1024)
        stamp = os.path.getmtime(os.path.join(str(tmp_path), "archive", ATLAS_DIR,
                                              "atlas-v1.json"))
        second = register_layout(arch, uv_path, item_id="relic_001", size_px=1024)

        assert first.atlas_version == "atlas-v1"
        assert second.atlas_version == "atlas-v1"
        assert AtlasRegistry(arch).versions() == [first] or \
            [r.atlas_version for r in AtlasRegistry(arch).versions()] == ["atlas-v1"]
        assert os.path.getmtime(os.path.join(str(tmp_path), "archive", ATLAS_DIR,
                                             "atlas-v1.json")) == stamp


class TestRegisterLayoutBumpsVersion:
    def test_changed_layout_creates_v2_and_keeps_v1(self, tmp_path, caplog):
        """布局变了 → 新建 atlas-v2，v1 原样保留，且必须 WARNING。

        「允许换版」与「允许静默换版」是两件事：v1 覆盖掉等于让所有基于旧图集
        的历史结论悄悄失效，而报表、耗时、日志全部正常。所以旧文件必须仍在，
        并且日志里能看到「谁把图集换了、旧版还在」。
        """
        arch = _archive(tmp_path)
        uv_path = str(tmp_path / "阶段成果" / "50_tex" / "relic_001"
                      / "relic_001_uv.npz")
        os.makedirs(os.path.dirname(uv_path), exist_ok=True)

        uv1, faces1 = _uv_square(1.0)
        save_uv_layout(uv_path, uv1, faces1)
        v1 = register_layout(arch, uv_path, item_id="relic_001", size_px=1024)
        v1_file = os.path.join(str(tmp_path), "archive", ATLAS_DIR, "atlas-v1.json")
        v1_bytes = open(v1_file, "rb").read()

        # xatlas 换了参数 / 网格变了 → 布局跟着变
        uv2 = uv1 * 0.5
        save_uv_layout(uv_path, uv2, faces1)
        with caplog.at_level("WARNING"):
            v2 = register_layout(arch, uv_path, item_id="relic_001", size_px=1024)

        assert v1.atlas_version == "atlas-v1"
        assert v2.atlas_version == "atlas-v2"
        assert v2.uv_digest != v1.uv_digest
        assert open(v1_file, "rb").read() == v1_bytes, "v1 被覆盖了"
        assert [r.atlas_version for r in AtlasRegistry(arch).versions()] == \
            ["atlas-v1", "atlas-v2"]
        assert any(r.levelname == "WARNING" and "atlas-v2" in r.getMessage()
                   for r in caplog.records), "换版必须留 WARNING"


class TestRegisterLayoutRefusesSilently:
    def test_missing_layout_does_not_create_atlas_dir(self, tmp_path):
        """没有 uv.npz → 返回 None，且不建 archive/atlas/。

        档案是增量能力，不能反过来让建模失败；但也不能为了「看起来登记过」就
        建一个空目录——继承端会以为图集存在，然后读到空记录。
        """
        arch = _archive(tmp_path)
        rec = register_layout(arch, str(tmp_path / "nope.npz"),
                              item_id="relic_001", size_px=1024)
        assert rec is None
        assert not os.path.isdir(os.path.join(str(tmp_path), "archive", ATLAS_DIR))

    def test_corrupt_layout_is_refused(self, tmp_path):
        """损坏的 npz → None（不抛），且不产生图集记录。"""
        arch = _archive(tmp_path)
        uv_path = os.path.join(str(tmp_path), "50_tex", "relic_001_uv.npz")
        os.makedirs(os.path.dirname(uv_path), exist_ok=True)
        with open(uv_path, "wb") as fh:
            fh.write(b"not a real npz")

        assert register_layout(arch, uv_path, item_id="relic_001") is None
        assert not os.path.isdir(os.path.join(str(tmp_path), "archive", ATLAS_DIR))
