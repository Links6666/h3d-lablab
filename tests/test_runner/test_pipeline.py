"""tests.test_runner.test_pipeline : 端到端批量处理（真实点云）。

生成带重叠的 PLY 站点数据，跑通 M1 预处理 + M2 配准，验证：
    1. 真实文件发现 → 任务构建
    2. M1 写出 10_clean/<item>/<item>_sNN.ply
    3. M2 写出 20_registered/<item>/<item>_sNN.ply 且配准指标（fitness）合理
    4. run_manifest.json 持久化 + 报表生成
"""
from __future__ import annotations

import os

import numpy as np
import open3d as o3d
import pytest

from h3d.core import PipelineConfig, RunManifest
from h3d.runner import run_batch


@pytest.fixture(autouse=True)
def _fix_o3d_random_seed():
    """固定 Open3D RANSAC 内部随机种子，消除端到端配准的随机性（测试确定性）。"""
    o3d.utility.random.seed(42)
    yield


def _make_bumpy_sphere(n=2000, seed=0, noise=0.0, colored=True):
    """生成带凹凸的不对称球面（具有可匹配 FPFH 特征）。

    noise>0 时叠加高斯噪声（模拟真实扫描噪声）；colored=True 时按坐标伪着色
    （模拟带色扫描，供 M4 顶点色纹理映射）。
    """
    rng = np.random.default_rng(seed)
    u = rng.uniform(0, 2 * np.pi, n)
    v = np.arccos(2 * rng.uniform(0, 1, n) - 1)
    r = 1.0 + 0.20 * np.sin(3 * u) * np.cos(2 * v) + 0.15 * np.cos(5 * u) * np.sin(3 * v)
    pts = np.vstack([
        r * np.sin(v) * np.cos(u),
        r * np.sin(v) * np.sin(u),
        r * np.cos(v),
    ]).T
    if noise > 0:
        pts = pts + rng.normal(0, noise, pts.shape)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts)
    if colored:
        span = max(pts.max(axis=0) - pts.min(axis=0))
        pcd.colors = o3d.utility.Vector3dVector(
            (pts - pts.min(axis=0)) / max(span, 1e-9))
    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=0.1, max_nn=30))
    return pcd


def _make_two_scan_project(tmp_path, item_id, trans=(0.10, 0.0, 0.0)):
    """为某个造像生成两个有重叠的 PLY 站点（输入到 00_raw/<item_id>/）。

    两站用不同随机采样（seed 1 / 5）的同一形状——模拟真实多视角扫描同一物体：
    几何相似但每站点位独立采样，配准后两站点交织而不完全重合（MPS 不退化，
    覆盖 / 中误差指标有意义）。
    """
    raw = tmp_path / "00_raw" / item_id
    raw.mkdir(parents=True, exist_ok=True)
    s0 = _make_bumpy_sphere(seed=1)
    s1 = _make_bumpy_sphere(seed=5)
    T = np.eye(4)
    T[0, 3] = trans[0]; T[1, 3] = trans[1]; T[2, 3] = trans[2]
    s1.transform(T)
    paths = []
    for i, s in enumerate([s0, s1], start=1):
        p = raw / f"scan_s{i:02d}.ply"
        o3d.io.write_point_cloud(str(p), s)
        paths.append(p)
    return paths


def _make_cfg(tmp_path, **kwargs):
    """构造项目用 PipelineConfig。"""
    raw = str(tmp_path / "00_raw")
    cfg = PipelineConfig()
    cfg.project.raw_dir = raw
    cfg.project.formats = ["ply"]
    # 略微调小体素与配准距离，让小尺度点云也能跑通
    cfg.preprocess.voxel_down_sample = 0.02
    cfg.preprocess.remove_outlier_std = 3.0
    cfg.preprocess.normal_estimate_k = 20
    cfg.register.fpfh_radius = 0.08
    cfg.register.ransac_distance = 0.20
    cfg.register.icp_distance = 0.10
    cfg.register.max_iter = 300
    # M3 网格重建参数（小尺度点云用较浅深度 + 宽松密度裁剪）
    cfg.mesh.method = kwargs.get("mesh_method", "poisson")
    cfg.mesh.poisson_depth = 7
    cfg.mesh.density_ratio = 0.0
    cfg.mesh.alpha = 0.08
    cfg.mesh.simplify_target = 0
    cfg.mesh.fill_holes = False
    # M5 质检：端到端聚焦「链路与报告」；严格阈值判定由 tests/test_quality 覆盖
    cfg.quality.coverage_threshold = 0.30
    cfg.quality.samples = 4000
    cfg.runner.manifest = str(tmp_path / "90_meta" / "run_manifest.json")
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
    cfg.report.enabled = False  # 避免在测试中画图
    return cfg


