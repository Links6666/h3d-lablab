"""h3d.gui.launcher : GUI 打包启动器（PyInstaller 入口 + --batch 子进程模式）。

打包后 exe 的 sys.executable 指向自身：GUI 内 BatchRunner 用
  H3D.exe --batch <config>    （frozen）
  python -m h3d.cli batch -c  （开发）
两种模式执行同一管线。

实机修复（2026-09-15）：
    1. ``--batch`` 的 stdout 必须**同时**写日志文件与管道——早期实现用
       ``redirect_stdout`` 只写文件，导致 GUI 收不到任何行、进度条不动。
    2. 顶层异常兜底：任何未捕获异常都写 traceback 到 batch_gui.log，并向
       stdout 输出 ``[H3D-ERROR]`` 行，返回码 1；配合 spec 中
       ``disable_windowed_traceback=True``，用户不会再看到 PyInstaller
       的「Unhandled exception in script」崩溃框。
"""

from __future__ import annotations

import os
import sys
import traceback


#: 打包版需要透传给 ``h3d.cli`` 的参数（第一个位置）。``--batch`` / ``--report``
#: 是历史入口，单独处理以保持兼容。
_CLI_PASSTHROUGH = {
    "-h", "--help", "--version",
    "init", "validate", "batch", "report", "doctor",
}


class _Tee:
    """把写入同时转发到多个流（管道 + 日志文件），任一失败不影响其余。"""

    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]

    def write(self, data):
        for s in self._streams:
            try:
                s.write(data)
                s.flush()
            except Exception:  # noqa: BLE001 - 管道关闭 / 磁盘满都不应致命
                continue
        return len(data) if isinstance(data, str) else 0

    def flush(self):
        for s in self._streams:
            try:
                s.flush()
            except Exception:  # noqa: BLE001
                continue

    def isatty(self):
        return False


class _NullStream:
    """windowed 模式下 stdout/stderr 的替身（吞掉一切写入）。

    2026-10-23 实机证据：用户 GUI 会话的崩溃日志里连着两条

        File "h3d\\gui\\app.py", line 548, in _on_finished
        File "h3d\\gui\\preview.py", line 45, in load_mesh
        AttributeError: 'NoneType' object has no attribute 'write'

    ``preview.py:45`` 是 ``o3d.io.read_triangle_mesh(...)``。PyInstaller
    ``console=False``（windowed）时 ``sys.stdout`` / ``sys.stderr`` 为
    ``None``；``print()`` 遇到 None 会静默丢弃，但**库里的显式
    ``stderr.write`` 不会**——Open3D 打印一条警告就把异常抛进 Qt 槽，
    预览区停在空白，看起来像「模型加载失败」。

    这里给一个哑流，让任何第三方库的写操作变成空操作。真正需要的日志走
    ``logging``（已落文件），不依赖标准流。
    """

    encoding = "utf-8"
    errors = "replace"

    def write(self, data) -> int:
        return len(data) if isinstance(data, str) else 0

    def writelines(self, lines) -> None:  # noqa: D401 - 兼容 TextIO 接口
        for _ in lines:
            pass

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("windowed 模式下没有文件描述符")


