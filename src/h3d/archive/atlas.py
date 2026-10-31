"""h3d.archive.atlas : 档案级 UV 图集的登记与版本冻结（B1）。

为什么图集要单独成一个实体
--------------------------
指南 2.2：两期要逐 texel 比色，必须共用同一套 UV 展开；第 2 期若独立重展开，
texel 与第 1 期不存在一一对应，ΔE 逐点比较完全失效。

而「共用同一套 UV」在代码里的前提常被忽略：**基准期的 UV 布局必须先落盘并且
不可变**。M4 现状只在内存里有 ``uv/faces``，跑完即丢 —— 第二次烘焙无从继承，
只能重新展开，而「重展开结果是否与上次逐 texel 对齐」没有任何保证。

冻结规则（指南「风险提示」的落点）
----------------------------------
* 同版本 + 同 ``uv_digest`` → **幂等**（返回已有记录，不重写文件）。
* 同版本 + 不同 ``uv_digest`` → 抛 :class:`AtlasFrozen`。

  不允许静默覆盖：覆盖等于让所有基于旧图集的历史结论悄悄失效，而报表、
  耗时、日志全部正常 —— 这类「看起来成功」的失效本项目已经栽过两次
  （r16 手写白名单、r17 自报 rmse）。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

import numpy as np

from .models import _is_absolute, _pick
from .store import Archive, _atomic_write_json, _load_json

__all__ = ["AtlasFrozen", "AtlasRecord", "AtlasRegistry", "ATLAS_DIR",
           "UvLayout", "uv_digest", "save_uv_layout", "load_uv_layout",
           "load_uv_layout_ex", "register_layout"]

#: 图集记录目录（位于 ``<项目>/archive/`` 下）。
ATLAS_DIR = "atlas"

#: 版本号格式。必须带 ``v`` 且为纯数字后缀，否则数值排序会失效
#: （``atlas-v10`` 按字符串排在 ``atlas-v9`` 之前）。
_VERSION_RE = re.compile(r"^atlas-v(\d+)$")

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

log = logging.getLogger(__name__)


class AtlasFrozen(RuntimeError):
    """试图改写已冻结版本号的 UV 布局。"""


def uv_digest(uv, faces) -> str:
    """UV 布局的内容指纹。

    三个刻意的处理：

    1. **统一降到 float32 再序列化**。同一套 UV 从内存（float64）与从 ``.npz``
       （float32）读回来必须得到同一个 digest，否则「落盘 → 重启 → 继承」这条
       主路径每次都判成「图集变了」，版本号会无意义地一路涨。
    2. **dtype 与 shape 进哈希头**：只拼字节会让「4 个顶点的 UV」与「8 个顶点的
       UV」在前半段相同时有撞车风险。
    3. **面顺序参与**：面的顺序变了就是另一套展开（texel 与面对应关系变了），
       必须换 digest —— 否则继承出来的纹理整体错位且不报错。
    """
    uv_a = np.ascontiguousarray(
        np.asarray(uv, dtype=np.float64).astype(np.float32))
    faces_a = np.ascontiguousarray(np.asarray(faces, dtype=np.int64))
    h = hashlib.sha256()
    h.update(f"uv:{uv_a.dtype.str}:{uv_a.shape}:".encode("utf-8"))
    h.update(uv_a.tobytes())
    h.update(f"|f:{faces_a.dtype.str}:{faces_a.shape}:".encode("utf-8"))
    h.update(faces_a.tobytes())
    return h.hexdigest()


def _array_digest(arr, tag: str) -> str:
    """数组摘要（float32 归一 + dtype/shape 进头）。"""
    if arr is None:
        return ""
    a = np.ascontiguousarray(np.asarray(arr, dtype=np.float64).astype(np.float32))
    h = hashlib.sha256()
    h.update(f"{tag}:{a.dtype.str}:{a.shape}:".encode("utf-8"))
    h.update(a.tobytes())
    return h.hexdigest()


@dataclass(frozen=True)
class UvLayout:
    """落盘的 UV 布局（含展开后拓扑的几何）。

    ``verts/normals`` 不是可有可无的附属信息：``uv``/``faces`` 属于**展开后**
    的拓扑（接缝处顶点被拆分，顶点数 ≥ 原始网格），拿原始 PLY 去承接会让面
    索引越界 —— 继承当场崩，或者更糟：在别的模型上"看起来能跑"。
    """

    uv: np.ndarray
    faces: np.ndarray
    verts: np.ndarray | None = None
    normals: np.ndarray | None = None

    def has_geometry(self) -> bool:
        return self.verts is not None and len(self.verts) == len(self.uv)


def save_uv_layout(path: str, uv, faces, verts=None,
                   normals=None) -> dict[str, Any]:
    """把 UV 布局写到 ``.npz``（``uv``/``faces``，可选 ``verts``/``normals``）。

    幂等：文件已存在且**布局与几何都未变**时不重写（``written=False``）。
    ``mtime`` 是运维上区分「图集被重算过」与「只是又跑了一次」的唯一线索。

    存 ``float32``：UV 在 [0,1] 内、顶点相对包围盒也远小于 float32 精度极限；
    换来文件体积减半。读回统一转 float64，且 :func:`uv_digest` 内部也先降到
    float32，所以「内存 → 落盘 → 读回」的指纹恒定。
    """
    uv_a = np.asarray(uv, dtype=np.float64).astype(np.float32)
    faces_a = np.asarray(faces, dtype=np.int64)
    digest = uv_digest(uv_a, faces_a)
    v_digest = _array_digest(verts, "verts")
    info: dict[str, Any] = {"path": os.path.abspath(path), "digest": digest,
                            "written": False,
                            "vertices": int(len(uv_a)),
                            "faces": int(len(faces_a)),
                            "has_geometry": verts is not None}
    if os.path.isfile(path):
        try:
            old = load_uv_layout_ex(path)
            if uv_digest(old.uv, old.faces) == digest \
                    and _array_digest(old.verts, "verts") == v_digest:
                info["bytes"] = int(os.path.getsize(path))
                return info
        except (OSError, ValueError):
            pass  # 损坏/读不出 → 按未命中重写（下面会覆盖）
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    payload: dict[str, Any] = {"uv": uv_a, "faces": faces_a}
    if verts is not None:
        payload["verts"] = np.asarray(verts, dtype=np.float64).astype(np.float32)
    if normals is not None:
        payload["normals"] = np.asarray(normals,
                                        dtype=np.float64).astype(np.float32)
    try:
        with open(tmp, "wb") as fh:
            np.savez_compressed(fh, **payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    info["written"] = True
    info["bytes"] = int(os.path.getsize(path))
    return info


def load_uv_layout_ex(path: str) -> UvLayout:
    """读回完整 UV 布局（``uv``/``faces`` + 可选的展开后几何）。

    损坏文件改名 ``.corrupt-<pid>`` 留证并抛 :class:`ValueError`。**不能**安静
    返回空数组：继承时拿到空 UV 会烘出一张全黑图集，而流程显示成功。
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    try:
        with np.load(path, allow_pickle=False) as npz:
            if "uv" not in npz or "faces" not in npz:
                raise ValueError(f"UV 布局缺少键（需要 uv 与 faces）：{path}")
            uv = np.asarray(npz["uv"], dtype=np.float64)
            faces = np.asarray(npz["faces"], dtype=np.int64)
            verts = (np.asarray(npz["verts"], dtype=np.float64)
                     if "verts" in npz else None)
            normals = (np.asarray(npz["normals"], dtype=np.float64)
                       if "normals" in npz else None)
    except (OSError, ValueError, KeyError) as exc:
        if exc.__class__ is ValueError and "缺少键" in str(exc):
            raise
        broken = f"{path}.corrupt-{os.getpid()}"
        try:
            os.replace(path, broken)
        except OSError:
            pass
        raise ValueError(f"UV 布局损坏，已改名保留现场：{broken}（{exc}）") from exc
    if uv.ndim != 2 or uv.shape[1] != 2:
        raise ValueError(f"UV 形状非法：{uv.shape}（期望 (N, 2)）：{path}")
    if faces.ndim != 2 or faces.shape[1] != 3:
        raise ValueError(f"面索引形状非法：{faces.shape}（期望 (M, 3)）：{path}")
    return UvLayout(uv=uv, faces=faces, verts=verts, normals=normals)


