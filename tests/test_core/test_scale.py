"""tests.test_core.test_scale : 尺度自适应（h3d.core.scale）回归测试。

覆盖背景（2026-10-09）：管线原先只面向大中型文物（米级），``preprocess`` /
``register`` 中的距离类参数是**绝对米制值**；扩大到中小型文物（5~30 cm）时，
同一套值会「吃掉纹饰细节」「FPFH 半径等效半个器身」。本模块以包围盒对角线
为基准线性缩放，使「参数 / 物体尺寸」之比恒定。

测试分层（锁行为本质，不止「不报错」）：
    1. bbox_diagonal —— 几何正确性 + 退化输入
    2. resolve_factor —— 开关语义、比例关系、钳制、非法输入
    3. scaled_config  —— 缩放正确性、零拷贝语义、原对象不变、变更摘要
    4. 端到端集成     —— resolve_task_config 读真实点云、指标注入
"""

from __future__ import annotations

import copy
import os

import numpy as np
import pytest

from h3d.core.config import PipelineConfig, ScaleConfig
from h3d.core.scale import (
    bbox_diagonal,
    describe,
    resolve_factor,
    scaled_config,
)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _cube_points(size: float, n: int = 400) -> np.ndarray:
    """生成填满 size 边长立方体的点云（对角线 = size * sqrt(3)）。"""
    rng = np.random.default_rng(20260915)
    return rng.uniform(-size / 2.0, size / 2.0, (n, 3))


def _flat_scale(**kw) -> ScaleConfig:
    return ScaleConfig(**kw)


# ---------------------------------------------------------------------------
# 1) bbox_diagonal
# ---------------------------------------------------------------------------
class TestBboxDiagonal:
    def test_unit_cube_diagonal(self):
        pts = np.array([[0, 0, 0], [1, 1, 1]], dtype=float)
        assert bbox_diagonal(pts) == pytest.approx(np.sqrt(3.0))

    def test_axis_aligned_box(self):
        # 2 x 6 x 9 外接盒 -> sqrt(4+36+81) = 11.0
        pts = np.array([[0, 0, 0], [2, 6, 9]], dtype=float)
        assert bbox_diagonal(pts) == pytest.approx(11.0)

    def test_random_cube_matches_analytic(self):
        d = bbox_diagonal(_cube_points(0.2))
        assert d == pytest.approx(0.2 * np.sqrt(3.0), rel=0.02)

    def test_empty_returns_zero(self):
        assert bbox_diagonal(np.zeros((0, 3))) == 0.0

    def test_nan_rows_ignored(self):
        pts = np.array([[0, 0, 0], [np.nan, np.nan, np.nan], [3, 0, 0]],
                       dtype=float)
        assert bbox_diagonal(pts) == pytest.approx(3.0)

    def test_all_nan_returns_zero(self):
        pts = np.full((5, 3), np.nan)
        assert bbox_diagonal(pts) == 0.0

    def test_extra_columns_ignored(self):
        # 带额外属性列（如 rgb / 强度）时只取前三维
        pts = np.array([[0, 0, 0, 9, 9], [1, 0, 0, 1, 1]], dtype=float)
        assert bbox_diagonal(pts) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 2) resolve_factor
