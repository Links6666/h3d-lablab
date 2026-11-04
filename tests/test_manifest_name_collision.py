"""两个不同 schema 不许共用 `delivery_manifest.json` 这个名字。

- `runner.manifest`（默认 `output/run_manifest.json`）= **阶段账本**，键 `stages`
- M6 的交付清单（`90_meta/delivery_manifest.json`）= **交付摘要**，键 `deliveries`

2026-11-04 真机撞上：`configs/delivery_check*.yaml` 把 `runner.manifest` 指到了
`delivery_manifest.json`，于是 **M6 每跑一次就用交付摘要覆盖阶段账本**，账本里
30 条阶段记录变成 0 条 → 断点续跑全废、`h3d verify` 报「没有任何阶段记录」。

两条修法都要测：
1. 核验优先读阶段账本（`run_manifest.json`），别被 `deliveries` 摘要骗了
2. M6 发现输出路径与 `runner.manifest` 相同 → **拒绝覆盖**
"""
import json
import os
import sys

TESTS = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(TESTS, "..", "src"))
sys.path.insert(0, TESTS)

from _delivery_fixture import ACC_HEADERS, build_project, write  # noqa: E402

import h3d.delivery_verify as vd  # noqa: E402


def _stages_manifest(items):
    st = {}
    for it in items:
        st[f"{it}/M4"] = {"status": "done", "outputs": [], "uv_baked": True,
                          "colored": True, "paint_mode": "photo",
                          "coverage_ratio": 0.5865}
        st[f"{it}/M5"] = {"status": "done", "outputs": [], "coverage": 0.6148,
                          "rmse_mm": 87.1479, "rmse_rel": 0.012845}
        st[f"{it}/M6"] = {"status": "done", "outputs": [],
                          "formats_requested": ["ply"], "formats_missing": [],
                          "texture_orphan": False}
    return {"stages": st}


class TestVerifyPrefersTheStageLedger:
    def test_delivery_summary_does_not_shadow_the_ledger(self, tmp_path):
        """`delivery_manifest.json` 只有 `deliveries` 时，要用 `run_manifest.json`。"""
        root = build_project(tmp_path, m6_done=["a"], delivered=["a"])
        meta = os.path.join(root, "90_meta")
        # 真账本
        write(os.path.join(meta, "run_manifest.json"),
              json.dumps(_stages_manifest(["a"]), ensure_ascii=False))
        # M6 的交付摘要（没有 stages）
        write(os.path.join(meta, "delivery_manifest.json"),
              json.dumps({"deliveries": [{"item_id": "a"}], "n_items": 1}))

        rep = vd.verify_report(root)

        assert "没有任何阶段记录" not in "\n".join(rep["fail"]), rep["fail"]
        assert rep["ok"], rep["fail"]


class TestM6RefusesToClobberTheLedger:
    def test_refuses_when_paths_are_identical(self):
        """M6 的输出路径 == `runner.manifest` 时不许写（那是账本，不是摘要）。"""
        from h3d.runner import _delivery_manifest_path

        same = os.path.join("/tmp", "proj", "90_meta", "delivery_manifest.json")
        got = _delivery_manifest_path(same, same)
        assert got is None, f"应当拒绝写入，却返回了 {got}"

    def test_writes_normally_when_paths_differ(self):
        from h3d.runner import _delivery_manifest_path

        ledger = os.path.join("/tmp", "proj", "output", "run_manifest.json")
        out = os.path.join("/tmp", "proj", "90_meta", "delivery_manifest.json")
        assert _delivery_manifest_path(out, ledger) == out


class TestPicksTheMostCompleteLedger:
    """候选账本有多份时，选「最了解这个项目」的那份。

    盲信文件名两头都错：`delivery_manifest.json` 可能是 M6 的摘要（没 stages），
    `run_manifest.json` 可能是探针留下的残缺账本（真机 relic_001 只有 6 条，
    M4 覆盖率还是 0.0010 的陈年值——照着它核验会把正确产物判成对不上）。
    """

    def _ledger(self, meta_dir, name, items_and_outputs):
        st = {}
        for it, outs in items_and_outputs:
            st[f"{it}/M4"] = {"status": "done", "outputs": outs,
                              "uv_baked": True, "colored": True,
                              "paint_mode": "photo", "coverage_ratio": 0.5865}
            st[f"{it}/M5"] = {"status": "done", "outputs": [], "coverage": 0.6148,
                              "rmse_mm": 87.1479, "rmse_rel": 0.012845}
            st[f"{it}/M6"] = {"status": "done", "outputs": [],
                              "formats_requested": ["ply"], "formats_missing": [],
                              "texture_orphan": False}
        write(os.path.join(meta_dir, name),
              json.dumps({"stages": st}, ensure_ascii=False))

    def test_more_complete_ledger_wins(self, tmp_path):
        root = build_project(tmp_path, m6_done=["a"], delivered=["a"])
        meta = os.path.join(root, "90_meta")
        real = os.path.join(root, "80_export", "a", "a.ply")
        # 残缺的（1 件、无产物）与完整的（3 件、产物在）
        self._ledger(meta, "run_manifest.json", [("a", [])])
        self._ledger(meta, "delivery_manifest.json",
                     [("a", [real]), ("b", [real]), ("c", [real])])

        path, man = vd.load_manifest(root)

        assert os.path.basename(path) == "delivery_manifest.json", path
        assert len(man["stages"]) == 9, len(man["stages"])


class TestPicksTheMostCompleteLedger:
    """候选账本有多份时，选「最了解这个项目」的那份。

    盲信文件名两头都错：`delivery_manifest.json` 可能是 M6 的摘要（没 stages），
    `run_manifest.json` 可能是探针留下的残缺账本（真机 relic_001 只有 6 条，
    M4 覆盖率还是 0.0010 的陈年值——照着它核验会把正确产物判成对不上）。
    """

    def _ledger(self, meta_dir, name, items_and_outputs):
        st = {}
        for it, outs in items_and_outputs:
            st[f"{it}/M4"] = {"status": "done", "outputs": outs,
                              "uv_baked": True, "colored": True,
                              "paint_mode": "photo", "coverage_ratio": 0.5865}
            st[f"{it}/M5"] = {"status": "done", "outputs": [], "coverage": 0.6148,
                              "rmse_mm": 87.1479, "rmse_rel": 0.012845}
            st[f"{it}/M6"] = {"status": "done", "outputs": [],
                              "formats_requested": ["ply"], "formats_missing": [],
                              "texture_orphan": False}
        write(os.path.join(meta_dir, name),
              json.dumps({"stages": st}, ensure_ascii=False))

    def test_more_complete_ledger_wins(self, tmp_path):
        root = build_project(tmp_path, m6_done=["a"], delivered=["a"])
        meta = os.path.join(root, "90_meta")
        real = os.path.join(root, "80_export", "a", "a.ply")
        # 残缺的（1 件、无产物）与完整的（3 件、产物在）
        self._ledger(meta, "run_manifest.json", [("a", [])])
        self._ledger(meta, "delivery_manifest.json",
                     [("a", [real]), ("b", [real]), ("c", [real])])

        path, man = vd.load_manifest(root)

        assert os.path.basename(path) == "delivery_manifest.json", path
        assert len(man["stages"]) == 9, len(man["stages"])
