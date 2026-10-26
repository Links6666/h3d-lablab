"""h3d.health._project : 项目运行前自检（这个项目现在能不能跑、会不会撞车）。

侧重点（与 ``boot`` 的分工）
--------------------------
``boot`` 看机器与程序本身；``project`` 只看**这一个数据根的当前状态**：

* 输入到底有没有（``00_raw`` 扫描件 / ``00_photo`` 照片至少得有一个）；
* 这个根是不是正被另一个进程锁着（活锁 → 直接阻断，别等它互删产物）；
* ``run_manifest.json`` 里写着 ``done`` 的阶段，产物是不是还在
  （否则续跑会"跳过"，下游却拿不到东西 —— M5「参考点云为空」的根因）；
* 稠密中间产物是否残留（位姿变了还留着旧深度图 → 点云错乱但退出码 0）；
* 照片与 ``capture_meta`` 的像素尺寸是否同源（不同源会导致贴图取错区域，
  而且**不报错**）；
* 同名照片是否跨件复用（同根多件同名 → 每件都重解一遍，耗时翻 N 倍）。

结论里 ``blocker`` 会拦住启动；``warning`` 只提示。判定原则是"能不能继续
跑出**正确**结果"，而不是"能不能在不完美的条件下跑完"——后者只会把错误
推迟到几小时后才暴露。

性能
----
全部走一层 ``scandir`` + 抽样（每件最多看 40 个文件名、每阶段最多 stat 3 个
产物），不做全目录遍历，实测约 420 ms。
"""

from __future__ import annotations

import os
from typing import Any, Optional

from h3d.health import CheckResult, Context, check
from h3d.health import _probe as P

#: 自检只看这些标准子目录名（与管线产物布局一致）
_DIR_RAW = "00_raw"
_DIR_PHOTO = "00_photo"
_DIR_META = "90_meta"
_DIR_RUNS = "99_runs"
_DIR_DENSE = "10_dense"


def data_root(cfg: Any, root: Optional[str] = None) -> str:
    """数据根 = ``00_raw`` 的父目录（**唯一实现**）。

    选数据根而非 ``raw_dir``：多项目共用同一批 ``00_photo`` / ``99_runs``
    时，锁必须覆盖整个根才有意义（实机那次两个进程在同一根下起了两个项目名
    ``yyyyyyy`` / ``rrrrr``，操作的是同一批文物目录）。

    ``h3d.runner.batch_processor._lock_root`` 直接转调本函数，避免两份实现
    漂移（漂移过一次：锁目录与自检目录不一致，自检说"没锁"、真跑却被拒）。
    """
    if root:
        return os.path.abspath(root)
    raw = str(getattr(getattr(cfg, "project", None), "raw_dir", "") or "").strip()
    if raw:
        parent = os.path.dirname(os.path.abspath(raw.rstrip("\\/")))
        if parent and os.path.isdir(parent):
            return parent
    man = str(getattr(getattr(cfg, "runner", None), "manifest", "") or "").strip()
    if man:
        return os.path.dirname(os.path.abspath(man)) or os.getcwd()
    return os.getcwd()


def _items(root: str) -> list[str]:
    """可处理的文物编号（优先 00_raw 子目录，其次 00_photo）。"""
    raw = P.dir_names(os.path.join(root, _DIR_RAW))
    photo = P.dir_names(os.path.join(root, _DIR_PHOTO))
    return sorted(set(raw) | set(photo))


@check("project", "root", title="数据根可写", order=10, needs_root=True)
def _check_root(ctx: Context) -> CheckResult:
    root = data_root(ctx.cfg, ctx.root)
    if not os.path.isdir(root):
        return CheckResult("root", "数据根可写", "blocker",
                           f"数据根不存在：{root}",
                           "检查 config.yaml 的 project.raw_dir 是否指向上级的 00_raw 目录")
    # 把整个盘符根当数据根是典型的配置误填：产物会散落到盘根，且锁会锁整盘
    if os.path.dirname(root) == root:
        return CheckResult("root", "数据根可写", "warning",
                           f"数据根是盘符根目录：{root}",
                           "建议把项目放在独立子目录（如 D:\\文保项目\\项目名），"
                           "否则产物与锁会散落在盘根")
    ok, why = P.write_probe(root)
    if not ok:
        return CheckResult("root", "数据根可写", "blocker",
                           f"数据根不可写：{root}（{why}）",
                           "换到可写目录，或去掉该目录的只读属性 / 换掉被占用的"
                           "OneDrive 占位目录")
    return CheckResult("root", "数据根可写", "ok", root)


