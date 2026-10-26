"""h3d.gui.diag : 全局崩溃诊断（GUI 无声闪退定位）。

打包版 console=False 下异常/原生崩溃完全无声——安装后：
  - sys.excepthook：Python 异常（含 Qt 槽内）写日志文件
  - faulthandler：原生崩溃（C++ segfault）写栈
日志：~/.workbuddy/h3d_gui_crash.log
"""

from __future__ import annotations

import faulthandler
import os
import sys
import traceback
from datetime import datetime

_LOG_PATH = os.path.join(os.path.expanduser("~"), ".workbuddy",
                         "h3d_gui_crash.log")


def _log(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    _log(f"[Python 异常] {text}")
    # 原默认行为：打印后继续（Qt 槽异常不再静默吞掉）
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def install_crash_guard() -> str:
    """安装异常/崩溃钩子；返回日志路径（供提示用户）。"""
    sys.excepthook = _excepthook
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as fh:
            faulthandler.enable(file=fh)
    except Exception:  # noqa: BLE001
        pass
    _log("=== H3D GUI 启动（crash guard 已启用）===")
    return _LOG_PATH


__all__ = ["install_crash_guard", "_LOG_PATH"]
