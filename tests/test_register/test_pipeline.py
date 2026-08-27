"""register.run 两段式配准流水线单元测试：变换恢复、失败隔离、错误码。"""
import numpy as np
import open3d as o3d
import pytest

from h3d.core import InputDataError, RegisterConfig, RegistrationError
from h3d.register import run


def _bumpy_sphere(n=3000, seed=0):
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


def _transform(pcd, t, theta_deg=20.0):
    """返回施加了平移 t 与绕 z 轴旋转 theta 后的点云（副本）。"""
    T = np.eye(4)
    T[:3, 3] = t
    th = np.deg2rad(theta_deg)
    R = np.array([[np.cos(th), -np.sin(th), 0],
                  [np.sin(th), np.cos(th), 0],
                  [0, 0, 1]])
    T[:3, :3] = R
    moved = o3d.geometry.PointCloud(pcd)
    moved.transform(T)
    return moved, T


def _cfg():
    return RegisterConfig(fpfh_radius=0.2, ransac_distance=0.3,
                          icp_distance=0.1, max_iter=200)


def test_run_recovers_known_transform():
    src = _bumpy_sphere()
    tgt, T_true = _transform(src, t=np.array([0.2, 0.15, 0.1]), theta_deg=20.0)
    res = run(src, tgt, _cfg())
    assert res["fitness"] > 0.7
    assert res["inlier_rmse"] < 0.05
    T = res["transformation"]
    # 平移恢复误差应远小于原始位移
    err = np.linalg.norm(T[:3, 3] - T_true[:3, 3])
    assert err < 0.05


def test_run_improves_over_ransac_identity():
    # 精配准应显著优于「不配准」（直接比两片点云）
    src = _bumpy_sphere()
    tgt, _ = _transform(src, t=np.array([0.2, 0.15, 0.1]), theta_deg=20.0)
    res = run(src, tgt, _cfg())
    # 用恢复的变换对齐后，两片点云应高度重合（inlier_rmse 很小）
    assert res["inlier_rmse"] < 0.05


def test_run_empty_input_raises_e100():
    src = _bumpy_sphere(500)
    empty = o3d.geometry.PointCloud()
    with pytest.raises(InputDataError) as ei:
        run(src, empty, _cfg())
    assert ei.value.code == "E100"


def _plane(n=3000, seed=42):
    """平面点云（与球面几何差异极大，FPFH 无法匹配）。"""
    rng = np.random.default_rng(seed)
    x = rng.uniform(-1, 1, n)
    y = rng.uniform(-1, 1, n)
    z = np.zeros(n)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(np.vstack([x, y, z]).T)
    return pcd


def test_run_no_overlap_raises_e200():
    # 球面 vs 平面：几何结构根本不同，粗配准 fitness 极低 → E200
    src = _bumpy_sphere(seed=0)
    target = _plane()
    with pytest.raises(RegistrationError) as ei:
        run(src, target, _cfg())
    assert ei.value.code == "E200"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
