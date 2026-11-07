"""h3d : 中小型文物三维建模自动化系统（路线 E）顶层包。

对外暴露版本号与核心底座，供 CLI 与报表层引用：

    import h3d
    h3d.__version__

    from h3d import core
"""

import os as _os
import sys as _sys

#: 配准用的线程数（ADR-0031）。**不存在"改成 2 试试"的选项**：Open3D 的 RANSAC
#: 用 OpenMP 做并行归约，合并顺序不受 `seed_rng` 约束，多线程下同一份输入会给出
#: 不同初值——实测 6 次 4 种解。所以这里不是"性能参数"，是**可复现性的开关**。
_THREAD_ENV = "OMP_NUM_THREADS"

#: 必须在 import open3d **之前**设置。实测：进程启动前设 =1 → 6 次同 1 种解；
#: `import open3d` 之后再设 =1 → 6 次仍 4 种解（完全无效）。OpenMP 在第一次
#: 并行区域初始化时就锁定了线程数，事后改环境变量只是改了个没人读的字符串。
#:
#: 代价：RANSAC 从 0.30s 变 0.47s（真机 33k 点）。相对整条 M2（含 ICP）可忽略，
#: 换到的是「同一输入必得同一结果」——这是报表数字可引用的前提，
#: 与 M3 泊松早就在用的 `n_threads=1` 同一个理由。
_prev_threads = _os.environ.get("OMP_NUM_THREADS")
_os.environ["OMP_NUM_THREADS"] = "1"

if "open3d" in _sys.modules:
    # 已经晚了：调用方（旧脚本 / notebook / 某些 conftest）先 import 了 open3d。
    # 这个场景修不好，但**不能静默**——否则用户以为结果可复现，其实每次都在变。
    # 与项目一贯口径一致：兜底必须同时写指标 + WARNING（禁静默降级）。
    import warnings as _warnings

    _warnings.warn(
        "open3d 在 import h3d 之前就已加载，寄存器单线程设置（OMP_NUM_THREADS=1）"
        "不会生效——配准每次可能给出不同结果。修法：把 `import h3d...` 提到所有 "
        "`import open3d` 之前，或让进程启动时就带上该环境变量。",
        RuntimeWarning, stacklevel=2)
elif _prev_threads not in (None, "", "1"):
    # 用户显式设了别的值（比如 8 想加速）。覆盖掉，但说出来——
    # 静默改用户的环境变量比不改更糟。
    import warnings as _warnings

    _warnings.warn(
        f"{_THREAD_ENV} 原为 {_prev_threads}，已强制改为 1：多线程下 Open3D 的 "
        "RANSAC 结果不可复现（同一输入 6 次给出 4 种解，ADR-0031）。",
        RuntimeWarning, stacklevel=2)

#: 版本号**唯一来源**。报表页脚、CLI、`pyproject.toml` 全部从这一行派生
#: （pyproject 用 `dynamic = ["version"]` → `attr = "h3d.__version__"`）。
#:
#: 2026-11-04 修：注释原来写「改版本只改这里 + pyproject」——**"改两处"不是唯一
#: 来源**，实测已漂成 pyproject 1.1.12 / 本文件 1.1.13 / CHANGELOG 1.1.16。
#: 现在只有这一行是真的，另外两处要么派生、要么由测试对着它校验。
#: 改版本时：改这一行 + 在 CHANGELOG 顶部加同号条目（`tests/test_version_single_source.py`
#: 会拦住漏改）。
__version__ = "1.1.29"

__all__ = ["__version__"]
