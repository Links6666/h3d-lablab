"""tests.test_paths_layout : 综合文件夹 + 两棵子树的路径解析与旧布局迁移。

这一层的 bug 都属于「静默错位」型：路径解析错了不会报错，只会把产物写到
别处、或者让 GUI 找不到模型。所以断言要**钉住具体路径**，不能只断言
"返回了非空字符串"。
"""
from __future__ import annotations

import os

import pytest


class TestDirNames:
    def test_stage_and_delivery_names(self):
        from h3d.core import paths
        assert paths.STAGE_DIR == "阶段成果"
        assert paths.DELIVERY_DIR == "交付成果"

    def test_stage_root_and_delivery_root(self, tmp_path):
        from h3d.core import paths
        root = str(tmp_path)
        assert paths.stage_root(root) == os.path.join(root, "阶段成果")
        assert paths.delivery_root(root) == os.path.join(root, "交付成果")

    def test_project_root_of_roundtrip(self, tmp_path):
        from h3d.core import paths
        root = str(tmp_path)
        for probe in (root, paths.stage_root(root),
                      os.path.join(paths.stage_root(root), "00_raw")):
            assert paths.project_root_of(probe) == os.path.abspath(root)


class TestDeliveryResolution:
    def test_new_layout_item_dir(self, tmp_path):
        from h3d.core import paths
        lay = paths.ensure_layout(str(tmp_path))
        assert paths.delivery_item_dir(lay["stage"], "relic_1") == \
            os.path.join(str(tmp_path), "交付成果", "relic_1")
        # 传项目根也要解析到同一处（GUI 手里是项目根，阶段代码手里是 stage）
        assert paths.delivery_item_dir(str(tmp_path), "relic_1") == \
            os.path.join(str(tmp_path), "交付成果", "relic_1")

    def test_legacy_layout_item_dir(self, tmp_path):
        """没有任何新布局标记时按旧布局解析（老项目不能被改名）。"""
        from h3d.core import paths
        assert paths.delivery_item_dir(str(tmp_path), "r1") == \
            os.path.join(str(tmp_path), "80_export", "r1")

    def test_legacy_becomes_new_once_layout_created(self, tmp_path):
        """一旦 ensure_layout 建了目录，同一路径必须切到新布局。

        这条是回归位：早期实现只看"是不是叫阶段成果"，于是 GUI 传项目根时
        永远走旧分支，交付物被写回 ``80_export``。
        """
        from h3d.core import paths
        root = str(tmp_path)
        assert paths.delivery_item_dir(root, "r1").endswith(
            os.path.join("80_export", "r1"))
        paths.ensure_layout(root)
        assert paths.delivery_item_dir(root, "r1") == \
            os.path.join(root, "交付成果", "r1")

    def test_report_dir_new_and_legacy(self, tmp_path):
        from h3d.core import paths
        root = str(tmp_path)
        assert paths.report_dir(root) == os.path.join(root, "report")
        paths.ensure_layout(root)
        assert paths.report_dir(root) == os.path.join(root, "交付成果", "report")
        assert paths.report_html(root).endswith("report.html")

    def test_find_report_prefers_new_layout(self, tmp_path):
        from h3d.core import paths
        root = str(tmp_path)
        lay = paths.ensure_layout(root)
        rpt = os.path.join(lay["delivery"], "report")
        os.makedirs(rpt, exist_ok=True)
        open(os.path.join(rpt, "report.html"), "w", encoding="utf-8").write("x")
        assert paths.find_report(root) == os.path.join(rpt, "report.html")

    def test_find_report_none_when_absent(self, tmp_path):
        from h3d.core import paths
        assert paths.find_report(str(tmp_path)) is None


class TestModelCandidates:
    def test_order_and_legacy_fallback(self, tmp_path):
        from h3d.core import paths
        root = str(tmp_path)
        cands = paths.model_candidates(root, "r1")
        assert cands[0].endswith(os.path.join("80_export", "r1", "r1.obj"))
        # 回退链必须包含 M3 网格与 S1 稠密点云（任一阶段失败也能预览）
        assert any(os.path.join("30_dense", "r1") in c for c in cands)
        assert any("fused_dense.ply" in c for c in cands)

    def test_new_layout_first(self, tmp_path):
        from h3d.core import paths
        root = str(tmp_path)
        paths.ensure_layout(root)
        cands = paths.model_candidates(root, "r1")
        assert cands[0] == os.path.join(root, "交付成果", "r1", "r1.obj")