def load_uv_layout(path: str):
    """读回 ``(uv float64, faces int64)``（不需要几何时的简便入口）。"""
    layout = load_uv_layout_ex(path)
    return layout.uv, layout.faces


@dataclass(frozen=True)
class AtlasRecord:
    """一个图集版本的定义：由哪个时相展开、布局指纹、布局文件在哪。

    ``uv_rel_path`` 指向落盘的 UV 布局（``.npz``，含 ``uv`` 与 ``faces``），
    相对项目根 —— 换盘符/换机器不断（与 ``CanonicalAsset`` 同一条纪律）。
    """

    atlas_version: str
    artifact_id: str
    epoch_id: str
    uv_digest: str
    #: 布局属于哪一件文物。图集是**件级**资产：一个项目可以有多件，各件各有
    #: 一套 UV 展开；没有这个字段就无法区分「同一件重跑」与「另一件登记」，
    #: 后者会被误判成换版。
    item_id: str = ""
    mesh_digest: str = ""
    size_px: int = 0
    uv_rel_path: str = ""
    texels: int = 0
    frozen_at: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if not _VERSION_RE.match(str(self.atlas_version)):
            raise ValueError(
                f"atlas_version 必须形如 atlas-v<N>，收到 {self.atlas_version!r}")
        if not str(self.artifact_id).strip():
            raise ValueError("AtlasRecord.artifact_id 不能为空")
        if not str(self.epoch_id).strip():
            raise ValueError("AtlasRecord.epoch_id 不能为空（图集由一个时相定义）")
        if not _HEX64_RE.match(str(self.uv_digest)):
            raise ValueError("uv_digest 必须是 64 位小写十六进制")
        if self.mesh_digest and not _HEX64_RE.match(str(self.mesh_digest)):
            raise ValueError("mesh_digest 必须为空或 64 位小写十六进制")
        if self.uv_rel_path and _is_absolute(str(self.uv_rel_path)):
            raise ValueError(
                f"uv_rel_path 必须是相对项目根的路径：{self.uv_rel_path}")
        if int(self.size_px) < 0:
            raise ValueError("size_px 不能为负")

    @property
    def version_number(self) -> int:
        m = _VERSION_RE.match(self.atlas_version)
        return int(m.group(1)) if m else 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AtlasRecord":
        return cls(**_pick(data, cls))


