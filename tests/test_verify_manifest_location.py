"""核验器要在 ``output/`` 里找到账本，不能只认 ``90_meta``。

2026-11-04 端到端实测（打包版 `H3D.exe --batch`，3 件全绿）：
跑完立刻 `h3d verify` → **FAIL 找不到 manifest**。

账本其实在 ``阶段成果/output/run_manifest.json``——那是 ``RunnerConfig.manifest``
的**默认值**，GUI 一键生成也写这里；``90_meta`` 只放 M6 的交付摘要与元数据快照。
于是「验收工具自己先不可靠」：一个刚跑完、交付包齐全的项目被判成坏的。

这类缺陷比它想检查的那些更难发现：**它只在项目"健康"的时候报错**——
有问题的项目反而会因为别的原因先失败，掩盖掉这一条。
"""
from __future__ import annotations

import json
import os

import pytest


def _ledger(n_stages=3, outputs=True) -> dict:
    stages = {}
    for i in range(n_stages):
        stages[f"r1/M{i + 1}"] = {
            "status": "done",
            "outputs": [f"out_{i}.ply"] if outputs else [],
            "metrics": {"coverage": 0.6},
        }
    return {"config_sha256": "x", "stages": stages}


class TestLedgerLocation:
    def _project(self, tmp_path):
        root = tmp_path / "proj"
        (root / "阶段成果" / "output").mkdir(parents=True)
        (root / "阶段成果" / "90_meta").mkdir(parents=True)
        (root / "交付成果" / "report").mkdir(parents=True)
        return root

    def test_manifest_in_output_dir_is_found(self, tmp_path):
        from h3d.delivery_verify import load_manifest

        root = self._project(tmp_path)
        p = root / "阶段成果" / "output" / "run_manifest.json"
        p.write_text(json.dumps(_ledger(4)), encoding="utf-8")
        path, man = load_manifest(str(root))
        assert path is not None, "账本在 output/ 下却找不到——正常项目会被判成坏"
        assert len(man["stages"]) == 4

    def test_old_layout_output_dir_is_found(self, tmp_path):
        """旧布局：root 就是阶段根，账本在 <root>/output。"""
        from h3d.delivery_verify import load_manifest

        root = tmp_path / "old"
        (root / "output").mkdir(parents=True)
        (root / "output" / "run_manifest.json").write_text(
            json.dumps(_ledger(2)), encoding="utf-8")
        path, man = load_manifest(str(root))
        assert path is not None and len(man["stages"]) == 2

    def test_most_complete_still_wins_across_locations(self, tmp_path):
        """位置变多了，但**挑最完整**的判据不变（不能退化成"按目录顺序取第一个"）。"""
        from h3d.delivery_verify import load_manifest

        root = self._project(tmp_path)
        (root / "阶段成果" / "90_meta" / "run_manifest.json").write_text(
            json.dumps(_ledger(1)), encoding="utf-8")
        (root / "阶段成果" / "output" / "run_manifest.json").write_text(
            json.dumps(_ledger(6)), encoding="utf-8")
        path, man = load_manifest(str(root))
        assert len(man["stages"]) == 6
        assert "output" in path

    def test_verify_does_not_fail_on_a_freshly_run_project(self, tmp_path):
        """端到端的返回口径：不能再出现「找不到 manifest」这一条 FAIL。"""
        from h3d.delivery_verify import verify_report

        root = self._project(tmp_path)
        # 产物路径留空：本用例只查「账本找得到吗」，别的 FAIL（产物缺失、
        # 交付为空）是夹具本来就该有的，不能混进来当判据。
        (root / "阶段成果" / "output" / "run_manifest.json").write_text(
            json.dumps(_ledger(2, outputs=False)), encoding="utf-8")
        rep = verify_report(str(root))
        assert not any("manifest" in name for name in rep["fail"]), rep["fail"]
