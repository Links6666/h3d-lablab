"""tests.test_runner.test_dense_budget : 稠密阶段超时预算与断点续算回归。

实机背景（2026-10-17）
----------------------
``run_dense_stereo`` 给 ``patch_match_stereo`` 写死 ``timeout=3600``，而该命令
在 ``geom_consistency=true`` 时**跑两遍**（photometric + geometric）：

    第一遍 21:47:37 → 22:31:12（30 张，2615 s，87 s/张）
    第二遍 22:33:08 → 22:47:38 被杀（仅 6 张，145 s/张）
    subprocess.TimeoutExpired: ... timed out after 3600 seconds

实测需求约 6815 s → 必然腰斩；且已经算完的 30 张第一遍深度图，下一次运行
又被「无条件清 stereo」删掉，43 分钟计算永久作废。用户等 1.5 小时得到 0 产出。

本测试锁五条本质行为：
1. 超时预算按「张数 × 像素数 × 遍数」估算，不再是固定 3600；
2. 位姿未变时**保留**深度图（断点续算），位姿变化才清；
3. ``force_redense`` 强制清空；
4. 超时必须给出「算到第几张 / 重跑会续算 / 怎么加速」的可诊断信息；
5. 稠密分辨率与几何一致性开关真正进入 COLMAP 命令行。
"""
from __future__ import annotations

import os
import subprocess

import pytest

from h3d.sfm_solve import (
    SfmError,
    _pose_fingerprint,
    dense_timeout_budget,
    run_dense_stereo,
)


def _touch(path: str, data: bytes = b"x") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def _make_model(ws: str) -> str:
    """造一个最小 sparse/0 模型（只需三个文件存在且有尺寸/时间）。"""
    model = os.path.join(ws, "sparse", "0")
    for name in ("cameras.bin", "images.bin", "points3D.bin"):
        _touch(os.path.join(model, name), b"1234")
    return model


class FakeColmap:
    """记录每次调用的命令行，并按需模拟失败/超时。"""

    def __init__(self, raise_timeout: bool = False):
        self.calls: list[list[str]] = []
        self.raise_timeout = raise_timeout

    def __call__(self, cmd, cwd=None, timeout=None, **kw):
        self.calls.append(list(cmd))
        if self.raise_timeout and "patch_match_stereo" in cmd:
            raise subprocess.TimeoutExpired(cmd, timeout or 1,
                                            output="Processing view 6 / 30")
        if "stereo_fusion" in cmd:
            out = cmd[cmd.index("--output_path") + 1]
            _touch(out)
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    def find(self, name: str) -> list[str]:
        for c in self.calls:
            if name in c:
                return c
        return []


class TestBudget:

    def test_scales_with_views_and_megapixels(self):
        """30 张 3.84 MP 双遍：30 × 3.84 × 45 × 2 ≈ 10368 s（2.9 h）。"""
        assert dense_timeout_budget(30, 3.84) == pytest.approx(10368, rel=0.02)

    def test_single_pass_halves_budget(self):
        two = dense_timeout_budget(30, 3.84, passes=2)
        one = dense_timeout_budget(30, 3.84, passes=1)
        assert one * 2 == pytest.approx(two, rel=0.01)

    def test_has_floor_for_tiny_datasets(self):
        """1 张 0.3 MP 也不得给出几百秒这种一启动就超时的预算。"""
        assert dense_timeout_budget(1, 0.3, passes=1) >= 1800

    def test_lower_resolution_budget_is_much_smaller(self):
        hi = dense_timeout_budget(30, 3.84)
        lo = dense_timeout_budget(30, 1.71)
        assert lo < hi * 0.5


class TestPoseFingerprint:

    def test_stable_for_unchanged_model(self, tmp_path):
        ws = str(tmp_path / "ws")
        model = _make_model(ws)
        assert _pose_fingerprint(model) == _pose_fingerprint(model)

    def test_changes_when_model_rewritten(self, tmp_path):
        """稀疏重建后位姿变了 -> 指纹必须变（否则旧深度图会被复用）。"""
        ws = str(tmp_path / "ws")
        model = _make_model(ws)
        before = _pose_fingerprint(model)
        _touch(os.path.join(model, "images.bin"), b"different-content-and-size")
        os.utime(os.path.join(model, "images.bin"), (1e9, 1e9))
        assert _pose_fingerprint(model) != before

    def test_empty_when_model_missing(self, tmp_path):
        assert _pose_fingerprint(str(tmp_path / "nope")) == ""


