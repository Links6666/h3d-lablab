"""阶段目录名只能有一份登记表，且代码里出现的名字都必须在表里。

真实事故（2026-11-03）：`paths._STAGE_CHILDREN`（迁移清单）漏了
`10_clean` / `20_registered` / `60_qc`，而 `cli` 的初始化清单**漏了 `10_dense`**
——两张手写清单漂向**相反**的方向。迁移时前者把三个当前阶段目录留在项目根，
后者根本不会给新项目建 `10_dense`。

这类缺陷靠"下次记得"防不住，只能靠**结构**：目录名登记一次，两处都从它派生，
再加一条守卫测试——代码里出现未登记的逻辑名就红。

接缝：``h3d.core.paths.STAGE_DIRS``（登记表）与 ``_STAGE_CHILDREN``（派生）。
"""
import os
import re

import pytest

from h3d.core import paths

SRC = os.path.join(os.path.dirname(__file__), "..", "src", "h3d")
#: 形如 00_raw / 10_clean / 80_export 的目录名（两位数字 + 下划线 + 小写）
_DIR_LITERAL = re.compile(r"""["'](\d{2}_[a-z]{3,})["']""")


def _literals_in_source():
    out = {}
    for dp, _dn, fns in os.walk(SRC):
        for f in fns:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            with open(p, encoding="utf-8", errors="ignore") as fh:
                for i, line in enumerate(fh, 1):
                    for m in _DIR_LITERAL.finditer(line):
                        out.setdefault(m.group(1), []).append(
                            f"{os.path.relpath(p, SRC)}:{i}")
    return out


class TestRegistry:
    def test_registry_is_ordered_and_unique(self):
        names = list(paths.STAGE_DIRS.values())
        assert len(names) == len(set(names)), f"登记表里目录名重复：{names}"
        # 顺序要稳定（迁移按它搬，初始化按它建）
        assert names == list(dict.fromkeys(names))

    def test_every_registered_dir_is_migrated(self):
        """登记表里的每个目录都必须出现在迁移清单里。

        反过来的漏洞（登记了但迁移漏了）就是 10_clean 那次。
        """
        missing = [d for d in paths.STAGE_DIRS.values()
                   if d not in paths._STAGE_CHILDREN]
        assert not missing, f"这些目录登记了却不在迁移清单里：{missing}"

    def test_migration_list_has_no_unregistered_dir(self):
        """迁移清单里不许出现登记表以外的目录（避免清单悄悄加东西）。"""
        extra = [d for d in paths._STAGE_CHILDREN
                 if d not in paths.STAGE_DIRS.values()]
        assert not extra, f"迁移清单里有未登记的目录：{extra}"


class TestSourceLiteralsAreRegistered:
    def test_no_unknown_stage_dir_literal(self):
        """代码里出现的阶段目录名，必须都在登记表（或旧布局别名）里。

        新增一个 `40_xxx` 目录却忘了登记 → 迁移与初始化都会漏掉它，
        而这正是这次事故的形状。
        """
        allowed = set(paths.STAGE_DIRS.values()) | {paths.LEGACY_EXPORT_DIR}
        found = _literals_in_source()
        unknown = {d: locs for d, locs in found.items() if d not in allowed}
        assert not unknown, (
            "源码里出现了未登记的阶段目录名："
            + "；".join(f"{d} @ {', '.join(l[:3])}" for d, l in unknown.items()))

    def test_guard_actually_sees_the_literals(self):
        """守卫自己得能看见东西——否则一个空扫描会让上面那条永远绿。"""
        found = _literals_in_source()
        assert "10_clean" in found or "30_dense" in found, sorted(found)


class TestInitialiseUsesRegistry:
    def test_init_creates_exactly_the_registry(self, tmp_path):
        """`h3d init` 建的目录集合 == 登记表（此处曾漏 10_dense）。"""
        from h3d.cli import main

        assert main(["init", "-p", str(tmp_path)]) == 0
        stage = paths.stage_root(str(tmp_path))
        got = {d for d in os.listdir(stage) if os.path.isdir(os.path.join(stage, d))}
        want = set(paths.STAGE_DIRS.values())
        assert got == want, f"少了 {want - got}；多了 {got - want}"
