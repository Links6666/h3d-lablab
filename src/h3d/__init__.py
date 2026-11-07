"""h3d : 中小型文物三维建模自动化系统（路线 E）顶层包。

对外暴露版本号与核心底座，供 CLI 与报表层引用：

    import h3d
    h3d.__version__

    from h3d import core
"""

#: 版本号**唯一来源**。报表页脚、CLI、`pyproject.toml` 全部从这一行派生
#: （pyproject 用 `dynamic = ["version"]` → `attr = "h3d.__version__"`）。
#:
#: 2026-11-04 修：注释原来写「改版本只改这里 + pyproject」——**"改两处"不是唯一
#: 来源**，实测已漂成 pyproject 1.1.12 / 本文件 1.1.13 / CHANGELOG 1.1.16。
#: 现在只有这一行是真的，另外两处要么派生、要么由测试对着它校验。
#: 改版本时：改这一行 + 在 CHANGELOG 顶部加同号条目（`tests/test_version_single_source.py`
#: 会拦住漏改）。
__version__ = "1.1.26"

__all__ = ["__version__"]
