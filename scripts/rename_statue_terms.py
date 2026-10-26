# -*- mode: python ; coding: utf-8 -*-
"""rename_statue_terms.py : 把全项目的「造像」表述统一为「中小型文物」表述。

背景（2026-10-18）：项目定位由「造像类文物（米级）」转向「中小型文物
（5~30 cm）」，界面标题、报表标题、安装包名称、代码注释里残留的「造像」
「尊」等字眼需一并改掉，否则界面/文档自相矛盾。

替换顺序**必须**是「长串在前、单字在后」：
    造像类文物三维建模自动化系统 -> 中小型文物三维建模自动化系统
    造像类文物                  -> 中小型文物
    造像                        -> 文物
    尊                          -> 件

量词「尊」只用于佛像/造像，器物用「件」；全项目扫描确认「尊」仅作量词与
「尊像标识」出现，故可整体替换，但「尊像标识」需先单独处理。

用法：
    python scripts/rename_statue_terms.py --dry-run
    python scripts/rename_statue_terms.py
"""

from __future__ import annotations

import argparse
import os
import sys

ROOTS = ["src", "tests", "packaging", "configs", "scripts"]
FILES = ["README.md", "h3d_gui.spec", "pyproject.toml"]

#: 跳过：发行快照（历史版本不动）、临时产物、记忆日志（历史记录不改写）
SKIP_DIRS = {"release", "output", "docs", "build_out", "build_work",
             ".git", ".venv_lab", ".workbuddy", "__pycache__",
             ".pytest_cache", "dist"}
SKIP_FILES = {"CHANGELOG.md", "rename_statue_terms.py", "make_icon.py"}

#: (旧, 新)，顺序敏感
RULES: list[tuple[str, str]] = [
    # 一、产品全名
    ("造像类文物三维建模自动化系统", "中小型文物三维建模自动化系统"),
    ("H3D 造像数字化自动化系统", "H3D 中小型文物三维建模自动化系统"),
    ("H3D 造像三维建模自动化系统", "H3D 中小型文物三维建模自动化系统"),
    ("H3D 造像类文物", "H3D 中小型文物"),
    ("造像类文物三维建模自动化", "中小型文物三维建模自动化"),
    ("造像数字化", "文物数字化"),
    # 二、领域词
    ("彩塑造像", "彩塑文物"),
    ("造像类文物", "中小型文物"),
    ("示例造像", "示例文物"),
    ("造像（米级）", "大中型文物（米级）"),
    ("造像贴图", "文物贴图"),
    ("造像轮廓", "文物轮廓"),
    ("造像体态", "文物形态"),
    ("造像编号", "文物编号"),
    ("造像标本", "文物标本"),
    # 三、量词（先处理复合词，再单字）
    ("尊像标识", "文物标识"),
    ("尊造像", "件文物"),
    # 四、兜底
    ("造像", "文物"),
]

#: 量词单字替换单独一轮（避免与上面规则互相干扰）
QUANT_RULES: list[tuple[str, str]] = [("尊", "件")]

TEXT_EXT = {".py", ".iss", ".md", ".spec", ".toml", ".yaml", ".yml",
            ".txt", ".json", ".cfg", ".ini"}


def iter_files(root: str):
    for base, dirs, names in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            if n in SKIP_FILES:
                continue
            if os.path.splitext(n)[1].lower() in TEXT_EXT:
                yield os.path.join(base, n)


def target_files() -> list[str]:
    out = []
    for p in FILES:
        if os.path.isfile(p):
            out.append(os.path.normpath(p))
    for r in ROOTS:
        if not os.path.isdir(r):
            continue
        for base, dirs, names in os.walk(r):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for n in names:
                if n in SKIP_FILES:
                    continue
                if os.path.splitext(n)[1].lower() in TEXT_EXT:
                    out.append(os.path.normpath(os.path.join(base, n)))
    return sorted(set(out))


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="造像 -> 中小型文物 术语统一")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    changed, total_hits = [], 0
    for path in target_files():
        try:
            with open(path, "r", encoding="utf-8", newline="") as f:
                src = f.read()
        except UnicodeDecodeError:
            continue
        new, hits = src, 0
        for old, rep in RULES + QUANT_RULES:
            c = new.count(old)
            if c:
                new = new.replace(old, rep)
                hits += c
        if hits and new != src:
            changed.append((path, hits))
            total_hits += hits
            if not args.dry_run:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(new)

    for p, h in changed:
        print(f"{h:4d}  {p}")
    print(f"\n{'[dry-run] ' if args.dry_run else ''}"
          f"{len(changed)} 个文件，{total_hits} 处替换")
    return 0


if __name__ == "__main__":
    sys.exit(main())
