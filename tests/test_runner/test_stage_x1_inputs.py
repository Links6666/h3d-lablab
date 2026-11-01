"""tests.test_runner.test_stage_x1_inputs : X1 的**输入定位**（多时相差分的入口）。

为什么要专门测这一层
--------------------
X1 在 1.1.12 上的状态一直是 ``disabled``，而且即使打开也会 skip——它把当期几何
硬拼成 ``10_dense/<件>/fused_dense.ply``，那是 **COLMAP 稠密点云（场景②）** 的
产物。激光件（场景①）的几何在 ``30_dense/<件>/<件>.ply``，于是「X1 从未在真机
上跑过」不是没时间，是路径根本对不上。

这一层不测 M3C2 的数值（那在 ``tests/test_change`` 里有 24 项），只测「能不能
找到该找的那两份几何」。
"""

from __future__ import annotations

import os

import numpy as np
import pytest

ITEM = "relic_001"


def _sphere_mesh(path, radius=0.05, n=1200):
    """半径 5cm 的球 —— 真实文物量级（M3C2 的尺度都是 MPS 倍数，尺寸差一个量级
    就会让「搜索半径」跨过整个表面）。"""
    """写一个小网格（激光件的 30_dense 产物形态）。"""
    import open3d as o3d

    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=radius,
                                                   resolution=8)
    mesh.compute_vertex_normals()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    o3d.io.write_triangle_mesh(path, mesh)
    return mesh


def _sphere_cloud(path, radius=0.05, n=20000, seed=0):
    """写一个小点云（参考期形态）。"""
    import open3d as o3d

    pts = np.random.default_rng(seed).normal(size=(n, 3))
    pts /= np.linalg.norm(pts, axis=1, keepdims=True)
    pts *= radius
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    o3d.io.write_point_cloud(path, pcd)
    return pcd


def _cfg(root):
    from h3d.core import PipelineConfig

    cfg = PipelineConfig()
    cfg.runner.x1_enabled = True
    cfg.report.enabled = False
    cfg.runner.manifest = os.path.join(str(root), "90_meta", "manifest.json")
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
    return cfg


def _task(root):
    from h3d.runner.task import Task

    return Task(item_id=ITEM, raw_paths=[], root=str(root))


class TestLaserProject:
    """激光件：几何在 30_dense，没有 10_dense。这是绝大多数真实项目。"""

    def test_dense_mesh_is_used_when_no_point_cloud_exists(self, tmp_path):
        root = tmp_path / "阶段成果"
        _sphere_mesh(str(root / "30_dense" / ITEM / f"{ITEM}.ply"))
        _sphere_cloud(str(root / "00_ref" / ITEM / "t1.ply"))

        from h3d.runner import _stage_multitemporal
        from h3d.runner.task import TaskStatus

        res = _stage_multitemporal(_task(root), _cfg(root))
        assert res.status == TaskStatus.DONE, res.metrics.get("skip_reason")

    def test_same_geometry_reports_no_change(self, tmp_path):
        """参考期与当期同源 → max_change 必须接近 0。

        这是可证伪的判据：若定位拿错了文件（比如拿到另一个件、或读成空点云），
        M3C2 会给出巨大的「变化」，而不是 0。
        """
        root = tmp_path / "阶段成果"
        mesh = _sphere_mesh(str(root / "30_dense" / ITEM / f"{ITEM}.ply"))
        import open3d as o3d

        # 与当期同密度：两期点云密度差一个量级时，M3C2 会把「采样密度差」
        # 报成形变（实测参考期 2000 点 vs 当期 20 万点时 max_change=1690mm）
        ref = mesh.sample_points_uniformly(20000)
        os.makedirs(str(root / "00_ref" / ITEM), exist_ok=True)
        o3d.io.write_point_cloud(str(root / "00_ref" / ITEM / "t1.ply"), ref)

        from h3d.runner import _stage_multitemporal
        from h3d.runner.task import TaskStatus

        res = _stage_multitemporal(_task(root), _cfg(root))
        assert res.status == TaskStatus.DONE
        assert res.metrics["max_change_mm"] < 5.0, res.metrics
        assert res.metrics["changed_ratio"] < 0.05, res.metrics


class TestSfmProject:
    """SfM 件（场景②）：几何是 10_dense/fused_dense.ply，行为不能退化。"""

    def test_fused_dense_is_still_used(self, tmp_path):
        root = tmp_path / "阶段成果"
        _sphere_cloud(str(root / "10_dense" / ITEM / "fused_dense.ply"))
        _sphere_cloud(str(root / "00_ref" / ITEM / "t1.ply"), seed=1)

        from h3d.runner import _stage_multitemporal
        from h3d.runner.task import TaskStatus

        res = _stage_multitemporal(_task(root), _cfg(root))
        assert res.status == TaskStatus.DONE, res.metrics.get("skip_reason")


