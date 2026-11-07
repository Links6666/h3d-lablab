"""``io/mvs`` 契约：把「COLMAP 三条命令」变成可替换的后端（P0-1）。

为什么需要这个 seam
-------------------
稠密重建现在写死在 ``sfm_solve.run_dense_stereo`` 里：``image_undistorter`` →
``patch_match_stereo`` → ``stereo_fusion``，三条命令的路径与参数全部就地拼接。
后果是**换后端只能改业务层**——而这正是评估报告里唯一被论证为「越晚做越贵」的
架构决定：论文把 ``(K, R, t)`` 当作输入这一事实，反过来证明稠密重建与位姿估计
可以按契约切开，位姿正好是 S1 的输出。

这里钉的是**命令逐字相同**。
最终判据是「走契约与走原路径产出的 ``fused_dense.ply`` 哈希相同」，但跑一次
COLMAP 要几分钟、还要 GPU，所以单测只钉更便宜的那一半：命令与参数一字不差。
两者合起来才等价——命令相同 + 输入相同 ⇒ 产物相同。

为什么期望值要手写
------------------
下面是**重构前代码的行为**的逐字转写，不是调用旧函数算出来的。若从旧实现里读期望值，
测试会跟着实现一起漂，等于没有判据（本项目对「自证闭环」的一贯态度）。
"""

from __future__ import annotations

import os

import pytest


def _request(tmp_path, **opts):
    """最小可用的稠密请求：路径都真实存在（plan 不做存在性校验，但别依赖这点）。"""
    from h3d.io.mvs import MvsRequest

    photo = tmp_path / "photos"
    photo.mkdir()
    for i in range(3):
        (photo / f"{i:02d}.jpg").write_bytes(b"x")
    sparse = tmp_path / "sparse" / "0"
    sparse.mkdir(parents=True)
    opt = {"min_pixels": 2, "max_pixels": 20,
           "geom_consistency": True, "max_image_size": 0}
    opt.update(opts)
    return MvsRequest(photo_dir=str(photo), sparse_model=str(sparse),
                      workspace=str(tmp_path / "ws"), exe="colmap",
                      options=opt)


class TestColmapCommandsAreUnchanged:
    """三条命令必须与重构前逐字相同——这是 ``fused.ply`` 哈希相同的必要条件。"""

    def test_three_steps_and_fused_path(self, tmp_path):
        from h3d.io.mvs import get_backend

        req = _request(tmp_path)
        plan = get_backend("colmap_patchmatch", exe="colmap").plan(req)
        dense = os.path.join(req.workspace, "dense")

        assert plan.dense_dir == dense
        assert plan.fused_path == os.path.join(dense, "fused.ply")
        assert [s.label for s in plan.steps] == [
            "image_undistorter", "patch_match_stereo", "stereo_fusion"]
        assert [s.heavy for s in plan.steps] == [False, True, False]

    def test_argv_is_byte_identical(self, tmp_path):
        from h3d.io.mvs import get_backend

        req = _request(tmp_path)
        plan = get_backend("colmap_patchmatch", exe="colmap").plan(req)
        dense = os.path.join(req.workspace, "dense")
        got = tuple(tuple(s.argv) for s in plan.steps)

        assert got == (
            ("colmap", "image_undistorter",
             "--image_path", req.photo_dir,
             "--input_path", req.sparse_model,
             "--output_path", dense,
             "--output_type", "COLMAP"),
            ("colmap", "patch_match_stereo",
             "--workspace_path", dense,
             "--workspace_format", "COLMAP",
             "--PatchMatchStereo.geom_consistency", "true",
             "--PatchMatchStereo.gpu_index", "0"),
            ("colmap", "stereo_fusion",
             "--workspace_path", dense,
             "--workspace_format", "COLMAP",
             "--input_type", "geometric",
             "--output_path", os.path.join(dense, "fused.ply"),
             "--StereoFusion.min_num_pixels", "2",
             "--StereoFusion.max_num_pixels", "20"),
        )


class TestGeomConsistencySwitch:
    """关掉几何一致性时只跑一遍，且 fusion 改读 photometric 深度图。

    这一条很容易写错：``--PatchMatchStereo.geom_consistency false`` 与
    ``--input_type photometric`` 必须**同时**改，只改一个会拿到空点云或旧深度图。
    """

    def test_off_switches_both_flags(self, tmp_path):
        from h3d.io.mvs import get_backend

        req = _request(tmp_path, geom_consistency=False)
        plan = get_backend("colmap_patchmatch", exe="colmap").plan(req)

        pm = plan.steps[1].argv
        assert pm[pm.index("--PatchMatchStereo.geom_consistency") + 1] == "false"
        sf = plan.steps[2].argv
        assert sf[sf.index("--input_type") + 1] == "photometric"


