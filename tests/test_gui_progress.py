"""tests.test_gui_progress : 生成页「动态加载表达」与子进程解包隔离回归。

实机背景（2026-10-17，第二次失败）
----------------------------------
30 张兵马俑照片跑纯照片模式，dense 全部完成后在**最后一步**崩：

    File "...open3d/__init__.py", line 31, in <module>
      os.add_dll_directory(.../_MEI000065282/open3d)
    FileNotFoundError: [WinError 2] ...: '...\\_MEI000065282\\open3d'

根因不是 open3d 缺文件（同一 exe 跑最小点云 M1~M6 全通），而是
**PyInstaller onefile 的解包目录共享**：GUI 用 ``_PYI_*`` / ``_MEIPASS2``
环境变量让 ``--batch`` 子进程复用父进程的 ``%TEMP%/. _MEIxxxx``，
父进程一退出 bootloader 就删掉它，而子进程还在跑 56 分钟。

同时用户反馈"生成页面卡住"：阶段灯只在完成时变色，``patch_match_stereo``
50 分钟里界面毫无变化。本测试同时锁住这两件事：

1. ``child_env()`` 必须剔除 ``_PYI_*`` / ``_MEIPASS2``；
2. 阶段开始 ``▶ S1 开始`` 必须能解析成 running；
3. 子步骤 / COLMAP 逐视图进度必须能解析成可读文本；
4. 日志着色必须把加载与失败行区分开。
"""
from __future__ import annotations

import os

import pytest

from h3d.gui.logparse import (stage_done_of, stage_failed_of,
                              stage_start_of, substep_of)
from h3d.gui.runner import _PYI_SHARED_ENV, child_env


class TestChildEnvIsolation:
    def test_strips_pyinstaller_shared_unpack_dir(self, monkeypatch):
        """核心回归：子进程不得继承父进程的解包目录。"""
        for key in _PYI_SHARED_ENV:
            monkeypatch.setenv(key, r"C:\Users\x\AppData\Local\Temp\_MEI123")
        monkeypatch.setenv("PATH", "C:/Windows")

        env = child_env()

        for key in _PYI_SHARED_ENV:
            assert key not in env, f"{key} 必须被剔除，否则父进程退出会删掉解包目录"
        assert env.get("PATH") == "C:/Windows", "其余环境变量必须原样保留"

    def test_covers_all_documented_keys(self):
        """PyInstaller 6.x 用 _PYI_APPLICATION_HOME_DIR 传递解包目录。"""
        assert "_PYI_APPLICATION_HOME_DIR" in _PYI_SHARED_ENV
        assert "_PYI_PARENT_PROCESS_LEVEL" in _PYI_SHARED_ENV
        assert "_MEIPASS2" in _PYI_SHARED_ENV


class TestStageStartParsing:
    def test_parses_stage_start(self):
        line = ("2026-10-17 15:22:43 [INFO] [h3d.runner.batch_processor] "
                "▶ S1 开始：relic_001BMYyyyy")
        assert stage_start_of(line) == "S1"

    def test_all_stage_ids(self):
        for sid in ("S1", "M1", "M2", "M3", "M4", "M5", "M6"):
            assert stage_start_of(f"▶ {sid} 开始：x") == sid

    def test_ignores_completion_lines(self):
        assert stage_start_of("M3 完成：probe") is None
        assert stage_done_of("2026-10-17 [INFO] [h3d.runner] M3 完成：probe") == "M3"

    def test_failure_still_parsed(self):
        assert stage_failed_of("失败隔离：item=x stage=S1 -> SfmError") == "S1"


class TestSubstepParsing:
    def test_colmap_step(self):
        sid, text = substep_of("[INFO] [h3d.sfm_solve] S1 COLMAP[2/3]: ...")
        assert sid == "S1"
        assert "2/3" in text and "特征匹配" in text

    def test_dense_step(self):
        sid, text = substep_of("[INFO] [h3d.sfm_solve] S1 dense[2/3]: ...")
        assert sid == "S1"
        assert "2/3" in text and "深度图" in text

    def test_colmap_view_progress(self):
        """patch_match_stereo 的真实进度行（这是"卡住"的直接解药）。"""
        sid, text = substep_of("2026-10-17 [INFO] [h3d.sfm_solve] "
                               "S1 Processing view 12 / 30")
        assert sid == "S1"
        assert text == "深度图 12/30"

    def test_unrelated_line_returns_none(self):
        assert substep_of("2026-10-17 [INFO] [h3d.runner] M3 完成：probe") is None


