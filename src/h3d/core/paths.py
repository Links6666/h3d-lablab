"""h3d.core.paths : 统一输出布局（一个综合文件夹 + 两棵子树）。

背景（2026-10-26 实机反馈）
---------------------------
跑完一件文物，项目根下会同时冒出 ``00_raw`` / ``00_photo`` / ``10_dense`` /
``30_dense`` / ``50_tex`` / ``80_export`` / ``90_meta`` / ``99_runs`` /
``output`` / ``report`` / ``config.yaml`` —— 十来个平级目录，用户找不到"哪个
是能直接拿走的成品"。目录语义只有开发者分得清（80_export 是交付、50_tex 是
中间贴图），交付方拿到后第一句话往往是"模型在哪"。

新布局（v1.1.10 起）
--------------------
::

    <项目根>/                      ← 综合文件夹（用户选定的那个）
        config.yaml                唯一放在根上的文件（要手改配置）
        输出说明.txt                自动生成：两个子目录各放什么
        阶段成果/                  ① 阶段性成果：沿用旧骨架，可整体重算
            00_raw/ 00_photo/ 00_ctrl/
            10_dense/ 30_dense/ 50_tex/ 90_meta/ 99_runs/ output/
        交付成果/                  ② 直接交付：只放能直接打开的东西
            report/                单一 report 目录（html/csv/png/md）
            <文物编号>/              <编号>.ply / .obj / .mtl / _diffuse.png / .glb

为什么这样切
------------
* **按"能不能直接拿走"分**，而不是按阶段号：``交付成果`` 里每个文件都能双击
  打开（模型、贴图、报表）；``阶段成果`` 里都是可重算的中间态。
* **阶段骨架不动**：所有阶段代码仍按 ``task.root/<阶段目录>`` 取路径，只是
  ``task.root`` 从"项目根"下沉一级变成"阶段成果"，改动集中在一处（数据根的
  父目录由 ``00_raw`` 决定，见 :func:`h3d.runner.batch_processor.discover_items`）。
* **旧项目仍能跑**：:func:`delivery_item_dir` / :func:`report_dir` 会识别
  "这一层是不是 ``阶段成果``"，不是就按旧布局（``80_export`` / ``report``）
  回退。迁移只在 GUI 打开历史项目时做（:func:`migrate_legacy`），CLI 不强制。

术语
----
``project_root``：用户选定的项目根，也就是"综合文件夹"。
``stage_root``：``<project_root>/阶段成果``，阶段代码眼里的"根"。
"""

from __future__ import annotations

import os
import shutil
from typing import Optional

#: 阶段成果子目录名（沿用旧骨架，不做二次分级）
STAGE_DIR = "阶段成果"
#: 交付成果子目录名
DELIVERY_DIR = "交付成果"

#: 旧布局下"交付模型"目录名（仅用于识别与迁移，新布局不再生成）
LEGACY_EXPORT_DIR = "80_export"
#: 旧布局下报表目录名
LEGACY_REPORT_DIR = "report"

#: 阶段成果下的目录骨架（迁移时按此顺序搬运；未出现的跳过）
_STAGE_CHILDREN = ("00_raw", "00_photo", "00_ctrl", "10_dense", "30_dense",
                   "50_tex", "70_report", "90_meta", "99_runs", "output")

#: 判定"这确实是个 H3D 项目"的痕迹（**故意不含** ``output`` 与 ``report``，
#: 理由见 :func:`looks_like_project`）
_PROJECT_MARKERS = ("config.yaml",) + tuple(
    d for d in _STAGE_CHILDREN if d != "output") + (LEGACY_EXPORT_DIR,)


def stage_root(project_root: str) -> str:
    """``<项目根>/阶段成果``。"""
    return os.path.join(project_root, STAGE_DIR)


def delivery_root(project_root: str) -> str:
    """``<项目根>/交付成果``。"""
    return os.path.join(project_root, DELIVERY_DIR)


def project_root_of(path: str) -> str:
    """把 ``阶段成果`` / ``交付成果`` / 子目录反推回项目根（综合文件夹）。

    传入已经是项目根的路径时原样返回 —— 于是同一条路径既能表达"新布局的
    阶段根"又能表达"旧布局的项目根"，下游只需调这一个函数。
    """
    p = os.path.abspath(path)
    base = os.path.basename(p)
    if base in (STAGE_DIR, DELIVERY_DIR):
        return os.path.dirname(p)
    # ``<项目根>/阶段成果/<子目录>`` 这种也按两级回退
    parent = os.path.dirname(p)
    if os.path.basename(parent) in (STAGE_DIR, DELIVERY_DIR):
        return os.path.dirname(parent)
    return p


def is_stage_root(path: str) -> bool:
    """该路径是不是"阶段成果"层（决定新/旧布局）。"""
    return os.path.basename(os.path.abspath(path)) == STAGE_DIR


