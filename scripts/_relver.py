"""scripts/_relver.py —— 发行脚本共用的版本与路径解析（单一来源）。

背景
----
``make_release_zip.py`` / ``sync_source_snapshot.py`` / ``update_release_meta.py``
此前各自把 ``H3D_v1.1.0`` 和 ``v1.1.0`` **写死在常量里**。发下个版本时三处
一起改，漏一处就出现「源码快照进了 1.1.4、zip 名还是 1.1.0」这类静默错误；
更糟的是 ``update_release_meta.py`` 的 ``REV``/``DATE``/``VER`` 还决定了
README/VERSION 里的修订标题，写错会让两个版本文档互相覆盖。

现在统一从 ``src/h3d/__init__.py`` 的 ``__version__`` 推导，
并允许用环境变量 ``H3D_REL_VERSION`` 临时指向历史版本（重打旧包用）。
"""
from __future__ import annotations

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def version() -> str:
    """当前版本号。环境变量 ``H3D_REL_VERSION`` 优先（重打历史版本用）。"""
    env = os.environ.get("H3D_REL_VERSION", "").strip()
    if env:
        return env.lstrip("vV")
    p = os.path.join(ROOT, "src", "h3d", "__init__.py")
    txt = open(p, encoding="utf-8").read()
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', txt)
    if not m:
        raise SystemExit(f"[E] 无法从 {p} 解析 __version__")
    return m.group(1)


#: 不带前缀的版本号，如 ``1.1.4``
VER = version()
#: 带 v 前缀，如 ``v1.1.4``（文档里用）
VTAG = f"v{VER}"
#: 发行目录名与 zip 内顶层目录名，如 ``H3D_v1.1.4``
REL_NAME = f"H3D_v{VER}"
#: 发行目录绝对路径
REL = os.path.join(ROOT, "release", REL_NAME)


def ensure_rel() -> str:
    """确保发行目录存在并返回路径。"""
    os.makedirs(REL, exist_ok=True)
    return REL
