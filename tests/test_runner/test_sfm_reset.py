"""tests.test_runner.test_sfm_reset : S1 数据库污染与失败可诊断性回归。

实机背景（2026-10-17）
----------------------
用户用 30 张兵马俑照片跑纯照片模式，S1 第一步失败：

    COLMAP 步骤 1 失败 (exit=3221226505):
    ... Camera: #2 - SIMPLE_RADIAL ...
    Could not create logging file: No such file or directory
    E... database_sqlite.cc:1220] SQLite error: constraint failed

同一批照片在**空库**上重跑则 30/30 全部成功（Camera #1），证明与照片、
中文路径、线程参数无关。根因是 ``run_colmap_sparse`` 复用上一次运行残留的
``database.db``：残留库处于半写入状态（cameras=3、images 分属 camera 1/3），
再次 feature_extractor 触发约束冲突 → COLMAP abort。

本测试锁三条本质行为：
1. 全量重建前必须清空 database.db（含 -wal/-shm）与 sparse 旧模型；
2. 照片副本 photos_small 不得被误删（清库不能把预处理成果一起清掉）；
3. 失败信息必须带完整日志路径与可执行诊断，而不是一段被截断的输出。
"""
from __future__ import annotations

import os
import subprocess

import pytest

from h3d.sfm_solve import (
    SfmError,
    _dump_step_log,
    _error_tail,
    _reset_sparse_state,
    run_colmap_sparse,
    run_dense_stereo,
)


def _touch(path: str, data: bytes = b"x") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


class TestResetSparseState:
    def test_removes_database_and_wal_shm(self, tmp_path):
        db = str(tmp_path / "database.db")
        for suffix in ("", "-wal", "-shm"):
            _touch(db + suffix)
        sparse = tmp_path / "sparse"
        sparse.mkdir()

        _reset_sparse_state(db, str(sparse))

        assert not os.path.exists(db)
        assert not os.path.exists(db + "-wal")
        assert not os.path.exists(db + "-shm")

    def test_removes_stale_models(self, tmp_path):
        """旧 sparse/0、sparse/0_txt 必须清掉，否则 _pick_largest_model 选到旧模型。"""
        sparse = tmp_path / "sparse"
        (sparse / "0").mkdir(parents=True)
        _touch(str(sparse / "0" / "images.bin"))
        (sparse / "0_txt").mkdir()
        _touch(str(sparse / "0_txt" / "images.txt"))

        _reset_sparse_state(str(tmp_path / "database.db"), str(sparse))

        assert list(sparse.iterdir()) == []

    def test_keeps_photo_cache(self, tmp_path):
        """photos_small 是预处理成果，清库不得波及（否则每次重跑都要重新缩放）。"""
        photos = tmp_path / "photos_small"
        photos.mkdir()
        _touch(str(photos / "DSC_0001.JPG"))

        _reset_sparse_state(str(tmp_path / "database.db"),
                            str(tmp_path / "sparse"))

        assert (photos / "DSC_0001.JPG").exists()

    def test_clears_stale_dense_depth_maps(self, tmp_path):
        """残留深度图必须随全量重建一起清掉（否则两套位姿混融，静默出错）。

        实机形态：30 张做完 30 个 photometric.bin、22 个 geometric.bin 时被中断。
        patch_match_stereo 会跳过已存在的 photometric.bin，只补 8 张几何一致
        性检查 —— 表面成功，实际融合了旧位姿的深度图。
        """
        dense = tmp_path / "dense"
        dm = dense / "stereo" / "depth_maps"
        dm.mkdir(parents=True)
        _touch(str(dm / "DSC_0001.JPG.photometric.bin"))
        _touch(str(dm / "DSC_0001.JPG.geometric.bin"))
        _touch(str(dense / "images" / "DSC_0001.JPG"))

        _reset_sparse_state(str(tmp_path / "database.db"),
                            str(tmp_path / "sparse"), str(dense))

        assert list(dense.iterdir()) == [], "dense 内容必须清空"
        assert dense.is_dir(), "dense 目录本身保留（下游按路径写入）"

    def test_omitting_dense_keeps_it(self, tmp_path):
        """增量场景不传 dense —— 不得顺手删掉正在复用的密集工作区。"""
        dense = tmp_path / "dense"
        _touch(str(dense / "fused.ply"))

        _reset_sparse_state(str(tmp_path / "database.db"),
                            str(tmp_path / "sparse"))

        assert (dense / "fused.ply").exists()

    def test_missing_files_is_noop(self, tmp_path):
        _reset_sparse_state(str(tmp_path / "nope.db"),
                            str(tmp_path / "no_sparse"))   # 不抛异常


