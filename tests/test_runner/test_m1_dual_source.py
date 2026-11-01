"""tests.test_runner.test_m1_dual_source : 双源合并前必须先问「是不是同一个坐标系」。

真机 2026-11-01（覆盖率 21% 的根因）
-----------------------------------
`D:/h3d_relic_test` 的 `00_raw` 是激光件（对角 6.5、中心 (2.6,-0.1,-4.8)），
`10_dense/fused_dense.ply` 是**照片重建**（对角 13.7、中心 (-0.1,3.1,0.6)）——
两者相距约一个对角线，是完全不同的两套坐标系。

M1 把它们合并了，于是：

| 环节 | 结果 |
|---|---|
| `10_clean` | 对角 6.5 → **13.8**（两个物体 + 中间空隙） |
| `30_dense` 泊松 | 对角 **20.97**，把两块包成一个壳 |
| M4 投影 | `in_frame_fraction` **45.8%**，coverage **21%** |

干净输入（只有激光件）重跑同一条链：`30_dense` 对角 6.78、`in_frame` **100%**。

为什么原有的 `novel` 判据拦不住
----------------------------
`dense_novel_ratio` 问的是「dense 点距既有几何有多远」，用来识别**冗余**。
但两源坐标系不同时，**每个 dense 点都"远离"激光件** → novel 反而很高
（真机 Otsu 切出 **27.19%** > 15% 阈值）→ 判「有增量信息」→ 合并。

也就是说：novel 在**异源**场景下给的是"两物体间距"，不是"新覆盖率"。
判据的语义在这里失效了，不是阈值调错。

正确的闸门是先问「同不同系」——`already_aligned` 对真机两源给出
`aligned=False / 中位距离 91.56% 对角线`，一判就准。
"""
from __future__ import annotations

import os

import numpy as np
import open3d as o3d
import pytest


def _blob(center=(0.0, 0.0, 0.0), radius=0.05, n=4000, seed=0):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    return v * radius + np.asarray(center)


def _save(path, pts, color=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pc = o3d.geometry.PointCloud()
    pc.points = o3d.utility.Vector3dVector(np.asarray(pts, dtype=np.float64))
    if color:
        pc.paint_uniform_color([0.6, 0.5, 0.4])
    o3d.io.write_point_cloud(path, pc)


def _project(tmp_path, base_pts, dense_pts):
    """造一个最小项目：00_raw/<item>/scan.ply + 10_dense/<item>/fused_dense.ply。"""
    root = tmp_path / "阶段成果"
    _save(str(root / "00_raw" / "r1" / "scan.ply"), base_pts)
    _save(str(root / "10_dense" / "r1" / "fused_dense.ply"), dense_pts)
    return root


def _cfg(root):
    from h3d.core import PipelineConfig

    cfg = PipelineConfig()
    cfg.project.raw_dir = str(root / "00_raw")
    cfg.runner.manifest = str(root / "90_meta" / "m.json")
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
    cfg.report.enabled = False
    return cfg


def _task(root):
    from h3d.runner.task import Task

    return Task(item_id="r1",
                raw_paths=[str(root / "00_raw" / "r1" / "scan.ply")],
                root=str(root))


class TestDifferentEpochIsRejected:
    def test_far_away_dense_is_not_merged(self, tmp_path):
        """两源相距一个对角线 → 绝不能合并。"""
        from h3d.runner import _stage_preprocess

        base = _blob((0, 0, 0))
        dense = _blob((1.0, 1.0, 1.0))      # 平移 1.7 倍直径
        root = _project(tmp_path, base, dense)
        res = _stage_preprocess(_task(root), _cfg(root))

        assert res.metrics.get("dense_merged") is False, (
            "异源 dense 被合并了 —— 几何会被撑成两个物体的壳")
        assert "epoch" in str(res.metrics.get("dense_reject", "")).lower() or \
               "坐标" in str(res.metrics.get("dense_skip_reason", "")), res.metrics

    def test_clean_geometry_stays_clean(self, tmp_path):
        """不合并时，10_clean 的尺度必须仍等于激光件本身。"""
        from h3d.runner import _stage_preprocess

        base = _blob((0, 0, 0), radius=0.05)
        dense = _blob((1.0, 1.0, 1.0), radius=0.05)
        root = _project(tmp_path, base, dense)
        _stage_preprocess(_task(root), _cfg(root))

        out = str(root / "10_clean" / "r1" / "r1_s01.ply")
        pts = np.asarray(o3d.io.read_point_cloud(out).points)
        diag = float(np.linalg.norm(pts.max(0) - pts.min(0)))
        # 激光件半径 0.05 → 直径 0.1，留 3 倍余量（降采样会略收）
        assert diag < 0.35, f"10_clean 对角 {diag:.3f} 远超激光件本身（0.1）"


class TestSameEpochKeepsOldBehaviour:
    # 「同系冗余 dense 仍按 novel 跳过」这条没有稳定用例：合成数据上
    # `online_novel_threshold` 的 Otsu 切分会把采样间距当成增量信息，
    # novel 恒偏高。真机上 MPS 小、点密，行为由既有测试覆盖。
    # 这里只保证**同系时不被新闸门误拦**（下面这条）：
    def test_same_epoch_is_not_rejected_by_gate(self, tmp_path):
        """同系的 dense 不该被「异源」闸门拦下（拦错方向同样是缺陷）。"""
        from h3d.runner import _stage_preprocess

        base = _blob((0, 0, 0), seed=0)
        dense = base.copy() + np.array([0.0, 0.0, 0.001])
        root = _project(tmp_path, base, dense)
        res = _stage_preprocess(_task(root), _cfg(root))
        assert res.metrics.get("dense_reject") != "different_epoch", res.metrics
        assert res.metrics.get("dense_same_epoch") is True
