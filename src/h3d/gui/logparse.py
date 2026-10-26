"""h3d.gui.logparse : CLI 输出日志解析（纯 Python，无 Qt 依赖）。

解析 h3d.cli batch 的标准输出：
  ▶ S1 开始：relic_001                   -> 阶段 running
  [INFO] [h3d.runner] M3 完成：...       -> 阶段 done
  [INFO] [h3d.sfm_solve] S1 COLMAP[1/3]  -> 子步骤（供进度条显示）
  失败隔离：item=.. stage=M5 ..          -> 阶段 failed
  批量处理完成：total=1 done=1 ..        -> 完成汇总

设计说明（2026-10-17）
----------------------
原实现只认「完成」行，阶段灯永远停在"等待"直到突然变绿，稠密重建这种
单步 50 分钟的阶段在界面上**完全没有活动迹象**，用户只能判断为"卡死"。
本次补齐三类信息：阶段开始、子步骤编号、COLMAP 逐视图进度。
"""

from __future__ import annotations

import re

#: 阶段显示顺序（含 P1）。
#: 2026-10-18：原先漏了 P1（内核 STAGE_REGISTRY 里有 S1/P1/X1/X2 四个
#: 可选阶段），P1 一旦执行，界面既没有灯、正则也不解析，
#: 而 _on_stage 会因 self.lamps["P1"] KeyError（Qt 吞掉异常）→
#: 状态行与灯全部停在上一阶段，看着像卡死。
STAGE_ORDER = ["S1", "M1", "M2", "M3", "M4", "P1", "M5", "M6",
               "X1", "X2"]

_STAGE_DONE = re.compile(
    r"\[(INFO|WARNING|ERROR)\].*\[h3d\.(?:runner|sfm_solve|quality|"
    r"photo_texture|dense_gate)\] (S1|M[1-6]|P1|X1|X2) (完成|质检报告)")
_STAGE_START = re.compile(r"▶\s*(S1|M[1-6]|P1|X1|X2)\s*开始")
#: 阶段开始行携带文物编号：``▶ S1 开始：relic_001BMYyyyy``
_STAGE_START_ITEM = re.compile(r"▶\s*(?:S1|M[1-6]|P1|X1|X2)\s*开始[：:]\s*(\S+)")
_STAGE_FAIL = re.compile(r"失败隔离：item=\S+ stage=(S1|M[1-6]|P1|X1|X2)")
_DONE_TOTAL = re.compile(r"批量处理完成：total=(\d+) done=(\d+) failed=(\d+)")
#: 同行的 skipped 字段（1.1.5 起才有；缺失时按 total-done-failed 兜底）
_DONE_SKIPPED = re.compile(r"skipped=(\d+)")
#: 真跳过：阶段已打「▶ 开始」但因条件不满足而未执行——
#: ``○ X1 跳过：relic_001（缺参考期 00_ref/.../*.ply）``
#: 2026-10-18：内核此前只在 debug 级留痕，界面等不到完成行 → 该灯橙色卡死、
#: 进度条少一格。现在跳过必须是**可见状态**（灰灯 + 计入进度）。
_STAGE_SKIP = re.compile(r"○\s*(S1|M[1-6]|P1|X1|X2)\s*跳过")
#: 阶段完成/跳过行携带文物编号：``S1 完成：relic_666（...）`` / ``○ M1 跳过：relic_666（...）``
_STAGE_ITEM = re.compile(r"(?:完成|跳过)[：:]\s*([^\s（(]+)")
#: 任务发现总数：``任务发现完成：5 件文物（其中纯照片 5 件）``
_ITEMS_TOTAL = re.compile(r"任务发现完成[：:]\s*(\d+)\s*件")

