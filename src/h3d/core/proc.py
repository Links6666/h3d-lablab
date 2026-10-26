"""h3d.core.proc : 外部进程调用的跨平台封装（GUI 不弹控制台窗口）。

背景（实机问题）：
    打包版 GUI 为 ``console=False``，调用 COLMAP 等**控制台程序**时，Windows
    会为每个子进程新建一个控制台窗口——实机表现为「一键演示时连续跳出多个
    黑色命令行窗口」。COLMAP sparse 共 4 个步骤，用户会看到连续 4 次弹窗。

本模块统一提供：
    1. :func:`no_window_kwargs` —— 注入 ``CREATE_NO_WINDOW``（仅 Windows）。
    2. :func:`run_external`     —— 隐藏窗口 + 指定可写 cwd + 容错解码的
       ``subprocess.run`` 包装（COLMAP 会在 cwd 落日志，cwd 不可写时会打印
       ``Could not create logging file``）。

依赖约束：core 不依赖任何业务子包。
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, Optional, Sequence

IS_WINDOWS = sys.platform.startswith("win")

#: Windows 下隐藏子进程控制台窗口的创建标志
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def no_window_kwargs() -> dict:
    """返回隐藏控制台窗口所需的 ``Popen``/``run`` 关键字（非 Windows 为空）。"""
    if IS_WINDOWS:
        return {"creationflags": _CREATE_NO_WINDOW, "close_fds": True}
    return {}


def resolve_cwd(preferred: Optional[str] = None) -> str:
    """返回一个**确定存在且可写**的工作目录。

    外部程序（COLMAP）会在其 cwd 写 ``COLMAP-*.log``；若 cwd 不存在或不
    可写（打包版从快捷方式启动时 cwd 可能失效），会打印
    ``Could not create logging file: No such file or directory`` 噪声日志。
    这里优先用业务给定目录，其次回退系统临时目录。
    """
    import tempfile

    for cand in (preferred, os.path.expanduser("~")):
        if cand and os.path.isdir(cand):
            try:
                probe = os.path.join(cand, ".h3d_cwd_probe")
                with open(probe, "w", encoding="utf-8") as fh:
                    fh.write("ok")
                os.remove(probe)
                return cand
            except OSError:
                continue
    return tempfile.gettempdir()


def run_external(cmd: Sequence[str], *, cwd: Optional[str] = None,
                 timeout: Optional[float] = None,
                 capture_output: bool = True,
                 env: Optional[dict] = None,
                 on_line: Optional[Any] = None) -> subprocess.CompletedProcess:
    """执行外部命令：隐藏窗口 + 保证 cwd 有效 + 解码容错。

    参数
    ----
    cmd:     命令与参数列表（不要用 shell=True，避免注入与引号问题）。
    cwd:     期望的工作目录；不可用时自动回退（见 :func:`resolve_cwd`）。
    timeout: 超时秒数；超时抛 :class:`subprocess.TimeoutExpired`。
    env:     额外环境变量（与当前环境合并）。
    on_line: 逐行回调 ``f(line: str)``。给出时改用 Popen 流式读取，
             stdout 与 stderr 合并后**边跑边回调**（同时仍返回完整输出）。

            为什么需要（2026-10-17 实机）：``patch_match_stereo`` 处理
             30 张 2400px 照片要 50 分钟，原实现 ``subprocess.run`` 期间
             一个字节都不外泄，GUI 只能显示"正在 dense 2/3"，用户判断为卡死。
             COLMAP 本身会打印 ``Processing view 12 / 30``，流式转发后界面
             就能显示真实进度。回调异常一律吞掉——进度显示不得影响主流程。

    返回
    ----
    ``subprocess.CompletedProcess``（stdout/stderr 为 str，非法字节以 U+FFFD 替代）。
    流式模式下 ``stderr`` 为空字符串，全部内容合并进 ``stdout``。
    """
    base: dict[str, Any] = {
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",   # COLMAP 日志含非 UTF-8 字节时不再抛 UnicodeDecodeError
        "cwd": resolve_cwd(cwd),
        **no_window_kwargs(),
    }
    if env:
        merged = dict(os.environ)
        merged.update({k: str(v) for k, v in env.items()})
        base["env"] = merged

    if on_line is None:
        kwargs = dict(base, capture_output=capture_output)
        if timeout is not None:
            kwargs["timeout"] = timeout
        return subprocess.run(list(cmd), **kwargs)

    # ---- 流式模式 ------------------------------------------------------
    import threading

    proc = subprocess.Popen(
        list(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        bufsize=1, **base)
    state = {"timed_out": False}

    def _kill() -> None:
        state["timed_out"] = True
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass

    timer = None
    if timeout:
        timer = threading.Timer(timeout, _kill)
        timer.daemon = True
        timer.start()
    chunks: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            chunks.append(line)
            try:
                on_line(line.rstrip("\r\n"))
            except Exception:  # noqa: BLE001 - 进度回调不得影响主流程
                pass
        rc = proc.wait()
    finally:
        if timer is not None:
            timer.cancel()
    if state["timed_out"]:
        # 带上已收集的输出：超时异常同样需要可诊断（谁算到第几张被打断）。
        # 原实现只抛 (cmd, timeout)，上层无法落盘任何证据，事后只能看到
        # 上一轮残留的旧日志 —— 实机 2026-10-17 因此把「稠密超时」误读成
        # 「需要 CUDA GPU」。
        raise subprocess.TimeoutExpired(list(cmd), timeout or 0,
                                        output="".join(chunks))
    return subprocess.CompletedProcess(list(cmd), rc, "".join(chunks), "")


__all__ = ["IS_WINDOWS", "no_window_kwargs", "resolve_cwd", "run_external"]
