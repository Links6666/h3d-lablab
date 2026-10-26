"""h3d.core.exceptions : 管线自定义异常类层级。

设计依据：《软件开发项目设计书》13.3 错误码规范 与 31.4 错误处理。

层级设计原则（《管线骨架构建分析》5.1 包内依赖）：
    exceptions 是 core 包的最底层，config / logging / metadata 均可依赖它，
    而它本身不依赖任何模块，保证底座可被单独测试、无循环导入。

错误码速查：
    E001  配置缺失 / 非法            (ConfigError)
    E100  输入缺失 / 损坏            (InputDataError, 可恢复)
    E200  配准失败                   (RegistrationError, 可恢复)
    E500  质检不达标                 (QualityGateError, 可恢复)
    E601  导出格式不支持 / 写失败     (ExportError, 可恢复)
    E900  外部子进程 / 磁盘异常       (ExternalProcessError)

可恢复错误（RecoverableError）对应编排层「失败隔离」语义：
    M0 编排器捕获 RecoverableError 时仅跳过该件（记入 manifest 后继续队列），
    不向上抛异常；非可恢复错误（如 ConfigError / ExternalProcessError）则按
    设计书要求中断或走更重的回滚路径。
"""

from __future__ import annotations

from typing import Optional


class H3DError(Exception):
    """管线所有自定义异常的基类。

    统一携带 ``code``（错误码）、``stage``（阶段，如 M1）、``item_id``（文物标识）
    三个上下文字段，便于编排层与日志层结构化记录。
    """

    code: str = "E000"

    def __init__(
        self,
        message: str = "",
        *,
        item_id: Optional[str] = None,
        stage: Optional[str] = None,
        code: Optional[str] = None,
    ) -> None:
        self.item_id = item_id
        self.stage = stage
        if code is not None:
            self.code = code
        super().__init__(self._format(message))

    def _format(self, message: str) -> str:
        parts: list[str] = [f"[{self.code}]"]
        if self.stage:
            parts.append(f"stage={self.stage}")
        if self.item_id:
            parts.append(f"item={self.item_id}")
        if message:
            parts.append(message)
        return " ".join(parts)


class RecoverableError(H3DError):
    """可恢复错误基类：单件失败可跳过，不中断整个批处理队列。

    编排器（M0）仅对继承本类的异常执行「记录 → 跳过该件 → 继续下一个」的
    失败隔离逻辑。
    """


class ConfigError(H3DError):
    """配置错误（E001）：配置文件缺失、字段非法、取值越界等。

    按设计书 31.4，配置缺失时「启动即报错退出」，因此本类为不可恢复错误。
    """

    code = "E001"


class InputDataError(RecoverableError):
    """输入错误（E100）：原始数据缺失、损坏、空点云等。"""

    code = "E100"


class RegistrationError(RecoverableError):
    """配准错误（E200）：重叠不足、RANSAC 失败、闭环误差超阈值等。"""

    code = "E200"


class QualityGateError(RecoverableError):
    """质检不达标（E500）：中误差 / 覆盖率等未通过质量门。"""

    code = "E500"


class ExportError(RecoverableError):
    """导出错误（E601）：目标格式不支持、路径不可写等。"""

    code = "E601"


class ExternalProcessError(H3DError):
    """外部子进程 / 磁盘异常（E900）：如 CloudCompare CLI 调用失败、写盘失败。"""

    code = "E900"


__all__ = [
    "H3DError",
    "RecoverableError",
    "ConfigError",
    "InputDataError",
    "RegistrationError",
    "QualityGateError",
    "ExportError",
    "ExternalProcessError",
]
