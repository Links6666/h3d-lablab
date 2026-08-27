"""tests.test_mesh.test_reconstruct : 三种重建算法单元测试。"""
from __future__ import annotations

import numpy as np
import open3d as o3d
import pytest

from h3d.mesh import (
    alpha_shape_reconstruct,
    ball_pivoting_reconstruct,
    poisson_reconstruct,
)
from h3d.mesh.postprocess import mesh_metrics


def _sphere_pcd(n=2000, seed=0):
    """带一致法线的非对称球面点云（Poisson/Ball 通用）。"""
    rng = np.random.default_rng(seed)
    u = rng.uniform(0, 2 * np.pi, n)
    v = np.arccos(2 * rng.uniform(0, 1, n) - 1)
    r = 1.0 + 0.2 * np.sin(3 * u) * np.cos(2 * v) + 0.15 * np.cos(5 * u) * np.sin(3 * v)
    pts = np.vstack([r * np.sin(v) * np.cos(u), r * np.sin(v) * np.sin(u), r * np.cos(v)]).T
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts)
    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=0.1, max_nn=30))
    pcd.orient_normals_consistent_tangent_plane(15)
    return pcd


def _uniform_pcd(n=5000, seed=1):
    """从网格均匀采样的点云（Ball-Pivoting 需要均匀分布）。"""
    mesh = o3d.geometry.TriangleMesh.create_box(1.0, 1.0, 1.0)
    return mesh.sample_points_uniformly(n, use_triangle_normal=True)


class TestPoisson:
    def test_reconstruct_basic(self):
        mesh = poisson_reconstruct(_sphere_pcd(), depth=8)
        m = mesh_metrics(mesh)
        assert m["n_vertices"] > 0
        assert m["n_triangles"] > 0
        assert mesh.has_vertex_normals()  # 清理链后已重算法线

    def test_density_crop_reduces_vertices(self):
        """密度裁剪剔除低密度碎面（设计书 TC-303）：ratio 越大保留越少。"""
        pcd = _sphere_pcd()
        full = poisson_reconstruct(pcd, depth=8, density_ratio=0.0)
        cropped = poisson_reconstruct(pcd, depth=8, density_ratio=0.5)
        assert len(cropped.vertices) < len(full.vertices)

    def test_depth_changes_detail(self):
        """depth 越大细节越多（面数更多）。"""
        pcd = _sphere_pcd(n=3000)
        shallow = poisson_reconstruct(pcd, depth=6, density_ratio=0.0)
        deep = poisson_reconstruct(pcd, depth=9, density_ratio=0.0)
        assert len(deep.triangles) > len(shallow.triangles)


class TestBallPivoting:
    def test_reconstruct_uniform_cloud(self):
        mesh = ball_pivoting_reconstruct(_uniform_pcd(), radii=[0.05, 0.1, 0.2])
        assert len(mesh.vertices) > 0
        assert len(mesh.triangles) > 0


class TestAlphaShape:
    def test_reconstruct_basic(self):
        mesh = alpha_shape_reconstruct(_sphere_pcd(), alpha=0.1)
        assert len(mesh.vertices) > 0
        assert len(mesh.triangles) > 0

    def test_both_alphas_produce_mesh(self):
        """不同 alpha 均能产出非空网格（顶点数取决于点云分布，不做大小断言）。"""
        pcd = _sphere_pcd()
        coarse = alpha_shape_reconstruct(pcd, alpha=0.3)
        fine = alpha_shape_reconstruct(pcd, alpha=0.05)
        assert len(coarse.vertices) > 0 and len(coarse.triangles) > 0
        assert len(fine.vertices) > 0 and len(fine.triangles) > 0
