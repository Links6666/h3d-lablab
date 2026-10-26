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

    return parser


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------
def _cmd_init(path: str) -> int:
    import os

    dirs = ["00_raw", "10_clean", "20_registered", "30_dense", "50_tex",
            "60_qc", "70_report", "80_export", "90_meta", "99_runs", "output"]
    for d in dirs:
        os.makedirs(os.path.join(path, d), exist_ok=True)
    print(f"已初始化项目目录结构：{os.path.abspath(path)}")
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
               fix: bool = False) -> int:
    from h3d.core import load, setup_logging, RunManifest
    from h3d.runner import run_batch
    from h3d.runner.batch_processor import discover_from_config

    cfg = load(cfg_path)
    setup_logging("INFO")

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
                              fix=getattr(args, "fix", False))
        if args.command == "doctor":
            return _cmd_doctor(args.config, fix=args.fix, as_json=args.json,
                               show_all=args.all, deep=args.deep)
        if args.command == "report":
            return _cmd_report(args.config)
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
