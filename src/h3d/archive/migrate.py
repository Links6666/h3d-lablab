"""h3d.archive.migrate : 把存量项目认成一个时相（B0 交付物：迁移脚本）。

背景
----
1.1.13 之前的所有项目都是「一件文物 = 一个项目目录 + 一次全流程」的扁平结构，
没有时相概念。要让档案层立刻可用，必须能把这些老项目认进来，而不是要求用户
重跑一遍。

三条约束
--------
1. **只读**：迁移只新建 ``archive/``，绝不改写 ``阶段成果`` 里的任何字节。
   顺手「规范化」产物会让历史哈希全变，等于把可追溯性一次性毁掉。
2. **幂等**：已有档案直接返回 ``already``，不重复建时相。
3. **有闸门**：只有 ``looks_like_project()`` 认可的目录才建档。``output`` / ``report``
   这种通用名不算证据 —— 2026-10-26 正是因为这条判据太松，把源码仓库的
   ``output/`` 整棵搬进了 ``阶段成果/``。
"""

from __future__ import annotations

import datetime as _dt
import logging
import os

from ..core import paths as _paths
from . import content_key as _content_key_fn
from . import file_digest
from .models import Artifact, CanonicalAsset, Epoch
from .store import Archive

__all__ = ["migrate"]

log = logging.getLogger(__name__)

#: 迁移产物 content_key 的成分之一（只影响去重键，不再写进 ``uv_atlas_ref``）。
#: 图集由基准期跑 M4 时登记（``archive.atlas.register_layout``），迁移**不知道**
#: 也不该猜：存量项目没有 ``archive/atlas/`` 记录，写一个版本号等于编造引用。
_INITIAL_ATLAS = "atlas-v1"

#: 视为「激光几何原始数据」的扩展名。
_RAW_EXTS = (".ply", ".xyz", ".pts", ".las", ".e57", ".pcd")


def _first_item(stage: str) -> str | None:
    """从 00_raw / 30_dense 的子目录名推断件号（item_id）。

    产物路径的键一直是 ``item_id``（``00_raw`` 的子目录名），不是项目名 ——
    用项目名拼会让示例项目全绿、真实项目全空。

    ``00_photo`` 也要探：纯照片路线（B）的项目根里没有 ``00_raw``，
    只按前四个目录找会判「没有文物目录」，于是这类项目永远建不了档。
    """
    for sub in ("00_raw", "00_photo", "30_dense", "10_dense", "50_tex"):
        base = os.path.join(stage, sub)
        if not os.path.isdir(base):
            continue
        candidates = [d for d in sorted(os.listdir(base))
                      if os.path.isdir(os.path.join(base, d))
                      and not d.startswith("_")]
        if candidates:
            return candidates[0]
    return None


def _find(stage: str, *parts: str) -> str | None:
    p = os.path.join(stage, *parts)
    return p if os.path.exists(p) else None


def _first_with_ext(base: str, exts: tuple[str, ...]) -> str | None:
    if not os.path.isdir(base):
        return None
    for name in sorted(os.listdir(base)):
        if name.lower().endswith(exts):
            return os.path.join(base, name)
    return None


def _date_of(path: str) -> str:
    try:
        ts = os.path.getmtime(path)
        return _dt.date.fromtimestamp(ts).isoformat()
    except OSError:
        return _dt.date.today().isoformat()


