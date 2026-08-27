"""h3d.core.logging : 分级日志系统（DEBUG / INFO / WARNING / ERROR）。

设计依据：《管线骨架构建分析》4.2 —— 分级日志（DEBUG/INFO/WARN/ERROR）；
《教学版》28.2 —— 生产环境默认 INFO 避免刷屏，落盘 pipeline.log 作为可复现证据。

实现要点：
    1. ``setup_logging`` 一次性配置控制台 + 可选文件双 handler，统一格式：
       ``时间 [级别] [模块名] 消息``。
    2. 通过「是否存在 handler」判断，避免重复调用导致日志重复输出。
    3. ``get_logger`` 返回带 ``h3d`` 前缀的子 logger，供各业务模块按名取用。
    4. 日志文件统一 UTF-8 编码，规避中文路径 / 中文消息乱码。
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_ROOT_LOGGER_NAME = "h3d"

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _resolve_level(level: str) -> int:
    """把字符串级别（大小写不敏感）解析为 logging 常量。"""
    key = str(level).upper()
    if key not in _LEVELS:
        raise ValueError(f"未知日志级别: {level!r}，可选 {sorted(_LEVELS)}")
    return _LEVELS[key]


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    fmt: str = DEFAULT_FORMAT,
    datefmt: str = DATE_FORMAT,
) -> logging.Logger:
    """配置并返回根 logger ``h3d``。

    参数
    ----
    level:    日志级别（DEBUG/INFO/WARNING/ERROR/CRITICAL）。
    log_file: 可选日志文件路径；提供则同时写入文件（UTF-8）。
    fmt:      日志格式字符串。
    datefmt:  时间格式字符串。

    说明：函数幂等——已配置过 handler 时不会重复添加，可安全多次调用。
    """
    root = logging.getLogger(_ROOT_LOGGER_NAME)
    root.setLevel(_resolve_level(level))

    formatter = logging.Formatter(fmt, datefmt)

    if not root.handlers:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        root.addHandler(console)

    if log_file and not any(
        isinstance(h, logging.FileHandler) and h.baseFilename == _abs(log_file)
        for h in root.handlers
    ):
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    return root


def get_logger(name: str = "") -> logging.Logger:
    """获取 ``h3d`` 命名空间下的子 logger。

    示例：``get_logger("core.config")`` -> ``h3d.core.config``。
    """
    if name:
        return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")
    return logging.getLogger(_ROOT_LOGGER_NAME)


def _abs(path: str) -> str:
    import os

    return os.path.abspath(path)


__all__ = ["setup_logging", "get_logger", "DEFAULT_FORMAT", "DATE_FORMAT"]
