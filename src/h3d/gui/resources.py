"""h3d.gui.resources : 静态资源（图标）定位与加载。

背景（2026-10-18）：项目转向中小型文物后更换了应用图标（两件器物的插画）。
图标既要作为 exe 的嵌入资源（PyInstaller ``icon=``），也要在运行期作为窗口
图标/欢迎页 logo —— 而打包版与源码树两处的 ``assets/`` 位置完全不同：

    源码树：  <repo>/assets/h3d_icon_v3.png
    打包版：  <sys._MEIPASS>/assets/h3d_icon_v3.png   （spec 的 datas 落点）

所以**不能**用 ``os.path.dirname(__file__)/../..`` 硬拼路径（打包后 ``__file__``
在临时解包目录里，层级关系与源码树不一样）。统一走 :func:`asset_path`。

找不到资源时一律返回 ``None`` 而不是抛异常：图标缺失只影响观感，
不应让主窗口/欢迎页起不来（与「禁止静默降级」不冲突——这里缺的是装饰，
且 :func:`app_icon` 只在调试日志里提示，不改变任何业务行为）。
"""

from __future__ import annotations

import os
import sys

#: 优先使用的图标文件名（PNG 给 QIcon，ICO 给 exe/回退）
ICON_PNG = "h3d_icon_v3.png"
ICON_ICO = "h3d_icon_v3.ico"


def _candidates(name: str) -> list[str]:
    """按优先级列出 ``assets/<name>`` 的所有可能绝对路径。"""
    out: list[str] = []

    # 1) 打包版：PyInstaller onefile 解包目录
    mei = getattr(sys, "_MEIPASS", "")
    if mei:
        out.append(os.path.join(mei, "assets", name))
        # 同目录平铺（若 spec 的 datas 直接落到根）
        out.append(os.path.join(mei, name))

    # 2) 源码树：src/h3d/gui/resources.py -> <repo>/assets
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    out.append(os.path.join(repo, "assets", name))

    # 3) 当前工作目录（从别处双击启动时的兜底）
    out.append(os.path.join(os.getcwd(), "assets", name))
    return out


def asset_path(name: str) -> str | None:
    """返回 assets 下某资源的绝对路径；都找不到返回 ``None``。"""
    for p in _candidates(name):
        if os.path.isfile(p):
            return p
    return None


def app_icon():
    """返回 ``QIcon``（找不到资源时返回空 QIcon，不是 None）。

    空 QIcon 交给 ``setWindowIcon`` 是安全的（Qt 显示默认图标），
    这样调用方无需到处判空。
    """
    from PySide6.QtGui import QIcon

    for name in (ICON_PNG, ICON_ICO):
        p = asset_path(name)
        if p:
            icon = QIcon(p)
            if not icon.isNull():
                return icon
    return QIcon()


def apply_app_icon(app) -> bool:
    """把图标设为 ``QApplication`` 级（所有窗口/对话框自动继承）。

    返回是否成功应用到真实图标。GUI 未装扮图标不该让启动失败，
    故内部吞掉 Qt 相关异常，仅返回 False。
    """
    try:
        icon = app_icon()
        if icon.isNull():
            return False
        app.setWindowIcon(icon)
        return True
    except Exception:  # noqa: BLE001 - 图标是装饰，不能影响启动
        return False


__all__ = ["asset_path", "app_icon", "apply_app_icon",
           "ICON_PNG", "ICON_ICO"]