def _uses_new_layout(base: str) -> bool:
    """判定这一层该按新布局解析交付/报表路径。

    三级依据，任一条成立即为新布局：

    1. 传入的就是 ``.../阶段成果``（阶段代码手里的 ``task.root``）；
    2. 项目根下已有 ``阶段成果/``（``ensure_layout`` 建过）；
    3. 项目根下已有 ``交付成果/``。

    **不用"项目根下有没有 00_raw"判断**：运行开始时 00_raw 可能还不存在
    （首次运行、数据还没复制），那样会判成旧布局，交付物写回项目根。
    """
    p = os.path.abspath(base)
    if is_stage_root(p):
        return True
    return any(os.path.isdir(os.path.join(p, d)) for d in (STAGE_DIR, DELIVERY_DIR))


def delivery_item_dir(stage_or_project_root: str, item_id: str) -> str:
    """某件文物的交付目录。

    * 新布局（``stage_or_project_root`` 是 ``阶段成果``）：``<项目根>/交付成果/<编号>``
    * 旧布局（传入的是项目根）：``<项目根>/80_export/<编号>``

    双分支不是"兼容代码能留就留"：旧项目已经在用户机器上跑了几个月，路径一旦
    强制改掉，断点续跑的 ``is_done()`` 全会失效、``90_meta`` 里的历史路径也全部
    指向空处。识别方式是**目录名**而非"文件是否存在"——运行开始时交付目录本来
    就还不存在，用存在性判断会在第一次运行时判错布局。
    """
    base = os.path.abspath(stage_or_project_root)
    if _uses_new_layout(base):
        root = os.path.dirname(base) if is_stage_root(base) else base
        return os.path.join(root, DELIVERY_DIR, item_id)
    return os.path.join(base, LEGACY_EXPORT_DIR, item_id)


def delivery_root_of(stage_or_project_root: str) -> str:
    """交付根目录（放 ``<编号>`` 子目录与 ``report`` 的那一层）。"""
    base = os.path.abspath(stage_or_project_root)
    if _uses_new_layout(base):
        root = os.path.dirname(base) if is_stage_root(base) else base
        return os.path.join(root, DELIVERY_DIR)
    return base


def report_dir(stage_or_project_root: str) -> str:
    """报表目录：新布局 ``<项目根>/交付成果/report``，旧布局 ``<项目根>/report``。"""
    base = os.path.abspath(stage_or_project_root)
    if _uses_new_layout(base):
        root = os.path.dirname(base) if is_stage_root(base) else base
        return os.path.join(root, DELIVERY_DIR, LEGACY_REPORT_DIR)
    return os.path.join(base, LEGACY_REPORT_DIR)


def report_html(stage_or_project_root: str) -> str:
    """``report.html`` 的完整路径（GUI 判断"有没有报表"用）。"""
    return os.path.join(report_dir(stage_or_project_root), "report.html")


def find_report(root: str) -> Optional[str]:
    """在项目根/阶段根下找 report.html，新老布局都试。"""
    for cand in (report_html(root),
                 os.path.join(root, LEGACY_REPORT_DIR, "report.html"),
                 os.path.join(project_root_of(root), LEGACY_REPORT_DIR,
                              "report.html")):
        if os.path.isfile(cand):
            return cand
    return None


def model_candidates(stage_or_project_root: str, item_id: str,
                     exts: tuple[str, ...] = ("obj", "ply", "glb")) -> list[str]:
    """可预览模型候选路径（按 exts 顺序）。

    交付目录优先，其次回退 M3 网格 / S1 稠密点云——任一阶段失败时用户仍能
    看到已经算出来的中间结果（旧实现只看 ``80_export``，失败即面板全空）。
    """
    root = os.path.abspath(stage_or_project_root)
    stage = root if is_stage_root(root) else stage_root(root)
    if not _uses_new_layout(root):
        stage = root
    out: list[str] = []
    ddir = delivery_item_dir(root, item_id)
    for ext in exts:
        out.append(os.path.join(ddir, f"{item_id}.{ext}"))
    for ext in exts:
        out.append(os.path.join(stage, "30_dense", item_id, f"{item_id}.{ext}"))
    out.append(os.path.join(stage, "10_dense", item_id, "fused_dense.ply"))
    out.append(os.path.join(stage, "99_runs", "sfm_tmp", item_id, "dense",
                            "fused.ply"))
    return out


def is_new_layout(project_root: str) -> bool:
    """项目根下是否已经是新布局（存在 ``阶段成果`` 或 ``交付成果``）。"""
    if os.path.basename(os.path.abspath(project_root)) == STAGE_DIR:
        return True
    return any(os.path.isdir(os.path.join(project_root, d))
               for d in (STAGE_DIR, DELIVERY_DIR))


