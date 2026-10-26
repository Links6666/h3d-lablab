"""h3d.sfm_solve : S1 可选 stage —— COLMAP 子进程解算相机位姿（照片导入方案）。

设计依据：
    《照片导入方案》—— 新增可选 stage sfm_solve（默认关闭）：用户仅放入
    00_photo/<item>/*.jpg，管线内调用 COLMAP（feature→match→mapper）自动
    解算相机内参/外方位，输出 capture_meta.json 供给 photo_texture 纹理烘焙；
    本机未装 COLMAP 时抛业务错误码 + 失败隔离；pytest 全部 mock（CI 不跑
    真实 SfM）；运行结束自动清理中间产物。
    《路线 R2》—— sfm_solve 是场景①「照片自动接入」闭环的关键 stage。

对外：
    run_sfm_solve(task, cfg) —— stage 函数（S1，注册于 M1 之前）；
    parse_cameras / parse_images —— sparse txt -> 位姿结构（供 photo_texture 复用）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import time
from typing import Optional

import numpy as np

from h3d.core import RecoverableError, get_logger, run_external
from h3d.core.media import is_image_file, list_images

log = get_logger("sfm_solve")

# 场景①自动化闭环的位姿元数据文件（S1 产物，P1 消费）
CAPTURE_META = "capture_meta.json"


class SfmError(Exception):
    """S1 内部错误（COLMAP 调用失败等），由编排层包装为业务错误码。"""


def _photo_dir_for(task, cfg) -> str:
    """00_photo/<item> 目录（照片根目录可配置；优先 task.root，回退上级）。

    平铺兜底（2026-10-17）：用户可能把照片**直接放在 00_photo/ 根下**而不建
    ``<item>`` 子目录。此时若 ``00_photo/<item>/`` 不存在而根目录内有图像，
    直接返回根目录——否则 S1 报"照片目录为空"，与导入阶段"已识别 N 张照片"
    互相矛盾，实机极难排查。
    """
    if cfg.sfm_solve.photo_dir:
        base = cfg.sfm_solve.photo_dir
    else:
        # 优先 task.root/00_photo（数据根）；兼容旧布局（raw 上级 + 00_photo）
        cand = os.path.join(task.root, "00_photo")
        if not os.path.isdir(cand):
            cand = os.path.join(
                os.path.dirname(os.path.abspath(task.root)), "00_photo")
        base = cand
    item_dir = os.path.join(base, task.item_id)
    if not os.path.isdir(item_dir) and list_images(base):
        return base
    return item_dir


def _find_colmap(cfg) -> Optional[str]:
    """定位 COLMAP 可执行文件（shutil.which + 配置路径）。"""
    path = cfg.sfm_solve.colmap_path or "colmap"
    found = shutil.which(path)
    if found:
        return found
    for cand in (path, "colmap", "colmap.exe"):
        if os.path.exists(cand):
            return cand
    return None


def _model_num_images(model_dir: str) -> int:
    """读取 COLMAP 二进制模型 ``images.bin`` 的图像数（不依赖 pycolmap）。

    COLMAP 二进制格式：文件头前 8 字节为小端 ``uint64`` 图像条数。
    """
    path = os.path.join(model_dir, "images.bin")
    if not os.path.isfile(path):
        return -1
    try:
        with open(path, "rb") as fh:
            raw = fh.read(8)
    except OSError:
        return -1
    if len(raw) < 8:
        return -1
    return int(struct.unpack("<Q", raw)[0])


def _pick_largest_model(sparse: str) -> str:
    """在 mapper 产出的多个子重建中选出**注册图像最多**的一个。

    重大缺陷修复（2026-09-15，实机定位）
    ------------------------------------
    COLMAP ``mapper`` 在匹配图不连通时会输出**多个子重建**
    （``sparse/0``、``sparse/1``…），并且**不保证 0 号是最大的那个**。
    原实现硬编码只读 ``sparse/0``，实测示例数据下::

        sparse/0 -> 2 张注册  / 1286 个 3D 点   （碎片）
        sparse/1 -> 16 张注册 / 6819 个 3D 点   （完整重建）

    于是 S1 报告「2/16 张注册」，连锁导致 M4 照片投影覆盖不足、M5 纹理
    质量指标偏低。此前观察到的「同一输入注册数在 2~16 之间随机波动」，
    本质就是 0 号槽位恰好被哪个子模型占用不同，与随机种子无关。

    返回选中的子模型目录；无可用模型返回空串。
    """
    cands = []
    for name in sorted(os.listdir(sparse)):
        d = os.path.join(sparse, name)
        if not os.path.isdir(d) or name.endswith("_txt"):
            continue
        n = _model_num_images(d)
        if n >= 0:
            cands.append((n, name, d))
    if not cands:
        return ""
    cands.sort(key=lambda x: (-x[0], x[1]))
    if len(cands) > 1:
        log.warning(
            "S1 COLMAP 产出 %d 个子重建（图像数 %s）——匹配图不连通。"
            "取最大者 sparse/%s；若主模型仍有缺失，建议增加相邻视角重叠"
            "或改用 sequential matcher。",
            len(cands), [c[0] for c in cands], cands[0][1])
    return cands[0][2]


def _normalize_model_slot(sparse: str, best_dir: str, slot: str = "0") -> str:
    """把选中的最大子模型归一化到 ``sparse/<slot>``，返回二进制模型目录。

    下游（``run_dense_stereo``、增量 ``image_registrator``）按 ``<sparse>/0``
    约定取二进制模型，因此必须在返回前完成归一，避免"择优了却仍读碎片"。
    被替换的原模型改名保留（便于事后核查），不删除。
    """
    best_name = os.path.basename(best_dir)
    target = os.path.join(sparse, slot)
    if best_name == slot:
        return target
    if os.path.isdir(target):
        spare = os.path.join(sparse, f"{best_name}_{slot}_replaced")
        if os.path.exists(spare):
            shutil.rmtree(spare)
        os.replace(target, spare)
        log.info("S1 原子重建（%s 张）改名保留为 sparse/%s_%s_replaced",
                 _model_num_images(spare), best_name, slot)
    os.replace(best_dir, target)
    log.info("S1 子重建归一：sparse/%s -> sparse/%s", best_name, slot)
    return target


def _mapper_threads(deterministic: bool = True, threads: int = 0) -> str:
    """mapper 线程数：确定性模式单线程（BA 浮点归约顺序固定），否则 4。

    实测同一 database.db 两次运行，注册图像数完全稳定，但位姿元素最大
    绝对差 6.2e-15（相对 1.7e-15）——属双精度舍入噪声。若需比特级复现
    （如对比实验要求逐位一致），须固定为单线程。
    """
    if threads > 0:
        return str(threads)
    return "1" if deterministic else "4"


def _matcher_cmd(matcher: str) -> str:
    """配置里的 matcher 名 -> COLMAP 子命令名。

    缺陷修复（2026-09-15）：``run_incremental_sparse`` 原先直接把配置值
    ``matcher``（"exhaustive" / "sequential"）当作子命令传给 colmap，而
    COLMAP 的子命令实为 ``exhaustive_matcher`` / ``sequential_matcher``。
    后果：P3 增量注册**每次都在第一步匹配失败**并静默回退全量重建
    （SfmError 被 except 吞掉，只留一行 warning），增量能力实际从未生效。
    """
    return {"exhaustive": "exhaustive_matcher",
            "sequential": "sequential_matcher"}.get(matcher, matcher)


def _repro_args(prefix: str, deterministic: bool = True,
                threads: int = 0) -> list:
    """COLMAP 可复现性参数（2026-09-15 实机排查结论）。

    **不确定性的真正来源是线程调度，而非随机种子**：
      ``--default_random_seed`` 的默认值本就是 0，单独设置无任何效果；而
      特征提取与匹配在多线程下的浮点归约顺序不确定，会导致同一组照片两次
      运行产出内容不同的 ``database.db``。

    实测（同一组 16 张示例照片，每配置跑两次，比对 DB 逻辑签名）：

    ==================== ============ ============== =============
    配置                  关键点哈希    匹配内点哈希    文件 md5
    ==================== ============ ============== =============
    提取 t=8             不一致        —              —
    提取 t=1 / 匹配 t=8  一致          不一致         不一致
    提取 t=1 / 匹配 t=1  一致          一致           **一致**
    ==================== ============ ============== =============

    因此 ``deterministic=True`` 时对**两个阶段都**强制单线程，此时 mapper
    注册图像数完全稳定，论文实验可复现。代价是大图集耗时上升。

    参数
    ----
    prefix:        COLMAP 选项前缀（"FeatureExtraction" / "FeatureMatching"）。
    deterministic: True -> 单线程（可复现）；False -> 自动多线程（快）。
    threads:       >0 时显式覆盖；0 表示按 deterministic 推导。
    """
    n = threads if threads > 0 else (1 if deterministic else -1)
    return [f"--{prefix}.num_threads", str(n), "--default_random_seed", "0"]


def _reset_sparse_state(db: str, sparse: str, dense: str = "") -> None:
    """非增量模式：清除旧 database、旧稀疏模型与旧 dense 工作区。

    背景（2026-10-17 实机 0xC0000409）
    ---------------------------------
    原实现直接复用 ``<workspace>/database.db``：用户点「重试」或断点续跑时，
    COLMAP 打开的是**上一次运行的残留库**。实测该库呈半写入状态
    （cameras=3 / images=30，前 4 张绑 camera 1、其余绑 camera 3），
    再次 feature_extractor 时触发
    ``SQLite error: constraint failed`` → COLMAP abort（exit=3221226505）。
    同一批照片在空库上重跑则稳定通过 30/30，证明与照片、中文路径无关。

    即使侥幸不崩，残留库也会让 ``--ImageReader.single_camera 1`` 失效：
    新会话各自创建相机（camera 1/2/3），mapper 按相机分组 → 多个子重建
    → 注册率虚低且结果不可复现。

    增量注册是独立流程（:func:`run_incremental_sparse`），它需要保留旧库，
    因此本函数只在**全量重建**路径调用。

    dense 残留（2026-10-17 实机中断后追加）
    --------------------------------------
    ``patch_match_stereo`` 会**跳过已存在的 photometric.bin**，只在
    ``stereo/depth_maps/`` 里补齐缺的那几张。若 sparse 已按新库重建、
    而 ``dense/stereo`` 仍留着上一次位姿算出的深度图，融合时就会把
    两套位姿的深度图混在一起 —— 产物是错的，但**全程不报错**。
    实机中断（30 张里做完 30 photometric / 22 geometric）后重跑正是此态，
    属典型的静默错误，因此全量重建必须连 dense 一起清。

    参数
    ----
    db:      ``database.db`` 绝对路径。
    sparse:  稀疏模型输出目录（``<workspace>/sparse``）。
    dense:   密集工作区（``<workspace>/dense``）；为空则不动。
    """
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = db + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
                log.info("S1 清理旧数据库：%s", os.path.basename(p))
            except OSError as exc:  # noqa: BLE001 - 清理失败交由 COLMAP 报错
                log.warning("S1 旧数据库删除失败（%s）：%s", p, exc)
    _clear_tree(sparse, "S1 清理旧稀疏模型")
    if dense:
        _clear_tree(dense, "S1 清理旧 dense 工作区")


def _clear_tree(path: str, what: str) -> None:
    """清空目录内容（保留目录本身），失败只告警不中断。"""
    if not os.path.isdir(path):
        return
    for name in os.listdir(path):
        p = os.path.join(path, name)
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
        except OSError:  # noqa: BLE001
            pass
    log.info("%s：%s", what, path)


_PROGRESS_HINTS = ("Processing view", "Processing file", "Processing block",
                   "Undistorting image", "Fusing image", "Registering image",
                   "Global bundle", "Bundle adjustment", "Matching block")

#: COLMAP 原始行 -> 中文可读进度（保留关键文件名，其余前缀丢弃）
_PROGRESS_CLEAN = (
    (re.compile(r"Processing view (\d+)\s*/\s*(\d+)\s*for\s*(\S+)"),
     r"深度图 \1/\2：\3"),
    (re.compile(r"Undistorting image \[(\d+)/(\d+)\]"), r"去畸变 \1/\2"),
    (re.compile(r"Fusing image \[(\d+)/(\d+)\]"), r"融合 \1/\2"),
    (re.compile(r"Processing block \[(\d+)/(\d+),[^\]]*\]"), r"匹配块 \1/\2"),
    (re.compile(r"Registering image #(\d+)"), r"注册图像 #\1"),
    (re.compile(r"Global bundle adjustment"), "全局光束法平差"),
)


#: 逐视图进度（patch_match_stereo），可选带文件名
_VIEW_PROG_RE = re.compile(
    r"Processing view (\d+)\s*/\s*(\d+)(?:\s*for\s*(\S+))?")


def _clean_progress(line: str) -> str:
    """把 COLMAP 的日志行压成一句人话。

    原始行长这样（GUI 里没法看）::

        I20261017 15:29:51.601188 1252 patch_match.cc:419] === Processing
        view 1 / 30 for DSC_0008.JPG ===

    清洗后::

        深度图 1/30：DSC_0008.JPG
    """
    for pat, rep in _PROGRESS_CLEAN:
        m = pat.search(line)
        if m:
            return pat.sub(rep, m.group(0))
    return line.strip()[:120]


class _ProgressLogger:
    """把 COLMAP 的逐视图进度行转发进日志（GUI 实时刷新用）。

    实机问题（2026-10-17）：``patch_match_stereo`` 处理 30 张 2400px 照片要
    50 分钟，期间子进程输出被 ``subprocess.run`` 全量缓冲，GUI 只显示
    "S1 dense[2/3]"，用户看到进度条 50 分钟不动 → 判断为"生成页面卡住"。
    COLMAP 自己会打印 ``=== Processing view 12 / 30 for DSC_0019.JPG ===``，
    转发后界面即可显示真实进度（含正在处理的文件名）。

    只挑进度类行、限制长度、连续重复去噪，避免刷屏（32 张图的
    feature_extractor 会产生上千行普通日志）。
    """

    def __init__(self, label: str = "S1") -> None:
        self.label = label
        self._last = ""
        #: patch_match_stereo 会跑**两遍**：第一遍 photometric（写
        #: *.photometric.bin），第二遍做几何一致性校验（写 *.geometric.bin）。
        #: 两遍的日志都是 "Processing view 1/30 ... 30/30"，若不区分，界面上
        #: 会出现「深度图 30/30 → 1/30」的进度倒退（实机 2026-10-17）。
        self._pass = 1
        self._max_view = 0

    def __call__(self, line: str) -> None:
        s = line.strip()
        if not s or len(s) > 400:
            return
        if not any(h in s for h in _PROGRESS_HINTS):
            return
        m = _VIEW_PROG_RE.search(s)
        if m:
            cur, total = int(m.group(1)), int(m.group(2))
            if cur == 1 and self._max_view >= total:
                self._pass += 1
            self._max_view = max(self._max_view, cur)
            name = m.group(3) if m.lastindex and m.lastindex >= 3 else ""
            tag = "深度图" if self._pass <= 1 else "几何校验"
            text = f"{tag} {cur}/{total}" + (f"：{name}" if name else "")
        else:
            text = _clean_progress(s)
        if text == self._last:
            return
        self._last = text
        log.info("%s %s", self.label, text)


def _dump_step_log(workspace: str, step_index: int, cmd: list[str],
                   proc: subprocess.CompletedProcess) -> str:
    """把某一步 COLMAP 的完整输出落盘，返回日志路径。

    原实现只把 stderr 末尾 600 字符塞进失败信息，用户既看不到完整原因，
    也无法事后排查（``failures.csv`` 里是一段被截断的乱序片段）。
    """
    logdir = os.path.join(workspace, "logs")
    os.makedirs(logdir, exist_ok=True)
    path = os.path.join(logdir, f"colmap_step{step_index}.log")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("COMMAND:\n" + " ".join(cmd) + "\n")
            fh.write(f"\nEXIT CODE: {proc.returncode}\n")
            fh.write("\n===== STDOUT =====\n" + (proc.stdout or ""))
            fh.write("\n===== STDERR =====\n" + (proc.stderr or ""))
    except OSError as exc:  # noqa: BLE001 - 日志写失败不得掩盖原始错误
        log.warning("S1 COLMAP 日志落盘失败：%s", exc)
        return ""
    return path


def _error_tail(text: str, max_chars: int = 900) -> str:
    """从 COLMAP 输出中提取可读的错误尾部（去掉空行与重复时间戳噪声）。"""
    lines = [ln.rstrip() for ln in (text or "").splitlines() if ln.strip()]
    tail = "\n".join(lines[-14:])
    return tail[-max_chars:]


def run_colmap_sparse(photo_dir: str, workspace: str, colmap: str,
                      matcher: str = "exhaustive",
                      max_features: int = 8192,
                      peak_threshold: float = 0.004,
                      deterministic: bool = True,
                      threads: int = 0) -> str:
    """子进程执行 COLMAP sparse：feature → match → mapper。

    返回 sparse 文本模型目录（<workspace>/sparse/0_txt）；失败抛 SfmError。
    ``deterministic=True``（默认）时特征提取与匹配强制单线程，保证同一
    输入产出比特级一致的 database.db（详见 :func:`_repro_args`）。

    每次调用都会**清空旧 database.db 与 sparse 目录**（见
    :func:`_reset_sparse_state`）——全量重建不可建立在残留状态之上。
    """
    os.makedirs(workspace, exist_ok=True)
    db = os.path.join(workspace, "database.db")
    sparse = os.path.join(workspace, "sparse")
    os.makedirs(sparse, exist_ok=True)
    _reset_sparse_state(db, sparse, os.path.join(workspace, "dense"))

    matcher_cmd = _matcher_cmd(matcher)
    steps = [
        [colmap, "feature_extractor",
         "--database_path", db, "--image_path", photo_dir,
         "--ImageReader.single_camera", "1",
         "--FeatureExtraction.use_gpu", "0",
         "--SiftExtraction.max_num_features", str(max_features),
         "--SiftExtraction.peak_threshold", str(peak_threshold)]
        + _repro_args("FeatureExtraction", deterministic, threads),
        [colmap, matcher_cmd,
         "--database_path", db,
         "--FeatureMatching.use_gpu", "0",
         "--FeatureMatching.max_num_matches", "8192",
         "--FeatureMatching.guided_matching", "1"]
        + _repro_args("FeatureMatching", deterministic, threads),
        [colmap, "mapper",
         "--database_path", db, "--image_path", photo_dir,
         "--output_path", sparse,
         "--Mapper.ba_use_gpu", "0",
         # mapper 的 BA 同样存在多线程浮点归约抖动：实测同一 DB 两次运行
         # 注册数完全稳定，但位姿末位有 6.2e-15（相对 1.7e-15）的差异。
         # 虽属双精度舍入噪声、工程上可忽略，但为满足论文级比特可复现，
         # 确定性模式下同样收敛为单线程。
         "--Mapper.num_threads", _mapper_threads(deterministic, threads),
         # 固定随机种子：mapper 本身在固定 DB 下与种子无关（实测 seed 0~4
         # 注册数完全相同），此处固定只为消除潜在残余差异。
         "--Mapper.random_seed", "0"],
    ]
    _step_names = ("特征提取", "特征匹配", "稀疏建图")
    for i, cmd in enumerate(steps, 1):
        log.info("S1 COLMAP[%d/3] %s: %s", i, _step_names[i - 1],
                 " ".join(cmd[:4]) + " ...")
        _t_step = time.time()
        proc = run_external(cmd, cwd=workspace, timeout=1800,
                            on_line=_ProgressLogger("S1"))
        log_path = _dump_step_log(workspace, i, list(cmd), proc)
        if proc.returncode != 0:
            err = _error_tail((proc.stderr or "") + "\n" + (proc.stdout or ""))
            hint = ""
            if "constraint failed" in err or "SQLite error" in err:
                hint = ("\n诊断：数据库约束冲突。通常是旧 database.db 残留导致；"
                        "系统已在本次运行前自动清理，若仍复现请检查是否同时"
                        "有多个 H3D/COLMAP 进程在写同一工作区。")
            elif proc.returncode == 3221226505:      # 0xC0000409
                hint = ("\n诊断：COLMAP 进程异常终止（0xC0000409）。"
                        "常见诱因为数据库半写入状态或并发写入。")
            raise SfmError(
                f"COLMAP 步骤 {i} 失败 (exit={proc.returncode})"
                + (f"；完整日志：{log_path}" if log_path else "")
                + hint + "\n" + err)
        log.info("S1 COLMAP[%d/3] %s 完成（%.1fs）", i, _step_names[i - 1],
                 time.time() - _t_step)

    best = _pick_largest_model(sparse)
    if not best:
        raise SfmError("SfM 未能生成稀疏模型（照片重叠不足或特征过少）")
    model_bin = _normalize_model_slot(sparse, best)
    model_txt = os.path.join(sparse, "0_txt")
    os.makedirs(model_txt, exist_ok=True)
    proc = run_external([colmap, "model_converter",
                         "--input_path", model_bin,
                         "--output_path", model_txt,
                         "--output_type", "TXT"],
                        cwd=workspace, timeout=300)
    if proc.returncode != 0:
        raise SfmError(f"模型转文本失败: {(proc.stderr or '')[-300:]}")
    return model_txt


def parse_cameras(path: str) -> dict:
    """cameras.txt -> {cam_id: {w, h, fx, fy, cx, cy}}。"""
    cams = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            params = [float(p) for p in parts[4:]]
            cams[int(parts[0])] = {
                "w": int(parts[2]), "h": int(parts[3]),
                "fx": params[0], "fy": params[0],
                "cx": params[1], "cy": params[2],
            }
    return cams


def parse_images(path: str) -> list:
    """images.txt -> [(name, R(3,3), t(3,), cam_id)]。

    **坐标系约定（全管线统一，2026-09-15 实机修复）**：相机前方为 **-Z**，
    ``t`` 为**相机中心**（世界坐标），投影为
    ``u = fx*Xc/Zc + cx, v = fy*Yc/Zc + cy``（``Zc < 0`` 可见）。
    与 :mod:`h3d.photo_texture` / :mod:`h3d.ctrl_points` /
    :mod:`h3d.ctrl_auto` 的投影公式一致。

    COLMAP 的 ``images.txt`` 存的是 ``qvec`` / ``tvec``（``X_cam = R_co@X + tvec``，
    前方 +Z）；因此转换公式为::

        C = -R_co^T @ tvec        # 相机中心
        R = -R_co                 # 使前方变为 -Z（等价于 X_cam = R @ (X - C)）

    实机问题（2026-09-15）：旧实现直接取 ``t = tvec``（既不是相机中心、轴向前后
    也不一致），导致 M4 照片投影 ``projected_cameras = 0``——纹理图整张空白，
    M5 纹理质量三项指标全 0。经验证（跨视图光度一致性：候选约定 118 vs 错误约定
    342/662，错误约定可用投影点为 0），上述转换是唯一正确解。
    """
    images = []
    with open(path, encoding="utf-8") as fh:
        lines = [l for l in fh if l.strip() and not l.startswith("#")]
    for i in range(0, len(lines), 2):
        parts = lines[i].split()
        if len(parts) < 10:
            continue
        qw, qx, qy, qz = map(float, parts[1:5])
        tx, ty, tz = map(float, parts[5:8])
        q = np.array([qw, qx, qy, qz])
        q = q / np.linalg.norm(q)
        qw, qx, qy, qz = q
        R_co = np.array([
            [1 - 2 * (qy**2 + qz**2), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
            [2 * (qx * qy + qz * qw), 1 - 2 * (qx**2 + qz**2), 2 * (qy * qz - qx * qw)],
            [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx**2 + qy**2)],
        ])
        tvec = np.array([tx, ty, tz])
        center = -R_co.T @ tvec           # 相机中心
        images.append({"name": parts[9], "R": -R_co,   # 前方 -Z（本管线约定）
                       "t": center, "cam_id": int(parts[8])})
    return images


def read_points3d(path: str) -> np.ndarray:
    """points3D.txt -> (N,3) 稀疏点云（供 ICP 坐标系对齐）。"""
    pts = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split()
            pts.append([float(p[1]), float(p[2]), float(p[3])])
    return np.array(pts, dtype=np.float64)


def write_capture_meta(cameras: dict, images: list, out_path: str) -> None:
    """写出 capture_meta.json（相机内参 + 每图外方位 + 相机中心）。

    ``images[i]["t"]`` 为**相机中心世界坐标**，``images[i]["R"]`` 按
    :func:`parse_images` 的约定（相机前方 -Z）；手工提供该文件的用户需遵循
    同一约定，否则照片投影会得到空纹理。
    """
    meta = {
        "n_cameras": len(cameras),
        "n_images": len(images),
        "cameras": {str(k): v for k, v in cameras.items()},
        "images": [{
            "name": im["name"],
            "R": np.asarray(im["R"]).round(6).tolist(),
            "t": np.asarray(im["t"]).round(6).tolist(),  # 相机中心世界坐标
            "cam_id": im["cam_id"],
        } for im in images],
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)


def _pose_fingerprint(sparse_model: str) -> str:
    """稀疏模型指纹（cameras/images/points3D 的尺寸 + 修改时间）。

    用途：判断 ``dense/stereo`` 下的深度图是否仍然对应**当前位姿**。
    ``patch_match_stereo`` 会跳过已存在的 ``*.photometric.bin``（这是 COLMAP
    的续算特性，也是 r4.1 里「旧深度图混融新位姿」静默错误的来源）。因此
    清理与否不能只看「目录非空」，而要看位姿是否真的变了：

    * 位姿**未变** → 保留深度图 → 超时/崩溃后重跑从断点继续（省下几十分钟）；
    * 位姿**已变** → 必须整棵 ``stereo`` 清掉，否则新旧深度图混融。

    返回空串表示模型文件缺失（调用方按「无法判定 → 保守清理」处理）。
    """
    parts: list[str] = []
    for name in ("cameras.bin", "images.bin", "points3D.bin"):
        path = os.path.join(sparse_model, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        parts.append(f"{name}:{st.st_size}:{int(st.st_mtime)}")
    return "|".join(parts)


#: patch_match_stereo 的速率常数（秒 / 兆像素 / 遍）。
#: 依据实机 2026-10-17（RTX 3050 Laptop，2400×1601 = 3.84 MP）：
#: 第一遍 87 s/张 → 22.7 s/MP；第二遍 145 s/张 → 37.8 s/MP。
#: 取 45 s/MP/遍（约 2 倍余量），保证不同显卡/降频下不会误杀。
_PATCH_MATCH_S_PER_MP_PASS = 45.0


def dense_timeout_budget(n_views: int, megapixels: float,
                         passes: int = 2) -> int:
    """估算 patch_match_stereo 的超时预算（秒）。

    原实现写死 ``timeout=3600``（60 分钟）覆盖**整条命令**，而该命令在
    ``geom_consistency=true`` 时跑两遍。实机 30 张 3.84 MP 需 6815 s，
    必然被腰斩——且已经算完的 30 张第一遍深度图随着进程被杀而作废。

    公式：``views × MP × 45 s × passes``，下限 1800 s。
    30 张 3.84 MP 双遍 → 10368 s（2.9 h）；1600 长边（1.71 MP）→ 4617 s。
    """
    views = max(int(n_views), 1)
    mp = max(float(megapixels), 0.2)
    budget = views * mp * _PATCH_MATCH_S_PER_MP_PASS * max(int(passes), 1)
    return int(max(1800, round(budget)))


def _count_depth_maps(dense_dir: str, suffix: str) -> int:
    """统计已生成的深度图数量（超时诊断用：已完成多少、还要多少）。"""
    dm = os.path.join(dense_dir, "stereo", "depth_maps")
    try:
        return sum(1 for f in os.listdir(dm) if f.endswith(suffix))
    except OSError:
        return 0


def run_dense_stereo(photo_dir: str, sparse_model: str, workspace: str,
                     colmap: str, min_pixels: int = 2,
                     max_pixels: int = 20, timeout_s: int = 0,
                     max_image_size: int = 0,
                     geom_consistency: bool = True,
                     force_clear: bool = False) -> str:
    """COLMAP 密集重建（场景②闭环）：undistort → patch_match_stereo → fusion。

    sparse_model: sparse 模型目录（S1 产物，含 sparse/0）。
    workspace:    dense 工作区（自动创建 colmap_ws/dense）。
    timeout_s:    稠密超时预算；0 = 按张数 × 像素数自动估算
                  （:func:`dense_timeout_budget`）。
    max_image_size: 稠密内部分辨率上限（0 = 跟随输入）。深度图像素数直接
                  决定耗时，与稀疏解算所需分辨率无关，故单独控制。
    geom_consistency: 是否做几何一致性校验（第二遍）。false 时只跑一遍，
                  fusion 改用 photometric 深度图。
    force_clear:  强制丢弃既有深度图（默认只在与当前位姿不匹配时才清）。
    返回 fused.ply 路径；任一步失败抛 SfmError（GPU 不可用时明确报错）。
    """
    dense_dir = os.path.join(workspace, "dense")
    stereo = os.path.join(dense_dir, "stereo")
    fp_path = os.path.join(dense_dir, ".h3d_pose_fp")

    # 深度图只对「生成它时的那套位姿」有效。旧实现无条件 rmtree(stereo)，
    # 使超时/崩溃后重跑必须从第 1 张重算（实机浪费 43 分钟）。
    # 现改为位姿指纹比对：指纹一致 → 保留并断点续算；不一致 → 清空。
    cur_fp = _pose_fingerprint(sparse_model)
    try:
        with open(fp_path, "r", encoding="utf-8") as fh:
            old_fp = fh.read().strip()
    except OSError:
        old_fp = ""
    has_stereo = os.path.isdir(stereo) and bool(os.listdir(stereo))
    if has_stereo:
        if force_clear:
            shutil.rmtree(stereo, ignore_errors=True)
            log.info("S1 强制丢弃旧深度图（force_redense）：%s", stereo)
        elif not cur_fp or old_fp != cur_fp:
            shutil.rmtree(stereo, ignore_errors=True)
            log.info("S1 丢弃旧 stereo 深度图（sparse 位姿已变）：%s", stereo)
        else:
            n_p = _count_depth_maps(dense_dir, ".photometric.bin")
            n_g = _count_depth_maps(dense_dir, ".geometric.bin")
            log.info("S1 复用既有深度图（位姿未变，断点续算）："
                     "photometric %d 张 / geometric %d 张", n_p, n_g)
    try:
        os.makedirs(dense_dir, exist_ok=True)
        with open(fp_path, "w", encoding="utf-8") as fh:
            fh.write(cur_fp)
    except OSError as exc:  # noqa: BLE001 - 指纹写失败只影响续算，不阻断
        log.warning("S1 位姿指纹落盘失败（不影响本次重建）：%s", exc)

    passes = 2 if geom_consistency else 1
    pm_cmd = [colmap, "patch_match_stereo",
              "--workspace_path", dense_dir,
              "--workspace_format", "COLMAP",
              "--PatchMatchStereo.geom_consistency",
              "true" if geom_consistency else "false",
              "--PatchMatchStereo.gpu_index", "0"]
    if max_image_size and max_image_size > 0:
        pm_cmd += ["--PatchMatchStereo.max_image_size", str(int(max_image_size))]
    steps = [
        [colmap, "image_undistorter",
         "--image_path", photo_dir,
         "--input_path", sparse_model,
         "--output_path", dense_dir,
         "--output_type", "COLMAP"],
        pm_cmd,
        [colmap, "stereo_fusion",
         "--workspace_path", dense_dir,
         "--workspace_format", "COLMAP",
         "--input_type", "geometric" if geom_consistency else "photometric",
         "--output_path", os.path.join(dense_dir, "fused.ply"),
         "--StereoFusion.min_num_pixels", str(min_pixels),
         "--StereoFusion.max_num_pixels", str(max_pixels)],
    ]
    n_views = _count_photos(photo_dir)
    mp = _photos_megapixels(photo_dir, max_image_size)
    budget = int(timeout_s) if timeout_s and timeout_s > 0 else \
        dense_timeout_budget(n_views, mp, passes=passes)
    for i, cmd in enumerate(steps, 1):
        log.info("S1 dense[%d/3]: %s", i, " ".join(cmd[:4]) + " ...")
        step_timeout = budget if i == 2 else max(600, budget // 4)
        try:
            proc = run_external(cmd, cwd=workspace, timeout=step_timeout,
                                on_line=_ProgressLogger("S1"))
        except subprocess.TimeoutExpired as exc:
            # 超时是最容易误诊的失败：必须说清「预算多少、算到哪、会不会重来」，
            # 否则用户只看到一句 TimeoutExpired（实机曾误判为需要 CUDA GPU）。
            n_p = _count_depth_maps(dense_dir, ".photometric.bin")
            n_g = _count_depth_maps(dense_dir, ".geometric.bin")
            partial = getattr(exc, "output", "") or ""
            log_path = ""
            if partial:
                log_path = _dump_text_log(workspace, 10 + i, list(cmd),
                                          str(partial))
            where = f"；完整日志：{log_path}" if log_path else ""
            if i == 2:
                raise SfmError(
                    f"dense[2/3] 稠密深度图超时：预算 {step_timeout}s 用尽"
                    f"（{n_views} 张 × {mp:.2f} MP × {passes} 遍）。"
                    f"已完成 photometric {n_p}/{n_views}、"
                    f"geometric {n_g}/{n_views}；"
                    "产物已保留，重跑会从断点续算（位姿未变时不再重算）。\n"
                    "加速建议：① 降低 sfm_solve.dense_max_image_size"
                    f"（当前 {max_image_size or '跟随输入'}）；"
                    "② sfm_solve.dense_geom_consistency=false（省掉第二遍）；"
                    "③ 显式设置 sfm_solve.dense_timeout_s 放宽预算。"
                    f"{where}") from exc
            raise SfmError(
                f"dense[{i}/3] 超时：预算 {step_timeout}s 用尽{where}") from exc
        log_path = _dump_step_log(workspace, 10 + i, list(cmd), proc)
        if proc.returncode != 0:
            err = _error_tail((proc.stderr or "") + "\n" + (proc.stdout or ""))
            where = f"；完整日志：{log_path}" if log_path else ""
            if i == 2 and ("CUDA" in err or "cuda" in err):
                raise SfmError(
                    "dense 需要可用 CUDA GPU（patch_match_stereo 为 GPU-only）；"
                    f"请确认 NVIDIA 驱动 >= CUDA 13{where}。详情: {err}")
            raise SfmError(
                f"dense 步骤 {i} 失败 (exit={proc.returncode}){where}\n{err}")
        log.info("S1 dense[%d/3] 完成", i)
    fused = os.path.join(dense_dir, "fused.ply")
    if not os.path.exists(fused):
        raise SfmError("dense 未生成 fused.ply（融合点数过少）")
    return fused


def _count_photos(photo_dir: str) -> int:
    """照片目录内的图像数（超时预算的分母）。"""
    try:
        return max(1, len(list_images(photo_dir)))
    except OSError:
        return 1


def _photos_megapixels(photo_dir: str, dense_max_size: int = 0) -> float:
    """抽样读取一张照片的像素数（百万像素），用于估算稠密耗时。

    ``dense_max_size`` > 0 时按 ``PatchMatchStereo.max_image_size`` 折算——
    深度图分辨率是**稠密上限**而非 photos_small 尺寸，直接拿 photos_small
    估会高估 2 倍以上（2400 vs 1600 → 2.25 倍）。
    """
    from PIL import Image

    try:
        for path in list_images(photo_dir)[:1]:
            with Image.open(path) as im:
                w, h = im.size
                if dense_max_size and dense_max_size > 0:
                    scale = min(1.0, float(dense_max_size) / max(w, h, 1))
                    w, h = int(w * scale), int(h * scale)
                return (w * h) / 1e6
    except Exception:  # noqa: BLE001 - 估算失败用保守中位数
        pass
    return 3.0


def _dump_text_log(workspace: str, step_index: int, cmd: list[str],
                   text: str) -> str:
    """把（可能是超时中途的）输出落盘，返回日志路径。

    超时抛的是异常而非 ``CompletedProcess``，原实现因此**不写日志**，
    用户事后只能在旧日志里看到上一轮的 COLMAP 报错 —— 实机 2026-10-17
    失败原因被误读为「需要 CUDA GPU」正是这么来的。
    """
    logdir = os.path.join(workspace, "logs")
    try:
        os.makedirs(logdir, exist_ok=True)
        path = os.path.join(logdir, f"colmap_step{step_index}.log")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("COMMAND:\n" + " ".join(cmd) + "\n")
            fh.write("\nEXIT CODE: TIMEOUT\n")
            fh.write("\n===== OUTPUT (partial) =====\n" + text)
        return path
    except OSError as exc:  # noqa: BLE001
        log.warning("S1 COLMAP 超时日志落盘失败：%s", exc)
        return ""



def _incremental_dense_metrics(old_path: str, new_path: str) -> dict:
    """增量 dense 差异评估（P3）：旧产物 vs 新产物 -> 新增点占比/估计数。"""
    from h3d.io import load_point_cloud
    try:
        old_pc = load_point_cloud(old_path)
        new_pc = load_point_cloud(new_path)
        from scipy.spatial import cKDTree
        if len(old_pc.points) == 0 or len(new_pc.points) == 0:
            return {"incremental": True, "error": "空点云"}
        d, _ = cKDTree(np.asarray(old_pc.points)).query(
            np.asarray(new_pc.points), k=1)
        added = float((d > 0.02).mean())
        return {
            "incremental": True,
            "added_ratio": round(added, 4),
            "n_added_est": int(added * len(new_pc.points)),
            "n_old_points": len(old_pc.points),
            "n_new_points": len(new_pc.points),
        }
    except Exception as exc:  # noqa: BLE001
        return {"incremental": True, "error": str(exc)}


def calibrate_sift_params(photo_dir: str, n_sample: int = 8) -> dict:
    """S1 特征参数自动标定（P1）：按照片统计自适应 peak_threshold / matcher。

    统计：照片分辨率中位数、灰度梯度方差（纹理丰富度）。
      纹理贫乏（梯度方差小）→ 降低 peak_threshold 提取更多弱特征；
      纹理丰富 → 保持默认；
      matcher：照片 >=40 张且文件名含时间序列（IMG_/DSC_）→ sequential，
      否则 exhaustive（小规模穷举更稳）。
    返回 {"peak_threshold", "matcher", "n_photos", "mean_grad_var", "size"}。
    """
    from PIL import Image
    files = [os.path.basename(p) for p in list_images(photo_dir)]
    if not files:
        raise SfmError(f"照片目录无图像: {photo_dir}")
    grads, sizes, seq = [], [], 0
    for fn in files[:n_sample]:
        try:
            img = np.asarray(Image.open(os.path.join(photo_dir, fn))
                             .convert("L"), dtype=np.float64)
        except Exception:  # noqa: BLE001
            continue
        sizes.append(img.size)
        if img.size > 0:
            g = np.gradient(img)
            grads.append(float((g[0] ** 2 + g[1] ** 2).mean()))
        if any(t in fn.upper() for t in ("IMG_", "DSC_", "R_", "L_")):
            seq += 1
    n = len(files)
    mean_grad = float(np.mean(grads)) if grads else 0.0
    if mean_grad < 200.0:
        peak = 0.004 * 0.5
    elif mean_grad < 800.0:
        peak = 0.004
    else:
        peak = 0.004 * 1.5
    matcher = "sequential" if (n >= 40 and seq >= n * 0.6) else "exhaustive"
    return {
        "peak_threshold": round(float(peak), 5),
        "matcher": matcher,
        "n_photos": n,
        "mean_grad_var": round(mean_grad, 3),
        "size_median": int(np.median(sizes)) if sizes else 0,
        "calibrated": True,
    }


def run_incremental_sparse(photo_dir: str, workspace: str, colmap: str,
                           matcher: str = "exhaustive",
                           max_features: int = 8192,
                           peak_threshold: float = 0.004,
                           deterministic: bool = True,
                           threads: int = 0) -> str:
    """增量稀疏注册（P3）：复用既有 database/模型，仅注册新照片。

    流程：feature_extractor 追加新图特征（旧库保留）→ 匹配（增量）→
    image_registrator 把新图注册进旧模型 → 输出新模型（含旧图+新图）。
    返回 sparse 文本模型目录；旧 workspace 不存在时退化为全量重建。
    """
    db = os.path.join(workspace, "database.db")
    sparse = os.path.join(workspace, "sparse")
    # 复用既有模型前先择优：历史 workspace 的 sparse/0 可能只是碎片
    model_bin = ""
    if os.path.isdir(sparse):
        best = _pick_largest_model(sparse)
        if best:
            model_bin = _normalize_model_slot(sparse, best)
    if not (os.path.exists(db) and model_bin):
        return run_colmap_sparse(photo_dir, workspace, colmap,
                                 matcher=matcher, max_features=max_features,
                                 peak_threshold=peak_threshold,
                                 deterministic=deterministic, threads=threads)
    inc_dir = os.path.join(sparse, "0_inc")
    if os.path.isdir(inc_dir):          # 清理上次残留，避免 COLMAP 拒绝写
        shutil.rmtree(inc_dir)
    # COLMAP 4.x 对 --output_path 做 ExistsDir 校验，目录必须预先存在
    os.makedirs(inc_dir, exist_ok=True)
    steps = [
        [colmap, "feature_extractor",
         "--database_path", db, "--image_path", photo_dir,
         "--ImageReader.single_camera", "1",
         "--FeatureExtraction.use_gpu", "0",
         "--SiftExtraction.max_num_features", str(max_features),
         "--SiftExtraction.peak_threshold", str(peak_threshold)]
        + _repro_args("FeatureExtraction", deterministic, threads),
        [colmap, _matcher_cmd(matcher), "--database_path", db,
         "--FeatureMatching.use_gpu", "0",
         "--FeatureMatching.guided_matching", "1"]
        + _repro_args("FeatureMatching", deterministic, threads),
        [colmap, "image_registrator",
         "--database_path", db, "--input_path", model_bin,
         "--output_path", os.path.join(sparse, "0_inc"),
         "--Mapper.ba_use_gpu", "0",
         "--Mapper.num_threads", _mapper_threads(deterministic, threads),
         "--Mapper.random_seed", "0"],
    ]
    for i, cmd in enumerate(steps, 1):
        log.info("S1 增量[%d/3]: %s", i, " ".join(cmd[:4]) + " ...")
        proc = run_external(cmd, cwd=workspace, timeout=1800)
        if proc.returncode != 0:
            raise SfmError(f"增量注册步骤 {i} 失败: {(proc.stderr or '')[-400:]}")
    model_new = os.path.join(sparse, "0_inc")
    if not os.path.isdir(model_new):
        raise SfmError("增量注册未产出模型")
    model_txt = os.path.join(sparse, "0_inc_txt")
    os.makedirs(model_txt, exist_ok=True)
    proc = run_external([colmap, "model_converter",
                         "--input_path", model_new,
                         "--output_path", model_txt, "--output_type", "TXT"],
                        cwd=workspace, timeout=300)
    if proc.returncode != 0:
        raise SfmError(f"模型转文本失败: {(proc.stderr or '')[-300:]}")
    return model_txt


def subset_names(new_views):
    """新视角文件名集合（用于参考图排除）。"""
    return {os.path.basename(f) for f in new_views}


def _name_distance(name: str, refs: list) -> int:
    """文件名排序邻近度：与任一参考的最小位置距离。"""
    best = 10 ** 9
    for r in refs:
        try:
            # 提取数字段比较排序位置
            import re
            n_num = re.findall(r"\d+", name)
            r_num = re.findall(r"\d+", r)
            if n_num and r_num:
                best = min(best, abs(int(n_num[0]) - int(r_num[0])))
        except (ValueError, IndexError):
            continue
    return best


def incremental_dense_new_views(photo_dir: str, workspace: str, colmap: str,
                                new_images: list, k_neighbors: int = 4,
                                deterministic: bool = True,
                                threads: int = 0) -> dict:
    """真增量 dense（P2，仅新视角）：只对新视角（+最近邻参考图）做稠密化。

    COLMAP patch_match_stereo 无按图过滤选项（如实）；本实现构建「新视角 +
    k 个最近邻旧视角」的子集 dense 工作区，仅对该子集运行 undistort + PMS，
    fused_new.ply 与旧 fused_dense 合并（voxel 去重）→ 增量几何更新。

    返回 {"new_views", "subset_size", "fused_new", "n_added"}；
    旧 fused 不存在时退化为全量 dense（走 run_dense_stereo 分支）。
    """
    old_fused = os.path.join(workspace, "dense", "fused.ply")
    new_views = [n for n in new_images]
    if not os.path.exists(old_fused) or len(new_views) < 2:
        return {"new_views": len(new_views), "subset_size": 0,
                "mode": "full_dense"}

    # 子集目录：新视角 + 最近邻参考图（文件名排序邻近，保证立体重叠）
    subset = os.path.join(workspace, "dense_inc")
    os.makedirs(subset, exist_ok=True)
    import shutil
    all_photos = [os.path.basename(p) for p in list_images(photo_dir)]
    for v in new_views[:k_neighbors + 2]:
        src = os.path.join(photo_dir, v)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(subset, v))
    # 参考图：排序列表中与 new_views 邻近且未复制的文件
    ref_count = 0
    if v_names := [os.path.basename(f) for f in new_views]:
        for i, name in enumerate(all_photos):
            if ref_count >= k_neighbors:
                break
            if name in subset_names(new_views):
                continue
            if _name_distance(name, v_names) < 3:  # 文件名排序邻近
                shutil.copy2(os.path.join(photo_dir, name),
                             os.path.join(subset, name))
                ref_count += 1
    if len(os.listdir(subset)) < 2:
        return {"new_views": len(new_views), "subset_size": 0,
                "mode": "full_dense"}
    # 子集独立重建（feature→matcher→mapper）：新视角+参考图完整位姿，
    # 避免 undistorter 对未注册图像崩溃；仅稠密化子集（真增量语义）
    ws_inc = workspace + "_inc"
    inc_model_txt = run_incremental_sparse(
        subset, ws_inc, colmap,
        matcher="exhaustive",
        max_features=8192, peak_threshold=0.004,
        deterministic=deterministic, threads=threads)
    model_bin_inc = os.path.join(os.path.dirname(inc_model_txt), "0")
    fused_new = run_dense_stereo(subset, model_bin_inc, ws_inc, colmap)
    # 与旧 fused 合并（体素去重）
    from h3d.io import load_point_cloud, save_point_cloud
    old_pc = load_point_cloud(old_fused)
    new_pc = load_point_cloud(fused_new)
    merged = old_pc + new_pc
    merged = merged.voxel_down_sample(0.01)
    save_point_cloud(merged, old_fused)
    added = len(new_pc.points)
    return {"new_views": len(new_views), "subset_size": len(os.listdir(subset)),
            "fused_new": fused_new, "n_added": added, "mode": "subset_dense"}


# ---------------------------------------------------------------------------
# 照片集指纹与「同照片多件」复用（2026-10-24 实机）
# ---------------------------------------------------------------------------
_S1_INDEX_REL = os.path.join("99_runs", "s1_reuse_index.json")
#: 索引未命中时，最多扫多少件已完成 S1 的兄弟目录（避免百件项目全量哈希）
_REUSE_SCAN_LIMIT = 12
#: 用户级索引文件名（2026-10-26：跨项目共享）
_INDEX_NAME = "s1_reuse_index.json"
#: 用户级索引最多保留多少条指纹（按登记时间淘汰最旧，防止无限增长）
_INDEX_MAX_ENTRIES = 500


def user_index_dir() -> str:
    """用户级索引目录。

    ``H3D_HOME`` 环境变量优先（便携版 / 测试用 tmp 目录 / 多用户隔离），
    否则 ``~/.h3d``。不用 ``%LOCALAPPDATA%``：那是 Windows 专有，而本系统
    在 Linux 上也要能跑（持续集成是用 Linux 跑的单元测试）。
    """
    home = os.environ.get("H3D_HOME")
    if home:
        return os.path.abspath(os.path.expanduser(home))
    return os.path.join(os.path.expanduser("~"), ".h3d")


def _entry_root(ent: dict) -> str:
    """索引条目里记录的项目根（老索引没有该字段 → 视为当前项目）。"""
    return str(ent.get("root") or "")


def photo_set_fingerprint(photo_dir: str) -> str:
    """照片集指纹：文件名（小写）+ 字节数 + 内容 SHA-1（流式，不整读进内存）。

    只比文件名不够（同一批照片换目录很常见，不同器物也可能同名 DSC_0008.JPG），
    所以必须把内容哈希进去；30 张 4.5K JPEG 约 120 MB，实测 < 0.5 s。
    """
    files = sorted(list_images(photo_dir),
                   key=lambda p: os.path.basename(p).lower())
    h = hashlib.sha1()
    for path in files:
        h.update(os.path.basename(path).lower().encode("utf-8"))
        h.update(b"\x00")
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def _sfm_params_key(cfg) -> str:
    """决定几何结果的那些参数（变了就不能复用别人的点云与位姿）。"""
    s = cfg.sfm_solve
    return "|".join(str(v) for v in (
        s.max_image_size, int(bool(s.dense)), s.dense_max_image_size,
        int(bool(s.dense_geom_consistency)), int(bool(s.dense_gate)),
        s.dense_gate_std, s.dense_gate_density,
        s.dense_min_pixels, s.dense_max_pixels))


def _load_index_file(path: str) -> dict:
    """读一份索引文件（缺失/损坏/结构不对一律返回空索引，不抛异常）。"""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "entries": {}}


def _load_reuse_index(root: str) -> dict:
    """加载复用索引：**用户级为主，项目级为兼容**。

    2026-10-26 起索引提升到用户级（``~/.h3d/s1_reuse_index.json``），
    这样同一批照片在「最终测试」「测试测试111」两个项目里只解一次 SfM，
    而不必各自解 30 分钟。项目级 ``99_runs/s1_reuse_index.json`` 仍会被读取
    （老项目升级后第一次运行即可命中），但新登记一律写用户级。

    合并规则：同指纹以**用户级**为准（它更新），项目级条目补 root 字段后并入
    —— 老条目没有 root，只能认定属于它所在的那个项目。
    """
    user = _load_index_file(os.path.join(user_index_dir(), _INDEX_NAME))
    legacy = _load_index_file(os.path.join(root, _S1_INDEX_REL))
    entries = dict(legacy.get("entries") or {})
    for fp, ent in entries.items():
        if isinstance(ent, dict) and not ent.get("root"):
            ent["root"] = os.path.abspath(root)
    entries.update(user.get("entries") or {})
    return {"version": 2, "entries": entries}


def _save_reuse_index(root: str, data: dict) -> None:
    """原子写用户级索引；并镜像一份到项目级（供旧版本 exe 读取）。

    写前**重新读一次**再合并：并发（workers>1 的进程池 / 用户开了两个项目）
    时两个进程各自持有旧快照，直接覆盖会丢掉对方刚登记的指纹，表现为
    「明明解算过却总是不命中」。

    索引损坏只影响复用、不影响主流程，所有失败都降级为 WARNING。
    """
    entries = dict((data or {}).get("entries") or {})
    # 按登记时间淘汰最旧，防止索引无限增长
    if len(entries) > _INDEX_MAX_ENTRIES:
        def _ts(key):
            ent = entries.get(key) or {}
            return float(ent.get("ts") or 0.0)
        keep = sorted(entries, key=_ts, reverse=True)[:_INDEX_MAX_ENTRIES]
        entries = {k: entries[k] for k in keep}
        log.info("S1 复用索引超上限，按时间淘汰旧条目（保留 %d 条）",
                 _INDEX_MAX_ENTRIES)
    payload = {"version": 2, "entries": entries}

    path = os.path.join(user_index_dir(), _INDEX_NAME)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        merged = _load_index_file(path).get("entries") or {}
        stale = {k: v for k, v in merged.items() if k not in entries}
        if stale:
            entries.update(stale)
            payload["entries"] = entries
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:  # noqa: BLE001
        log.warning("S1 复用索引写入失败（不影响主流程）：%s", exc)
        return
    # 项目级镜像：新版不再读它（用户级已覆盖），但换回旧 exe 时还能命中。
    legacy = os.path.join(root, _S1_INDEX_REL)
    try:
        os.makedirs(os.path.dirname(legacy), exist_ok=True)
        tmp2 = legacy + ".tmp"
        with open(tmp2, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp2, legacy)
    except OSError as exc:  # noqa: BLE001 - 镜像失败无所谓
        log.debug("S1 复用索引项目级镜像写入失败：%s", exc)


def _reuse_outputs(root: str, item: str) -> tuple[str, str] | None:
    """某件的 S1 可复用产物：(fused_dense.ply, capture_meta.json)，缺一不可。"""
    if not root:
        return None
    fused = os.path.join(root, "10_dense", item, "fused_dense.ply")
    meta = os.path.join(root, "90_meta", f"{item}_{CAPTURE_META}")
    if os.path.exists(fused) and os.path.exists(meta):
        return fused, meta
    return None


def reuse_sfm_result(task, cfg, photo_dir: str) -> dict | None:
    """照片集与同项目内另一件完全相同 → 复制其 S1 产物，跳过 30 分钟解算。

    返回 ``{"outputs": [...], "metrics": {...}, "elapsed": s}``；不适用时 None。
    所有判断（复用与否、来自哪件）都会写进日志与 manifest ``s1_reuse``，
    不做静默替换 —— 两件共用一次重建是**语义变化**，用户必须看得见。
    """
    s = cfg.sfm_solve
    if not getattr(s, "reuse_identical_photos", True):
        return None
    if s.force_redense or not s.dense:
        return None
    t0 = time.time()
    fp = photo_set_fingerprint(photo_dir)
    params = _sfm_params_key(cfg)
    idx = _load_reuse_index(task.root)
    entries = idx["entries"]

    donor = None
    donor_root = ""
    params_known = True
    ent = entries.get(fp)
    if ent and ent.get("params") == params:
        ent_root = _entry_root(ent) or os.path.abspath(task.root)
        if (ent.get("item") != task.item_id
                or os.path.abspath(ent_root) != os.path.abspath(task.root)):
            if _reuse_outputs(ent_root, ent["item"]):
                donor = ent["item"]
                donor_root = ent_root
    if donor is None:
        # 旧项目 / 升级前已解算的件没有索引，扫同根兄弟目录（限量）。
        # 这类复用源不记录参数，一致性未知 —— 必须降级为 WARNING 告知。
        params_known = False
        pdir = os.path.join(task.root, "00_photo")
        try:
            names = sorted(os.listdir(pdir))
        except OSError:
            names = []
        cand = [n for n in names
                if n != task.item_id
                and os.path.isdir(os.path.join(pdir, n))
                and _reuse_outputs(task.root, n)]
        if len(cand) > _REUSE_SCAN_LIMIT:
            log.info("S1 复用扫描跳过：同根 %d 件已完成 S1，超过扫描上限 %d",
                     len(cand), _REUSE_SCAN_LIMIT)
            cand = []
        for name in cand:
            try:
                if photo_set_fingerprint(os.path.join(pdir, name)) == fp:
                    donor = name
                    donor_root = task.root
                    break
            except OSError as exc:  # noqa: BLE001
                log.debug("S1 复用扫描 %s 失败：%s", name, exc)

    if donor is None:
        return None
    src = _reuse_outputs(donor_root, donor)
    if src is None:      # 竞态：扫描后文件被删
        return None
    fused, meta = src
    same_root = os.path.abspath(donor_root) == os.path.abspath(task.root)
    if not same_root:
        # 跨项目复用是**更强的语义变化**：两件文物在不同项目里，用同一份重建
        # 结果。必须显式告知，并提醒交付包应各自独立保存。
        log.warning("S1 跨项目复用：%s（本项目）← %s（%s）——两件产物逐字节同源，"
                    "交付时请确认是否为同一件器物的重复拍摄",
                    task.item_id, donor, donor_root)
    dst_fused = os.path.join(task.root, "10_dense", task.item_id,
                             "fused_dense.ply")
    dst_meta = os.path.join(task.root, "90_meta",
                            f"{task.item_id}_{CAPTURE_META}")
    os.makedirs(os.path.dirname(dst_fused), exist_ok=True)
    os.makedirs(os.path.dirname(dst_meta), exist_ok=True)
    shutil.copy2(fused, dst_fused)
    shutil.copy2(meta, dst_meta)

    n_images = n_cameras = 0
    try:
        with open(dst_meta, encoding="utf-8") as fh:
            cm = json.load(fh)
        n_images = int(cm.get("n_images") or 0)
        n_cameras = int(cm.get("n_cameras") or 0)
    except (OSError, ValueError) as exc:  # noqa: BLE001
        log.warning("S1 复用：capture_meta 解析失败（%s），计数留 0", exc)

    entries[fp] = {"item": task.item_id, "params": params, "donor": donor,
                   "root": os.path.abspath(task.root), "ts": time.time()}
    _save_reuse_index(task.root, idx)

    elapsed = time.time() - t0
    log.info("S1 复用：%s ← %s（照片集完全相同，指纹 %s，跳过 SfM 与稠密重建%s）",
             task.item_id, donor, fp[:12],
             "" if same_root else f"（跨项目：{donor_root}）")
    if not params_known:
        log.warning("S1 复用源 %s 是旧版本产物，稠密参数一致性未知；"
                    "若改过 dense_* 参数，请把 sfm_solve.reuse_identical_photos "
                    "置 false 后重跑", donor)
    metrics = {
        "n_cameras": n_cameras,
        "n_registered": n_images,
        "n_photos": len(list_images(photo_dir)),
        "capture_meta": dst_meta,
        "fused_dense": dst_fused,
        "dense": True,
        "dense_gate": {"status": "reused"},
        "dense_incremental": {"incremental": False},
        "calibrated": {"calibrated": False, "reused": True},
        "incremental": {"incremental": False},
        "s1_reuse": {"from": donor, "fingerprint": fp[:12],
                     "params_match": params_known,
                     "from_root": donor_root,
                     "cross_project": not same_root},
        "timings": {"sparse_s": 0.0, "dense_s": 0.0,
                    "reuse_s": round(elapsed, 2), "total_s": round(elapsed, 2)},
    }
    return {"outputs": [dst_meta, dst_fused], "metrics": metrics,
            "elapsed": elapsed}


def _record_reuse_index(task, cfg, photo_dir: str) -> None:
    """正常解算完成后登记指纹，供后续同照片的件（含跨项目）复用。"""
    if not getattr(cfg.sfm_solve, "reuse_identical_photos", True):
        return
    try:
        fp = photo_set_fingerprint(photo_dir)
        idx = _load_reuse_index(task.root)
        idx["entries"][fp] = {"item": task.item_id,
                              "params": _sfm_params_key(cfg),
                              "root": os.path.abspath(task.root),
                              "ts": time.time()}
        _save_reuse_index(task.root, idx)
    except OSError as exc:  # noqa: BLE001
        log.debug("S1 复用索引登记失败：%s", exc)


def run_sfm_solve(task, cfg):
    """S1 stage：00_photo/<item> → capture_meta.json（COLMAP sparse 位姿解算）。

    默认关闭（cfg.sfm_solve.enabled=false）；开启后未找到 COLMAP 抛
    SfmError -> 编排层失败隔离（failures.csv 记录，不中断其余文物）。
    """
    if not cfg.sfm_solve.enabled:
        from h3d.runner.task import StageResult, TaskStatus
        return StageResult("S1", TaskStatus.SKIPPED, [],
                           {"skip_reason": "sfm_solve.enabled=false"}, 0.0)
    photo_dir = _photo_dir_for(task, cfg)
    # 统一口径：只接受受支持图像（.jfif/.jpe/.tiff 等变体也算），
    # 避免「目录非空但全是非图像文件」时把空输入交给 COLMAP。
    if not list_images(photo_dir):
        # 报错必须给出**正确预期路径**。原实现只打印解析结果，而解析在
        # 「00_photo/<item> 不存在」时会回退到上级目录，用户看到的是
        # 与项目无关的路径（实机 2026-10-17：项目在 output/_scene1_no_photo，
        # 报错却指向 output/00_photo），完全无法据此排查。
        expect = os.path.join(task.root, "00_photo", task.item_id)
        raise RecoverableError(
            f"S1 照片目录为空或不存在: {photo_dir}\n"
            f"  正确布局应为：{expect}{os.sep}*.jpg\n"
            f"  （也支持把照片直接放在 {os.path.join(task.root, '00_photo')}"
            f" 根下，不建 <编号> 子目录）\n"
            f"  若本件**只有激光扫描件、没有照片**，请关闭 sfm_solve.enabled；"
            f"此时 S1 会被跳过，M1~M6 照常执行并输出纯几何模型。")
    colmap = _find_colmap(cfg)
    from h3d.runner.task import StageResult, TaskStatus
    src_photo_dir = photo_dir          # 指纹必须基于**原图**，不是缩放副本
    # 同照片多件复用：30 张照片被复制成 5 件时，逐件重解 SfM 是纯浪费
    # （单件 ~30 min，位姿按定义完全相同）。命中则复制产物直接返回。
    if getattr(cfg.sfm_solve, "reuse_identical_photos", True):
        try:
            reused = reuse_sfm_result(task, cfg, src_photo_dir)
        except Exception as exc:  # noqa: BLE001 - 复用失败退回正常解算
            log.warning("S1 复用检查失败（改为正常解算）：%s", exc)
            reused = None
        if reused is not None:
            return StageResult("S1", TaskStatus.DONE, reused["outputs"],
                               reused["metrics"], reused["elapsed"])
    if not colmap:
        raise RecoverableError(
            "S1 未找到 COLMAP，请安装并配置 sfm_solve.colmap_path"
            "（本机安装、仓库不打包二进制）")
    t0 = time.time()
    workspace = cfg.sfm_solve.workspace or os.path.join(
        task.root, "99_runs", "sfm_tmp", task.item_id)
    # 真实大图稳定性：超尺寸照片下采样副本（COLMAP Windows 构建对 4496px
    # 大图特征提取偶发崩溃 exit 0xC0000005）
    if cfg.sfm_solve.max_image_size > 0:
        photo_dir = _prepare_photos(photo_dir, workspace,
                                    cfg.sfm_solve.max_image_size)
    # S1 特征参数自动标定（P1）：按真实照片统计自适应 peak_threshold/matcher
    calib = {}
    matcher = cfg.sfm_solve.matcher
    peak = cfg.sfm_solve.peak_threshold
    if cfg.sfm_solve.auto_calibrate:
        try:
            calib = calibrate_sift_params(photo_dir)
            peak = calib["peak_threshold"]
            # 配置优先（2026-10-23 实机）：用户显式写 matcher: sequential 时，
            # 标定把它偷偷改成 exhaustive —— 30 张照片的匹配对从 58 涨到 435，
            # 匹配段耗时 ×7，而日志里只在 metrics 里留一句 calibrated。
            # 现在只有 matcher="auto" 才允许标定决定匹配策略。
            if str(matcher).lower() == "auto":
                matcher = calib["matcher"]
            elif calib["matcher"] != matcher:
                log.warning("S1 标定建议 matcher=%s，但配置显式指定 %s —— "
                            "以配置为准（配对数 %d vs %d）",
                            calib["matcher"], matcher,
                            calib["n_photos"], calib["n_photos"])
                calib["matcher_overridden_by_config"] = True
                calib["matcher"] = matcher
            log.info("S1 特征标定：%s", calib)
        except SfmError:
            log.warning("S1 特征标定失败，使用配置默认参数")
    if str(matcher).lower() == "auto":
        matcher = "exhaustive"
    # 增量判定（P3）：运行前 workspace 已有 database + 模型 → 增量注册
    #
    # 缺陷修复（2026-09-15）：原实现末尾有一句无条件的
    # ``inc_meta = {"incremental": False}``，会把上面增量分支写入的
    # ``{"incremental": True, "fallback": ..., "error": ...}`` **整段覆盖**，
    # 导致：(1) 增量成功运行在报表里被误标为全量重建；(2) 增量失败回退的
    # 原因（error）丢失，S1 指标无法追溯。此处改为按实际分支赋值。
    model_txt = None
    inc_meta = {"incremental": False}
    if cfg.sfm_solve.workspace and os.path.exists(
            os.path.join(cfg.sfm_solve.workspace, "database.db")):
        try:
            model_txt = run_incremental_sparse(
                photo_dir, cfg.sfm_solve.workspace, colmap,
                matcher=matcher, max_features=cfg.sfm_solve.max_features,
                peak_threshold=peak,
                deterministic=cfg.sfm_solve.deterministic,
                threads=cfg.sfm_solve.num_threads)
            inc_meta = {"incremental": True, "fallback": False}
            log.info("S1 增量注册完成（复用既有模型）")
        except SfmError as exc:
            log.warning("增量注册失败（回退全量重建）：%s", exc)
            inc_meta = {"incremental": True, "fallback": True,
                        "error": str(exc)}
    if model_txt is None:
        model_txt = run_colmap_sparse(
            photo_dir, workspace, colmap,
            matcher=matcher,
            max_features=cfg.sfm_solve.max_features,
            peak_threshold=peak,
            deterministic=cfg.sfm_solve.deterministic,
            threads=cfg.sfm_solve.num_threads)
    metrics_calib = calib or {"calibrated": False}
    cameras = parse_cameras(os.path.join(model_txt, "cameras.txt"))
    images = parse_images(os.path.join(model_txt, "images.txt"))
    if not images:
        raise RecoverableError("S1 SfM 未注册任何图像（照片质量不足）")
    meta_dir = os.path.join(task.root, "90_meta")
    os.makedirs(meta_dir, exist_ok=True)
    meta_path = os.path.join(meta_dir, f"{task.item_id}_{CAPTURE_META}")
    write_capture_meta(cameras, images, meta_path)
    elapsed = time.time() - t0
    # 分段计时（2026-10-23）：S1 一次跑 30 分钟，manifest 里只有一个总数，
    # 完全无法判断时间花在特征/匹配/建图/稠密哪一段，用户只能凭感觉优化。
    timings = {"sparse_s": round(elapsed, 1)}
    outputs = [meta_path]
    metrics = {
        "n_cameras": len(cameras),
        "n_registered": len(images),
        "n_photos": len(list_images(photo_dir)),
        "capture_meta": meta_path,
        "calibrated": metrics_calib,
        "incremental": inc_meta,
    }
    # 场景②闭环：dense 可选步骤（需 GPU）→ fused_dense.ply 供 M1 消费
    # （P3 增量式更新：检测既有 fused_dense，记录增量指标；多时相补采场景）
    if cfg.sfm_solve.dense:
        _t_dense = time.time()
        model_bin = os.path.join(os.path.dirname(model_txt), "0")
        fused = run_dense_stereo(
            photo_dir, model_bin, workspace, colmap,
            min_pixels=cfg.sfm_solve.dense_min_pixels,
            max_pixels=cfg.sfm_solve.dense_max_pixels,
            timeout_s=cfg.sfm_solve.dense_timeout_s,
            max_image_size=cfg.sfm_solve.dense_max_image_size,
            geom_consistency=cfg.sfm_solve.dense_geom_consistency,
            force_clear=cfg.sfm_solve.force_redense)
        # 质量门（P2）：离群剔除 + 密度过滤，保证合并稳健
        gate_metrics = {}
        if cfg.sfm_solve.dense_gate:
            from h3d.dense_gate import run_dense_gate
            from h3d.io import load_point_cloud, save_point_cloud
            pcd = load_point_cloud(fused)
            pcd, gate_metrics = run_dense_gate(
                pcd, std_ratio=cfg.sfm_solve.dense_gate_std,
                min_density_ratio=cfg.sfm_solve.dense_gate_density)
            fused = os.path.join(workspace, "dense", "fused_gated.ply")
            save_point_cloud(pcd, fused)
        dense_out = os.path.join(task.root, "10_dense", task.item_id)
        os.makedirs(dense_out, exist_ok=True)
        fused_dense = os.path.join(dense_out, "fused_dense.ply")
        # 增量式更新（P2 真增量）：既有 fused_dense 时只对新视角做子集 dense
        inc_metrics = {"incremental": False}
        if os.path.exists(fused_dense):
            # 增量 dense（P2 数字孪生维护闭环）：优先自研深度图增量更新
            #（绕开 COLMAP 子集限制）；失败/无新增回退全量重建
            new_names = [im["name"] for im in images]
            inc_metrics = {"incremental": False}
            try:
                from h3d.incremental_depth import incremental_depth_update
                upd = incremental_depth_update(
                    photo_dir, fused_dense, new_names, cameras,
                    np.eye(3), step=8)
                if upd["n_added_total"] > 0:
                    inc_metrics = {"incremental": True,
                                   "method": "own_depth", **upd}
                    log.info("S1 dense 深度图增量更新：%s", upd)
                else:
                    log.warning("深度图增量无新增点，回退全量 dense")
                    inc_metrics = {"incremental": True, "method": "fallback",
                                   "reason": "own_depth_no_added"}
                    shutil.copy2(fused, fused_dense)
            except Exception as exc:  # noqa: BLE001 - 增量失败回退全量
                log.warning("深度图增量失败（回退全量）：%s", exc)
                inc_metrics = {"incremental": True, "method": "fallback",
                               "error": str(exc)}
                shutil.copy2(fused, fused_dense)
        else:
            shutil.copy2(fused, fused_dense)
        outputs.append(fused_dense)
        metrics["dense"] = True
        metrics["fused_dense"] = fused_dense
        metrics["dense_gate"] = gate_metrics or {"status": "skipped"}
        metrics["dense_incremental"] = inc_metrics
        log.info("S1 dense 完成：%s -> %s（%s）", task.item_id, fused_dense,
                 gate_metrics)
        timings["dense_s"] = round(time.time() - _t_dense, 1)
    else:
        timings["dense_s"] = 0.0
    # 稠密段必须计入阶段耗时：原实现 elapsed 在 dense 之前取，
    # 30 分钟的稠密重建会从 elapsed_s 里凭空消失（报表看起来"很快"）。
    elapsed = time.time() - t0
    timings["total_s"] = round(elapsed, 1)
    metrics["timings"] = timings
    # 登记指纹：后续同照片的件可直接复用本件结果（省 30 min/件）
    _record_reuse_index(task, cfg, src_photo_dir)
    log.info("S1 完成：%s（%d/%d 张注册，%d 相机，%.1fs = 稀疏 %.1fs + 稠密 %.1fs）",
             task.item_id, len(images), metrics["n_photos"],
             len(cameras), elapsed, timings["sparse_s"], timings["dense_s"])
    return StageResult("S1", TaskStatus.DONE, outputs, metrics, elapsed)


__all__ = [
    "CAPTURE_META", "SfmError", "run_sfm_solve",
    "parse_cameras", "parse_images", "read_points3d",
    "write_capture_meta", "run_colmap_sparse", "run_dense_stereo",
    "photo_set_fingerprint", "reuse_sfm_result",
]


def _cache_is_stale(src: str, dst: str) -> bool:
    """预处理副本是否需要刷新：副本不存在、或源文件比副本新。

    mtime 容差 1 秒（FAT/NTFS 时间戳精度与文件系统拷贝可能带来微差）。
    """
    if not os.path.exists(dst):
        return True
    try:
        return os.path.getmtime(src) - os.path.getmtime(dst) > 1.0
    except OSError:
        return True


def _prepare_photos(photo_dir: str, workspace: str,
                    max_size: int = 0) -> str:
    """照片预处理（真实大图稳定性）：超尺寸时下采样副本供 COLMAP。

    COLMAP Windows CUDA 构建对 4496×3000 大图特征提取偶发崩溃
    （exit 0xC0000005）——下采样到 max_size 长边后稳定。返回照片目录
    （原目录或预处理副本目录）。
    """
    if max_size <= 0:
        return photo_dir
    from PIL import Image
    out = os.path.join(workspace, "photos_small")
    os.makedirs(out, exist_ok=True)
    files = [os.path.basename(p) for p in list_images(photo_dir)]
    n_skip = 0
    for fn in files:
        src = os.path.join(photo_dir, fn)
        dst = os.path.join(out, fn)
        try:
            with Image.open(src) as im:
                w, h = im.size
                if max(w, h) <= max_size:
                    # 缺陷修复（2026-09-15）：原实现在副本已存在时**无条件跳过**，
                    # 于是「源照片被替换/重拍后重跑」会静默继续使用过期副本，
                    # 表现为「换了照片但重建结果不变」，极难排查。
                    # 改为：源文件比副本新（或尺寸不同）时刷新。
                    if _cache_is_stale(src, dst):
                        im.save(dst)
                    n_skip += 1
                    continue
                scale = max_size / max(w, h)
                im2 = im.resize((int(w * scale), int(h * scale)),
                                Image.LANCZOS)
                im2.save(dst, quality=90)
        except Exception:  # noqa: BLE001
            continue
    log.info("照片预处理：%d 张（%d 张未缩放）-> %s", len(files), n_skip,
             out)
    return out