class TestLogStyling:
    def test_error_is_red_and_bold(self):
        from h3d.gui.app import _line_style
        color, bold = _line_style("[ERROR] [h3d.runner.batch_processor] 失败隔离")
        assert color == "#ff6b6b" and bold is True

    def test_progress_line_is_highlighted(self):
        from h3d.gui.app import _line_style
        color, bold = _line_style(
            "[INFO] [h3d.sfm_solve] S1 Processing view 12 / 30")
        assert color == "#a5d6ff" and bold is True, "加载进度必须突出显示"

    def test_plain_line_has_no_color(self):
        from h3d.gui.app import _line_style
        color, _ = _line_style("[INFO] [h3d.io] 读取点云")
        assert color == ""


class TestBatchProcessorStageLog:
    def test_emits_start_line(self, caplog):
        """batch_processor 必须输出可被 GUI 解析的阶段开始行。"""
        import logging
        from h3d.runner.batch_processor import _log_stage_start
        with caplog.at_level(logging.INFO, logger="h3d.runner.batch_processor"):
            _log_stage_start("S1", "relic_001")
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "▶ S1 开始：relic_001" in text
        assert stage_start_of("▶ S1 开始：relic_001") == "S1"


class TestRunExternalStreaming:
    """run_external(on_line=...) 是进度可见性的基础设施。"""

    def test_lines_streamed_and_output_kept(self):
        import sys

        from h3d.core.proc import run_external
        code = ("for i in range(1, 4):\n"
                "    print(f'Processing view {i} / 3', flush=True)\n")
        seen: list[str] = []
        proc = run_external([sys.executable, "-c", code],
                            on_line=seen.append, timeout=60)

        assert proc.returncode == 0
        assert seen == ["Processing view 1 / 3",
                        "Processing view 2 / 3",
                        "Processing view 3 / 3"]
        # 流式模式下仍要保留完整输出，供失败时落盘诊断
        assert "Processing view 3 / 3" in proc.stdout

    def test_callback_exception_does_not_break_run(self):
        import sys

        from h3d.core.proc import run_external

        def boom(_line: str) -> None:
            raise RuntimeError("进度回调炸了")

        proc = run_external([sys.executable, "-c", "print('ok', flush=True)"],
                            on_line=boom, timeout=60)
        assert proc.returncode == 0, "回调异常不得影响主流程"

    def test_timeout_still_raises(self):
        import subprocess
        import sys

        from h3d.core.proc import run_external
        with pytest.raises(subprocess.TimeoutExpired):
            run_external([sys.executable, "-c",
                          "import time; time.sleep(10)"],
                         on_line=lambda _l: None, timeout=1)


class TestProgressLoggerFilter:
    def test_forwards_only_progress_and_dedups(self, caplog):
        import logging

        from h3d.sfm_solve import _ProgressLogger
        cb = _ProgressLogger("S1")
        with caplog.at_level(logging.INFO, logger="h3d.sfm_solve"):
            cb("Processing view 1 / 30")
            cb("Processing view 1 / 30")          # 连续重复 -> 去重
            cb("Reading configuration from blah")  # 非进度 -> 丢弃
            cb("Fusing image 2")
        msgs = [r.getMessage() for r in caplog.records]
        # 输出已被清洗成中文：Processing view 1 / 30 -> 深度图 1/30
        assert sum("深度图 1/30" in m for m in msgs) == 1
        assert any("Fusing image 2" in m for m in msgs)
        assert not any("Reading configuration" in m for m in msgs)

    def test_ignores_overlong_lines(self, caplog):
        import logging

        from h3d.sfm_solve import _ProgressLogger
        cb = _ProgressLogger("S1")
        with caplog.at_level(logging.INFO, logger="h3d.sfm_solve"):
            cb("Processing view " + "x" * 500)
        assert not any("Processing view" in r.getMessage() for r in caplog.records)



