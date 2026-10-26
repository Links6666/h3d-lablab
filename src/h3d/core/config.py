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


class ScaleConfig(_ConfigBase):
    """尺度自适应（跨尺度文物通用化，2026-10-09 新增）。

    背景：``preprocess`` / ``register`` / ``mesh`` 中的距离类参数是**绝对米制值**
    （如体素 5 mm、FPFH 半径 2 cm），这套默认值是按**约 1 m 量级**的文物标定的。
    中小型文物（铜镜、印章、小件陶瓷/玉器，5~30 cm）若沿用同一套值，会出现
    「体素吃掉纹饰细节」「FPFH 半径等效半个器身」的退化。

    机制：以**包围盒对角线**为唯一尺度基准，按
    ``factor = clip(bbox_diag / reference_diag, min_factor, max_factor)``
    把配置中的距离类参数整体线性缩放，使「参数 / 物体尺寸」之比保持恒定。

    不参与缩放的参数（有意为之）：k 近邻数、比率、迭代次数、八叉树深度等
    **无量纲**量——只要体素随尺度缩放，点密度相对物体即保持恒定，这些量无需变。
    """

    enabled: bool = Field(dtype=bool, default=True)
    # 标定基准：现有默认参数所对应的包围盒对角线（米）。
    # 3.7 ≈ 示例文物外接盒 2.0 x 2.6 x 1.7 m 的对角线 3.71 m —— 即
    # 「preprocess.voxel_down_sample=0.005 这套值」的调参基准。取该值可保证
    # 示例数据与历史项目的 factor≈1.0（零回归）。若你的历史数据是在别的
    # 尺度上调的参，改成对应尺度即可。
    reference_diag: float = Field(dtype=float, default=3.7, low=0.001)
    # 缩放因子钳制区间：防止噪声点云/单位错误把参数压到 0 或放大到失控。
    # 0.005 对应约 1.85 cm 的小件下限；5.0 对应约 18.5 m 的上限。
    min_factor: float = Field(dtype=float, default=0.005, low=1e-6)
    max_factor: float = Field(dtype=float, default=5.0, low=1.0)
    # 对角线超过此值（米）时告警——常见于「数据单位实为毫米」的情形。
    warn_diag_gt: float = Field(dtype=float, default=15.0, low=1.0)


class PreprocessConfig(_ConfigBase):
    voxel_down_sample: float = Field(dtype=float, default=0.005, low=0.0)
    remove_outlier_std: float = Field(dtype=float, default=2.0, low=0.0)
    normal_estimate_k: int = Field(dtype=int, default=30, low=1)
    #: 纯照片场景去掉背景/台面（2026-10-18 实机）：SfM 稠密重建会把白色
    #: 衬板、桌面一并建出来，实测一件器物被拆成 25 个连通块、最大块仅占
    #: 27.8% 面积，3D 预览"白花花一片、看不出是器物"。开后在 M1 里做
    #: 「RANSAC 主平面剔除 + 主簇保留」，只对**无激光锚点**的件生效。
    remove_background: bool = Field(dtype=bool, default=True)
    #: 平面剔除数（默认 0 = 不剔）。实测背景是"零散碎片"而非一整块衬板，
    #: 剔平面会误伤器物自身的平面；确有整块衬板时才调成 1~3。
    bg_max_planes: int = Field(dtype=int, default=0, low=0)
    #: 平面判定：内点到平面的距离阈值 = 该值 × 点云包围盒对角线
    bg_plane_dist_frac: float = Field(dtype=float, default=0.005, low=0.0)
    #: 聚类邻域半径的**密度基准**：eps = min(0.02×diag, 该值 × MPS)。
    #: 2026-10-23 实机：677k 点的稠密点云上 0.02×diag 已是 102×MPS，
    #: 每点邻域 279 个 → Open3D 单线程 DBSCAN 撑到 10 GB / 50 分钟没结束。
    bg_cluster_eps_k: float = Field(dtype=float, default=10.0, low=1.0)
    #: 聚类工作副本点数上限：超过就体素降采样后聚类，再把标签回投全分辨率。
    #: 聚类只用来判「哪些点是同一件器物」，不需要全分辨率。
    bg_max_cluster_points: int = Field(dtype=int, default=150000, low=1000)
    #: 主簇保留：小于「最大簇 × 该比例」的碎片直接丢弃
    #: （0.25 = 单件器物只留主簇；多件/带盖器物可降到 0.10~0.15）
    bg_keep_cluster_frac: float = Field(dtype=float, default=0.25, low=0.0)


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
    """批量调度参数（《软著方案》2.8 runtime 段，映射到本项目的 runner 段）。

    x1_enabled / x2_enabled: 可选评估 stage（X1 多时相形变 / X2 色差），
    默认关闭；X1 需 00_ref/<item> 参考期点云 + 当期 dense 产物。
    """

    fail_isolation: bool = Field(dtype=bool, default=True)
    resume: bool = Field(dtype=bool, default=True)
    manifest: str = Field(dtype=str, default="output/run_manifest.json")
    workers: int = Field(dtype=int, default=1, low=1)
    x1_enabled: bool = Field(dtype=bool, default=False)
    x2_enabled: bool = Field(dtype=bool, default=False)


