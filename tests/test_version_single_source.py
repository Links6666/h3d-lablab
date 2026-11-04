"""版本号只能有一处真源，且必须与 CHANGELOG 对得上。

`src/h3d/__init__.py` 自己写着「版本号唯一来源（2026-10-18 统一）……改版本只改
这里 + pyproject」——**"改两处"从来不是唯一来源**，2026-11-04 就漂了：

    __init__.py   1.1.13   （注释说它是唯一来源）
    pyproject     1.1.12
    CHANGELOG     1.1.16-r22

报表页脚读的是 `h3d.__version__`，于是交付出去的 PDF/HTML 上印的是 1.1.13，
而变更记录最高到 1.1.16——**用户看到的版本号是错的**，这正是"数字能不能引用"
那类缺陷里最难看的一种：连版本都不可信。
"""
import os
import re
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
PYPROJECT = os.path.join(ROOT, "pyproject.toml")


def _pyproject_version():
    """读出 pyproject 的版本：优先 `dynamic`（真单一来源），否则读静态值。"""
    if sys.version_info >= (3, 11):
        import tomllib
        with open(PYPROJECT, "rb") as fh:
            proj = tomllib.load(fh)["project"]
        if "version" in (proj.get("dynamic") or []):
            return "dynamic"
        return proj.get("version")
    with open(PYPROJECT, encoding="utf-8") as fh:      # py3.10 兜底
        text = fh.read()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else None


def _changelog_version():
    with open(CHANGELOG, encoding="utf-8") as fh:
        m = re.search(r"^## v(\d+\.\d+\.\d+)", fh.read(), re.M)
    return m.group(1) if m else None


class TestVersionIsSingleSource:
    def test_pyproject_does_not_carry_its_own_copy(self):
        """pyproject 要么从包读（dynamic），要么与 `__version__` 一致。

        静态写死一份就是第二处真源——它必然会漂，因为没人会记得两处都改。
        """
        import h3d

        got = _pyproject_version()
        assert got == "dynamic" or got == h3d.__version__, (
            f"pyproject 版本 {got} 与 h3d.__version__ {h3d.__version__} 不一致")

    def test_changelog_matches_the_code(self):
        """CHANGELOG 最高版本 == 代码版本。

        不一致说明「改了代码没记变更」或「记了变更没改版本」，两种都会让
        交付物上的版本号失去意义。
        """
        import h3d

        assert _changelog_version() == h3d.__version__, (
            f"CHANGELOG 最新 {_changelog_version()}，代码 {h3d.__version__}")

    def test_version_looks_like_semver(self):
        import h3d

        assert re.fullmatch(r"\d+\.\d+\.\d+", h3d.__version__), h3d.__version__
