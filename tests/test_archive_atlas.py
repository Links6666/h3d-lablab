"""档案级 UV 图集登记与冻结（B1 切片 1）。

为什么需要「图集」这个独立实体
------------------------------
指南 2.2 节：两个时相要逐 texel 比色，必须共用同一套 UV 展开。若第 2 期独立
重新展开，texel 与第 1 期不存在一一对应关系，ΔE 逐点比较完全失效。

而「共用同一套 UV」在代码里的前提是：**基准期的 UV 布局本身要落盘并且不可变**。
M4 现状只在内存里持有 ``uv/faces``，跑完就丢 —— 于是第二次烘焙根本无从「继承」，
只能重新展开，而 xatlas 重展开的结果不保证与上次逐 texel 对齐。

冻结语义（指南「风险提示」的落点）
----------------------------------
* 一旦某版本登记，它的 ``uv_digest`` 不可改。基准期重算导致 UV 变化时，
  必须**新建版本**（``atlas-v2``），由调用方把引用旧版本的资产标记过期。
* 同版本 + 同 digest 的重复登记是**幂等**的：GUI 每次打开项目都会尝试登记。
* 同版本 + 不同 digest → 抛 :class:`AtlasFrozen`。**不允许静默覆盖**：
  覆盖等于让所有基于旧图集的历史结论悄悄失效。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _uv(n: int = 4, seed: int = 0):
    rng = np.random.default_rng(seed)
    uv = rng.random((n, 2))
    faces = np.array([[0, 1, 2], [1, 2, 3]], dtype=np.int64)
    return uv, faces


def _project(tmp_path):
    (tmp_path / "阶段成果" / "00_raw" / "relic_001").mkdir(parents=True)
    (tmp_path / "阶段成果" / "00_raw" / "relic_001" / "scan.ply").write_bytes(b"PLY\n")
    return tmp_path


def _arch(tmp_path):
    from h3d.archive.workspace import ensure_archive
    from h3d.archive import Archive

    root = _project(tmp_path)
    ensure_archive(str(root))
    return Archive.open(str(root))


def _rec(epoch_id: str = "E1", version: str = "atlas-v1", digest: str = "a" * 64,
         uv_rel: str = "阶段成果/50_tex/relic_001/relic_001_uv.npz"):
    from h3d.archive.atlas import AtlasRecord

    return AtlasRecord(
        atlas_version=version, artifact_id="relic_001", epoch_id=epoch_id,
        uv_digest=digest, mesh_digest="d" * 64, size_px=1024,
        uv_rel_path=uv_rel, texels=1234, frozen_at="2026-10-31T10:00:00")


class TestUvDigest:
    def test_same_input_same_digest(self):
        from h3d.archive.atlas import uv_digest

        uv, faces = _uv()
        assert uv_digest(uv, faces) == uv_digest(uv.copy(), faces.copy())

    def test_digest_is_64_hex(self):
        from h3d.archive.atlas import uv_digest

        d = uv_digest(*_uv())
        assert len(d) == 64 and all(c in "0123456789abcdef" for c in d)

    def test_changed_uv_changes_digest(self):
        from h3d.archive.atlas import uv_digest

        uv, faces = _uv()
        uv2 = uv.copy()
        uv2[0, 0] += 1e-3
        assert uv_digest(uv, faces) != uv_digest(uv2, faces)

    def test_face_order_matters(self):
        """面的顺序变了就是另一套展开 —— 必须换 digest，否则会静默错位。"""
        from h3d.archive.atlas import uv_digest

        uv, faces = _uv()
        assert uv_digest(uv, faces) != uv_digest(uv, faces[::-1])

    def test_shape_mismatch_does_not_collide(self):
        """不同顶点数的 UV 不许撞车（拼接式实现最容易在这里出错）。"""
        from h3d.archive.atlas import uv_digest

        uv, faces = _uv(4)
        uv8 = np.concatenate([uv, uv], axis=0)
        faces8 = np.concatenate([faces, faces + 4], axis=0)
        assert uv_digest(uv, faces) != uv_digest(uv8, faces8)

    def test_float32_and_float64_agree(self):
        """同一套 UV 换个 dtype 不许变成另一个 digest（否则 npz 往返就失效）。"""
        from h3d.archive.atlas import uv_digest

        uv, faces = _uv()
        d64 = uv_digest(uv.astype(np.float64), faces)
        d32 = uv_digest(uv.astype(np.float32).astype(np.float64), faces)
        assert d64 == d32


class TestAtlasRegistry:
    def test_register_writes_json(self, tmp_path):
        from h3d.archive.atlas import AtlasRegistry

        arch = _arch(tmp_path)
        reg = AtlasRegistry(arch)
        reg.register(_rec())
        p = os.path.join(arch.archive_dir, "atlas", "atlas-v1.json")
        assert os.path.isfile(p)

    def test_register_is_idempotent(self, tmp_path):
        """GUI 每次打开都会尝试登记 —— 同版本同 digest 必须原样返回。"""
        from h3d.archive.atlas import AtlasRegistry

        arch = _arch(tmp_path)
        reg = AtlasRegistry(arch)
        first = reg.register(_rec())
        p = os.path.join(arch.archive_dir, "atlas", "atlas-v1.json")
        before = open(p, "rb").read()
        second = reg.register(_rec())
        assert second.uv_digest == first.uv_digest
        assert open(p, "rb").read() == before, "幂等登记不得重写文件"

    def test_same_version_different_digest_is_refused(self, tmp_path):
        """冻结的核心：不许把 v1 的 UV 悄悄换掉。"""
        from h3d.archive.atlas import AtlasFrozen, AtlasRegistry

        reg = AtlasRegistry(_arch(tmp_path))
        reg.register(_rec(digest="a" * 64))
        with pytest.raises(AtlasFrozen) as ei:
            reg.register(_rec(digest="b" * 64))
        assert "atlas-v1" in str(ei.value)

    def test_new_version_allowed(self, tmp_path):
        from h3d.archive.atlas import AtlasRegistry

        reg = AtlasRegistry(_arch(tmp_path))
        reg.register(_rec(digest="a" * 64))
        rec2 = reg.register(_rec(version="atlas-v2", digest="b" * 64))
        assert rec2.atlas_version == "atlas-v2"

    def test_get_and_missing(self, tmp_path):
        from h3d.archive.atlas import AtlasRegistry

        reg = AtlasRegistry(_arch(tmp_path))
        reg.register(_rec())
        assert reg.get("atlas-v1") is not None
        assert reg.get("atlas-v9") is None

    def test_versions_sorted_numerically(self, tmp_path):
        """atlas-v10 必须排在 atlas-v9 之后（字符串排序会颠倒）。"""
        from h3d.archive.atlas import AtlasRegistry

        reg = AtlasRegistry(_arch(tmp_path))
        for v in ("atlas-v1", "atlas-v2", "atlas-v10"):
            reg.register(_rec(version=v, digest=f"{v[-1]}" * 64))
        assert [r.atlas_version for r in reg.versions()] == [
            "atlas-v1", "atlas-v2", "atlas-v10"]

    def test_current_is_latest(self, tmp_path):
        from h3d.archive.atlas import AtlasRegistry

        reg = AtlasRegistry(_arch(tmp_path))
        assert reg.current() is None
        reg.register(_rec())
        reg.register(_rec(version="atlas-v2", digest="b" * 64))
        assert reg.current().atlas_version == "atlas-v2"

    def test_next_version(self, tmp_path):
        from h3d.archive.atlas import AtlasRegistry

        reg = AtlasRegistry(_arch(tmp_path))
        assert reg.next_version() == "atlas-v1"
        reg.register(_rec())
        assert reg.next_version() == "atlas-v2"
        reg.register(_rec(version="atlas-v9", digest="9" * 64))
        assert reg.next_version() == "atlas-v10"

    def test_bad_version_name_rejected(self):
        from h3d.archive.atlas import AtlasRecord

        with pytest.raises(ValueError):
            AtlasRecord(atlas_version="atlas-1", artifact_id="r",
                        epoch_id="E1", uv_digest="a" * 64,
                        mesh_digest="b" * 64, size_px=1024,
                        uv_rel_path="x.npz")

    def test_absolute_path_rejected(self):
        from h3d.archive.atlas import AtlasRecord

        with pytest.raises(ValueError):
            AtlasRecord(atlas_version="atlas-v1", artifact_id="r",
                        epoch_id="E1", uv_digest="a" * 64,
                        mesh_digest="b" * 64, size_px=1024,
                        uv_rel_path="E:/x/y.npz")

    def test_bad_digest_rejected(self):
        from h3d.archive.atlas import AtlasRecord

        with pytest.raises(ValueError):
            AtlasRecord(atlas_version="atlas-v1", artifact_id="r",
                        epoch_id="E1", uv_digest="short",
                        mesh_digest="b" * 64, size_px=1024,
                        uv_rel_path="x.npz")

    def test_survives_reload(self, tmp_path):
        """新建 Registry 对象（等价于重启 GUI）仍能读到冻结记录。"""
        from h3d.archive import Archive
        from h3d.archive.atlas import AtlasFrozen, AtlasRegistry

        arch = _arch(tmp_path)
        AtlasRegistry(arch).register(_rec(digest="a" * 64))
        reg2 = AtlasRegistry(Archive.open(str(tmp_path)))
        assert reg2.get("atlas-v1").uv_digest == "a" * 64
        with pytest.raises(AtlasFrozen):
            reg2.register(_rec(digest="c" * 64))