class ReportConfig(_ConfigBase):
    """报表输出参数（《软著方案》2.8 report 段 / 设计书 M6）。

    formats:    输出格式（csv / png / html，可组合）。
    output_dir: 报表输出目录。
    project_name: 报表展示的项目名（CLI 传入 cfg.project.name）。
    """

    enabled: bool = Field(dtype=bool, default=True)
    formats: list = Field(default=["csv", "png", "html"])
    #: 2026-10-18：默认值原为 "output/report"，而 GUI 写 "report"、README 与
    #: 结果页也都按 <root>/report 查找 —— 用 example.yaml 跑 CLI 的用户
    #: 会找不到报表（产物在 root/output/report）。统一为 "report"。
    output_dir: str = Field(dtype=str, default="report")
    project_name: str = Field(dtype=str, default="")


class MeshConfig(_ConfigBase):
    """网格重建参数（《设计书》§34.3 M3 稠密重建 / 教学版第 17 章）。

    method:         重建方法 poisson / ball_pivoting / alpha_shape。
    poisson_depth:  泊松重建深度（越大越细越慢，千万级点云取 8-10）。
    density_ratio:  密度裁剪分位数（剔除低密度离群碎面，设计书 TC-303）。
    ball_radii:     Ball-Pivoting 多球半径列表（米）。
    alpha:          Alpha-shape 半径（米）。
    poisson_threads:泊松求解线程数（默认 1）。Open3D 多线程下泊松**不可复现**
                    （同输入连跑两次面表哈希不同），会连带让 M4 图集复用失效，
                    故默认单线程；确需速度可调大，但结果不再可复现。
    simplify_target:目标三角面数；<=0 表示不简化（教学版 18.1 QEM 简化）。
    fill_holes:     是否补洞（需可选依赖 trimesh，MIT；缺失时降级警告）。
    """

    enabled: bool = Field(dtype=bool, default=True)
    method: str = Field(dtype=str, default="poisson",
                        choices=["poisson", "ball_pivoting", "alpha_shape"])
    poisson_depth: int = Field(dtype=int, default=8, low=1, high=12)
    poisson_threads: int = Field(dtype=int, default=1, low=1)
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
                            choices=["vertex", "uv", "photo"])
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


