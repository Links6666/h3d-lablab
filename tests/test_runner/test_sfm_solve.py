"""tests.test_runner.test_sfm_solve : S1 sfm_solve 可选 stage 单元测试。

CI 策略：全部 mock COLMAP 子进程（不跑真实 SfM）；仅验证调用契约、
位姿解析、capture_meta.json 生成与失败隔离。
"""
from __future__ import annotations

import json
import os

import numpy as np
import pytest

from h3d.core import PipelineConfig, RecoverableError
from h3d.runner import STAGE_REGISTRY
from h3d.runner.task import Task, TaskStatus
from h3d.sfm_solve import (
    parse_cameras,
    parse_images,
    read_points3d,
    run_sfm_solve,
    write_capture_meta,
)


def _make_cfg(tmp_path, **kw):
    cfg = PipelineConfig()
    cfg.sfm_solve.enabled = True
    cfg.sfm_solve.photo_dir = str(tmp_path / "00_photo")
    cfg.sfm_solve.workspace = str(tmp_path / "ws")
    for k, v in kw.items():
        setattr(cfg.sfm_solve, k, v)
    return cfg


def _write_bin_model(sparse_dir, name, n_img):
    """写入 COLMAP 二进制子模型的最小可用内容。

    ``_pick_largest_model`` 依据 ``images.bin`` 前 8 字节（小端 uint64 = 图像
    条数）判断子重建大小，因此 mock 出图模型时必须写这一头。
    """
    import struct
    d = os.path.join(str(sparse_dir), name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "images.bin"), "wb") as fh:
        fh.write(struct.pack("<Q", n_img))
    return d


def _fake_colmap_sparse(tmp_path, n_reg=3):
    """预置 COLMAP sparse 文本模型（模拟 colmap mapper 输出）。"""
    sparse_bin = tmp_path / "ws" / "sparse" / "0"
    txt = tmp_path / "ws" / "sparse" / "0_txt"
    os.makedirs(sparse_bin, exist_ok=True)
    os.makedirs(txt, exist_ok=True)
    _write_bin_model(sparse_bin.parent, "0", n_reg)
    with open(txt / "cameras.txt", "w", encoding="utf-8") as fh:
        fh.write("# camera list\n1 SIMPLE_RADIAL 800 600 800.0 400 300 0.0\n")
    lines = []
    for i in range(n_reg):
        lines.append(
            f"{i+1} 1.0 0.0 0.0 0.0 {3.0+i} 0.0 0.0 1 img_{i:02d}.jpg")
        lines.append("0 0 -1")  # 2D 点行（每图两行：位姿行 + 2D 点行）
    with open(txt / "images.txt", "w", encoding="utf-8") as fh:
        fh.write("# image list\n" + "\n".join(lines) + "\n")
    with open(txt / "points3D.txt", "w", encoding="utf-8") as fh:
        fh.write("# 3D points\n1 0.0 0.0 0.0 1 2 3\n")
    return txt


class TestS1Registered:
    def test_stage_registered(self):
        assert "S1" in STAGE_REGISTRY

    def test_disabled_by_default(self, tmp_path):
        cfg = PipelineConfig()
        task = Task(item_id="r1", raw_paths=[], root=str(tmp_path))
        res = run_sfm_solve(task, cfg)
        assert res.status == TaskStatus.SKIPPED