# ---------------------------------------------------------------------------
class TestResolveFactor:
    def test_disabled_always_unit(self):
        # 关闭自适应 -> 无论多小/多大都返回 1.0（历史行为）
        cfg = _flat_scale(enabled=False, reference_diag=3.7)
        assert resolve_factor(0.05, cfg) == 1.0
        assert resolve_factor(30.0, cfg) == 1.0

    def test_reference_diag_gives_unit(self):
        cfg = _flat_scale(reference_diag=3.7)
        assert resolve_factor(3.7, cfg) == pytest.approx(1.0)

    def test_proportional_scaling(self):
        """核心不变式：因子与对角线成正比。"""
        cfg = _flat_scale(reference_diag=2.0)
        assert resolve_factor(1.0, cfg) == pytest.approx(0.5)
        assert resolve_factor(4.0, cfg) == pytest.approx(2.0)
        assert resolve_factor(8.0, cfg) == pytest.approx(4.0)

    def test_small_relic_yields_small_factor(self):
        """20 cm 铜镜（对角线约 0.25 m）应得到远小于 1 的因子。"""
        cfg = _flat_scale(reference_diag=3.7)
        f = resolve_factor(0.25, cfg)
        assert f < 0.1
        assert f == pytest.approx(0.25 / 3.7, rel=1e-6)

    def test_large_relic_yields_large_factor(self):
        cfg = _flat_scale(reference_diag=3.7)
        assert resolve_factor(7.4, cfg) == pytest.approx(2.0, rel=1e-6)

    def test_clamped_by_min_max(self):
        cfg = _flat_scale(reference_diag=3.7, min_factor=0.005, max_factor=5.0)
        # 极小物体 -> 钳到下限
        assert resolve_factor(1e-6, cfg) == pytest.approx(0.005)
        # 极大物体（疑似毫米单位）-> 钳到上限
        assert resolve_factor(3700.0, cfg) == pytest.approx(5.0)

    def test_invalid_diag_falls_back_to_unit(self):
        cfg = _flat_scale(reference_diag=3.7)
        assert resolve_factor(0.0, cfg) == 1.0
        assert resolve_factor(-1.0, cfg) == 1.0
        assert resolve_factor(float("nan"), cfg) == 1.0
        assert resolve_factor(float("inf"), cfg) == 1.0

    def test_invalid_reference_falls_back_to_unit(self):
        # reference_diag<=0 时退化（配置层已挡，这里测运行时兜底）
        cfg = _flat_scale(reference_diag=3.7)
        cfg.__dict__["reference_diag"] = 0.0   # 绕过描述符校验，模拟坏配置
        assert resolve_factor(1.0, cfg) == 1.0


# ---------------------------------------------------------------------------
# 3) scaled_config
# ---------------------------------------------------------------------------
class TestScaledConfig:
    def _cfg(self) -> PipelineConfig:
        return PipelineConfig.from_dict({
            "scale": {"enabled": True, "reference_diag": 3.7},
            "preprocess": {"voxel_down_sample": 0.02},
            "register": {"fpfh_radius": 0.2, "ransac_distance": 0.5,
                         "icp_distance": 0.2, "max_iter": 300},
            "mesh": {"poisson_depth": 8, "ball_radii": [2.0, 4.0, 6.0],
                     "alpha": 0.05},
            "change": {"core_radius": 0.05},
        })

    def test_unit_factor_returns_same_object(self):
        """因子为 1.0 时必须原样返回入参对象（零拷贝、零风险）。"""
        cfg = self._cfg()
        out, changes = scaled_config(cfg, 1.0)
        assert out is cfg
        assert changes == []

    def test_scaled_values_are_proportional(self):
        cfg = self._cfg()
        out, changes = scaled_config(cfg, 0.1)
        # 0.02 * 0.1 = 0.002
        assert out.preprocess.voxel_down_sample == pytest.approx(0.002)
        assert out.register.fpfh_radius == pytest.approx(0.02)
        assert out.register.ransac_distance == pytest.approx(0.05)
        assert out.register.icp_distance == pytest.approx(0.02)
        assert out.mesh.alpha == pytest.approx(0.005)
        assert out.change.core_radius == pytest.approx(0.005)
        # 6 个标量字段 + 1 个列表字段
        assert len(changes) == 7

    def test_list_field_scaled_elementwise(self):
        cfg = self._cfg()
        out, _ = scaled_config(cfg, 0.5)
        assert out.mesh.ball_radii == pytest.approx([1.0, 2.0, 3.0])

    def test_dimensionless_fields_untouched(self):
        """k 近邻数 / 迭代次数 / 八叉树深度等无量纲量不得被缩放。"""
        cfg = self._cfg()
        out, _ = scaled_config(cfg, 0.1)
        assert out.register.max_iter == 300
        assert out.mesh.poisson_depth == 8
        assert out.preprocess.normal_estimate_k == cfg.preprocess.normal_estimate_k

    def test_source_config_not_mutated(self):
        """缩放必须返回副本——原配置对象不得被就地修改。"""
        cfg = self._cfg()
        before_voxel = cfg.preprocess.voxel_down_sample
        before_fpfh = cfg.register.fpfh_radius
        before_ball = list(cfg.mesh.ball_radii)
        scaled_config(cfg, 0.1)
        assert cfg.preprocess.voxel_down_sample == before_voxel
        assert cfg.register.fpfh_radius == before_fpfh
        assert list(cfg.mesh.ball_radii) == before_ball

    def test_scale_section_itself_not_scaled(self):
        """scale.reference_diag 是基准，绝不能自我缩放（否则会递归失真）。"""
        cfg = self._cfg()
        out, _ = scaled_config(cfg, 0.1)
        assert out.scale.reference_diag == pytest.approx(3.7)
        assert out.scale.enabled is True

    def test_changes_summary_shape(self):
        cfg = self._cfg()
        _, changes = scaled_config(cfg, 0.5)
        paths = {c["path"] for c in changes}
        assert "register.fpfh_radius" in paths
        assert "mesh.ball_radii" in paths
        for c in changes:
            assert set(c) == {"path", "before", "after"}

    def test_describe_renders_all_changes(self):
        cfg = self._cfg()
        _, changes = scaled_config(cfg, 0.5)
        text = describe(changes)
        assert "register.fpfh_radius" in text and "->" in text
        assert describe([]) == "无字段变化"

    def test_invalid_factor_returns_same_object(self):
        cfg = self._cfg()
        assert scaled_config(cfg, float("nan"))[0] is cfg


