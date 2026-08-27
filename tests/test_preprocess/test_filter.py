"""preprocess.filter 单元测试：统计 / 半径离群去噪。"""
import numpy as np
import open3d as o3d
import pytest

from h3d.preprocess import (
    radius_outlier_removal,
    statistical_outlier_removal,
)
from h3d.core import InputDataError


def _pcd(pts):
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(np.asarray(pts, dtype=np.float64))
    return pcd


def test_statistical_outlier_removes_far_points():
    rng = np.random.default_rng(0)
    body = rng.uniform(0.0, 1.0, size=(2000, 3))
    outliers = np.array([[50.0, 50.0, 50.0], [50.0, 50.0, 50.1],
                         [-50.0, -50.0, -50.0]])
    pcd = _pcd(np.vstack([body, outliers]))
    filtered = statistical_outlier_removal(pcd, nb_neighbors=20, std_ratio=2.0)
    assert len(filtered.points) <= 2000  # 3 个飞点被移除
    # 无点落在远处
    assert np.max(np.abs(np.asarray(filtered.points))) < 10.0


def test_radius_outlier_removes_isolated_points():
    rng = np.random.default_rng(1)
    body = rng.uniform(0.0, 0.3, size=(5000, 3))  # 密集主体
    iso = np.array([[10.0, 10.0, 10.0], [10.0, 10.0, 10.05]])  # 孤立飞点
    pcd = _pcd(np.vstack([body, iso]))
    filtered = radius_outlier_removal(pcd, nb_points=8, radius=0.05)
    assert len(filtered.points) > 0
    # 孤立飞点被移除（无点落在远处）
    assert np.max(np.abs(np.asarray(filtered.points))) < 5.0


def test_statistical_outlier_empty_input_raises():
    pcd = _pcd(np.zeros((0, 3)))
    with pytest.raises(InputDataError) as ei:
        statistical_outlier_removal(pcd)
    assert ei.value.code == "E100"


def test_radius_outlier_empty_input_raises():
    pcd = _pcd(np.zeros((0, 3)))
    with pytest.raises(InputDataError):
        radius_outlier_removal(pcd)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