@check("project", "inputs", title="输入数据", order=20, needs_root=True)
def _check_inputs(ctx: Context) -> CheckResult:
    root = data_root(ctx.cfg, ctx.root)
    raw_items = P.dir_names(os.path.join(root, _DIR_RAW))
    photo_items = P.dir_names(os.path.join(root, _DIR_PHOTO))
    # 抽样：每件最多数 20 个文件，避免大目录拖慢自检
    n_raw_files = 0
    for item in raw_items[:20]:
        n_raw_files += len(P.dir_files(os.path.join(root, _DIR_RAW, item),
                                       limit=20,
                                       exts=(".ply", ".xyz", ".pcd", ".txt", ".las")))
    n_photos = 0
    for item in photo_items[:20]:
        n_photos += len(P.dir_files(os.path.join(root, _DIR_PHOTO, item),
                                    limit=20,
                                    exts=(".jpg", ".jpeg", ".png", ".tif", ".tiff")))

    if not raw_items and not photo_items:
        return CheckResult(
            "inputs", "输入数据", "blocker",
            f"{_DIR_RAW}/ 与 {_DIR_PHOTO}/ 下都没有文物目录",
            "在 00_raw/<编号>/ 放扫描件，或在 00_photo/<编号>/ 放照片。\n"
            "目录名即文物编号，必须与产物路径一致（不能随意改名）。")
    if n_raw_files == 0 and n_photos == 0:
        return CheckResult(
            "inputs", "输入数据", "blocker",
            f"发现 {len(raw_items)} 件 / {len(photo_items)} 件目录，但里面没有可识别的"
            f"扫描件（ply/xyz/pcd）或照片（jpg/png/tif）",
            "确认扩展名与目录层级：输入必须放在 00_raw/<编号>/ 或 00_photo/<编号>/ 下")
    bits = []
    if n_raw_files:
        bits.append(f"扫描件 {len(raw_items)} 件（抽样 {n_raw_files} 个文件）")
    elif raw_items:
        # 目录建了但没放文件：不能报"扫描件 N 件"，否则用户以为自己有几何源
        bits.append(f"00_raw 有 {len(raw_items)} 个目录但无扫描件")
    if photo_items:
        bits.append(f"照片 {len(photo_items)} 件（抽样 {n_photos} 张）")
    detail = " · ".join(bits) or "未发现输入"
    if n_raw_files == 0:
        return CheckResult(
            "inputs", "输入数据", "info",
            detail + "（无扫描件 → 场景②：几何只能来自 SfM 稠密重建）",
            "纯照片模式的几何精度取决于照片覆盖度；建议同时拍全器物并补拍镂空/凹陷处")
    return CheckResult("inputs", "输入数据", "ok", detail)


