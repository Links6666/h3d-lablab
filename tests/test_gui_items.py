"""tests.test_gui_items : GUI 用「文物编号」而非「项目名」拼产物路径。

实机背景（2026-10-18）
----------------------
用户的真实项目：项目名 ``yyyyyyy``，``00_raw`` 下是 ``relic_001BMYyyyy``。
GUI 的预览 / 贴图 / 导出 / 热力图全部用 ``cfg.name``（=项目名）拼路径：

    80_export/yyyyyyy/yyyyyyy.obj     ← 不存在（实际在 80_export/relic_001BMYyyyy/）

后果：M6 明明交付了 obj+mtl+diffuse+glb，界面上仍然是
「未生成可预览的三维模型」，点「导出交付包」弹「未找到交付产物」。

示例项目 ``demo_relic`` 恰好项目名==文物编号，所以自测一直没暴露。
本测试锁住三件事：
1. 反查顺序：manifest → 00_raw 子目录 → 平铺文件 → 空；
2. 多文物时优先选**已交付**那件；
3. 导出包按件组织，多件时不互相覆盖同名文件。
"""
from __future__ import annotations

import json
import os
import zipfile

from h3d.gui.items import (discover_items, is_delivered, items_from_manifest,
                           items_from_raw, primary_item)


def _write_manifest(root: str, items: list[str]) -> None:
    os.makedirs(os.path.join(root, "output"), exist_ok=True)
    stages = {f"{it}/{sid}": {"status": "done"}
              for it in items for sid in ("M1", "M2", "M3")}
    with open(os.path.join(root, "output", "run_manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"stages": stages}, fh)


class TestDiscover:
    def test_manifest_first(self, tmp_path):
        root = str(tmp_path)
        _write_manifest(root, ["relic_A", "relic_B"])
        os.makedirs(os.path.join(root, "00_raw", "relic_C"))
        assert items_from_manifest(root) == ["relic_A", "relic_B"]
        # manifest 优先于目录扫描
        assert discover_items(root) == ["relic_A", "relic_B"]

    def test_raw_subdirs_when_no_manifest(self, tmp_path):
        root = str(tmp_path)
        for it in ("文物001", "文物002"):
            d = os.path.join(root, "00_raw", it)
            os.makedirs(d)
            open(os.path.join(d, "s01.ply"), "w").close()
        os.makedirs(os.path.join(root, "00_raw", "空目录"))
        assert items_from_raw(root) == ["文物001", "文物002"]

    def test_flat_files(self, tmp_path):
        root = str(tmp_path)
        os.makedirs(os.path.join(root, "00_raw"))
        open(os.path.join(root, "00_raw", "s1.ply"), "w").close()
        open(os.path.join(root, "00_raw", "s2.pcd"), "w").close()
        open(os.path.join(root, "00_raw", "readme.txt"), "w").close()
        assert items_from_raw(root) == ["s1", "s2"]

    def test_empty_project_returns_empty(self, tmp_path):
        assert discover_items(str(tmp_path)) == []

    def test_corrupt_manifest_falls_back(self, tmp_path):
        root = str(tmp_path)
        os.makedirs(os.path.join(root, "output"))
        with open(os.path.join(root, "output", "run_manifest.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{ not json")
        d = os.path.join(root, "00_raw", "relic_X")
        os.makedirs(d)
        open(os.path.join(d, "s.ply"), "w").close()
        assert discover_items(root) == ["relic_X"]


class TestPrimary:
    def test_prefers_delivered(self, tmp_path):
        root = str(tmp_path)
        _write_manifest(root, ["relic_A", "relic_B"])
        os.makedirs(os.path.join(root, "80_export", "relic_B"))
        assert primary_item(root, "项目名") == "relic_B"
        assert is_delivered(root, "relic_B")
        assert not is_delivered(root, "relic_A")

    def test_fallback_to_project_name(self, tmp_path):
        """什么都没有时退回项目名（保持历史行为，不抛异常）。"""
        assert primary_item(str(tmp_path), "h3d_project") == "h3d_project"


class TestExportPackage:
    def _mk(self, root: str, item: str, name: str) -> None:
        d = os.path.join(root, "80_export", item)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{name}.obj"), "w", encoding="utf-8") as fh:
            fh.write(f"# {item}")

    def test_single_item_flat_layout(self, tmp_path):
        from h3d.gui.export import export_delivery_package
        root = str(tmp_path)
        self._mk(root, "relic_A", "relic_A")
        out = os.path.join(root, "pkg.zip")
        r = export_delivery_package(root, "relic_A", out)
        assert r and r["items"] == ["relic_A"]
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        assert "交付模型/relic_A.obj" in names

    def test_multi_item_nested_layout(self, tmp_path):
        from h3d.gui.export import export_delivery_package
        root = str(tmp_path)
        self._mk(root, "relic_A", "model")      # 同名 obj
        self._mk(root, "relic_B", "model")
        out = os.path.join(root, "pkg.zip")
        r = export_delivery_package(root, ["relic_A", "relic_B"], out)
        assert r and r["n_files"] == 2 and len(r["items"]) == 2
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
        assert "交付模型/relic_A/model.obj" in names
        assert "交付模型/relic_B/model.obj" in names

    def test_report_only_project_returns_empty(self, tmp_path):
        """只有报表、没有 80_export 时不得产包（否则用户以为模型已导出）。"""
        from h3d.gui.export import export_delivery_package
        root = str(tmp_path)
        os.makedirs(os.path.join(root, "report"))
        open(os.path.join(root, "report", "report.html"), "w").close()
        r = export_delivery_package(root, ["relic_A"], os.path.join(root, "x.zip"))
        assert r == {}

    def test_missing_items_returns_empty(self, tmp_path):
        from h3d.gui.export import export_delivery_package
        root = str(tmp_path)
        r = export_delivery_package(root, ["nope"], os.path.join(root, "x.zip"))
        assert r == {}
        assert not os.path.exists(os.path.join(root, "x.zip"))
