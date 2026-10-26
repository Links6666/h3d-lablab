"""scripts/build_installer.py —— 编译 Inno Setup 安装包（版本自动注入）。

为什么需要
----------
``packaging/H3D_installer.iss`` 里有两处必须与版本一致的信息：

* ``MyAppVersion`` —— 决定安装包文件名、AppVersion、以及两处
  ``VersionInfo*`` 四段数值；
* ``MyAppDir``  —— 决定从哪个 ``release/H3D_v<版本>`` 目录取 exe 与文档。

手工改这两处必然漏改（2026-10-18 就出现过「安装包装的是 1.1.0、发行目录
已是 1.1.4」）。本脚本从 ``h3d.__version__`` 取值，用 ISCC 的 ``/D`` 注入，
并做三件事前检查：

1. 发行目录存在且含 H3D.exe；
2. ``H3D.exe`` 的 PE 文件版本与 ``__version__`` 一致（防止把上一版 exe
   打进新版安装包 —— 命令行 ``--version`` 与属性页可能不同源）；
3. 编译后校验产物存在、体积合理，再复制进发行目录。

用法：
    python scripts/build_installer.py            # 编译 + 复制
    python scripts/build_installer.py --dry-run  # 只打印将执行的命令
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _relver import REL, VER  # noqa: E402  单一来源

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ISS = os.path.join(ROOT, "packaging", "H3D_installer.iss")
OUT_DIR = os.path.join(ROOT, "packaging", "Output")
#: Inno Setup 7 编译器（本机固定位置；可用环境变量 H3D_ISCC 覆盖）
ISCC = os.environ.get(
    "H3D_ISCC", r"D:\h3d_relic_test\InnoSetup7\ISCC.exe")


def pe_version(path: str) -> str | None:
    """读 Windows PE 的文件版本（四段）。非 Windows / 无版本资源返回 None。"""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001 - 非 Windows 平台
        return None
    ver = ctypes.WinDLL("version")
    ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR,
                                            ctypes.POINTER(wintypes.DWORD)]
    ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                        wintypes.DWORD, ctypes.c_void_p]
    ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                   ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.POINTER(wintypes.UINT)]
    size = ver.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(path, 0, size, buf):
        return None
    ptr = ctypes.c_void_p()
    ln = wintypes.UINT()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(ln)):
        return None

    class VS(ctypes.Structure):        # VS_FIXEDFILEINFO 前 4 个 DWORD
        _fields_ = [("sig", wintypes.DWORD), ("struc", wintypes.DWORD),
                    ("ms", wintypes.DWORD), ("ls", wintypes.DWORD)]

    fi = ctypes.cast(ptr, ctypes.POINTER(VS)).contents
    return (f"{fi.ms >> 16}.{fi.ms & 0xFFFF}."
            f"{fi.ls >> 16}.{fi.ls & 0xFFFF}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    exe = os.path.join(REL, "H3D.exe")
    if not os.path.isfile(exe):
        print(f"[E] 发行目录里没有 H3D.exe：{exe}")
        print("    请先把打包产物复制为 release/H3D_v<版本>/H3D.exe")
        return 2
    if not os.path.isfile(ISCC):
        print(f"[E] 找不到 ISCC：{ISCC}（可用 H3D_ISCC 环境变量指定）")
        return 2

    got = pe_version(exe)
    want = VER + ".0"
    if got and got != want:
        print(f"[E] PE 文件版本 {got} 与 __version__ {want} 不一致 —— "
              "会把上一版 exe 打进新版安装包，请重新打包 exe")
        return 3
    print(f"exe 版本校验通过：{got or '（无版本资源，跳过）'}")

    cmd = [ISCC,
           f"/DMyAppVersion={VER}",
           f"/DMyAppDir={REL.replace(os.sep, '/')}",
           "/Qp", ISS]
    print("执行：", " ".join(cmd))
    if a.dry_run:
        return 0
    rc = subprocess.call(cmd)
    if rc != 0:
        print(f"[E] ISCC 返回 {rc}")
        return rc

    setup = os.path.join(OUT_DIR, f"H3D-Setup-v{VER}.exe")
    if not os.path.isfile(setup):
        print(f"[E] 未生成安装包：{setup}")
        return 4
    dst = os.path.join(REL, os.path.basename(setup))
    shutil.copy2(setup, dst)
    mb = os.path.getsize(dst) / 1024 / 1024
    if mb < 50:
        print(f"[E] 安装包只有 {mb:.1f} MB，明显不完整")
        return 5
    print(f"安装包已就位：{dst}（{mb:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