class AtlasRegistry:
    """``<项目>/archive/atlas/<version>.json`` 的读写与冻结判定。"""

    def __init__(self, arch: Archive) -> None:
        self._arch = arch

    # -- 路径 ---------------------------------------------------------------
    @property
    def atlas_dir(self) -> str:
        return os.path.join(self._arch.archive_dir, ATLAS_DIR)

    def _path(self, version: str) -> str:
        return os.path.join(self.atlas_dir, f"{version}.json")

    # -- 查询 ---------------------------------------------------------------
    def get(self, version: str) -> AtlasRecord | None:
        data = _load_json(self._path(version))
        if not data:
            return None
        try:
            return AtlasRecord.from_dict(data)
        except ValueError:
            return None

    def versions(self) -> list[AtlasRecord]:
        """按版本号**数值**升序（字符串排序会把 v10 排到 v9 前面）。"""
        if not os.path.isdir(self.atlas_dir):
            return []
        out: list[AtlasRecord] = []
        for name in sorted(os.listdir(self.atlas_dir)):
            if not name.endswith(".json"):
                continue
            rec = self.get(name[:-5])
            if rec is not None:
                out.append(rec)
        return sorted(out, key=lambda r: r.version_number)

    def current(self) -> AtlasRecord | None:
        found = self.versions()
        return found[-1] if found else None

    def next_version(self) -> str:
        found = self.versions()
        n = found[-1].version_number + 1 if found else 1
        return f"atlas-v{n}"

    # -- 写入 ---------------------------------------------------------------
    def register(self, rec: AtlasRecord) -> AtlasRecord:
        """登记一个图集版本。幂等；同版本不同布局抛 :class:`AtlasFrozen`。"""
        existing = self.get(rec.atlas_version)
        if existing is not None:
            if existing.uv_digest == rec.uv_digest:
                return existing
            raise AtlasFrozen(
                f"{rec.atlas_version} 已冻结（uv_digest {existing.uv_digest[:12]}…），"
                f"本次布局为 {rec.uv_digest[:12]}…。"
                f"UV 变了必须新建版本（建议 {self.next_version()}），"
                f"并把引用旧图集的历史产物标记「图集过期、需重算」")
        payload = rec.to_dict()
        if not payload.get("frozen_at"):
            import datetime as _dt

            payload["frozen_at"] = _dt.datetime.now().isoformat(
                timespec="seconds")
        saved = AtlasRecord.from_dict(payload)
        _atomic_write_json(self._path(saved.atlas_version), saved.to_dict())
        return saved