# 子步骤：S1 COLMAP[2/3] / S1 dense[2/3]
_SUBSTEP = re.compile(r"\[h3d\.sfm_solve\]\s*(S1)\s+(COLMAP|dense)\[(\d+)/(\d+)\]")
# COLMAP 逐视图进度：已被 sfm_solve._ProgressLogger 清洗成 "深度图 12/30：xxx.JPG"
# 「几何校验」是 patch_match_stereo 的第二遍（geom_consistency），必须与第一遍
# 区分，否则界面会从 30/30 退回 1/30（实机 2026-10-17）。
_CN_PROG = re.compile(r"(深度图|几何校验|去畸变|融合|特征提取) (\d+)/(\d+)")
_VIEW_PROG = re.compile(r"Processing view (\d+)\s*/\s*(\d+)", re.IGNORECASE)

_STEP_NAMES = {
    1: "特征提取", 2: "特征匹配", 3: "稀疏重建",
}


def stage_done_of(line: str) -> str | None:
    """返回行中完成的阶段 id；无则 None。"""
    m = _STAGE_DONE.search(line)
    return m.group(2) if m else None


def stage_start_of(line: str) -> str | None:
    """返回行中开始的阶段 id（阶段灯置 running）。"""
    m = _STAGE_START.search(line)
    return m.group(1) if m else None


def item_of(line: str) -> str | None:
    """返回阶段开始行里的文物编号（如 ``relic_001BMYyyyy``）；无则 None。

    用途（2026-10-17 实机）：进度页要统计「当前文物的深度图产物」，
    原实现遍历 ``99_runs/sfm_tmp`` 下**所有**文物并取最大值，续跑或多文物
    时会把上一次运行残留的深度图算进来 —— 实测 ``relic_666`` 残留 30 张，
    导致界面在稀疏阶段就一直显示「深度图 30/30」，用户判断为卡死。
    """
    m = _STAGE_START_ITEM.search(line)
    return m.group(1) if m else None


def stage_skip_of(line: str) -> str | None:
    """返回被跳过的阶段 id（灰灯）；无则 None。"""
    m = _STAGE_SKIP.search(line)
    return m.group(1) if m else None


def stage_failed_of(line: str) -> str | None:
    m = _STAGE_FAIL.search(line)
    return m.group(1) if m else None


def done_summary(line: str) -> tuple | None:
    """返回 (total, done, failed)；非完成行 None。

    仅作**向后兼容**保留（历史调用方与测试按三元组取值）。需要 ``skipped``
    的新代码请用 :func:`batch_summary` —— 断点续跑整批跳过时 ``done=0``，
    只看三元组必然把「已完成」误判成「失败」。
    """
    m = _DONE_TOTAL.search(line)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def batch_summary(line: str) -> dict | None:
    """解析「批量处理完成」行为字典；非完成行 None。

    返回 ``{"total","done","failed","skipped"}``。

    为什么必须带 skipped（2026-10-25 实机）
    ----------------------------------------
    用户跑完一件文物后**又点了一次**「一键生成交付物」：断点续跑让 7 个阶段
    全部跳过，末行是 ``total=1 done=0 failed=0 skipped=1``。GUI 原判据是
    ``failed == 0 and done > 0`` —— done=0 直接判成失败，于是**一次完全正确的
    幂等重跑**弹出了「运行未完成」警告、进度条从 100% 归零、状态灯变红。
    「跳过」和「失败」是两种截然不同的终态：跳过意味着产物齐备、无需重算。
    """
    m = _DONE_TOTAL.search(line)
    if not m:
        return None
    total, done, failed = (int(m.group(i)) for i in (1, 2, 3))
    # skipped 是 1.1.5 起追加的字段；老日志没有时用 total-done-failed 兜底
    m2 = _DONE_SKIPPED.search(line)
    skipped = int(m2.group(1)) if m2 else max(0, total - done - failed)
    return {"total": total, "done": done, "failed": failed,
            "skipped": skipped}