class TestMaxImageSize:
    """``max_image_size`` 只在 >0 时追加——0 表示跟随输入，不能传 "0"。"""

    def test_zero_means_follow_input(self, tmp_path):
        from h3d.io.mvs import get_backend

        plan = get_backend("colmap_patchmatch", exe="colmap").plan(_request(tmp_path))
        assert "--PatchMatchStereo.max_image_size" not in plan.steps[1].argv

    def test_positive_is_appended(self, tmp_path):
        from h3d.io.mvs import get_backend

        plan = get_backend("colmap_patchmatch", exe="colmap").plan(
            _request(tmp_path, max_image_size=1600))
        pm = plan.steps[1].argv
        assert pm[pm.index("--PatchMatchStereo.max_image_size") + 1] == "1600"


class TestProgressProbe:
    """超时诊断要能说「算到哪了」。探针在空目录下必须返回可读文字而不是抛异常。"""

    def test_empty_workspace_reads_as_zero(self, tmp_path):
        from h3d.io.mvs import get_backend

        plan = get_backend("colmap_patchmatch", exe="colmap").plan(_request(tmp_path))
        note = plan.progress()
        assert "photometric 0" in note and "geometric 0" in note


class TestBackendIdentity:
    """P0-3 需要 ``backend_id`` / ``backend_version`` 落到元数据，这里先保证它们存在。"""

    def test_id_and_version_are_non_empty(self):
        from h3d.io.mvs import get_backend

        b = get_backend("colmap_patchmatch", exe="colmap")
        assert b.backend_id == "colmap_patchmatch"
        assert isinstance(b.backend_version, str) and b.backend_version

    def test_unknown_backend_is_rejected_loudly(self):
        from h3d.io.mvs import get_backend

        with pytest.raises(ValueError, match="未知|unknown"):
            get_backend("no_such_backend")


class TestCommandsLiveInTheAdapterOnly:
    """COLMAP 的命令名只许出现在 ``io/mvs`` 里。

    为什么要有这条守卫：契约的价值不在「现在拆开了」，而在「以后不会被缝回去」。
    最可能的退化形态就是有人图省事，在 ``sfm_solve`` 里加一句
    ``cmd.append("stereo_fusion")``——那样换后端又要改业务层，而且测试全绿。

    只看**代码里的字符串常量**（AST 的 Constant），docstring 豁免：本项目大量注释
    在解释「这条命令为什么这么传」，把文档也算上就会逼人删掉解释，比不设守卫更糟。
    """

    #: COLMAP 稠密三步的名字。它们属于适配层。
    COMMANDS = {"image_undistorter", "patch_match_stereo", "stereo_fusion"}

    def test_business_layer_has_no_colmap_command_literals(self):
        import ast
        import os

        path = os.path.join(os.path.dirname(__file__), "..", "..",
                            "src", "h3d", "sfm_solve.py")
        tree = ast.parse(open(path, encoding="utf-8").read())

        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) \
                        and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))

        offenders = [(n.lineno, n.value) for n in ast.walk(tree)
                     if isinstance(n, ast.Constant)
                     and isinstance(n.value, str)
                     and id(n) not in docstrings
                     and n.value in self.COMMANDS]

        assert not offenders, (
            "业务层又拼起 COLMAP 命令了（应只在 h3d/io/mvs 里）："
            f"{offenders}")


class TestCommandsLiveInTheAdapterOnly:
    """COLMAP 的命令名只许出现在 ``io/mvs`` 里。

    为什么要有这条守卫：契约的价值不在「现在拆开了」，而在「以后不会被缝回去」。
    最可能的退化形态就是有人图省事，在 ``sfm_solve`` 里加一句
    ``cmd.append("stereo_fusion")``——那样换后端又要改业务层，而且测试全绿。

    只看**代码里的字符串常量**（AST 的 Constant），docstring 豁免：本项目大量注释
    在解释「这条命令为什么这么传」，把文档也算上就会逼人删掉解释，比不设守卫更糟。
    """

    #: COLMAP 稠密三步的名字。它们属于适配层。
    COMMANDS = {"image_undistorter", "patch_match_stereo", "stereo_fusion"}

    def test_business_layer_has_no_colmap_command_literals(self):
        import ast
        import os

        path = os.path.join(os.path.dirname(__file__), "..", "..",
                            "src", "h3d", "sfm_solve.py")
        tree = ast.parse(open(path, encoding="utf-8").read())

        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", [])
                if body and isinstance(body[0], ast.Expr) \
                        and isinstance(body[0].value, ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))

        offenders = [(n.lineno, n.value) for n in ast.walk(tree)
                     if isinstance(n, ast.Constant)
                     and isinstance(n.value, str)
                     and id(n) not in docstrings
                     and n.value in self.COMMANDS]

        assert not offenders, (
            "业务层又拼起 COLMAP 命令了（应只在 h3d/io/mvs 里）："
            f"{offenders}")
