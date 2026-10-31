"""h3d.cli : 命令行入口 h3d init / validate / batch / report。

设计依据：
    《软著方案》2.9 —— CLI 命令表：init / validate / run / batch / report / verify；
    《设计书》31 —— M0 命令与编排：统一入口，解析配置、初始化 manifest、遍历队列。

本模块是 L4 应用层薄壳，只做「参数解析 + 调度分发」，编排内核在
:mod:`h3d.runner`、报表在 :mod:`h3d.report`，二者与 GUI 可共用同一套逻辑
（《设计书》6.4「一致性」）。
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from h3d import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="h3d",
        description="中小型文物三维建模自动化系统（路线 E）命令行工具",
    )
    parser.add_argument("--version", action="version", version=f"h3d {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="初始化项目目录结构")
    p_init.add_argument("-p", "--path", default=".", help="项目根目录（默认当前目录）")

    p_validate = sub.add_parser("validate", help="校验配置合法性与数据完整性（不处理）")
    p_validate.add_argument("-c", "--config", required=True, help="配置文件路径")

    p_batch = sub.add_parser("batch", help="批量处理全部文物（无人值守）")
    p_batch.add_argument("-c", "--config", required=True, help="配置文件路径")
    p_batch.add_argument("--dry-run", action="store_true",
                         help="仅发现任务并预览队列，不执行")
    p_batch.add_argument("--no-preflight", action="store_true",
                         help="跳过开跑前自检（不建议：会把可提前发现的问题留到中途）")
    p_batch.add_argument("--fix", action="store_true",
                         help="自检时自动清理可安全清理的残留（如崩溃留下的陈旧锁）")
    p_batch.add_argument("--force", action="store_true",
                         help="强制重算：忽略断点续跑，全部阶段重跑（S1 深度图工作区"
                              "一并重建）。等价配置 runner.force_rerun=true")

    p_doctor = sub.add_parser(
        "doctor", help="系统自检：环境（boot）+ 项目（project）是否可运行")
    p_doctor.add_argument("-c", "--config", help="配置文件路径（给出则同时做项目自检）")
    p_doctor.add_argument("--fix", action="store_true",
                          help="自动清理可安全清理的残留（陈旧锁）")
    p_doctor.add_argument("--json", action="store_true", help="以 JSON 输出")
    p_doctor.add_argument("--all", action="store_true", help="同时列出正常项")
    p_doctor.add_argument("--deep", action="store_true",
                          help="深度校验：实际启动一次 COLMAP（慢约 1 秒）")

    p_report = sub.add_parser("report", help="仅重新生成报表")
    sub.add_parser("gui", help="启动图形界面（P1 核心版）")
    p_report.add_argument("-c", "--config", required=True, help="配置文件路径")

    p_arch = sub.add_parser(
        "archive", help="文物档案（时相登记 / 存量迁移 / 状态查看）")
    arch_sub = p_arch.add_subparsers(dest="archive_cmd", required=True)
    p_arch_mig = arch_sub.add_parser(
        "migrate", help="把存量项目认成一个时相（只读，不改产物）")
    p_arch_mig.add_argument("-p", "--path", default=".",
                            help="项目根或其「阶段成果」目录（默认当前目录）")
    p_arch_mig.add_argument("--epoch", default="E1", help="时相 id（默认 E1）")
    p_arch_mig.add_argument("--title", default="", help="文物名称（默认取件号）")
    p_arch_mig.add_argument("--new-epoch", action="store_true",
                            help="已有档案时再追加一个时相，而不是返回 already")
    p_arch_status = arch_sub.add_parser("status", help="列出档案中的时相与产物版本")
    p_arch_status.add_argument("-p", "--path", default=".", help="项目根目录")
    p_arch_status.add_argument("--json", action="store_true", help="以 JSON 输出")

    p_arch_inh = arch_sub.add_parser(
        "inherit", help="把复测项目烘到基准项目的图集上（跨时相纹理继承）")
    p_arch_inh.add_argument("--base", required=True,
                            help="基准项目根（图集由它定义）")
    # dest 必须显式写成 source：argparse 默认会用 --from → 属性名 from
    p_arch_inh.add_argument("--from", dest="source", required=True,
                            help="复测项目根（要继承图集的那一件）")
    p_arch_inh.add_argument(
        "--matrix", default="",
        help="显式 4×4 相似变换（JSON 文件，形如 [[...],[...]]），"
             "不给则自动配准（质量闸门拒绝时不会烘图）")
    p_arch_inh.add_argument("--size", type=int, default=0,
                            help="图集边长；默认沿用基准图集记录的尺寸")
    p_arch_inh.add_argument("--json", action="store_true", help="以 JSON 输出")

    p_anom = sub.add_parser(
        "anomaly", help="X3 单期纹理异常检测（单次采集的快速筛查）")
    p_anom.add_argument("-p", "--path", default=".",
                        help="项目根或其「阶段成果」目录（默认当前目录）")
    p_anom.add_argument("--item", default="", help="只跑某一件（默认全部）")
    p_anom.add_argument("--k", type=float, default=0.0,
                        help="robust z 阈值（默认 3.5，越小越敏感）")
    p_anom.add_argument("--no-write", action="store_true",
                        help="只计算不落盘（不写 60_qc/<件>/anomaly）")
    p_anom.add_argument("--json", action="store_true", help="以 JSON 输出")

    p_track = sub.add_parser(
        "anomaly-track",
        help="X4 病害时序追踪（两期图斑关联 + 五态判定）")
    p_track.add_argument("-p", "--path", default=".",
                         help="项目根或其「阶段成果」目录（默认当前目录）")
    p_track.add_argument("--item", required=True, help="件号（掩膜从这里取）")
    p_track.add_argument("--a", required=True, help="第一期扩散图集路径")
    p_track.add_argument("--b", required=True, help="第二期扩散图集路径")
    p_track.add_argument("--delta-e", type=float, default=0.0,
                         help="可见性门限（CIE76，默认 5.0）")
    p_track.add_argument("--no-write", action="store_true",
                         help="只计算不落盘（不写 60_qc/<件>/temporal）")
    p_track.add_argument("--json", action="store_true", help="以 JSON 输出")

    p_arch_refs = arch_sub.add_parser(
        "refs", help="体检资产里的图集引用（查无实体的可置空修复）")
    p_arch_refs.add_argument("-p", "--path", default=".", help="项目根目录")
    p_arch_refs.add_argument(
        "--fix", action="store_true",
        help="把「查无实体」的引用置空（原值留在 params.ref_repair）")
    p_arch_refs.add_argument(
        "--dry-run", action="store_true", help="只列出将要修复的条目，不写文件")
    p_arch_refs.add_argument("--json", action="store_true", help="以 JSON 输出")

    return parser


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------
def _cmd_init(path: str) -> int:
    import os

    from h3d.core import paths

    # 新布局（v1.1.10）：一个综合文件夹 + 两棵子树。阶段骨架不变，只是整体
    # 下沉到 阶段成果/；交付模型与报表进 交付成果/。写说明文件是因为用户
    # 打开项目第一眼只能看到两个目录名，得有个地方解释"模型在哪"。
    lay = paths.ensure_layout(path)
    stage_dirs = ["00_raw", "00_photo", "00_ctrl", "10_clean", "20_registered",
                  "30_dense", "50_tex", "60_qc", "70_report", "90_meta",
                  "99_runs", "output"]
    for d in stage_dirs:
        os.makedirs(os.path.join(lay["stage"], d), exist_ok=True)
    print(f"已初始化项目目录结构：{os.path.abspath(path)}")
    print(f"  阶段成果：{lay['stage']}")
    print(f"  交付成果：{lay['delivery']}（含 report/）")
    return 0


def _cmd_validate(cfg_path: str) -> int:
    from h3d.core import load
    from h3d.runner.batch_processor import discover_from_config

    cfg = load(cfg_path)
    tasks = discover_from_config(cfg)
    n_scans = sum(len(t.raw_paths) for t in tasks)
    print(f"配置合法：项目名 = {cfg.project.name or '(未命名)'}")
    print(f"数据完整性：发现 {len(tasks)} 件文物，共 {n_scans} 个采集站点文件")
    for t in tasks:
        print(f"  - {t.item_id}: {len(t.raw_paths)} 站 / "
              f"{len(t.photo_paths)} 张照片")
    if not tasks:
        # 0 件不是"校验通过"：用户拿 validate 当 CI 门禁时，rc=0 会让空项目
        # （目录写错 / 扩展名不符 / 只剩系统垃圾目录）蒙混过关。
        print("校验失败：未发现任何文物。请检查 project.raw_dir 是否存在、"
              "子目录名是否为文物编号、文件扩展名是否在 project.formats 内；"
              "纯照片项目还需开启 sfm_solve.enabled。", file=sys.stderr)
        return 1
    return 0


def _preflight_or_stop(cfg, *, fix: bool, no_preflight: bool) -> bool:
    """开跑前自检（项目作用域）。返回 ``True`` 表示可以继续。

    这是 ``batch`` 的**第一道工序**：先自检、再抢数据根锁。顺序不能反 ——
    ``acquire_run_lock`` 会写下自己持有的锁，之后自检就会看到「该根已被
    PID <自己> 占用」而误判（自检把自己报告成抢占者，看起来像假故障）。
    """
    if no_preflight:
        print("[自检] 已按 --no-preflight 跳过（问题会在运行中途暴露）")
        return True
    from h3d.health import format_report, run_preflight

    rep = run_preflight("project", cfg=cfg, fix=fix)
    print(rep.summary_line())
    if not rep.ok:
        print(format_report(rep), file=sys.stderr)
        print("\n已中止：请先处理上述阻断项；确认无碍可加 --no-preflight 强制运行。",
              file=sys.stderr)
        return False
    text = format_report(rep, only_problems=True)
    if text:
        print(text)
    return True


def _cmd_batch(cfg_path: str, dry_run: bool, no_preflight: bool = False,
               fix: bool = False, force: bool = False) -> int:
    from h3d.core import load, setup_logging, RunManifest
    from h3d.runner import run_batch
    from h3d.runner.batch_processor import discover_from_config

    cfg = load(cfg_path)
    setup_logging("INFO")
    if force:
        # 命令行开关优先于配置文件：`--force` 明确表达「这次要重算」，
        # 不必为了跑一次去改 yaml 再改回来（改配置文件最容易忘改回）。
        cfg.runner.force_rerun = True
        print("[force] 强制重算已开启：忽略断点续跑，全部阶段重跑")

    if not _preflight_or_stop(cfg, fix=fix, no_preflight=no_preflight):
        return 1

    if dry_run:
        # 与实跑共用同一发现入口（含纯照片模式开关）
        tasks = discover_from_config(cfg)
        print(f"[dry-run] 预览队列：{len(tasks)} 件文物")
        for t in tasks:
            print(f"  - {t.item_id}: {len(t.raw_paths)} 站")
        return 0

    result = run_batch(cfg)
    s = result.summary()
    print(f"批量处理完成：total={s['total']} done={s['done']} "
          f"failed={s['failed']} skipped={s['skipped']}")
    print(f"run_manifest -> {s['manifest']}")

    # 批次结束统一出报表（《软著方案》2.7.4）
    # 实机修复：报表只是交付附属物，其失败绝不能让整条批量流程报错退出
    #（否则 GUI 会弹出「Unhandled exception in script」崩溃框）。
    if cfg.report.enabled:
        from h3d.core import get_logger
        from h3d.report import generate
        try:
            manifest = RunManifest.load(cfg.runner.manifest)
            cfg.report.project_name = cfg.project.name or ""
            files = generate(manifest, result.failures, cfg.report)
            for f in files:
                print(f"报表产物 -> {f}")
        except Exception as exc:  # noqa: BLE001 - 报表异常降级为警告
            get_logger("cli").warning("报表生成失败（不影响重建结果）：%s", exc)
            print(f"[警告] 报表生成失败（不影响重建结果）：{exc}")
    return 0


def _cmd_report(cfg_path: str) -> int:
    from h3d.core import load, RunManifest
    from h3d.report import generate

    cfg = load(cfg_path)
    manifest = RunManifest.load(cfg.runner.manifest)
    cfg.report.project_name = cfg.project.name or ""
    files = generate(manifest, failures=None, report_cfg=cfg.report)
    for f in files:
        print(f"报表产物 -> {f}")
    return 0


def _doctor_deep():
    """深度校验：实际启动一次 COLMAP（约 1.2 s，不在启动关键路径上）。"""
    import tempfile
    import time

    from h3d.core.proc import run_external
    from h3d.health import CheckResult, HealthReport

    t0 = time.time()
    try:
        from h3d.health._boot import _find_colmap
        path = _find_colmap()
    except Exception:  # noqa: BLE001 - 深度校验失败不影响主结论
        path = None
    if not path:
        res = CheckResult("colmap_deep", "COLMAP 启动校验", "info",
                          "未找到 COLMAP，跳过深度校验")
    else:
        try:
            out = run_external([path, "-h"], cwd=tempfile.gettempdir(),
                               timeout=8.0)
            lines = ((out.stdout or "") + (out.stderr or "")).strip().splitlines()
            head = lines[0][:120] if lines else ""
            good = out.returncode in (0, 1) and bool(head)
            res = CheckResult(
                "colmap_deep", "COLMAP 启动校验",
                "ok" if good else "warning",
                head or f"返回码 {out.returncode} 且无输出",
                "" if good else
                "COLMAP 启动失败通常是缺 CUDA 运行时或 VC++ 运行库；\n"
                "在命令行手动执行该路径可看到完整报错")
        except Exception as exc:  # noqa: BLE001
            res = CheckResult("colmap_deep", "COLMAP 启动校验", "warning",
                              f"启动失败：{type(exc).__name__}: {exc}")
    res.elapsed_ms = int((time.time() - t0) * 1000)
    return HealthReport("deep", [res], res.elapsed_ms)


def _cmd_doctor(cfg_path: Optional[str], fix: bool, as_json: bool,
                show_all: bool, deep: bool) -> int:
    """``h3d doctor``：环境自检（boot）+ 可选项目自检（project）。

    退出码：0 = 无阻断项；1 = 存在阻断项（脚本可直接据此判断能否开跑）。
    """
    import json as _json

    from h3d.health import format_report, run_preflight

    reports = [run_preflight("boot", fix=fix, force=True)]
    if cfg_path:
        from h3d.core import load
        cfg = load(cfg_path)
        reports.append(run_preflight("project", cfg=cfg, fix=fix, force=True))
    if deep:
        reports.append(_doctor_deep())

    if as_json:
        print(_json.dumps({r.scope: r.to_dict() for r in reports},
                          ensure_ascii=False, indent=2))
    else:
        for rep in reports:
            print(rep.summary_line())
            text = format_report(rep, only_problems=not show_all)
            if text:
                print(text)
            print()
    return 0 if all(r.ok for r in reports) else 1


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def _cmd_archive_inherit(args) -> int:
    """``h3d archive inherit``：把复测项目烘到基准图集上（B1）。

    只做一件事：调编排层并把结果翻译成退出码。所有判定（有没有图集、
    配准是否可信）都在 :func:`inherit_epoch` 里，命令行不重复实现 ——
    两处判据必然漂移，而这类漂移的后果是「命令行说成功、GUI 说失败」。
    """
    import json as _json

    from h3d.archive.inherit_epoch import inherit_epoch

    transform = None
    if args.matrix:
        try:
            with open(args.matrix, encoding="utf-8-sig") as fh:
                data = _json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"变换矩阵读取失败：{exc}", file=sys.stderr)
            return 1
        if isinstance(data, dict):
            data = data.get("matrix", data)
        transform = data

    res = inherit_epoch(args.base, args.source, transform=transform,
                        size=args.size or None)
    if args.json:
        print(_json.dumps({
            "ok": res.ok, "state": res.state, "message": res.message,
            "texture_path": res.texture_path, "atlas_version": res.atlas_version,
            "atlas_ref": res.atlas_ref, "base_epoch": res.base_epoch,
            "source_epoch": res.source_epoch,
            "metrics": {k: v for k, v in res.metrics.items()
                        if isinstance(v, (int, float, str, bool))},
        }, ensure_ascii=False, indent=2))
    else:
        print(res.message) if res.ok else print(res.message, file=sys.stderr)
    if res.ok and not args.json:
        print(f"  纹理：{res.texture_path}")
        print(f"  图集：{res.atlas_ref}（基准期 {res.base_epoch}）")
    return 0 if res.ok else 1


#: 体检状态的显示名。状态码本身给脚本用，中文名给人看 —— 两边都要有，
#: 只输出 ``dangling`` 这种词的话，用户不会知道它意味着「这条引用指向空气」。
_REF_STATE_LABEL = {
    "ok": "合格",
    "empty": "无引用",
    "external": "跨项目",
    "unverified": "时间倒挂",
    "dangling": "查无实体",
}


def _cmd_archive_refs(args) -> int:
    """``h3d archive refs``：体检资产里的图集引用，可选修复。

    背景：B1 切片 7 之前 ``migrate`` 与 runner 都往 ``uv_atlas_ref`` 里写死
    ``"atlas-v1"``，而档案里没有那条记录。改掉写入口只对新建档生效，存量档案
    里那批假引用还在——所以需要一次显式体检 + 修复。

    退出码当门用：**还剩「查无实体」就是 1**（没修或修不掉），全绿才 0。
    时间倒挂（``unverified``）不算失败：实体现在确实在，只是证明不了当时也在。
    """
    import json as _json

    from h3d.archive import Archive
    from h3d.archive.audit import audit_atlas_refs, repair_atlas_refs

    try:
        arch = Archive.open(args.path)
    except FileNotFoundError:
        print(f"该目录还没有文物档案：{args.path}\n"
              f"  建档：h3d archive migrate <项目目录>", file=sys.stderr)
        return 1

    report = repair_atlas_refs(arch, dry_run=bool(args.dry_run or not args.fix))
    fixed = report.repaired if (args.fix and not args.dry_run) else []
    # 修完之后退出码必须看**盘上现在**还剩几条，而不是修复前的清单：
    # 否则「刚修好」也会报 rc=1，脚本以为失败了。
    findings = audit_atlas_refs(arch) if fixed else report.findings
    dangling = [f for f in findings if f.state == "dangling"]

    if args.json:
        print(_json.dumps({
            "artifact": arch.artifact().to_dict(),
            "findings": [{"epoch_id": f.epoch_id, "version": f.version,
                          "ref": f.ref, "state": f.state, "detail": f.detail}
                         for f in report.findings],
            "repaired": [] if not fixed else
                        [{"epoch_id": f.epoch_id, "version": f.version,
                          "dropped": f.ref} for f in fixed],
            "dry_run": bool(args.dry_run or not args.fix),
        }, ensure_ascii=False, indent=2))
        return 0 if not dangling else 1

    art = arch.artifact()
    print(f"文物：{art.artifact_id}  {art.title}")
    if not report.findings:
        print("  （还没有产物可体检）")
    counts: dict[str, int] = {}
    for f in report.findings:
        counts[f.state] = counts.get(f.state, 0) + 1
        label = _REF_STATE_LABEL.get(f.state, f.state)
        print(f"  {label:<6} {f.epoch_id} v{f.version}  {f.ref or '—':<18} "
              f"{f.detail}")

    if fixed:
        print(f"\n已置空 {len(fixed)} 条引用的图集字段"
              f"（原值留在 params.ref_repair，可追溯）：")
        for f in fixed:
            print(f"  {f.epoch_id} v{f.version}  {f.ref}")
    elif dangling and args.fix:
        print(f"\n--dry-run：以上 {len(dangling)} 条**将**被置空，本次未写文件。")
    elif dangling:
        print(f"\n{len(dangling)} 条引用查无实体。加 --fix 置空"
              f"（不可逆，先用 --dry-run 看清单）：")
        for f in dangling:
            print(f"  {f.epoch_id} v{f.version}  {f.ref}")

    summary = "  ".join(f"{_REF_STATE_LABEL.get(k, k)} {v}"
                        for k, v in sorted(counts.items()))
    tail = f"（已置空 {len(fixed)}）" if fixed else ""
    print(f"\n体检 {len(report.findings)} 条：{summary}{tail}")
    return 0 if not dangling else 1


def _cmd_anomaly_track(args) -> int:
    """``h3d anomaly-track``：两期图斑关联与五态判定（X4）。

    只做编排：解析路径、调一层、把结论翻成退出码。判据全在
    :func:`h3d.anomaly.temporal.track` 里。

    退出码：0 = 正常；1 = 输入问题（缺图集/缺掩膜/布局不一致）。
    「有变化」本身**不是**错误——保护工作的常态就是有变化。
    """
    import json as _json
    import os as _os

    from h3d.anomaly.temporal import STATE_LABEL, TemporalConfig
    from h3d.anomaly.atlas_io import track_item
    from h3d.core import paths

    project = paths.project_root_of(_os.path.abspath(args.path))
    stage = paths.stage_root(project)
    tcfg = TemporalConfig()
    if args.delta_e > 0:
        tcfg = TemporalConfig(delta_e_min=args.delta_e)
    try:
        summary = track_item(stage, args.item, _os.path.abspath(args.a),
                             _os.path.abspath(args.b), temporal_config=tcfg,
                             write=not args.no_write)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[{args.item}] 无法比对：{exc}", file=sys.stderr)
        return 1

    if args.json:
        print(_json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    m = summary["metrics"]
    print(f"病害时序追踪  {args.item}")
    print(f"  第一期 {summary['epoch_a']['regions']} 处"
          f"（异常 {summary['epoch_a']['anomalous_ratio'] * 100:.2f}%）"
          f"  第二期 {summary['epoch_b']['regions']} 处"
          f"（异常 {summary['epoch_b']['anomalous_ratio'] * 100:.2f}%）")
    print(f"  布局 IoU {m['layout_iou']:.6f}  配对 {m['matched']} 对  "
          f"ΔE 门限挡下 {m['filtered_by_delta_e']} 条")
    print("  " + "  ".join(f"{STATE_LABEL[s]} {m[s]}"
                           for s in ("appeared", "grown", "stable",
                                     "shrank", "disappeared")))
    if summary["changes"]:
        print(f"  {'状态':6s} {'纹素A':>6s} {'纹素B':>6s} {'面积比':>7s} "
              f"{'中位ΔE':>7s}  边界框")
        for row in summary["changes"]:
            ratio = ("—" if row["area_ratio"] is None
                     else f"{row['area_ratio']:.2f}")
            box = (row["x0_b"], row["y0_b"], row["x1_b"], row["y1_b"]) \
                if row["x0_b"] is not None else \
                (row["x0_a"], row["y0_a"], row["x1_a"], row["y1_a"])
            print(f"  {row['label']:6s} {row['texels_a']:6d} {row['texels_b']:6d} "
                  f"{ratio:>7s} {row['delta_e']:7.2f}  {box}")
        print(f"  产物：{summary['output_dir']}/（changes.csv / summary.json）")
    else:
        print("  两期没有可比对的变化。")
    print("  口径：五态是「两期之间变没变」，不是病害类别；ΔE 门限挡下的条目"
          "不计入（它们落在「看不清」的灰区）。")
    return 0


def _cmd_anomaly(args) -> int:
    """``h3d anomaly``：对单期 UV 图集做无监督异常筛查（X3）。

    只做编排：找输入、逐件调用、把结论翻译成退出码。判据全在
    :func:`h3d.anomaly.detect_atlas_anomalies` 里，命令行不重复实现。
    """
    import json as _json

    from h3d.anomaly import AnomalyConfig
    from h3d.core import paths
    from h3d.anomaly.atlas_io import find_items, run_item

    import os as _os

    project = paths.project_root_of(_os.path.abspath(args.path))
    stage = paths.stage_root(project)
    items = [args.item] if args.item else find_items(stage)
    if not items:
        print(f"{stage}/50_tex 下没有同时具备图集与掩膜的件。\n"
              f"  先跑 M4 照片纹理（产物：<件>_L1.0_diffuse.png + "
              f"<件>_L1.0_mask.png），再回来做异常筛查。", file=sys.stderr)
        return 1

    cfg = AnomalyConfig(k=args.k) if args.k > 0 else AnomalyConfig()
    results, errors = [], []
    for item in items:
        try:
            results.append(run_item(stage, item, config=cfg,
                                    write=not args.no_write))
        except (FileNotFoundError, ValueError) as exc:
            errors.append({"item_id": item, "error": str(exc)})
            print(f"[{item}] 跳过：{exc}", file=sys.stderr)

    if args.json:
        print(_json.dumps({"items": results, "errors": errors},
                          ensure_ascii=False, indent=2))
    else:
        if results:
            scales = "/".join(str(int(x)) for x in cfg.scales)
            print(f"单期纹理异常筛查（k={cfg.k}，背景窗口 {scales} 纹素）")
        for r in results:
            m = r["metrics"]
            print(f"  {r['item_id']}  {r['size'][0]}²  "
                  f"岛内 {m['island_ratio'] * 100:.1f}%  "
                  f"异常 {m['anomalous_ratio'] * 100:.2f}%  "
                  f"图斑 {m['n_regions']} 处"
                  + (f"  最大 {m['largest_ratio'] * 100:.2f}%"
                     if m.get("largest_ratio") else ""))
            if r.get("output_dir"):
                print(f"      产物：{r['output_dir']}/"
                      f"（heat.png / mask.png / regions.csv / metrics.json）")
            elif args.no_write:
                print("      （--no-write：未落盘）")
    if errors and not results:
        return 1
    return 0 if not errors else 1


def _cmd_archive(args) -> int:
    """文物档案：迁移（存量项目 → 时相）与状态查看。

    档案层是「重新采集」与「重新计算」解耦的载体（ADR-0001），所以这里的
    migrate 只**新建** ``archive/``，绝不改写 ``阶段成果`` 里的任何字节。
    """
    import json as _json

    from h3d.archive import Archive
    from h3d.archive.migrate import migrate

    if args.archive_cmd == "migrate":
        try:
            res = migrate(args.path, epoch_id=args.epoch, title=args.title,
                          force_new_epoch=args.new_epoch)
        except ValueError as exc:
            # 迁移闸门（非项目目录 / 无可登记产物）属于用户输入问题，
            # 给可读信息 + rc=1，不打印 traceback。
            print(f"迁移失败：{exc}", file=sys.stderr)
            return 1
        if res["status"] == "already":
            print(f"档案已存在，未变更（基准期 {res['epoch_id']}）。"
                  f"如需追加时相，加 --new-epoch。")
        else:
            print(f"已建档：{res['artifact_id']} / {res['epoch_id']} "
                  f"v{res['version']}  内容键 {res['content_key'][:16]}…")
            if res["missing"]:
                print(f"  缺失产物：{'、'.join(res['missing'])}（报表会显示 N/A）")
        return 0

    if args.archive_cmd == "inherit":
        return _cmd_archive_inherit(args)

    if args.archive_cmd == "refs":
        return _cmd_archive_refs(args)

    # status
    try:
        arch = Archive.open(args.path)
    except FileNotFoundError:
        print(f"该目录还没有文物档案：{args.path}\n"
              f"  建档：h3d archive migrate <项目目录>"
              f"（GUI 打开项目时会自动建档）", file=sys.stderr)
        return 1
    art = arch.artifact()
    rows = []
    for ep in arch.epochs():
        assets = arch.assets(ep.epoch_id)
        rows.append({
            "epoch_id": ep.epoch_id, "route": ep.route,
            "baseline": ep.is_baseline, "captured_on": ep.captured_on,
            "qc": ep.qc_status, "versions": [a.version for a in assets],
            "content_keys": [a.content_key[:12] for a in assets],
        })
    if args.json:
        print(_json.dumps({"artifact": art.to_dict(), "epochs": rows},
                          ensure_ascii=False, indent=2))
        return 0
    print(f"文物：{art.artifact_id}  {art.title}")
    if not rows:
        print("  （还没有时相）")
    for r in rows:
        tag = "基准期" if r["baseline"] else "复测期"
        print(f"  {r['epoch_id']}  路线{r['route']}  {tag}  "
              f"{r['captured_on']}  QC={r['qc']}  "
              f"产物版本 {r['versions'] or '无'}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    from h3d.core import ConfigError, H3DError, get_logger

    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            return _cmd_init(args.path)
        if args.command == "validate":
            return _cmd_validate(args.config)
        if args.command == "batch":
            return _cmd_batch(args.config, args.dry_run,
                              no_preflight=getattr(args, "no_preflight", False),
                              fix=getattr(args, "fix", False),
                              force=getattr(args, "force", False))
        if args.command == "doctor":
            return _cmd_doctor(args.config, fix=args.fix, as_json=args.json,
                               show_all=args.all, deep=args.deep)
        if args.command == "report":
            return _cmd_report(args.config)
        if args.command == "archive":
            return _cmd_archive(args)
        if args.command == "anomaly":
            return _cmd_anomaly(args)
        if args.command == "anomaly-track":
            return _cmd_anomaly_track(args)
        parser.print_help()
        return 2
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)  # 异常 str 已含 [E001] 前缀
        return 1
    except FileNotFoundError as exc:
        print(f"[E100] 文件缺失：{exc}", file=sys.stderr)
        return 1
    except H3DError as exc:
        # 业务异常统一兜底：打印可读信息 + 完整栈入日志，避免打包版弹出
        # PyInstaller「Unhandled exception in script」崩溃框（实机 E601）。
        import traceback
        get_logger("cli").error("命令 %s 失败：%s\n%s", args.command, exc,
                                traceback.format_exc())
        print(f"命令失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())


# ---------------------------------------------------------------------------
# GUI 入口（P1 核心版）：python -m h3d.cli gui
# ---------------------------------------------------------------------------
def _cmd_gui(args) -> int:
    from h3d.gui.app import run_gui
    return run_gui()
