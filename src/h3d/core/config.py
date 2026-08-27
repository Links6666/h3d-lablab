"""h3d.core.config : 参数校验与配置加载（dataclass + 描述符元编程 + yaml 解析）。

设计依据：
    《管线骨架构建分析》4.2 —— config.py 以「dataclass + 描述符元编程」实现参数校验与 yaml 解析；
    《设计书》12.2 —— 配置 schema 分层（project / preprocess / register / change / colour / runner）；
    《教学版》13.2 —— 解析统一使用 ``yaml.safe_load``（防代码注入）。

实现要点：
    1. 描述符 ``Field`` 在 ``__set__`` 阶段完成类型 / 取值校验，任何 ``cfg.attr = v`` 赋值
       或构造期赋值都会被拦截校验，杜绝非法值进入运行期。
    2. ``Nested`` 描述符承载子配置对象，实现配置的层级化。
    3. 所有配置类继承 ``_ConfigBase``：由 ``__init_subclass__`` 扫描描述符声明，
       自动生成 ``__init__`` / ``to_dict`` / ``from_dict``，避免逐类手写样板。
    4. ``load()`` 为对外入口，读取 YAML 并返回 ``PipelineConfig``。

依赖方向：仅依赖 ``h3d.core.exceptions.ConfigError``，符合「core 底座自洽」约束。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional, Type

import yaml

from .exceptions import ConfigError

_REQUIRED = object()  # 无默认值的哨兵


# ---------------------------------------------------------------------------
# 描述符：赋值期校验（描述符元编程）
# ---------------------------------------------------------------------------
class _Descriptor:
    """描述符基类：约定 ``__set_name__`` 记录存储名，子类实现校验逻辑。"""

    _name: str

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def __get__(self, obj: Any, objtype: Optional[type] = None) -> Any:
        if obj is None:  # 类级访问，返回描述符本身
            return self
        return obj.__dict__.get(self._name)

    def __set__(self, obj: Any, value: Any) -> None:
        obj.__dict__[self._name] = self.validate(value)

    def validate(self, value: Any) -> Any:  # pragma: no cover - 由子类覆盖
        return value


class Field(_Descriptor):
    """标量配置字段描述符：类型 + 取值范围 + 可选枚举校验。

    参数
    ----
    dtype:    目标类型（如 float / int / str / bool / list）；赋值时可安全转换。
    default:  默认值；未提供则字段为必填。
    low/high: 数值取值闭区间下限 / 上限。
    choices:  可选取值集合（枚举校验）。
    nullable: 是否允许 None。
    """

    def __init__(
        self,
        *,
        dtype: Optional[type] = None,
        default: Any = _REQUIRED,
        low: Optional[float] = None,
        high: Optional[float] = None,
        choices: Optional[Any] = None,
        nullable: bool = False,
    ) -> None:
        self.dtype = dtype
        self.default = default
        self.low = low
        self.high = high
        self.choices = choices
        self.nullable = nullable

    def validate(self, value: Any) -> Any:
        if value is None:
            if self.nullable:
                return None
            raise ConfigError(f"字段 {self._name} 不允许为 None")

        if self.dtype is not None and not isinstance(value, self.dtype):
            try:
                value = self.dtype(value)
            except (TypeError, ValueError) as exc:
                raise ConfigError(
                    f"字段 {self._name} 类型错误：期望 {self.dtype.__name__}，"
                    f"实际 {type(value).__name__}"
                ) from exc

        if self.low is not None and value < self.low:
            raise ConfigError(f"字段 {self._name} 取值 {value!r} 低于下限 {self.low!r}")
        if self.high is not None and value > self.high:
            raise ConfigError(f"字段 {self._name} 取值 {value!r} 高于上限 {self.high!r}")
        if self.choices is not None and value not in self.choices:
            raise ConfigError(
                f"字段 {self._name} 取值 {value!r} 不在允许集合 {list(self.choices)!r}"
            )
        return value


class Nested(_Descriptor):
    """嵌套配置描述符：值为指定配置类的实例（或可转换的 dict）。"""

    def __init__(self, config_cls: Type["_ConfigBase"], default_factory: Callable[[], Any]) -> None:
        self.config_cls = config_cls
        self.default_factory = default_factory

    def validate(self, value: Any) -> "_ConfigBase":
        if isinstance(value, self.config_cls):
            return value
        if isinstance(value, dict):
            return self.config_cls.from_dict(value)
        raise ConfigError(
            f"字段 {self._name} 类型错误：期望 {self.config_cls.__name__} 或 dict，"
            f"实际 {type(value).__name__}"
        )


# ---------------------------------------------------------------------------
# 配置基类：由描述符自动派生 __init__ / to_dict / from_dict
# ---------------------------------------------------------------------------
class _ConfigBase:
    """配置基类：扫描类上的描述符声明，自动生成构造与序列化逻辑。"""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # init=False：构造逻辑由本类的 __init__ 统一驱动（描述符校验），
        # dataclass 仅补充 repr / eq / asdict 兼容，避免覆盖 __init__ 导致默认值失效。
        dataclass(cls, init=False)

    def __init__(self, **kwargs: Any) -> None:
        descriptors = self._descriptors()
        unknown = set(kwargs) - set(descriptors)
        if unknown:
            raise ConfigError(f"未知参数: {sorted(unknown)}")
        for name, desc in descriptors.items():
            if name in kwargs:
                setattr(self, name, kwargs[name])  # 触发描述符校验
            elif isinstance(desc, Nested):
                setattr(self, name, desc.default_factory())
            elif desc.default is not _REQUIRED:
                # 可变默认值深拷贝，避免实例间共享
                default = copy.deepcopy(desc.default) if isinstance(
                    desc.default, (list, dict, set)
                ) else desc.default
                setattr(self, name, default)
            else:
                raise ConfigError(f"缺少必填参数: {name}")

    @classmethod
    def _descriptors(cls) -> dict[str, _Descriptor]:
        return {
            name: attr
            for name, attr in vars(cls).items()
            if isinstance(attr, _Descriptor)
        }

    @classmethod
    def _iter_fields(cls) -> Iterator[tuple[str, _Descriptor]]:
        yield from cls._descriptors().items()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "_ConfigBase":
        """从字典构造配置对象（嵌套 dict 由 ``Nested`` 描述符自动递归转换）。"""
        if not isinstance(data, dict):
            raise ConfigError(f"{cls.__name__}.from_dict 需要 dict，实际 {type(data).__name__}")
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        """递归序列化为普通字典（可写回 YAML / JSON）。"""
        result: dict[str, Any] = {}
        for name, _ in self._iter_fields():
            value = getattr(self, name)
            result[name] = value.to_dict() if isinstance(value, _ConfigBase) else value
        return result


# ---------------------------------------------------------------------------
# 各配置段（字段名与 configs/example.yaml 严格对齐）
# ---------------------------------------------------------------------------
class ProjectConfig(_ConfigBase):
    name: str = Field(dtype=str, default="")
    operator: str = Field(dtype=str, default="")
    raw_dir: str = Field(dtype=str, default="data/raw")
    formats: list = Field(default=["ply", "pcd", "obj"])


class PreprocessConfig(_ConfigBase):
    voxel_down_sample: float = Field(dtype=float, default=0.005, low=0.0)
    remove_outlier_std: float = Field(dtype=float, default=2.0, low=0.0)
    normal_estimate_k: int = Field(dtype=int, default=30, low=1)


class RegisterConfig(_ConfigBase):
    fpfh_radius: float = Field(dtype=float, default=0.02, low=0.0)
    ransac_distance: float = Field(dtype=float, default=0.05, low=0.0)
    icp_distance: float = Field(dtype=float, default=0.02, low=0.0)
    max_iter: int = Field(dtype=int, default=2000, low=1)


class ChangeConfig(_ConfigBase):
    """多时相形变检测参数（X1 M3C2，可选模块，默认关闭）。

    设计依据：《设计书》§38（x1.cores=4 / x1.change_mm=2.0）、
    《软著方案》2.4（normal_radius_k / cyl_radius_k / max_distance_k / reg_error）。

    尺度类参数以 MPS（平均点间距）的倍数表达（*_k 后缀，软著方案 2.7.2 尺度自适应）：
        normal_radius_k: M3C2 法线搜索半径 = k × MPS（默认 10 倍）。
        cyl_radius_k:    圆柱投影半径 = k × MPS（默认 3 倍）。
        max_distance_k:  最大搜索距离 = k × MPS（默认 50 倍）。
    """

    enabled: bool = Field(dtype=bool, default=False)
    cores: int = Field(dtype=int, default=4, low=1)
    change_mm: float = Field(dtype=float, default=2.0, low=0.0)
    normal_radius_k: float = Field(dtype=float, default=10.0, low=0.0)
    cyl_radius_k: float = Field(dtype=float, default=3.0, low=0.0)
    max_distance_k: float = Field(dtype=float, default=50.0, low=0.0)
    registration_error: float = Field(dtype=float, default=0.0012, low=0.0)
    grid: list = Field(default=[32, 32])  # 核心点网格分辨率（nx, ny）
    core_radius: float = Field(dtype=float, default=0.05, low=0.0)  # 兼容旧字段


class ColourConfig(_ConfigBase):
    """色彩量化参数（X2 色差，可选模块，默认关闭）。

    设计依据：《设计书》§39（x2.illuminant=D65）、
    《软著方案》2.4（colourchecker=ColorChecker24 / delta_e_method=CIE2000）。

    use_calibration: 是否先用 ColorChecker24 实测色块对参考色块做线性校准。
    jnd_threshold:   JND（恰可察觉差）阈值，默认 2.3（CIE2000 单位）。
    """

    enabled: bool = Field(dtype=bool, default=False)
    illuminant: str = Field(dtype=str, default="D65")
    delta_e_method: str = Field(dtype=str, default="CIE2000",
                                choices=["CIE2000", "CIE1976", "CIE1994"])
    use_calibration: bool = Field(dtype=bool, default=True)
    jnd_threshold: float = Field(dtype=float, default=2.3, low=0.0)


class RunnerConfig(_ConfigBase):
    """批量调度参数（《软著方案》2.8 runtime 段，映射到本项目的 runner 段）。"""

    fail_isolation: bool = Field(dtype=bool, default=True)
    resume: bool = Field(dtype=bool, default=True)
    manifest: str = Field(dtype=str, default="output/run_manifest.json")
    workers: int = Field(dtype=int, default=1, low=1)


class ReportConfig(_ConfigBase):
    """报表输出参数（《软著方案》2.8 report 段 / 设计书 M6）。

    formats:    输出格式（csv / png / html，可组合）。
    output_dir: 报表输出目录。
    project_name: 报表展示的项目名（CLI 传入 cfg.project.name）。
    """

    enabled: bool = Field(dtype=bool, default=True)
    formats: list = Field(default=["csv", "png", "html"])
    output_dir: str = Field(dtype=str, default="output/report")
    project_name: str = Field(dtype=str, default="")


class MeshConfig(_ConfigBase):
    """网格重建参数（《设计书》§34.3 M3 稠密重建 / 教学版第 17 章）。

    method:         重建方法 poisson / ball_pivoting / alpha_shape。
    poisson_depth:  泊松重建深度（越大越细越慢，千万级点云取 8-10）。
    density_ratio:  密度裁剪分位数（剔除低密度离群碎面，设计书 TC-303）。
    ball_radii:     Ball-Pivoting 多球半径列表（米）。
    alpha:          Alpha-shape 半径（米）。
    simplify_target:目标三角面数；<=0 表示不简化（教学版 18.1 QEM 简化）。
    fill_holes:     是否补洞（需可选依赖 trimesh，MIT；缺失时降级警告）。
    """

    enabled: bool = Field(dtype=bool, default=True)
    method: str = Field(dtype=str, default="poisson",
                        choices=["poisson", "ball_pivoting", "alpha_shape"])
    poisson_depth: int = Field(dtype=int, default=8, low=1, high=12)
    density_ratio: float = Field(dtype=float, default=0.05, low=0.0, high=1.0)
    ball_radii: list = Field(default=[2.0, 4.0, 6.0])
    alpha: float = Field(dtype=float, default=0.05, low=0.0)
    simplify_target: int = Field(dtype=int, default=0, low=0)
    fill_holes: bool = Field(dtype=bool, default=False)


class QualityConfig(_ConfigBase):
    """质检质量门参数（《设计书》§36 M5 质检 / 教学版第 18 章）。

    rmse_threshold_mm: 中误差阈值（毫米），默认 5.0（WW/T 0115/0117 精细级）。
    coverage_threshold: 模型表面覆盖率下限（0~1），默认 0.80（设计书 §6.3）。
    coverage_k_mps:     覆盖率判定阈值 = k × MPS（平均点间距）——尺度自适应，
                        不写死绝对距离（《软著方案》2.7.2 独创点）。
    samples:            网格采样点数（覆盖率 / 中误差估计）。
    """

    enabled: bool = Field(dtype=bool, default=True)
    rmse_threshold_mm: float = Field(dtype=float, default=5.0, low=0.0)
    coverage_threshold: float = Field(dtype=float, default=0.80, low=0.0, high=1.0)
    coverage_k_mps: float = Field(dtype=float, default=3.0, low=0.0)
    samples: int = Field(dtype=int, default=50000, low=100)


class TextureConfig(_ConfigBase):
    """纹理化参数（《设计书》§35 M4 网格化与纹理 / 软著方案 2.4 texture 段）。

    paint_mode:     纹理方式 vertex=顶点色映射（Open3D 原生，零重依赖）/ uv=UV 图集（增强，未实装时降级警告）。
    export_formats: 带色模型导出格式（ply / obj / glb）。
    lod_levels:     多分辨率 LOD 比例列表（1.0 = 原始精度，越小面数越少）。
    max_vertices:   LOD 简化时的顶点数上限（0 = 不额外限制，仅按比例简化）。
    """

    enabled: bool = Field(dtype=bool, default=True)
    paint_mode: str = Field(dtype=str, default="vertex",
                            choices=["vertex", "uv"])
    export_formats: list = Field(default=["ply", "obj", "glb"])
    lod_levels: list = Field(default=[1.0, 0.3, 0.1])
    max_vertices: int = Field(dtype=int, default=0, low=0)


class PublishConfig(_ConfigBase):
    """发布归档参数（《设计书》§37 M6 发布与溯源 / 软著方案 2.4 publish 段）。

    formats:     交付模型导出格式（标准开放格式，默认 ply/obj/glb）。
    embed_meta:  是否生成 manifest 快照元数据（90_meta/<item>_meta.json，溯源）。
    copy_qc:     是否把质检报告（70_report/accuracy.csv）一并归档进交付包。
    """

    enabled: bool = Field(dtype=bool, default=True)
    formats: list = Field(default=["ply", "obj", "glb"])
    embed_meta: bool = Field(dtype=bool, default=True)
    copy_qc: bool = Field(dtype=bool, default=True)


class PipelineConfig(_ConfigBase):
    """顶层配置：聚合各阶段子配置，对应 configs/example.yaml 的分段结构。"""

    project: ProjectConfig = Nested(ProjectConfig, ProjectConfig)
    preprocess: PreprocessConfig = Nested(PreprocessConfig, PreprocessConfig)
    register: RegisterConfig = Nested(RegisterConfig, RegisterConfig)
    mesh: MeshConfig = Nested(MeshConfig, MeshConfig)
    texture: TextureConfig = Nested(TextureConfig, TextureConfig)
    quality: QualityConfig = Nested(QualityConfig, QualityConfig)
    publish: PublishConfig = Nested(PublishConfig, PublishConfig)
    change: ChangeConfig = Nested(ChangeConfig, ChangeConfig)
    colour: ColourConfig = Nested(ColourConfig, ColourConfig)
    runner: RunnerConfig = Nested(RunnerConfig, RunnerConfig)
    report: ReportConfig = Nested(ReportConfig, ReportConfig)


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------
def load(path: str) -> PipelineConfig:
    """加载 YAML 配置并返回 ``PipelineConfig``。

    使用 ``yaml.safe_load``（禁止任意代码执行）。文件缺失或字段非法时抛 ``ConfigError``。
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"配置文件不存在: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件解析失败: {path}") from exc

    if data is None:
        data = {}
    return PipelineConfig.from_dict(data)


def loads(text: str) -> PipelineConfig:
    """从 YAML 文本解析配置（供测试与内嵌配置使用）。"""
    data = yaml.safe_load(text)
    if data is None:
        data = {}
    return PipelineConfig.from_dict(data)


def dump(cfg: PipelineConfig, path: str) -> None:
    """将配置对象写回 YAML 文件。"""
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg.to_dict(), fh, allow_unicode=True, sort_keys=False)


__all__ = [
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
]
