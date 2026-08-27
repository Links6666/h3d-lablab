"""h3d.core : 核心底座（P0）—— 配置 / 日志 / 溯源元数据 / 自定义异常。

对外统一暴露公共符号，供业务子包与编排层按需导入：

    from h3d.core import ConfigError, RecoverableError   # 异常
    from h3d.core import PipelineConfig, load             # 配置
    from h3d.core import setup_logging, get_logger        # 日志
    from h3d.core import RunManifest, sha256_file         # 溯源

依赖约束（《管线骨架构建分析》5.2）：core 不依赖任何业务子包，可被单独测试。
"""

from .exceptions import (
    H3DError,
    RecoverableError,
    ConfigError,
    InputDataError,
    RegistrationError,
    QualityGateError,
    ExportError,
    ExternalProcessError,
)
from .config import (
    Field,
    Nested,
    ProjectConfig,
    PreprocessConfig,
    RegisterConfig,
    ChangeConfig,
    ColourConfig,
    RunnerConfig,
    ReportConfig,
    MeshConfig,
    QualityConfig,
    TextureConfig,
    PublishConfig,
    PipelineConfig,
    load,
    loads,
    dump,
)
from .logging import setup_logging, get_logger
from .metadata import (
    sha256_file,
    sha256_bytes,
    sha256_text,
    generate_run_id,
    software_versions,
    RunManifest,
)

__all__ = [
    # 异常
    "H3DError",
    "RecoverableError",
    "ConfigError",
    "InputDataError",
    "RegistrationError",
    "QualityGateError",
    "ExportError",
    "ExternalProcessError",
    # 配置
    "Field",
    "Nested",
    "ProjectConfig",
    "PreprocessConfig",
    "RegisterConfig",
    "ChangeConfig",
    "ColourConfig",
    "RunnerConfig",
    "ReportConfig",
    "MeshConfig",
    "QualityConfig",
    "TextureConfig",
    "PublishConfig",
    "PipelineConfig",
    "load",
    "loads",
    "dump",
    # 日志
    "setup_logging",
    "get_logger",
    # 溯源
    "sha256_file",
    "sha256_bytes",
    "sha256_text",
    "generate_run_id",
    "software_versions",
    "RunManifest",
]
