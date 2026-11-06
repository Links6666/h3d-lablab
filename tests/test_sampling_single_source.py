"""表面采样只能有一个入口：`h3d.io.sample_surface`。

为什么要一条 grep 式守卫
------------------------
2026-11-04 修 M5 的数字漂移时（ADR-0020）定的纪律是「新代码不要再直接调
``mesh.sample_points_uniformly``」。之所以要写成测试而不是写在文档里：

* 同一个坑**已经踩过两次**——2026-11-03 在对齐精化（`ctrl_points`），
  2026-11-04 又在 M5 质检。两次都是「加了一个新的采样调用点，忘了定种子」，
  而症状都是**小于 1% 的漂移**：不触发任何断言，又让两天前的报表对不上。
* 直接调用的可怕之处是它**看起来没问题**：代码能跑、数能出、测试全绿，
  只有把两次运行的数字逐字比一遍才发现不同。

所以判据放在源码上：除 ``h3d/io/mesh_io.py``（适配层，实现处）外，
任何模块都不许出现 ``sample_points_uniformly`` 或 Open3D 的随机种子调用。
"""
from __future__ import annotations

import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..", "src", "h3d")
#: 采样 API 只允许在「采样实现」里出现（`sample_surface` 的实现处）
ALLOWED_SAMPLING = {os.path.join("io", "mesh_io.py")}
#: 随机源只允许在 `io/rng.py` 里碰 —— 比采样更严一格：
#: 「定种子」与「采样」是两个动作，各有一个入口，谁也别替谁做
ALLOWED_RNG = {os.path.join("io", "rng.py")}


def _sources():
    for dirpath, _dirnames, filenames in os.walk(ROOT):
        for name in filenames:
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT)
            yield rel, os.path.join(dirpath, name)


def _code_lines(path):
    """只取代码行：注释和文档字符串里提到函数名不算违规。

    这条很重要——本项目大量注释在解释「为什么不要直接调它」。守卫要是把
    注释也算上，就会逼人删掉解释，那比不设守卫更糟。
    """
    in_doc = False
    quote = ""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if in_doc:
                if quote in stripped:
                    in_doc = False
                continue
            if stripped.startswith(('"""', "'''")):
                quote = stripped[:3]
                # 单行文档字符串：同一行里有第二个闭合
                if stripped.count(quote) < 2:
                    in_doc = True
                continue
            code = stripped.split("#", 1)[0]
            if code.strip():
                yield code


class TestSamplingHasOneEntry:
    def test_no_direct_open3d_sampling_outside_the_adapter(self):
        offenders = []
        for rel, path in _sources():
            if rel in ALLOWED_SAMPLING:
                continue
            for code in _code_lines(path):
                if re.search(r"\.sample_points_uniformly\s*\(", code) \
                        or re.search(r"\.sample_points_poisson_disk\s*\(", code):
                    offenders.append(f"{rel}: {code.strip()}")
        assert not offenders, (
            "这些地方直接调了 Open3D 采样（无法定种子 → 每次重跑数字都会漂）。"
            "改用 h3d.io.sample_surface(mesh, n, seed)：\n  "
            + "\n  ".join(offenders))

    def test_no_raw_open3d_rng_seeding_outside_the_adapter(self):
        offenders = []
        for rel, path in _sources():
            if rel in ALLOWED_RNG:
                continue
            for code in _code_lines(path):
                if "utility.random.seed" in code:
                    offenders.append(f"{rel}: {code.strip()}")
        assert not offenders, (
            "种子只能在 io 层设（和采样绑在一起）。裸设种子留下的是"
            "「先定种子、后自己采样」的两个语句，中间插一行就断：\n  "
            + "\n  ".join(offenders))

    def test_the_adapter_actually_exposes_the_entries(self):
        from h3d.io import SAMPLE_SEED, sample_surface, seed_rng

        assert callable(sample_surface)
        assert callable(seed_rng)
        assert isinstance(SAMPLE_SEED, int)


#: 会消耗 Open3D 随机源的 API。用到它们的模块必须**自己**把种子定住。
_STOCHASTIC = ("segment_plane(", "registration_ransac", "sample_points_uniformly(",
               "sample_points_poisson_disk(", "cluster_dbscan(")
#: 这两个模块是「种子/采样」的实现处，本身不重复调用 seed_rng
_SEED_IMPL = {os.path.join("io", "rng.py"), os.path.join("io", "mesh_io.py")}


class TestStochasticCallsAreSeeded:
    """用了随机 API 的模块，必须在同一文件里出现 `seed_rng(` 或 `sample_surface(`。

    为什么这条守卫比"禁止直调"更根本：直调被禁了，但**忘了定种子**照样会漂。
    本项目已经在这种形态上踩过四次——M5 采样（ADR-0020）、四处直调（ADR-0023）、
    M2 的 RANSAC（ADR-0024）、M1 的平面剔除（ADR-0026，跨进程差 1 个内点）。
    四次都是「代码能跑、数能出、测试全绿」。
    """

    def test_every_stochastic_module_seeds(self):
        offenders = []
        for rel, path in _sources():
            if rel in _SEED_IMPL:
                continue
            code = "\n".join(_code_lines(path))
            if not any(k in code for k in _STOCHASTIC):
                continue
            if "seed_rng(" in code or "sample_surface(" in code:
                continue
            offenders.append(rel)
        assert not offenders, (
            "这些模块调用了随机的 Open3D API 却没有定种子（同输入会漂）：\n  "
            + "\n  ".join(offenders))