# =====================================================================
# r6（2026-10-17 晚）——「深度图 30/30 卡住」与解析线程静默死亡
# =====================================================================
# 实机现象：跑 18 分钟，状态行一直显示「深度图 30/30」，日志只有 3 行。
# 现场核对：
#   - 子进程 batch_gui.log 有 115 行完整进度，patch_match 正从 1/30 涨到 2/30；
#   - 但 GUI 侧一行没收到 → `runner.run()` 调用了 `substep_of` 却**没有导入**，
#     第一行日志后抛 NameError，QThread 静默结束，界面看起来就是卡死；
#   - 「30/30」来自 `_depth_progress()` 遍历所有文物取最大值，撞上了上一轮
#     残留的 `relic_666`（真有 30 张深度图），而当前文物是 0 张。
# 本组测试锁住三件事：漏导入、深度图计数必须按文物隔离、进度单调。


class TestItemOf:
    def test_reads_item_from_stage_start(self):
        from h3d.gui.logparse import item_of
        line = ("2026-10-17 20:36:40 [INFO] [h3d.runner.batch_processor] "
                "▶ S1 开始：relic_001BMYyyyy")
        assert item_of(line) == "relic_001BMYyyyy"

    def test_none_for_other_lines(self):
        from h3d.gui.logparse import item_of
        assert item_of("[INFO] [h3d.sfm_solve] S1 COLMAP[1/3]: ...") is None
        assert item_of("▶ S1 完成：relic_001") is None


class TestS1Fraction:
    def test_monotonic_along_real_run(self):
        """真实运行时序：进度比例必须严格递增（否则进度条会倒退）。"""
        from h3d.gui.logparse import s1_fraction
        seq = [
            "[INFO] [h3d.sfm_solve] S1 COLMAP[1/3]: colmap.exe feature_extractor",
            "[INFO] [h3d.sfm_solve] S1 特征提取 15/30",
            "[INFO] [h3d.sfm_solve] S1 特征提取 30/30",
            "[INFO] [h3d.sfm_solve] S1 COLMAP[2/3]: colmap.exe exhaustive_matcher",
            "[INFO] [h3d.sfm_solve] S1 COLMAP[3/3]: colmap.exe mapper",
            "[INFO] [h3d.sfm_solve] S1 dense[1/3]: colmap.exe image_undistorter",
            "[INFO] [h3d.sfm_solve] S1 去畸变 30/30",
            "[INFO] [h3d.sfm_solve] S1 dense[2/3]: colmap.exe patch_match_stereo",
            "[INFO] [h3d.sfm_solve] S1 深度图 1/30：DSC_0008.JPG",
            "[INFO] [h3d.sfm_solve] S1 深度图 30/30：DSC_0037.JPG",
            "[INFO] [h3d.sfm_solve] S1 dense[3/3]: colmap.exe stereo_fusion",
            "[INFO] [h3d.sfm_solve] S1 融合 30/30",
        ]
        vals = [s1_fraction(x) for x in seq]
        assert all(v is not None for v in vals), f"未解析：{list(zip(seq, vals))}"
        assert vals == sorted(vals), f"必须单调不减：{vals}"
        assert 0.0 < vals[0] and vals[-1] <= 1.0

    def test_none_for_unrelated(self):
        from h3d.gui.logparse import s1_fraction
        assert s1_fraction("[INFO] [h3d.runner] M3 完成：probe") is None


class TestChildEnvEncoding:
    def test_forces_utf8_stdio(self, monkeypatch):
        """打包版子进程默认 cp936，GUI 按 utf-8 读 → 中文全变乱码。"""
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        env = child_env()
        assert env.get("PYTHONIOENCODING") == "utf-8"
        assert env.get("PYTHONUTF8") == "1"


