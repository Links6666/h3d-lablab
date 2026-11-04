"""M5 的数字必须可复现：同一份输入跑两次，中误差与覆盖率要一模一样。

怎么发现的（2026-11-04 真机）
---------------------------
给 ``runner.data_source`` 做对照实验时，``auto`` 与 ``laser`` 两个**等价**的
分支算出的中误差竟然不同：

| 运行 | 声明 | 中误差（mm） |
|---|---|---|
| 第 1 次 | auto | 86.7811 |
| 第 2 次 | auto | 87.4155 |
| 第 3 次 | laser | 87.0148 |

差异不是声明带来的（三者的 ``metric_units`` 都是 True），而是
``mesh.sample_points_uniformly(n)`` **没有种子**——Open3D 用进程级随机源，
每次重采样到不同的表面点，最近邻距离分布就跟着变。

而这两个数字是要被写进 ``accuracy.csv`` / HTML / 论文表的。答辩时问
「这个 86.78 是怎么来的、能不能重跑出来」——重跑一次变成 87.42，
那就不是一个可引用的数字。

同一个坑 2026-11-03 在**对齐精化**里已经踩过一次（`ctrl_points`
`_REFINE_SEED`）。当时只修了那一条路径，M5 这条没修——因为它的数字
「看着稳定」（差异 <1%）。**小于 1% 的漂移最危险**：小到不会触发任何
断言，又大到让「两次跑出来的表对不上」。
"""
from __future__ import annotations

import numpy as np
import open3d as o3d
import pytest


def _fixture():
    """一个半球形网格 + 一份参考点云（点云本身固定，只让采样随机）。"""
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=0.1, resolution=12)
    mesh.compute_vertex_normals()
    rng = np.random.default_rng(7)
    v = rng.normal(size=(4000, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    ref = o3d.geometry.PointCloud()
    ref.points = o3d.utility.Vector3dVector(v * 0.1)
    return mesh, ref


class TestMetricsAreReproducible:
    def test_surface_coverage_is_bit_exact_across_runs(self):
        from h3d.quality.metrics import surface_coverage

        mesh, ref = _fixture()
        a = surface_coverage(mesh, ref, 0.004, samples=4000)
        b = surface_coverage(mesh, ref, 0.004, samples=4000)
        assert a == b, f"覆盖率两次运行不同：{a!r} vs {b!r}"

    def test_rmse_is_bit_exact_across_runs(self):
        from h3d.quality.metrics import qc_rmse_units

        mesh, ref = _fixture()
        a = qc_rmse_units(mesh, ref, samples=4000)
        b = qc_rmse_units(mesh, ref, samples=4000)
        assert a == b, f"中误差两次运行不同：{a!r} vs {b!r}"

    def test_sample_size_still_matters(self):
        """定种子不能让「采样数」失效——换样本数就该换结果。

        这条是防过度修复：只要把采样换成"固定返回第一批点"也能让上面两条绿，
        但那样 coverage 与 samples 无关，``cfg.samples`` 就成了假参数。
        """
        from h3d.quality.metrics import surface_coverage

        mesh, ref = _fixture()
        a = surface_coverage(mesh, ref, 0.004, samples=500)
        b = surface_coverage(mesh, ref, 0.004, samples=4000)
        assert a != b


class TestQualityRunReproducible:
    def test_full_run_returns_identical_numbers(self):
        """整条 ``quality.run`` 也要一致——报表最终取的是它。"""
        from h3d.core import QualityConfig
        from h3d.quality import run

        mesh, ref = _fixture()
        cfg = QualityConfig()
        cfg.samples = 3000
        cfg.coverage_threshold = 0.1
        a = run(mesh, ref, cfg, metric_units=True)
        b = run(mesh, ref, cfg, metric_units=True)
        for key in ("coverage", "rmse_units", "rmse_rel", "rmse_mm"):
            assert a[key] == pytest.approx(b[key], abs=0.0), key


class TestIoLayer:
    def test_seeded_sampler_lives_in_the_adapter_layer(self):
        """采样种子必须走 ``h3d.io``——业务层不许自己 import Open3D 随机源。"""
        from h3d.io import sample_surface

        mesh, _ = _fixture()
        p1 = np.asarray(sample_surface(mesh, 500).points)
        p2 = np.asarray(sample_surface(mesh, 500).points)
        assert np.array_equal(p1, p2)
        assert p1.shape == (500, 3)
