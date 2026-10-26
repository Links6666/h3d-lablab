"""h3d.core.lockfile : 数据根单实例运行锁（防并发互删）。

实机背景（2026-10-17 实机，代价 3 小时）
----------------------------------------
用户在同一数据根 ``E:\\兵马俑照片`` 上先后启动了**两个**批处理进程
（``H3D.exe --batch``，PID 26048 与 26332）。二者轮流处理同 4 件文物：

1. 新进程进入 S1 时执行 ``_reset_sparse_state(db, sparse, dense)``，把
   ``<workspace>/dense`` 整个删掉；旧进程的 ``patch_match_stereo`` 恰好在写
   最后一张深度图，于是 COLMAP 报::

       Check failed: (file).is_open() Could not open ".../DSC_0037.JPG.geometric.bin"
       Could not create logging file: No such file or directory

   旧进程 69 分钟的稠密计算瞬间作废。
2. 两个 ``patch_match_stereo`` 同时抢同一块 GPU：单张深度图从 ~45 s 退化到
   87 s（第一遍）/ 145 s（第二遍），稠密总耗时翻 2~3 倍。
3. 两个进程各自写 ``run_manifest.json``（无锁、非原子），失败记录互相覆盖，
   用户看到的失败原因来自**另一个进程的旧日志**。

本模块提供「同一数据根同时只允许一个批处理进程」的互斥锁。锁为
``<root>/.h3d_run.lock``（JSON），用 ``O_EXCL`` 抢占，进程退出时删除；
持有者已死（崩溃/被强杀）则视为陈旧锁自动接管，不会把用户永久锁在门外。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Optional

#: 锁文件名（放在数据根，与 00_raw / 99_runs 同级，便于用户自查）
LOCK_NAME = ".h3d_run.lock"

_STILL_ACTIVE = 259


class LockBusyError(RuntimeError):
    """数据根已被另一个仍在运行的批处理进程占用。"""

    def __init__(self, message: str, holder: Optional[dict] = None) -> None:
        super().__init__(message)
        self.holder = holder or {}


def _pid_alive(pid: int) -> bool:
    """判断进程是否仍存活（跨平台；不可查询一律按『已死』处理）。

    Windows 下 ``os.kill(pid, 0)`` 会直接 TerminateProcess，**绝不能**用；
    改用 ``OpenProcess`` + ``GetExitCodeProcess == STILL_ACTIVE``。
    """
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED
            if not handle:
                return False
            try:
                code = ctypes.c_ulong(0)
                ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                return bool(ok) and code.value == _STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001 - 查询失败按已死处理（宁可不阻塞用户）
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _read_holder(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


class RunLock:
    """数据根运行锁（上下文管理器）。

    用法::

        with acquire_run_lock(root) as lock:
            ...   # 独占运行
    """

    def __init__(self, path: str, holder: dict) -> None:
        self.path = path
        self.holder = holder
        self._released = False

    # -- 生命周期 ----------------------------------------------------------
    def release(self) -> None:
        """删除锁文件（仅删除自己持有的那份）。"""
        if self._released:
            return
        self._released = True
        try:
            cur = _read_holder(self.path)
            # 防止「接管者刚写入、被释放者误删」：只删 pid 与 token 都匹配的
            if cur.get("token") and cur.get("token") != self.holder.get("token"):
                return
            os.remove(self.path)
        except OSError:
            pass

    def __enter__(self) -> "RunLock":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    # -- 诊断 --------------------------------------------------------------
    def describe_holder(self) -> str:
        h = self.holder or {}
        pid = h.get("pid", "?")
        started = h.get("started_at", "?")
        return f"PID {pid}（启动于 {started}）"


def acquire_run_lock(root: str, argv: Optional[list[str]] = None) -> RunLock:
    """在 ``root`` 上取得单实例锁。

    返回已持有的 :class:`RunLock`；若另一**存活**进程持有则抛
    :class:`LockBusyError`（消息已是可直接展示给用户的中文）。
    持有者已死（崩溃残留）时自动接管，不阻塞用户。
    """
    if not root:
        raise ValueError("acquire_run_lock 需要非空数据根目录")
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, LOCK_NAME)
    holder = {
        "pid": os.getpid(),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "argv": list(argv or []),
        "token": f"{os.getpid()}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
    }
    payload = json.dumps(holder, ensure_ascii=False, indent=2)

    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        old = _read_holder(path)
        old_pid = int(old.get("pid") or 0)
        if old_pid and old_pid != os.getpid() and _pid_alive(old_pid):
            raise LockBusyError(
                "该数据目录已有批处理进程在运行："
                f"{RunLock(path, old).describe_holder()}。\n"
                "同时运行两个批处理会互相删除中间产物、争抢 GPU，"
                "导致双方都跑不完（实机 2026-10-17 已复现）。\n"
                "请等待其结束，或先停止它（任务管理器结束 H3D.exe / colmap.exe）"
                "后重试。",
                holder=old)
        # 陈旧锁（崩溃残留）→ 接管
        try:
            os.remove(path)
        except OSError:
            pass
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # 极小概率的抢占竞争：重读一次，仍存活则如实报忙
            again = _read_holder(path)
            if _pid_alive(int(again.get("pid") or 0)):
                raise LockBusyError(
                    "该数据目录已有批处理进程在运行："
                    f"{RunLock(path, again).describe_holder()}。",
                    holder=again)
            fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    return RunLock(path, holder)


__all__ = ["LOCK_NAME", "LockBusyError", "RunLock", "acquire_run_lock"]
