"""纯照片模式（场景②）任务发现与图像识别的回归测试。

覆盖 2026-10-17 实机缺陷：
    选择「纯照片三维重建」后，``00_photo/<item>/*.jpg`` 完全不被识别，
    管线任务数为 0（此前 ``discover_items`` 只扫描 ``00_raw`` 的点云）。

断言锁行为本质（不是"不报错"）：
    - 纯照片条目必须产出 Task，且 raw_paths 空、photo_paths 非空；
    - S1 未开启时不得凭空产出无几何任务；
    - 双源条目两类路径都要保留；
    - 未经改动的"仅激光"布局行为必须与历史一致（零回归）。
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from h3d.core.media import IMAGE_EXTS, count_images, is_image_file, list_images
from h3d.runner.batch_processor import discover_items
from h3d.runner.task import Task


# ---------------------------------------------------------------------------
# 图像扩展名统一口径
# ---------------------------------------------------------------------------
class TestImageExtDetection:
    @pytest.mark.parametrize("name", [
        "a.jpg", "A.JPG", "b.jpeg", "c.jfif", "d.jpe",
        "e.tif", "f.TIFF", "g.png", "h.JpEg",
    ])
    def test_accepts_all_jpeg_and_tiff_variants(self, name):
        assert is_image_file(name) is True

    @pytest.mark.parametrize("name", [
        "a.txt", "b.heic", "c.cr2", "d.mp4", "noext", "e.jpg.txt",
    ])
    def test_rejects_non_images(self, name):
        assert is_image_file(name) is False

    def test_exts_are_lowercase_tuple(self):
        assert all(e == e.lower() and e.startswith(".") for e in IMAGE_EXTS)


class TestListImages:
    def test_lists_only_images_sorted(self, tmp_path):
        for n in ["b.jpg", "A.JPG", "c.tif", "note.txt"]:
            (tmp_path / n).write_bytes(b"x")
        names = [os.path.basename(p) for p in list_images(str(tmp_path))]
        assert names == ["A.JPG", "b.jpg", "c.tif"]
        assert count_images(str(tmp_path)) == 3

    def test_missing_dir_is_empty_not_error(self, tmp_path):
        assert list_images(str(tmp_path / "nope")) == []

    def test_recursive(self, tmp_path):
        sub = tmp_path / "batch01"
        sub.mkdir()
        (sub / "x.jpg").write_bytes(b"x")
        (tmp_path / "y.jpg").write_bytes(b"y")
        assert count_images(str(tmp_path), recursive=True) == 2


# ---------------------------------------------------------------------------
# 场景②：仅照片条目必须进入任务队列
# ---------------------------------------------------------------------------
def _mk_photo_item(root, item="relic_001", names=("a.jpg", "b.JPG", "c.jpeg")):
    d = root / "00_photo" / item
    d.mkdir(parents=True)
    for n in names:
        (d / n).write_bytes(b"not-a-real-jpeg-but-ext-checked")
    return d


class TestDiscoverPhotoOnly:
    def test_photo_only_creates_task(self, tmp_path):
        _mk_photo_item(tmp_path)
        (tmp_path / "00_raw").mkdir()
        (tmp_path / "00_raw" / "relic_001").mkdir()

        tasks = discover_items(
            str(tmp_path / "00_raw"), ["ply"],
            photo_dir=str(tmp_path / "00_photo"),
            allow_photo_only=True)

        assert len(tasks) == 1, "纯照片条目未被识别为任务（回归）"
        t = tasks[0]
        assert t.item_id == "relic_001"
        assert t.raw_paths == []
        assert len(t.photo_paths) == 3
        assert {os.path.basename(p) for p in t.photo_paths} == {
            "a.jpg", "b.JPG", "c.jpeg"}

    def test_photo_only_blocked_when_sfm_disabled(self, tmp_path):
        _mk_photo_item(tmp_path)
        (tmp_path / "00_raw").mkdir()
        tasks = discover_items(
            str(tmp_path / "00_raw"), ["ply"],
            photo_dir=str(tmp_path / "00_photo"),
            allow_photo_only=False)
        assert tasks == [], "S1 关闭时不应产出无几何任务"

    def test_dual_source_keeps_both(self, tmp_path):
        _mk_photo_item(tmp_path)
        raw = tmp_path / "00_raw" / "relic_001"
        raw.mkdir(parents=True)
        (raw / "scan.ply").write_bytes(b"ply")

        tasks = discover_items(
            str(tmp_path / "00_raw"), ["ply"],
            photo_dir=str(tmp_path / "00_photo"),
            allow_photo_only=True)

        assert len(tasks) == 1
        assert len(tasks[0].raw_paths) == 1
        assert len(tasks[0].photo_paths) == 3

    def test_raw_only_behavior_unchanged(self, tmp_path):
        """零回归：只有激光的条目仍正常工作，且不扫照片目录。"""
        raw = tmp_path / "00_raw" / "relic_001"
        raw.mkdir(parents=True)
        (raw / "scan.ply").write_bytes(b"ply")

        tasks = discover_items(str(tmp_path / "00_raw"), ["ply"])
        assert len(tasks) == 1
        assert tasks[0].raw_paths and not tasks[0].photo_paths

    def test_raw_dir_missing_but_photos_present(self, tmp_path):
        """场景②首跑：00_raw 可能尚不存在（GUI 建目录失败/用户手动搬数据）。"""
        _mk_photo_item(tmp_path)
        tasks = discover_items(
            str(tmp_path / "00_raw"), ["ply"],
            photo_dir=str(tmp_path / "00_photo"),
            allow_photo_only=True)
        assert len(tasks) == 1
        assert tasks[0].photo_paths

    def test_flat_photo_layout(self, tmp_path):
        """用户把照片直接放在 00_photo/ 根下（未建 <item> 子目录）。"""
        photo = tmp_path / "00_photo"
        photo.mkdir()
        for n in ["1.jpg", "2.jpg"]:
            (photo / n).write_bytes(b"x")
        tasks = discover_items(
            str(tmp_path / "00_raw"), ["ply"],
            photo_dir=str(photo), allow_photo_only=True)
        assert len(tasks) == 1
        assert len(tasks[0].photo_paths) == 2

    def test_multiple_items_sorted(self, tmp_path):
        for item in ("b_item", "a_item"):
            _mk_photo_item(tmp_path, item=item, names=("1.jpg",))
        tasks = discover_items(
            str(tmp_path / "00_raw"), ["ply"],
            photo_dir=str(tmp_path / "00_photo"),
            allow_photo_only=True)
        assert [t.item_id for t in tasks] == ["a_item", "b_item"]


# ---------------------------------------------------------------------------
# S1 照片目录解析：平铺兜底
# ---------------------------------------------------------------------------
class TestPhotoDirFallback:
    def _cfg(self, photo_dir=""):
        return SimpleNamespace(sfm_solve=SimpleNamespace(photo_dir=photo_dir))

    def test_uses_item_subdir(self, tmp_path):
        from h3d.sfm_solve import _photo_dir_for
        d = tmp_path / "00_photo" / "relic"
        d.mkdir(parents=True)
        (d / "a.jpg").write_bytes(b"x")
        task = Task(item_id="relic", root=str(tmp_path))
        assert _photo_dir_for(task, self._cfg()) == str(d)

    def test_flat_fallback_when_item_dir_absent(self, tmp_path):
        from h3d.sfm_solve import _photo_dir_for
        photo = tmp_path / "00_photo"
        photo.mkdir()
        (photo / "a.jpg").write_bytes(b"x")
        task = Task(item_id="relic", root=str(tmp_path))
        assert _photo_dir_for(task, self._cfg()) == str(photo)

    def test_no_photos_returns_item_dir(self, tmp_path):
        from h3d.sfm_solve import _photo_dir_for
        (tmp_path / "00_photo").mkdir()
        task = Task(item_id="relic", root=str(tmp_path))
        assert _photo_dir_for(task, self._cfg()).endswith("relic")

    def test_explicit_photo_dir_wins(self, tmp_path):
        from h3d.sfm_solve import _photo_dir_for
        custom = tmp_path / "custom" / "relic"
        custom.mkdir(parents=True)
        (custom / "a.jpg").write_bytes(b"x")
        task = Task(item_id="relic", root=str(tmp_path))
        cfg = self._cfg(photo_dir=str(tmp_path / "custom"))
        assert _photo_dir_for(task, cfg) == str(custom)