@check("project", "lock", title="数据根占用", order=30, needs_root=True)
def _check_lock(ctx: Context) -> CheckResult:
    """活锁 = 阻断（不能等它把 99_runs 删了）；陈旧锁 = 自动接管。"""
    from h3d.core.lockfile import LOCK_NAME, _pid_alive, _read_holder

    root = data_root(ctx.cfg, ctx.root)
    path = os.path.join(root, LOCK_NAME)
    if not os.path.isfile(path):
        colmap = P.find_processes(("colmap",), ctx.cache)
        if colmap:
            return CheckResult(
                "lock", "数据根占用", "warning",
                "本根无锁，但有 COLMAP 在跑（PID %s）"
                % ", ".join(str(p["pid"]) for p in colmap),
                "可能是别的项目在用 GPU。同时跑两个稠密任务会让单张深度图"
                "从 45 s 退化到 145 s，请先确认。")
        return CheckResult("lock", "数据根占用", "ok", "未被占用")

    holder = _read_holder(path)
    pid = int(holder.get("pid") or 0)
    if pid and _pid_alive(pid):
        started = holder.get("started_at", "?")
        argv = holder.get("argv") or []
        return CheckResult(
            "lock", "数据根占用", "blocker",
            f"该数据根正被 PID {pid} 占用（启动于 {started}）"
            + ("；命令行：" + " ".join(str(a) for a in argv) if argv else ""),
            "同一数据根同时跑两个批处理会互删中间产物、争抢 GPU"
            "（实机已损失 3 小时）。\n"
            "等它结束；或确认它已失效后：任务管理器结束该 PID，或删除\n"
            f"  {path}")
    # 陈旧锁：acquire_run_lock 会自动接管，这里只在 --fix 时顺手清掉
    fix = ""
    if ctx.fix:
        try:
            os.remove(path)
            fix = f"已清理陈旧锁（原持有 PID {pid or '?'} 已退出）"
        except OSError as exc:
            return CheckResult("lock", "数据根占用", "warning",
                               f"发现陈旧锁但清理失败：{exc}",
                               f"手动删除 {path}")
    return CheckResult("lock", "数据根占用", "ok",
                       f"发现陈旧锁（PID {pid or '?'} 已退出），运行时自动接管",
                       fix=fix)


@check("project", "manifest", title="断点续跑状态", order=40, needs_cfg=True)
def _check_manifest(ctx: Context) -> CheckResult:
    """``done`` 但产物不在 —— 续跑会"跳过"，下游却拿不到东西。

    这是本项目最贵的一类坑：M5 报「参考点云为空」，根因是续跑只把阶段标成
    跳过、没恢复 outputs。现已用 ``restore_stage`` 修好；但如果产物被手工删掉，
    恢复出来的是**不存在的路径**，同样会炸。自检就是要在开跑前把它戳破。
    """
    root = data_root(ctx.cfg, ctx.root)
    man_path = str(getattr(getattr(ctx.cfg, "runner", None), "manifest", "") or "")
    if not man_path:
        man_path = os.path.join(root, "output", "run_manifest.json")
    if not os.path.isfile(man_path):
        return CheckResult("manifest", "断点续跑状态", "ok", "首次运行（无 manifest）")
    data = P.read_json(man_path)
    if data is None:
        return CheckResult("manifest", "断点续跑状态", "warning",
                           f"manifest 无法解析：{man_path}",
                           "删除该文件可从头跑（会重跑已完成阶段，含 S1 稠密）")
    stages = data.get("stages") or {}
    if not isinstance(stages, dict):
        return CheckResult("manifest", "断点续跑状态", "warning",
                           "manifest 结构异常（stages 字段不是字典）",
                           "删除该文件后重跑")
    missing: list[str] = []
    running: list[str] = []
    n_done = n_fail = 0
    for key, entry in stages.items():
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "")
        if status == "running":
            running.append(str(key))
        elif status == "done":
            n_done += 1
            # 每阶段最多抽查 3 个产物，避免大项目自检变慢
            for out in list(entry.get("outputs") or [])[:3]:
                if isinstance(out, str) and not os.path.exists(out):
                    missing.append(f"{key} → {os.path.basename(out)}")
                    break
        elif status == "fail":
            n_fail += 1

    if missing:
        preview = "；".join(missing[:4]) + ("…" if len(missing) > 4 else "")
        return CheckResult(
            "manifest", "断点续跑状态", "warning",
            f"{len(missing)} 个「已完成」阶段的产物已不在磁盘上（{preview}）",
            "续跑会把这些阶段当成「跳过」，下游却拿不到产物（曾导致 M5 "
            "报「参考点云为空」）。\n"
            "处理：删掉这些阶段在 manifest 里的记录（或整份 manifest）后重跑；"
            "若只想重跑 M1 之后，可保留 S1 记录、删其余。")
    if running:
        return CheckResult(
            "manifest", "断点续跑状态", "info",
            f"{len(running)} 个阶段上次停在「运行中」（{', '.join(running[:3])}）",
            "上次异常退出。重跑会从这些阶段继续，不必从头开始。")
    detail = f"已完成 {n_done} 个阶段记录"
    if n_fail:
        detail += f"，历史失败 {n_fail} 个"
    return CheckResult("manifest", "断点续跑状态", "ok", detail)


