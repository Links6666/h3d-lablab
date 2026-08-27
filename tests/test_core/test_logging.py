"""core.logging 单元测试：级别解析、命名空间、幂等配置。"""
import logging

import pytest

from h3d.core import setup_logging, get_logger
from h3d.core.logging import _resolve_level


def test_setup_returns_root_logger():
    logger = setup_logging("INFO")
    assert isinstance(logger, logging.Logger)
    assert logger.name == "h3d"


def test_get_logger_namespace():
    logger = get_logger("core.config")
    assert logger.name == "h3d.core.config"


def test_get_logger_default():
    assert get_logger().name == "h3d"


def test_resolve_level_case_insensitive():
    assert _resolve_level("debug") == logging.DEBUG
    assert _resolve_level("WARN") == logging.WARNING
    assert _resolve_level("error") == logging.ERROR


def test_invalid_level_raises():
    with pytest.raises(ValueError):
        _resolve_level("NOT_A_LEVEL")


def test_setup_idempotent():
    # 多次调用不应无限叠加 handler（否则日志会重复输出）
    first = setup_logging("INFO")
    n_before = len(first.handlers)
    setup_logging("INFO")
    assert len(first.handlers) == n_before
