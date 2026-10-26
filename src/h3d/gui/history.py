"""h3d.gui.history : 历史项目记录（~/.workbuddy/h3d_history.json）。

存储 {path, name, scene, last_opened}，支持最近列表 + 快速打开。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime

_HISTORY_PATH = os.path.join(os.path.expanduser("~"), ".workbuddy",
                             "h3d_history.json")


class HistoryStore:
    def __init__(self, path: str | None = None):
        self.path = path or _HISTORY_PATH
        self.items: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.items = data.get("projects", [])
        except Exception:  # noqa: BLE001
            self.items = []

    def add(self, root_dir: str, name: str, scene: str) -> None:
        """记录/更新项目（置顶去重）。"""
        entry = {"path": root_dir, "name": name, "scene": scene,
                 "last_opened": datetime.now().strftime(
                     "%Y-%m-%d %H:%M:%S")}
        self.items = [e for e in self.items if e.get("path") != root_dir]
        self.items.insert(0, entry)
        self.items = self.items[:12]
        self._save()

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"projects": self.items}, fh,
                      ensure_ascii=False, indent=2)

    def list(self) -> list[dict]:
        return list(self.items)

    def remove(self, root_dir: str) -> None:
        self.items = [e for e in self.items if e.get("path") != root_dir]
        self._save()

    @staticmethod
    def find_config(root_dir: str) -> str | None:
        """在项目根找 config.yaml（打开历史项目用）。"""
        for cand in (os.path.join(root_dir, "config.yaml"),
                     os.path.join(root_dir, "90_meta", "config.yaml")):
            if os.path.exists(cand):
                return cand
        return None


# 2026-10-18：__all__ 里原来写 HISTORY_PATH（无下划线），而模块只有
# _HISTORY_PATH —— `from h3d.gui.history import *` 会直接 AttributeError。
__all__ = ["HistoryStore", "_HISTORY_PATH"]
