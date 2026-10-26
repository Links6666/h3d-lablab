"""h3d.gui.timing : 阶段耗时统计（运行中实时累计 + 结束后从 manifest 校准）。

需求（2026-10-26 实机反馈）
--------------------------
"总时间有了，但每个阶段各花了多久看不到"——30 分钟的运行里，用户只能看到
一根总进度条；结束之后想复盘"是 S1 稠密贵还是 M4 贴图贵"，只能去翻报表。

两条数据来源，互补使用：

1. **运行中**：GUI 侧看到 ``▶ S1 开始`` / ``✔ S1 完成`` 就累计墙钟时长。
   优点是即时；缺点只统计"看得见的那段"，且与子进程内计时有小误差。
2. **结束后**：读 ``run_manifest.json`` 的 ``elapsed_s``（每件每阶段都记，
   由批处理进程自己计时）。这是**权威值**，多件批处理时还能按阶段求和。

于是 :func:`stage_times_from_manifest` 在运行结束时覆盖实时值——用户看到的
最终数字与报表「耗时」列同源，不会出现"界面说 18 分钟、报表说 21 分钟"。
"""

from __future__ import annotations

import json
import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGridLayout, QLabel, QSizePolicy, QVBoxLayout,
                               QWidget)

from h3d.gui.widgets import ElidedLabel

#: 阶段名的展示顺序（与 gui.runner.STAGE_ORDER 一致，此处不 import 以免循环）
DEFAULT_STAGES = ("S1", "M1", "M2", "M3", "M4", "P1", "M5", "M6", "X1", "X2")

_STATUS_COLOR = {
    "waiting": ("#3a4150", "#9aa4b2"),
    "running": ("#8a5a00", "#ffd08a"),
    "done": ("#1e4d2b", "#9be8a8"),
    "failed": ("#5c1f1a", "#ffb3ab"),
    "skipped": ("#2a2f36", "#8d949e"),
}


def fmt_duration(seconds: float) -> str:
    """秒 → ``12:34`` / ``1:02:03``（不足 1 秒显示 ``00:00``）。"""
    try:
        s = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        return "—"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def stage_times_from_manifest(manifest_path: str) -> dict[str, object]:
    """从 run_manifest 汇总逐阶段耗时。

    返回 ``{"stages": {sid: 秒}, "total": 秒, "n_items": n, "path": 路径}``；
    文件不存在或损坏时返回 ``{"stages": {}, "total": 0.0, "n_items": 0}``
    （GUI 只拿它做展示，**不得因读不到而报错**）。

    口径：同一阶段在多件文物上各跑一次，这里**求和**（用户问的是"这个阶段一共
    花了多久"）；``total`` 同样求和，因此会略大于"墙钟总时长"（多件串行时两者
    相等，只有并发 worker 时 total 才是累计 CPU 时间）。界面上把「总时长」与
    「各阶段和」分开标注，避免用户拿两个数硬对。
    """
    out: dict[str, object] = {"stages": {}, "total": 0.0, "n_items": 0,
                              "path": manifest_path or ""}
    if not manifest_path or not os.path.isfile(manifest_path):
        return out
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return out
    stages = data.get("stages") or {}
    acc: dict[str, float] = {}
    items: set[str] = set()
    for key, entry in stages.items():
        if not isinstance(entry, dict):
            continue
        sid = key.rsplit("/", 1)[-1]
        if "/" in key:
            items.add(key.split("/", 1)[0])
        try:
            sec = float(entry.get("elapsed_s") or 0.0)
        except (TypeError, ValueError):
            sec = 0.0
        acc[sid] = acc.get(sid, 0.0) + sec
    out["stages"] = acc
    out["total"] = sum(acc.values())
    out["n_items"] = len(items)
    return out