class TestDepthMapReuse:

    def _seed_depth_maps(self, ws: str) -> str:
        dm = os.path.join(ws, "dense", "stereo", "depth_maps")
        _touch(os.path.join(dm, "a.JPG.photometric.bin"))
        _touch(os.path.join(dm, "a.JPG.geometric.bin"))
        return dm

    def test_keeps_depth_maps_when_pose_unchanged(self, tmp_path, monkeypatch):
        """超时/崩溃后重跑：位姿没变就必须复用，不能从第 1 张重来。"""
        ws = str(tmp_path / "ws")
        model = _make_model(ws)
        dm = self._seed_depth_maps(ws)
        # 首次运行会写指纹；这里先写一份与当前位姿一致的指纹
        _touch(os.path.join(ws, "dense", ".h3d_pose_fp"), b"")
        with open(os.path.join(ws, "dense", ".h3d_pose_fp"), "w",
                  encoding="utf-8") as fh:
            fh.write(_pose_fingerprint(model))
        photos = tmp_path / "photos"
        photos.mkdir()

        seen = {}
        fake = FakeColmap()

        def run(cmd, cwd=None, timeout=None, **kw):
            if "patch_match_stereo" in cmd:
                seen["depth"] = sorted(os.listdir(dm))
            return fake(cmd, cwd=cwd, timeout=timeout, **kw)

        monkeypatch.setattr("h3d.sfm_solve.run_external", run)
        run_dense_stereo(str(photos), model, ws, "colmap")

        assert seen["depth"] == ["a.JPG.geometric.bin", "a.JPG.photometric.bin"], \
            "位姿未变时深度图必须保留（断点续算）"

    def test_clears_depth_maps_when_pose_changed(self, tmp_path, monkeypatch):
        """位姿变了还复用旧深度图 = 静默错误点云（r4.1 踩过），必须清。"""
        ws = str(tmp_path / "ws")
        model = _make_model(ws)
        dm = self._seed_depth_maps(ws)
        with open(os.path.join(ws, "dense", ".h3d_pose_fp"), "w",
                  encoding="utf-8") as fh:
            fh.write("stale-fingerprint")
        photos = tmp_path / "photos"
        photos.mkdir()

        seen = {}
        fake = FakeColmap()

        def run(cmd, cwd=None, timeout=None, **kw):
            if "patch_match_stereo" in cmd:
                seen["depth"] = sorted(os.listdir(dm)) if os.path.isdir(dm) else []
            return fake(cmd, cwd=cwd, timeout=timeout, **kw)

        monkeypatch.setattr("h3d.sfm_solve.run_external", run)
        run_dense_stereo(str(photos), model, ws, "colmap")

        assert seen.get("depth") == [], "位姿变化后旧深度图必须清空"

    def test_force_redense_clears_even_if_pose_same(self, tmp_path, monkeypatch):
        ws = str(tmp_path / "ws")
        model = _make_model(ws)
        dm = self._seed_depth_maps(ws)
        with open(os.path.join(ws, "dense", ".h3d_pose_fp"), "w",
                  encoding="utf-8") as fh:
            fh.write(_pose_fingerprint(model))
        photos = tmp_path / "photos"
        photos.mkdir()

        seen = {}
        fake = FakeColmap()

        def run(cmd, cwd=None, timeout=None, **kw):
            if "patch_match_stereo" in cmd:
                seen["depth"] = sorted(os.listdir(dm)) if os.path.isdir(dm) else []
            return fake(cmd, cwd=cwd, timeout=timeout, **kw)

        monkeypatch.setattr("h3d.sfm_solve.run_external", run)
        run_dense_stereo(str(photos), model, ws, "colmap", force_clear=True)

        assert seen.get("depth") == []


class TestDiagnostics:

    def test_timeout_message_is_actionable(self, tmp_path, monkeypatch):
        """超时信息必须回答：算到第几张、能不能续、怎么加速。"""
        ws = str(tmp_path / "ws")
        model = _make_model(ws)
        photos = tmp_path / "photos"
        photos.mkdir()
        _touch(os.path.join(ws, "dense", "stereo", "depth_maps",
                            "a.JPG.photometric.bin"))

        monkeypatch.setattr("h3d.sfm_solve.run_external", FakeColmap(True))
        with pytest.raises(SfmError) as ei:
            run_dense_stereo(str(photos), model, ws, "colmap", timeout_s=60)
        msg = str(ei.value)
        assert "断点续算" in msg
        assert "dense_max_image_size" in msg
        assert "photometric" in msg

    def test_timeout_writes_step_log(self, tmp_path, monkeypatch):
        """超时也要留下本轮日志：原实现不落盘，事后只能读到上一轮的旧报错，
        于是把「稠密超时」误读成「需要 CUDA GPU」。"""
        ws = str(tmp_path / "ws")
        model = _make_model(ws)
        photos = tmp_path / "photos"
        photos.mkdir()
        monkeypatch.setattr("h3d.sfm_solve.run_external", FakeColmap(True))
        with pytest.raises(SfmError):
            run_dense_stereo(str(photos), model, ws, "colmap", timeout_s=60)
        body = open(os.path.join(ws, "logs", "colmap_step12.log"),
                    encoding="utf-8").read()
        assert "EXIT CODE: TIMEOUT" in body
        assert "patch_match_stereo" in body


class TestCommandLine:

    def test_dense_resolution_passed_to_patch_match(self, tmp_path, monkeypatch):
        ws = str(tmp_path / "ws")
        model = _make_model(ws)
        photos = tmp_path / "photos"
        photos.mkdir()
        fake = FakeColmap()
        monkeypatch.setattr("h3d.sfm_solve.run_external", fake)
        run_dense_stereo(str(photos), model, ws, "colmap", max_image_size=1600)
        cmd = fake.find("patch_match_stereo")
        assert "--PatchMatchStereo.max_image_size" in cmd
        assert cmd[cmd.index("--PatchMatchStereo.max_image_size") + 1] == "1600"

    def test_geom_consistency_false_switches_fusion_input(self, tmp_path, monkeypatch):
        """关掉第二遍后 fusion 必须改吃 photometric，否则找不到 geometric.bin。"""
        ws = str(tmp_path / "ws")
        model = _make_model(ws)
        photos = tmp_path / "photos"
        photos.mkdir()
        fake = FakeColmap()
        monkeypatch.setattr("h3d.sfm_solve.run_external", fake)
        run_dense_stereo(str(photos), model, ws, "colmap",
                         geom_consistency=False)
        pm = fake.find("patch_match_stereo")
        assert pm[pm.index("--PatchMatchStereo.geom_consistency") + 1] == "false"
        fu = fake.find("stereo_fusion")
        assert fu[fu.index("--input_type") + 1] == "photometric"