class TestDepthProgressScopedToItem:
    def _mk(self, root, item, n_photo, n_geom):
        base = root / "99_runs" / "sfm_tmp" / item
        (base / "photos_small").mkdir(parents=True)
        for i in range(n_photo):
            (base / "photos_small" / f"DSC_{i:04d}.JPG").write_bytes(b"x")
        dm = base / "dense" / "stereo" / "depth_maps"
        dm.mkdir(parents=True)
        for i in range(n_geom):
            (dm / f"DSC_{i:04d}.JPG.geometric.bin").write_bytes(b"x")

    def test_ignores_other_items_residue(self, tmp_path):
        """核心回归：别的文物残留 30 张，当前文物 2 张，必须显示 2/30。"""
        from h3d.gui.app import depth_progress
        self._mk(tmp_path, "relic_666", 30, 30)          # 上一轮残留
        self._mk(tmp_path, "relic_act", 30, 2)           # 本次正在跑（几何校验相位）
        assert depth_progress(str(tmp_path), "relic_act", 2) == (2, 30)

    def test_no_item_yields_zero(self, tmp_path):
        from h3d.gui.app import depth_progress
        self._mk(tmp_path, "relic_666", 30, 30)
        assert depth_progress(str(tmp_path), "") == (0, 0)

    def test_photometric_only_still_counts(self, tmp_path):
        """geom_consistency 打开时先写 photometric，此时也要能显示进度。"""
        from h3d.gui.app import depth_progress
        self._mk(tmp_path, "a", 10, 0)
        dm = tmp_path / "99_runs" / "sfm_tmp" / "a" / "dense" / "stereo" / "depth_maps"
        for i in range(3):
            (dm / f"DSC_{i:04d}.JPG.photometric.bin").write_bytes(b"x")
        assert depth_progress(str(tmp_path), "a") == (3, 10)


class TestRunnerImportCompleteness:
    def test_every_logparse_helper_used_is_imported(self):
        """回归锁：r5 漏导入 substep_of → QThread 首行即 NameError 静默死亡。"""
        import ast
        import pathlib
        path = pathlib.Path(__file__).resolve().parents[1] / "src" / "h3d" / \
            "gui" / "runner.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "h3d.gui.logparse":
                imported |= {a.asname or a.name for a in node.names}
        required = {"STAGE_ORDER", "ProgressModel", "stage_start_of",
                    "stage_done_of", "stage_failed_of", "batch_summary",
                    "substep_of", "item_of", "items_total_of"}
        missing = required - imported
        assert not missing, f"runner.py 使用了但未导入：{missing}"


class _FakeProc:
    def __init__(self, lines):
        self.stdout = iter(lines)
        self.returncode = 0

    def wait(self):
        return self.returncode

    def poll(self):
        return self.returncode


class TestRunnerStreamsSignals:
    def _run(self, monkeypatch, tmp_path, lines):
        from h3d.gui import runner as R
        monkeypatch.setattr(R.subprocess, "Popen",
                            lambda *a, **k: _FakeProc(lines))
        r = R.BatchRunner(str(tmp_path / "config.yaml"))
        got = {"sub": [], "item": [], "stage": [], "prog": []}
        r.substep.connect(lambda s, t: got["sub"].append((s, t)))
        r.item_changed.connect(got["item"].append)
        r.stage_updated.connect(lambda s, st: got["stage"].append((s, st)))
        r.progress.connect(got["prog"].append)
        r.run()
        return got

    def test_full_sequence_emits_all_signals(self, monkeypatch, tmp_path):
        lines = [
            "▶ S1 开始：relic_001\n",
            "[INFO] [h3d.sfm_solve] S1 COLMAP[1/3]: colmap.exe feature_extractor\n",
            "[INFO] [h3d.sfm_solve] S1 COLMAP[2/3]: colmap.exe exhaustive_matcher\n",
            "[INFO] [h3d.sfm_solve] S1 深度图 1/30：DSC_0008.JPG\n",
            "[INFO] [h3d.sfm_solve] S1 完成：30/30 张注册，1 相机，18.8s\n",
            "[INFO] [h3d.runner] 批量处理完成：total=1 done=1 failed=0\n",
        ]
        got = self._run(monkeypatch, tmp_path, lines)

        assert got["item"] == ["relic_001"], "文物编号必须传给进度页"
        assert ("S1", "running") in got["stage"]
        assert ("S1", "done") in got["stage"]
        assert any("深度图 1/30" in t for _, t in got["sub"])
        assert got["prog"] == sorted(got["prog"]), f"进度不得回退：{got['prog']}"
        assert got["prog"][-1] == 100

    def test_parse_error_is_reported_not_silent(self, monkeypatch, tmp_path):
        """解析异常必须冒泡成可见错误——静默死线程是 r5 的致命表现。"""
        from h3d.gui import runner as R

        def boom(_line):
            raise RuntimeError("模拟解析崩溃")

        monkeypatch.setattr(R, "substep_of", boom)
        lines = ["▶ S1 开始：x\n", "任意一行\n"]
        monkeypatch.setattr(R.subprocess, "Popen",
                            lambda *a, **k: _FakeProc(lines))
        r = R.BatchRunner(str(tmp_path / "config.yaml"))
        logs, res = [], []
        r.log_line.connect(logs.append)
        r.finished_ok.connect(lambda ok, s: res.append((ok, s)))
        r.run()

        assert any("日志解析线程异常" in x for x in logs)
        assert res and res[0][0] is False