# ---------------------------------------------------------------------------
# 4) 端到端集成：任务级解算 + 指标注入
# ---------------------------------------------------------------------------
class TestTaskScaleIntegration:
    def _write_ply(self, path: str, size: float) -> str:
        """写一个最小合法 PLY（用 h3d.io 保证格式一致）。"""
        import open3d as o3d
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(_cube_points(size))
        o3d.io.write_point_cloud(path, pcd)
        return path

    def _cfg(self) -> PipelineConfig:
        return PipelineConfig.from_dict({
            "scale": {"enabled": True, "reference_diag": 3.7},
            "preprocess": {"voxel_down_sample": 0.02},
            "register": {"fpfh_radius": 0.2, "ransac_distance": 0.5,
                         "icp_distance": 0.2},
            "mesh": {"ball_radii": [2.0, 4.0, 6.0], "alpha": 0.05},
        })

    def test_small_relic_probe_and_resolve(self, tmp_path):
        from h3d.runner.batch_processor import (_inject_scale_metrics,
                                                probe_geometry_diagonal,
                                                resolve_task_config)
        from h3d.runner.task import StageResult, Task, TaskStatus

        small = self._write_ply(str(tmp_path / "small.ply"), 0.15)
        task = Task(item_id="mirror", raw_paths=[small], root=str(tmp_path))

        diag = probe_geometry_diagonal(task)
        assert diag == pytest.approx(0.15 * np.sqrt(3.0), rel=0.05)

        cfg, info = resolve_task_config(task, self._cfg())
        assert info["applied"] is True
        assert info["factor"] < 0.1
        # 20 cm 量级的物件 -> 体素进入毫米以下（原 0.02 m 会吃掉细节）
        assert cfg.preprocess.voxel_down_sample < 0.002

        res = StageResult("M1", TaskStatus.DONE)
        _inject_scale_metrics("M1", res, info)
        assert res.metrics["scale_factor"] == pytest.approx(info["factor"],
                                                            abs=1e-6)

    def test_disabled_returns_original_cfg(self, tmp_path):
        from h3d.runner.batch_processor import resolve_task_config
        from h3d.runner.task import Task

        small = self._write_ply(str(tmp_path / "small.ply"), 0.15)
        task = Task(item_id="mirror", raw_paths=[small], root=str(tmp_path))
        cfg = self._cfg()
        cfg.scale.enabled = False
        out, info = resolve_task_config(task, cfg)
        assert out is cfg
        assert info["applied"] is False
        assert info["reason"] == "disabled"

    def test_probe_failure_degrades_gracefully(self, tmp_path):
        """点云不可读时不得抛异常，应退化为「不缩放」。"""
        from h3d.runner.batch_processor import resolve_task_config
        from h3d.runner.task import Task

        task = Task(item_id="ghost",
                    raw_paths=[str(tmp_path / "missing.ply")],
                    root=str(tmp_path))
        cfg = self._cfg()
        out, info = resolve_task_config(task, cfg)
        assert out is cfg
        assert info["applied"] is False
        assert info["factor"] == 1.0

    def test_metrics_not_injected_for_other_stages(self, tmp_path):
        """尺度指标只注入 M1（任务级代表），不得污染其它阶段。"""
        from h3d.runner.batch_processor import _inject_scale_metrics
        from h3d.runner.task import StageResult, TaskStatus

        res = StageResult("M2", TaskStatus.DONE)
        _inject_scale_metrics("M2", res, {"applied": True, "factor": 0.1,
                                          "diag": 0.25})
        assert "scale_factor" not in res.metrics

    def test_metrics_absent_when_not_applied(self):
        from h3d.runner.batch_processor import _inject_scale_metrics
        from h3d.runner.task import StageResult, TaskStatus

        res = StageResult("M1", TaskStatus.DONE)
        _inject_scale_metrics("M1", res, {"applied": False, "factor": 1.0})
        assert "scale_factor" not in res.metrics


