"""h3d.archive.store : 档案的落盘与读取（文件系统 + JSON 清单）。

存储布局（ADR-0001 D2）
-----------------------
::

    <项目根>/
      archive/
        artifact.json                  Artifact 一条
        epochs/<epoch_id>.json         每时相一条
        assets/<epoch_id>__v<N>.json   每次登记一个不可变版本

为什么不用 SQLite
-----------------
档案要跟项目目录一起拷贝、归档到移动硬盘。SQLite 会让「目录 + 数据库」变成两套
状态，异地打开必然不同步；而单机单人无并发多写，SQLite 的收益用不上。JSON 清单
配合原子写（临时文件 + ``os.replace``）已足够。

三条硬规则的落点
----------------
* 只读不可变 → :meth:`Archive.register` 只新增 ``v<N+1>``，从不覆盖旧文件；
  :meth:`Archive.mount` 返回的句柄对任何写模式抛 :class:`ReadOnlyViolation`。
* 内容哈希去重 → 同 ``content_key`` 的重复登记直接返回已有版本。
* 图集属档案级 → ``uv_atlas_ref`` 记在图集版本上，不在单时相内自增。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import IO, Any

from .models import Artifact, CanonicalAsset, Epoch

__all__ = ["Archive", "ReadOnlyAsset", "ReadOnlyViolation", "ARCHIVE_DIR"]

log = logging.getLogger(__name__)

#: 档案目录名（位于项目根下，与 ``阶段成果`` / ``交付成果`` 平级）。
ARCHIVE_DIR = "archive"

#: 判定为「写」的打开模式。``+`` 一律视为写，因为 r+ 可写。
_WRITE_MODES = ("w", "a", "x", "+")


class ReadOnlyViolation(RuntimeError):
    """对只读挂载的产物发起写操作。

    指南 1.2 的实施建议：只读约束必须由代码强制。分析入口拿到的必须是句柄，
    任何写操作被拒绝并抛异常 —— 这是防止「分析过程污染基准数据」的唯一可靠手段。
    """


def _atomic_write_json(path: str, payload: dict[str, Any]) -> None:
    """原子落盘 JSON。

    与 ``core.metadata.RunManifest.save`` 同一套纪律：管线随时可能被硬杀
    （超时 taskkill / 取消 / 断电），就地写会留下截断的 JSON，之后每次启动都
    按损坏文件解析，项目再也打不开。档案是比 manifest 更根本的状态，更不能省这一步。
    """
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _load_json(path: str) -> dict[str, Any] | None:
    """读 JSON；损坏时改名留档并返回 None（不抛）。

    档案损坏不应让「项目打不开」。改名 ``.corrupt-<pid>`` 保留现场，记 WARNING，
    由调用方决定是重建还是报缺失。
    """
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("顶层不是对象")
        return data
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        broken = f"{path}.corrupt-{os.getpid()}"
        try:
            os.replace(path, broken)
        except OSError:
            pass
        log.warning("档案文件损坏，已改名保留现场：%s（%s）", broken, exc)
        return None


@dataclass(frozen=True)
class ReadOnlyAsset:
    """规范产物的只读句柄。

    只暴露 ``path`` / ``read_bytes`` / ``open``；``open`` 收到写模式即抛
    :class:`ReadOnlyViolation`。刻意**不提供** ``write_bytes`` 之类的方法 ——
    不给调用方留下"也许能写"的错觉。
    """

    epoch_id: str
    version: int
    content_key: str
    _paths: dict[str, str] = field(default_factory=dict)

    def path(self, kind: str = "mesh") -> str:
        if kind not in self._paths:
            raise KeyError(f"该产物不含 {kind}（可用：{sorted(self._paths)}）")
        return self._paths[kind]

    def kinds(self) -> list[str]:
        return sorted(self._paths)

    def read_bytes(self, kind: str = "mesh") -> bytes:
        with open(self.path(kind), "rb") as fh:
            return fh.read()

    def open(self, kind: str = "mesh", mode: str = "rb") -> IO[Any]:
        """以只读方式打开产物；写模式一律拒绝。"""
        if any(flag in mode for flag in _WRITE_MODES):
            raise ReadOnlyViolation(
                f"规范产物只读（{self.epoch_id} v{self.version}），"
                f"拒绝以 {mode!r} 打开。分析结果请另存到交付目录。")
        return open(self.path(kind), mode)


class Archive:
    """一个项目的文物档案。

    典型用法::

        arch = Archive.init(root, Artifact(artifact_id="relic_001", title="彩陶俑"))
        arch.add_epoch(epoch)
        asset = arch.register(asset)          # 版本自增；同 content_key 直接命中
        mount = arch.mount(asset)             # 只读句柄
    """

    def __init__(self, root: str, artifact: Artifact,
                 epochs: dict[str, Epoch] | None = None) -> None:
        self._root = os.path.abspath(root)
        self._artifact = artifact
        self._epochs: dict[str, Epoch] = epochs or {}

    # -- 构造 ---------------------------------------------------------------
    @classmethod
    def init(cls, root: str, artifact: Artifact) -> "Archive":
        """建立/打开档案。**幂等**：已存在则沿用，不覆盖现有 Artifact 与时相。

        GUI 每次打开项目都会走到这里，所以「重复 init 清空档案」是绝对不能有的行为。
        """
        arch_dir = os.path.join(os.path.abspath(root), ARCHIVE_DIR)
        os.makedirs(os.path.join(arch_dir, "epochs"), exist_ok=True)
        os.makedirs(os.path.join(arch_dir, "assets"), exist_ok=True)
        art_path = os.path.join(arch_dir, "artifact.json")
        existing = _load_json(art_path)
        if existing:
            return cls(root, Artifact.from_dict(existing),
                       cls._read_epochs(arch_dir))
        _atomic_write_json(art_path, artifact.to_dict())
        return cls(root, artifact, {})

    @classmethod
    def open(cls, root: str) -> "Archive":
        """打开已有档案；不存在时抛 :class:`FileNotFoundError`。"""
        arch_dir = os.path.join(os.path.abspath(root), ARCHIVE_DIR)
        art_path = os.path.join(arch_dir, "artifact.json")
        data = _load_json(art_path)
        if not data:
            raise FileNotFoundError(
                f"该目录没有文物档案：{art_path}（先跑 init 建档）")
        return cls(root, Artifact.from_dict(data), cls._read_epochs(arch_dir))

    @staticmethod
    def _read_epochs(arch_dir: str) -> dict[str, Epoch]:
        out: dict[str, Epoch] = {}
        ep_dir = os.path.join(arch_dir, "epochs")
        if not os.path.isdir(ep_dir):
            return out
        for name in sorted(os.listdir(ep_dir)):
            if not name.endswith(".json"):
                continue
            data = _load_json(os.path.join(ep_dir, name))
            if not data:
                continue
            try:
                ep = Epoch.from_dict(data)
            except ValueError as exc:
                log.warning("时相文件字段非法，跳过：%s（%s）", name, exc)
                continue
            out[ep.epoch_id] = ep
        return out

    # -- 路径 ---------------------------------------------------------------
    @property
    def root(self) -> str:
        return self._root

    @property
    def archive_dir(self) -> str:
        return os.path.join(self._root, ARCHIVE_DIR)

    def _epoch_path(self, epoch_id: str) -> str:
        return os.path.join(self.archive_dir, "epochs", f"{epoch_id}.json")

    def _asset_path(self, epoch_id: str, version: int) -> str:
        return os.path.join(self.archive_dir, "assets",
                            f"{epoch_id}__v{version}.json")

    def _abs(self, rel: str) -> str:
        """把档案里的相对路径解析成绝对路径，并锁在项目根内。

        相对路径可能被手工改成 ``../../x``。若不拦，``mount`` 会安静地给出项目
        之外的句柄 —— 只读约束形同虚设，且报错时用户看不懂为什么「文件在」却打不开。
        """
        base = self._root
        target = os.path.normpath(os.path.join(base, rel))
        if not (target == base or target.startswith(base + os.sep)):
            raise ValueError(f"产物路径越出项目根：{rel}")
        return target

    # -- 查询 ---------------------------------------------------------------
    def artifact(self) -> Artifact:
        return self._artifact

    def epochs(self) -> list[Epoch]:
        return sorted(self._epochs.values(), key=lambda e: (e.captured_on, e.epoch_id))

    def epoch(self, epoch_id: str) -> Epoch:
        if epoch_id not in self._epochs:
            raise KeyError(f"档案中没有时相 {epoch_id}")
        return self._epochs[epoch_id]

    def baseline(self) -> Epoch | None:
        for ep in self.epochs():
            if ep.is_baseline:
                return ep
        return None

    def assets(self, epoch_id: str) -> list[CanonicalAsset]:
        """该时相的全部规范产物版本（按 version 升序）。"""
        prefix = f"{epoch_id}__v"
        out: list[CanonicalAsset] = []
        adir = os.path.join(self.archive_dir, "assets")
        if not os.path.isdir(adir):
            return out
        for name in sorted(os.listdir(adir)):
            if not (name.startswith(prefix) and name.endswith(".json")):
                continue
            data = _load_json(os.path.join(adir, name))
            if data:
                out.append(CanonicalAsset.from_dict(data))
        return sorted(out, key=lambda a: a.version)

    def latest(self, epoch_id: str) -> CanonicalAsset | None:
        found = self.assets(epoch_id)
        return found[-1] if found else None

    def lookup(self, content_key: str) -> CanonicalAsset | None:
        """按内容哈希查已登记的产物（跨时相）。命中即复用，不重算。"""
        adir = os.path.join(self.archive_dir, "assets")
        if not os.path.isdir(adir):
            return None
        for name in sorted(os.listdir(adir)):
            if not name.endswith(".json"):
                continue
            data = _load_json(os.path.join(adir, name))
            if data and data.get("content_key") == content_key:
                return CanonicalAsset.from_dict(data)
        return None

    # -- 写入 ---------------------------------------------------------------
    def add_epoch(self, epoch: Epoch) -> Epoch:
        """登记一个时相。

        基准期规则（ADR-0001 未决项之一，此处定）：
        * 档案里还没有基准期 → 新时相自动成为基准期（否则用户首次建档没有坐标系锚点）；
        * 显式 ``is_baseline=True`` → 允许改换基准期，但**必须留 WARNING**
          （换基准期意味着图集与尺度基准要重定，历史结论可能失效，不能静默发生）。
        """
        if epoch.artifact_id != self._artifact.artifact_id:
            raise ValueError(
                f"时相 {epoch.epoch_id} 属于 {epoch.artifact_id}，"
                f"与本档案 {self._artifact.artifact_id} 不符")
        if epoch.epoch_id in self._epochs:
            raise ValueError(f"时相 {epoch.epoch_id} 已存在（时相不可就地覆盖）")

        has_baseline = any(e.is_baseline for e in self._epochs.values())
        if epoch.is_baseline and has_baseline:
            old = self.baseline()
            log.warning(
                "基准期变更：%s → %s。基准期定义坐标系与 UV 图集，"
                "更换后基于旧图集的历史分析结果需标记「图集过期、需重算」。",
                old.epoch_id if old else "?", epoch.epoch_id)
            for eid, e in list(self._epochs.items()):
                if e.is_baseline:
                    demoted = Epoch.from_dict({**e.to_dict(), "is_baseline": False})
                    self._epochs[eid] = demoted
                    _atomic_write_json(self._epoch_path(eid), demoted.to_dict())
        elif not has_baseline and not epoch.is_baseline:
            epoch = Epoch.from_dict({**epoch.to_dict(), "is_baseline": True})

        self._epochs[epoch.epoch_id] = epoch
        _atomic_write_json(self._epoch_path(epoch.epoch_id), epoch.to_dict())
        return epoch

    def register(self, asset: CanonicalAsset) -> CanonicalAsset:
        """登记规范产物：同内容哈希命中缓存，否则新增版本。

        返回**实际生效**的产物（可能是已有版本，``version`` 会与入参不同）。
        旧版本文件一律保留 —— 历史分析结论要能复现。
        """
        if asset.epoch_id not in self._epochs:
            raise KeyError(f"档案中没有时相 {asset.epoch_id}，不能登记其产物")
        hit = self.lookup(asset.content_key)
        if hit is not None:
            log.info("内容哈希命中，复用 %s v%d（跳过重算）",
                     hit.epoch_id, hit.version)
            return hit
        existing = self.assets(asset.epoch_id)
        version = (existing[-1].version + 1) if existing else 1
        saved = CanonicalAsset.from_dict({**asset.to_dict(), "version": version})
        _atomic_write_json(self._asset_path(asset.epoch_id, version),
                           saved.to_dict())
        return saved

    # -- 只读挂载 -----------------------------------------------------------
    def rewrite_asset(self, asset: CanonicalAsset) -> CanonicalAsset:
        """**就地**改写一条已登记资产，只允许改引用类字段。

        存在的理由只有一条：存量档案里 ``uv_atlas_ref`` 被写成了不存在的图集
        （B1 切片 7 之前的 ``migrate`` 与 runner 都写死 ``atlas-v1``），需要一次
        显式修复。修复**不能**走 :meth:`register` —— 那会按 ``content_key`` 命中
        缓存直接返回旧记录（改了也不生效），或者在哈希不同时另起一个版本
        （历史变成两条，谁也说不清哪条是真的）。

        因此这里把口子收窄到「引用元数据」：``uv_atlas_ref`` 与 ``params`` 之外
        的任何字段（产物路径、内容键、时相、版本号）与盘上的记录不一致，
        一律拒绝 —— 否则这个接口就成了「悄悄篡改历史」的后门。
        """
        path = self._asset_path(asset.epoch_id, asset.version)
        current = _load_json(path)
        if current is None:
            raise FileNotFoundError(
                f"档案里没有 {asset.epoch_id} v{asset.version} 这条资产，不能改写")
        old = CanonicalAsset.from_dict(current)
        for name in ("content_key", "mesh_path", "cloud_path", "texture_path",
                     "cameras_path"):
            if getattr(old, name) != getattr(asset, name):
                raise ValueError(
                    f"只有引用类字段可改：{name} 从 {getattr(old, name)!r} 变成 "
                    f"{getattr(asset, name)!r}，这会篡改历史产物")
        saved = CanonicalAsset.from_dict({**asset.to_dict(),
                                          "version": old.version})
        _atomic_write_json(path, saved.to_dict())
        return saved

    def mount(self, asset: CanonicalAsset) -> ReadOnlyAsset:
        """把产物挂成只读句柄，供分析入口（X1 / X3 / A0…）使用。"""
        paths: dict[str, str] = {}
        for kind, rel in (("mesh", asset.mesh_path),
                          ("cloud", asset.cloud_path),
                          ("texture", asset.texture_path),
                          ("cameras", asset.cameras_path)):
            if rel:
                paths[kind] = self._abs(rel)
        return ReadOnlyAsset(epoch_id=asset.epoch_id, version=asset.version,
                             content_key=asset.content_key, _paths=paths)
