"""tests.test_gui_preview_load : 预览读模型的容错（2026-10-26 真机事故）。

事故现场
--------
用户把项目放在 ``E:\\兵马俑照片\\1.1.10``（**中文路径**）。管线跑完、
交付物齐全、报表正常，但界面预览区只有一个空坐标框。日志停在
``● 完成：total=1 done=1 failed=0 skipped=0`` —— 之后什么都没打印。

根因链
------
1. ``MeshPreview.load_mesh`` 直接 ``o3d.io.read_triangle_mesh(交付成果/…/x.obj)``；
   OBJ 在 Open3D 0.19 里走 ASSIMP，路径按 ANSI（GBK）传进 C++ 再当 UTF-8 解码，
   于是 ``UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb1 in position 40``
   （``0xb1`` 是「兵」的 GBK 首字节）。同目录的 ``.ply`` 走原生 reader，正常。
2. 该调用发生在 Qt 槽 ``_on_finished`` 内。PySide6 不会把槽里的异常抛给调用方，
   只往 stderr 打一行；windowed 打包版 ``sys.stderr is None`` → **彻底静默**。
3. 于是槽内异常之后的一切都不执行：结果条不出现、导出按钮不恢复、X1 按钮不启用。

所以这一组用例锁两件事：**读模型不许抛**、**预览失败也不许连累后续界面更新**。
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("open3d")


def _write_obj(dirpath: str, stem: str = "relic_001") -> str:
    """写一个最小 OBJ + MTL + 贴图；贴图用真 PNG（否则 Open3D 会刷警告）。"""
    os.makedirs(dirpath, exist_ok=True)
    obj = os.path.join(dirpath, f"{stem}.obj")
    with open(obj, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"# H3D textured mesh ({stem})\nmtllib {stem}.mtl\no {stem}\n")
        for v in ((0, 0, 0), (1, 0, 0), (0, 1, 0)):
            fh.write("v %d %d %d\n" % v)
        fh.write("vt 0 0\nvt 1 0\nvt 0 1\n")
        fh.write("usemtl m\nf 1/1 2/2 3/3\n")
    with open(os.path.join(dirpath, f"{stem}.mtl"), "w",
              encoding="utf-8", newline="\n") as fh:
        fh.write(f"newmtl m\nKd 1 1 1\nmap_Kd {stem}_diffuse.png\n")
    png = os.path.join(dirpath, f"{stem}_diffuse.png")
    try:
        from PIL import Image
        Image.new("RGB", (2, 2), (200, 180, 160)).save(png)
    except Exception:  # noqa: BLE001 - 没有 PIL 就退化成合法 PNG 头
        with open(png, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return obj


class TestReadMeshNonAsciiPath:
    """中文路径下的 OBJ：直读必炸，必须靠 ASCII 副本回退。"""

    def test_direct_read_is_the_bug(self, tmp_path):
        """先钉住底层事实：直读会抛 UnicodeDecodeError（否则本组用例没意义）。"""
        import open3d as o3d
        obj = _write_obj(str(tmp_path / "兵马俑照片" / "1.1.10"))
        with pytest.raises(UnicodeDecodeError):
            o3d.io.read_triangle_mesh(obj)

    def test_read_mesh_falls_back(self, tmp_path):
        from h3d.gui.meshload import read_mesh
        obj = _write_obj(str(tmp_path / "兵马俑照片" / "1.1.10"))
        mesh = read_mesh(obj)
        assert mesh is not None, "中文路径读不到模型 = 预览必空白"
        assert len(mesh.triangles) == 1

    def test_preview_arrays_returns_geometry(self, tmp_path):
        from h3d.gui.meshload import preview_arrays
        obj = _write_obj(str(tmp_path / "中文目录"))
        verts, tris = preview_arrays(obj)
        assert verts.shape == (3, 3) and tris.shape == (1, 3)

    def test_ascii_copy_preserves_texture_bytes(self, tmp_path):
        """贴图必须**按字节**复制：当文本重写会把 PNG 头变成 EF BF BD。"""
        import hashlib
        from h3d.gui.meshload import ascii_copy
        d = str(tmp_path / "兵马俑照片")
        obj = _write_obj(d)
        alt = ascii_copy(obj)
        assert alt and not any(ord(c) > 127 for c in alt)
        src = os.path.join(d, "relic_001_diffuse.png")
        dst = os.path.join(os.path.dirname(alt), "relic_001_diffuse.png")
        digest = lambda p: hashlib.sha1(open(p, "rb").read()).hexdigest()
        assert digest(src) == digest(dst)

    def test_ascii_copy_is_cached(self, tmp_path):
        from h3d.gui.meshload import ascii_copy
        obj = _write_obj(str(tmp_path / "中文"))
        assert ascii_copy(obj) == ascii_copy(obj)


class TestReadMeshFailures:
    def test_missing_file(self):
        from h3d.gui.meshload import read_mesh
        assert read_mesh(os.path.join("no_such_dir", "a.obj")) is None

    def test_corrupt_file(self, tmp_path):
        from h3d.gui.meshload import read_mesh
        p = tmp_path / "bad.ply"
        p.write_bytes(b"ply\nnot really a ply\n\x00\x01\x02")
        assert read_mesh(str(p)) is None

    def test_point_cloud_missing(self):
        from h3d.gui.meshload import read_point_cloud
        assert read_point_cloud("no_such.ply") is None


class TestPreviewWidgetNeverRaises:
    def test_corrupt_model_returns_false(self, tmp_path, _qapp_session):
        from h3d.gui.preview import MeshPreview
        p = tmp_path / "bad.ply"
        p.write_bytes(b"garbage" * 10)
        w = MeshPreview()
        try:
            assert w.load_mesh(str(p)) is False
        finally:
            w.deleteLater()

    def test_unicode_obj_loads(self, tmp_path, _qapp_session):
        from h3d.gui.preview import MeshPreview
        obj = _write_obj(str(tmp_path / "兵马俑照片"))
        w = MeshPreview()
        try:
            assert w.load_mesh(obj) is True
        finally:
            w.deleteLater()


class TestPostRunUiSurvivesBadPreview:
    """预览坏掉时，运行结束后的界面更新必须照常完成。"""

    def _win(self, tmp_path, item="relic_001"):
        from h3d.gui.app import MainWindow
        from h3d.gui.config_edit import ProjectConfig
        cfg = ProjectConfig()
        cfg.name = item
        cfg.root_dir = str(tmp_path)
        return MainWindow(cfg)

    def test_result_bar_still_appears(self, tmp_path, _qapp_session):
        """交付物存在但读不出来（损坏）→ 结果条仍要出现，不能静默崩在槽里。"""
        from h3d.core import paths
        lay = paths.ensure_layout(str(tmp_path))
        ddir = paths.delivery_item_dir(str(tmp_path), "relic_001")
        os.makedirs(ddir, exist_ok=True)
        (tmp_path / "阶段成果" / "00_raw" / "relic_001").mkdir(parents=True,
                                                             exist_ok=True)
        with open(os.path.join(ddir, "relic_001.ply"), "wb") as fh:
            fh.write(b"not a ply at all")
        w = self._win(tmp_path, item="relic_001")
        try:
            w._on_finished(True, "完成：total=1 done=1 failed=0 skipped=0")
            assert w.result_bar.isHidden() is False
            assert "生成完成" in w.lbl_result.text()
            assert w.btn_open3d.isEnabled() is False
            assert any("预览跳过" in ln for ln in
                       w.log.toPlainText().splitlines())
        finally:
            w.close()

    def test_good_model_is_shown_on_open(self, tmp_path, _qapp_session):
        """打开已完成的项目就要有预览（旧实现只在跑完后才载入）。"""
        import open3d as o3d
        import numpy as np
        from h3d.core import paths
        lay = paths.ensure_layout(str(tmp_path))
        ddir = paths.delivery_item_dir(str(tmp_path), "relic_001")
        os.makedirs(ddir, exist_ok=True)
        mesh = o3d.geometry.TriangleMesh.create_box(0.1, 0.1, 0.2)
        o3d.io.write_triangle_mesh(os.path.join(ddir, "relic_001.ply"), mesh)
        w = self._win(tmp_path, item="relic_001")
        try:
            assert w._preview_path.endswith("relic_001.ply")
            assert w.btn_open3d.isEnabled() is True
        finally:
            w.close()
