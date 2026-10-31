"""h3d.archive.inherit_epoch : 跨项目图集继承的编排层（B1）。

场景（ADR-0002 D1）
-------------------
基准期在 A 项目，复测期在 B 项目。两件是同一件文物的两次采集，各自跑过全流程。
B 的纹理要能与 A 逐 texel 比色，就必须烘到 **A 的 UV 布局**上。

本模块把「读基准图集 → 求跨期位姿 → 重投影 → 落盘 → 登记」串起来；算法本身
在 :mod:`h3d.archive.inherit`（纯函数，可单独测）。

三条不兜底的失败
----------------
1. 基准期没有图集记录 → 说明基准期没跑过 M4，或跑的是不落 UV 的旧版本；
2. UV 布局文件缺失/损坏 → 继承下去会烘出全黑图集，而流程显示成功；
3. 自动配准被质量闸门拒绝 → 拿恒等变换去烘等于把纹理整体错位。

这三条都返回 ``failed`` 并把缺什么写进 ``message``：B1 的价值全在「同一套
texel」上，一个错了的图集比没有图集更危险 —— 它会被当成可引用的数据。
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..core import paths as _paths
from . import content_key as _content_key_fn
from . import file_digest
from .atlas import AtlasRegistry, load_uv_layout_ex
from .inherit import Similarity, inherit_texture
from .models import CanonicalAsset
from .store import Archive

__all__ = ["InheritResult", "inherit_epoch"]

log = logging.getLogger(__name__)

#: 继承产物的默认图集尺寸（与 M4 默认 2048 对齐）。
DEFAULT_SIZE = 2048

#: 自动配准用的采样上限。网格顶点直接当点云用，过多只会拖慢 ICP。
_MAX_REG_POINTS = 20000


@dataclass(frozen=True)
class InheritResult:
    """一次继承的结果。``ok=False`` 时 ``message`` 必须能告诉用户缺什么。"""

    ok: bool
    state: str                      # done / failed
    message: str
    texture_path: str = ""
    atlas_version: str = ""
    atlas_ref: str = ""             # 跨项目限定名 <artifact>@atlas-vN
    base_project: str = ""
    source_project: str = ""
    base_epoch: str = ""
    source_epoch: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


def _fail(message: str, **kw: Any) -> InheritResult:
    log.warning("图集继承失败：%s", message)
    return InheritResult(ok=False, state="failed", message=message, **kw)


def _photo_dir(stage: str, item: str) -> str:
    """复测期照片目录：优先 ``00_photo/<item>``，回退平铺的 ``00_photo``。"""
    base = os.path.join(stage, "00_photo")
    item_dir = os.path.join(base, item)
    if os.path.isdir(item_dir):
        return item_dir
    return base


def _solve_transform(base_mesh, source_mesh,
                     n_samples: int = _MAX_REG_POINTS):
    """自动求「复测网格 → 基准网格」的相似变换。

    直接复用 ``geo_align.align_points_to_mesh``：它带质量闸门，判定无效时
    返回恒等并写 ``rejected``。恒等**不能**当成"配准成功"用（见模块文档
    第 3 条），所以这里返回 ``None`` 让编排层失败。
    """
    from h3d.geo_align import align_points_to_mesh

    pts = np.asarray(source_mesh.vertices, dtype=np.float64)
    if not len(pts):
        return None, {"reason": "复测网格没有顶点"}
    if len(pts) > n_samples:
        idx = np.linspace(0, len(pts) - 1, n_samples).astype(np.int64)
        pts = pts[idx]
    s, R, T, metrics = align_points_to_mesh(pts, base_mesh)
    if metrics.get("rejected") or metrics.get("accepted") is False:
        return None, metrics
    return Similarity(R=R, t=np.asarray(T, dtype=np.float64).reshape(3),
                      s=float(s)), metrics


def inherit_epoch(base_project: str, source_project: str, *, transform=None,
                  size: int | None = None, top_k: int = 3,
                  ) -> InheritResult:
    """把复测项目的影像烘到基准项目的图集上，并登记为复测项目的一份新版本。

    参数
    ----
    base_project / source_project: 两个项目根（也可以是各自的 ``阶段成果``）。
    transform: 显式给定的相似变换（``Similarity`` 或 4×4 矩阵）。不给则自动配准。
    size: 图集尺寸；默认沿用基准图集记录里的 ``size_px``，没有再退 2048。
    """
    from h3d.io import load_mesh, make_mesh
    from h3d.photo_texture import load_capture_meta

    base = _paths.project_root_of(os.path.abspath(base_project))
    src = _paths.project_root_of(os.path.abspath(source_project))

    # -- 1. 两边都必须已建档 ------------------------------------------------
    try:
        arch_b = Archive.open(base)
    except FileNotFoundError:
        return _fail(f"基准项目没有文物档案：{base}（先打开项目或 h3d archive migrate）")
    if os.path.abspath(base) == os.path.abspath(src):
        return _fail("基准项目与复测项目是同一个目录；继承需要一个独立项目承载复测期")
    try:
        arch_s = Archive.open(src)
    except FileNotFoundError:
        return _fail(f"复测项目没有文物档案：{src}（先打开项目建档）")

    ep_b = arch_b.baseline()
    if ep_b is None:
        return _fail("基准项目档案里没有基准期")
    asset_b = arch_b.latest(ep_b.epoch_id)
    if asset_b is None:
        return _fail(f"基准期 {ep_b.epoch_id} 还没有规范产物（先跑一次完整管线）")
    atlas_ver = asset_b.uv_atlas_ref
    if not atlas_ver:
        return _fail(f"基准期 {ep_b.epoch_id} 的产物没有登记图集版本")

    # -- 2. 图集记录与 UV 布局 ---------------------------------------------
    rec = AtlasRegistry(arch_b).get(atlas_ver)
    if rec is None:
        return _fail(
            f"基准项目没有 {atlas_ver} 的图集记录（基准期可能跑的是不落 UV 布局的"
            f"旧版本，或还没跑照片纹理）。没有图集定义就无法保证 texel 对应")
    if not rec.uv_rel_path:
        return _fail(f"{atlas_ver} 的记录里没有 UV 布局路径")
    uv_path = os.path.join(base, rec.uv_rel_path)
    try:
        layout = load_uv_layout_ex(uv_path)
    except FileNotFoundError:
        return _fail(f"基准 UV 布局文件缺失：{uv_path}")
    except ValueError as exc:
        return _fail(f"基准 UV 布局损坏：{exc}")
    uv, faces = layout.uv, layout.faces

    # 几何载体必须与 uv/faces 同拓扑：布局里存了展开后顶点就直接用；
    # 旧版布局没存（has_geometry 为假）则回退基准网格，但**必须校验索引范围**，
    # 否则会在 project_photos_to_texture 里以「index out of bounds」崩掉。
    if layout.has_geometry():
        base_mesh = make_mesh(layout.verts, faces, layout.normals)
    else:
        if not asset_b.mesh_path:
            return _fail("基准资产没有网格路径，且 UV 布局未含展开后几何")
        base_mesh_path = os.path.join(base, asset_b.mesh_path)
        if not os.path.isfile(base_mesh_path):
            return _fail(f"基准网格缺失：{base_mesh_path}")
        base_mesh = load_mesh(base_mesh_path)
        if len(layout.uv) and int(faces.max(initial=-1)) >= len(base_mesh.vertices):
            return _fail(
                "UV 布局与基准网格拓扑不符（面索引越界）：该布局是旧版生成、"
                "没有存展开后几何。重跑一次基准期照片纹理即可修复")

    # -- 3. 复测期相机与照片 -----------------------------------------------
    item = arch_s.artifact().artifact_id
    stage_s = _paths.stage_root(src)
    meta_path = os.path.join(stage_s, "90_meta", f"{item}_capture_meta.json")
    if not os.path.isfile(meta_path):
        return _fail(f"复测期缺少相机位姿：{meta_path}")
    try:
        cameras, images = load_capture_meta(meta_path)
    except (OSError, ValueError, KeyError) as exc:
        return _fail(f"复测期相机位姿读取失败：{exc}")
    if not images:
        return _fail("复测期相机位姿为空（capture_meta 里没有 images）")
    photo_dir = _photo_dir(stage_s, item)
    if not os.path.isdir(photo_dir):
        return _fail(f"复测期照片目录不存在：{photo_dir}")

    ep_s = arch_s.baseline() or arch_s.epochs()[0]

    # -- 4. 位姿变换 --------------------------------------------------------
    reg_metrics: dict[str, Any] = {}
    if transform is None:
        src_mesh_path = os.path.join(stage_s, "30_dense", item, f"{item}.ply")
        if not os.path.isfile(src_mesh_path):
            return _fail(f"自动配准需要复测期网格：{src_mesh_path}（或用 --matrix 指定变换）")
        src_mesh = load_mesh(src_mesh_path)
        sim, reg_metrics = _solve_transform(base_mesh, src_mesh)
        if sim is None:
            reason = reg_metrics.get("reason") or "质量闸门判无效"
            return _fail(f"跨期配准未通过，拒绝用未校验的变换烘焙纹理（{reason}）",
                         base_project=base, source_project=src)
    elif isinstance(transform, Similarity):
        sim = transform
    else:
        try:
            sim = Similarity.from_matrix(transform)
        except ValueError as exc:
            return _fail(f"给定的变换矩阵不可用：{exc}")

    # -- 5. 重投影（用基准 mesh/uv/faces，只换相机与照片）-------------------
    atlas_ref = f"{arch_b.artifact().artifact_id}@{atlas_ver}"
    use_size = int(size or rec.size_px or DEFAULT_SIZE)
    try:
        image, metrics = inherit_texture(
            base_mesh, uv, faces, images, cameras, photo_dir, sim,
            size=use_size, top_k=top_k, atlas_version=atlas_ver,
            base_epoch=ep_b.epoch_id, source_epoch=ep_s.epoch_id)
    except Exception as exc:  # noqa: BLE001 - 投影失败要给可读原因
        return _fail(f"重投影失败：{exc}", base_project=base, source_project=src)

    # -- 6. 落盘（复测项目自己的 50_tex，不覆盖它原有的独立图集）-----------
    out_dir = os.path.join(stage_s, "50_tex", item)
    os.makedirs(out_dir, exist_ok=True)
    tex_path = os.path.join(out_dir, f"{item}__{atlas_ref}_inherited.png")
    try:
        from PIL import Image as _Image

        _Image.fromarray(np.asarray(image, dtype=np.uint8)).save(tex_path)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"继承图集落盘失败：{exc}", base_project=base, source_project=src)

    # -- 7. 登记（只新增版本，旧版本原样保留）------------------------------
    transform_snapshot = {"R": sim.R.round(9).tolist(),
                          "t": sim.t.round(9).tolist(),
                          "s": round(sim.s, 9)}
    key = _content_key_fn(
        {"texture": file_digest(tex_path), "uv": rec.uv_digest},
        {"atlas_ref": atlas_ref, "transform": transform_snapshot,
         "size": use_size, "top_k": int(top_k), "inherit_schema": 1})
    try:
        saved = arch_s.register(CanonicalAsset(
            epoch_id=ep_s.epoch_id, version=1, content_key=key,
            mesh_path=None, cloud_path=None, texture_path=os.path.relpath(
                tex_path, src).replace(os.sep, "/"),
            uv_atlas_ref=atlas_ref, cameras_path=None,
            params={
                "inherited_from": {
                    "project": base,
                    "artifact_id": arch_b.artifact().artifact_id,
                    "epoch_id": ep_b.epoch_id,
                    "atlas_version": atlas_ver,
                    "mesh_rel_path": asset_b.mesh_path,
                },
                "transform": transform_snapshot,
                "registration": {k: v for k, v in reg_metrics.items()
                                 if isinstance(v, (int, float, str, bool))},
                "pipeline": "archive.inherit",
            },
            created_at=_dt.datetime.now().isoformat(timespec="seconds")))
    except (KeyError, ValueError) as exc:
        return _fail(f"档案登记失败：{exc}", base_project=base, source_project=src)

    log.info("图集继承完成：%s ← %s（%s，v%d，图集 %s）",
             item, arch_b.artifact().artifact_id, atlas_ref, saved.version,
             use_size)
    return InheritResult(
        ok=True, state="done",
        message=(f"已继承：{atlas_ref} → {item} v{saved.version}"
                 f"（{use_size}²，transform s={sim.s:.4f}）"),
        texture_path=tex_path, atlas_version=atlas_ver, atlas_ref=atlas_ref,
        base_project=base, source_project=src,
        base_epoch=ep_b.epoch_id, source_epoch=ep_s.epoch_id,
        metrics=metrics)
