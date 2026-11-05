"""h3d.io.rng : 系统里**唯一**允许碰 Open3D 全局随机源的地方。

为什么要单独一个模块
--------------------
2026-11-04 修「M5 数字每次重跑都在漂」时（ADR-0020），把表面采样收敛成
:func:`h3d.io.sample_surface(mesh, n, seed)` —— 「定种子 + 采样」绑在一起，
没法只做一半。那条路走得很好，一直走到 r27 把四处残留都扫干净。

但采样不是唯一的随机消费者：**RANSAC 也是**。2026-11-05 实测
``scripts/verify_registration_e2e.py`` 注入 120° 旋转：

    17 次运行里 3 次解出 179° 翻转解（fitness 0.52），其余 14 次正确（fitness 1.00）

同一份输入两种结果，其中一种还是错的。RANSAC 内部用 Open3D 的进程级随机源，
而这里从来没定过种子。

所以需要第二个入口，但**只有第二个**，而且它不负责采样：

* :func:`seed_rng`  —— 给「不经过我们采样函数」的随机算法定种子（RANSAC）；
* ``sample_surface`` —— 表面采样（自带种子，不用先调 seed_rng）。

把这两者混成一个 ``seed_sampling`` 就是上一版的老毛病：名字承诺了一件事
（"让采样可复现"），实现只做了一半（只设种子，采不采、采几次不管）。

守卫：`tests/test_sampling_single_source.py` 只允许本文件出现
``utility.random.seed`` —— 加进来时把 ``io/mesh_io.py`` 从白名单里去掉了。
"""

from __future__ import annotations

#: 默认种子。固定值是**判据的一部分**：同一输入两次必须同结果。
DEFAULT_SEED = 20261105


def seed_rng(seed: int = DEFAULT_SEED) -> None:
    """固定 Open3D 的全局随机源。

    只在「随机算法不经过 :func:`h3d.io.sample_surface`」时用（目前是 RANSAC）。
    API 缺失或失败时静默跳过——不因为设不了种子就让整条管线跑不动。
    """
    try:
        import open3d as o3d

        o3d.utility.random.seed(int(seed))
    except Exception:  # noqa: BLE001 - 定不了种子不该阻断主流程
        pass


__all__ = ["DEFAULT_SEED", "seed_rng"]
