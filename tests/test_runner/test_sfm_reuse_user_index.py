"""tests.test_runner.test_sfm_reuse_user_index : S1 复用索引提升到用户级（2026-10-26）。

背景
----
索引原先放在 ``<项目根>/99_runs/s1_reuse_index.json``，只在同一个项目内共享。
``E:\\兵马俑照片`` 本身就是「项目根套项目根」的结构（``最终测试``、
``测试测试111`` 各是一个项目），同一批照片在两个项目里各解了 30 分钟 SfM。

现在索引提升到用户级（``H3D_HOME`` / ``~/.h3d/s1_reuse_index.json``），
条目里记下 ``root``，跨项目也能命中。

本组测试锁定的行为
------------------
1. A 项目解算过的照片，B 项目直接复用（跨项目），且留下 ``cross_project`` 痕迹；
2. 索引落在用户级目录，**不再**依赖项目内位置（项目级只作兼容镜像与迁移）；
3. 老索引（无 ``root`` 字段）仍能读，缺省认为属于它所在的项目；
4. 捐赠者文件被删 → 不复用（不得崩、不得复制不存在的文件）；
5. 参数不一致 → 不复用（跨项目更容易撞上这条）。
"""
from __future__ import annotations

import json
import os
import types

from h3d.sfm_solve import (_INDEX_NAME, _load_reuse_index,
                           _save_reuse_index, _sfm_params_key,
                           photo_set_fingerprint, reuse_sfm_result,
                           user_index_dir)


def _cfg(**over):
    s = dict(reuse_identical_photos=True, force_redense=False, dense=True,
             max_image_size=0, dense_max_image_size=1600,
             dense_geom_consistency=True, dense_gate=True,
             dense_gate_std=2.0, dense_gate_density=0.01,
             dense_min_pixels=2, dense_max_pixels=20)
    s.update(over)
    return types.SimpleNamespace(sfm_solve=types.SimpleNamespace(**s))


def _task(root, item):
    return types.SimpleNamespace(root=str(root), item_id=item)


def _photos(root, item, n=3, tag=b"same"):
    d = root / "00_photo" / item
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"DSC_{i:04d}.JPG").write_bytes(tag + bytes([i]))
    return d


def _outputs(root, item, n_images=3):
    dense = root / "10_dense" / item
    dense.mkdir(parents=True, exist_ok=True)
    (dense / "fused_dense.ply").write_bytes(b"ply\nfake\n")
    meta = root / "90_meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / f"{item}_capture_meta.json").write_text(
        json.dumps({"n_cameras": 1, "n_images": n_images, "images": []}),
        encoding="utf-8")


