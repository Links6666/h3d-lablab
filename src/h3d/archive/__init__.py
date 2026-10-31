"""h3d.archive : 文物档案层（Artifact / Epoch / CanonicalAsset）。

设计依据
--------
《彩绘文物数字化管线_入口重构与模块构建指南》1.3 节三级数据模型，与
``docs/adr/0001-archive-layer.md`` 的四项决策。

为什么要有这一层
----------------
改造前执行器的作用域是**文物**（一个项目目录 = 一件文物 = 一次全流程），
所以「文物变了要再做一次差分」只能把 A/B 重建整套重跑一遍。档案层把
「重新采集」（物理必需）与「重新计算」（纯浪费）分开：时相产物登记后只读，
按 ``content_key`` 命中缓存，跨时相分析只拿引用。

三条硬规则（写进代码，不是文档约定）
------------------------------------
1. **只读不可变**：规范产物登记后禁止原地修改；重跑产出新 ``version``，旧版保留。
2. **内容哈希去重**：键 = ``sha256(输入数据哈希 + 参数快照)``，命中即复用。
3. **图集属档案级**：UV 图集不是某时相的私有属性，基准期建一次，后续时相继承。

模块边界
--------
本包只做**元数据与登记**，不读写 Open3D 几何（业务层禁 import Open3D 的纪律同样
适用于此）；产物本身仍在 ``阶段成果/`` 与 ``交付成果/`` 里，档案只存相对路径 + 哈希。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Mapping

from .models import ROUTES, Artifact, CanonicalAsset, Epoch
from .store import ARCHIVE_DIR, Archive, ReadOnlyAsset, ReadOnlyViolation

__all__ = [
    "content_key", "file_digest", "KEY_SCHEMA",
    "Artifact", "Epoch", "CanonicalAsset", "ROUTES",
    "Archive", "ReadOnlyAsset", "ReadOnlyViolation", "ARCHIVE_DIR",
]

#: 内容哈希的算法版本。算法或 payload 结构一旦变更必须提升此值，
#: 否则新旧键会混在同一命名空间里互相命中（缓存给出错误产物）。
KEY_SCHEMA = 1

#: 文件摘要的分块大小。大件产物（PLY 常在 100 MB 级）不能一次读进内存。
_READ_CHUNK = 1 << 20


def _canonical(obj: Any) -> Any:
    """把任意 JSON 友好对象规约成可稳定序列化的形式。

    字典按键排序（``sort_keys`` 也做，这里额外处理嵌套 set/tuple）；
    ``float`` 原样保留，不做四舍五入 —— 参数快照要求「同参数必同键」，
    引入舍入会让 0.1000001 与 0.1 误判为同一组参数。
    """
    if isinstance(obj, Mapping):
        return {str(k): _canonical(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_canonical(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(str(v) for v in obj)
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    # 路径对象、numpy 标量、dataclass 之类：退化为字符串，宁可键偏严也不可漏参数
    return str(obj)


def content_key(inputs: Mapping[str, str], params: Mapping[str, Any]) -> str:
    """由「输入数据哈希 + 参数快照」计算内容键（sha256 十六进制，64 字符）。

    参数
    ----
    inputs: 逻辑名 → 已算好的内容哈希（如 ``{"cloud": "a1b2…", "photos": "c3d4…"}``）。
            传哈希而非路径，是为了让「同一份数据换个位置放」仍命中缓存。
    params: 参数快照（配置片段、算法参数）。

    实现要点
    --------
    用**嵌套结构 + sort_keys** 而非字符串拼接。拼接式实现会让
    ``{"a": "1", "b": "23"}`` 与 ``{"a": "12", "b": "3"}`` 撞车 ——
    缓存返回错误产物，且没有任何报错。``KEY_SCHEMA`` 进 payload，
    保证算法换代后旧键不会与新键混用。
    """
    payload = {
        "schema": KEY_SCHEMA,
        "inputs": _canonical(inputs),
        "params": _canonical(params),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def file_digest(path: str) -> str:
    """文件内容摘要（sha256 十六进制）。

    文件不存在时抛 :class:`FileNotFoundError` —— **不能**静默返回空哈希。
    缓存层把「空哈希」当成一次正常命中时，用户会拿到上一次的产物而毫无察觉，
    这类静默降级在 r16 已经查过一次（``novel`` 被吞数周）。
    """
    # 显式判存在：Windows 下含非法字符的路径（如 ``<x>.ply``）会抛
    # ``OSError(22)`` 而不是 ``FileNotFoundError``，调用方按异常类型分流时会漏。
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_READ_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
