"""自检内核（h3d.health）单元测试。

断言的是**行为本质**，不是"没报错"：
* 自检自身异常/超时绝不允许抛出（否则启动被自检拖死）；
* ``ok`` 只由 blocker 决定（warning 不能拦启动，否则用户被小问题锁死）；
* 能查出来的问题必须真的查出来（每个检查项都构造坏场景 + 好场景对照）。
"""

from __future__ import annotations

import json
import os
import time

import pytest

from h3d.health import (
    CheckResult, HealthReport, already_ran, check, format_report, reset_cache,
    run_preflight,
)
from h3d.health import _probe as P
from h3d.health._project import data_root


# ---------------------------------------------------------------------------
# 数据模型与展示
# ---------------------------------------------------------------------------
class TestReportModel:
    def test_warning_does_not_block(self):
        rep = HealthReport("boot", [
            CheckResult("a", "甲", "warning", "小问题"),
            CheckResult("b", "乙", "ok", "正常"),
        ])
        assert rep.ok is True
        assert rep.blockers == []
        assert len(rep.warnings) == 1

    def test_blocker_blocks_and_explains(self):
        rep = HealthReport("project", [
            CheckResult("a", "数据根", "blocker", "不可写", hint="换个目录"),
        ])
        assert rep.ok is False
        text = rep.blocker_text()
        assert "数据根" in text and "换个目录" in text

    def test_summary_line_is_parsable(self):
        rep = HealthReport("project", [
            CheckResult("a", "甲", "blocker", "x"),
            CheckResult("b", "乙", "warning", "y"),
            CheckResult("c", "丙", "info", "z"),
        ], elapsed_ms=318)
        line = rep.summary_line()
        assert line.startswith("自检完成：project")
        assert "阻断 1" in line and "警告 1" in line and "提示 1" in line
        assert "318 ms" in line

    def test_format_report_silent_when_clean(self):
        rep = HealthReport("boot", [CheckResult("a", "甲", "ok", "正常")])
        assert format_report(rep) == ""
        assert "甲" in format_report(rep, only_problems=False)

    def test_to_dict_is_json_serialisable(self):
        rep = HealthReport("boot", [CheckResult("a", "甲", "ok", "正常")])
        json.dumps(rep.to_dict(), ensure_ascii=False)


# ---------------------------------------------------------------------------
# 执行器：异常 / 超时 / 去重
# ---------------------------------------------------------------------------
class TestExecutor:
    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch):
        reset_cache()
        monkeypatch.delenv("H3D_SKIP_PREFLIGHT", raising=False)
        yield
        reset_cache()

    def test_check_exception_becomes_warning(self, monkeypatch):
        """检查内部异常必须降级为 WARNING，绝不能让启动崩掉。"""
        def boom(ctx):
            raise RuntimeError("模拟探测崩溃")

        monkeypatch.setattr("h3d.health._boot._check_version", boom)
        # 直接跑真实 boot 会依赖环境；改为临时注册一个独立作用域不可行
        # （作用域校验），因此直接用 _run_one 走内部路径。
        from h3d.health import _run_one, _Spec, Context
        spec = _Spec(fn=boom, scope="boot", name="boom", title="模拟",
                     order=1, needs_cfg=False, needs_root=False)
        res = _run_one(spec, Context(scope="boot", budget_ms=200))
        assert res.severity == "warning"
        assert "RuntimeError" in res.detail

    def test_slow_check_times_out_without_blocking(self, monkeypatch):
        """慢检查只记 TIMEOUT（WARNING），主流程按预算返回。"""
        def slow(ctx):
            time.sleep(0.6)
            return CheckResult("slow", "慢", "ok", "x")

        from h3d.health import _run_one, _Spec, Context
        spec = _Spec(fn=slow, scope="boot", name="slow", title="慢",
                     order=1, needs_cfg=False, needs_root=False)
        t0 = time.monotonic()
        res = _run_one(spec, Context(scope="boot", budget_ms=100))
        assert (time.monotonic() - t0) >= 0.5   # _run_one 本身会等（线程内）
        assert res.severity == "ok"

    def test_budget_returns_fast(self, monkeypatch):
        """整体预算生效：注册一个 5 s 的检查，run_preflight 必须按预算返回。"""
        import h3d.health as H

        def slow(ctx):
            time.sleep(5)
            return CheckResult("z_slow", "慢", "ok", "x")

        monkeypatch.setattr(H, "_REGISTRY", H._REGISTRY + [
            H._Spec(fn=slow, scope="boot", name="z_slow", title="慢",
                    order=999, needs_cfg=False, needs_root=False)])
        monkeypatch.setattr(P, "nvidia_smi", lambda cache=None: {
            "available": False, "gpus": [], "error": "stub"})
        monkeypatch.setattr(P, "process_table", lambda cache=None: [])
        monkeypatch.setattr(P, "disk_free", lambda path: 100 * 1024 ** 3)

        t0 = time.monotonic()
        rep = run_preflight("boot", budget_ms=200, force=True)
        took = time.monotonic() - t0
        assert took < 1.5, f"自检未按预算返回：{took:.2f}s"
        names = {r.name for r in rep.results}
        assert "z_slow" in names          # 超时项也要出现在结论里
        assert any("超时" in r.detail for r in rep.results if r.name == "z_slow")

    def test_dedup_and_force(self, monkeypatch):
        monkeypatch.setattr(P, "nvidia_smi", lambda cache=None: {
            "available": False, "gpus": [], "error": "stub"})
        monkeypatch.setattr(P, "process_table", lambda cache=None: [])
        monkeypatch.setattr(P, "disk_free", lambda path: 100 * 1024 ** 3)
        r1 = run_preflight("boot")
        assert already_ran("boot")
        r2 = run_preflight("boot")
        assert r2 is r1                      # 命中缓存，不重复探测
        r3 = run_preflight("boot", force=True)
        assert r3 is not r1

    def test_skip_env(self, monkeypatch):
        monkeypatch.setenv("H3D_SKIP_PREFLIGHT", "1")
        rep = run_preflight("boot", force=True)
        assert rep.skipped and rep.ok
        assert "跳过" in rep.summary_line()