class TestUserLevelIndexLocation:
    def test_index_written_under_h3d_home(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setenv("H3D_HOME", str(home))
        proj = tmp_path / "proj"
        proj.mkdir()
        _save_reuse_index(str(proj), {"entries": {"abc": {"item": "x"}}})
        assert (home / _INDEX_NAME).is_file()
        data = json.loads((home / _INDEX_NAME).read_text("utf-8"))
        assert "abc" in data["entries"]

    def test_project_level_mirror_kept_for_old_exe(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _save_reuse_index(str(proj), {"entries": {"abc": {"item": "x"}}})
        legacy = proj / "99_runs" / _INDEX_NAME
        assert legacy.is_file(), "旧版 exe 只认项目级索引，必须留镜像"

    def test_user_index_dir_respects_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("H3D_HOME", str(tmp_path / "custom"))
        assert user_index_dir() == str(tmp_path / "custom")

    def test_corrupt_index_falls_back_to_empty(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        (home / _INDEX_NAME).write_text("{not json", "utf-8")
        idx = _load_reuse_index(str(tmp_path))
        assert idx["entries"] == {}


class TestCrossProjectReuse:
    def _seed(self, donor_root, item="donor", tag=b"same"):
        _photos(donor_root, item, 3, tag)
        _outputs(donor_root, item)
        fp = photo_set_fingerprint(str(donor_root / "00_photo" / item))
        return fp

    def test_reuse_across_two_projects(self, tmp_path):
        proj_a = tmp_path / "proj_a"
        proj_b = tmp_path / "proj_b"
        proj_a.mkdir()
        proj_b.mkdir()
        fp = self._seed(proj_a)
        # 登记：模拟 A 项目正常解算完成
        _save_reuse_index(str(proj_a), {"entries": {fp: {
            "item": "donor", "params": _sfm_params_key(_cfg()),
            "root": str(proj_a), "ts": 1.0}}})

        _photos(proj_b, "new", 3, b"same")     # B 项目同一批照片
        got = reuse_sfm_result(_task(proj_b, "new"), _cfg(),
                               str(proj_b / "00_photo" / "new"))
        assert got is not None, "跨项目必须命中"
        assert got["metrics"]["s1_reuse"]["from"] == "donor"
        assert got["metrics"]["s1_reuse"]["cross_project"] is True
        assert got["metrics"]["s1_reuse"]["from_root"] == str(proj_a)
        assert (proj_b / "10_dense" / "new" / "fused_dense.ply").is_file()
        assert (proj_b / "90_meta" / "new_capture_meta.json").is_file()

    def test_same_project_is_not_flagged_cross(self, tmp_path):
        fp = self._seed(tmp_path)
        _save_reuse_index(str(tmp_path), {"entries": {fp: {
            "item": "donor", "params": _sfm_params_key(_cfg()),
            "root": str(tmp_path), "ts": 1.0}}})
        _photos(tmp_path, "new", 3, b"same")
        got = reuse_sfm_result(_task(tmp_path, "new"), _cfg(),
                               str(tmp_path / "00_photo" / "new"))
        assert got is not None
        assert got["metrics"]["s1_reuse"]["cross_project"] is False

    def test_params_mismatch_blocks_cross_reuse(self, tmp_path):
        proj_a = tmp_path / "a"
        proj_b = tmp_path / "b"
        proj_a.mkdir()
        proj_b.mkdir()
        fp = self._seed(proj_a)
        other = _cfg(dense_max_image_size=2400)   # 稠密参数不同
        _save_reuse_index(str(proj_a), {"entries": {fp: {
            "item": "donor", "params": _sfm_params_key(other),
            "root": str(proj_a), "ts": 1.0}}})
        _photos(proj_b, "new", 3, b"same")
        assert reuse_sfm_result(_task(proj_b, "new"), _cfg(),
                                str(proj_b / "00_photo" / "new")) is None

    def test_stale_donor_root_is_not_reused(self, tmp_path):
        """索引里记的项目已被删/移动：不得复制不存在的文件。"""
        proj_b = tmp_path / "b"
        proj_b.mkdir()
        gone = tmp_path / "gone"
        _photos(gone, "donor", 3, b"same")
        fp = photo_set_fingerprint(str(gone / "00_photo" / "donor"))
        _save_reuse_index(str(proj_b), {"entries": {fp: {
            "item": "donor", "params": _sfm_params_key(_cfg()),
            "root": str(gone), "ts": 1.0}}})   # 但**没有** 10_dense/90_meta
        _photos(proj_b, "new", 3, b"same")
        assert reuse_sfm_result(_task(proj_b, "new"), _cfg(),
                                str(proj_b / "00_photo" / "new")) is None


class TestLegacyMigration:
    def test_old_entry_without_root_is_read(self, tmp_path):
        """v1 索引条目没有 root 字段：认定属于它所在的项目。"""
        _photos(tmp_path, "donor", 3, b"x")
        _outputs(tmp_path, "donor")
        fp = photo_set_fingerprint(str(tmp_path / "00_photo" / "donor"))
        legacy = tmp_path / "99_runs" / "s1_reuse_index.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({"version": 1, "entries": {fp: {
            "item": "donor", "params": _sfm_params_key(_cfg())}}}),
            encoding="utf-8")
        idx = _load_reuse_index(str(tmp_path))
        assert idx["entries"][fp]["root"] == os.path.abspath(str(tmp_path))

        _photos(tmp_path, "new", 3, b"x")
        got = reuse_sfm_result(_task(tmp_path, "new"), _cfg(),
                               str(tmp_path / "00_photo" / "new"))
        assert got is not None


class TestIndexBounded:
    def test_entries_are_trimmed(self, tmp_path, monkeypatch):
        import h3d.sfm_solve as S
        monkeypatch.setattr(S, "_INDEX_MAX_ENTRIES", 3)
        entries = {f"fp{i}": {"item": f"i{i}", "ts": float(i)} for i in range(6)}
        _save_reuse_index(str(tmp_path), {"entries": entries})
        idx = _load_reuse_index(str(tmp_path))
        assert len(idx["entries"]) <= 3
        # 保留的必须是最新的三条
        assert set(idx["entries"]) == {"fp3", "fp4", "fp5"}
