"""后端标识进元数据与内容键（P0-3）。

为什么需要
----------
跨期比对要求「同一条对比链内重建后端版本一致」（ADR-0028 引用的第六项约定）。
不同后端（甚至同一后端的不同版本）在弱纹理区「补点」的方式不同，产生的偏差是
**系统性偏置**，LOD 吃不掉——整片区域会被判成「变化」。

所以在做真实两期之前，必须先把「这期是哪个后端哪版跑的」写进两处：

1. **资产元数据**（``CanonicalAsset.params``）——给人查；
2. **内容键**（``content_key`` 的 params）——给机器判「能不能复用/对比」。

只写第 1 处等于没写：报表上看得见，但复用与对比仍然按旧键命中。

判据（来自文献分析的 P0-3）
--------------------------
- 同一项目跑两次，``backend_version`` 一致；
- 手动改一位，``content_key`` 变化；
- 有稠密产物但缺标识 → 交付核验 WARN。
"""

from __future__ import annotations

import os

import pytest


class TestBackendInfo:
    """``io.mvs.backend_info`` 是唯一取「后端身份」的入口。"""

    def test_reports_id_and_version(self):
        from h3d.io.mvs import backend_info

        info = backend_info("colmap_patchmatch", exe="colmap")
        assert info["backend_id"] == "colmap_patchmatch"
        # 本机没配 PATH 时是 "unknown" —— 但**必须非空**：
        # 空串会被下游当成「没这个字段」，与「后端确实没有版本」混为一谈。
        assert info["backend_version"]

    def test_two_calls_agree(self):
        """必须可缓存：每件都去问一次 ``colmap -h`` 会让批量跑白等。"""
        from h3d.io.mvs import backend_info

        a = backend_info("colmap_patchmatch", exe="colmap")
        b = backend_info("colmap_patchmatch", exe="colmap")
        assert a == b

    def test_unknown_backend_raises(self):
        from h3d.io.mvs import backend_info

        with pytest.raises(ValueError, match="未知"):
            backend_info("no_such_backend")


class TestIdentityEntersContentKey:
    """身份必须进内容键——否则「换了后端」仍会命中旧缓存。"""

    def test_version_bit_flips_key(self):
        from h3d.archive import content_key

        base = {"pipeline": 1, "backend_id": "colmap_patchmatch",
                "backend_version": "COLMAP 4.1.1 (Commit a0d785f)"}
        same = content_key({"mesh": "aa"}, base)
        changed = content_key({"mesh": "aa"},
                              {**base, "backend_version": "COLMAP 4.1.2"})
        assert same != changed, "后端版本变了但内容键没变——缓存会命中错产物"

    def test_backend_id_alone_flips_key(self):
        from h3d.archive import content_key

        a = content_key({"mesh": "aa"}, {"backend_id": "colmap_patchmatch"})
        b = content_key({"mesh": "aa"}, {"backend_id": "ngmvs"})
        assert a != b

    def test_stable_for_same_identity(self):
        from h3d.archive import content_key

        p = {"backend_id": "colmap_patchmatch", "backend_version": "4.1.1"}
        assert content_key({"mesh": "aa"}, p) == content_key({"mesh": "aa"}, p)


class TestDenseBackendIdentity:
    """编排层要能把「这件用的哪个后端」取出来，且**两处来源都要认**。"""

    def _task(self, tmp_path):
        from h3d.runner.task import StageResult, Task, TaskStatus

        task = Task(item_id="relic_001", root=str(tmp_path), raw_paths=[])
        return task, StageResult, TaskStatus

    def test_reads_from_stage_results(self, tmp_path):
        from h3d.runner.batch_processor import _dense_backend_identity

        task, SR, TS = self._task(tmp_path)
        task.results["S1"] = SR("S1", TS.DONE, [], {
            "backend_id": "colmap_patchmatch",
            "backend_version": "COLMAP 4.1.1"})
        got = _dense_backend_identity(task, None)
        assert got == {"backend_id": "colmap_patchmatch",
                       "backend_version": "COLMAP 4.1.1"}

    def test_falls_back_to_manifest(self, tmp_path):
        """断点续跑时 S1 没执行、results 里没有它，但账本里有。"""
        from h3d.core import RunManifest
        from h3d.runner.batch_processor import _dense_backend_identity

        task, _, _ = self._task(tmp_path)
        man = RunManifest(config_sha256="x")
        man.record("relic_001/S1", "done", [], 1.0,
                   backend_id="colmap_patchmatch",
                   backend_version="COLMAP 4.1.1")
        got = _dense_backend_identity(task, man)
        assert got["backend_version"] == "COLMAP 4.1.1"

    def test_empty_when_never_ran(self, tmp_path):
        """没跑过 S1（纯激光件）→ 空字典，不是 {"backend_id": None}。

        空字典能干净地 ``{**base, **identity}``；带 None 的值会污染内容键，
        而且会让「没跑过」和「跑了但没记」看起来一样。
        """
        from h3d.runner.batch_processor import _dense_backend_identity

        task, _, _ = self._task(tmp_path)
        assert _dense_backend_identity(task, None) == {}


