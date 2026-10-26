"""tests.test_gui_launcher : 打包启动器（--batch 子进程模式）回归测试。"""
from __future__ import annotations

import io
import os


class TestTee:
    """回归（实机问题：连续跳出的命令行 + GUI 无进度）：

    --batch 模式的 stdout 必须**双写**（GUI 管道 + batch_gui.log）。旧实现用
    redirect_stdout 只写文件，GUI 收不到任何行、进度条不动。
    """

    def test_writes_to_all_streams(self):
        from h3d.gui.launcher import _Tee
        a, b = io.StringIO(), io.StringIO()
        tee = _Tee(a, b)
        tee.write("hello\n")
        tee.flush()
        assert a.getvalue() == "hello\n"
        assert b.getvalue() == "hello\n"

    def test_broken_stream_does_not_raise(self):
        from h3d.gui.launcher import _Tee

        class _Boom:
            def write(self, data):
                raise OSError("pipe closed")

            def flush(self):
                raise OSError("pipe closed")

        ok = io.StringIO()
        tee = _Tee(_Boom(), ok)
        tee.write("x")
        tee.flush()
        assert ok.getvalue() == "x"


class TestBatchModeGuard:
    def test_exception_is_logged_and_returns_1(self, tmp_path, monkeypatch):
        """批处理内部异常必须被兜底为退出码 1 + 日志，不冒泡成崩溃框。"""
        import h3d.cli as cli
        from h3d.gui import launcher

        cfg = os.path.join(str(tmp_path), "config.yaml")
        open(cfg, "w", encoding="utf-8").write("project: {}\n")

        def _boom(argv=None):
            raise RuntimeError("模拟管线崩溃")

        monkeypatch.setattr(cli, "main", _boom)
        rc = launcher._run_batch(cfg)
        assert rc == 1
        log_path = os.path.join(str(tmp_path), "batch_gui.log")
        assert os.path.exists(log_path)
        assert "模拟管线崩溃" in open(log_path, encoding="utf-8").read()


class TestWindowedStdio:
    """回归（2026-10-23 实机崩溃日志）：

        preview.py:45 (o3d.io.read_triangle_mesh)
        AttributeError: 'NoneType' object has no attribute 'write'

    PyInstaller windowed（console=False）下 sys.stdout/stderr 为 None。
    print() 会静默丢弃，但库里的显式 stderr.write 会抛异常 —— Open3D 打一条
    警告就能让预览区崩在 Qt 槽里、画面停在空白。要求 _force_utf8_stdio()
    结束后两条流都**不为 None**，且任何库的写操作都是空操作而非异常。
    """

    def test_none_streams_are_replaced(self, monkeypatch):
        import sys
        from h3d.gui import launcher

        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)
        launcher._force_utf8_stdio()
        assert sys.stdout is not None, "windowed 下 stdout 不能留 None"
        assert sys.stderr is not None, "windowed 下 stderr 不能留 None"

    def test_library_write_does_not_raise(self, monkeypatch):
        import sys
        from h3d.gui import launcher

        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)
        launcher._force_utf8_stdio()
        # 模拟 Open3D / matplotlib 之类的告警输出
        sys.stderr.write("[Open3D WARNING] 模拟告警\n")
        sys.stderr.flush()
        sys.stdout.write("progress\n")
        assert sys.stderr.encoding  # logging 模块会读这个属性

    def test_real_stream_is_kept(self):
        import sys
        from h3d.gui import launcher

        before_out, before_err = sys.stdout, sys.stderr
        launcher._force_utf8_stdio()
        assert sys.stdout is before_out and sys.stderr is before_err, \
            "已有流必须原样保留（不能被替换成哑流）"


class TestWindowedStdio:
    """回归（2026-10-23 实机崩溃日志）：

        preview.py:45 (o3d.io.read_triangle_mesh)
        AttributeError: 'NoneType' object has no attribute 'write'

    PyInstaller windowed（console=False）下 sys.stdout/stderr 为 None。
    print() 会静默丢弃，但库里的显式 stderr.write 会抛异常 —— Open3D 打一条
    警告就能让预览区崩在 Qt 槽里、画面停在空白。要求 _force_utf8_stdio()
    结束后两条流都**不为 None**，且任何库的写操作都是空操作而非异常。
    """

    def test_none_streams_are_replaced(self, monkeypatch):
        import sys
        from h3d.gui import launcher

        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)
        launcher._force_utf8_stdio()
        assert sys.stdout is not None, "windowed 下 stdout 不能留 None"
        assert sys.stderr is not None, "windowed 下 stderr 不能留 None"

    def test_library_write_does_not_raise(self, monkeypatch):
        import sys
        from h3d.gui import launcher

        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)
        launcher._force_utf8_stdio()
        # 模拟 Open3D / matplotlib 之类的告警输出
        sys.stderr.write("[Open3D WARNING] 模拟告警\n")
        sys.stderr.flush()
        sys.stdout.write("progress\n")
        assert sys.stderr.encoding  # logging 模块会读这个属性

    def test_real_stream_is_kept(self):
        import sys
        from h3d.gui import launcher

        before_out, before_err = sys.stdout, sys.stderr
        launcher._force_utf8_stdio()
        assert sys.stdout is before_out and sys.stderr is before_err, \
            "已有流必须原样保留（不能被替换成哑流）"