class TestForceUtf8Stdio:
    def test_packaged_stdout_becomes_utf8(self):
        """打包版流默认 cp936，GUI 按 UTF-8 读 → 中文变乱码方块。

        回归锁：``_force_utf8_stdio()`` 必须真的把流重新绑定为 UTF-8
        （``PYTHONIOENCODING`` 在 PyInstaller 环境里不生效）。
        """
        import io
        import sys

        from h3d.gui.launcher import _force_utf8_stdio

        buf = io.BytesIO()
        wrapper = io.TextIOWrapper(buf, encoding="gbk", newline="")
        out, err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = wrapper, io.StringIO()
        try:
            _force_utf8_stdio()
            sys.stdout.write("批处理进程 26048")
            sys.stdout.flush()
        finally:
            sys.stdout, sys.stderr = out, err
            wrapper.detach()      # 防止 TextIOWrapper 被回收时关闭底缓冲
        assert buf.getvalue().decode("utf-8") == "批处理进程 26048"

    def test_none_stream_is_safe(self):
        """windowed 模式下 sys.stdout 可能为 None，不得抛异常。"""
        import sys

        from h3d.gui.launcher import _force_utf8_stdio

        out, err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = None
        try:
            _force_utf8_stdio()
        finally:
            sys.stdout, sys.stderr = out, err


# =====================================================================
# r6.1：patch_match_stereo 跑两遍（photometric + geometric）
# =====================================================================
# 实测日志（relic_666/logs/colmap_step12.log）里 "Processing view n / 30"
# 出现 60 次：前 30 次是第一遍 photometric，后 30 次是 geom_consistency 校验。
# 不区分就会在界面上看到「深度图 30/30 → 深度图 1/30」的进度倒退。


class TestTwoPassDepthMaps:
    def test_progress_logger_marks_second_pass(self, caplog):
        import logging

        from h3d.sfm_solve import _ProgressLogger
        cb = _ProgressLogger("S1")
        with caplog.at_level(logging.INFO, logger="h3d.sfm_solve"):
            for i in (29, 30):
                cb(f"I x patch_match.cc:419] === Processing view {i} / 30 "
                   f"for DSC_{i:04d}.JPG ===")
            for i in (1, 2):
                cb(f"I x patch_match.cc:419] === Processing view {i} / 30 "
                   f"for DSC_{i:04d}.JPG ===")
        msgs = [r.getMessage() for r in caplog.records]
        assert any("深度图 30/30" in m for m in msgs)
        assert any("几何校验 1/30" in m for m in msgs), msgs
        assert not any("深度图 1/30" in m for m in msgs), "第二遍不得再报『深度图 1/30』"

    def test_substep_parses_geometric_pass(self):
        from h3d.gui.logparse import substep_of
        sid, text = substep_of("[INFO] [h3d.sfm_solve] S1 几何校验 5/30：DSC_0012.JPG")
        assert sid == "S1"
        assert text.startswith("几何校验 5/30")

    def test_fraction_never_goes_backwards_across_passes(self):
        """跨两遍的进度比例必须单调，否则进度条会往回缩。"""
        from h3d.gui.logparse import s1_fraction
        seq = ["[INFO] [h3d.sfm_solve] S1 dense[2/3]: patch_match_stereo",
               "[INFO] [h3d.sfm_solve] S1 深度图 30/30：DSC_0030.JPG",
               "[INFO] [h3d.sfm_solve] S1 几何校验 1/30：DSC_0001.JPG",
               "[INFO] [h3d.sfm_solve] S1 几何校验 30/30：DSC_0030.JPG",
               "[INFO] [h3d.sfm_solve] S1 dense[3/3]: stereo_fusion"]
        vals = [s1_fraction(x) for x in seq]
        assert all(v is not None for v in vals), list(zip(seq, vals))
        assert vals == sorted(vals), vals

    def test_phase2_counts_geometric_files(self, tmp_path):
        from h3d.gui.app import depth_progress
        base = tmp_path / "99_runs" / "sfm_tmp" / "a"
        (base / "photos_small").mkdir(parents=True)
        for i in range(10):
            (base / "photos_small" / f"DSC_{i:04d}.JPG").write_bytes(b"x")
        dm = base / "dense" / "stereo" / "depth_maps"
        dm.mkdir(parents=True)
        for i in range(10):                       # 第一遍已全部完成
            (dm / f"DSC_{i:04d}.JPG.photometric.bin").write_bytes(b"x")
        for i in range(3):                        # 第二遍刚开始
            (dm / f"DSC_{i:04d}.JPG.geometric.bin").write_bytes(b"x")

        assert depth_progress(str(tmp_path), "a", 1) == (10, 10)
        assert depth_progress(str(tmp_path), "a", 2) == (3, 10),             "第二遍必须按 geometric.bin 计数，否则一直停在 10/10"