def looks_like_project(project_root: str) -> bool:
    """目录是否**确实**是一个 H3D 项目根（而不是随便一个文件夹）。

    实机事故（2026-10-26）
    ----------------------
    在源码仓库根上误触发了迁移：``output/``（167 个文件的文档产物）被整棵搬进
    ``阶段成果/output``，``git status`` 一片 ``D``，看起来像"测试把 output 删了"。
    根因是旧判据把 ``output`` / ``report`` 这种**通用名**也算作"旧布局痕迹"，
    而任何仓库、任何用户目录都可能有一个叫 ``output`` 的文件夹。

    判据因此收紧成"项目专属痕迹"：``config.yaml``、或任一阶段专属目录
    （``00_raw`` / ``00_photo`` / ``30_dense`` / ``80_export`` …）。
    ``output`` 与 ``report`` 单独出现**不算数**——它们只是迁移的附带命中项，
    不能反过来当作"这是项目"的证据。
    """
    root = os.path.abspath(project_root)
    if os.path.basename(root) == STAGE_DIR:
        root = os.path.dirname(root)
    for name in _PROJECT_MARKERS:
        if os.path.exists(os.path.join(root, name)):
            return True
    return False


def looks_like_h3d_project(project_root: str) -> bool:
    """目录是不是一个 H3D 项目根，**新旧两种布局都认**。

    与 :func:`looks_like_project` 的区别
    ------------------------------------
    ``looks_like_project`` 只认**旧布局**（标记直接躺在项目根），它是
    ``migrate_legacy`` 的闸门，要求严格。1.1.13 起产物已下沉到
    ``<项目>/阶段成果/``，新布局下根目录里只有一个 ``阶段成果``，
    旧判据会判否 —— 于是档案迁移（``h3d.archive.migrate``）不能用它。

    这里在新布局下多探一层：``阶段成果/`` 里任一份阶段专属目录存在即算数。
    **仍然不含** ``output`` 与 ``report`` —— 2026-10-26 的教训不变。
    """
    root = os.path.abspath(project_root)
    if looks_like_project(root):
        return True
    stage = stage_root(root if os.path.basename(root) != STAGE_DIR
                       else os.path.dirname(root))
    if not os.path.isdir(stage):
        return False
    return any(os.path.isdir(os.path.join(stage, d))
               for d in _STAGE_CHILDREN if d != "output")


def legacy_dirs_present(project_root: str) -> list[str]:
    """旧布局下**直接位于项目根**的阶段/交付目录（迁移候选）。"""
    if not os.path.isdir(project_root) or not looks_like_project(project_root):
        return []
    if is_new_layout(project_root) and os.path.isdir(
            os.path.join(project_root, STAGE_DIR)):
        return []
    found = []
    for name in _STAGE_CHILDREN + (LEGACY_EXPORT_DIR, LEGACY_REPORT_DIR):
        if os.path.isdir(os.path.join(project_root, name)):
            found.append(name)
    return found


def migrate_legacy(project_root: str, dry_run: bool = False) -> dict:
    """把旧布局就地迁到新布局（幂等，同名冲突跳过并记录）。

    只做**同卷改名**级别的搬运（``shutil.move`` 在 Windows 同卷即 rename，
    465 MB 的项目也不会有明显耗时）；跨卷时才真复制，此时会慢，属预期。

    返回
    ----
    ``{"moved": [(src, dst), ...], "skipped": [(name, reason), ...],
       "root": 项目根, "dry_run": bool}``
    """
    root = os.path.abspath(project_root)
    moved: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    if os.path.basename(root) == STAGE_DIR:
        root = os.path.dirname(root)

    # 第二道闸门：即使调用方没先问 legacy_dirs_present，也绝不搬"不像项目"的目录
    if not looks_like_project(root):
        return {"moved": [], "skipped": [], "root": root,
                "dry_run": dry_run, "refused": "不像 H3D 项目根（无 config.yaml "
                                                "或阶段目录），拒绝迁移"}

    sroot, droot = stage_root(root), delivery_root(root)

    for name in _STAGE_CHILDREN:
        src = os.path.join(root, name)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(sroot, name)
        if os.path.exists(dst):
            skipped.append((name, f"目标已存在：{dst}"))
            continue
        moved.append((src, dst))
        if not dry_run:
            os.makedirs(sroot, exist_ok=True)
            shutil.move(src, dst)

    # 交付模型：80_export/<编号>/ -> 交付成果/<编号>/
    legacy_export = os.path.join(root, LEGACY_EXPORT_DIR)
    if os.path.isdir(legacy_export):
        if not dry_run:
            os.makedirs(droot, exist_ok=True)
        for entry in sorted(os.listdir(legacy_export)):
            src = os.path.join(legacy_export, entry)
            dst = os.path.join(droot, entry)
            if os.path.exists(dst):
                skipped.append((f"{LEGACY_EXPORT_DIR}/{entry}",
                                f"目标已存在：{dst}"))
                continue
            moved.append((src, dst))
            if not dry_run:
                shutil.move(src, dst)
        if not dry_run:
            try:
                os.rmdir(legacy_export)     # 空了才删得掉；非空说明还有残留
            except OSError:
                skipped.append((LEGACY_EXPORT_DIR, "目录非空，保留"))

    # 报表：report -> 交付成果/report
    legacy_report = os.path.join(root, LEGACY_REPORT_DIR)
    if os.path.isdir(legacy_report):
        dst = os.path.join(droot, LEGACY_REPORT_DIR)
        if os.path.exists(dst):
            skipped.append((LEGACY_REPORT_DIR, f"目标已存在：{dst}"))
        else:
            moved.append((legacy_report, dst))
            if not dry_run:
                os.makedirs(droot, exist_ok=True)
                shutil.move(legacy_report, dst)

    if not dry_run:
        write_layout_readme(root)
    return {"moved": moved, "skipped": skipped, "root": root,
            "dry_run": dry_run}