class TestRunColmapSparseResets:
    def test_clears_old_db_before_feature_extraction(self, tmp_path, monkeypatch):
        """核心回归：即便旧库存在，feature_extractor 也必须从空库开始。"""
        ws = tmp_path / "ws"
        ws.mkdir()
        db = ws / "database.db"
        _touch(str(db))
        _touch(str(db) + "-wal")
        photos = tmp_path / "photos"
        photos.mkdir()
        _touch(str(photos / "a.jpg"))

        seen = {}

        def fake_run(cmd, cwd=None, timeout=None, **kw):
            if "feature_extractor" in cmd:
                # feature_extractor 启动瞬间，旧库必须已被删除
                seen["db_exists"] = os.path.exists(str(db))
            return subprocess.CompletedProcess(cmd, 0, "ok", "")

        monkeypatch.setattr("h3d.sfm_solve.run_external", fake_run)
        # mapper 后处理：伪造 sparse/0 供 _pick_largest_model / 转换
        monkeypatch.setattr("h3d.sfm_solve._pick_largest_model",
                            lambda _s: str(ws / "sparse" / "0"))
        monkeypatch.setattr("h3d.sfm_solve._normalize_model_slot",
                            lambda _s, _b: str(ws / "sparse" / "0"))
        (ws / "sparse" / "0").mkdir(parents=True)

        run_colmap_sparse(str(photos), str(ws), "colmap",
                          matcher="exhaustive", deterministic=True)

        assert seen["db_exists"] is False, "feature_extractor 前必须清空旧库"

    def test_failure_message_has_log_path_and_hint(self, tmp_path, monkeypatch):
        ws = tmp_path / "ws"
        photos = tmp_path / "photos"
        photos.mkdir()

        def fake_run(cmd, cwd=None, timeout=None, **kw):
            return subprocess.CompletedProcess(
                cmd, 3221226505,
                "I... feature_extraction.cc:282] Camera: #2\n"
                "E... database_sqlite.cc:1220] SQLite error: constraint failed\n",
                "")

        monkeypatch.setattr("h3d.sfm_solve.run_external", fake_run)

        with pytest.raises(SfmError) as ei:
            run_colmap_sparse(str(photos), str(ws), "colmap")

        msg = str(ei.value)
        assert "colmap_step1.log" in msg, "失败信息必须给出完整日志路径"
        assert "数据库约束冲突" in msg, "必须给出可执行诊断"
        # 日志文件真实落盘
        log = ws / "logs" / "colmap_step1.log"
        assert log.exists()
        assert "constraint failed" in log.read_text(encoding="utf-8")


class TestDenseStereoResets:
    def test_drops_old_depth_maps_before_patch_match(self, tmp_path, monkeypatch):
        """run_dense_stereo 是「按当前 sparse 重建 dense」的语义：旧深度图必须弃用。

        即便上层没清 dense（例如被独立调用），也不允许复用旧 photometric.bin。
        """
        ws = tmp_path / "ws"
        dm = ws / "dense" / "stereo" / "depth_maps"
        dm.mkdir(parents=True)
        _touch(str(dm / "a.JPG.photometric.bin"))
        photos = tmp_path / "photos"
        photos.mkdir()

        seen = {}

        def fake_run(cmd, cwd=None, timeout=None, **kw):
            if "patch_match_stereo" in cmd:
                seen["stale"] = os.listdir(str(dm)) if os.path.isdir(str(dm)) else []
            if "stereo_fusion" in cmd:
                _touch(str(ws / "dense" / "fused.ply"))
            return subprocess.CompletedProcess(cmd, 0, "ok", "")

        monkeypatch.setattr("h3d.sfm_solve.run_external", fake_run)
        out = run_dense_stereo(str(photos), str(ws / "sparse" / "0"),
                               str(ws), "colmap")

        assert seen["stale"] == [], "patch_match_stereo 前旧深度图必须已清除"
        assert out.endswith("fused.ply")


class TestLogHelpers:
    def test_error_tail_keeps_last_lines(self):
        text = "\n".join(f"line{i}" for i in range(40))
        tail = _error_tail(text)
        assert "line39" in tail
        assert "line0\n" not in tail

    def test_dump_step_log_writes_command_and_streams(self, tmp_path):
        proc = subprocess.CompletedProcess(["colmap"], 1, "OUT", "ERR")
        path = _dump_step_log(str(tmp_path), 2, ["colmap", "mapper"], proc)
        body = open(path, encoding="utf-8").read()
        assert "colmap mapper" in body
        assert "EXIT CODE: 1" in body
        assert "OUT" in body and "ERR" in body