class TestSkipReasonsStayReadable:
    def test_missing_reference_names_the_directory(self, tmp_path):
        """缺参考期 ≠ 缺当期几何 —— 两种 skip 的原因必须能分开。

        缺参考期是「用户还没放上期数据」，缺当期几何是「本次建模失败」，
        两者的处置完全不同（去准备数据 vs 去查管线）。
        """
        root = tmp_path / "阶段成果"
        _sphere_mesh(str(root / "30_dense" / ITEM / f"{ITEM}.ply"))

        from h3d.runner import _stage_multitemporal
        from h3d.runner.task import TaskStatus

        res = _stage_multitemporal(_task(root), _cfg(root))
        assert res.status == TaskStatus.SKIPPED
        assert "00_ref" in res.metrics["skip_reason"]

    def test_missing_current_geometry_says_so(self, tmp_path):
        root = tmp_path / "阶段成果"
        _sphere_cloud(str(root / "00_ref" / ITEM / "t1.ply"))

        from h3d.runner import _stage_multitemporal
        from h3d.runner.task import TaskStatus

        res = _stage_multitemporal(_task(root), _cfg(root))
        assert res.status == TaskStatus.SKIPPED
        reason = res.metrics["skip_reason"]
        assert "00_ref" not in reason, reason
        assert "当期" in reason or "几何" in reason, reason


class TestMeshIsSampledNotReadAsVertices:
    """网格文件**不能**被当成点云直接读。

    `o3d.io.read_point_cloud` 读网格 PLY 不报错——它把**顶点**当点云返回。而泊松
    重建后的顶点分布极不均匀（平坦区稀疏、细节区密集），与参考期的均匀采样密度
    差一个量级，M3C2 会把这个差报成形变。真机 1.1.12 第一次跑出
    ``max_change=1181mm``（两期同源），就是这个原因。
    """

    def test_mesh_yields_uniform_samples_not_vertices(self, tmp_path):
        from h3d.io import load_mesh
        from h3d.runner import _load_any_as_cloud

        path = str(tmp_path / "m.ply")
        mesh = _sphere_mesh(path)
        cloud = _load_any_as_cloud(path)

        n_tri = len(load_mesh(path).triangles)
        expected = min(200_000, max(20_000, n_tri * 30))
        assert len(cloud.points) == expected, (
            f"拿到 {len(cloud.points)} 点，期望均匀采样 {expected}"
            f"（网格顶点数是 {len(mesh.vertices)}）")
        assert len(cloud.points) != len(mesh.vertices), "把顶点当点云了"

    def test_point_cloud_file_is_read_as_is(self, tmp_path):
        """纯点云文件必须原样读，不许被重采样（会丢原始精度）。"""
        from h3d.runner import _load_any_as_cloud

        path = str(tmp_path / "c.ply")
        _sphere_cloud(path, n=5000)
        cloud = _load_any_as_cloud(path)
        assert len(cloud.points) == 5000, len(cloud.points)


class TestCorepointsFollowTheSurface:
    """核心点必须落在点云表面上。

    旧实现从 **XY 包围盒**生成规则网格，Z 是常量——那只在「薄板 / 近平面」点云上
    成立。兵马俑是 1.8 米高的立体，网格面与表面完全错开，M3C2 的圆柱里没有点，
    1024 个核心点**全部返回 NaN**（真机 1.1.12 实测 ``max_change=nan``）。

    判据用「核心点的 Z 起伏 vs 点云的 Z 起伏」：平铺在某个平面上时前者恒为 0。
    """

    def test_corepoints_span_the_cloud_height(self, tmp_path):
        import numpy as np

        from h3d.core import PipelineConfig
        from h3d.io import load_point_cloud
        from h3d.change import corepoint_grid

        # 立体点云：两个上下分开的球（模拟头 + 躯干的高度差）
        path = str(tmp_path / "tall.ply")
        a = _sphere_cloud(str(tmp_path / "a.ply"), n=5000, seed=0)
        b = _sphere_cloud(str(tmp_path / "b.ply"), n=5000, seed=1)
        import open3d as o3d

        pa = np.asarray(a.points); pb = np.asarray(b.points)
        pb = pb + np.array([0.0, 0.0, 0.6])
        merged = o3d.geometry.PointCloud()
        merged.points = o3d.utility.Vector3dVector(np.vstack([pa, pb]))
        o3d.io.write_point_cloud(path, merged)

        cloud = load_point_cloud(path)
        pts = np.asarray(cloud.points)
        core = corepoint_grid(cloud, PipelineConfig().change)
        assert core.shape[1] == 3
        # 核心点的 Z 必须跟着表面起伏，而不是恒定在一个平面
        assert core[:, 2].std() > 0.2 * pts[:, 2].std(), (
            f"核心点 Z 标准差 {core[:, 2].std():.4f} vs 点云 {pts[:, 2].std():.4f}"
            " —— 核心点没有落在表面上")


