"""真机探针：``runner.data_source`` 在真数据上是否真的改变了口径。

为什么不能只靠单元测试
----------------------
单测里的 ``raw_paths=["dummy.ply"]`` 是造的，``metric_units`` 翻转只看布尔。
真机上要回答的是另外两件事：

1. 换声明之后，**报表里的那个数字**从「3.xx mm」变成什么（口径真的退了毫米门？）
2. 默认值（auto）下，同一份数据跑出来的数字**有没有变**（老项目重跑不能漂）

做法：把 ``output/_build/batch3`` 的 M3/M4 产物复制到临时目录，只换
``runner.data_source`` 调 ``_stage_quality``，然后读回 ``70_report/accuracy.csv``。

用法：
    .venv_lab\\Scripts\\python.exe scripts\\probe_data_source.py
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

SRC = os.path.join(os.path.dirname(__file__), "..", "output", "_build", "batch3",
                   "阶段成果")
ITEM = "ping_a"


def _stage_copy(dst: str) -> None:
    for sub in ("20_registered", "30_dense", "50_tex"):
        s = os.path.join(SRC, sub)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(dst, sub))


def _run(root: str, declared: str) -> dict:
    from h3d.core import PipelineConfig
    from h3d.runner import _stage_quality
    from h3d.runner.task import StageResult, Task, TaskStatus

    cfg = PipelineConfig()
    cfg.project.raw_dir = os.path.join(root, "00_raw")
    cfg.project.formats = ["ply"]
    cfg.report.enabled = False
    cfg.runner.manifest = os.path.join(root, "90_meta", "manifest.json")
    cfg.runner.data_source = declared
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)

    reg = os.path.join(root, "20_registered", ITEM)
    ref = os.path.join(reg, f"{ITEM}_s01.ply")
    if not os.path.isfile(ref):
        cands = [f for f in os.listdir(reg) if f.endswith(".ply")] if os.path.isdir(reg) else []
        ref = os.path.join(reg, cands[0]) if cands else ""
    task = Task(item_id=ITEM, root=root, raw_paths=["x.ply"])
    task.results["M2"] = StageResult("M2", TaskStatus.DONE, [ref], {})

    err = ""
    try:
        metrics = _stage_quality(task, cfg).metrics
    except Exception as exc:  # noqa: BLE001 - 判门失败也要读回落盘的报告
        err = type(exc).__name__
        metrics = json.load(open(os.path.join(
            root, "60_qc", ITEM, "qc_metrics.json"), encoding="utf-8"))
    rows = list(csv.DictReader(open(os.path.join(
        root, "70_report", ITEM, "accuracy.csv"), encoding="utf-8-sig")))
    return {"metrics": metrics, "csv": rows[0] if rows else {}, "err": err}


def main() -> int:
    if not os.path.isdir(SRC):
        print(f"缺少真机数据目录：{SRC}")
        return 2
    tmp = tempfile.mkdtemp(prefix="h3d_ds_")
    try:
        _stage_copy(tmp)
        out = {}
        for declared in ("auto", "photogrammetry", "laser"):
            r = _run(tmp, declared)
            m, c = r["metrics"], r["csv"]
            out[declared] = r
            print(f"[{declared:15s}] metric_units={m.get('metric_units')!s:5s} "
                  f"reason={m.get('metric_units_reason') or '-':38s} "
                  f"中误差={c.get('中误差(mm)') or '-':>10s} "
                  f"口径={c.get('中误差口径') or '-':22s} "
                  f"冲突={m.get('declared_source_conflict')} "
                  f"{('判门失败:' + r['err']) if r['err'] else ''}")
        # 对照组：auto 与 laser 在「无冲突证据」时应给出**逐字相同**的数字
        a, l = out["auto"]["csv"], out["laser"]["csv"]
        same = all(a.get(k) == l.get(k) for k in
                   ("中误差(mm)", "覆盖率", "边界边比例"))
        print(f"\nauto vs laser 数值逐字一致：{same}")
        pc = out["photogrammetry"]["csv"]
        print(f"photogrammetry 的中误差列：{pc.get('中误差(mm)')!r} "
              f"（应为 N/A —— 不主张米制）")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
