"""h3d.archive.models : 档案层三级实体的类型定义。

三个实体各司其职（指南 1.3 / 表 1-2）：

============  ==========================================  ==================
实体           描述                                        变化频率
============  ==========================================  ==================
Artifact       文物本体                                    几乎不变
Epoch          一次采集（时相）                            随复测线性增长
CanonicalAsset 该次采集经处理后的规范产物（只读、可版本化）  每次重跑新增版本
============  ==========================================  ==================

为什么校验写在构造函数里
------------------------
这些实体最终会落成 JSON，坏数据一旦写入就要靠人工修。所以「空主键」「非法路线」
「绝对路径」「长度不对的哈希」都在 ``__post_init__`` 里当场拒绝 —— 让错误发生在
调用栈里，而不是三个月后有人打开项目发现打不开。
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Mapping

__all__ = ["Artifact", "Epoch", "CanonicalAsset", "ROUTES"]

#: 重建路线：A = 激光几何 + 影像 ICP 融合（绝对尺度），B = COLMAP 纯影像 SfM。
ROUTES = ("A", "B")

#: 盘符前缀（Windows）。跨平台时 ``os.path.isabs`` 不认 ``E:/x``，需单独判。
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _is_absolute(path: str) -> bool:
    return bool(_DRIVE_RE.match(path)) or os.path.isabs(path) \
        or path.startswith("\\\\")


def _pick(data: Mapping[str, Any], cls) -> dict[str, Any]:
    """只挑本类声明过的字段。

    直接 ``cls(**data)`` 会在 JSON 里多出一个键时抛 TypeError；而档案文件是要被
    未来版本读的，多键应当忽略而不是崩掉（向前兼容）。
    """
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


@dataclass(frozen=True)
class Artifact:
    """文物本体。全局唯一，是全部时相的归属对象。"""

    artifact_id: str
    title: str
    dynasty: str = ""
    material: str = ""
    height_cm: float | None = None
    width_cm: float | None = None
    protection_level: str = ""
    location: str = ""

    def __post_init__(self) -> None:
        if not str(self.artifact_id).strip():
            raise ValueError("Artifact.artifact_id 不能为空（它是时相的归属主键）")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Artifact":
        return cls(**_pick(data, cls))


@dataclass(frozen=True)
class Epoch:
    """时相：同一文物在一次采集时刻形成的一套原始数据与处理参数。

    基准期（``is_baseline=True``）定义坐标系、UV 图集、物理尺度与色彩协议；
    其余时相一律单向配准到基准期，**禁止链式对齐**（指南 2.3）。
    """

    epoch_id: str
    artifact_id: str
    captured_on: str                      # ISO 日期 YYYY-MM-DD
    route: str                            # "A" | "B"
    device: str = ""
    raw_paths: list[str] = field(default_factory=list)
    colour_protocol: dict[str, Any] = field(default_factory=dict)
    scale_ref: dict[str, Any] = field(default_factory=dict)
    qc_status: str = "pending"            # pending | pass | fail
    is_baseline: bool = False

    def __post_init__(self) -> None:
        if not str(self.epoch_id).strip():
            raise ValueError("Epoch.epoch_id 不能为空")
        if not str(self.artifact_id).strip():
            raise ValueError("Epoch.artifact_id 不能为空")
        route = str(self.route).strip().upper()
        if route not in ROUTES:
            raise ValueError(
                f"Epoch.route 必须是 {'/'.join(ROUTES)} 之一，收到 {self.route!r}")
        # frozen dataclass 里改字段要用 object.__setattr__
        object.__setattr__(self, "route", route)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Epoch":
        return cls(**_pick(data, cls))


@dataclass(frozen=True)
class CanonicalAsset:
    """规范产物：经配准 / 图集继承 / 尺度与色彩标定并通过质检的只读数据集。

    只读不可变 —— 重跑产出新 ``version``，旧版保留。路径一律**相对项目根**，
    与 ADR-0001 D3「包裹现有布局」一致：产物还在 ``阶段成果/`` 里，档案只存引用。
    """

    epoch_id: str
    version: int
    content_key: str                      # sha256 十六进制，64 字符
    mesh_path: str | None = None
    cloud_path: str | None = None
    texture_path: str | None = None
    uv_atlas_ref: str | None = None       # 档案级图集版本号，如 "atlas-v1"
    cameras_path: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not str(self.epoch_id).strip():
            raise ValueError("CanonicalAsset.epoch_id 不能为空")
        if int(self.version) < 1:
            raise ValueError(f"CanonicalAsset.version 从 1 起，收到 {self.version}")
        key = str(self.content_key)
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError(
                "CanonicalAsset.content_key 必须是 64 位小写 sha256 十六进制，"
                f"收到 {key[:12]!r}…")
        for name in ("mesh_path", "cloud_path", "texture_path", "cameras_path"):
            value = getattr(self, name)
            if value and _is_absolute(str(value)):
                raise ValueError(
                    f"CanonicalAsset.{name} 必须存相对路径（项目换盘符/换机器后"
                    f"绝对路径必断），收到 {value}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CanonicalAsset":
        return cls(**_pick(data, cls))