class TestMpsIsNotInflatedBySubsampling:
    """MPS 不能因为「抽样后再算」而被放大。

    ``estimate_mps`` 为了省内存先抽到 2 万点再算最近邻。但抽样会让间距按
    ``sqrt(n_sample/n_full)`` **变大**——真机 1.1.12 实测：20 万点的真实互最近邻
    中位 4.83mm，而 ``estimate_mps`` 报 16.2mm（放大 3.3 倍 ≈ sqrt(200k/20k)）。

    M3C2 的所有尺度都是 MPS 的倍数，MPS 放大 3 倍意味着圆柱半径 48mm、
    max_distance 812mm —— 在凹槽处会抓到对面表面的点，报出**同源两期 458mm**
    这种荒谬的「形变」。
    """

    def test_result_does_not_depend_on_sample_limit(self):
        """同一份点云，抽 2 万点与抽 10 万点算出的 MPS 必须一致。

        这是条**可证伪**的判据：若 MPS 随抽样量变化，说明它测的是「抽样后的
        间距」而不是点云本身的间距。真机 1.1.12 上就是这个症状——20 万点报
        16.2mm，而实际互最近邻中位是 4.83mm（比值 3.35 ≈ sqrt(200k/20k)）。

        夹具必须用**随机分布**的点：规则网格抽掉一半仍有邻点，间距不变，
        测不出这个问题（第一版夹具就是这么写错的）。
        """
        import numpy as np

        import open3d as o3d
        from h3d.quality import estimate_mps

        # **表面点云**（球面随机），不是体分布：点云是 2D 流形，密度与间距是
        # 平方根关系；在 3D 体里随机取点则是立方根关系，用错会得出「校正过度」。
        rng = np.random.default_rng(0)
        v = rng.normal(size=(200_000, 3))
        v /= np.linalg.norm(v, axis=1, keepdims=True)
        pts = v * 0.1                                    # 半径 10cm 的球面
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(pts)

        lo = estimate_mps(pcd, sample_limit=20_000)
        hi = estimate_mps(pcd, sample_limit=200_000)
        assert lo == pytest.approx(hi, rel=0.25), (
            f"抽 2 万给 {lo * 1000:.2f}mm，抽 20 万给 {hi * 1000:.2f}mm"
            f"（比值 {lo / hi:.2f}）—— MPS 在测抽样量而不是点云本身")


class TestUncalibratedNumbersStayOutOfTheTables:
    """X1 未标定时，数值不能进论文表 / accuracy.csv。

    真机 1.1.12 上同源两期报 177mm，而两期点云实测互最近邻中位只有 4.83mm
    （ADR-0005）。这类数字一旦写进论文表就是硬伤——与 X3 的 `rmse_scope`
    同一套纪律：**能引用才给数，否则给文字**。
    """

    def _acc_csv(self, tmp_path):
        import csv

        path = tmp_path / "accuracy.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["item_id", "coverage"])
            w.writerow(["relic_001", "0.9"])
        return str(path)

    def test_accuracy_csv_prints_uncalibrated(self, tmp_path):
        import csv

        from h3d.runner import _append_x1_to_accuracy

        path = self._acc_csv(tmp_path)
        _append_x1_to_accuracy(path, "relic_001",
                               {"max_change_mm": 177.3, "changed_ratio": 0.159,
                                "calibrated": False})
        with open(path, encoding="utf-8-sig") as fh:
            header, row = list(csv.reader(fh))[:2]
        assert row[header.index("X1最大形变(mm)")] == "未标定"
        assert row[header.index("X1变化占比")] == "未标定"

    def test_accuracy_csv_prints_number_when_calibrated(self, tmp_path):
        import csv

        from h3d.runner import _append_x1_to_accuracy

        path = self._acc_csv(tmp_path)
        _append_x1_to_accuracy(path, "relic_001",
                               {"max_change_mm": 2.5, "changed_ratio": 0.01,
                                "calibrated": True})
        with open(path, encoding="utf-8-sig") as fh:
            header, row = list(csv.reader(fh))[:2]
        assert row[header.index("X1最大形变(mm)")] == "2.50"

    def test_thesis_table_prints_uncalibrated(self):
        from h3d.thesis_tables import _fmt_x1

        assert _fmt_x1(177.3, False) == "未标定"
        assert _fmt_x1(177.3, True) == "177.30"
        assert _fmt_x1(None, True) == "N/A"


def _shift_cloud(src, dst, offset):
    """把点云整体平移后另存（造「不同源」的几何）。"""
    import open3d as o3d

    p = o3d.io.read_point_cloud(src)
    p.translate(tuple(offset))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    o3d.io.write_point_cloud(dst, p)


