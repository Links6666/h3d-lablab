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
        description="造像类文物三维建模自动化系统（路线 E）命令行工具",
    )
    parser.add_argument("--version", action="version", version=f"h3d {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="初始化项目目录结构")
    p_init.add_argument("-p", "--path", default=".", help="项目根目录（默认当前目录）")

    p_validate = sub.add_parser("validate", help="校验配置合法性与数据完整性（不处理）")
    p_validate.add_argument("-c", "--config", required=True, help="配置文件路径")

    p_batch = sub.add_parser("batch", help="批量处理全部造像（无人值守）")
    p_batch.add_argument("-c", "--config", required=True, help="配置文件路径")
    p_batch.add_argument("--dry-run", action="store_true",
                         help="仅发现任务并预览队列，不执行")

    p_report = sub.add_parser("report", help="仅重新生成报表")
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
    from h3d.runner import discover_items

    cfg = load(cfg_path)
    tasks = discover_items(cfg.project.raw_dir, cfg.project.formats)
    n_scans = sum(len(t.raw_paths) for t in tasks)
    print(f"配置合法：项目名 = {cfg.project.name or '(未命名)'}")
    print(f"数据完整性：发现 {len(tasks)} 尊造像，共 {n_scans} 个采集站点文件")
    for t in tasks:
        print(f"  - {t.item_id}: {len(t.raw_paths)} 站")
    return 0


def _cmd_batch(cfg_path: str, dry_run: bool) -> int:
    from h3d.core import load, setup_logging, RunManifest
    from h3d.runner import run_batch, discover_items

    cfg = load(cfg_path)
    setup_logging("INFO")

    if dry_run:
        tasks = discover_items(cfg.project.raw_dir, cfg.project.formats)
        print(f"[dry-run] 预览队列：{len(tasks)} 尊造像")
        for t in tasks:
            print(f"  - {t.item_id}: {len(t.raw_paths)} 站")
        return 0

    result = run_batch(cfg)
    s = result.summary()
    print(f"批量处理完成：total={s['total']} done={s['done']} "
          f"failed={s['failed']} skipped={s['skipped']}")
    print(f"run_manifest -> {s['manifest']}")

    # 批次结束统一出报表（《软著方案》2.7.4）
    if cfg.report.enabled:
        from h3d.report import generate
        manifest = RunManifest.load(cfg.runner.manifest)
        cfg.report.project_name = cfg.project.name or ""
        files = generate(manifest, result.failures, cfg.report)
        for f in files:
            print(f"报表产物 -> {f}")
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


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    from h3d.core import ConfigError

    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            return _cmd_init(args.path)
        if args.command == "validate":
            return _cmd_validate(args.config)
        if args.command == "batch":
            return _cmd_batch(args.config, args.dry_run)
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
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
