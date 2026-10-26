"""scripts/sync_source_snapshot.py —— 发行包源码快照同步（镜像式）。

背景
----
发行包里的 ``source/`` 是给评审/复现用的完整可重打包源码。历史上有两个坑：

1. 只做「复制覆盖」不删旧文件 → 已删除/改名的模块残留在快照里，
   拿到快照的人会看到两个 `xxx.py`、两份 `.iss`，不知道哪份有效。
2. 快照里带 ``h3d.egg-info`` / ``__pycache__`` / ``packaging/Output``
   （后者是 236 MB 的旧安装包），直接把发行 zip 撑大。
3. 漏了 ``assets/`` —— 而 ``h3d_gui.spec`` 的 ``datas`` 与 ``icon`` 都指向
   ``assets/h3d_icon_v3.*``，缺了就**重打包失败**。

本脚本按镜像同步：先清空目标目录（仅限 ``release/<当前版本>/source`` 内），
再按白名单复制，并排除缓存与构建产物。版本目录由 ``scripts/_relver.py``
从 ``h3d.__version__`` 推导，不再写死。
"""
from __future__ import annotations

import filecmp
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _relver import REL, REL_NAME  # noqa: E402  单一来源，勿写死版本号

DST = os.path.join(REL, "source")

#: 整目录复制（相对项目根）
DIRS = ["src", "tests", "packaging", "assets", "scripts", "configs"]
#: 单文件复制
FILES = ["h3d_gui.spec", "pyproject.toml", "requirements.txt",
         "README.md", "CHANGELOG.md"]
#: 任何层级都跳过的目录名
SKIP_DIRS = {"__pycache__", ".pytest_cache", "Output", "build", "dist",
             "h3d.egg-info", ".mypy_cache", ".ruff_cache"}
#: 跳过的后缀
SKIP_EXT = {".pyc", ".pyo", ".log", ".tmp"}


def _skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.endswith(".egg-info")


def _copy_tree(src: str, dst: str) -> int:
    n = 0
    for base, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if not _skip_dir(d)]
        rel = os.path.relpath(base, src)
        out = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(out, exist_ok=True)
        for f in files:
            if os.path.splitext(f)[1].lower() in SKIP_EXT:
                continue
            s, d = os.path.join(base, f), os.path.join(out, f)
            if os.path.isfile(d) and filecmp.cmp(s, d, shallow=False):
                continue
            shutil.copy2(s, d)
            n += 1
    return n


def main() -> int:
    if not os.path.isdir(REL):
        print(f"!! 发行目录不存在：{REL}（请先建立 {REL_NAME}）")
        return 2
    if os.path.isdir(DST):
        # 镜像语义：清空后重建。目标被硬限制在 source/ 内。
        assert os.path.basename(DST) == "source", DST
        shutil.rmtree(DST)
    os.makedirs(DST, exist_ok=True)

    total = 0
    for d in DIRS:
        src = os.path.join(ROOT, d)
        if not os.path.isdir(src):
            print(f"  跳过（不存在）：{d}")
            continue
        total += _copy_tree(src, os.path.join(DST, d))
        print(f"  目录 {d}/ 完成")

    for f in FILES:
        src = os.path.join(ROOT, f)
        if not os.path.isfile(src):
            print(f"  跳过（不存在）：{f}")
            continue
        shutil.copy2(src, os.path.join(DST, f))
        total += 1
        print(f"  文件 {f} 完成")

    # 自检：必需要素
    must = ["src/h3d/__init__.py", "h3d_gui.spec",
            "assets/h3d_icon_v3.ico", "assets/h3d_icon_v3.png",
            "packaging/H3D_installer.iss", "tests"]
    miss = [m for m in must if not os.path.exists(os.path.join(DST, m))]
    print(f"共复制 {total} 个文件 -> {DST}")
    if miss:
        print("!! 快照缺失必需项：", miss)
        return 1
    if any("造像" in p for p in os.listdir(DST)):
        print("!! 快照根目录仍有旧称文件")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
