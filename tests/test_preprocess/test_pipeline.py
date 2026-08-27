"""preprocess.run 组合流水线单元测试：降采样 → 去噪 → 法线（配置驱动）。"""
import numpy as np
import open3d as o3d
import pytest

from h3d.core import PreprocessConfig
from h3d.preprocess import run


def _pcd_with_outliers(n_body=8000):
    rng = np.random.default_rng(3)
    body = rng.uniform(0.0, 1.0, size=(n_body, 3))
    outliers = np.array([[40.0, 40.0, 40.0], [40.0, 40.0, 40.1],
                         [-40.0, -40.0, -40.0]])
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(
        np.vstack([body, outliers]).astype(np.float64))
    return pcd


def test_run_pipeline_reduces_and_normalizes():
    pcd = _pcd_with_outliers()
    n_before = len(pcd.points)
    out = run(pcd, PreprocessConfig())  # 默认 voxel=0.005, std=2.0, k=30
    assert out.has_normals()
    assert len(out.points) < n_before            # 降采样 + 去噪
    assert np.max(np.abs(np.asarray(out.points))) < 5.0  # 飞点已去除


def test_run_pipeline_config_driven():
    cfg = PreprocessConfig(voxel_down_sample=0.02, remove_outlier_std=2.0,
                           normal_estimate_k=20)
    out = run(_pcd_with_outliers(20000), cfg)
    assert out.has_normals()
    # 体素更大 → 点数更少
    assert len(out.points) < 20000


def test_run_pipeline_roundtrip_with_io(tmp_path):
    """端到端：io 读取 → preprocess.run → io 写出。"""
    from h3d.io import load_point_cloud, save_point_cloud

    pcd = _pcd_with_outliers(5000)
    src = tmp_path / "raw.ply"
    save_point_cloud(pcd, str(src))

    loaded = load_point_cloud(str(src))
    out = run(loaded, PreprocessConfig(voxel_down_sample=0.02))
    dst = tmp_path / "clean.ply"
    save_point_cloud(out, str(dst))
    assert dst.exists()
    assert len(load_point_cloud(str(dst)).points) < 5000


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