class TestProgressModelPerItem:
    """回归（2026-10-24 实机）：5 件批处理进度锁死在 70%。

    旧实现 ``len(stages_done)/len(STAGE_ORDER)``：
      * ``stages_done`` 按阶段 id 去重，没有「件」这个维度 → 第 1 件跑完
        7 个阶段即集满，后 4 件不再贡献任何进度；
      * 分母 10 含从未启用的 P1/X1/X2 → 硬上限 70%。
    用户看到「加载 40 分钟停在 70%」判断卡死并取消。
    """

    def test_first_item_is_about_one_fifth(self):
        from h3d.gui.logparse import CORE_STAGES, ProgressModel
        m = ProgressModel(n_items=5)
        for s in CORE_STAGES:
            m.on_stage("a", s, "start")
            m.on_stage("a", s, "done")
        assert 18 <= m.value() <= 21, f"首件完成应 ~20%，实测 {m.value()}%"

    def test_five_items_reach_99_and_are_monotonic(self):
        from h3d.gui.logparse import CORE_STAGES, ProgressModel
        m = ProgressModel(n_items=5)
        seen = []
        for i in range(5):
            for s in CORE_STAGES:
                m.on_stage(f"item{i}", s, "done")
            seen.append(m.value())
        assert seen == sorted(seen), seen
        assert seen[-1] >= 98 and max(seen) <= 99, seen

    def test_s1_inner_fraction_moves_the_bar(self):
        from h3d.gui.logparse import ProgressModel
        m = ProgressModel(n_items=2)
        m.on_stage("a", "S1", "start")
        v0 = m.value()
        m.on_in_stage("a", "S1", 0.42)
        v1 = m.value()
        m.on_in_stage("a", "S1", 0.90)
        v2 = m.value()
        assert v0 < v1 < v2, (v0, v1, v2)

    def test_s1_skip_does_not_hand_out_ninety_percent(self):
        """纯扫描件：S1 跳过时不得凭 S1 权重直接到 90%。"""
        from h3d.gui.logparse import ProgressModel
        m = ProgressModel(n_items=1)
        m.on_stage("scan", "S1", "skip")
        assert m.value() <= 1, m.value()
        m.on_stage("scan", "M1", "done")
        assert m.value() >= 19, m.value()

    def test_feed_uses_item_owner_of_skip_and_done(self):
        """跳过行必须归到它自己的件，不能算到「当前件」上。"""
        from h3d.gui.logparse import ProgressModel
        m = ProgressModel()
        m.feed("任务发现完成：2 件文物")
        # 遍历 CORE_STAGES 而不是写死一份清单：写死的那份加一个阶段就会让
        # 「全跳过 ≈ 100%」不成立（2026-10-31 加 X3 时正是这么红的）。
        from h3d.gui.logparse import CORE_STAGES
        for s in CORE_STAGES:
            m.feed(f"○ {s} 跳过：item_b（断点续跑）")
        assert m.value() > 0
        assert m.item_fraction("item_b") > 0.99
        assert m.item_fraction("item_a") == 0.0


