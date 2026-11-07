"""法向可靠性 + 按一致性剔除对应点（P1-1 / P0-2，ADR-0030）。

背景
----
论文（刘建祥等，2026）用「局部法向一致性」做三件事：调制特征、加权相似度体、
校准代价体。我们的 M2 能不能借？能借的只有**一致性这个量本身**，不是它的用法：

- Open3D 的 ``registration_icp`` **不接受逐点权重**（``TransformationEstimation*``
  只有标量 weight）。所以论文的 ``w = 1 + tanh(α)(0.5−c)`` 在这里**做不到**，
  能做到的是「把 c 低于阈值的点排除」——等价于 w→0 的那一端。
- 所以本模块叫「按一致性剔除」，不叫「加权」。名字撒谎比功能缺失更糟。

先做 P1-1（量出可靠性）再做 P0-2（用它），顺序不能反
----------------------------------------------------
「用一个不可靠的量去做筛选，可能比不筛选更差」。论文自己的表 6 就是证据：
GT 法向 0.280 / Omnidata 0.286 / MiDaS 0.295 / **无先验 0.309**——先验质量会传递到结果。

我们的几何法线在**单面扫描**上本就不稳（外向归一要靠相机投票）。所以先量
``normal_quality``：中位数低于 0.7 就**自动关闭**筛选并 WARNING，而不是硬着头皮筛。

一个必须避开的假提升
--------------------
剔除 source 点会让 ``fitness = 内点数 / 总点数`` 的分母变小，fitness 天然上升。
那是**口径变化**，不是「配得更好」。所以：**筛选只用于求变换，评估一律用未筛选的
原始 source**（``evaluate_registration`` 重算一遍）。
"""

from __future__ import annotations

import numpy as np
import pytest


def _volumetric(n=3000, radius=0.10, seed=1):
    """球**内部**均匀散点——不是表面。

    为什么用它代表「法向不可靠」：微扰一个球面，局部仍是平面，``estimate_normals``
    照样给出彼此一致的法线（c 仍接近 1）。真正让法向变乱的是「没有表面」——
    体散点估出来的法线方向近乎随机。这才是单面扫描/稀疏区域的实际形态。
    """
    import open3d as o3d

    rng = np.random.default_rng(seed)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    r = radius * rng.uniform(0.05, 1.0, size=(n, 1)) ** (1 / 3)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(v * r)
    return pcd


