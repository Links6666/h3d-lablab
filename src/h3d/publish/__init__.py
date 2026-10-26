"""h3d.publish : 发布归档（M6 发布与溯源，L2 业务层 / S4 阶段）—— 主线收官。

设计依据：
    《设计书》§37 —— M6 发布与溯源：将带纹理模型导出为标准开放格式，嵌入元数据
        与 manifest 快照，形成可交付包（80_export 导出 + 90_meta 元数据）；
    《软著方案》2.4 —— 交付包 = 导出模型 + 质检报告 + manifest 一并归档；
    《设计书》§6.3 —— 任一质量门不达标时该件不进入交付包（M5 失败已隔离）。

实现要点：
    1. 交付模型取自 M4 最高精度带色模型（50_tex/<item>/<item>_L1.<fmt>），
       复制到 80_export/<item>/ 并去掉 LOD 标签（标准命名 <item>.<fmt>）。
    2. 元数据快照（90_meta/<item>_meta.json）：项目信息 + 各阶段指标 +
       软件版本 + 配置哈希，构成溯源依据（设计书「可复算、可审计」）。
    3. 交付清单（90_meta/delivery_manifest.json）：汇总本批次全部交付包，
       含文件路径 / 状态 / 关键指标，验收依据（TC-602）。
    4. 可选把质检报告（70_report/accuracy.csv）复制进交付包（copy_qc）。
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any, Optional

from h3d.core import PublishConfig, get_logger

log = get_logger("publish")


def export_delivery(tex_dir: str, out_dir: str, item_id: str,
                    formats: Optional[list[str]] = None) -> list[str]:
    """把 50_tex 的最高精度带色模型复制为交付模型（80_export/<item>/）。

    取 ``<item>_L1.<fmt>``（LOD 1.0 原始精度，M4 产物）；缺少某格式时尝试任意
    LOD 变体并告警。目标路径不可写抛 ExportError（E601）。

    参数
    ----
    tex_dir:  M4 输出目录（50_tex/<item>/）。
    out_dir:  交付输出目录（80_export/<item>/）。
    item_id:  文物编号。
    formats:  交付格式列表（默认 [ply, obj, glb]）。

    返回
    ----
    交付模型文件绝对路径列表。
    """
    from h3d.core import ExportError

    formats = formats or ["ply", "obj", "glb"]
    os.makedirs(out_dir, exist_ok=True)
    outputs: list[str] = []
    for fmt in formats:
        fmt = fmt.lstrip(".").lower()
        # 优先 L1（原始精度），回退任意 *_L* 变体
        # 命名兼容：M4（照片纹理链路）产物为 <item>_L1.0.<fmt>，P1 顶点色链路
        # 为 <item>_L1.<fmt>——两者都要命中，避免"未找到"的噪声告警。
        src = os.path.join(tex_dir, f"{item_id}_L1.{fmt}")
        if not os.path.exists(src):
            for alias in (f"{item_id}_L1.0.{fmt}", f"{item_id}_L1.00.{fmt}"):
                cand = os.path.join(tex_dir, alias)
                if os.path.exists(cand):
                    src = cand
                    break
        if not os.path.exists(src):
            candidates = [f for f in os.listdir(tex_dir)
                          if f.startswith(f"{item_id}_L") and f.endswith(f".{fmt}")]
            if candidates:
                src = os.path.join(tex_dir, sorted(candidates)[-1])
                log.debug("%s 无 L1.%s，使用 %s", item_id, fmt,
                          os.path.basename(src))
            else:
                # 该格式未被导出（由 texture.export_formats 决定）——正常跳过，
                # 不是错误：用 info 级说明，避免误读为导出失败。
                log.info("%s 未导出 %s 格式（见 texture.export_formats），跳过",
                         item_id, fmt)
                continue
        dst = os.path.join(out_dir, f"{item_id}.{fmt}")
        try:
            shutil.copy2(src, dst)
        except OSError as exc:
            raise ExportError(f"交付模型复制失败: {dst}") from exc
        # 实机问题（2026-10-18）：OBJ 的贴图在**旁挂文件**里（.mtl + _diffuse.png）。
        # 旧实现只复制 .obj 本体 → 交付包的 mtllib 指向不存在的文件、贴图缺失，
        # 模型在任意查看器里都是灰白一片，且无任何报错。这是「从没拿到完整
        # 模型输出」的直接原因之一。必须连同旁挂文件一起交付并改写引用。
        if fmt == "obj":
            _copy_obj_sidecars(src, dst, item_id)
        outputs.append(os.path.abspath(dst))
    log.info("交付模型导出：%s（%d 格式 -> %s）", item_id, len(outputs), out_dir)
    return outputs


def deliver_texture_atlas(tex_dir: str, out_dir: str, item_id: str,
                          formats: Optional[list[str]] = None) -> Optional[str]:
    """把 M4 的纹理图集复制进交付包（``80_export/<item>/<item>_diffuse.png``）。

    为什么需要单独一步（实机 2026-10-26）：交付包原先只复制
    ``export_formats`` 里的模型文件。当配置是 ``[ply]`` 时，交付包内
    **没有任何图像纹理**——PLY 只能存顶点色，2048² 图集（2,639,926 纹素）
    留在 50_tex 里没被带走，有效色彩采样从 264 万掉到 8.5 万。
    图集是照片纹理链路的核心产物，**无论导出格式如何都必须随交付包归档**：
    它是"重新生成 obj/glb 即可复原纹理"的唯一依据，也是质检与复核的凭据。

    参数
    ----
    tex_dir:  M4 输出目录（50_tex/<item>/）。
    out_dir:  交付输出目录（80_export/<item>/）。
    item_id:  文物编号。
    formats:  本次已交付的模型格式（含 obj 时图集已由 ``_copy_obj_sidecars``
              以同名复制，这里只校验存在性、不重复搬运）。

    返回
    ----
    交付包内图集路径；无图集（顶点色路径 / uv 模式无图集）时返回 None。
    """
    formats = [str(f).lstrip(".").lower() for f in (formats or [])]
    src = _find_atlas(tex_dir, item_id)
    if not src:
        return None
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, f"{item_id}_diffuse.png")
    try:
        if not (os.path.exists(dst) and _same_file(src, dst)):
            shutil.copy2(src, dst)
    except OSError as exc:
        log.warning("图集复制失败（交付包将缺纹理图集）：%s（%s）", dst, exc)
        return None
    if "obj" in formats:
        log.info("交付包图集：%s（由 OBJ 材质引用）", os.path.basename(dst))
    else:
        log.warning("交付包只含 %s，无模型可引用图集 %s；"
                    "如需「可直接贴图的模型」，请在 texture.export_formats 里加 obj/glb",
                    formats or ["（空）"], os.path.basename(dst))
    return os.path.abspath(dst)


def _find_atlas(tex_dir: str, item_id: str) -> Optional[str]:
    """在 50_tex/<item>/ 里定位纹理图集 PNG（多命名兼容，排除着色掩膜）。"""
    if not os.path.isdir(tex_dir):
        return None
    for name in (f"{item_id}_L1.0_diffuse.png", f"{item_id}_L1_diffuse.png",
                 f"{item_id}_diffuse.png"):
        cand = os.path.join(tex_dir, name)
        if os.path.exists(cand):
            return cand
    # uv 模式（Open3D 原生命名 <item>_L1.0_0.png）/ 其他 LOD 变体
    cands = sorted(
        f for f in os.listdir(tex_dir)
        if f.lower().endswith((".png", ".jpg"))
        and f.startswith(item_id) and "mask" not in f.lower())
    return os.path.join(tex_dir, cands[0]) if cands else None


def _same_file(a: str, b: str) -> bool:
    """两个路径是否指向同一份内容（大小 + mtime 近似判定，避免重复搬运）。"""
    try:
        sa, sb = os.stat(a), os.stat(b)
    except OSError:
        return False
    return sa.st_size == sb.st_size and abs(sa.st_mtime - sb.st_mtime) < 1.0


def _copy_obj_sidecars(src_obj: str, dst_obj: str, item_id: str) -> None:
    """复制 OBJ 的材质与贴图，并改写交付包内的相对引用。

    处理链：``obj --mtllib--> mtl --map_Kd--> png``，每层跟随复制并统一改名为
    ``<item>.mtl`` / ``<item>_diffuse.png``；任何一层缺失都记 warning（不静默），
    但不阻断交付（几何模型仍有价值）。
    """
    src_dir = os.path.dirname(os.path.abspath(src_obj))
    dst_dir = os.path.dirname(os.path.abspath(dst_obj))
    try:
        with open(src_obj, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        log.warning("读取交付 OBJ 失败，跳过材质复制：%s", exc)
        return

    mtl_name = None
    for line in lines:
        s = line.strip()
        if s.lower().startswith("mtllib "):
            mtl_name = s.split(None, 1)[1].strip()
            break
    if not mtl_name:
        return  # 纯几何 obj（顶点色模式），无旁挂文件

    src_mtl = os.path.join(src_dir, mtl_name)
    if not os.path.exists(src_mtl):
        log.warning("OBJ 引用的材质文件不存在，交付模型将无贴图：%s", src_mtl)
        return
    dst_mtl_name = f"{item_id}.mtl"
    dst_mtl = os.path.join(dst_dir, dst_mtl_name)

    with open(src_mtl, encoding="utf-8", errors="replace") as fh:
        mtl_lines = fh.readlines()
    copied_tex: dict[str, str] = {}
    new_mtl: list[str] = []
    for line in mtl_lines:
        s = line.strip()
        low = s.lower()
        if low.startswith(("map_kd", "map_ka", "map_ks", "map_bump", "bump")):
            parts = s.split(None, 1)
            if len(parts) == 2:
                tex_old = parts[1].strip()
                ext = os.path.splitext(tex_old)[1] or ".png"
                tex_new = (f"{item_id}_diffuse{ext}"
                           if low.startswith(("map_kd", "map_ka"))
                           else f"{item_id}_{low.split()[0]}{ext}")
                src_tex = os.path.join(src_dir, tex_old)
                if os.path.exists(src_tex):
                    if tex_new not in copied_tex:
                        try:
                            shutil.copy2(src_tex, os.path.join(dst_dir, tex_new))
                            copied_tex[tex_new] = tex_old
                        except OSError as exc:
                            log.warning("贴图复制失败：%s（%s）", src_tex, exc)
                    indent = line[:len(line) - len(line.lstrip())]
                    new_mtl.append(f"{indent}{parts[0]} {tex_new}\n")
                    continue
                log.warning("MTL 引用的贴图不存在：%s", src_tex)
        new_mtl.append(line)

    try:
        with open(dst_mtl, "w", encoding="utf-8") as fh:
            fh.writelines(new_mtl)
    except OSError as exc:
        log.warning("材质文件写入失败：%s（%s）", dst_mtl, exc)
        return

    # 改写 OBJ 的 mtllib 指向交付包内的新材质名
    patched = []
    for line in lines:
        if line.strip().lower().startswith("mtllib "):
            indent = line[:len(line) - len(line.lstrip())]
            patched.append(f"{indent}mtllib {dst_mtl_name}\n")
        else:
            patched.append(line)
    try:
        with open(dst_obj, "w", encoding="utf-8") as fh:
            fh.writelines(patched)
    except OSError as exc:
        log.warning("OBJ 材质引用改写失败：%s（%s）", dst_obj, exc)
        return
    log.info("交付包贴图随附：%s（材质 %s，贴图 %s）",
             item_id, dst_mtl_name, list(copied_tex) or "无")


def build_meta(item_id: str, stage_metrics: dict[str, Any],
               project: dict[str, Any], software: dict[str, Any],
               run_id: str, config_hash: str) -> dict[str, Any]:
    """构建元数据快照（manifest 溯源，设计书 §37.2 embed_meta）。

    参数
    ----
    item_id:      文物编号。
    stage_metrics: 该件各阶段指标（manifest 中 <item>/<stage> 的合并）。
    project:      项目信息（name / operator 等）。
    software:     软件版本快照（core.metadata.software_versions）。
    run_id:       本次运行标识。
    config_hash:  配置哈希（可复算）。

    返回
    ----
    元数据字典（序列化到 90_meta/<item>_meta.json）。
    """
    return {
        "item_id": item_id,
        "run_id": run_id,
        "config_hash": config_hash,
        "project": project,
        "software": software,
        "stages": stage_metrics,
        "delivery": {
            "models": list(sorted(stage_metrics.get("M6", {}).get("outputs", []))),
            "qc": stage_metrics.get("M5", {}).get("status", "N/A"),
        },
    }


def write_meta(meta: dict[str, Any], meta_dir: str, item_id: str) -> str:
    """把元数据快照写为 90_meta/<item>_meta.json，返回路径。"""
    os.makedirs(meta_dir, exist_ok=True)
    path = os.path.join(meta_dir, f"{item_id}_meta.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    log.info("元数据快照：%s（%d 项）", path, len(meta))
    return os.path.abspath(path)


def copy_qc_report(task_root: str, out_dir: str, item_id: str) -> Optional[str]:
    """把质检报告 accuracy.csv 复制进交付包（可选，copy_qc=True）。"""
    src = os.path.join(task_root, "70_report", item_id, "accuracy.csv")
    if not os.path.exists(src):
        log.info("%s 无质检报告，跳过归档", item_id)
        return None
    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, "accuracy.csv")
    shutil.copy2(src, dst)
    return os.path.abspath(dst)


def build_delivery_manifest(entries: list[dict[str, Any]],
                            manifest_path: str) -> str:
    """汇总全部交付包清单（90_meta/delivery_manifest.json，验收依据 TC-602）。

    参数
    ----
    entries:       每件交付记录（item_id / status / models / qc_status / meta）。
    manifest_path: 输出路径（90_meta/delivery_manifest.json）。

    返回
    ----
    清单文件绝对路径。
    """
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    payload = {"deliveries": entries, "n_items": len(entries),
               "n_published": sum(1 for e in entries if e.get("status") == "published")}
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    log.info("交付清单：%s（%d 件，%d 已发布）", manifest_path,
             payload["n_items"], payload["n_published"])
    return os.path.abspath(manifest_path)


__all__ = [
    "export_delivery",
    "deliver_texture_atlas",
    "build_meta",
    "write_meta",
    "copy_qc_report",
    "build_delivery_manifest",
]