class TestEpochConsistencyGate:
    """两期**同源闸门**：候选链挑出来的两份几何必须是同一件的两期。

    真机 ``D:/h3d_relic_test`` 上 ``10_dense/fused_dense.ply``（照片重建稠密）
    与 ``00_raw`` 的激光件互最近邻中位 **5541mm**——两个坐标系。M3C2 对这样的
    输入照样成功返回，报出的「最大形变」是坐标系间距而不是形变，而且看起来
    完全正常。ADR-0005 那个 177mm 就是这么来的。
    """

    def test_foreign_candidate_is_skipped_and_next_one_wins(self, tmp_path):
        """10_dense 不同源 → 拒绝它，回落到 30_dense（同源）。"""
        root = tmp_path / "阶段成果"
        _sphere_cloud(str(root / "00_ref" / ITEM / "t1.ply"))
        # 10_dense：同一个球平移到 3 米外（对角线比仍≈1，靠距离判据才抓得住）
        _sphere_cloud(str(root / "10_dense" / ITEM / "fused_dense.ply"), seed=2)
        _shift_cloud(str(root / "10_dense" / ITEM / "fused_dense.ply"),
                     str(root / "10_dense" / ITEM / "fused_dense.ply"),
                     (3.0, 0.0, 0.0))
        _sphere_mesh(str(root / "30_dense" / ITEM / f"{ITEM}.ply"))

        from h3d.runner import _stage_multitemporal
        from h3d.runner.task import TaskStatus

        res = _stage_multitemporal(_task(root), _cfg(root))
        assert res.status == TaskStatus.DONE, res.metrics.get("skip_reason")
        assert "30_dense" in res.metrics["epoch_source"]
        assert res.metrics["epoch_median_mm"] < 10.0, res.metrics

    def test_all_candidates_foreign_skips_with_evidence(self, tmp_path):
        """全部候选都不同源 → 不出数，并把每个候选的实测差距留下来。"""
        root = tmp_path / "阶段成果"
        _sphere_cloud(str(root / "00_ref" / ITEM / "t1.ply"))
        _sphere_cloud(str(root / "10_dense" / ITEM / "fused_dense.ply"), seed=2)
        _shift_cloud(str(root / "10_dense" / ITEM / "fused_dense.ply"),
                     str(root / "10_dense" / ITEM / "fused_dense.ply"),
                     (3.0, 0.0, 0.0))

        from h3d.runner import _stage_multitemporal
        from h3d.runner.task import TaskStatus

        res = _stage_multitemporal(_task(root), _cfg(root))
        assert res.status == TaskStatus.SKIPPED
        assert "同源" in res.metrics["skip_reason"]
        checked = res.metrics["epoch_checked"]
        assert checked and checked[0]["ok"] is False
        assert checked[0]["median_mm"] > 1000.0, checked
        assert "fused_dense.ply" in checked[0]["path"]

    def test_scale_mismatch_is_caught_by_bbox_ratio(self, tmp_path):
        """尺度差 2 倍（单位错 / 不是同一件）先被对角线比挡下，不算距离。"""
        root = tmp_path / "阶段成果"
        _sphere_cloud(str(root / "00_ref" / ITEM / "t1.ply"))
        _sphere_cloud(str(root / "10_dense" / ITEM / "fused_dense.ply"),
                      radius=0.10, seed=2)   # 半径翻倍 → 对角线比 2.0

        from h3d.runner import _stage_multitemporal
        from h3d.runner.task import TaskStatus

        res = _stage_multitemporal(_task(root), _cfg(root))
        assert res.status == TaskStatus.SKIPPED
        first = res.metrics["epoch_checked"][0]
        assert first["ok"] is False
        assert "尺度不符" in first["reason"], first

    def test_gate_numbers_reach_metrics(self, tmp_path):
        """同源核对的两个数要写进 metrics（报表据此解释「这数能不能用」）。"""
        root = tmp_path / "阶段成果"
        mesh = _sphere_mesh(str(root / "30_dense" / ITEM / f"{ITEM}.ply"))
        import open3d as o3d

        ref = mesh.sample_points_uniformly(20000)
        os.makedirs(str(root / "00_ref" / ITEM), exist_ok=True)
        o3d.io.write_point_cloud(str(root / "00_ref" / ITEM / "t1.ply"), ref)

        from h3d.runner import _stage_multitemporal
        from h3d.runner.task import TaskStatus

        res = _stage_multitemporal(_task(root), _cfg(root))
        assert res.status == TaskStatus.DONE
        assert res.metrics["epoch_bbox_ratio"] == pytest.approx(1.0, abs=0.15)
        assert res.metrics["epoch_median_mm"] >= 0.0
        assert res.metrics["epoch_source"].endswith(".ply")