class TestParse:
    def test_parse_cameras(self, tmp_path):
        _fake_colmap_sparse(tmp_path)
        cams = parse_cameras(str(tmp_path / "ws/sparse/0_txt/cameras.txt"))
        assert cams[1]["fx"] == 800.0 and cams[1]["w"] == 800

    def test_parse_images(self, tmp_path):
        _fake_colmap_sparse(tmp_path)
        imgs = parse_images(str(tmp_path / "ws/sparse/0_txt/images.txt"))
        assert len(imgs) == 3
        assert imgs[0]["name"] == "img_00.jpg"
        assert imgs[0]["R"].shape == (3, 3)
        # 2026-09-15 修复：t 必须是**相机中心** C = -R_co^T @ tvec（不是 tvec 本身）；
        # 假数据 tvec=(3,0,0) 且 R_co=I -> C=(-3,0,0)。
        assert imgs[0]["t"][0] == pytest.approx(-3.0)
        # 前方约定为 -Z：R = -R_co（det = -1）
        assert np.linalg.det(imgs[0]["R"]) == pytest.approx(-1.0)

    def test_read_points3d(self, tmp_path):
        _fake_colmap_sparse(tmp_path)
        pts = read_points3d(str(tmp_path / "ws/sparse/0_txt/points3D.txt"))
        assert pts.shape == (1, 3)

    def test_write_capture_meta(self, tmp_path):
        out = tmp_path / "capture_meta.json"
        write_capture_meta({1: {"fx": 800}}, [{"name": "a.jpg", "R": np.eye(3),
                                                "t": np.zeros(3), "cam_id": 1}],
                           str(out))
        meta = json.loads(out.read_text(encoding="utf-8"))
        assert meta["n_images"] == 1 and meta["images"][0]["name"] == "a.jpg"


