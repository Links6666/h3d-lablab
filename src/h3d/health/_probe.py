"""h3d.health._probe : 自检用的底层探测原语（全部只读、极短超时）。

设计约束
--------
1. **不 import 重依赖**：这里绝不 ``import open3d`` / ``torch`` / ``PySide6``。
   依赖是否可用用 :func:`module_available`（``importlib.util.find_spec``，只
   查目录不执行模块），否则自检本身要花 3~8 秒，反而把「快速启动」变成负担。
2. **外部命令一律走** :func:`h3d.core.proc.run_external`：隐藏控制台窗口、
   容错解码、cwd 兜底（COLMAP/任务管理器类命令在打包版 console=False 下
   直接 ``subprocess.run`` 会弹黑框）。
3. **任何探测失败都不抛异常**：返回 ``None`` / 空列表，由上层判成 WARNING
   或 INFO。自检工具崩掉比漏检更糟——用户会以为"自检通过了"。

为什么把它单独成文件
--------------------
进程表、GPU、磁盘这些探测在 boot 与 project 两个作用域里都要用，集中一处
方便加缓存（同一次自检内 ``tasklist`` 只跑一次，实测省 120~180 ms）。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from typing import Any, Optional

#: 探测类子进程的默认超时（秒）—— 宁可返回"未知"，也不让启动卡住
PROBE_TIMEOUT = 1.5


# ---------------------------------------------------------------------------
# 文件系统
# ---------------------------------------------------------------------------
def write_probe(directory: str) -> tuple[bool, str]:
    """探测目录是否**可写**（写入并立刻删除一个 1 字节文件）。

    比 ``os.access(W_OK)`` 可靠：Windows 上 ``os.access`` 只看只读属性，对
    ACL 拒绝、磁盘满、OneDrive 占位目录都会给出错误的 True（实机踩过：
    数据根在只读网络盘上，access 通过但一写就 EACCES）。

    返回 ``(ok, reason)``。
    """
    if not directory:
        return False, "目录为空"
    if not os.path.isdir(directory):
        return False, "目录不存在"
    probe = os.path.join(directory, f".h3d_probe_{os.getpid()}.tmp")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.remove(probe)
        return True, ""
    except OSError as exc:
        return False, str(exc)
    finally:
        try:
            if os.path.exists(probe):
                os.remove(probe)
        except OSError:
            pass


def disk_free(path: str) -> Optional[int]:
    """返回 ``path`` 所在卷的可用字节数；失败返回 ``None``。"""
    probe = path
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return None
        probe = parent
    try:
        return int(shutil.disk_usage(probe).free)
    except OSError:
        return None


def dir_names(path: str, limit: int = 500) -> list[str]:
    """一层 ``scandir`` 取子目录名（**不递归**）。

    不允许 ``os.walk``：00_photo 下常有上千张原图，递归扫描会拖慢自检。
    """
    out: list[str] = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir():
                        out.append(entry.name)
                except OSError:
                    continue
                if len(out) >= limit:
                    break
    except OSError:
        return []
    return sorted(out)


def dir_files(path: str, limit: int = 200, exts: Optional[tuple[str, ...]] = None
              ) -> list[str]:
    """一层 ``scandir`` 取文件名（可选扩展名过滤，大小写不敏感）。"""
    out: list[str] = []
    lo = tuple(e.lower() for e in exts) if exts else None
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if not entry.is_file():
                        continue
                except OSError:
                    continue
                if lo and not entry.name.lower().endswith(lo):
                    continue
                out.append(entry.name)
                if len(out) >= limit:
                    break
    except OSError:
        return []
    return sorted(out)


def read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def age_seconds(path: str) -> Optional[float]:
    """文件/目录距现在多少秒；不存在返回 ``None``。"""
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None


# ---------------------------------------------------------------------------
# 依赖可用性（不 import）
# ---------------------------------------------------------------------------
def module_available(name: str) -> bool:
    """模块是否**可被导入**（只查定位，不执行模块代码）。"""
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def exe_path(name: str) -> Optional[str]:
    """在 PATH 中查找可执行文件（带 .exe 后缀兜底）。"""
    found = shutil.which(name)
    if found:
        return found
    if sys.platform.startswith("win") and not name.lower().endswith(".exe"):
        return shutil.which(name + ".exe")
    return None


# ---------------------------------------------------------------------------
# 进程表
# ---------------------------------------------------------------------------
def _fast_cwd() -> str:
    """探测类子进程统一使用的工作目录 = 系统临时目录。

    ``h3d.core.proc.resolve_cwd(None)`` 会回退到 ``~``，而**在用户主目录里
    写一个探针文件实测要 750 ms**（Windows Defender 实时扫描；项目盘 E: 也要
    550 ms，只有 ``%TEMP%`` 是 2 ms）。``run_external`` 每次调用都会做这个写
    探测 —— 直接用它会让 nvidia-smi（227 ms）膨胀到 922 ms，进而挤爆 boot 预算。
    这里显式给 cwd，让探测落在一个快的地方。
    """
    import tempfile

    return tempfile.gettempdir()


def _run(cmd: list, timeout: float = PROBE_TIMEOUT):
    """执行探测命令（隐藏窗口 + 快 cwd）。"""
    from h3d.core.proc import run_external

    return run_external(cmd, cwd=_fast_cwd(), timeout=timeout)


def process_table(cache: Optional[dict] = None) -> list[dict]:
    """返回 ``[{pid, name}]`` 的进程快照（同一次自检内只取一次）。

    三级实现，按速度择优（实测同一台机器，283 个进程）::

        psutil.process_iter    ~17 ms   ← 首选（已随环境安装）
        ctypes EnumProcesses   ~25 ms   ← 兜底（不依赖第三方）
        tasklist               ~1500 ms ← 最后兜底（杀软扫描时会慢到 1.5 s）

    最初直接用 ``tasklist``，实测单这一项就吃掉整个 boot 预算的 150%，
    把「快速自检」变成新的启动负担 —— 这就是先测再写的原因。

    拿不到时返回空列表：**「看不到进程」只应导致漏报**，不应让自检失败。
    不提供 ``cmdline``：``wmic`` 已弃用且 400 ms+，而结束进程只需 PID。
    """
    if cache is not None and "processes" in cache:
        return cache["processes"]
    rows = _processes_psutil()
    if rows is None:
        rows = _processes_ctypes()
    if rows is None:
        rows = _processes_tasklist()
    if cache is not None:
        cache["processes"] = rows
    return rows


def _processes_psutil() -> Optional[list[dict]]:
    try:
        import psutil
    except Exception:  # noqa: BLE001 - 未安装则走下一级
        return None
    rows: list[dict] = []
    try:
        for proc in psutil.process_iter(["pid", "name"]):
            info = proc.info or {}
            name = info.get("name")
            if name:
                rows.append({"pid": int(info.get("pid") or 0), "name": str(name)})
        return rows
    except Exception:  # noqa: BLE001 - 权限不足等，退回下一级
        return None


def _processes_ctypes() -> Optional[list[dict]]:
    """Windows: EnumProcesses + QueryFullProcessImageName（无第三方依赖）。"""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        # 4000 个 PID 足够（系统上限远高于实际进程数），避免二次扩容调用
        arr = (wintypes.DWORD * 4000)()
        need = wintypes.DWORD()
        if not psapi.EnumProcesses(ctypes.byref(arr),
                                   ctypes.sizeof(arr), ctypes.byref(need)):
            return None
        n = int(need.value // 4)
        rows: list[dict] = []
        query_limited = 0x1000
        for pid in list(arr)[:n]:
            if not pid:
                continue
            handle = kernel32.OpenProcess(query_limited, False, pid)
            if not handle:
                continue
            try:
                buf = ctypes.create_unicode_buffer(260)
                size = wintypes.DWORD(260)
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf,
                                                      ctypes.byref(size)):
                    rows.append({"pid": int(pid),
                                 "name": os.path.basename(buf.value)})
            finally:
                kernel32.CloseHandle(handle)
        return rows
    except Exception:  # noqa: BLE001
        return None


def _processes_tasklist() -> list[dict]:
    rows: list[dict] = []
    try:
        if sys.platform.startswith("win"):
            res = _run(["tasklist", "/FO", "CSV", "/NH"])
            for line in (res.stdout or "").splitlines():
                parts = [p.strip().strip('"') for p in line.split('","')]
                if len(parts) < 2:
                    continue
                try:
                    rows.append({"pid": int(parts[1].strip('"')),
                                 "name": parts[0].strip('"')})
                except ValueError:
                    continue
        else:
            res = _run(["ps", "-eo", "pid=,comm="])
            for line in (res.stdout or "").splitlines():
                bits = line.split(None, 1)
                if len(bits) != 2:
                    continue
                try:
                    rows.append({"pid": int(bits[0]),
                                 "name": os.path.basename(bits[1].strip())})
                except ValueError:
                    continue
    except Exception:  # noqa: BLE001 - 取不到就当没有
        rows = []
    return rows


def find_processes(names: tuple[str, ...], cache: Optional[dict] = None,
                   exclude_pid: Optional[int] = None) -> list[dict]:
    """按进程名（小写、忽略 .exe 后缀）筛选进程表。"""
    want = {n.lower().replace(".exe", "") for n in names}
    me = int(exclude_pid if exclude_pid is not None else os.getpid())
    out = []
    for row in process_table(cache):
        name = str(row.get("name") or "").lower().replace(".exe", "")
        if name in want and int(row.get("pid") or 0) != me:
            out.append(row)
    return out


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------
def nvidia_smi(cache: Optional[dict] = None) -> dict:
    """查询 NVIDIA GPU 概况（**单次调用**，实测约 227 ms）。

    返回 ``{"available": bool, "gpus": [...], "error": str}``。
    ``available=False`` 只代表「没查到 NVIDIA GPU / 驱动不可用」，对纯 CPU
    用户不算错误（因此上层给 INFO 而非 WARNING）。

    刻意**不**查 ``--query-compute-apps``：那是第二次独立调用（再 +220 ms），
    而"谁占着 GPU"完全可以用进程表判断（psutil 17 ms），且 ``run_external``
    的 cwd 写探测在并发下会把冷启动放大到 2 s（实测 2015 ms）。GPU 利用率与
    已用显存本身已足够判断"是否被别的任务占着"。
    """
    if cache is not None and "gpu" in cache:
        return cache["gpu"]

    info: dict[str, Any] = {"available": False, "gpus": [], "error": ""}
    exe = exe_path("nvidia-smi")
    if not exe:
        cand = os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"),
                            "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe")
        exe = cand if os.path.isfile(cand) else None
    if not exe:
        info["error"] = "未找到 nvidia-smi（无 NVIDIA 驱动或未装）"
        if cache is not None:
            cache["gpu"] = info
        return info

    try:
        res = _run([exe, "--query-gpu=name,memory.total,memory.used,"
                    "utilization.gpu", "--format=csv,noheader,nounits"])
        if res.returncode == 0:
            for line in (res.stdout or "").strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    info["gpus"].append({
                        "name": parts[0],
                        "mem_total_mb": _num(parts[1]),
                        "mem_used_mb": _num(parts[2]),
                        "util_pct": _num(parts[3]),
                    })
            info["available"] = bool(info["gpus"])
        else:
            tail = (res.stdout or "").strip().splitlines()
            info["error"] = (tail[-1] if tail else "") or "nvidia-smi 返回非 0"
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"nvidia-smi 调用失败：{exc}"
    if cache is not None:
        cache["gpu"] = info
    return info


def _num(text: str) -> Optional[float]:
    try:
        return float(str(text).replace("[N/A]", "").strip())
    except (TypeError, ValueError):
        return None


__all__ = [
    "PROBE_TIMEOUT", "age_seconds", "dir_files", "dir_names", "disk_free",
    "exe_path", "find_processes", "module_available", "nvidia_smi",
    "process_table", "read_json", "write_probe",
]