class TestSfmStageRecordsIdentity:
    """S1 的 metrics 必须带后端身份（**结构守卫**）。

    为什么用结构守卫而不是跑一遍 ``run_sfm_solve``：跑它需要真的 sparse 模型 +
    photos + capture_meta，构造成本远高于它要证明的事；而「有没有把这两个键写进
    metrics」是个结构事实，AST 能直接看。

    这条守卫挡的是本项目反复栽的那类坑：**上游算得再对，没写进 metrics 白名单
    就等于没算**（r16 起已踩三次）。
    """

    def test_stage_function_mentions_backend_keys(self):
        import ast
        import os

        path = os.path.join(os.path.dirname(__file__), "..", "..",
                            "src", "h3d", "sfm_solve.py")
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src)

        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "run_sfm_solve":
                target = node
                break
        assert target is not None, "找不到 run_sfm_solve"

        body = ast.get_source_segment(src, target) or ""
        assert "backend_id" in body, "S1 metrics 里没写 backend_id"
        assert "backend_version" in body, "S1 metrics 里没写 backend_version"


class TestVerifyReportsMissingIdentity:
    """交付核验要能指出「有稠密产物但查不到后端」——这是它的唯一用途。

    设计上只认「有 ``fused_dense.ply`` **且** 有 S1 记录」：
    - 只跑稀疏的项目（dense=false）不该被要求标后端；
    - 纯激光件项目（没有 S1）更不该。
    """

    def _project(self, tmp_path, *, with_dense: bool, backend: dict | None):
        from h3d.core import paths

        lay = paths.ensure_layout(str(tmp_path))
        item = "relic_001"
        if with_dense:
            d = os.path.join(lay["stage"], paths.STAGE_DIRS["dense"], item)
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "fused_dense.ply"), "wb").write(b"ply")
        entry = {"status": "done"}
        entry.update(backend or {})
        return str(tmp_path), {"stages": {f"{item}/S1": entry}}

    def test_flags_when_identity_missing(self, tmp_path):
        from h3d.delivery_verify import dense_backend_gaps

        root, man = self._project(tmp_path, with_dense=True, backend=None)
        assert dense_backend_gaps(root, man) == ["relic_001"]

    def test_clean_when_identity_present(self, tmp_path):
        from h3d.delivery_verify import dense_backend_gaps

        root, man = self._project(
            tmp_path, with_dense=True,
            backend={"backend_id": "colmap_patchmatch",
                     "backend_version": "COLMAP 4.1.1"})
        assert dense_backend_gaps(root, man) == []

    def test_ignores_projects_without_dense(self, tmp_path):
        """没有稠密产物（纯稀疏 / 激光件）→ 不要求后端标识。"""
        from h3d.delivery_verify import dense_backend_gaps

        root, man = self._project(tmp_path, with_dense=False, backend=None)
        assert dense_backend_gaps(root, man) == []

    def test_reads_nested_metrics_shape(self, tmp_path):
        """账本也有 ``{"metrics": {...}}`` 的嵌套形状，得认。"""
        from h3d.delivery_verify import dense_backend_gaps

        root, man = self._project(tmp_path, with_dense=True, backend=None)
        man["stages"]["relic_001/S1"] = {
            "status": "done",
            "metrics": {"backend_id": "colmap_patchmatch"}}
        assert dense_backend_gaps(root, man) == []