# ---------------------------------------------------------------------------
# 7) 点间距下限保护
# ---------------------------------------------------------------------------
class TestSpacingFloor:
    """回归（2026-10-09 实验发现）：尺度缩放是乘法的，若输入点云点间距
    不随尺寸同比变化（网格均匀采样 / 多精度设备混用），小尺度下 FPFH 半径
    会**小于点间距**——邻域只剩自身、描述子退化、粗配准直接失败。"""

    def test_estimate_spacing_on_regular_grid(self):
        from h3d.core.scale import estimate_spacing
        # 边长 0.01 的规则立方格点
        g = np.arange(0.0, 0.101, 0.01)
        grid = np.array(np.meshgrid(g, g, g)).reshape(3, -1).T
        assert estimate_spacing(grid) == pytest.approx(0.01, rel=0.02)

    def test_estimate_spacing_degenerate_inputs(self):
        from h3d.core.scale import estimate_spacing
        assert estimate_spacing(np.zeros((0, 3))) == 0.0
        assert estimate_spacing(np.array([[1.0, 2.0, 3.0]])) == 0.0
        assert estimate_spacing(np.full((6, 3), np.nan)) == 0.0

    def test_estimate_spacing_uses_median_not_mean(self):
        """中位数应不被少量离群稀疏点拉大。"""
        from h3d.core.scale import estimate_spacing
        dense = np.random.default_rng(1).uniform(0, 0.01, (500, 3))
        far = np.random.default_rng(2).uniform(5.0, 5.1, (5, 3))
        combined = np.vstack([dense, far])
        # 中位数应接近稠密区的间距量级，而非被离群点拉到米级
        assert estimate_spacing(combined) < 0.05

    def _cfg(self):
        return PipelineConfig.from_dict({
            "register": {"fpfh_radius": 0.002, "ransac_distance": 0.005,
                         "icp_distance": 0.002},
        })

    def test_floor_lifts_too_small_radius(self):
        from h3d.core.scale import apply_spacing_floor
        cfg = self._cfg()
        # 点间距 0.01 -> FPFH 下限 0.04，远大于原 0.002 -> 必须被抬升
        out, changes = apply_spacing_floor(cfg, 0.01)
        assert out.register.fpfh_radius == pytest.approx(0.04)
        assert out.register.ransac_distance == pytest.approx(0.04)
        assert out.register.icp_distance == pytest.approx(0.02)   # 2 倍
        assert len(changes) == 3
        assert all(c["reason"] == "spacing_floor" for c in changes)

    def test_floor_noop_for_normal_dense_cloud(self):
        """正常稠密点云（点间距远小于参数）不得产生任何改动已保证零回归。"""
        from h3d.core.scale import apply_spacing_floor
        cfg = PipelineConfig.from_dict({
            "register": {"fpfh_radius": 0.2, "ransac_distance": 0.5,
                         "icp_distance": 0.2},
        })
        out, changes = apply_spacing_floor(cfg, 0.001)
        assert changes == []
        assert out is cfg

    def test_floor_does_not_mutate_source(self):
        from h3d.core.scale import apply_spacing_floor
        cfg = self._cfg()
        before = cfg.register.fpfh_radius
        apply_spacing_floor(cfg, 0.01)
        assert cfg.register.fpfh_radius == before

    def test_floor_invalid_spacing_noop(self):
        from h3d.core.scale import apply_spacing_floor
        cfg = self._cfg()
        for bad in (0.0, -1.0, float("nan")):
            assert apply_spacing_floor(cfg, bad)[1] == []

    def test_resolve_task_config_applies_floor_on_sparse_cloud(self, tmp_path):
        """集成：稀疏点云 + 小尺度时，FPFH 半径必须被抬到点间距之上，
        否则配准必然失败（这正是本轮实验 B 暴露的问题）。"""
        import open3d as o3d
        from h3d.runner.batch_processor import resolve_task_config
        from h3d.runner.task import Task

        # 20 cm 物体 + 稀疏点云（1200 点 -> 点间距约 1 cm，不随尺寸缩小）
        rng = np.random.default_rng(7)
        v = rng.normal(size=(1200, 3))
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        pts = v * np.array([0.10, 0.07, 0.14])
        path = str(tmp_path / "sparse.ply")
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(pts)
        o3d.io.write_point_cloud(path, pcd)

        task = Task(item_id="sparse", raw_paths=[path], root=str(tmp_path))
        cfg = PipelineConfig.from_dict({
            "scale": {"enabled": True, "reference_diag": 3.7},
            "preprocess": {"voxel_down_sample": 0.005},
            "register": {"fpfh_radius": 0.02, "ransac_distance": 0.05,
                         "icp_distance": 0.02},
        })
        out, info = resolve_task_config(task, cfg)

        spacing = info["spacing"]
        assert spacing > 0.0
        # 缩放后的 FPFH 会小于点间距，必须由下限兜住
        scaled_fpfh = 0.02 * info["factor"]
        assert scaled_fpfh < spacing, "本用例前提：缩放后半径小于点间距"
        assert out.register.fpfh_radius >= 4.0 * spacing * 0.99
        assert info["applied"] is True