def _fmt_path(rel: str) -> str:
    return rel.replace(os.sep, "/")


_README = """H3D 输出目录说明（自动生成，可删除）
================================================================

本目录（综合文件夹）下的两个子目录分工如下：

【阶段成果】  阶段性成果，全部由管线生成，可整体重算，不建议手工编辑
  {s}/00_raw/      本次输入：激光点云 / 扫描网格（场景①）
  {s}/00_photo/    本次输入：照片（场景②的几何来源也是它）
  {s}/00_ctrl/     控制点坐标（若有）
  {s}/10_dense/    稠密点云（COLMAP 融合结果）
  {s}/30_dense/    网格模型（泊松重建中间态）
  {s}/50_tex/      纹理烘焙产物（UV 图集、带贴图模型、掩膜）
  {s}/70_report/   质检明细（几何精度）
  {s}/90_meta/     溯源元数据、交付清单
  {s}/99_runs/     COLMAP 工作区与缓存（可随时删除，删了只是下次慢）
  {s}/output/      运行清单 run_manifest.json（断点续跑依据）

【交付成果】  直接可以打开 / 提交的东西
  {d}/report/                单一报表目录：report.html、components.csv、
                             metrics_summary.csv、stage_timings.png 等
  {d}/<文物编号>/             <编号>.ply / .obj / .mtl / .glb
                             <编号>_diffuse.png（纹理图集，2048²）

怎么读
  · 要交模型：进【{d}/<文物编号>/】，.ply 是几何+顶点色，.obj 连 .mtl 与
    _diffuse.png 才是带纹理的完整模型（三者要放在一起，不要单独拷 .obj）。
  · 要写报告/验收：进【{d}/report/】，先看 report.html。
  · 要复算/排查：进【{s}/】，任何阶段目录删掉即触发该阶段重算。

本文件由 h3d.core.paths.write_layout_readme 生成，修改无效。
"""


def write_layout_readme(project_root: str) -> str:
    """在项目根写 ``输出说明.txt``（BOM + CRLF，Windows 记事本直接可读）。"""
    root = os.path.abspath(project_root)
    if os.path.basename(root) == STAGE_DIR:
        root = os.path.dirname(root)
    path = os.path.join(root, "输出说明.txt")
    text = _README.format(s=STAGE_DIR, d=DELIVERY_DIR)
    try:
        os.makedirs(root, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="\r\n") as fh:
            fh.write(text)
    except OSError:
        return ""
    return path


def ensure_layout(project_root: str) -> dict:
    """建好 ``阶段成果`` / ``交付成果`` 两级目录并写说明（幂等）。"""
    root = os.path.abspath(project_root)
    if os.path.basename(root) == STAGE_DIR:
        root = os.path.dirname(root)
    sroot, droot = stage_root(root), delivery_root(root)
    os.makedirs(sroot, exist_ok=True)
    os.makedirs(os.path.join(droot, LEGACY_REPORT_DIR), exist_ok=True)
    readme = write_layout_readme(root)
    return {"root": root, "stage": sroot, "delivery": droot, "readme": readme}


__all__ = [
    "STAGE_DIR", "DELIVERY_DIR", "LEGACY_EXPORT_DIR", "LEGACY_REPORT_DIR",
    "stage_root", "delivery_root", "delivery_root_of", "project_root_of",
    "is_stage_root", "is_new_layout", "delivery_item_dir", "report_dir",
    "report_html", "find_report", "model_candidates", "legacy_dirs_present",
    "looks_like_project", "migrate_legacy", "write_layout_readme",
    "ensure_layout",
]