def substep_of(line: str) -> tuple[str, str] | None:
    """返回 (阶段, 子步骤描述)；用于状态栏显示"正在做什么"。

    例：``S1 COLMAP[2/3]`` -> ``("S1", "COLMAP 2/3 特征匹配")``
        ``S1 dense[2/3]``  -> ``("S1", "稠密 stereo 2/3")``
        ``Processing view 12 / 30`` -> ``("S1", "深度图 12/30")``
    """
    m = _SUBSTEP.search(line)
    if m:
        sid, kind, cur, total = m.group(1), m.group(2), m.group(3), m.group(4)
        if kind == "COLMAP":
            name = _STEP_NAMES.get(int(cur), "COLMAP")
            return sid, f"COLMAP {cur}/{total} {name}"
        label = {1: "去畸变", 2: "深度图批量计算", 3: "点云融合"}.get(
            int(cur), "稠密")
        return sid, f"稠密 {cur}/{total} {label}"
    m = _CN_PROG.search(line)
    if m:
        return "S1", f"{m.group(1)} {m.group(2)}/{m.group(3)}"
    m = _VIEW_PROG.search(line)
    if m:
        return "S1", f"深度图 {m.group(1)}/{m.group(2)}"
    return None


#: S1 内部子步骤在「S1 这一段」里的归一化进度（0~1）。
#: S1 自身只占总进度的 1/len(STAGE_ORDER)，但耗时可能是全部阶段的 80%
#: （30 张 2400px 实测：特征 1.6min + 匹配 5min + 稀疏 13min + 稠密 25min）。
#: 若只在 S1 完成时把进度条从 0 跳到 11%，用户看到的就是「50 分钟不动」。
_S1_COLMAP = {1: 0.05, 2: 0.18, 3: 0.30}
_S1_DENSE = {1: 0.36, 2: 0.42, 3: 0.92}


def s1_fraction(line: str) -> float | None:
    """把一行 S1 输出换算成「S1 内部完成比例」（0~1）；非 S1 行返回 None。

    与 :func:`substep_of` 共用同一批正则，保证两者判据一致。
    """
    m = _SUBSTEP.search(line)
    if m:
        kind, cur = m.group(2), int(m.group(3))
        table = _S1_COLMAP if kind == "COLMAP" else _S1_DENSE
        return table.get(cur)
    m = _CN_PROG.search(line)
    if m:
        name, cur, total = m.group(1), int(m.group(2)), int(m.group(3))
        if not total:
            return None
        f = cur / total
        if name == "特征提取":
            return 0.05 + 0.13 * f
        if name == "去畸变":
            return 0.36 + 0.06 * f
        if name == "深度图":
            # 第一遍 photometric（约一半稠密耗时）
            return 0.42 + 0.23 * f
        if name == "几何校验":
            # 第二遍 geom_consistency（另一半耗时），与第一遍不重叠
            return 0.65 + 0.25 * f
        if name == "融合":
            return 0.92 + 0.07 * f
    return None


def stage_item_of(line: str) -> str | None:
    """返回「完成 / 跳过」行里的文物编号；无则 None。

    进度必须按「件 × 阶段」累计（见 :class:`ProgressModel`），而完成/跳过行
    是唯一同时带文物编号与阶段的事件来源。
    """
    m = _STAGE_ITEM.search(line)
    return m.group(1) if m else None


def items_total_of(line: str) -> int | None:
    """返回本批文物总数（``任务发现完成：N 件文物``）；无则 None。"""
    m = _ITEMS_TOTAL.search(line)
    return int(m.group(1)) if m else None


#: 每件都会跑的**核心**阶段（P1/X1/X2 是可选项，本项目默认不启用）
CORE_STAGES = ["S1", "M1", "M2", "M3", "M4", "M5", "M6"]

#: 单件内部的阶段权重（按实测耗时标定，和为 1）。
#: 30 张 4496×3000 照片：S1 ≈ 30 min（其中稠密占 76%）、M4 ≈ 90 s、其余秒级。
W_PHOTO = {"S1": 0.900, "M1": 0.020, "M2": 0.003, "M3": 0.007,
           "M4": 0.060, "M5": 0.005, "M6": 0.005}
#: S1 被跳过时（纯扫描件场景①）按剩余阶段重新归一 —— 否则一件没有 S1 的
#: 文物会在开跑瞬间拿到 90%，随后 M1~M4 的真实工作只推动 10%。
W_SCAN = {"M1": 0.20, "M2": 0.03, "M3": 0.07, "M4": 0.60,
          "M5": 0.05, "M6": 0.05}