# ---------------------------------------------------------------------------
# 5) 零回归：示例尺度下因子 ≈ 1
# ---------------------------------------------------------------------------
class TestNoRegressionOnDemoScale:
    def test_demo_diagonal_yields_near_unit_factor(self):
        """示例文物对角线 3.71 m 对基准 3.7 m -> 因子≈1，现有行为不变。"""
        cfg = _flat_scale(reference_diag=3.7, min_factor=0.005,
                          max_factor=5.0)
        f = resolve_factor(3.7066, cfg)
        assert f == pytest.approx(1.0018, abs=2e-3)
        # 体素扰动必须可忽略（<0.5%），否则示例结果会漂移
        assert abs(f - 1.0) < 0.005


# ---------------------------------------------------------------------------
# 6) GUI 配置往返
# ---------------------------------------------------------------------------
class TestGuiScaleConfig:
    def test_yaml_roundtrip(self, tmp_path):
        from h3d.core import load
        from h3d.gui.config_edit import ProjectConfig

        cfg = ProjectConfig()
        cfg.root_dir = str(tmp_path)
        cfg.item_id = "relic"
        cfg.photo_dir = str(tmp_path)
        cfg.autoscale = False
        cfg.ref_diag = 2.5
        path = cfg.to_yaml(str(tmp_path / "config.yaml"))
        loaded = load(path)
        assert loaded.scale.enabled is False
        assert loaded.scale.reference_diag == pytest.approx(2.5)

    def test_default_template_enables_autoscale(self):
        from h3d.gui.config_edit import SCENE1_CONFIG, SCENE2_CONFIG
        for tpl in (SCENE1_CONFIG, SCENE2_CONFIG):
            assert tpl["scale"]["enabled"] is True
            assert tpl["scale"]["reference_diag"] == pytest.approx(3.7)

    def test_template_not_polluted_by_to_yaml(self, tmp_path):
        """回归：``to_yaml`` 曾用浅拷贝，嵌套赋值会**就地改写模块级模板**，
        导致同一进程内第二次生成的配置继承上一次的用户选择。"""
        from h3d.gui.config_edit import SCENE1_CONFIG, SCENE2_CONFIG

        before1 = copy.deepcopy(SCENE1_CONFIG)
        before2 = copy.deepcopy(SCENE2_CONFIG)

        from h3d.gui.config_edit import ProjectConfig
        cfg = ProjectConfig()
        cfg.root_dir = str(tmp_path)
        cfg.item_id = "relic"
        cfg.photo_dir = str(tmp_path)
        cfg.autoscale = False
        cfg.ref_diag = 9.9
        cfg.voxel = 0.5
        cfg.to_yaml(str(tmp_path / "config.yaml"))

        assert SCENE1_CONFIG == before1, "SCENE1_CONFIG 模板被就地改写"
        assert SCENE2_CONFIG == before2, "SCENE2_CONFIG 模板被就地改写"