@check("project", "dense_residual", title="稠密中间产物", order=50, needs_cfg=True)
def _check_dense_residual(ctx: Context) -> CheckResult:
    """``patch_match_stereo`` 会跳过已存在的 ``photometric.bin``。

    位姿/内参变了而旧深度图还在 → 点云错乱，但**退出码 0、无任何警告**
    （实机踩过）。这里在开跑前提醒"要么清、要么确认位姿没变"。
    """
    sfm = getattr(ctx.cfg, "sfm_solve", None)
    if sfm is None or not getattr(sfm, "enabled", False):
        return CheckResult("dense_residual", "稠密中间产物", "ok",
                           "SfM 未启用（跳过）")
    root = data_root(ctx.cfg, ctx.root)
    base = os.path.join(root, _DIR_RUNS, "sfm_tmp")
    if not os.path.isdir(base):
        return CheckResult("dense_residual", "稠密中间产物", "ok", "无稠密临时目录")
    hit: list[str] = []
    for item in P.dir_names(base)[:20]:
        stereo = os.path.join(base, item, "dense", "stereo")
        if os.path.isdir(stereo) and P.dir_files(stereo, limit=1):
            hit.append(item)
    if not hit:
        return CheckResult("dense_residual", "稠密中间产物", "ok", "无残留深度图")
    fix = ""
    msg = f"{len(hit)} 件存在上次的深度图（{', '.join(hit[:3])}）"
    if ctx.fix:
        return CheckResult(
            "dense_residual", "稠密中间产物", "info", msg,
            "确认位姿未变可直接续算；位姿变过则必须先清空 deep 目录",
            fix="未自动删除（删除会丢失可续算的稠密结果）")
    return CheckResult(
        "dense_residual", "稠密中间产物", "info", msg,
        "patch_match_stereo 会跳过已存在的 photometric.bin：若位姿/内参变过，"
        "必须删掉\n"
        f"  {os.path.join(base, '<编号>', 'dense')}\n"
        "否则点云会错乱且不报错（实机踩过）。位姿没变时可放心续算。")


@check("project", "photo_pixels", title="照片与位姿尺寸", order=60, needs_cfg=True)
def _check_photo_pixels(ctx: Context) -> CheckResult:
    """照片实际像素 vs ``capture_meta`` 记录的内参尺寸。

    内参属于 ``photos_small``（2400 长边），M4 读的是 ``00_photo`` 原图
    （4496）。若 M4 直接按内参索引原图，颜色会**全部取到左上角**且不报错。
    M4 已按 ``pixel_scale`` 换算，本检查只做"是否换了照片/比例变了"的哨兵。
    """
    root = data_root(ctx.cfg, ctx.root)
    items = P.dir_names(os.path.join(root, _DIR_PHOTO), limit=5)
    if not items:
        return CheckResult("photo_pixels", "照片与位姿尺寸", "ok",
                           "无照片（跳过）")
    item = items[0]
    photos = P.dir_files(os.path.join(root, _DIR_PHOTO, item), limit=1,
                         exts=(".jpg", ".jpeg", ".png", ".tif", ".tiff"))
    if not photos:
        return CheckResult("photo_pixels", "照片与位姿尺寸", "warning",
                           f"00_photo/{item}/ 下没有可识别照片",
                           "确认扩展名（jpg/jpeg/png/tif）与目录层级")
    meta_path = os.path.join(root, _DIR_META, f"{item}_capture_meta.json")
    meta = P.read_json(meta_path) if os.path.isfile(meta_path) else None
    if meta is None:
        return CheckResult(
            "photo_pixels", "照片与位姿尺寸", "warning",
            f"缺少位姿元数据：90_meta/{item}_capture_meta.json",
            "没有位姿就无法做照片投影纹理（M4 会降级为顶点色或几何模型）。\n"
            "先让 S1（COLMAP）跑完并生成 capture_meta，再跑 M4。")
    px_w = px_h = None
    try:
        from PIL import Image
        with Image.open(os.path.join(root, _DIR_PHOTO, item, photos[0])) as im:
            px_w, px_h = im.size
    except Exception:  # noqa: BLE001 - 读图失败只影响这一项
        return CheckResult("photo_pixels", "照片与位姿尺寸", "info",
                           f"照片尺寸读取失败（{photos[0]}），跳过比对")
    ref_w, ref_h = _meta_size(meta)
    if not ref_w or not ref_h:
        return CheckResult("photo_pixels", "照片与位姿尺寸", "info",
                           f"位姿元数据未记录图像尺寸；照片 {px_w}×{px_h}",
                           "M4 会按内参自行换算，不影响出图；建议在 capture_meta "
                           "里补记 image_size")
    ratio = px_w / float(ref_w)
    if abs(ratio - 1.0) < 0.02:
        return CheckResult("photo_pixels", "照片与位姿尺寸", "ok",
                           f"照片与位姿同源（{px_w}×{px_h}）")
    return CheckResult(
        "photo_pixels", "照片与位姿尺寸", "info",
        f"照片 {px_w}×{px_h} 与位姿尺寸 {ref_w}×{ref_h} 不同源"
        f"（比例 {ratio:.3f}×）",
        "M4 已按 pixel_scale 换算，正常出图无影响；\n"
        "但若照片被替换过（换相机/换批次），必须重跑 S1 重新解算位姿，"
        "否则投影会整体偏移。")