def _record_item(rec: AtlasRecord) -> str:
    """记录归属的件号。旧记录（无 ``item_id`` 字段）从 ``note`` 里捞回来。"""
    if rec.item_id:
        return rec.item_id
    m = re.search(r"item=([^,;\s]+)", rec.note or "")
    return m.group(1) if m else ""


def register_layout(arch: Archive, uv_path: str, *, item_id: str,
                    size_px: int = 0,
                    mesh_digest: str = "") -> AtlasRecord | None:
    """把一份落盘的 UV 布局登记成档案的图集版本。返回登记后的记录。

    为什么需要这个入口（真机 2026-10-31）
    ------------------------------------
    B1 第一版只做了「冻结判定 + 继承读取」，**没有任何代码写
    ``archive/atlas/atlas-vN.json``**，于是 ``inherit`` 永远报「没有图集记录」，
    而且重跑 M4 也补不上：落 ``uv.npz`` 与登记图集是两件事。同处还留着
    ``uv_atlas_ref="atlas-v1"`` 的手写常量——资产声称自己挂在一个从不存在的
    图集上，报表一切正常。这是 r16「手写白名单」的同构缺陷。

    语义
    ----
    * 图集由**基准期**定义：记录一律挂在 ``arch.baseline()`` 上，调用方负责
      判断「本次跑的是不是基准期」（复测期不得改写基准图集）。
    * 无档案基准期 / 布局文件缺失或损坏 → 返回 ``None``，**不建目录不抛错**：
      档案是增量能力，不能反过来让建模失败。
    * ``uv_rel_path`` 相对项目根（换盘符/换机器不断，与 ``CanonicalAsset``
      同一条纪律）；解析后落到项目根之外的路径直接拒绝。
    """
    layout_path = os.path.abspath(uv_path) if uv_path else ""
    if not layout_path or not os.path.isfile(layout_path):
        return None
    try:
        layout = load_uv_layout_ex(layout_path)
    except (OSError, ValueError):
        return None
    base = arch.baseline()
    if base is None:
        return None
    root = arch.root
    rel = os.path.relpath(layout_path, root).replace(os.sep, "/")
    if rel.startswith("../") or _is_absolute(rel):
        return None
    registry = AtlasRegistry(arch)
    digest = uv_digest(layout.uv, layout.faces)
    same_item = [r for r in registry.versions()
                 if _record_item(r) == (item_id or "")]
    current = same_item[-1] if same_item else None
    if current is not None and current.uv_digest == digest:
        # 幂等短路：又跑了一次 M4，但布局一字未改。若不在这里返回，每跑一次
        # 版本号就涨一格，复测期继承的到底是哪一版将无法追溯。
        return current
    rec = AtlasRecord(
        atlas_version=registry.next_version(),
        artifact_id=arch.artifact().artifact_id,
        epoch_id=base.epoch_id,
        uv_digest=digest,
        mesh_digest=mesh_digest,
        size_px=int(size_px),
        uv_rel_path=rel,
        texels=int(size_px) * int(size_px),
        item_id=item_id, note=f"item={item_id}")
    if current is not None:
        # 换版不是错误，但**绝不许静默**：v1 原样保留，可引用旧版的资产从此
        # 与新图集无法逐 texel 比对 —— 这类「看起来成功」的失效本项目栽过两次
        # （r16 手写白名单、r17 自报 rmse）。
        log.warning(
            "基准图集换版：%s（uv_digest %s）→ %s（uv_digest %s）。"
            "旧版记录与布局文件均保留未覆盖；引用旧图集的产物需重算才能与新图集"
            "逐 texel 比对（atlas_stale 自动标记尚未实装）",
            current.atlas_version, current.uv_digest[:12],
            rec.atlas_version, digest[:12])
    return registry.register(rec)
