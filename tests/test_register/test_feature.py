"""register.feature 单元测试：FPFH 特征提取与法线补齐。"""
import numpy as np
import open3d as o3d
import pytest

from h3d.register import compute_fpfh, ensure_normals
from h3d.core import InputDataError


def _bumpy_sphere(n=2000, seed=0):
    """带凹凸的球面采样（非对称，提供可匹配的 FPFH 局部特征）。"""
    rng = np.random.default_rng(seed)
    u = rng.uniform(0, 2 * np.pi, n)
    v = np.arccos(2 * rng.uniform(0, 1, n) - 1)
    r = 1.0 + 0.2 * np.sin(3 * u) * np.cos(2 * v) + 0.15 * np.cos(5 * u) * np.sin(3 * v)
    pts = np.vstack([r * np.sin(v) * np.cos(u),
                     r * np.sin(v) * np.sin(u),
                     r * np.cos(v)]).T
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts)
    return pcd


def test_compute_fpfh_returns_feature():
    pcd = _bumpy_sphere()
    f = compute_fpfh(pcd, radius=0.2)
    # FPFH 描述子为 33 维直方图
    assert np.asarray(f.data).shape[0] == 33


def test_compute_fpfh_adds_normals_if_missing():
    pcd = _bumpy_sphere()
    assert not pcd.has_normals()
    compute_fpfh(pcd, radius=0.2)
    assert pcd.has_normals()  # 自动补齐法线


def test_ensure_normals_idempotent():
    pcd = _bumpy_sphere(500)
    ensure_normals(pcd, radius=0.2)
    n1 = np.asarray(pcd.normals).copy()
    ensure_normals(pcd, radius=0.2)  # 已有法线不再重估
    n2 = np.asarray(pcd.normals)
    assert np.array_equal(n1, n2)


def test_compute_fpfh_empty_raises_e100():
    pcd = o3d.geometry.PointCloud()
    with pytest.raises(InputDataError) as ei:
        compute_fpfh(pcd, radius=0.2)
    assert ei.value.code == "E100"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