def _meta_size(meta: dict) -> tuple[Optional[int], Optional[int]]:
    """从 capture_meta 里取参考图像尺寸（兼容多种记录方式）。

    实际结构（relic_001 真实数据）::

        {"n_cameras":1, "cameras": {"1": {"w":2399,"h":1601,"fx":...,"cx":...}},
         "images":[{"name":"DSC_0008.JPG","R":[...],"t":[...],"cam_id":1}]}

    注意 ``cameras`` 记录的是 **photos_small** 的尺寸（2400 长边），而
    ``00_photo`` 放的是原图（4496）—— 两者不同源是正常的，比例由 M4 的
    ``pixel_scale`` 吸收。这里只负责把它算出来给用户看。
    """
    cams = meta.get("cameras")
    if isinstance(cams, dict) and cams:
        first = next(iter(cams.values()))
        if isinstance(first, dict):
            w = first.get("w") or first.get("width")
            h = first.get("h") or first.get("height")
            if w and h:
                try:
                    return int(w), int(h)
                except (TypeError, ValueError):
                    pass
    elif isinstance(cams, list) and cams and isinstance(cams[0], dict):
        w = cams[0].get("w") or cams[0].get("width")
        h = cams[0].get("h") or cams[0].get("height")
        if w and h:
            try:
                return int(w), int(h)
            except (TypeError, ValueError):
                pass

    for key in ("image_size", "size", "photos_small_size"):
        val = meta.get(key)
        if isinstance(val, (list, tuple)) and len(val) >= 2:
            try:
                return int(val[0]), int(val[1])
            except (TypeError, ValueError):
                pass
    images = meta.get("images")
    if isinstance(images, list) and images:
        first = images[0] if isinstance(images[0], dict) else {}
        for key in ("width", "w"):
            if first.get(key):
                return int(first[key]), int(first.get("height") or first.get("h") or 0)
    return None, None