def migrate(root: str, *, epoch_id: str = "E1", title: str = "",
            force_new_epoch: bool = False,
            allow_no_asset: bool = False) -> dict:
    """把存量项目迁移进档案层。

    参数
    ----
    root: 项目根，或它的 ``阶段成果`` 目录（GUI 拖目录时很常见，两种都认）。
    epoch_id: 新时相的 id（默认 ``E1``）。
    title: 文物名称（默认取件号）。
    force_new_epoch: 已有档案时是否**再追加**一个时相。用于「同一份数据重跑后
        登记新版本」之外的场景（例如把复测期单独登记）。
    allow_no_asset: 允许「有时相、无产物」的档案。

        命令行迁移刻意**不**允许：给一个空目录建一个永远命中不了的时相，
        档案里就多了一条没有任何产物的记录，事后无法分辨「没跑」和「跑失败」。

        但 GUI「打开项目」必须允许 —— 新建项目在第一次运行之前只有采集数据，
        若此时不建时相，runner 的档案登记会因为「无基准期」永久跳过（见
        ``batch_processor._register_archive``），跑完一次也补不上。

    返回
    ----
    ``{"status": "created"|"already", "artifact_id", "epoch_id", "version",
    "content_key", "missing": [...]}``；``allow_no_asset`` 且无产物时
    ``version`` 与 ``content_key`` 为 ``None``。
    """
    proj = _paths.project_root_of(os.path.abspath(root))
    if not _paths.looks_like_h3d_project(proj):
        raise ValueError(
            f"这不是一个 H3D 项目：{proj}（缺少 config.yaml 或阶段专属目录）。"
            "为免误伤普通目录，迁移拒绝执行。")

    stage = _paths.stage_root(proj)
    item = _first_item(stage)
    if not item:
        raise ValueError(f"项目里没有任何文物目录：{stage}")

    archive_dir = os.path.join(proj, "archive")
    arch: Archive | None
    if os.path.isdir(archive_dir):
        try:
            arch = Archive.open(proj)
        except FileNotFoundError:
            arch = None
    else:
        arch = None

    if arch is not None and not force_new_epoch:
        return {"status": "already", "artifact_id": arch.artifact().artifact_id,
                "epoch_id": arch.baseline().epoch_id if arch.baseline() else None,
                "version": None, "content_key": None, "missing": []}

    # -- 探测产物（缺失只记录，不抛）--------------------------------------
    missing: list[str] = []
    mesh = _first_with_ext(os.path.join(stage, "30_dense", item), (".ply", ".obj"))
    if not mesh:
        mesh = _first_with_ext(os.path.join(stage, "10_dense", item), (".ply",))
    if not mesh:
        missing.append("mesh")

    tex = _first_with_ext(os.path.join(stage, "50_tex", item),
                          ("_diffuse.png", "_diffuse.jpg", ".png", ".jpg"))
    if not tex:
        missing.append("texture")

    meta = (os.path.join(stage, "90_meta", f"{item}_capture_meta.json")
            if os.path.isfile(os.path.join(stage, "90_meta",
                                           f"{item}_capture_meta.json"))
            else None)
    if not meta:
        missing.append("cameras")

    raw_dir = os.path.join(stage, "00_raw", item)
    raw_files = [os.path.join(raw_dir, f) for f in sorted(os.listdir(raw_dir))
                 if f.lower().endswith(_RAW_EXTS)] if os.path.isdir(raw_dir) else []
    if not raw_files:
        missing.append("raw")

    def rel(p: str | None) -> str | None:
        if not p:
            return None
        # 档案只存相对路径（ADR-0001 D3）：换盘符 / 换机器后绝对路径必断
        return os.path.relpath(p, proj).replace(os.sep, "/")

    # -- 内容哈希 -----------------------------------------------------------
    inputs: dict[str, str] = {}
    for kind, path in (("mesh", mesh), ("texture", tex), ("cameras", meta)):
        if path and os.path.isfile(path):
            inputs[kind] = file_digest(path)
    if not inputs and not allow_no_asset:
        # 一件产物都没有：不该建一个永远命中不了的时相
        raise ValueError(
            f"{item} 没有任何可登记产物（30_dense / 50_tex / 90_meta 全空），"
            "先跑建模再迁移。")
    key = (_content_key_fn(inputs, {"migrator": 1, "initial_atlas": _INITIAL_ATLAS})
           if inputs else None)

    # -- 建档 ---------------------------------------------------------------
    if arch is None:
        artifact = Artifact(artifact_id=item, title=title or item)
        arch = Archive.init(proj, artifact)
    if arch.artifact().artifact_id != item:
        raise ValueError(
            f"项目档案已属于 {arch.artifact().artifact_id}，"
            f"但本次探测到 {item}；多文物项目需逐件建档，暂不支持自动合并")

    route = "A" if raw_files else "B"
    # 采集日期：优先原始几何目录的 mtime；纯照片项目退到 00_photo；都没有就取今天。
    # 不能让 mesh 缺失时崩在 ``os.path.dirname(None)`` 上（新建项目就是这种情况）。
    anchor = None
    for cand in (raw_dir, os.path.join(stage, "00_photo", item),
                 os.path.dirname(mesh) if mesh else None):
        if cand and os.path.isdir(cand):
            anchor = cand
            break
    epoch = Epoch(
        epoch_id=epoch_id,
        artifact_id=item,
        captured_on=_date_of(anchor or proj),
        route=route,
        device="",
        raw_paths=[rel(p) for p in raw_files],
        colour_protocol={},
        scale_ref={"kind": "unknown", "note": "迁移时未记录，B1 前须补"},
        qc_status="unknown",
        is_baseline=not arch.epochs(),
    )
    try:
        arch.add_epoch(epoch)
    except ValueError:
        if not force_new_epoch:
            raise
        log.warning("时相 %s 已存在，改用追加模式", epoch_id)

    saved: CanonicalAsset | None = None
    if inputs:
        asset = CanonicalAsset(
            epoch_id=epoch_id, version=1, content_key=key,
            mesh_path=rel(mesh), cloud_path=None, texture_path=rel(tex),
            uv_atlas_ref="", cameras_path=rel(meta),
            params={"migrated_from": "legacy_layout"},
            created_at=_dt.datetime.now().isoformat(timespec="seconds"),
        )
        saved = arch.register(asset)
    if missing and not allow_no_asset:
        log.warning("迁移完成，但以下产物缺失（报表会显示 N/A）：%s",
                    "、".join(missing))
    return {"status": "created", "artifact_id": item, "epoch_id": epoch_id,
            "version": saved.version if saved else None,
            "content_key": saved.content_key if saved else None,
            "missing": missing}
