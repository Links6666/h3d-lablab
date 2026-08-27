"""tests.test_mesh.test_pipeline : mesh.run 组合重建流水线测试。"""
from __future__ import annotations

import numpy as np
import open3d as o3d
import pytest

from h3d.core import ConfigError, InputDataError, MeshConfig
from h3d.mesh import mesh_metrics, run


def _sphere_pcd(n=2000, seed=0, with_normals=True):
    rng = np.random.default_rng(seed)
    u = rng.uniform(0, 2 * np.pi, n)
    v = np.arccos(2 * rng.uniform(0, 1, n) - 1)
    r = 1.0 + 0.2 * np.sin(3 * u) * np.cos(2 * v)
    pts = np.vstack([r * np.sin(v) * np.cos(u), r * np.sin(v) * np.sin(u), r * np.cos(v)]).T
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts)
    if with_normals:
        pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=0.1, max_nn=30))
        pcd.orient_normals_consistent_tangent_plane(15)
    return pcd


class TestRunPipeline:
    def test_poisson_default(self):
        mesh = run(_sphere_pcd(), MeshConfig())
        m = mesh_metrics(mesh)
        assert m["n_vertices"] > 0 and m["n_triangles"] > 0

    def test_alpha_shape_method(self):
        mesh = run(_sphere_pcd(), MeshConfig(method="alpha_shape", alpha=0.08))
        assert len(mesh.vertices) > 0

    def test_simplify_applied(self):
        cfg = MeshConfig(simplify_target=500)
        mesh = run(_sphere_pcd(), cfg)
        assert len(mesh.triangles) <= 600

    def test_fill_holes_applied(self):
        mesh = run(_sphere_pcd(), MeshConfig(fill_holes=True))
        assert len(mesh.vertices) > 0

    def test_missing_normals_auto_estimated(self):
        """设计书 §34.4：法向缺失 -> 先估计法向再重建。"""
        pcd = _sphere_pcd(with_normals=False)
        assert not pcd.has_normals()
        mesh = run(pcd, MeshConfig())
        assert len(mesh.vertices) > 0

    def test_empty_input_raises_e100(self):
        with pytest.raises(InputDataError) as ei:
            run(o3d.geometry.PointCloud(), MeshConfig())
        assert ei.value.code == "E100"

    def test_invalid_method_rejected_by_config(self):
        # choices 校验在配置层拦截（E001）
        with pytest.raises(ConfigError):
            MeshConfig(method="marching_cubes")