@check("project", "photo_duplicates", title="跨件同名照片", order=70, needs_cfg=True)
def _check_photo_duplicates(ctx: Context) -> CheckResult:
    """同一根下多件共用同名照片 = 每件都重解一遍（耗时翻 N 倍）。"""
    root = data_root(ctx.cfg, ctx.root)
    items = P.dir_names(os.path.join(root, _DIR_PHOTO), limit=30)
    if len(items) < 2:
        return CheckResult("photo_duplicates", "跨件同名照片", "ok",
                           "单件或未放照片（跳过）")
    seen: dict[str, str] = {}
    clashes: dict[str, list[str]] = {}
    for item in items:
        files = P.dir_files(os.path.join(root, _DIR_PHOTO, item), limit=40)
        for name in files:
            low = name.lower()
            if low in seen and seen[low] != item:
                clashes.setdefault(low, [seen[low]]).append(item)
            else:
                seen[low] = item
    if not clashes:
        return CheckResult("photo_duplicates", "跨件同名照片", "ok",
                           f"{len(items)} 件照片无重名")
    sample = list(clashes)[:2]
    return CheckResult(
        "photo_duplicates", "跨件同名照片", "warning",
        f"{len(clashes)} 个文件名在多个文物目录中重复（如 "
        f"{'、'.join(sample)}）",
        "同根多件共用同名照片时，每件都会独立重新解算一遍 SfM（实测耗时翻 N 倍）。\n"
        "若照片**内容也完全相同**（1.1.5 起按内容哈希判断），后续各件会自动复用"
        "第一件的 SfM 结果（S1 30 min → 约 2 s），日志与 manifest 的 s1_reuse "
        "字段会写明复用来源。\n"
        "纹理阶段同样会自动复用（1.1.6 起）：几何+位姿+照片+参数一致时直接取"
        "第一件的 UV 图集与纹理图集（M4 83~85 s → 4~6 s/件），日志与 manifest 的 "
        "atlas_reuse 字段写明来源。\n"
        "若只是重名、内容不同（不同器物恰好同名），请把照片改到独立目录并换成"
        "唯一文件名，或分批运行——否则每件都要完整解算一遍。")


@check("project", "atlas_cache", title="纹理图集缓存", order=75, needs_cfg=True)
def _check_atlas_cache(ctx: Context) -> CheckResult:
    """图集缓存体积提示：1.1.6 起 UV/纹理图集缓存在 99_runs/atlas_cache。"""
    if not getattr(ctx.cfg.photo_texture, "reuse_atlas", True):
        return CheckResult("atlas_cache", "纹理图集缓存", "info",
                           "已关闭（reuse_atlas=false）：每件都会重算 UV 与采样")
    root = data_root(ctx.cfg, ctx.root)
    path = os.path.join(root, "99_runs", "atlas_cache")
    if not os.path.isdir(path):
        return CheckResult("atlas_cache", "纹理图集缓存", "ok",
                           "首次运行后自动建立（同几何同照片的件共享图集）")
    total = 0
    n_tex = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
            if name.endswith(".png") and ".mask." not in name:
                n_tex += 1
    mb = total / (1024 ** 2)
    if mb > 2048:
        return CheckResult("atlas_cache", "纹理图集缓存", "warning",
                           f"{n_tex} 套图集占 {mb:.0f} MB",
                           "缓存只影响速度、可随时删除：删掉 99_runs/atlas_cache "
                           "后下次运行会重算并重建。\n"
                           "默认按 mtime 只保留最近 24 套（单套 1024² 约 3 MB、"
                           "2048² 约 10 MB）。")
    return CheckResult("atlas_cache", "纹理图集缓存", "ok",
                       f"{n_tex} 套图集 / {mb:.1f} MB（可随时删除，仅影响速度）")


@check("project", "disk", title="项目磁盘空间", order=80, needs_root=True)
def _check_disk(ctx: Context) -> CheckResult:
    root = data_root(ctx.cfg, ctx.root)
    free = P.disk_free(root)
    if free is None:
        return CheckResult("disk", "项目磁盘空间", "info", "无法读取磁盘空间")
    gb = free / (1024 ** 3)
    if gb < 0.5:
        return CheckResult("disk", "项目磁盘空间", "blocker",
                           f"项目所在盘仅剩 {gb:.2f} GB",
                           "稠密重建中间产物可达数 GB/件，写入失败会让阶段中途崩。\n"
                           "先清 99_runs/sfm_tmp 下已完成项目的残留。")
    if gb < 2.0:
        return CheckResult("disk", "项目磁盘空间", "warning",
                           f"项目所在盘仅剩 {gb:.2f} GB",
                           "建议至少保留 5 GB 再开跑纯照片重建")
    return CheckResult("disk", "项目磁盘空间", "ok", f"可用 {gb:.1f} GB")


__all__ = ["data_root"]