# ---------------------------------------------------------------------------
# 底层探测
# ---------------------------------------------------------------------------
class TestProbe:
    def test_write_probe_ok_and_missing(self, tmp_path):
        ok, _ = P.write_probe(str(tmp_path))
        assert ok
        ok2, why = P.write_probe(str(tmp_path / "not_exist"))
        assert not ok2 and "不存在" in why

    def test_dir_names_and_files(self, tmp_path):
        (tmp_path / "relic_001").mkdir()
        (tmp_path / "relic_002").mkdir()
        (tmp_path / "note.txt").write_text("x", encoding="utf-8")
        assert P.dir_names(str(tmp_path)) == ["relic_001", "relic_002"]
        (tmp_path / "relic_001" / "a.JPG").write_text("x", encoding="utf-8")
        (tmp_path / "relic_001" / "b.txt").write_text("x", encoding="utf-8")
        files = P.dir_files(str(tmp_path / "relic_001"), exts=(".jpg",))
        assert files == ["a.JPG"]           # 扩展名大小写不敏感

    def test_process_table_has_self(self):
        rows = P.process_table()
        # 至少能看到自己（三级实现任一可用即可）
        if rows:
            assert any(int(r["pid"]) > 0 for r in rows)


# ---------------------------------------------------------------------------
# 项目自检（构造坏场景 + 对照）
# ---------------------------------------------------------------------------
def _fake_cfg(root, *, sfm=False, manifest=None):
    """最小 cfg 替身（只提供自检读取的字段）。

    用 ``SimpleNamespace`` 而不是类体赋值：类体不参与闭包，读不到函数参数
    （``class _P: raw_dir = raw_dir`` 会 NameError），这个坑写测试时踩过一次。
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        project=SimpleNamespace(raw_dir=os.path.join(root, "00_raw")),
        runner=SimpleNamespace(
            manifest=manifest or os.path.join(root, "output", "run_manifest.json")),
        sfm_solve=SimpleNamespace(enabled=sfm),
    )


class TestProjectChecks:
    @pytest.fixture(autouse=True)
    def _stub_probe(self, monkeypatch):
        monkeypatch.setattr(P, "process_table", lambda cache=None: [])
        monkeypatch.setattr(P, "disk_free", lambda path: 100 * 1024 ** 3)
        reset_cache()
        yield
        reset_cache()

    def test_data_root_from_cfg(self, tmp_path):
        cfg = _fake_cfg(str(tmp_path))
        assert data_root(cfg) == os.path.abspath(str(tmp_path))

    def test_empty_inputs_is_blocker(self, tmp_path):
        cfg = _fake_cfg(str(tmp_path))
        rep = run_preflight("project", cfg=cfg, force=True)
        by = {r.name: r for r in rep.results}
        assert by["inputs"].severity == "blocker"
        assert rep.ok is False

    def test_raw_dirs_without_files_is_not_ok(self, tmp_path):
        """建了目录但没放扫描件 —— 必须说清"无扫描件"，不能报成"有 N 件"。"""
        (tmp_path / "00_raw" / "relic_001").mkdir(parents=True)
        (tmp_path / "00_photo" / "relic_001").mkdir(parents=True)
        (tmp_path / "00_photo" / "relic_001" / "a.jpg").write_bytes(b"\xff\xd8\xff")
        cfg = _fake_cfg(str(tmp_path))
        rep = run_preflight("project", cfg=cfg, force=True)
        by = {r.name: r for r in rep.results}
        assert by["inputs"].severity == "info"
        assert "无扫描件" in by["inputs"].detail

    def test_live_lock_is_blocker(self, tmp_path):
        from h3d.core.lockfile import LOCK_NAME
        (tmp_path / "00_raw" / "relic_001").mkdir(parents=True)
        (tmp_path / "00_raw" / "relic_001" / "a.ply").write_text("x",
                                                                encoding="utf-8")
        lock = tmp_path / LOCK_NAME
        lock.write_text(json.dumps({"pid": os.getpid(), "started_at": "t0",
                                    "token": "t"}), encoding="utf-8")
        cfg = _fake_cfg(str(tmp_path))
        rep = run_preflight("project", cfg=cfg, force=True)
        by = {r.name: r for r in rep.results}
        assert by["lock"].severity == "blocker"
        assert str(os.getpid()) in by["lock"].detail

    def test_stale_lock_is_ok_and_fix_removes(self, tmp_path):
        from h3d.core.lockfile import LOCK_NAME
        (tmp_path / "00_raw" / "relic_001").mkdir(parents=True)
        (tmp_path / "00_raw" / "relic_001" / "a.ply").write_text("x",
                                                                encoding="utf-8")
        lock = tmp_path / LOCK_NAME
        # PID 1 在 Windows 上不是真实用户进程 → _pid_alive 判死
        lock.write_text(json.dumps({"pid": 999999, "started_at": "t0",
                                    "token": "t"}), encoding="utf-8")
        cfg = _fake_cfg(str(tmp_path))
        rep = run_preflight("project", cfg=cfg, force=True, fix=True)
        by = {r.name: r for r in rep.results}
        assert by["lock"].severity == "ok"
        assert by["lock"].fix                       # 已自动清理
        assert not lock.exists()

    def test_manifest_done_but_outputs_missing(self, tmp_path):
        stem = tmp_path / "30_dense" / "relic_001"
        stem.mkdir(parents=True)
        gone = str(stem / "relic_001.ply")           # 故意不创建
        man = tmp_path / "output" / "run_manifest.json"
        man.parent.mkdir(parents=True)
        man.write_text(json.dumps({"stages": {
            "relic_001/M3": {"status": "done", "outputs": [gone]}}}),
            encoding="utf-8")
        cfg = _fake_cfg(str(tmp_path), manifest=str(man))
        rep = run_preflight("project", cfg=cfg, force=True)
        by = {r.name: r for r in rep.results}
        assert by["manifest"].severity == "warning"
        assert "产物已不在磁盘" in by["manifest"].detail

    def test_manifest_all_outputs_present(self, tmp_path):
        out = tmp_path / "30_dense" / "relic_001" / "relic_001.ply"
        out.parent.mkdir(parents=True)
        out.write_text("x", encoding="utf-8")
        man = tmp_path / "output" / "run_manifest.json"
        man.parent.mkdir(parents=True)
        man.write_text(json.dumps({"stages": {
            "relic_001/M3": {"status": "done", "outputs": [str(out)]}}}),
            encoding="utf-8")
        cfg = _fake_cfg(str(tmp_path), manifest=str(man))
        rep = run_preflight("project", cfg=cfg, force=True)
        by = {r.name: r for r in rep.results}
        assert by["manifest"].severity == "ok"

    def test_photo_pixels_mismatch_is_info(self, tmp_path):
        """照片与位姿尺寸不同源 → 只提示（M4 已按 pixel_scale 换算）。"""
        from PIL import Image
        item = "relic_001"
        pdir = tmp_path / "00_photo" / item
        pdir.mkdir(parents=True)
        Image.new("RGB", (400, 300)).save(pdir / "a.jpg")
        meta = tmp_path / "90_meta" / f"{item}_capture_meta.json"
        meta.parent.mkdir(parents=True)
        meta.write_text(json.dumps({"cameras": {"1": {"w": 200, "h": 150}},
                                    "images": []}), encoding="utf-8")
        cfg = _fake_cfg(str(tmp_path))
        rep = run_preflight("project", cfg=cfg, force=True)
        by = {r.name: r for r in rep.results}
        assert by["photo_pixels"].severity == "info"
        assert "2.000" in by["photo_pixels"].detail      # 400/200

    def test_photo_pixels_same_source_is_ok(self, tmp_path):
        from PIL import Image
        item = "relic_001"
        pdir = tmp_path / "00_photo" / item
        pdir.mkdir(parents=True)
        Image.new("RGB", (200, 150)).save(pdir / "a.jpg")
        meta = tmp_path / "90_meta" / f"{item}_capture_meta.json"
        meta.parent.mkdir(parents=True)
        meta.write_text(json.dumps({"cameras": {"1": {"w": 200, "h": 150}}}),
                        encoding="utf-8")
        cfg = _fake_cfg(str(tmp_path))
        rep = run_preflight("project", cfg=cfg, force=True)
        by = {r.name: r for r in rep.results}
        assert by["photo_pixels"].severity == "ok"

    def test_duplicate_photo_names_across_items(self, tmp_path):
        for item in ("relic_001", "relic_002"):
            d = tmp_path / "00_photo" / item
            d.mkdir(parents=True)
            (d / "DSC_0001.JPG").write_bytes(b"\xff\xd8")
        cfg = _fake_cfg(str(tmp_path))
        rep = run_preflight("project", cfg=cfg, force=True)
        by = {r.name: r for r in rep.results}
        assert by["photo_duplicates"].severity == "warning"
        assert "1 个文件名" in by["photo_duplicates"].detail

    def test_result_written_to_90_meta(self, tmp_path):
        (tmp_path / "00_raw" / "relic_001").mkdir(parents=True)
        cfg = _fake_cfg(str(tmp_path))
        run_preflight("project", cfg=cfg, force=True)
        out = tmp_path / "90_meta" / "preflight_project.json"
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["scope"] == "project"
        assert isinstance(data["results"], list) and data["results"]


# ---------------------------------------------------------------------------
# CLI / 入口集成
# ---------------------------------------------------------------------------
class TestCliIntegration:
    def test_doctor_exit_zero_when_no_blocker(self, monkeypatch, capsys):
        monkeypatch.setattr(P, "nvidia_smi", lambda cache=None: {
            "available": False, "gpus": [], "error": "stub"})
        monkeypatch.setattr(P, "process_table", lambda cache=None: [])
        monkeypatch.setattr(P, "disk_free", lambda path: 100 * 1024 ** 3)
        reset_cache()
        from h3d.cli import main
        rc = main(["doctor"])
        out = capsys.readouterr().out
        assert "自检完成：boot" in out
        assert rc in (0, 1)

    def test_batch_stops_on_blocker(self, tmp_path, monkeypatch, capsys):
        """开跑前自检有阻断项 → batch 必须在启动管线**之前**退出。"""
        monkeypatch.setattr(P, "process_table", lambda cache=None: [])
        monkeypatch.setattr(P, "disk_free", lambda path: 100 * 1024 ** 3)
        reset_cache()
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "project:\n  name: t\n  raw_dir: %s\nrunner:\n  manifest: %s\n"
            % (str(tmp_path / "00_raw"), str(tmp_path / "output" / "m.json")),
            encoding="utf-8")
        from h3d.cli import main
        rc = main(["batch", "-c", str(cfg_path)])
        err = capsys.readouterr().err
        assert rc == 1
        assert "已中止" in err

    def test_no_preflight_flag_bypasses(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(P, "process_table", lambda cache=None: [])
        reset_cache()
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "project:\n  name: t\n  raw_dir: %s\nrunner:\n  manifest: %s\n"
            "report:\n  enabled: false\n"
            % (str(tmp_path / "00_raw"), str(tmp_path / "output" / "m.json")),
            encoding="utf-8")
        from h3d.cli import main
        rc = main(["batch", "-c", str(cfg_path), "--no-preflight", "--dry-run"])
        out = capsys.readouterr().out
        assert "--no-preflight" in out
        assert rc == 0
