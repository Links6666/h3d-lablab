"""h3d : 中小型文物三维建模自动化系统（路线 E）顶层包。

对外暴露版本号与核心底座，供 CLI 与报表层引用：

    import h3d
    h3d.__version__

    from h3d import core
"""

#: 版本号**唯一来源**（2026-10-18 统一）：此前 __version__ 是 0.1.0、
#: pyproject 是 1.0.0、发行包是 v1.1.0、报表页脚还把 0.1.0 写死在模板里，
#: 四处不一致。改版本只改这里 + pyproject，报表/CLI 全部跟着变。
__version__ = "1.1.13"

__all__ = ["__version__"]