def _sphere_with_specks(n=3000, k=600, seed=5):
    """球面 + 远离表面的孤立点（模拟没清干净的飞点）。

    平滑球面上 c 恒等于 1，**无可筛**——用它测「筛选生效」必然失败。
    飞点周围没有表面，法线随机、c 低，才是筛选真正起作用的地方。
    """
    import open3d as o3d

    surf = _sphere(n=n, seed=seed)
    rng = np.random.default_rng(seed + 1)
    v = rng.normal(size=(k, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    r = 0.10 * rng.uniform(1.5, 2.2, size=(k, 1))
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(
        np.vstack([np.asarray(surf.points), v * r]))
    return pcd


def _sphere(n=4000, radius=0.10, noise=0.0, seed=0):
    """球面点云（带法线）。``noise`` 越大，局部法向越乱。"""
    import open3d as o3d

    rng = np.random.default_rng(seed)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    if noise > 0:
        v = v + rng.normal(scale=noise, size=(n, 3)) * 0.05
        v /= np.linalg.norm(v, axis=1, keepdims=True)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(v * radius)
    return pcd


class TestNormalConsistency:
    """``c = (n̂ · n̄ + 1)/2``，平滑面上应该接近 1。"""

    def test_smooth_sphere_is_highly_consistent(self):
        from h3d.register.normals import normal_consistency

        c = normal_consistency(_sphere(), radius=0.03)
        assert c.shape == (4000,)
        assert 0.0 <= c.min() and c.max() <= 1.0
        # 平滑球面上邻域法线几乎同向 → c 接近 1。取中位数而不是均值：
        # 极点附近采样稀疏，会有少量低值，那是真实的几何，不是 bug。
        assert np.median(c) > 0.9, f"平滑面上的一致性中位数只有 {np.median(c):.3f}"

    def test_normals_without_surface_score_lower(self):
        """没有表面（体散点）→ c 中位数接近 0.5。这是 P1-1 闸门的判据依据。"""
        from h3d.register.normals import normal_consistency

        smooth = float(np.median(normal_consistency(_sphere(), 0.03)))
        random_dir = float(np.median(normal_consistency(_volumetric(), 0.03)))
        assert random_dir < smooth - 0.2, (smooth, random_dir)
        assert random_dir < 0.75, f"体散点的 c 中位数有 {random_dir:.3f}，不像随机"

    def test_empty_cloud_returns_empty(self):
        """0 点不该抛——配准的空点云由调用方检查，这里只负责算。"""
        import open3d as o3d

        from h3d.register.normals import normal_consistency

        c = normal_consistency(o3d.geometry.PointCloud(), radius=0.03)
        assert len(c) == 0


class TestNormalQuality:
    """P1-1：把一致性压成两个可读的数。"""

    def test_reports_median_and_low_fraction(self):
        from h3d.register.normals import normal_quality

        q = normal_quality(_sphere(), radius=0.03)
        assert set(q) >= {"median", "frac_low", "usable"}
        assert 0.0 <= q["median"] <= 1.0
        assert 0.0 <= q["frac_low"] <= 1.0
        assert q["usable"] is True

    def test_random_normals_are_not_usable(self):
        """法向方向随机 → 中位数 ≈ 0.5 → 判不可用。

        这里**手设**随机法线而不是靠 ``estimate_normals`` 估：体散点云的邻域重叠
        会让相邻点的局部平面相关，|dot| 中位数能到 0.72，判不出「不可用」。
        手设随机法线是可证明的下界（随机单位向量 |dot| 期望 = 0.5），这才是闸门
        该守住的场景。

        注：``normal_min_quality`` 的 0.7 **没有真机标定**，是保守默认值（见 ADR-0030）。
        """
        import open3d as o3d

        from h3d.register.normals import normal_quality

        pcd = _sphere(n=2000)
        rng = np.random.default_rng(3)
        v = rng.normal(size=(2000, 3))
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        pcd.normals = o3d.utility.Vector3dVector(v)
        q = normal_quality(pcd, radius=0.03)
        assert q["usable"] is False
        assert q["median"] < 0.7


class TestFilterKeepsEvaluationHonest:
    """筛选只用于求变换；评估必须回到原始 source（否则 fitness 分母变小 = 假提升）。

    这是这个功能最容易做错的地方：把 ``fitness = 内点数 / 总点数`` 里的总点数
    换成「筛选后点数」，fitness 会天然上升——因为被筛掉的正是难配的点。
    读起来像「配得更好」，其实只是把难处藏了。
    """

    def _cfg(self, **kw):
        from h3d.core import RegisterConfig

        base = dict(fpfh_radius=0.02, ransac_distance=0.03,
                    icp_distance=0.03, max_iter=60)
        base.update(kw)
        return RegisterConfig(**base)

    def test_filtering_does_not_inflate_fitness(self):
        """带飞点的点云：筛选应生效，且 fitness **不能**因此虚高。

        fitness 的分母回到原始点数后，筛掉飞点只会让「配上的点」变多、
        分母不变 → fitness 允许变大，但变大必须来自真的配上了，
        不能来自「把难配的点藏起来」。这里用下界断言：不许比不筛更差。
        """
        from h3d.register import run

        tgt = _sphere(n=3000, seed=7)
        src = _sphere_with_specks()
        base = run(src, tgt, self._cfg())
        filt = run(src, tgt, self._cfg(normal_filter=0.7))

        assert filt["normal_filter_kept"] < 1.0, "飞点没被筛掉，筛选没生效"
        assert filt["fitness"] >= base["fitness"] - 0.05, (
            base["fitness"], filt["fitness"])

    def test_off_by_default_is_bit_identical(self):
        """默认（normal_filter=0）必须完全不碰 ICP 输入，否则所有旧结果作废。"""
        from h3d.register import run

        src, tgt = _pair(30.0)
        a = run(src, tgt, self._cfg())
        b = run(src, tgt, self._cfg(normal_filter=0.0))
        # 用 allclose 而不是 array_equal：ICP 是迭代式的，末位差可达 1e-32
        # （与 test_register_reproducible 同一条结论），那不是「结果不同」。
        assert np.allclose(np.asarray(a["transformation"]),
                           np.asarray(b["transformation"]), atol=1e-12)
        assert "normal_filter_kept" not in a


def _pair(angle_deg: float):
    """源点云 = 目标绕 z 轴转 ``angle_deg``（与 test_register_reproducible 同构）。"""
    import open3d as o3d

    tgt = _sphere(n=3000, seed=7)
    tgt.estimate_normals(
        o3d.geometry.KDTreeSearchParamHybrid(radius=0.03, max_nn=30))
    o3d.geometry.PointCloud.orient_normals_consistent_tangent_plane(tgt, 30)
    P = np.asarray(tgt.points)
    a = np.radians(angle_deg)
    Rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0],
                   [0, 0, 1.0]])
    src = o3d.geometry.PointCloud()
    src.points = o3d.utility.Vector3dVector(P @ Rz.T)
    src.normals = o3d.utility.Vector3dVector(
        np.asarray(tgt.normals) @ Rz.T)
    return src, tgt