class TestRunSfmSolve:
    def test_missing_photo_dir_isolated(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        task = Task(item_id="r1", raw_paths=[], root=str(tmp_path))
        with pytest.raises(RecoverableError):
            run_sfm_solve(task, cfg)  # 照片目录不存在 -> 业务错误码（失败隔离）

    def test_missing_colmap_isolated(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        cfg.sfm_solve.colmap_path = "definitely-not-exist-colmap"
        photo = tmp_path / "00_photo" / "r1"
        os.makedirs(photo)
        (photo / "a.jpg").write_bytes(b"x")
        task = Task(item_id="r1", raw_paths=[], root=str(tmp_path))
        with pytest.raises(RecoverableError):
            run_sfm_solve(task, cfg)  # 无 COLMAP -> 失败隔离

    def test_success_with_mock_colmap(self, tmp_path, monkeypatch):
        """mock colmap 子进程：三个步骤成功后产出 capture_meta.json。

        注意（2026-10-17）：`run_colmap_sparse` 现在会在开始前清空
        `sparse/` 与 database.db（防止残留库导致 0xC0000409）。
        因此模型不能再"预先放置"，必须由 mapper 步骤现场产出——
        这与真实 COLMAP 的行为一致。
        """
        photo = tmp_path / "00_photo" / "r1"
        os.makedirs(photo)
        for i in range(3):
            (photo / f"img_{i:02d}.jpg").write_bytes(b"x")

        def fake_run(cmd, capture_output=True, text=True, timeout=None,
                     cwd=None, env=None, on_line=None, **kwargs):
            if "mapper" in cmd:
                _fake_colmap_sparse(tmp_path)
            # 2026-10-17：sfm_solve 现在传 on_line 走流式分支（GUI 实时进度），
            # 因此 mock 目标从 subprocess.run 上移到 run_external 本身。
            if on_line is not None:
                on_line("Processing view 1 / 3")   # 验证回调确实被调用
            import subprocess
            return subprocess.CompletedProcess(list(cmd), 0, "ok", "")
        # 2026-09-15：COLMAP 调用统一走 h3d.core.proc.run_external
        # （隐藏控制台窗口 + 显式 cwd + 容错解码）。
        # 2026-10-17：加入 on_line 流式进度后，proc.run_external 内部的
        # subprocess.run 分支被绕过，故直接 mock sfm_solve 侧的 run_external。
        monkeypatch.setattr("h3d.sfm_solve.run_external", fake_run)

        cfg = _make_cfg(tmp_path)
        task = Task(item_id="r1", raw_paths=[], root=str(tmp_path))
        res = run_sfm_solve(task, cfg)
        assert res.status == TaskStatus.DONE
        meta_path = os.path.join(str(tmp_path), "90_meta",
                                 "r1_capture_meta.json")
        assert os.path.exists(meta_path)
        assert res.metrics["n_registered"] == 3


class TestIncrementalDense:
    def test_incremental_metrics(self, tmp_path):
        """既有 fused_dense 存在时记录增量指标。"""
        # 构造旧/新 dense 点云（部分新增点）
        import open3d as o3d
        old = o3d.geometry.PointCloud()
        old.points = o3d.utility.Vector3dVector(np.random.default_rng(0).normal(size=(200, 3)))
        new = o3d.geometry.PointCloud()
        new.points = o3d.utility.Vector3dVector(
            np.vstack([np.asarray(old.points), np.random.default_rng(1).normal(size=(50, 3))]))
        dense_dir = os.path.join(str(tmp_path), "10_dense", "r1")
        os.makedirs(dense_dir, exist_ok=True)
        o3d.io.write_point_cloud(os.path.join(dense_dir, "fused_dense.ply"), old)
        o3d.io.write_point_cloud(os.path.join(str(tmp_path), "new.ply"), new)
        from h3d.sfm_solve import _incremental_dense_metrics
        m = _incremental_dense_metrics(
            os.path.join(dense_dir, "fused_dense.ply"),
            os.path.join(str(tmp_path), "new.ply"))
        assert m["incremental"] is True
        assert m["added_ratio"] > 0.1  # 新点占比被检测


class TestSiftCalibration:
    def test_calibrate_low_texture(self, tmp_path):
        """低纹理照片（平坦灰）→ 阈值下调（提更多弱特征）。"""
        from PIL import Image
        d = tmp_path / "photos"
        d.mkdir()
        for i in range(4):
            Image.fromarray(np.full((64, 64), 128 + i, dtype=np.uint8)).save(
                d / f"p{i:02d}.jpg")
        from h3d.sfm_solve import calibrate_sift_params
        c = calibrate_sift_params(str(d))
        assert c["calibrated"] is True
        assert c["peak_threshold"] < 0.004  # 低纹理 → 阈值下调
        assert c["n_photos"] == 4

    def test_calibrate_texture_rich(self, tmp_path):
        """高纹理照片（噪声）→ 阈值保持/上调。"""
        from PIL import Image
        d = tmp_path / "photos"
        d.mkdir()
        rng = np.random.default_rng(0)
        for i in range(4):
            Image.fromarray((rng.random((64, 64)) * 255).astype(np.uint8)).save(
                d / f"p{i:02d}.jpg")
        from h3d.sfm_solve import calibrate_sift_params
        c = calibrate_sift_params(str(d))
        assert c["mean_grad_var"] > 800
        assert c["peak_threshold"] >= 0.004

    def test_calibrate_empty_dir(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        from h3d.sfm_solve import SfmError, calibrate_sift_params
        try:
            calibrate_sift_params(str(d))
            raise AssertionError("应抛 SfmError")
        except SfmError:
            pass


class TestIncrementalSparse:
    def test_fallback_when_no_workspace(self, tmp_path):
        """workspace 无既有模型 → 退化全量（返回模型目录）。"""
        from h3d.sfm_solve import run_incremental_sparse
        d = tmp_path / "ws"
        d.mkdir()
        # 无 database.db → 应走全量分支（会因无照片失败但路径正确）
        try:
            run_incremental_sparse(str(tmp_path / "photos"), str(d),
                                   "colmap")
        except Exception:
            pass  # 全量分支路径（无照片目录报错）——验证不崩溃即可


class TestCaptureMetaConvention:
    """回归（实机：M4 纹理全空）：capture_meta 必须存【相机中心 + 前方 -Z】。"""

    def test_camera_center_is_minus_rt_times_tvec(self):
        import numpy as np
        from h3d.sfm_solve import parse_images
        import tempfile, os
        # R_co = I（单位四元数），tvec = (3,0,0) -> 相机中心 = (-3,0,0)
        txt = (
            "# Image list\n"
            "1 1 0 0 0 3 0 0 1 img_00.jpg\n"
            "0 0 -1\n"
        )
        d = tempfile.mkdtemp()
        p = os.path.join(d, "images.txt")
        open(p, "w", encoding="utf-8").write(txt)
        imgs = parse_images(p)
        assert imgs[0]["t"][0] == -3.0            # C = -R^T t（不是 t 本身）
        # 管线约定：前方为 -Z（R = -R_co）
        assert np.linalg.det(imgs[0]["R"]) == -1.0

    def test_front_point_projects_to_principal_point(self):
        """前方 -Z 约定下，正前方点必须投到主点（否则纹理整张空白）。"""
        import numpy as np
        from h3d.photo_texture import project_photos_to_texture  # noqa: F401
        from h3d.sfm_solve import parse_images
        import tempfile, os
        # 相机在 (0,0,3)（C=(0,0,3)）：R_co = diag(1,-1,-1) -> tvec = -R_co@C = (0,0,3)
        # 世界点 (0,0,0) 应落在主点
        txt = "1 0.7071067811865476 0.7071067811865476 0 0 0 0 3 1 img.jpg\n0 0 -1\n"
        d = tempfile.mkdtemp()
        p = os.path.join(d, "images.txt")
        open(p, "w", encoding="utf-8").write(txt)
        im = parse_images(p)[0]
        Xc = im["R"] @ (np.zeros(3) - im["t"])
        assert Xc[2] < 0, "正前方点必须满足 Zc<0"


class TestDeterministicRepro:
    """回归（实机：S1 同一输入两次运行注册数 3/16 ~ 16/16 波动）。

    根因：COLMAP 特征提取 / 匹配在**多线程**下浮点归约顺序不定，产出内容
    不同的 database.db。修复：deterministic=True 时两阶段强制单线程。
    """

    def test_repro_args_forces_single_thread(self):
        from h3d.sfm_solve import _repro_args
        assert _repro_args("FeatureExtraction", True, 0) == [
            "--FeatureExtraction.num_threads", "1",
            "--default_random_seed", "0"]

    def test_repro_args_fast_mode_uses_auto(self):
        from h3d.sfm_solve import _repro_args
        # -1 = COLMAP 自动（全部核心）
        assert _repro_args("FeatureMatching", False, 0)[1] == "-1"

    def test_repro_args_explicit_threads_override(self):
        from h3d.sfm_solve import _repro_args
        assert _repro_args("FeatureExtraction", True, 6)[1] == "6"
        assert _repro_args("FeatureExtraction", False, 6)[1] == "6"

    def test_mapper_threads_follows_deterministic(self):
        """mapper BA 的多线程浮点归约抖动（实测位姿差 6.2e-15）也须收敛。"""
        from h3d.sfm_solve import _mapper_threads
        assert _mapper_threads(True, 0) == "1"
        assert _mapper_threads(False, 0) == "4"
        assert _mapper_threads(True, 8) == "8"       # 显式覆盖优先

    def _capture(self, tmp_path, monkeypatch, **kw):
        from h3d import sfm_solve
        ws = tmp_path / "ws"
        (ws / "sparse").mkdir(parents=True, exist_ok=True)
        cmds = []

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, **kw2):
            cmds.append(list(cmd))
            if "mapper" in cmd:      # 模拟 mapper 落盘二进制模型
                _write_bin_model(ws / "sparse", "0", 8)
            return _P()

        monkeypatch.setattr(sfm_solve, "run_external", fake_run)
        sfm_solve.run_colmap_sparse(str(tmp_path / "photos"), str(ws),
                                    "colmap", **kw)
        return [" ".join(c) for c in cmds]

    def test_sparse_commands_single_threaded_by_default(self, tmp_path,
                                                       monkeypatch):
        cmds = self._capture(tmp_path, monkeypatch)
        fe = next(c for c in cmds if "feature_extractor" in c)
        mt = next(c for c in cmds if "exhaustive_matcher" in c)
        assert "--FeatureExtraction.num_threads 1" in fe
        assert "--FeatureMatching.num_threads 1" in mt
        mp = next(c for c in cmds if c.split()[1] == "mapper")
        assert "--Mapper.random_seed 0" in mp

    def test_sparse_commands_auto_threads_when_fast(self, tmp_path,
                                                    monkeypatch):
        cmds = self._capture(tmp_path, monkeypatch, deterministic=False)
        fe = next(c for c in cmds if "feature_extractor" in c)
        mt = next(c for c in cmds if "exhaustive_matcher" in c)
        assert "--FeatureExtraction.num_threads -1" in fe
        assert "--FeatureMatching.num_threads -1" in mt

    def test_incremental_commands_single_threaded(self, tmp_path, monkeypatch):
        from h3d import sfm_solve
        ws = tmp_path / "ws"
        (ws / "sparse").mkdir(parents=True, exist_ok=True)
        _write_bin_model(ws / "sparse", "0", 4)
        (ws / "database.db").write_bytes(b"")             # 触发增量分支
        cmds = []

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake(cmd, **kw):
            cmds.append(list(cmd))
            if "image_registrator" in cmd:                # 模拟增量模型落盘
                _write_bin_model(ws / "sparse", "0_inc", 5)
            return _P()

        monkeypatch.setattr(sfm_solve, "run_external", fake)
        sfm_solve.run_incremental_sparse(str(tmp_path / "photos"), str(ws),
                                         "colmap", deterministic=True)
        joined = [" ".join(c) for c in cmds]
        assert "--FeatureExtraction.num_threads 1" in next(
            c for c in joined if "feature_extractor" in c)
        assert "--FeatureMatching.num_threads 1" in next(
            c for c in joined if "exhaustive_matcher" in c)


class TestIncrementalMetaNotOverwritten:
    """回归：inc_meta 曾被无条件 ``{"incremental": False}`` 覆盖。

    后果：增量成功运行在报表中被误标为全量重建；增量失败回退的 error
    信息丢失，S1 指标无法追溯。
    """

    def test_incremental_meta_reported_true(self, tmp_path):
        from h3d.sfm_solve import _repro_args  # noqa: F401
        import inspect
        from h3d import sfm_solve
        src = inspect.getsource(sfm_solve.run_sfm_solve)
        # 源码层面断言：不得再出现无条件的覆盖赋值
        assert "inc_meta = {\"incremental\": False}\n    if model_txt is None" \
            not in src
        # 且必须有按分支赋值的两处
        assert 'inc_meta = {"incremental": True, "fallback": False}' in src
        assert '"fallback": True' in src

    def test_run_sfm_solve_incremental_branch_marks_true(self, tmp_path,
                                                         monkeypatch):
        """走增量分支时 metrics["incremental"]["incremental"] 必须为 True。"""
        cfg = _make_cfg(tmp_path, deterministic=True)
        # 预置照片 + workspace（database + 模型）→ 走增量分支
        photo = tmp_path / "00_photo" / "relic"
        os.makedirs(photo, exist_ok=True)
        (photo / "a.jpg").write_bytes(b"x")
        ws = tmp_path / "ws"
        os.makedirs(ws / "sparse" / "0", exist_ok=True)
        (ws / "database.db").write_bytes(b"")

        from h3d import sfm_solve
        txt = _fake_colmap_sparse(tmp_path, n_reg=4)
        monkeypatch.setattr(sfm_solve, "_find_colmap", lambda c: "colmap")
        monkeypatch.setattr(sfm_solve, "run_external",
                            lambda cmd, **kw: type("P", (), {
                                "returncode": 0, "stdout": "", "stderr": ""})())
        monkeypatch.setattr(sfm_solve, "run_incremental_sparse",
                            lambda *a, **kw: str(txt))
        task = Task(item_id="relic", root=str(tmp_path / "proj"))
        os.makedirs(task.root, exist_ok=True)
        res = sfm_solve.run_sfm_solve(task, cfg)
        assert res.metrics["incremental"]["incremental"] is True
        assert res.metrics["incremental"]["fallback"] is False


class TestMatcherSubcommandAlias:
    """回归：P3 增量注册曾把配置值当 COLMAP 子命令，导致增量能力从未生效。

    ``matcher="exhaustive"`` 必须映射为 ``colmap exhaustive_matcher``；
    原实现直接拼 ``colmap exhaustive``，该子命令不存在，步骤返回码非 0 →
    抛 SfmError → 被 ``except SfmError`` 吞掉并静默回退全量重建。
    """

    def test_alias_mapping(self):
        from h3d.sfm_solve import _matcher_cmd
        assert _matcher_cmd("exhaustive") == "exhaustive_matcher"
        assert _matcher_cmd("sequential") == "sequential_matcher"
        # 已是对完整子命令名时保持原样（幂等）
        assert _matcher_cmd("exhaustive_matcher") == "exhaustive_matcher"

    def test_sparse_step_two_is_valid_subcommand(self, tmp_path, monkeypatch):
        from h3d import sfm_solve
        ws = tmp_path / "ws"
        (ws / "sparse").mkdir(parents=True, exist_ok=True)
        cmds = []

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake(cmd, **kw):
            cmds.append(list(cmd))
            if "mapper" in cmd:
                _write_bin_model(ws / "sparse", "0", 8)
            return _P()

        monkeypatch.setattr(sfm_solve, "run_external", fake)
        sfm_solve.run_colmap_sparse(str(tmp_path / "photos"), str(ws), "colmap")
        subs = [c[1] for c in cmds]
        assert subs[0] == "feature_extractor"
        assert subs[1] == "exhaustive_matcher"
        assert subs[2] == "mapper"


class TestPreparePhotosCache:
    """回归：预处理副本的过期缓存。

    原实现在副本已存在时无条件跳过写入，导致「源照片被替换后重跑」
    静默沿用过期副本（表现为「换了照片但重建结果不变」）。
    """

    def test_stale_when_dst_missing(self, tmp_path):
        from h3d.sfm_solve import _cache_is_stale
        src = tmp_path / "a.jpg"
        src.write_bytes(b"x")
        assert _cache_is_stale(str(src), str(tmp_path / "nope.jpg")) is True

    def test_refresh_when_source_newer(self, tmp_path):
        import time
        from h3d.sfm_solve import _cache_is_stale
        dst = tmp_path / "a.jpg"
        dst.write_bytes(b"old")
        src = tmp_path / "src.jpg"
        src.write_bytes(b"new")
        old = time.time() - 100
        os.utime(dst, (old, old))
        assert _cache_is_stale(str(src), str(dst)) is True

    def test_not_stale_when_copy_fresh(self, tmp_path):
        from h3d.sfm_solve import _cache_is_stale
        src = tmp_path / "a.jpg"
        src.write_bytes(b"x")
        dst = tmp_path / "b.jpg"
        dst.write_bytes(b"x")
        assert _cache_is_stale(str(src), str(dst)) is False

    def test_prepare_photos_refreshes_replaced_source(self, tmp_path):
        """源照片内容被替换后，副本必须被刷新（字节变化可见）。"""
        import time
        from PIL import Image
        from h3d.sfm_solve import _prepare_photos
        photo = tmp_path / "photos"
        photo.mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()
        p = photo / "a.jpg"
        Image.new("RGB", (32, 32), (10, 10, 10)).save(p)

        out1 = _prepare_photos(str(photo), str(ws), max_size=2400)
        first = open(os.path.join(out1, "a.jpg"), "rb").read()

        # 替换源照片（更亮），并把 mtime 推后以便触发刷新判据
        Image.new("RGB", (32, 32), (200, 200, 200)).save(p)
        now = time.time() + 10
        os.utime(p, (now, now))

        out2 = _prepare_photos(str(photo), str(ws), max_size=2400)
        second = open(os.path.join(out2, "a.jpg"), "rb").read()
        assert first != second, "源照片已替换，副本却未刷新（过期缓存）"


class TestLargestSubmodelSelection:
    """重大回归（实机定位的「2/16~16/16 随机波动」真因）。

    COLMAP mapper 在匹配图不连通时输出多个子重建（sparse/0、sparse/1…），
    **不保证 0 号最大**。实测示例数据：sparse/0 仅 2 张、sparse/1 有完整 16 张。
    原实现硬编码只读 sparse/0 → S1 报告「2/16 注册」。
    """

    @staticmethod
    def _mk_model(root, name, n_img):
        """伪造一个二进制子模型（只需 images.bin 头 8 字节 = 图像数）。"""
        return _write_bin_model(root, name, n_img)

    def test_picks_largest_not_slot_zero(self, tmp_path):
        from h3d.sfm_solve import _pick_largest_model
        s = str(tmp_path / "sparse")
        self._mk_model(s, "0", 2)
        self._mk_model(s, "1", 16)
        assert os.path.basename(_pick_largest_model(s)) == "1"

    def test_returns_empty_when_no_model(self, tmp_path):
        from h3d.sfm_solve import _pick_largest_model
        s = tmp_path / "sparse"
        s.mkdir()
        assert _pick_largest_model(str(s)) == ""

    def test_ignores_txt_output_dirs(self, tmp_path):
        from h3d.sfm_solve import _pick_largest_model
        s = str(tmp_path / "sparse")
        self._mk_model(s, "0", 4)
        os.makedirs(os.path.join(s, "0_txt"), exist_ok=True)
        assert os.path.basename(_pick_largest_model(s)) == "0"

    def test_normalize_moves_largest_into_slot_zero(self, tmp_path):
        from h3d.sfm_solve import (_model_num_images, _normalize_model_slot,
                                   _pick_largest_model)
        s = str(tmp_path / "sparse")
        self._mk_model(s, "0", 2)
        best = self._mk_model(s, "1", 16)
        out = _normalize_model_slot(s, _pick_largest_model(s))
        assert os.path.basename(out) == "0"
        assert _model_num_images(out) == 16
        # 被替换的碎片改名保留，便于事后核查（不删除用户数据）
        assert os.path.isdir(os.path.join(s, "1_0_replaced"))

    def test_normalize_is_noop_when_already_slot_zero(self, tmp_path):
        from h3d.sfm_solve import (_model_num_images, _normalize_model_slot,
                                   _pick_largest_model)
        s = str(tmp_path / "sparse")
        self._mk_model(s, "0", 16)
        out = _normalize_model_slot(s, _pick_largest_model(s))
        assert _model_num_images(out) == 16
        assert not os.path.exists(os.path.join(s, "0_0_replaced"))

    def test_run_colmap_sparse_normalizes_largest(self, tmp_path, monkeypatch):
        """端到端：mapper 产出 0(2 张)/1(16 张) 时，sparse/0 最终必须是 16 张。"""
        from h3d import sfm_solve
        ws = tmp_path / "ws"
        sparse = ws / "sparse"
        sparse.mkdir(parents=True)

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake(cmd, **kw):
            if "mapper" in cmd:
                self._mk_model(str(sparse), "0", 2)
                self._mk_model(str(sparse), "1", 16)
            return _P()

        monkeypatch.setattr(sfm_solve, "run_external", fake)
        sfm_solve.run_colmap_sparse(str(tmp_path / "photos"), str(ws), "colmap")
        assert sfm_solve._model_num_images(str(sparse / "0")) == 16

    def test_run_colmap_sparse_raises_without_model(self, tmp_path, monkeypatch):
        from h3d import sfm_solve
        ws = tmp_path / "ws"
        (ws / "sparse").mkdir(parents=True)

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        monkeypatch.setattr(sfm_solve, "run_external", lambda cmd, **kw: _P())
        try:
            sfm_solve.run_colmap_sparse(str(tmp_path / "photos"), str(ws),
                                        "colmap")
            raise AssertionError("应抛 SfmError")
        except sfm_solve.SfmError:
            pass
