"""M2 配准必须可复现：同一份输入两次，变换矩阵要逐位相同。

2026-11-05 实测（`scripts/verify_registration_e2e.py`，真机点云
`D:/h3d_relic_test/.../test111.ply`，注入 120° 旋转 + 0.2% 对角线噪声）：

| 运行 | `icp_init` | fitness | 旋转角误差 | 判定 |
|---|---|---|---|---|
| 1 | flip180_axis2 | 1.000 | 0.04° | PASS |
| 2 | ransac | **0.5216** | **179.26°** | FAIL |
| 3–10 | — | 1.000 | 0.04° | PASS |

**同一份输入，10 次里 1 次解出 179° 翻转解**（另一次独立实验 3 次里 2 次）。
根因是 RANSAC 用 Open3D 的进程级随机源，而这里从来没有定过种子——与 r24 修的
M5 采样是同一类病，只是换了个消费者。

注意这不是 r24–r27 引入的回归：用 `git worktree` 检出 r23（`f2471b3`）跑同一
用例，同样 4 次里出现过 `icp_init=ransac` 的走法，稳定与否只是运气。
"""
from __future__ import annotations

import numpy as np
import open3d as o3d
import pytest


def _blob(seed: int = 0, n: int = 3000):
    """一个略有不对称的团块（纯球体对配准太容易，测不出翻转）。"""
    rng = np.random.default_rng(seed)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    v *= 0.05
    v[:, 0] *= 1.0 + 0.25 * np.sin(3 * np.arctan2(v[:, 1], v[:, 2]))
    v[:, 2] += 0.01 * v[:, 2] ** 2 / 0.0025
    pc = o3d.geometry.PointCloud()
    pc.points = o3d.utility.Vector3dVector(v)
    return pc


def _rot_y(deg: float) -> np.ndarray:
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], float)


def _pair(deg: float = 40.0):
    src = _blob()
    tgt = _blob()
    tgt.rotate(_rot_y(deg), center=np.zeros(3))
    return src, tgt


class TestRegistrationIsReproducible:
    def test_same_input_same_transformation(self):
        from h3d.core import RegisterConfig
        from h3d.register import run

        cfg = RegisterConfig()
        src, tgt = _pair()
        a = run(src, tgt, cfg)
        b = run(src, tgt, cfg)
        # 判据用 1e-12 而不是逐位相等：ICP 是迭代式的，末位差可达 1e-32
        # （实测），那不是"结果不同"而是浮点累加顺序。真正要一致的是**决策量**：
        # 选哪个初值、fitness 多少 —— 那才决定配准对不对。
        assert np.allclose(np.asarray(a["transformation"]),
                           np.asarray(b["transformation"]), atol=1e-12), (
            "同一份输入两次配准得到了不同的变换矩阵——RANSAC 没有定种子")
        assert a["fitness"] == b["fitness"]

    def test_fitness_and_init_also_match(self):
        from h3d.core import RegisterConfig
        from h3d.register import run

        cfg = RegisterConfig()
        src, tgt = _pair()
        a, b = run(src, tgt, cfg), run(src, tgt, cfg)
        assert a["icp_init"] == b["icp_init"]
        assert a["inlier_rmse"] == b["inlier_rmse"]
