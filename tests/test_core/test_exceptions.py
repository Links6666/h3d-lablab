"""core.exceptions 单元测试：层级、错误码、消息格式化、可恢复性判定。"""
import pytest

from h3d.core.exceptions import (
    H3DError,
    RecoverableError,
    ConfigError,
    InputDataError,
    RegistrationError,
    QualityGateError,
    ExportError,
    ExternalProcessError,
)


def test_hierarchy():
    assert issubclass(RecoverableError, H3DError)
    assert issubclass(ConfigError, H3DError)
    assert issubclass(InputDataError, RecoverableError)
    assert issubclass(RegistrationError, RecoverableError)
    assert issubclass(QualityGateError, RecoverableError)
    assert issubclass(ExportError, RecoverableError)
    assert issubclass(ExternalProcessError, H3DError)


def test_error_codes():
    assert ConfigError.code == "E001"
    assert InputDataError.code == "E100"
    assert RegistrationError.code == "E200"
    assert QualityGateError.code == "E500"
    assert ExportError.code == "E601"
    assert ExternalProcessError.code == "E900"


def test_message_format():
    exc = InputDataError("空点云", item_id="rock001", stage="M1")
    text = str(exc)
    assert "[E100]" in text
    assert "rock001" in text
    assert "M1" in text
    assert "空点云" in text


def test_recoverable_vs_fatal():
    # 可恢复：输入/配准/质检/导出；不可恢复：配置/外部子进程
    assert issubclass(InputDataError, RecoverableError)
    assert not issubclass(ConfigError, RecoverableError)
    assert not issubclass(ExternalProcessError, RecoverableError)


def test_override_code():
    exc = H3DError("generic", code="E999")
    assert exc.code == "E999"
