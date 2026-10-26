"""h3d.texture.atlas_cache : UV 图集与纹理图集跨件复用（M4 / P1 共用）。

实机背景（2026-10-24，v1.1.5 真机 5 件）
----------------------------------------
同一批照片的 5 件（relic_001BMYyyyy / relic_666 / relic_6669997 / rrrrr /
yyyyyyy）经 S1 指纹复用后拿到**逐字节相同**的 ``fused_dense.ply`` 与
``capture_meta.json`` → M3 网格相同 → M4 里两件最贵的事被重复做了 5 次：

    xatlas UV 展开         实测 33 s/件
    逐纹素多视角采样+融合   实测 50 s/件
    ------------------------------------------
    合计                   83~85 s/件 × 5 件 ≈ 7 min，产出 5 张完全相同的图集

复用为什么安全（这是省时间的前提，必须逐项成立）
------------------------------------------------
1. **UV 只由几何决定**（顶点坐标 + 三角形索引）。xatlas 无随机源、线程数固定，
   同输入同输出 → 几何指纹相同即可复用 ``vmap/indices/uvs``。
2. **纹理像素由五项决定**：几何（含法线）+ 相机位姿 + 照片内容 + 采样参数
   + 缓存版本。任一项变化即 key 变化，必然重算（不会把 A 件的贴图按到 B 件上）。
3. **导出不缓存**：交付文件名含 ``item_id``（``<item>_L1.0_diffuse.png`` /
   ``.obj`` / ``.mtl`` / ``.glb``），复制还需改写 MTL 的 ``map_Kd``；重导出
   单件仅 1~3 s，远低于出错重做的代价。

与 S1 复用一致的三条硬边界
--------------------------
* **缺一不复用**：UV 缓存必须同时含 ``vmap/indices/uvs``；纹理缓存必须同时含
  ``image/mask/json``，缺任一件按未命中处理（不半复用地拼出畸形结果）。
* **参数不一致不复用**：``texture_size/top_k/samples_per_tri/poisson`` 进 key。
* **三处留痕**：日志一行、metrics（``atlas_cache``/``atlas_reused_from``）、
  报表（metrics 全量落盘）。

损坏可恢复
----------
派生缓存损坏只能影响"是否省时间"，绝不能影响主流程：读失败 → 改名
``.corrupt-<ts>`` + WARNING + 按未命中重算（与 manifest 自愈同一套思路）。
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import time
from typing import Any, Optional

import numpy as np

from h3d.core import get_logger

log = get_logger("texture.atlas_cache")

#: 缓存格式版本。几何/纹理语义一旦改动（例如换了 UV 展开算法、改了加权公式）
#: 必须 +1，否则旧缓存会把"上一版算法"的结果伪装成当前算法的产出。
CACHE_VERSION = 1

_CACHE_REL = os.path.join("99_runs", "atlas_cache")


def _update_array(h: "hashlib._Hash", arr: np.ndarray, tag: bytes) -> None:
    arr = np.ascontiguousarray(arr)
    h.update(tag)
    h.update(struct.pack("<ii", *arr.shape))
    h.update(arr.dtype.str.encode("ascii"))
    h.update(arr.tobytes())


def mesh_fingerprint(mesh, with_normals: bool = False) -> str:
    """几何指纹：``float32`` 顶点 + ``int32`` 面（可选法线）。

    为什么先降到 float32：不同来源的同一网格可能在 double 末位有 1e-12 级差异
    （Ply 读写/变换链），而 float32 量化后仍逐位相同；反过来，真不同的几何
    （哪怕整体平移 1e-3）必然哈希不同。顶点**顺序**参与哈希，因为 UV 的
    ``vmap`` 是按原顶点下标索引的，顺序不同不能通用。
    """
    verts = np.asarray(mesh.vertices, dtype=np.float32)
    faces = np.asarray(mesh.triangles, dtype=np.int32)
    h = hashlib.sha1()
    h.update(b"geom-v1\x00")
    _update_array(h, verts, b"v")
    _update_array(h, faces, b"f")
    if with_normals:
        if getattr(mesh, "has_vertex_normals", lambda: False)():
            nrm = np.asarray(mesh.vertex_normals, dtype=np.float32)
        else:  # 法线缺失时用空数组占位，避免"有的件有法线有的没有"混用同一纹理
            nrm = np.zeros((0, 3), dtype=np.float32)
        _update_array(h, nrm, b"n")
    return h.hexdigest()[:20]


def pose_fingerprint(cameras: dict, images: list) -> str:
    """位姿指纹：内参（fx/fy/cx/cy/w/h）+ 每张图的名字与外参（R, t）。

    按图像名排序后再哈希：capture_meta 的图像顺序可能因 COLMAP 版本/写入顺序
    变化，而「同一组相机姿态」不该因为顺序不同就判为不同。位姿的数值分辨率
    仍走 float32——SfM 复用是文件级复制，数值必然逐位一致。
    """
    h = hashlib.sha1()
    h.update(b"pose-v1\x00")
    for cam_id in sorted(cameras, key=str):
        c = cameras[cam_id]
        vals = [c.get(k, 0.0) or 0.0
                for k in ("fx", "fy", "cx", "cy", "w", "h")]
        h.update(repr((str(cam_id), *[round(float(v), 6) for v in vals]))
                 .encode("utf-8"))
    for im in sorted(images, key=lambda x: str(x.get("name", ""))):
        cam = cameras.get(im.get("cam_id"), {})
        inner = [cam.get(k, 0.0) or 0.0 for k in ("fx", "fy", "cx", "cy")]
        R = np.asarray(im.get("R", np.eye(3)), dtype=np.float64).reshape(9)
        t = np.asarray(im.get("t", np.zeros(3)), dtype=np.float64).reshape(3)
        h.update(str(im.get("name", "")).encode("utf-8"))
        h.update(np.round(np.concatenate([R, t]), 6).astype(np.float32).tobytes())
        h.update(np.round(np.asarray(inner, dtype=np.float64), 6)
                 .astype(np.float32).tobytes())
    return h.hexdigest()[:20]


def photo_fingerprint(photo_dir: str) -> str:
    """照片内容指纹（名字 + 内容 SHA-1）；与 S1 复用同一实现，避免两套口径。"""
    from h3d.sfm_solve import photo_set_fingerprint
    try:
        return photo_set_fingerprint(photo_dir)
    except OSError as exc:      # 读不了照片 → 不缓存（宁可不省，不能错省）
        log.warning("照片指纹计算失败（本次不复用纹理图集）：%s", exc)
        return "unreadable"


def params_fingerprint(cfg) -> str:
    """影响纹理像素的采样/融合参数。改任一项都必须重算。"""
    p = cfg.photo_texture
    key = "|".join(str(v) for v in (
        CACHE_VERSION, p.texture_size, p.top_k, p.samples_per_tri,
        int(bool(p.poisson_blend))))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _atomic_write_bytes(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _quarantine(path: str) -> None:
    """损坏文件改名留档：既不阻断流程，也不静默丢掉证据。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        os.replace(path, f"{path}.corrupt-{stamp}")
    except OSError:
        pass