class SfmSolveConfig(_ConfigBase):
    """S1 sfm_solve 可选 stage（照片导入方案）：COLMAP 子进程解算相机位姿。

    enabled:     默认关闭；开启后扫描 00_photo/<item>/，调用 COLMAP sparse
                 （feature→match→mapper）自动生成 capture_meta.json。
    photo_dir:   照片根目录（00_photo）；为空时取 project.raw_dir 上级 + 00_photo。
    colmap_path: COLMAP 可执行文件路径（用户本机安装，仓库不打包二进制）。
    backend:     colmap_cli（子进程）/ pycolmap（预留）。
    workspace:   COLMAP 中间产物目录（运行结束自动清理）。
    matcher:     auto / exhaustive / sequential。``auto`` = 交给特征标定按张数与
                 命名决定（照片少时 exhaustive 更稳，40 张以上按序列拍则 sequential，
                 匹配对数从 n²/2 降到 ~n，是 S1 最省时的一档）；写死则以配置为准。
    max_features: SIFT 最大特征数。
    """

    enabled: bool = Field(dtype=bool, default=False)
    photo_dir: str = Field(dtype=str, default="")
    colmap_path: str = Field(dtype=str, default="colmap")
    backend: str = Field(dtype=str, default="colmap_cli")
    workspace: str = Field(dtype=str, default="")
    matcher: str = Field(dtype=str, default="auto")
    max_features: int = Field(dtype=int, default=8192, low=256)
    peak_threshold: float = Field(dtype=float, default=0.004, low=0.001)
    auto_calibrate: bool = Field(dtype=bool, default=True)
    dense: bool = Field(dtype=bool, default=False)
    dense_min_pixels: int = Field(dtype=int, default=2, low=1)
    dense_max_pixels: int = Field(dtype=int, default=20, low=5)
    dense_gate: bool = Field(dtype=bool, default=True)
    dense_gate_std: float = Field(dtype=float, default=2.0, low=0.5)
    dense_gate_density: float = Field(dtype=float, default=0.01, low=0.0)
    max_image_size: int = Field(dtype=int, default=0, low=0)
    # 稠密（patch_match_stereo）分辨率上限：**独立于** max_image_size。
    # 稀疏解算需要 2400 长边保证特征点质量，稠密深度图却与之无关——深度图
    # 像素数直接决定 patch_match 耗时（≈ 像素数 × 遍数）。
    # 实机 2026-10-17：30 张 2400×1601（3.84 MP）双遍稠密在 RTX 3050 Laptop
    # 上需 6815 s（第一遍 2615 s + 第二遍约 4200 s），且被 3600 s 超时腰斩。
    # 降到 1600 长边（1.71 MP）后同机约 3000 s 双遍，几何精度损失在 0.3 mm/px
    # 量级，对文物贴图/建模无实质影响。0 = 跟随输入分辨率（不限制）。
    dense_max_image_size: int = Field(dtype=int, default=1600, low=0)
    # 稠密超时预算（秒）。0 = 按「张数 × 像素数 × 遍数」自动估算（见
    # sfm_solve.dense_timeout_budget）。显式 >0 时以其为准。
    # 原实现写死 3600 s 覆盖**整个** patch_match_stereo 命令，而该命令在
    # geom_consistency=true 时会跑两遍，实测需求 6815 s → 必然被腰斩。
    dense_timeout_s: int = Field(dtype=int, default=0, low=0)
    # 几何一致性校验（第二遍）。true = 深度图两遍（photometric + geometric），
    # 融合用 geometric（质量最好，耗时 ×2）；false = 只跑 photometric，融合
    # 改用 photometric（耗时减半，边缘噪声略增）。追求「先跑通」时设 false。
    dense_geom_consistency: bool = Field(dtype=bool, default=True)
    # 强制丢弃既有深度图并重算。默认 false：位姿未变时复用已完成的深度图
    # （超时/崩溃后可断点续算，不必从第 1 张重来）。
    force_redense: bool = Field(dtype=bool, default=False)
    # 照片集完全相同的件直接复用已解算结果（2026-10-24 实机）。
    #
    # 同一批照片被复制成多件（实机 5 件共用 30 张 DSC_*.JPG）时，每件都会
    # 从头解一遍 SfM：单件 30 min（稠密占 76%），5 件就是 2.5 h，而位姿与
    # 点云按定义完全相同。开启后先比对照片集指纹（名字+大小+内容哈希）与
    # 解算参数，命中就用已完成的 10_dense/<件>/fused_dense.ply 与
    # 90_meta/<件>_capture_meta.json 复制一份，S1 从 30 min 降到 ~2 s。
    # 复用会在日志、manifest（s1_reuse）与报表里明确留痕；需要每件独立
    # 解算时置 false（或 force_redense=true）。
    reuse_identical_photos: bool = Field(dtype=bool, default=True)
    # 可复现性（2026-09-15 实机排查结论）：
    #   COLMAP 特征提取 / 匹配在**多线程**下的浮点归约顺序不确定，同一组照片
    #   两次运行会产出**内容不同**的 database.db（实测关键点坐标哈希、匹配
    #   内点哈希均不同），导致 mapper 注册图像数剧烈波动（示例数据 3/16~16/16）。
    #   置 True 时强制单线程（等价 --FeatureExtraction.num_threads 1
    #   --FeatureMatching.num_threads 1 --Mapper.num_threads 1
    #   --default_random_seed 0），实测两次运行的 database.db **比特级一致**、
    #   位姿逐位一致，满足论文实验可复现要求。
    #   代价：大图集时特征提取与匹配耗时上升；追求速度可置 False。
    #   注：--default_random_seed 单独设置**无效**（其默认值本为 0），
    #   真正的随机性来源是线程调度，故必须同时固定线程数。
    deterministic: bool = Field(dtype=bool, default=True)
    # 显式线程数覆盖：0 = 按 deterministic 推导（1 或 -1=自动）；>0 生效。
    num_threads: int = Field(dtype=int, default=0, low=0)


