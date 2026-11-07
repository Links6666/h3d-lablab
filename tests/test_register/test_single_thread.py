"""配准必须单线程（ADR-0031）—— 设置位置错了就等于没设。

背景（2026-11-07 诊断，`scripts/diagnose_register_steps.py`）
------------------------------------------------------------
把 `register.run` 拆成四段各跑 8 次（真机 `test111.ply`，注入 7°）：

    A compute_fpfh        唯一 1  → 确定
    B RANSAC 初值          唯一 3  → **不确定**
    C ICP（固定初值）       唯一 1  → 确定

根因是 Open3D 的 RANSAC 内部用 OpenMP 并行归约，合并顺序不受 `seed_rng` 约束。
r28 当年把原因写成推断（「并行归约不受种子完全约束」），这次拆开量到了证据。

为什么测试要盯**设置位置**而不是「设没设」
------------------------------------------
三条实测，同一份数据、同一段代码，只改环境变量的设置时机（6 次一批）：

    baseline（未设）            → 4 种解
    import open3d **之后**设 =1 → **4 种解**（完全无效）
    进程启动前设 =1             → 1 种解

也就是说 `os.environ["OMP_NUM_THREADS"]="1"` 写在 open3d 加载之后是**假的约束**：
代码看起来管住了，实际一点用没有。本项目对这种「看起来有约束、实际没有」的东西
已经栽过一次（r27 那行空的 `_seed_random`），所以这里用子进程把时机钉死。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def _run(code: str, env_extra: dict | None = None) -> str:
    """在干净子进程里跑一段代码，返回 stdout（合并 stderr 便于查 WARNING）。"""
    env = dict(os.environ)
    env.pop("OMP_NUM_THREADS", None)          # 不让父进程的设定作弊
    env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
    if env_extra:
        env.update(env_extra)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, env=env, timeout=180)
    return (p.stdout or "") + (p.stderr or "")


class TestSingleThreadIsForced:
    """核心断言：加载了 open3d 的进程里，OMP_NUM_THREADS 必须是 1。"""

    def test_io_path_is_single_thread(self):
        """`import h3d.io` 之后环境变量已是 1。

        注意这里**不要求 open3d 已加载**：`h3d.io` 是惰性的（真到要用时才在
        `_o3d()` 里 import），这恰好是好事——加一个包就设好了单线程，
        与有没有碰过 open3d 无关。
        """
        out = _run(
            "import h3d.io, os\n"
            "print('OMP=' + str(os.environ.get('OMP_NUM_THREADS')))\n")
        assert "OMP=1" in out, (
            "import h3d.io 之后 OMP_NUM_THREADS 不是 1 —— 单线程没生效，"
            f"配准会每次给出不同结果。输出：{out}")

    def test_register_path_is_single_thread(self):
        """`import h3d.register` 之后 OMP 是 1。

        注意 open3d 此时**仍未加载**：整个 h3d 包都是惰性的（`_o3d()` 里才
        真 import），这不是缺陷，是分层架构的要求（业务层不直接 import
        Open3D）。正因为惰性，「设置在前、加载在后」是结构性成立的，
        不依赖调用方习惯——下面那条测试验证这个推论。
        """
        out = _run(
            "import h3d.register, os, sys\n"
            "print('OMP=' + str(os.environ.get('OMP_NUM_THREADS')))\n"
            "print('o3d=' + str('open3d' in sys.modules))\n")
        assert "OMP=1" in out, f"import h3d.register 之后不是单线程：{out}"

    def test_gate_is_already_set_when_open3d_finally_loads(self):
        """第一次真正碰 open3d 时，OMP 早已是 1——这是「可复现」的结构保证。

        顺序：import h3d（设 OMP）→ 断言 → 才 import open3d。
        真实路径也是这样：`h3d.io` / `h3d.register` 导入时不加载 open3d，
        直到第一次调用 `_o3d()`。所以只要包入口设了，就一定赶在加载之前。
        """
        out = _run(
            "import h3d, os\n"
            "assert os.environ.get('OMP_NUM_THREADS') == '1', '包入口没设 OMP'\n"
            "import open3d as o3d\n"
            "print('loaded_with=' + os.environ.get('OMP_NUM_THREADS'))\n")
        assert "loaded_with=1" in out, (
            f"open3d 加载时 OMP 不是 1：{out}")


class TestLateSettingIsDetected:
    """open3d 已经加载时才 import h3d —— 设置无效，**必须说出来**。

    这不是能修好的场景（OpenMP 在第一次并行区域就初始化了），但也不能静默：
    用户会以为"我用了这套管线所以结果可复现"，而实际上并没有。

    与项目一贯口径一致：兜底必须同时写指标 + WARNING（禁静默降级）。
    """

    def test_warns_when_open3d_loaded_first(self):
        out = _run(
            "import open3d\n"                 # 抢先加载，模拟用户脚本 / 旧 notebook
            "import h3d\n"
            "print('done')\n")
        assert "done" in out
        low = out.lower()
        assert ("单线程" in out or "open3d" in low) and "warn" in low, (
            "open3d 先加载导致单线程失效时没有 WARNING——这就是静默降级。"
            f"输出：{out}")

    def test_does_not_crash_when_late(self):
        """晚设是「降级」不是「崩溃」：管线必须还能跑（只是不可复现）。"""
        out = _run(
            "import open3d\n"
            "import h3d\n"
            "import h3d.register\n"
            "print('ok')\n")
        assert "ok" in out, f"晚设场景直接崩了：{out}"


class TestSourceOrderIsRight:
    """源码层面再钉一次：设置必须写在 `h3d/__init__.py`（包入口）。

    为什么还要这条：子进程测试依赖「导入顺序」，而导入顺序会随 conftest、
    插件的引入改变。放在包入口是唯一不依赖调用方习惯的位置。

    AST 检查而不是正则：注释里大量写着 `import open3d`（解释为什么不能先导它），
    正则会把注释也算上，逼人删解释。
    """

    def test_gate_is_in_package_entry(self):
        import ast

        path = os.path.join(_SRC, "h3d", "__init__.py")
        tree = ast.parse(open(path, encoding="utf-8").read())
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if (isinstance(tgt, ast.Subscript)
                            and isinstance(tgt.value, ast.Attribute)
                            and tgt.value.attr == "environ"):
                        seg = ast.get_source_segment(
                            open(path, encoding="utf-8").read(), node) or ""
                        if "OMP_NUM_THREADS" in seg:
                            found = True
        assert found, (
            "h3d/__init__.py 里没有对 OMP_NUM_THREADS 的赋值——"
            "单线程必须落在包入口，否则谁先 import 谁决定结果")

    def test_entry_imports_nothing_heavy(self):
        """包入口不能 import open3d（否则设置永远来不及）。"""
        import ast

        path = os.path.join(_SRC, "h3d", "__init__.py")
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert "open3d" not in a.name, "h3d/__init__.py 不许 import open3d"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert "open3d" not in node.module, \
                    "h3d/__init__.py 不许 import open3d"
