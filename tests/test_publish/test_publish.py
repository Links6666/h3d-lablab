"""tests.test_publish : M6 发布归档单元测试。"""
from __future__ import annotations

import json
import os

import numpy as np
import open3d as o3d
import pytest

from h3d.core import PublishConfig
from h3d.publish import (
    build_delivery_manifest,
    build_meta,
    copy_qc_report,
    export_delivery,
    write_meta,
)


def _make_tex_dir(tmp_path, item_id="rock001", formats=("ply", "obj", "glb")):
    """构造 50_tex/<item>/ 带色模型目录（含 L1 + L0.3）。"""
    tex = tmp_path / "50_tex" / item_id
    tex.mkdir(parents=True)
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=1.0, resolution=20)
    mesh.paint_uniform_color([0.8, 0.4, 0.2])
    for fmt in formats:
        o3d.io.write_triangle_mesh(str(tex / f"{item_id}_L1.{fmt}"), mesh)
    o3d.io.write_triangle_mesh(str(tex / f"{item_id}_L0.3.ply"), mesh)
    return tex


class TestExportDelivery:
    def test_exports_all_formats(self, tmp_path):
        _make_tex_dir(tmp_path)
        out = tmp_path / "80_export" / "rock001"
        files = export_delivery(str(tmp_path / "50_tex" / "rock001"),
                                str(out), "rock001", ["ply", "obj", "glb"])
        assert len(files) == 3
        for f in files:
            assert os.path.exists(f)
        # 标准命名（去 LOD 标签）
        names = {os.path.basename(f) for f in files}
        assert names == {"rock001.ply", "rock001.obj", "rock001.glb"}

    def test_fallback_lod_when_l1_missing(self, tmp_path):
        _make_tex_dir(tmp_path)
        tex = tmp_path / "50_tex" / "rock001"
        # 删除 L1.ply，只剩 L0.3.ply -> 回退
        (tex / "rock001_L1.ply").unlink()
        files = export_delivery(str(tex), str(tmp_path / "out"), "rock001", ["ply"])
        assert len(files) == 1
        assert os.path.basename(files[0]) == "rock001.ply"

    def test_skip_missing_format(self, tmp_path):
        _make_tex_dir(tmp_path, formats=("ply", "obj"))
        files = export_delivery(str(tmp_path / "50_tex" / "rock001"),
                                str(tmp_path / "out"), "rock001",
                                ["ply", "glb"])
        assert len(files) == 1  # glb 缺失跳过

    def test_copy_preserves_colors(self, tmp_path):
        _make_tex_dir(tmp_path)
        out = tmp_path / "80_export" / "rock001"
        files = export_delivery(str(tmp_path / "50_tex" / "rock001"),
                                str(out), "rock001", ["ply"])
        reloaded = o3d.io.read_triangle_mesh(files[0])
        assert reloaded.has_vertex_colors()


class TestMeta:
    def test_build_meta_snapshot(self):
        meta = build_meta(
            "rock001",
            {"M3": {"n_vertices": 100, "status": "done"},
             "M5": {"coverage": 0.9, "status": "done"}},
            {"name": "demo", "operator": ""},
            {"open3d": "0.19.0"}, "run-001", "hash123")
        assert meta["item_id"] == "rock001"
        assert meta["run_id"] == "run-001"
        assert meta["config_hash"] == "hash123"
        assert meta["stages"]["M5"]["coverage"] == 0.9
        assert meta["software"]["open3d"] == "0.19.0"

    def test_write_meta_roundtrip(self, tmp_path):
        meta = build_meta("rock001", {"M5": {"status": "done"}},
                          {}, {}, "r1", "h1")
        path = write_meta(meta, str(tmp_path / "90_meta"), "rock001")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        assert loaded["item_id"] == "rock001"
        assert loaded["stages"]["M5"]["status"] == "done"


class TestDeliveryManifest:
    def test_build_and_append(self, tmp_path):
        entries = [{"item_id": "rock001", "status": "published",
                    "models": ["a.ply"], "qc_status": "pass"}]
        path = build_delivery_manifest(entries,
                                       str(tmp_path / "delivery_manifest.json"))
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["n_items"] == 1
        assert payload["n_published"] == 1
        # 追加语义：重跑后合并（新条目 + 旧条目）
        entries2 = entries + [{"item_id": "rock002", "status": "published",
                               "models": ["b.ply"], "qc_status": "pass"}]
        build_delivery_manifest(entries2, str(tmp_path / "delivery_manifest.json"))
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["n_items"] == 2


class TestCopyQcReport:
    def test_copies_when_exists(self, tmp_path):
        rpt = tmp_path / "70_report" / "rock001"
        rpt.mkdir(parents=True)
        (rpt / "accuracy.csv").write_text("object,status\nrock001,pass\n",
                                          encoding="utf-8")
        out = tmp_path / "80_export" / "rock001"
        dst = copy_qc_report(str(tmp_path), str(out), "rock001")
        assert dst is not None
        assert os.path.exists(dst)

    def test_none_when_missing(self, tmp_path):
        assert copy_qc_report(str(tmp_path), str(tmp_path / "out"), "nope") is None


class TestPublishConfig:
    def test_defaults(self):
        cfg = PublishConfig()
        assert cfg.enabled is True
        assert cfg.formats == ["ply", "obj", "glb"]
        assert cfg.embed_meta is True
        assert cfg.copy_qc is True