class StageTimingStrip(QWidget):
    """阶段耗时条：每阶段一枚「代号 + 耗时」标签，常驻显示。

    设计取舍
    --------
    * **不做进度条**：阶段耗时不是百分比，条形图会让人误读为"还差多少"。
    * **运行中与结束后都保留**：结束后清零就失去了"刚才哪一步慢"的信息，
      而用户看报表前多半先看界面。
    * 每枚标签宽度由文字自然决定（``S1 12:34``），最长 ``X2 1:02:03``，不会
      随运行变化 → 不会引起窗口跳变。
    """

    def __init__(self, stages=DEFAULT_STAGES, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 2)
        v.setSpacing(2)

        head = ElidedLabel("阶段耗时", min_chars=6)
        head.setStyleSheet("QLabel{color:#7d8795;font-size:11px;}")
        v.addWidget(head)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(3)
        self._chips: dict[str, QLabel] = {}
        self._secs: dict[str, float] = {}
        self._status: dict[str, str] = {}
        per_row = 5
        for i, sid in enumerate(stages):
            lb = QLabel(f"{sid} —")
            lb.setAlignment(Qt.AlignCenter)
            lb.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            lb.setMinimumWidth(64)
            self._chips[sid] = lb
            self._secs[sid] = 0.0
            self._status[sid] = "waiting"
            self._paint(sid)
            grid.addWidget(lb, i // per_row, i % per_row)
        for col in range(per_row):
            grid.setColumnStretch(col, 1)
        v.addLayout(grid)

        self._footer = ElidedLabel("", min_chars=18)
        self._footer.setStyleSheet("QLabel{color:#9aa4b2;font-size:11px;}")
        v.addWidget(self._footer)

    # ---------------------------------------------------------- 内部
    def _paint(self, sid: str) -> None:
        chip = self._chips.get(sid)
        if chip is None:
            return
        bg, fg = _STATUS_COLOR.get(self._status.get(sid, "waiting"),
                                   _STATUS_COLOR["waiting"])
        sec = self._secs.get(sid, 0.0)
        text = f"{sid} {fmt_duration(sec)}" if sec > 0 else f"{sid} —"
        chip.setText(text)
        chip.setToolTip(f"{sid}：{fmt_duration(sec)}" if sec > 0
                        else f"{sid}：尚未运行")
        chip.setStyleSheet(
            f"QLabel{{background:{bg};color:{fg};border-radius:4px;"
            f"padding:2px 4px;font-size:11px;font-weight:bold;}}")

    # ---------------------------------------------------------- API
    def set_status(self, sid: str, status: str) -> None:
        if sid not in self._chips:
            return
        self._status[sid] = status
        self._paint(sid)

    def set_seconds(self, sid: str, seconds: float) -> None:
        if sid not in self._chips:
            return
        try:
            self._secs[sid] = max(0.0, float(seconds))
        except (TypeError, ValueError):
            return
        self._paint(sid)

    def add_seconds(self, sid: str, seconds: float) -> None:
        self.set_seconds(sid, self._secs.get(sid, 0.0) + float(seconds))

    def seconds(self, sid: str) -> float:
        return self._secs.get(sid, 0.0)

    def all_seconds(self) -> dict[str, float]:
        return dict(self._secs)

    def set_footer(self, text: str) -> None:
        self._footer.setText(text)

    def apply_manifest(self, manifest_path: str, wall_seconds: Optional[float] = None) -> bool:
        """用 manifest 的权威值覆盖实时值。

        返回 ``True`` 表示确实读到并覆盖了（调用方据此决定是否打日志）。
        """
        info = stage_times_from_manifest(manifest_path)
        stages = dict(info["stages"] or {})
        if not stages:
            return False
        for sid, sec in stages.items():
            if sid in self._chips:
                self._secs[sid] = float(sec)
                if self._status.get(sid) in (None, "waiting") and sec > 0:
                    self._status[sid] = "done"
                self._paint(sid)
        bits = [f"各阶段合计 {fmt_duration(info['total'])}"]
        if info.get("n_items"):
            bits.append(f"{info['n_items']} 件")
        if wall_seconds:
            bits.append(f"总时长 {fmt_duration(wall_seconds)}")
        bits.append("（耗时取自 run_manifest，与报表同源）")
        self._footer.setText(" · ".join(bits))
        return True

    def reset(self) -> None:
        for sid in self._chips:
            self._secs[sid] = 0.0
            self._status[sid] = "waiting"
            self._paint(sid)
        self._footer.setText("")


__all__ = ["DEFAULT_STAGES", "fmt_duration", "stage_times_from_manifest",
           "StageTimingStrip"]
