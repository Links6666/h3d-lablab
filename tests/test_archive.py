"""档案层（B0）单元测试：内容哈希、三级模型往返、只读挂载、版本不可变。

对应 ADR-0001 的切片顺序。本文件只依赖标准库与 ``h3d.archive``，
不触碰 runner / GUI，保证能在任何环境独立跑红跑绿。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# --------------------------------------------------------------------------- #
# 切片 1：content_key —— 内容哈希（纯函数）
# --------------------------------------------------------------------------- #
def test_content_key_is_stable():
    """同一输入 + 同一参数 → 同一 key（跨进程、跨机器可复现）。"""
    from h3d.archive import content_key

    a = content_key(inputs={"cloud": "a1b2"}, params={"radius": 0.1})
    b = content_key(inputs={"cloud": "a1b2"}, params={"radius": 0.1})
    assert a == b
    assert len(a) == 64  # sha256 hexdigest


def test_content_key_changes_with_content():
    from h3d.archive import content_key

    a = content_key(inputs={"cloud": "a1b2"}, params={})
    b = content_key(inputs={"cloud": "ffff"}, params={})
    assert a != b


def test_content_key_changes_with_params():
    from h3d.archive import content_key

    a = content_key(inputs={"cloud": "a1b2"}, params={"radius": 0.1})
    b = content_key(inputs={"cloud": "a1b2"}, params={"radius": 0.2})
    assert a != b


def test_content_key_ignores_key_order():
    """字典序不影响结果：参数快照是 JSON，写入顺序不应算作不同输入。"""
    from h3d.archive import content_key

    a = content_key(inputs={"a": "1", "b": "2"}, params={"x": 1, "y": 2})
    b = content_key(inputs={"b": "2", "a": "1"}, params={"y": 2, "x": 1})
    assert a == b


def test_content_key_does_not_concat_collide():
    """拼接式实现会让 {a:1,b:23} 与 {a:12,b:3} 撞车，这里必须区分。"""
    from h3d.archive import content_key

    a = content_key(inputs={"a": "1", "b": "23"}, params={})
    b = content_key(inputs={"a": "12", "b": "3"}, params={})
    assert a != b


def test_file_digest_of_missing_file_raises(tmp_path):
    """文件不存在必须报错，不能静默返回空哈希（否则缓存会把「没数据」当命中）。"""
    from h3d.archive import file_digest

    with pytest.raises(FileNotFoundError):
        file_digest(str(tmp_path / "not-exists.ply"))


def test_file_digest_of_same_size_differs_by_content(tmp_path):
    """同尺寸不同内容必须不同哈希（按大小+路径做键的实现在此露馅）。"""
    from h3d.archive import file_digest

    p1 = tmp_path / "a.bin"
    p2 = tmp_path / "b.bin"
    p1.write_bytes(b"0" * 1024)
    p2.write_bytes(b"1" * 1024)
    assert file_digest(str(p1)) != file_digest(str(p2))


# --------------------------------------------------------------------------- #
# 切片 2：三级模型 —— Artifact / Epoch / CanonicalAsset 与 JSON 往返
# --------------------------------------------------------------------------- #
def _artifact():
    from h3d.archive import Artifact

    return Artifact(artifact_id="relic_001", title="彩绘陶俑", dynasty="汉",
                    material="陶", height_cm=48.5, width_cm=17.0,
                    protection_level="三级", location="库房A")


def _epoch(epoch_id="E1", route="A", baseline=True):
    from h3d.archive import Epoch

    return Epoch(epoch_id=epoch_id, artifact_id="relic_001",
                 captured_on="2026-10-01", route=route, device="Faro Focus",
                 raw_paths=["阶段成果/00_raw/relic_001/a.ply"],
                 colour_protocol={"card": "ColorChecker24", "ev": 0.0},
                 scale_ref={"kind": "control_points", "n": 8},
                 qc_status="pass", is_baseline=baseline)


def test_artifact_rejects_empty_id():
    """artifact_id 是全局主键，空值会让所有时相失去归属，必须当场拒绝。"""
    from h3d.archive import Artifact

    with pytest.raises(ValueError, match="artifact_id"):
        Artifact(artifact_id="", title="x")


def test_artifact_roundtrip():
    from h3d.archive import Artifact

    a = _artifact()
    assert Artifact.from_dict(a.to_dict()) == a


def test_epoch_rejects_unknown_route():
    """route 只有 A/B 两种；拼错的 "a" 或 "C" 必须报错，不能落盘成脏数据。"""
    from h3d.archive import Epoch

    with pytest.raises(ValueError, match="route"):
        Epoch(epoch_id="E1", artifact_id="x", captured_on="2026-10-01",
              route="C", device="", raw_paths=[], colour_protocol={},
              scale_ref={}, qc_status="pending", is_baseline=False)


def test_epoch_roundtrip():
    from h3d.archive import Epoch

    e = _epoch()
    back = Epoch.from_dict(e.to_dict())
    assert back == e
    assert back.captured_on == "2026-10-01"


def test_epoch_accepts_lowercase_route_by_normalising():
    """用户/GUI 传小写 a 时归一为大写，不报错也不落脏值。"""
    from h3d.archive import Epoch

    e = Epoch(epoch_id="E1", artifact_id="x", captured_on="2026-10-01",
              route="b", device="", raw_paths=[], colour_protocol={},
              scale_ref={}, qc_status="pending", is_baseline=False)
    assert e.route == "B"


def test_canonical_asset_roundtrip():
    from h3d.archive import CanonicalAsset

    c = CanonicalAsset(
        epoch_id="E1", version=1, content_key="f" * 64,
        mesh_path="阶段成果/30_dense/relic_001/relic_001.ply",
        cloud_path=None,
        texture_path="阶段成果/50_tex/relic_001/relic_001_L1.0_diffuse.png",
        uv_atlas_ref="atlas-v1", cameras_path="阶段成果/90_meta/cameras.json",
        params={"dense_max_image_size": 1600},
        created_at="2026-10-31T10:00:00")
    assert CanonicalAsset.from_dict(c.to_dict()) == c


def test_canonical_asset_rejects_absolute_path():
    """D3 的要求：档案里只存**相对路径**，否则项目一挪位置全断。

    绝对路径在换机器/换盘符后仍能序列化成功，问题要到打开时才暴露，
    所以必须登记时就拦下。
    """
    from h3d.archive import CanonicalAsset

    with pytest.raises(ValueError, match="相对路径"):
        CanonicalAsset(
            epoch_id="E1", version=1, content_key="0" * 64,
            mesh_path="E:/兵马俑照片/1.1.12/阶段成果/30_dense/x.ply",
            cloud_path=None, texture_path=None, uv_atlas_ref=None,
            cameras_path=None, params={}, created_at="")


def test_canonical_asset_rejects_bad_hash_length():
    from h3d.archive import CanonicalAsset

    with pytest.raises(ValueError, match="content_key"):
        CanonicalAsset(epoch_id="E1", version=1, content_key="abc",
                       mesh_path=None, cloud_path=None, texture_path=None,
                       uv_atlas_ref=None, cameras_path=None, params={},
                       created_at="")


# --------------------------------------------------------------------------- #
# 切片 3：Archive —— 落盘、基准期规则、版本自增、缓存命中、只读挂载
# --------------------------------------------------------------------------- #
def _open(tmp_path):
    from h3d.archive import Archive

    return Archive.init(str(tmp_path), _artifact())


def _asset(epoch_id="E1", key=None, version=1):
    from h3d.archive import CanonicalAsset

    return CanonicalAsset(
        epoch_id=epoch_id, version=version, content_key=key or ("a" * 64),
        mesh_path="阶段成果/30_dense/relic_001/relic_001.ply",
        texture_path="阶段成果/50_tex/relic_001/diffuse.png",
        uv_atlas_ref="atlas-v1", params={"k": 1},
        created_at="2026-10-31T10:00:00")


def test_init_creates_layout(tmp_path):
    arch = _open(tmp_path)
    root = tmp_path / "archive"
    assert (root / "artifact.json").is_file()
    assert (root / "epochs").is_dir()
    assert (root / "assets").is_dir()
    assert arch.artifact().artifact_id == "relic_001"


def test_init_is_idempotent_and_keeps_epochs(tmp_path):
    """重复 init 不能清掉已有档案（GUI 每次打开项目都会调）。"""
    arch = _open(tmp_path)
    arch.add_epoch(_epoch())
    again = _open(tmp_path)
    assert [e.epoch_id for e in again.epochs()] == ["E1"]


def test_add_epoch_persists_and_reloads(tmp_path):
    from h3d.archive import Archive

    arch = _open(tmp_path)
    arch.add_epoch(_epoch(epoch_id="E1"))
    reloaded = Archive.open(str(tmp_path))
    assert reloaded.epochs()[0].device == "Faro Focus"


def test_add_epoch_rejects_duplicate_id(tmp_path):
    arch = _open(tmp_path)
    arch.add_epoch(_epoch(epoch_id="E1"))
    with pytest.raises(ValueError, match="E1"):
        arch.add_epoch(_epoch(epoch_id="E1"))


def test_first_epoch_becomes_baseline_automatically(tmp_path):
    """首个时相自动为基准期 —— 否则用户第一次建档时没有任何坐标系锚点。"""
    arch = _open(tmp_path)
    arch.add_epoch(_epoch(epoch_id="E1", baseline=False))
    assert arch.baseline().epoch_id == "E1"


def test_second_epoch_does_not_steal_baseline(tmp_path):
    arch = _open(tmp_path)
    arch.add_epoch(_epoch(epoch_id="E1"))
    arch.add_epoch(_epoch(epoch_id="E2", baseline=False))
    assert arch.baseline().epoch_id == "E1"


def test_explicit_baseline_moves_with_warning(tmp_path, caplog):
    """显式指定新基准期要允许（换扫描仪重定图集是真实场景），但必须留痕。"""
    arch = _open(tmp_path)
    arch.add_epoch(_epoch(epoch_id="E1"))
    with caplog.at_level("WARNING"):
        arch.add_epoch(_epoch(epoch_id="E2", baseline=True))
    assert arch.baseline().epoch_id == "E2"
    assert any("基准期" in r.message for r in caplog.records)


def test_register_assigns_incrementing_versions(tmp_path):
    arch = _open(tmp_path)
    arch.add_epoch(_epoch(epoch_id="E1"))
    a1 = arch.register(_asset(key="a" * 64))
    a2 = arch.register(_asset(key="b" * 64))
    assert (a1.version, a2.version) == (1, 2)
    assert len(arch.assets("E1")) == 2


def test_register_same_content_key_hits_cache(tmp_path):
    """同内容哈希重复登记 → 返回已有版本，不新增。这是「不重复计算」的机制本体。"""
    arch = _open(tmp_path)
    arch.add_epoch(_epoch(epoch_id="E1"))
    a1 = arch.register(_asset(key="c" * 64))
    a2 = arch.register(_asset(key="c" * 64))
    assert a1.version == a2.version == 1
    assert len(arch.assets("E1")) == 1


def test_register_unknown_epoch_raises(tmp_path):
    arch = _open(tmp_path)
    with pytest.raises(KeyError, match="E9"):
        arch.register(_asset(epoch_id="E9"))


def test_old_version_file_is_kept(tmp_path):
    """旧版本必须留在磁盘上（可追溯），不能就地覆盖。"""
    arch = _open(tmp_path)
    arch.add_epoch(_epoch(epoch_id="E1"))
    arch.register(_asset(key="a" * 64))
    arch.register(_asset(key="b" * 64))
    files = sorted(p.name for p in (tmp_path / "archive" / "assets").iterdir())
    assert files == ["E1__v1.json", "E1__v2.json"]


def test_mount_returns_readonly_handle(tmp_path):
    (tmp_path / "阶段成果" / "30_dense" / "relic_001").mkdir(parents=True)
    (tmp_path / "阶段成果" / "30_dense" / "relic_001" /
     "relic_001.ply").write_bytes(b"PLY\n")
    arch = _open(tmp_path)
    arch.add_epoch(_epoch(epoch_id="E1"))
    asset = arch.register(_asset())
    mount = arch.mount(asset)
    assert mount.read_bytes("mesh") == b"PLY\n"
    assert mount.path("mesh").endswith("relic_001.ply")


@pytest.mark.parametrize("mode", ["w", "wb", "a", "r+b", "x"])
def test_mount_refuses_write_modes(tmp_path, mode):
    """只读约束必须由代码强制（指南 1.2 实施建议），不能只靠约定。"""
    (tmp_path / "阶段成果" / "30_dense" / "relic_001").mkdir(parents=True)
    (tmp_path / "阶段成果" / "30_dense" / "relic_001" /
     "relic_001.ply").write_bytes(b"PLY\n")
    arch = _open(tmp_path)
    arch.add_epoch(_epoch(epoch_id="E1"))
    mount = arch.mount(arch.register(_asset()))
    with pytest.raises(Exception, match="只读|ReadOnly"):
        mount.open("mesh", mode)


def test_mount_refuses_path_escape(tmp_path):
    """契约里的相对路径可能被手改成 ``../../etc``，解析时必须锁在项目根内。"""
    arch = _open(tmp_path)
    arch.add_epoch(_epoch(epoch_id="E1"))
    from h3d.archive import CanonicalAsset

    bad = CanonicalAsset.from_dict({
        **_asset().to_dict(),
        "mesh_path": "../../outside.ply",
    })
    with pytest.raises(ValueError, match="项目根"):
        arch.mount(bad)