# ---------------------------------------------------------------------------
# 「跳过」不是「失败」：幂等重跑不得被判成失败（2026-10-25 实机）
# ---------------------------------------------------------------------------


class TestSkippedIsNotFailure:
    """用户跑完一件文物后又点了一次「一键生成交付物」。

    断点续跑让 7 个阶段全部跳过，末行 ``total=1 done=0 failed=0 skipped=1``。
    GUI 原判据 ``failed == 0 and done > 0`` → done=0 → 判失败 → 弹「运行未
    完成」、进度条从 100% 归零、状态灯变红。一次完全正确的幂等重跑被报成错误。
    """

    LINE = "2026-10-25 21:04:49 [INFO] [h3d.runner.batch_processor] " \
           "批量处理完成：total=1 done=0 failed=0 skipped=1（workers=1）"

    def test_batch_summary_reads_skipped(self):
        from h3d.gui.logparse import batch_summary
        s = batch_summary(self.LINE)
        assert s == {"total": 1, "done": 0, "failed": 0, "skipped": 1}

    def test_batch_summary_infers_skipped_for_old_logs(self):
        """1.1.5 之前的日志没有 skipped=，必须按 total-done-failed 兜底。"""
        from h3d.gui.logparse import batch_summary
        s = batch_summary("批量处理完成：total=5 done=3 failed=0")
        assert s["skipped"] == 2

    def test_batch_summary_ignores_other_lines(self):
        from h3d.gui.logparse import batch_summary
        assert batch_summary("▶ S1 开始：relic_BMY") is None

    def test_all_skipped_is_ok(self, monkeypatch):
        """整批跳过 → ok=True，且 summary 说明「无需重跑」。"""
        from h3d.gui import runner as R

        monkeypatch.setattr(R.subprocess, "Popen",
                            lambda *a, **k: _FakeProc([self.LINE]))
        runner = R.BatchRunner("x.yaml")
        got: list[tuple] = []
        runner.finished_ok.connect(lambda ok, s: got.append((ok, s)))
        runner.run()
        assert got and got[0][0] is True, got
        assert "无需重跑" in got[0][1]

    def test_failed_zero_but_nothing_done_is_not_ok(self, monkeypatch):
        """total=0（没发现任务）不能算成功——否则空项目也报「完成」。"""
        from h3d.gui import runner as R

        monkeypatch.setattr(
            R.subprocess, "Popen",
            lambda *a, **k: _FakeProc(["批量处理完成：total=0 done=0 failed=0"]))
        runner = R.BatchRunner("x.yaml")
        got: list[tuple] = []
        runner.finished_ok.connect(lambda ok, s: got.append((ok, s)))
        runner.run()
        assert got and got[0][0] is False

    def test_partial_skip_still_ok(self, monkeypatch):
        """部分跳过的批次（done=3 skipped=2）必须判成功并注明跳过件数。"""
        from h3d.gui import runner as R

        monkeypatch.setattr(
            R.subprocess, "Popen",
            lambda *a, **k: _FakeProc(
                ["批量处理完成：total=5 done=3 failed=0 skipped=2"]))
        runner = R.BatchRunner("x.yaml")
        got: list[tuple] = []
        runner.finished_ok.connect(lambda ok, s: got.append((ok, s)))
        runner.run()
        assert got and got[0][0] is True
        assert "其中 2 件已是最新" in got[0][1]

    def test_reads_tail_of_stdout_before_finishing(self, monkeypatch):
        """完成行之后还有报表产物行：必须读完再收尾，否则子进程写满管道阻塞。"""
        from h3d.gui import runner as R

        monkeypatch.setattr(
            R.subprocess, "Popen",
            lambda *a, **k: _FakeProc([self.LINE,
                                       "报表产物 -> report/report.html"]))
        runner = R.BatchRunner("x.yaml")
        seen: list[str] = []
        runner.log_line.connect(lambda s: seen.append(s))
        runner.run()
        assert any("报表产物" in s for s in seen)
