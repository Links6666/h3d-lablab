"""scripts/make_version_info.py —— 由 ``h3d.__version__`` 生成 PE 版本资源。

为什么要脚本化
--------------
``packaging/version_info.txt`` 是 PyInstaller 写进 Windows PE 的
VS_VERSIONINFO（右键属性 → 详细信息 → 文件版本）。它是**手抄的第二份
版本号**，于是必然漂移：

* 2026-10-23 实测：``h3d.__version__`` 与 pyproject 都已改成 1.1.4，
  命令行 ``H3D.exe --version`` 也输出 ``h3d 1.1.4``，但
  ``version_info.txt`` 还停在 1.1.3.0 —— 打出来的 1.1.4 安装包在系统
  属性页里显示 **1.1.3**，用户无法确认手上是哪个版本。
* 命令行自证与 PE 资源互相矛盾时，用户只会相信属性页。

所以版本资源不再手写：``h3d_gui.spec`` 在打包时调用本模块的
``build()`` 重新生成它，``__version__`` 是唯一来源。

用法：
    python scripts/make_version_info.py            # 生成
    python scripts/make_version_info.py --check    # 只校验是否与 __version__ 一致
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "packaging", "version_info.txt")

#: 版本号只允许数字段（PE 的 filevers 是 4 个 WORD，非数字会编译失败）
_VRE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:\.(\d+))?")

_TEMPLATE = """\
# PyInstaller 版本资源（Windows PE 的 VS_VERSIONINFO）
#
# ！！本文件由 scripts/make_version_info.py 自动生成，请勿手工编辑 ！！
# 版本号唯一来源是 src/h3d/__init__.py 的 __version__；
# h3d_gui.spec 在打包开始前会重新生成它，因此不会出现
# 「命令行说 1.1.4、属性页说 1.1.3」这类漂移。
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vers},
    prodvers={vers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404b0',
        [StringStruct('CompanyName', '文保数字化研发组'),
         StringStruct('FileDescription', 'H3D 中小型文物三维建模自动化系统'),
         StringStruct('FileVersion', '{ver4}'),
         StringStruct('InternalName', 'H3D'),
         StringStruct('OriginalFilename', 'H3D.exe'),
         StringStruct('ProductName', 'H3D 中小型文物三维建模自动化系统'),
         StringStruct('ProductVersion', '{ver4}'),
         StringStruct('LegalCopyright', '完全开源'),
         StringStruct('Comments', '激光几何 + SfM 照片纹理，GUI 向导式零命令行')]),
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])]),
  ],
)
"""


def current_version() -> str:
    """读取 ``h3d.__version__``，不 import 整个包（避免拉起重依赖）。"""
    p = os.path.join(ROOT, "src", "h3d", "__init__.py")
    txt = open(p, encoding="utf-8").read()
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', txt)
    if not m:
        raise SystemExit(f"[E] 无法从 {p} 解析 __version__")
    return m.group(1)


def as_quad(ver: str) -> tuple[int, int, int, int]:
    m = _VRE.match(ver.strip())
    if not m:
        raise SystemExit(f"[E] 版本号不是数字点分格式：{ver!r}")
    parts = [int(g) if g else 0 for g in m.groups()]
    return tuple(parts)  # type: ignore[return-value]


def build(ver: str | None = None) -> str:
    ver = ver or current_version()
    quad = as_quad(ver)
    text = _TEMPLATE.format(vers=quad, ver4=".".join(map(str, quad)))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    # 无 BOM、LF：PyInstaller 的版本文件解析器不接受编码前缀
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return ver


def matches() -> bool:
    """现有文件是否已与 ``__version__`` 一致（用于 CI/打包前置断言）。"""
    if not os.path.exists(OUT):
        return False
    txt = open(OUT, encoding="utf-8").read()
    m = re.search(r"filevers=\(([^)]*)\)", txt)
    if not m:
        return False
    got = tuple(int(x) for x in m.group(1).replace(" ", "").split(","))
    return got == as_quad(current_version())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="只校验版本资源是否与 __version__ 一致")
    a = ap.parse_args()
    if a.check:
        ok = matches()
        print(f"version_info 与 __version__ {current_version()} "
              f"{'一致' if ok else '不一致（需重新生成）'}")
        return 0 if ok else 1
    ver = build()
    print(f"已生成 {OUT}（版本 {'.'.join(map(str, as_quad(ver)))}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