class TestEnsureLayout:
    def test_dirs_and_readme(self, tmp_path):
        from h3d.core import paths
        lay = paths.ensure_layout(str(tmp_path))
        assert os.path.isdir(lay["stage"])
        assert os.path.isdir(os.path.join(lay["delivery"], "report"))
        assert os.path.isfile(lay["readme"])
        body = open(lay["readme"], encoding="utf-8-sig").read()
        # 说明文件必须解释"模型在哪"，否则用户还是要逐个目录翻
        assert "阶段成果" in body and "交付成果" in body
        assert "report.html" in body

    def test_idempotent(self, tmp_path):
        from h3d.core import paths
        a = paths.ensure_layout(str(tmp_path))
        b = paths.ensure_layout(str(tmp_path))
        assert a["stage"] == b["stage"] and os.path.isdir(b["stage"])


class TestMigrateLegacy:
    @staticmethod
    def _legacy(root: str) -> None:
        for d in ("00_raw", "00_photo", "10_dense", "30_dense", "50_tex",
                  "90_meta", "99_runs", "output", "report"):
            os.makedirs(os.path.join(root, d), exist_ok=True)
        os.makedirs(os.path.join(root, "80_export", "r1"), exist_ok=True)
        open(os.path.join(root, "80_export", "r1", "r1.ply"), "w").write("x")
        open(os.path.join(root, "output", "run_manifest.json"), "w").write("{}")

    def test_moves_into_two_subtrees(self, tmp_path):
        from h3d.core import paths
        root = str(tmp_path)
        self._legacy(root)
        res = paths.migrate_legacy(root)
        assert res["skipped"] == []
        assert sorted(os.listdir(root)) == sorted(["阶段成果", "交付成果",
                                                   "输出说明.txt"])
        assert os.path.isfile(os.path.join(root, "阶段成果", "output",
                                           "run_manifest.json"))
        assert os.path.isfile(os.path.join(root, "交付成果", "r1", "r1.ply"))
        assert os.path.isdir(os.path.join(root, "交付成果", "report"))
        assert not os.path.exists(os.path.join(root, "80_export"))

    def test_idempotent_second_run(self, tmp_path):
        from h3d.core import paths
        root = str(tmp_path)
        self._legacy(root)
        paths.migrate_legacy(root)
        assert paths.legacy_dirs_present(root) == []
        assert paths.migrate_legacy(root)["moved"] == []

    def test_dry_run_changes_nothing(self, tmp_path):
        from h3d.core import paths
        root = str(tmp_path)
        self._legacy(root)
        res = paths.migrate_legacy(root, dry_run=True)
        assert res["moved"]                      # 有计划
        assert os.path.isdir(os.path.join(root, "00_raw"))   # 但没动
        assert not os.path.isdir(os.path.join(root, "阶段成果"))

    def test_conflict_is_skipped_not_overwritten(self, tmp_path):
        """目标已存在时**跳过并记录**，绝不静默覆盖用户数据。"""
        from h3d.core import paths
        root = str(tmp_path)
        os.makedirs(os.path.join(root, "00_raw", "r1"))
        os.makedirs(os.path.join(root, "阶段成果", "00_raw", "r1"))
        res = paths.migrate_legacy(root)
        assert any(name == "00_raw" for name, _ in res["skipped"])
        assert os.path.isdir(os.path.join(root, "00_raw", "r1"))

    def test_new_layout_untouched(self, tmp_path):
        from h3d.core import paths
        root = str(tmp_path)
        lay = paths.ensure_layout(root)
        os.makedirs(os.path.join(lay["stage"], "00_raw", "r1"))
        assert paths.migrate_legacy(root)["moved"] == []


