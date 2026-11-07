"""局部法向一致性（P1-1 可靠性 / P0-2 按一致性剔除对应点）。

这一层只回答两个问题：
    1. 这片点云的**法向本身可靠吗**（``normal_quality``）；
    2. 每个点周围的法向**乱不乱**（``normal_consistency``，即论文里的 c）。

论文怎么用它，和我们能用它做什么，是两件事
------------------------------------------
论文（刘建祥等，2026）把 c 用在三处：调制特征、加权相似度体、校准代价体。
我们只借**这个量本身**：

* Open3D 的 ``registration_icp`` **不接受逐点权重**（``TransformationEstimation*``
  只有标量 weight）。论文的 ``w = 1 + tanh(α)(0.5−c)`` 在这里做不到。
* 能做到的是「把 c 低于阈值的点排除」——等价于 w→0 那一端。
  所以函数和字段都叫 ``filter``（剔除），不叫 weight（加权）。名字撒谎比功能缺失更糟。

为什么必须先量可靠性（P1-1）
---------------------------
「用一个不可靠的量去做筛选，可能比不筛选更差」。论文自己的表 6 就是证据：
GT 法向 0.280 / Omnidata 0.286 / MiDaS 0.295 / 无先验 0.309 —— 先验质量会传到结果。

我们的几何法线在单面扫描上本就不稳（外向归一要靠相机投票，ADR-0025 记过）。
所以 ``normal_quality`` 的中位数低于 ``MIN_USABLE_CONSISTENCY`` 时，调用方应当
**放弃筛选**并留 WARNING，而不是硬筛。

点云上用 ``|dot|``，不是论文的 ``(dot+1)/2``
-------------------------------------------
论文的图像法向是逐像素预测的，符号天然连续，所以 ``c = (dot+1)/2`` 成立。
点云的 ``estimate_normals`` 符号是逐点随机的：同一片平面上相邻两点可能一正一负，
直接套 ``(dot+1)/2`` 会把「完全平滑的面」算成 c≈0——那不是几何乱，是符号没统一。

第一版试过「先一致化定向再算 dot」：**结果更糟**。
``orient_normals_consistent_tangent_plane`` 是沿邻域传播方向的，它对「本没有表面」
的散点云也会硬传播出一套平滑的伪场——于是 c 恒高，闸门失效（实测体散点云的 c
中位数被判成 0.9+，而实际上那些法线是随机的）。

所以这里用 ``c = |n̂ · n̄|``：只问「方向是否共线」，不问朝向。符号问题归
``normal_agreement``（ADR-0025）管，两者分工不重叠。
"""

from __future__ import annotations

import copy

import numpy as np

from h3d.core import get_logger

log = get_logger("register.normals")

#: 「可用」的门槛：一致性**中位数**低于它就别拿去做筛选。
MIN_USABLE_CONSISTENCY = 0.7
#: 统计 ``frac_low`` 时用的「局部不一致」阈值（0.6 对应夹角约 106°）。
LOW_CONSISTENCY = 0.6

__all__ = ["normal_consistency", "normal_quality",
           "MIN_USABLE_CONSISTENCY", "LOW_CONSISTENCY"]


def _arrays(pcd, radius: float, max_nn: int):
    """取（拷贝后）点与法线。**不做法向定向**——定向会把不一致抹平。

    **必须拷贝**：``estimate_normals`` 是原地修改。调用方（M2 的 source/target）
    在别处还要用这些法线，被悄悄改掉会让 ``normal_agreement`` 的结论变掉却不报错。
    """
    import open3d as o3d

    work = copy.deepcopy(pcd)
    if not work.has_normals():
        work.estimate_normals(
            o3d.geometry.KDTreeSearchParamHybrid(radius=float(radius),
                                                 max_nn=int(max_nn)))
    pts = np.asarray(work.points, dtype=np.float64)
    nrm = np.asarray(work.normals, dtype=np.float64)
    return pts, nrm


def normal_consistency(pcd, radius: float, max_nn: int = 30) -> np.ndarray:
    """逐点局部法向一致性 ``c = |n̂ · n̄|``，取值 [0, 1]。

    ``n̄`` 是邻域（``max_nn`` 个最近点）法线的**均值再单位化**。平滑曲面上一片
    区域的 c 接近 1；折角、边缘、以及**根本没有表面**的散点云明显偏低（随机方向
    的 ``|dot|`` 中位数约 0.5）。

    与论文的差别（``|dot|`` 而非 ``(dot+1)/2``）见模块 docstring：点云法线符号
    不连续，取绝对值才有判别力。

    返回长度与点数相同的数组；点云为空时返回空数组（不抛——空点云的检查属于调用方）。
    """
    from scipy.spatial import cKDTree

    pts, nrm = _arrays(pcd, radius, max_nn)
    n = len(pts)
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    if n < 3:
        return np.ones(n, dtype=np.float64)

    k = max(2, min(int(max_nn), n))
    _, idx = cKDTree(pts).query(pts, k=k)
    if idx.ndim == 1:
        idx = idx[:, None]
    n_bar = nrm[idx].mean(axis=1)
    norm = np.linalg.norm(n_bar, axis=1, keepdims=True)
    n_bar = n_bar / np.maximum(norm, 1e-12)
    c = np.abs(np.einsum("ij,ij->i", nrm, n_bar))
    return np.clip(c, 0.0, 1.0)


def normal_quality(pcd, radius: float, max_nn: int = 30) -> dict:
    """法向可靠性：``{"median", "frac_low", "usable", "n"}``。

    这两个数是 P1-1 的闸门判据：
        * ``median`` 低 → 法向整体不可信，筛出来的「低一致性点」多半是噪声造成的；
        * ``frac_low`` 高 → 大面积法向不一致，筛选会砍掉大块面积、把配准变成
          「用半件去配整件」。
    """
    c = normal_consistency(pcd, radius, max_nn)
    if len(c) == 0:
        return {"median": 0.0, "frac_low": 1.0, "usable": False, "n": 0}
    med = float(np.median(c))
    return {
        "median": med,
        "frac_low": float((c < LOW_CONSISTENCY).mean()),
        "usable": bool(med >= MIN_USABLE_CONSISTENCY),
        "n": int(len(c)),
    }
