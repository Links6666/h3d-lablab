"""tests.test_preprocess.test_background : 背景/碎片剔除（主簇保留）。"""
from __future__ import annotations

import numpy as np
import open3d as o3d
import pytest

from h3d.preprocess.background import remove_background


def _pcd(points):
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(np.asarray(points, dtype=np.float64))
    return pcd


def _main_blob(n=4000, rng=None):
    """主目标：一个小立方体附近的点簇。"""
    rng = rng or np.random.default_rng(0)
    return rng.normal(scale=0.05, size=(n, 3))


def _far_fragments(n=600, rng=None):
    """远处零散碎片：各 60 点，分布在 8 个远离主目标的角落。"""
    rng = rng or np.random.default_rng(1)
    blocks = []
    for i in range(8):
        c = np.array([2.0 + i * 0.5, 1.5 * (i % 3), -2.5 + 0.4 * i])
        blocks.append(c + rng.normal(scale=0.02, size=(n // 8, 3)))
    return np.vstack(blocks)


class TestRemoveBackground:
    def test_keeps_main_cluster_drops_fragments(self):
        """主簇必须完整保留，远处碎片必须丢弃——这是"器物缩成小点"的解药。

        判据锁行为本质：结果点的包围盒必须收回到主目标尺度，且点数≈主簇点数。
        """
        main = _main_blob()
        frag = _far_fragments()
        pcd = _pcd(np.vstack([main, frag]))

        before_diag = float(np.linalg.norm(
            np.asarray(pcd.points).max(0) - np.asarray(pcd.points).min(0)))
        out, m = remove_background(pcd)

        after_diag = float(np.linalg.norm(
            np.asarray(out.points).max(0) - np.asarray(out.points).min(0)))
        assert after_diag < before_diag * 0.2, "包围盒没有收回主目标尺度"
        assert m["bg_kept_clusters"] == 1
        assert 0.8 < m["bg_kept_ratio"] <= 1.0
        assert m["bg_rollback"] is False
        assert len(out.points) == m["bg_kept_points"]
        # 主簇的点必须全部还在（一个都不许少）
        assert len(out.points) >= len(main) * 0.95

    def test_rollback_when_over_removal(self):
        """剔除过度必须整体回滚：宁可留背景，也不能把文物剔没了。"""
        main = _main_blob(n=200)
        frag = _far_fragments(n=4000)      # 碎片远多于主目标
        pts = np.vstack([main, frag])
        pcd = _pcd(pts)

        out, m = remove_background(pcd)
        if m["bg_rollback"]:
            assert len(out.points) == len(pts) or m["bg_kept_ratio"] >= 0.2
        else:
            assert m["bg_kept_ratio"] >= 0.2

    def test_small_cloud_is_passthrough(self):
        """少于 200 点直接原样返回（避免在小样本上瞎剔）。"""
        pcd = _pcd(np.zeros((10, 3)))
        out, m = remove_background(pcd)
        assert len(out.points) == 10
        assert m["bg_kept_ratio"] == 1.0
        assert "过小" in m["bg_note"]

    def test_plane_removal_off_by_default(self):
        """默认不剔平面：贴合"背景是碎片而非整块衬板"的实机结论。"""
        rng = np.random.default_rng(3)
        plane = np.stack([rng.uniform(-5, 5, 3000),
                          rng.uniform(-5, 5, 3000),
                          np.zeros(3000)], axis=1)
        pcd = _pcd(plane)
        out, m = remove_background(pcd)
        assert m["bg_removed_planes"] == 0

    def test_plane_removal_when_explicitly_enabled(self):
        """显式开启时，撑满场景的平面应被剔掉（且剩余比例足够时不回滚）。"""
        rng = np.random.default_rng(4)
        plane = np.stack([rng.uniform(-5, 5, 1200),
                          rng.uniform(-5, 5, 1200),
                          rng.normal(scale=1e-4, size=1200)], axis=1)
        blob = rng.normal(scale=0.2, size=(2000, 3)) + np.array([0, 0, 1.0])
        pcd = _pcd(np.vstack([plane, blob]))

        out, m = remove_background(pcd, max_planes=1)
        assert m["bg_removed_planes"] == 1
        assert m["bg_rollback"] is False
        assert len(out.points) < len(pcd.points)
        # 平面被剔掉后，点云不应再横跨 ±5
        ext = np.asarray(out.points).max(0) - np.asarray(out.points).min(0)
        assert float(ext[0]) < 5.0

    def test_rollback_protects_artifact(self):
        """平面远多于目标时宁可回滚，也不能只留下一点残渣。"""
        rng = np.random.default_rng(5)
        plane = np.stack([rng.uniform(-5, 5, 4000),
                          rng.uniform(-5, 5, 4000),
                          rng.normal(scale=1e-4, size=4000)], axis=1)
        blob = rng.normal(scale=0.2, size=(400, 3)) + np.array([0, 0, 1.0])
        pcd = _pcd(np.vstack([plane, blob]))

        out, m = remove_background(pcd, max_planes=1)
        assert m["bg_rollback"] is True
        assert len(out.points) == len(pcd.points), "回滚必须原样返回"


class TestDensityAdaptiveClustering:
    """2026-10-23 实机：稠密点云上 DBSCAN 半径必须与点间距挂钩。

    真实 fused_dense.ply：677274 点、MPS=0.0030、diag=15.35，旧实现
    eps = 0.02×diag = 0.307 = 102×MPS → 每点邻域 279 个点 → 邻域表 1.9 亿条边
    → 实测 4 分 44 秒 RSS 已 4.6 GB 且未结束（用户实机 10 GB / 50 分钟）。
    这一组用例锁死"半径以 MPS 为基准"和"聚类规模有上限"这两条本质行为。
    """

    def _dense_cloud(self, n=80000, rng=None):
        """稠密平面 + 远处碎片：模拟 SfM 稠密重建的高密度点云。"""
        rng = rng or np.random.default_rng(11)
        # 主目标：半径 1 的球面采样（n 点，间距 ~1/sqrt(n)）
        u = rng.normal(size=(n, 3))
        u /= np.linalg.norm(u, axis=1, keepdims=True)
        main = u * 1.0
        # 远处碎片：4 块各 500 点，距主目标 8 个单位（把包围盒撑到 ~18）
        frag = np.vstack([np.array([6.0 + i * 2.0, 3.0, -4.0])
                          + rng.normal(scale=0.05, size=(500, 3))
                          for i in range(4)])
        return np.vstack([main, frag])

    def test_eps_is_capped_by_mean_point_spacing(self):
        """eps 必须被 k×MPS 压住，而不是跟着包围盒无限放大。"""
        pcd = _pcd(self._dense_cloud())
        _out, m = remove_background(pcd, cluster_eps_k=8.0)

        assert m["bg_cluster_mps"] > 0
        # 允许 1% 数值容差（MPS 是在工作副本上重算的）
        assert m["bg_eps_mps_ratio"] <= 8.0 * 1.01, (
            f"eps 是 {m['bg_eps_mps_ratio']}×MPS，超过配置的 8×")

    def test_cluster_workcopy_is_capped(self):
        """聚类规模必须有上限：点数再多也不让 DBSCAN 见全量。"""
        pcd = _pcd(self._dense_cloud(n=60000))
        _out, m = remove_background(pcd, max_cluster_points=5000)

        assert m["bg_cluster_points"] <= 5000, "聚类工作副本没有被压到上限以内"
        assert m["bg_backprojected"] == 1, "压过上限就必须回投标签"
        assert m["bg_cluster_edges"] > 0

    def test_backprojection_keeps_full_resolution(self):
        """标签回投后保留的是**全分辨率**点，不是降采样副本。"""
        cloud = self._dense_cloud(n=40000)
        pcd = _pcd(cloud)
        out, m = remove_background(pcd, max_cluster_points=4000)

        # 主球面 40000 点应几乎全部保留（碎片被剔）
        assert m["bg_kept_ratio"] > 0.85
        assert len(out.points) > m["bg_cluster_points"] * 5, (
            "回投后点数应远多于工作副本，说明保留的是全分辨率点")
        assert len(out.points) <= len(cloud)

    def test_edge_budget_shrinks_radius_not_silently_skips(self):
        """邻域表超预算时应收紧半径继续做，而不是直接放弃背景剔除。"""
        # 极密点云：同一小球里塞 3 万点，MPS 极小
        rng = np.random.default_rng(13)
        dense = rng.normal(scale=0.001, size=(30000, 3))
        frag = np.array([[3.0, 0.0, 0.0]]) + rng.normal(
            scale=0.001, size=(200, 3))
        pcd = _pcd(np.vstack([dense, frag]))

        out, m = remove_background(pcd)
        assert m["bg_clusters"] > 0, f"聚类被跳过了：{m['bg_note']}"
        assert m["bg_kept_ratio"] < 1.0, "远处碎片应被剔掉"
        assert len(out.points) >= len(dense) * 0.9
