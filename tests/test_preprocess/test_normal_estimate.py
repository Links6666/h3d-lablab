"""preprocess.normal_estimate 单元测试：法线估计与一致化。"""
import numpy as np
import open3d as o3d
import pytest

from h3d.preprocess import estimate_normals, orient_normals_consistent
from h3d.core import InputDataError


def _sphere(n=2000):
    """单位球面采样点（法线良好的测试数据）。"""
    rng = np.random.default_rng(0)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(v)
    return pcd


def test_estimate_normals_adds_normals():
    pcd = _sphere()
    out = estimate_normals(pcd, max_nn=30)
    assert out.has_normals()
    assert len(np.asarray(out.normals)) == 2000


def test_estimate_normals_with_radius():
    pcd = _sphere(500)
    out = estimate_normals(pcd, max_nn=30, radius=0.2)
    assert out.has_normals()


def test_orient_normals_consistent_keeps_count():
    pcd = _sphere(500)
    pcd = estimate_normals(pcd, max_nn=30)
    out = orient_normals_consistent(pcd, k=15)
    assert len(np.asarray(out.normals)) == 500


def test_orient_requires_normals_raises():
    pcd = _sphere(100)  # 未估计法线
    with pytest.raises(InputDataError) as ei:
        orient_normals_consistent(pcd)
    assert ei.value.code == "E100"


def test_estimate_empty_input_raises():
    pcd = o3d.geometry.PointCloud()
    with pytest.raises(InputDataError):
        estimate_normals(pcd)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