class ProgressModel:
    """整批进度：按「件 × 阶段」加权累计。

    历史缺陷（2026-10-24 实机）
    ---------------------------
    原实现是 ``len(stages_done) / len(STAGE_ORDER)``：

    * ``stages_done`` 是按**阶段 id** 去重的集合，没有件这个维度 —— 第 1 件
      跑完 7 个阶段就集满 7 个 id，后面 4 件无论跑多久都不会再加一个；
    * ``STAGE_ORDER`` 有 10 个阶段，含 P1/X1/X2 —— 本项目从未启用，
      于是硬上限就是 7/10 = **70%**。

    实测：5 件批处理在 18:39:55 就跳到 70%，之后 40 分钟一动不动，
    用户据此判断卡死并取消。现在改成：每件独立计分（S1 占 90% 权重，
    其内部子进度线性映射进来），整批 = 各件平均，上限 99（100 由
    「批量处理完成」给出）。
    """

    def __init__(self, n_items: int = 0) -> None:
        self.n_items = int(n_items or 0)
        self.items: dict[str, dict] = {}
        self.cur = ""

    # -- 内部 ---------------------------------------------------------
    def _slot(self, item: str) -> dict:
        st = self.items.get(item)
        if st is None:
            st = {"done": set(), "stage": "", "frac": 0.0, "no_s1": False}
            self.items[item] = st
        return st

    def _weights(self, st: dict) -> dict:
        return W_SCAN if st["no_s1"] else W_PHOTO

    def on_stage(self, item: str, stage: str, status: str) -> None:
        """status: start / done / skip。"""
        st = self._slot(item)
        if stage == "S1" and status == "skip":
            st["no_s1"] = True
        if status == "start":
            st["stage"], st["frac"] = stage, 0.0
            return
        st["done"].add(stage)
        if st["stage"] == stage:      # 该阶段结束，不再计入 in-stage 分量
            st["stage"], st["frac"] = "", 0.0

    def on_in_stage(self, item: str, stage: str, frac: float) -> None:
        """阶段内部进度（S1 的稠密/稀疏子步骤）。"""
        st = self._slot(item)
        st["stage"] = stage
        st["frac"] = max(0.0, min(1.0, float(frac)))

    # -- 对外 ---------------------------------------------------------
    def item_fraction(self, item: str) -> float:
        st = self.items.get(item)
        if st is None:
            return 0.0
        w = self._weights(st)
        tot = sum(w.values())
        if tot <= 0:
            return 0.0
        acc = sum(w.get(s, 0.0) for s in st["done"])
        if st["stage"] and st["stage"] not in st["done"]:
            acc += w.get(st["stage"], 0.0) * st["frac"]
        return min(1.0, acc / tot)

    def value(self) -> int:
        n = self.n_items or max(1, len(self.items))
        done = sum(self.item_fraction(i) for i in self.items)
        return max(0, min(99, int(done / n * 100)))

    def feed(self, line: str) -> int | None:
        """吃一行日志；有进度事件时返回新的整批百分比，否则 None。"""
        touched = False
        n = items_total_of(line)
        if n:
            self.n_items = n
            touched = True
        it = item_of(line)
        if it:
            self.cur = it
            self._slot(it)
            touched = True
        sid = stage_start_of(line)
        if sid:
            self.on_stage(self.cur or "?", sid, "start")
            touched = True
        frac = s1_fraction(line)
        if frac is not None:
            self.on_in_stage(self.cur or "?", "S1", frac)
            touched = True
        sid = stage_done_of(line)
        if sid:
            self.on_stage(stage_item_of(line) or self.cur or "?", sid, "done")
            touched = True
        sk = stage_skip_of(line)
        if sk:
            self.on_stage(stage_item_of(line) or self.cur or "?", sk, "skip")
            touched = True
        return self.value() if touched else None


__all__ = ["STAGE_ORDER", "CORE_STAGES", "W_PHOTO", "W_SCAN", "ProgressModel",
           "stage_done_of", "stage_start_of", "stage_failed_of", "stage_item_of",
           "items_total_of", "done_summary", "batch_summary", "substep_of",
           "item_of", "s1_fraction"]