class TestMigrateRefusesNonProject:
    """迁移闸门：**不像项目根就不许动**（实机事故回归测试）。

    事故现场（2026-10-26）：在源码仓库根上触发迁移，仓库自带的 ``output/``
    （167 个文件的文档产物）被搬进 ``阶段成果/output``，``git status`` 一片 D。
    根因是 ``output`` / ``report`` 是**通用目录名**，任何仓库都可能命中。
    """

    @staticmethod
    def _repo_like(root: str) -> None:
        for d in ("src", "tests", "output", "report", "docs"):
            os.makedirs(os.path.join(root, d), exist_ok=True)
        open(os.path.join(root, "output", "a.pdf"), "w").write("x")
        open(os.path.join(root, "report", "b.html"), "w").write("x")

    def test_output_alone_is_not_a_project_marker(self, tmp_path):
        from h3d.core import paths
        root = str(tmp_path)
        self._repo_like(root)
        assert paths.looks_like_project(root) is False
        assert paths.legacy_dirs_present(root) == []

    def test_migrate_refuses_and_moves_nothing(self, tmp_path):
        from h3d.core import paths
        root = str(tmp_path)
        self._repo_like(root)
        res = paths.migrate_legacy(root)
        assert res.get("refused"), "非项目根必须拒绝，而不是照搬"
        assert res["moved"] == []
        assert res["skipped"] == []
        # 一个文件都不许挪走
        assert os.path.isfile(os.path.join(root, "output", "a.pdf"))
        assert not os.path.isdir(os.path.join(root, "阶段成果"))
        assert not os.path.exists(os.path.join(root, "输出说明.txt"))

    def test_project_marker_wins(self, tmp_path):
        """只要有 config.yaml 或任一阶段目录，就算项目根，正常迁移。"""
        from h3d.core import paths
        for marker in ("config.yaml", "00_raw", "30_dense", "80_export"):
            root = tmp_path / marker.replace(".", "_")
            os.makedirs(root)
            if marker.endswith(".yaml"):
                open(os.path.join(str(root), marker), "w").write("name: p\n")
            else:
                os.makedirs(os.path.join(str(root), marker))
            os.makedirs(os.path.join(str(root), "output"))
            assert paths.looks_like_project(str(root)) is True, marker
            assert "output" in paths.legacy_dirs_present(str(root)), marker


class TestConfigIntegration:
    def test_build_layout_creates_new_tree(self, tmp_path):
        from h3d.gui.config_edit import ProjectConfig
        c = ProjectConfig()
        c.root_dir = str(tmp_path)
        c.item_id = "r1"
        lay = c.build_layout()
        assert lay["raw"] == os.path.join(str(tmp_path), "阶段成果", "00_raw",
                                          "r1")
        assert os.path.isdir(lay["raw"])

    def test_to_yaml_points_into_stage_root(self, tmp_path):
        import yaml
        from h3d.gui.config_edit import ProjectConfig
        c = ProjectConfig()
        c.root_dir = str(tmp_path)
        c.item_id = "r1"
        c.build_layout()
        path = c.to_yaml(str(tmp_path / "config.yaml"))
        cfg = yaml.safe_load(open(path, encoding="utf-8"))
        stage = os.path.join(str(tmp_path), "阶段成果")
        assert cfg["project"]["raw_dir"] == os.path.join(stage, "00_raw")
        assert cfg["runner"]["manifest"] == os.path.join(
            stage, "output", "run_manifest.json")
        assert cfg["report"]["output_dir"] == os.path.join(
            str(tmp_path), "交付成果", "report")


class TestRunnerUsesDeliveryRoot:
    def test_task_root_is_stage_root(self, tmp_path):
        """任务发现的口径：``task.root`` = 00_raw 的父目录 = 阶段成果。

        这条是整个布局方案的地基：所有阶段代码按 ``task.root/<阶段目录>`` 取
        路径，只要 00_raw 位于 阶段成果 下，200+ 处拼接就自动跟着走。
        """
        from h3d.runner.batch_processor import discover_items
        from h3d.core import paths
        lay = paths.ensure_layout(str(tmp_path))
        raw = os.path.join(lay["stage"], "00_raw", "r1")
        os.makedirs(raw)
        open(os.path.join(raw, "scan.ply"), "w").write("ply\n")
        tasks = discover_items(os.path.join(lay["stage"], "00_raw"), ["ply"])
        assert len(tasks) == 1
        assert tasks[0].root == os.path.join(str(tmp_path), "阶段成果")
        assert paths.delivery_item_dir(tasks[0].root, "r1") == \
            os.path.join(str(tmp_path), "交付成果", "r1")