class AtlasCache:
    """``<task root>/99_runs/atlas_cache`` 下的 UV / 纹理图集缓存。

    用法::

        cache = AtlasCache(task.root, enabled=cfg.photo_texture.reuse_atlas)
        uv_hit = cache.get_uv(geom_fp)          # -> (vmap, indices, uvs, meta) | None
        tex_hit = cache.get_texture(key)        # -> (image, mask, meta) | None
    """

    def __init__(self, task_root: str, enabled: bool = True,
                 max_textures: int = 24):
        self.root = os.path.join(task_root, _CACHE_REL)
        self.enabled = bool(enabled)
        self.max_textures = int(max_textures)

    # ---------------- UV ----------------
    def _uv_path(self, geom_fp: str) -> str:
        return os.path.join(self.root, "uv", f"v{CACHE_VERSION}_{geom_fp}.npz")

    def get_uv(self, geom_fp: str) -> Optional[tuple]:
        if not self.enabled:
            return None
        path = self._uv_path(geom_fp)
        if not os.path.exists(path):
            return None
        try:
            with np.load(path, allow_pickle=False) as z:
                vmap = np.asarray(z["vmap"], dtype=np.int64)
                indices = np.asarray(z["indices"], dtype=np.int64)
                uvs = np.asarray(z["uvs"], dtype=np.float32)
            if vmap.ndim != 1 or indices.ndim != 2 or uvs.shape != (len(vmap), 2):
                raise ValueError(f"形状不自洽 vmap{vmap.shape} "
                                 f"indices{indices.shape} uvs{uvs.shape}")
            meta = {}
            jpath = path[:-4] + ".json"
            if os.path.exists(jpath):
                with open(jpath, encoding="utf-8") as fh:
                    meta = json.load(fh)
            return vmap, indices, uvs, meta
        except (OSError, ValueError, KeyError) as exc:
            log.warning("UV 图集缓存不可用（按未命中重算）：%s（%s）",
                        os.path.basename(path), exc)
            _quarantine(path)
            return None

    def put_uv(self, geom_fp: str, vmap, indices, uvs, item_id: str) -> None:
        if not self.enabled:
            return
        path = self._uv_path(geom_fp)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.tmp{os.getpid()}"
            np.savez(tmp + ".npz", vmap=np.asarray(vmap, dtype=np.int64),
                     indices=np.asarray(indices, dtype=np.int64),
                     uvs=np.asarray(uvs, dtype=np.float32))
            os.replace(tmp + ".npz", path)
            _atomic_write_bytes(
                path[:-4] + ".json",
                json.dumps({"version": CACHE_VERSION, "source_item": item_id,
                            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "n_verts": int(len(vmap)),
                            "n_faces": int(len(indices))},
                           ensure_ascii=False).encode("utf-8"))
        except OSError as exc:      # 写不进缓存只损失速度
            log.warning("UV 图集缓存写入失败（不影响交付）：%s", exc)
            # np.savez 会在 tmp 后补 .npz，异常分支要自己清残留
            try:
                os.remove(f"{path}.tmp{os.getpid()}.npz")
            except OSError:
                pass

    # ---------------- 纹理 ----------------
    def texture_key(self, geom_fp: str, nrm_fp: str, pose_fp: str,
                    photo_fp: str, param_fp: str) -> str:
        raw = "|".join((f"v{CACHE_VERSION}", geom_fp, nrm_fp, pose_fp,
                        photo_fp, param_fp))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]

    def _tex_path(self, key: str) -> str:
        return os.path.join(self.root, "tex", f"{key}.png")

    def get_texture(self, key: str) -> Optional[tuple]:
        """返回 ``(image_uint8, painted_bool, meta)``；缺任一件即未命中。"""
        if not self.enabled:
            return None
        img_path = self._tex_path(key)
        mask_path = img_path[:-4] + ".mask.png"
        json_path = img_path[:-4] + ".json"
        for p in (img_path, mask_path, json_path):
            if not os.path.exists(p):
                if os.path.exists(img_path):   # 只有半套 → 明说，别再猜
                    log.warning("纹理缓存不完整（缺 %s），按未命中重算",
                                os.path.basename(p))
                return None
        try:
            from PIL import Image as _Image
            image = np.asarray(_Image.open(img_path).convert("RGB"))
            mask = np.asarray(_Image.open(mask_path).convert("L")) > 127
            with open(json_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            if meta.get("version") != CACHE_VERSION:
                return None
            if image.shape[:2] != mask.shape:
                raise ValueError(f"图/掩膜尺寸不一致 {image.shape} {mask.shape}")
            return image, mask, meta
        except (OSError, ValueError, KeyError) as exc:
            log.warning("纹理图集缓存不可用（按未命中重算）：%s", exc)
            for p in (img_path, mask_path, json_path):
                if os.path.exists(p):
                    _quarantine(p)
            return None

    def put_texture(self, key: str, image: np.ndarray, painted: np.ndarray,
                    metrics: dict, item_id: str) -> None:
        if not self.enabled:
            return
        try:
            from PIL import Image as _Image
            img_path = self._tex_path(key)
            os.makedirs(os.path.dirname(img_path), exist_ok=True)
            tmp = f"{img_path}.tmp{os.getpid()}.png"
            _Image.fromarray(np.asarray(image, dtype=np.uint8)).save(tmp)
            os.replace(tmp, img_path)
            tmpm = f"{img_path}.tmp{os.getpid()}.mask.png"
            _Image.fromarray((np.asarray(painted, dtype=bool)
                              .astype(np.uint8) * 255)).save(tmpm)
            os.replace(tmpm, img_path[:-4] + ".mask.png")
            payload = {"version": CACHE_VERSION, "source_item": item_id,
                       "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "metrics": _jsonable(metrics)}
            _atomic_write_bytes(img_path[:-4] + ".json",
                                json.dumps(payload, ensure_ascii=False)
                                .encode("utf-8"))
            self._evict()
        except (OSError, ValueError) as exc:
            log.warning("纹理图集缓存写入失败（不影响交付）：%s", exc)

    def _evict(self) -> None:
        """按 mtime 保留最近 ``max_textures`` 套，防止批量任务把盘吃满。"""
        tex_dir = os.path.join(self.root, "tex")
        try:
            pngs = [os.path.join(tex_dir, n) for n in os.listdir(tex_dir)
                    if n.endswith(".png") and ".mask." not in n]
        except OSError:
            return
        if len(pngs) <= self.max_textures:
            return
        pngs.sort(key=lambda p: os.path.getmtime(p))
        for path in pngs[:len(pngs) - self.max_textures]:
            stem = path[:-4]
            for p in (path, stem + ".mask.png", stem + ".json"):
                try:
                    os.remove(p)
                except OSError:
                    pass


def _jsonable(obj: Any) -> Any:
    """metrics 里可能有 numpy 标量 / 元组，json 化时统一降级为原生类型。"""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return _jsonable(obj.tolist())
    return obj


__all__ = ["CACHE_VERSION", "AtlasCache", "mesh_fingerprint", "pose_fingerprint",
           "photo_fingerprint", "params_fingerprint"]