def _force_utf8_stdio() -> None:
    """把本进程的 stdout/stderr 固定为 UTF-8，且保证它们**不为 None**。

    打包版（``console=False``）的流默认跟随系统代码页（cp936），而 GUI 的
    ``BatchRunner`` 按 UTF-8 解码子进程输出 —— 中文日志在界面上会变成
    ``[H3D] ���������� 26048 ���Ŀ¼``（实测 2026-10-17）。

    ``PYTHONIOENCODING`` / ``PYTHONUTF8`` 在 PyInstaller 打包环境里**不生效**：
    bootloader 在 Python 初始化前就按控制台代码页建好了流对象。必须在进程内
    ``reconfigure``。windowed 模式下流可能为 ``None``，全部包在 try 里；
    为 None 时换成 ``_NullStream``，否则第三方库的 ``write`` 会崩（见上）。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, _NullStream())
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 非 TextIOWrapper 或无该能力
            continue


def _run_batch(config_path: str, extra: list | None = None) -> int:
    """子进程批处理模式：stdout/stderr 双写（GUI 管道 + batch_gui.log）。

    ``extra`` 透传给 ``h3d.cli batch``（如 ``--no-preflight``）—— 自检出问题时
    需要一个明确写出来的应急通道，而不是逼用户删 manifest 或改代码。
    """
    import contextlib

    from h3d.cli import main as cli_main

    _force_utf8_stdio()
    log_path = os.path.join(os.path.dirname(os.path.abspath(config_path)),
                            "batch_gui.log")
    orig_out, orig_err = sys.stdout, sys.stderr
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        fh = open(log_path, "w", encoding="utf-8", errors="replace")
    except OSError:
        fh = None
    if fh is None:
        return cli_main(["batch", "-c", config_path] + list(extra or []))

    try:
        with fh:
            tee = _Tee(orig_out, fh)
            with contextlib.redirect_stdout(tee), \
                    contextlib.redirect_stderr(tee):
                # 解包目录与进程号：打包版排查「_MEI 被父进程删掉」的第一现场
                mei = getattr(sys, "_MEIPASS", "")
                if mei:
                    print(f"[H3D] 批处理进程 {os.getpid()} 解包目录：{mei}",
                          flush=True)
                try:
                    return cli_main(["batch", "-c", config_path] + list(extra or []))
                except SystemExit as exc:      # argparse 等
                    return int(exc.code or 0)
                except BaseException as exc:   # noqa: BLE001 - 打包版必须兜底
                    tb = traceback.format_exc()
                    msg = f"\n[H3D-ERROR] 批处理异常：{exc}\n{tb}"
                    # sys.stdout 此刻是 tee（管道 + 日志文件），保证崩溃栈
                    # 既回传 GUI 又落进 batch_gui.log；再兜底写原始 stdout。
                    for stream in (sys.stdout, sys.__stdout__):
                        try:
                            stream.write(msg)
                            stream.flush()
                        except Exception:  # noqa: BLE001
                            continue
                    return 1
    finally:
        sys.stdout, sys.stderr = orig_out, orig_err


def _run_cli(argv: list) -> int:
    """把参数透传给 ``h3d.cli``（打包版脚本化入口）。"""
    try:
        from h3d.cli import main as cli_main

        return cli_main(list(argv))
    except SystemExit as exc:  # argparse 的 --version / -h 走的是 SystemExit(0)
        return int(exc.code or 0)
    except Exception as exc:  # noqa: BLE001 - 命令行异常不该弹 GUI 崩溃框
        print(f"[H3D-ERROR] 命令行执行异常：{exc}")
        return 1


def main(argv: list | None = None) -> int:
    argv = argv or sys.argv[1:]
    _force_utf8_stdio()
    if len(argv) >= 2 and argv[0] == "--batch":
        return _run_batch(argv[1], argv[2:])
    if len(argv) >= 2 and argv[0] == "--report":
        return _run_cli(["report", "-c", argv[1]])
    # 打包版长期只认 --batch/--report，其余参数一律进 GUI：``H3D.exe --version``
    # 会**开出一个窗口然后挂住**（巡检 2026-10-23 实测，脚本里表现为永久阻塞），
    # ``doctor`` / ``validate`` 同样开窗口。CLI 子命令必须原样透传。
    if argv and (argv[0] in _CLI_PASSTHROUGH):
        return _run_cli(argv)
    # GUI 模式：先装崩溃诊断（无声闪退写日志）
    from h3d.gui.diag import install_crash_guard
    crash_log = install_crash_guard()
    # 软件启动自检（**第一道工序**，约 220 ms）：环境/依赖/残留进程/崩溃记录。
    # 放在创建 QApplication 之前跑，不占界面首帧时间；结论存进 h3d.health 的
    # 进程内缓存，欢迎页据此决定是否提示（见 h3d.gui.app._show_boot_preflight）。
    try:
        from h3d.health import run_preflight
        run_preflight("boot")
    except Exception as exc:  # noqa: BLE001 - 自检不可用不能拦住启动
        from h3d.gui.diag import _log
        _log(f"[启动自检] 不可用（忽略）：{exc}")
    try:
        from h3d.gui.app import run_gui
        return run_gui()
    except Exception as exc:  # noqa: BLE001 - 打包版 GUI 崩溃不留白屏
        from h3d.gui.diag import _log
        _log(f"[启动失败] {exc}\n{traceback.format_exc()}")
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication([])
            QMessageBox.critical(
                None, "H3D 启动失败",
                f"程序启动时发生异常：\n{exc}\n\n详细日志：{crash_log}")
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