class TestEndToEndPipeline:
    def test_m1_only_single_scan(self, tmp_path):
        """单站点：M1 成功 + M2 标记 registered=False（无配准必要）。"""
        _make_two_scan_project(tmp_path, "rock001")
        cfg = _make_cfg(tmp_path)
        # 只保留第一个站点
        (tmp_path / "00_raw" / "rock001" / "scan_s02.ply").unlink()

        result = run_batch(cfg)
        assert result.done == 1
        assert result.failed == 0

        # M1 产出物
        clean_dir = tmp_path / "10_clean" / "rock001"
        assert (clean_dir / "rock001_s01.ply").exists()
        # M2 跳过（< 2 站）
        reg_dir = tmp_path / "20_registered" / "rock001"
        assert reg_dir.exists() and not any(reg_dir.iterdir())

        # manifest 记录
        m = RunManifest.load(cfg.runner.manifest)
        assert m.is_done("rock001/M1")
        assert m.is_done("rock001/M2")
        assert m.stages["rock001/M2"]["registered"] is False

    def test_m1_and_m2_two_scans(self, tmp_path):
        """双站点：M1 + M2 完整跑通，配准 fitness 接近 1.0。"""
        _make_two_scan_project(tmp_path, "rock001")
        cfg = _make_cfg(tmp_path)

        result = run_batch(cfg)
        assert result.done == 1

        # 配准产物
        reg_dir = tmp_path / "20_registered" / "rock001"
        assert (reg_dir / "rock001_s02.ply").exists()
        registered_pcd = o3d.io.read_point_cloud(str(reg_dir / "rock001_s02.ply"))
        assert len(registered_pcd.points) > 0

        # 配准指标
        m = RunManifest.load(cfg.runner.manifest)
        m2 = m.stages["rock001/M2"]
        assert m2["registered"] is True
        # 在有重叠场景下 fitness 应明显高于 E200 阈值（0.2）
        assert m2["fitness"] >= 0.3
        # 末站 fitness 写到顶层
        assert m2["inlier_rmse"] >= 0.0
        # 1 尊造像 × 6 阶段（M1~M6）= 6 个阶段条目（M6 发布收官）
        assert len(m.stages) == 6

    def test_multiple_items_with_one_failing(self, tmp_path):
        """两尊造像：rock001 正常；rock002 故意构造为单点云（只 1 站）+M2 应跳过。"""
        _make_two_scan_project(tmp_path, "rock001")
        # rock002：只有一个站
        raw2 = tmp_path / "00_raw" / "rock002"
        raw2.mkdir(parents=True)
        o3d.io.write_point_cloud(str(raw2 / "only.ply"), _make_bumpy_sphere(seed=7))

        cfg = _make_cfg(tmp_path)
        result = run_batch(cfg)
        assert result.done == 2
        assert result.failed == 0
        assert len(result.failures) == 0

    def test_m1_m2_m3_full_closure(self, tmp_path):
        """「点云 -> 模型」核心闭环：M1 预处理 -> M2 配准 -> M3 网格重建。

        验证：
            1. 30_dense/<item>/<item>.ply 网格产物存在（可读、非空）；
            2. manifest 记录 M3 指标（n_vertices / n_triangles / n_input_points）；
            3. M3 输出为多站合并点云重建（n_input_points 包含两站）。
        """
        _make_two_scan_project(tmp_path, "rock001")
        cfg = _make_cfg(tmp_path)

        result = run_batch(cfg)
        assert result.done == 1
        assert result.failed == 0

        # 1) 网格产物存在且可读
        mesh_path = tmp_path / "30_dense" / "rock001" / "rock001.ply"
        assert mesh_path.exists()
        mesh = o3d.io.read_triangle_mesh(str(mesh_path))
        assert len(mesh.vertices) > 0
        assert len(mesh.triangles) > 0
        assert mesh.has_vertex_normals()

        # 2) manifest M3 指标
        m = RunManifest.load(cfg.runner.manifest)
        m3 = m.stages["rock001/M3"]
        assert m3["status"] == "done"
        assert m3["n_vertices"] == len(mesh.vertices)
        assert m3["n_triangles"] == len(mesh.triangles)
        # 3) 两站合并（每站约 2k 点预处理后）
        assert m3["n_input_points"] > 3000

        # 4) 阶段顺序齐全：M1 -> ... -> M6 共 6 个阶段条目（主线收官）
        assert list(m.stages.keys()) == [
            "rock001/M1", "rock001/M2", "rock001/M3",
            "rock001/M4", "rock001/M5", "rock001/M6"]

    def test_m3_single_scan_closure(self, tmp_path):
        """单站造像：M2 跳过配准，M3 仍能用 M1 产物重建网格。"""
        _make_two_scan_project(tmp_path, "rock001")
        (tmp_path / "00_raw" / "rock001" / "scan_s02.ply").unlink()
        cfg = _make_cfg(tmp_path)

        result = run_batch(cfg)
        assert result.done == 1
        mesh_path = tmp_path / "30_dense" / "rock001" / "rock001.ply"
        assert mesh_path.exists()
        mesh = o3d.io.read_triangle_mesh(str(mesh_path))
        assert len(mesh.vertices) > 0

        m = RunManifest.load(cfg.runner.manifest)
        assert m.stages["rock001/M2"]["registered"] is False  # 单站无配准
        assert m.stages["rock001/M3"]["n_triangles"] > 0

    def test_full_chain_with_m5_quality(self, tmp_path):
        """「点云 -> 模型 -> 纹理 -> 质检」全链路：M1 -> M2 -> M3 -> M4 -> M5。

        验证：
            1. 60_qc/<item>/qc_metrics.json 与 70_report/<item>/accuracy.csv 报告产物；
            2. manifest 记录 M5 指标（coverage / rmse_mm / mps_m）；
            3. 稀疏扫描下中误差门自动旁路（rmse_reliable=False），覆盖率门判定通过。
        """
        _make_two_scan_project(tmp_path, "rock001")
        cfg = _make_cfg(tmp_path)

        result = run_batch(cfg)
        assert result.done == 1
        assert result.failed == 0

        # 1) 质检报告产物
        qc_json = tmp_path / "60_qc" / "rock001" / "qc_metrics.json"
        acc_csv = tmp_path / "70_report" / "rock001" / "accuracy.csv"
        assert qc_json.exists()
        assert acc_csv.exists()
        import json
        record = json.loads(qc_json.read_text(encoding="utf-8"))
        assert record["item_id"] == "rock001"
        assert record["status"] == "pass"
        assert "thresholds" in record

        # 2) manifest M5 指标
        m = RunManifest.load(cfg.runner.manifest)
        m5 = m.stages["rock001/M5"]
        assert m5["status"] == "done"
        assert 0.0 < m5["coverage"] <= 1.0
        assert m5["rmse_mm"] > 0
        assert m5["mps_m"] > 0
        # 3) 阶段顺序：M1 -> ... -> M6（主线收官）
        assert list(m.stages.keys()) == [
            "rock001/M1", "rock001/M2", "rock001/M3",
            "rock001/M4", "rock001/M5", "rock001/M6"]

    def test_m4_texture_delivery(self, tmp_path):
        """M4 带色模型交付：50_tex/<item>/ 多格式 LOD 带色产物 + manifest 指标。"""
        _make_two_scan_project(tmp_path, "rock001")
        cfg = _make_cfg(tmp_path)
        # 控制 LOD/格式规模，加快测试
        cfg.texture.export_formats = ["ply", "obj"]
        cfg.texture.lod_levels = [1.0, 0.3]

        result = run_batch(cfg)
        assert result.done == 1
        assert result.failed == 0

        # 1) 50_tex 带色产物（2 LOD × 2 格式 = 4 文件）
        tex_dir = tmp_path / "50_tex" / "rock001"
        assert tex_dir.is_dir()
        files = sorted(os.listdir(tex_dir))
        assert len(files) == 4
        assert "rock001_L1.ply" in files
        assert "rock001_L1.obj" in files
        assert "rock001_L0.3.ply" in files

        # 2) 带色模型（PLY 读回 has_colors）
        colored_ply = o3d.io.read_triangle_mesh(str(tex_dir / "rock001_L1.ply"))
        assert colored_ply.has_vertex_colors()

        # 3) LOD 简化：L0.3 面数 < L1 面数
        full = o3d.io.read_triangle_mesh(str(tex_dir / "rock001_L1.ply"))
        light = o3d.io.read_triangle_mesh(str(tex_dir / "rock001_L0.3.ply"))
        assert len(light.triangles) < len(full.triangles)

        # 4) manifest M4 指标
        m = RunManifest.load(cfg.runner.manifest)
        m4 = m.stages["rock001/M4"]
        assert m4["status"] == "done"
        assert m4["colored"] is True
        assert m4["n_outputs"] == 4
        assert m4["paint_mode"] == "vertex"

    def test_m6_publish_full_closure(self, tmp_path):
        """「点云 -> 模型 -> 带色 -> 质检 -> 发布」主线收官：M1~M6 全链路。

        验证：
            1. 80_export/<item>/ 交付模型（标准命名，多格式）；
            2. 90_meta/<item>_meta.json 元数据快照（manifest 溯源）；
            3. 90_meta/delivery_manifest.json 交付清单（含 accuracy.csv 归档）；
            4. manifest 记录 M6 指标。
        """
        _make_two_scan_project(tmp_path, "rock001")
        cfg = _make_cfg(tmp_path)
        cfg.texture.export_formats = ["ply", "obj"]
        cfg.texture.lod_levels = [1.0, 0.3]

        result = run_batch(cfg)
        assert result.done == 1
        assert result.failed == 0

        # 1) 交付模型（80_export/<item>/，标准命名）
        export_dir = tmp_path / "80_export" / "rock001"
        files = sorted(os.listdir(export_dir))
        assert "rock001.ply" in files
        assert "rock001.obj" in files
        assert "accuracy.csv" in files  # 质检报告归档进交付包
        model = o3d.io.read_triangle_mesh(str(export_dir / "rock001.ply"))
        assert len(model.vertices) > 0
        assert model.has_vertex_colors()

        # 2) 元数据快照（90_meta/<item>_meta.json）
        meta_path = tmp_path / "90_meta" / "rock001_meta.json"
        assert meta_path.exists()
        import json
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["item_id"] == "rock001"
        assert meta["stages"]["M5"]["status"] == "done"
        assert "config_hash" in meta and len(meta["config_hash"]) >= 16

        # 3) 交付清单（90_meta/delivery_manifest.json）
        dm_path = tmp_path / "90_meta" / "delivery_manifest.json"
        assert dm_path.exists()
        dm = json.loads(dm_path.read_text(encoding="utf-8"))
        assert dm["n_items"] == 1
        assert dm["deliveries"][0]["item_id"] == "rock001"
        assert dm["deliveries"][0]["status"] == "published"
        assert dm["deliveries"][0]["qc_status"] == "done"

        # 4) manifest M6 指标 + 完整阶段顺序
        m = RunManifest.load(cfg.runner.manifest)
        m6 = m.stages["rock001/M6"]
        assert m6["status"] == "done"
        assert m6["n_models"] == 2
        assert list(m.stages.keys()) == [
            "rock001/M1", "rock001/M2", "rock001/M3",
            "rock001/M4", "rock001/M5", "rock001/M6"]

    def test_m5_quality_gate_isolates_bad_item(self, tmp_path):
        """质检不达标 -> QualityGateError(E500) -> 失败隔离（确定性 mock 验证）。

        通过注入自定义阶段确定性构造：r2 的 M5 抛 E500，验证该尊被隔离
        （failed），r1 正常完成（done），失败清单字段齐全。
        """
        from h3d.core import QualityGateError
        from h3d.runner import BatchProcessor
        from h3d.runner.task import StageResult, Task, TaskStatus

        cfg = _make_cfg(tmp_path)
        tasks = [Task(item_id="r1", root=str(tmp_path)),
                 Task(item_id="r2", root=str(tmp_path))]

        def mock_m1(task, cfg):
            return StageResult("M1", TaskStatus.DONE, ["a.ply"], {}, 0.0)

        def mock_m5(task, cfg):
            if task.item_id == "r2":
                raise QualityGateError(
                    "质检不达标: coverage=10.0% (阈值 80.0%)",
                    item_id="r2", stage="M5")
            return StageResult("M5", TaskStatus.DONE,
                               ["qc.json", "accuracy.csv"],
                               {"coverage": 0.99, "rmse_mm": 1.0,
                                "mps_m": 0.01, "boundary_ratio": 0.0}, 0.1)

        result = BatchProcessor(cfg, stages={"M1": mock_m1, "M5": mock_m5}).run(tasks)

        assert result.done == 1    # r1 全阶段通过
        assert result.failed == 1  # r2 在 M5 被隔离
        assert tasks[0].status == TaskStatus.DONE
        assert tasks[1].status == TaskStatus.FAIL
        assert tasks[1].results["M5"].status == TaskStatus.FAIL
        assert len(result.failures) == 1
        f = result.failures[0]
        assert f["item_id"] == "r2" and f["stage"] == "M5"
        assert f["error_type"] == "QualityGateError"
        assert "质检不达标" in f["message"]
