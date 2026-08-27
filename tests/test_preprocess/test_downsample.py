"""preprocess.downsample 单元测试：体素降采样。"""
import numpy as np
import open3d as o3d
import pytest

from h3d.preprocess import voxel_down_sample
from h3d.core import InputDataError


def _pcd(pts):
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(np.asarray(pts, dtype=np.float64))
    return pcd


def test_voxel_downsample_reduces_points():
    rng = np.random.default_rng(0)
    pts = rng.uniform(0.0, 1.0, size=(20000, 3))
    down = voxel_down_sample(_pcd(pts), voxel_size=0.05)
    assert len(down.points) < 20000
    assert len(down.points) > 0


def test_voxel_downsample_keeps_shape_bounds():
    rng = np.random.default_rng(1)
    pts = rng.uniform(0.0, 1.0, size=(10000, 3))
    down = voxel_down_sample(_pcd(pts), voxel_size=0.05)
    arr = np.asarray(down.points)
    # 包围盒仍应落在 [0,1] 附近（形状保留）
    assert arr.min() >= -0.05 and arr.max() <= 1.05


def test_voxel_downsample_empty_input_raises():
    with pytest.raises(InputDataError) as ei:
        voxel_down_sample(_pcd(np.zeros((0, 3))), 0.05)
    assert ei.value.code == "E100"


def test_voxel_downsample_invalid_voxel_raises():
    pcd = _pcd(np.random.default_rng(2).uniform(0, 1, (100, 3)))
    with pytest.raises(InputDataError):
        voxel_down_sample(pcd, 0.0)
    with pytest.raises(InputDataError):
        voxel_down_sample(pcd, -1.0)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