class PhotoTextureConfig(_ConfigBase):
    """P1 photo_texture 可选 stage（场景①：激光几何 + 照片纹理）。

    enabled:      默认关闭；开启后使用 capture_meta.json 位姿把 00_photo 照片
                 投影到 M3 网格，输出高保真带纹理模型（照片投影替代点云 RGB）。
    texture_size: 纹理图集边长（px）。
    top_k:        每面视角加权取 top-k 张照片平均采样（抗接缝）。
    samples_per_tri: 每三角形采样点数。
    icp_align:    是否启用控制点 ICP 坐标系对齐（创新点 2，把 SfM 位姿/几何
                 坐标系统一到几何坐标系；无靶标时残差可能较大，可关闭）。
    export_formats: 输出格式（obj/glb；PLY 不支持纹理回退顶点色）。
    reuse_atlas:  跨件复用 UV 图集与纹理图集（默认开启）。同批照片的件经 S1
                  复用后几何逐位相同 → xatlas 展开与逐纹素采样必然同结果，
                  复用把单件纹理耗时从 83~85 s 降到 4~6 s；几何/位姿/照片/
                  参数任一不同即自动重算，不会串件。
    """

    enabled: bool = Field(dtype=bool, default=False)
    photo_dir: str = Field(dtype=str, default="")
    texture_size: int = Field(dtype=int, default=1024, low=256)
    top_k: int = Field(dtype=int, default=3, low=1)
    samples_per_tri: int = Field(dtype=int, default=4, low=1)
    icp_align: bool = Field(dtype=bool, default=True)
    poisson_blend: bool = Field(dtype=bool, default=True)
    blend_iterations: int = Field(dtype=int, default=150, low=10)
    export_formats: list = Field(default=["obj", "glb"])
    reuse_atlas: bool = Field(dtype=bool, default=True)


class PipelineConfig(_ConfigBase):
    """顶层配置：聚合各阶段子配置，对应 configs/example.yaml 的分段结构。"""

    project: ProjectConfig = Nested(ProjectConfig, ProjectConfig)
    scale: ScaleConfig = Nested(ScaleConfig, ScaleConfig)
    preprocess: PreprocessConfig = Nested(PreprocessConfig, PreprocessConfig)
    register: RegisterConfig = Nested(RegisterConfig, RegisterConfig)
    mesh: MeshConfig = Nested(MeshConfig, MeshConfig)
    texture: TextureConfig = Nested(TextureConfig, TextureConfig)
    quality: QualityConfig = Nested(QualityConfig, QualityConfig)
    publish: PublishConfig = Nested(PublishConfig, PublishConfig)
    change: ChangeConfig = Nested(ChangeConfig, ChangeConfig)
    colour: ColourConfig = Nested(ColourConfig, ColourConfig)
    sfm_solve: SfmSolveConfig = Nested(SfmSolveConfig, SfmSolveConfig)
    photo_texture: PhotoTextureConfig = Nested(PhotoTextureConfig, PhotoTextureConfig)
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
        # utf-8-sig 与 utf-8 等价，但会吃掉记事本写下的 BOM —— 直接 open(utf-8)
        # 会把 "\ufeff" 留在首键名里，变成「未知参数: ['\ufeffproject']」这种
        # 用户完全看不懂的报错。
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"配置文件不存在: {path}") from exc
    except UnicodeDecodeError as exc:
        # Windows 记事本的「ANSI」在中文系统上就是 GBK。裸抛 UnicodeDecodeError
        # 会打印一大段 PyYAML 栈，用户看不出该改什么。
        raise ConfigError(
            f"配置文件编码不是 UTF-8：{path}\n"
            f"　Windows 记事本另存为「ANSI」会写成 GBK。请用记事本/VS Code "
            f"另存为「UTF-8」（有 BOM 也可以）。"
        ) from exc
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
