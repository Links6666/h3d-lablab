"""跨项目图集继承（B1 切片 5）：编排层。

语义（对应 D2 决策「独立项目 + 跨项目引用」）
--------------------------------------------
基准期在 A 项目，复测期在 B 项目。B 项目**不复制** A 的图集，只引用它的
版本号；纹理烘进 B 自己的 ``50_tex``，并登记成 B 的一份新版本。

**命名冲突是这里最容易埋雷的地方**：``atlas-v1`` 是项目内命名空间，
A 的 ``atlas-v1`` 与 B 的 ``atlas-v1`` 是两套完全不同的 UV。跨项目引用必须
写成限定名 ``<artifact_id>@<atlas_version>``（如 ``relic_001@atlas-v1``），
否则报表上会出现两个都叫 v1、texel 却对不上的图集。

失败必须显式
------------
以下任一情况**不烘图**、返回 ``failed`` 并说清缺什么：
* 基准期没有图集记录（说明基准期还没跑过 M4，或跑的是旧版不落 UV 布局）；
* 基准 UV 布局文件缺失/损坏（继承下去会烘出全黑图集而流程成功）；
* 自动配准被质量闸门拒绝（拿恒等变换去烘 = 把纹理整体错位）。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

ITEM = "relic_001"


def _sphere():
    import open3d as o3d

    m = o3d.geometry.TriangleMesh.create_sphere(radius=1.0, resolution=20)
    m.compute_vertex_normals()
    return m


def _write_photos(photo_dir, n=4):
    from PIL import Image

    os.makedirs(photo_dir, exist_ok=True)
    yy, xx = np.mgrid[0:300, 0:400]
    for i in range(n):
        ph = int(400 * i / n)
        img = np.stack([((xx + ph) % 400) * 255 // 400,
                        yy * 255 // 300,
                        (((xx + ph) // 32 + yy // 32) % 2) * 200 + 30],
                       axis=-1).astype(np.uint8)
        Image.fromarray(img).save(os.path.join(photo_dir, f"img_{i:02d}.png"))


def _cameras(n=4, offset=(0.0, 0.0, 0.0)):
    out = []
    for i in range(n):
        az = 2 * np.pi * i / n
        eye = np.array([3.6 * np.cos(az), 3.6 * np.sin(az), 0.3]) + np.asarray(offset)
        f = -eye / np.linalg.norm(eye)
        s = np.cross(f, np.array([0.0, 0.0, 1.0]))
        s /= np.linalg.norm(s)
        u = np.cross(s, f)
        R = np.stack([s, u, -f], axis=1).T
        out.append({"name": f"img_{i:02d}.png", "R": R, "t": eye, "cam_id": 1})
    return {1: {"w": 400, "h": 300, "fx": 420.0, "fy": 420.0,
                "cx": 200.0, "cy": 150.0}}, out


def _layer_archive(root, item=ITEM, with_atlas=True, with_asset=True):
    """建项目骨架 + 档案 + （可选）图集记录与基准资产。"""
    import open3d as o3d

    from h3d.archive import Archive, Artifact, CanonicalAsset, Epoch
    from h3d.archive.atlas import (AtlasRecord, AtlasRegistry, save_uv_layout,
                                   uv_digest)
    from h3d.texture.uv import unwrap_mesh

    stage = root / "阶段成果"
    (stage / "00_raw" / item).mkdir(parents=True)
    (stage / "00_raw" / item / "scan.ply").write_bytes(b"PLY\n")
    (stage / "30_dense" / item).mkdir(parents=True)
    (stage / "90_meta").mkdir(parents=True)
    mesh = _sphere()
    o3d.io.write_triangle_mesh(str(stage / "30_dense" / item / f"{item}.ply"),
                               mesh)

    arch = Archive.init(str(root), Artifact(artifact_id=item, title=item))
    arch.add_epoch(Epoch(epoch_id="E1", artifact_id=item,
                         captured_on="2026-01-01", route="A", is_baseline=True))

    if with_atlas:
        new_mesh, uv, faces = unwrap_mesh(mesh)
        uv_rel = f"阶段成果/50_tex/{item}/{item}_uv.npz"
        # 必须连展开后几何一起存（uv/faces 的索引属于 new_mesh，不是原始 PLY）
        save_uv_layout(str(root / uv_rel), uv, faces,
                       verts=np.asarray(new_mesh.vertices),
                       normals=np.asarray(new_mesh.vertex_normals))
        AtlasRegistry(arch).register(AtlasRecord(
            atlas_version="atlas-v1", artifact_id=item, epoch_id="E1",
            uv_digest=uv_digest(uv, faces), size_px=256, uv_rel_path=uv_rel))
    if with_asset:
        arch.register(CanonicalAsset(
            epoch_id="E1", version=1, content_key="a" * 64,
            mesh_path=f"阶段成果/30_dense/{item}/{item}.ply",
            texture_path=None, uv_atlas_ref="atlas-v1"))
    return root, item, mesh


def _base(tmp_path, **kw):
    return _layer_archive(tmp_path / "base", **kw)


def _source(tmp_path, offset=(0.0, 0.0, 0.0)):
    from h3d.sfm_solve import write_capture_meta

    root, item, mesh = _layer_archive(tmp_path / "src", with_atlas=False)
    _write_photos(str(root / "阶段成果" / "00_photo" / item))
    cams, imgs = _cameras(offset=offset)
    write_capture_meta(cams, imgs,
                       str(root / "阶段成果" / "90_meta" /
                           f"{item}_capture_meta.json"))
    return root, item, mesh


class TestInheritEpoch:
    def test_success_writes_texture_and_registers_asset(self, tmp_path):
        from h3d.archive import Archive
        from h3d.archive.inherit import Similarity
        from h3d.archive.inherit_epoch import inherit_epoch

        base, item, _m = _base(tmp_path)
        src, _i2, _m2 = _source(tmp_path)
        res = inherit_epoch(str(base), str(src),
                            transform=Similarity.identity(), size=256)

        assert res.ok, res.message
        assert os.path.isfile(res.texture_path)
        assert res.atlas_version == "atlas-v1"
        # 跨项目限定名：避免与复测项目自己的 atlas-v1 混淆
        arch = Archive.open(str(src))
        asset = arch.latest("E1")
        assert asset.uv_atlas_ref == f"{ITEM}@atlas-v1"
        assert asset.params["inherited_from"]["artifact_id"] == ITEM
        assert asset.params["inherited_from"]["atlas_version"] == "atlas-v1"

    def test_texture_matches_base_when_same_pose(self, tmp_path):
        """同一批照片、同一套位姿 → 继承纹理应与基准纹理几乎一致。"""
        from PIL import Image

        from h3d.archive.atlas import load_uv_layout_ex
        from h3d.archive.inherit import Similarity, inherit_texture
        from h3d.archive.inherit_epoch import inherit_epoch
        from h3d.io import make_mesh
        from h3d.photo_texture import load_capture_meta

        base, item, _mesh = _base(tmp_path)
        src, _i, _m = _source(tmp_path)
        res = inherit_epoch(str(base), str(src),
                            transform=Similarity.identity(), size=256)
        assert res.ok, res.message

        # 基准侧按同一套布局与相机独立烘一遍作为对照。几何必须用布局里存的
        # 展开后顶点（原始 PLY 的拓扑不同，面索引会越界）。
        layout = load_uv_layout_ex(
            str(base / "阶段成果" / "50_tex" / item / f"{item}_uv.npz"))
        mesh_b = make_mesh(layout.verts, layout.faces, layout.normals)
        cams, imgs = load_capture_meta(
            str(src / "阶段成果" / "90_meta" / f"{item}_capture_meta.json"))
        ref, _ = inherit_texture(
            mesh_b, layout.uv, layout.faces, imgs, cams,
            str(src / "阶段成果" / "00_photo" / item), Similarity.identity(),
            size=256)
        got = np.asarray(Image.open(res.texture_path).convert("RGB"))
        assert got.shape == ref.shape
        assert float(np.median(np.abs(got.astype(np.int16)
                                     - ref.astype(np.int16)))) < 6

    def test_missing_atlas_is_failure_not_silent(self, tmp_path):
        """基准期没有图集记录 → 明确失败，且不得产出任何纹理。"""
        from h3d.archive.inherit import Similarity
        from h3d.archive.inherit_epoch import inherit_epoch

        base, _i, _m = _base(tmp_path, with_atlas=False)
        src, _i2, _m2 = _source(tmp_path)
        res = inherit_epoch(str(base), str(src),
                            transform=Similarity.identity(), size=256)
        assert res.ok is False
        assert res.state == "failed"
        assert "图集" in res.message
        assert not res.texture_path

    def test_missing_uv_layout_is_failure(self, tmp_path):
        from h3d.archive.inherit import Similarity
        from h3d.archive.inherit_epoch import inherit_epoch

        base, item, _m = _base(tmp_path)
        (base / "阶段成果" / "50_tex" / item / f"{item}_uv.npz").unlink()
        src, _i2, _m2 = _source(tmp_path)
        res = inherit_epoch(str(base), str(src),
                            transform=Similarity.identity(), size=256)
        assert res.ok is False
        assert res.state == "failed"

    def test_rejected_registration_is_failure(self, tmp_path, monkeypatch):
        """自动配准被判无效时不能拿恒等变换去烘 —— 那等于把纹理整体错位。"""
        import h3d.archive.inherit_epoch as mod

        monkeypatch.setattr(mod, "_solve_transform", lambda *a, **k: (
            None, {"rejected": "diag_ratio 1.83 超出 ±30%"}))
        from h3d.archive.inherit_epoch import inherit_epoch

        base, _i, _m = _base(tmp_path)
        src, _i2, _m2 = _source(tmp_path)
        res = inherit_epoch(str(base), str(src), size=256)
        assert res.ok is False
        assert "配准" in res.message

    def test_unregistered_source_project_is_failure(self, tmp_path):
        from h3d.archive.inherit import Similarity
        from h3d.archive.inherit_epoch import inherit_epoch

        base, _i, _m = _base(tmp_path)
        src, _i2, _m2 = _source(tmp_path)
        (src / "archive" / "artifact.json").unlink()
        res = inherit_epoch(str(base), str(src),
                            transform=Similarity.identity(), size=256)
        assert res.ok is False
        assert "档案" in res.message

    def test_history_is_not_rewritten(self, tmp_path):
        """继承只新增版本，旧版本必须原样保留（只读不可变同一条纪律）。"""
        from h3d.archive import Archive
        from h3d.archive.inherit import Similarity
        from h3d.archive.inherit_epoch import inherit_epoch

        base, _i, _m = _base(tmp_path)
        src, _i2, _m2 = _source(tmp_path)
        before = [a.version for a in Archive.open(str(src)).assets("E1")]
        res = inherit_epoch(str(base), str(src),
                            transform=Similarity.identity(), size=256)
        assert res.ok
        after = [a.version for a in Archive.open(str(src)).assets("E1")]
        assert after[:len(before)] == before
        assert len(after) == len(before) + 1
