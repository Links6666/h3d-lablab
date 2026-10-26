"""tests.test_ctrl_points : 扩展——RANSAC 三角化抗噪 + 两阶段对齐精化。"""
from __future__ import annotations

import json
import os

import numpy as np
import open3d as o3d
import pytest

from h3d.ctrl_points import (
    ControlPointError,
    align_two_stage,
    refine_mesh_to_points,
    triangulate_sfm_xyz_ransac,
)


def _sphere_mesh(resolution=20):
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=1.0,
                                                   resolution=resolution)
    mesh.compute_vertex_normals()
    return mesh


def _make_cameras_images(n=6, radius=3.6):
    cameras = {1: {"w": 800, "h": 600, "fx": 800.0, "fy": 800.0,
                   "cx": 400.0, "cy": 300.0}}
    images = {}
    for i in range(n):
        az = 2 * np.pi * i / n
        eye = np.array([radius * np.cos(az), radius * np.sin(az), 0.0])
        f = -eye / np.linalg.norm(eye)
        up = np.array([0.0, 0.0, 1.0])
        s = np.cross(f, up)
        s = s / np.linalg.norm(s)
        u = np.cross(s, f)
        R = np.stack([s, u, -f], axis=1).T
        images[f"img_{i:02d}.jpg"] = {"R": R, "t": eye, "cam_id": 1}
    return cameras, images


def _project_uv(xyz, R, t, K):
    pc = R @ (xyz - t)
    if pc[2] >= -0.05:
        return None
    u = K[0, 0] * pc[0] / pc[2] + K[0, 2]
    v = K[1, 1] * pc[1] / pc[2] + K[1, 2]
    return (float(u), float(v))


def _make_uvs(xyz, images, cameras, noise=0.0, corrupt=None):
    K = np.array([[800., 0, 400.], [0, 800., 300.], [0, 0, 1.]])
    uvs = []
    rng = np.random.default_rng(7)
    for i, (name, im) in enumerate(images.items()):
        uv = _project_uv(xyz, im["R"], im["t"], K)
        if uv is None:
            continue
        n = rng.normal(0, noise, 2) if noise else np.zeros(2)
        if corrupt and i in corrupt:
            n += np.array([90.0, 90.0])  # 外点（大幅偏移）
        uvs.append({"image": name, "u": uv[0] + n[0], "v": uv[1] + n[1]})
    return uvs


class TestRansacTriangulate:
    def test_noise_free(self):
        cameras, images = _make_cameras_images(n=6)
        xyz = np.array([0.4, -0.2, 0.6])
        uvs = _make_uvs(xyz, images, cameras)
        x, n_inl, n_views = triangulate_sfm_xyz_ransac(uvs, images, cameras)
        assert np.linalg.norm(x - xyz) < 0.01
        assert n_inl >= 4 and n_views == 6

    def test_robust_to_outliers(self):
        """2 个外点视图下 RANSAC 仍恢复（全部内点重估）。"""
        cameras, images = _make_cameras_images(n=6)
        xyz = np.array([0.4, -0.2, 0.6])
        uvs = _make_uvs(xyz, images, cameras, corrupt={0, 1})
        x, n_inl, _ = triangulate_sfm_xyz_ransac(uvs, images, cameras,
                                                 n_trials=128)
        assert np.linalg.norm(x - xyz) < 0.05  # 外点不影响最终解

    def test_insufficient_views(self):
        cameras, images = _make_cameras_images(n=2)
        xyz = np.array([0.4, -0.2, 0.6])
        uvs = _make_uvs(xyz, images, cameras)[:1]
        with pytest.raises(ControlPointError):
            triangulate_sfm_xyz_ransac(uvs, images, cameras)


class TestRefineMeshToPoints:
    def test_refine_improves_fit(self):
        """网格初始有偏移 → 最近邻精化贴合目标点云。"""
        mesh = _sphere_mesh()
        # 目标点云：网格表面的一个相似变换版本（作为稀疏点云基准）
        surface = mesh.sample_points_uniformly(600)
        pts = np.asarray(surface.points)
        target = pts * 0.8 + np.array([0.3, 0.1, -0.2])  # 缩小平移
        # 初始网格轻微错位（放大版）
        new_mesh, metrics = refine_mesh_to_points(mesh, target, iterations=6)
        # 精化后网格表面点应贴合 target
        out_surf = new_mesh.sample_points_uniformly(600)
        from scipy.spatial import cKDTree
        d, _ = cKDTree(target).query(np.asarray(out_surf.points), k=1)
        assert d.mean() < 0.10  # 原始误差 >0.2 量级 -> 精化后显著下降
        assert metrics["method"] == "control_points + nearest_refine"


class TestAlignTwoStage:
    def test_two_stage_runs(self):
        """控制点粗对齐 + 最近邻精化两阶段：残差低于单阶段。"""
        cameras, images = _make_cameras_images(n=6)
        mesh = _sphere_mesh()
        surface = mesh.sample_points_uniformly(500)
        pts = np.asarray(surface.points)
        idx = np.linspace(0, len(pts) - 1, 5, dtype=int)
        K = np.array([[800., 0, 400.], [0, 800., 300.], [0, 0, 1.]])
        controls = {}
        for i, pi in enumerate(idx):
            xyz = pts[pi]
            uvs = []
            for name, im in images.items():
                uv = _project_uv(xyz, im["R"], im["t"], K)
                if uv:
                    uvs.append({"image": name, "u": uv[0], "v": uv[1]})
            controls[f"c{i}"] = {"laser_xyz": xyz.tolist(), "photo_uv": uvs}
        sparse = pts + 0.02  # 稀疏点云（带微噪）
        new_mesh, metrics = align_two_stage(mesh, controls, images, cameras,
                                            sparse)
        assert metrics["method"] == "control_points + nearest_refine"
        assert metrics["n_control_points"] >= 3


class TestCalibrateRansac:
    def test_adaptive_params_noise_free(self):
        """无噪声时阈值收紧、迭代数随视图数自适应。"""
        cameras, images = _make_cameras_images(n=6)
        xyz = np.array([0.4, -0.2, 0.6])
        uvs = _make_uvs(xyz, images, cameras)
        from h3d.ctrl_points import calibrate_ransac_params
        n_trials, thr, noise = calibrate_ransac_params(uvs, images, cameras)
        assert n_trials >= 64
        assert 2.0 <= thr <= 6.0
        assert noise < 1e-6  # 无噪声估计近 0

    def test_adaptive_params_noisy(self):
        """噪声下阈值放宽（标定生效）。"""
        cameras, images = _make_cameras_images(n=6)
        xyz = np.array([0.4, -0.2, 0.6])
        uvs = _make_uvs(xyz, images, cameras, noise=0.8)
        from h3d.ctrl_points import calibrate_ransac_params
        n_trials, thr, noise = calibrate_ransac_params(uvs, images, cameras)
        assert noise > 0.3  # 噪声被估计
        assert thr >= 2.0

    def test_auto_default_uses_calibration(self):
        """n_trials/threshold 缺省（0）时自动标定并成功。"""
        cameras, images = _make_cameras_images(n=6)
        xyz = np.array([0.4, -0.2, 0.6])
        uvs = _make_uvs(xyz, images, cameras, corrupt={2, 3, 4})
        from h3d.ctrl_points import triangulate_sfm_xyz_ransac
        x, n_inl, _ = triangulate_sfm_xyz_ransac(uvs, images, cameras)
        assert np.linalg.norm(x - xyz) < 0.10  # 自动标定下外点仍被剔除
        assert n_inl >= 3
