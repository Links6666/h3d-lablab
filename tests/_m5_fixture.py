"""M5 项目夹具（唯一一份）。

为什么单独抽出来
----------------
"造一个最小 M5 输入"（网格 + 参考点云 + 图集 + 上游指标）在多个测试里都要用。
每处各写一份的结果是本项目反复踩过的那个坑：**夹具写两份必然漂**——
``tests/test_verify_accuracy_values.py`` 当年自己写了一份 CSV，于是它测的
根本不是产品生成的那张表，绿着但无效。

用法::

    from tests._m5_fixture import m5_project
    task, cfg = m5_project(tmp_path, m4_metrics={"texture_overlap": 2.4})
"""
from __future__ import annotations

import os

import numpy as np


def write_atlas(root: str, item: str = "r1", size: int = 96) -> None:
    """写一份最小图集 + 掩膜，避免 M5 因缺图集而报一堆 WARN。"""
    from PIL import Image

    d = os.path.join(root, "50_tex", item)
    os.makedirs(d, exist_ok=True)
    rgb = np.full((size, size, 3), 128, np.uint8)
    rgb[size // 4: size // 2, size // 4: size // 2] = (170, 150, 120)
    mask = np.zeros((size, size), np.uint8)
    mask[size // 4: size // 2, size // 4: size // 2] = 255
    Image.fromarray(rgb).save(os.path.join(d, f"{item}_L1.0_diffuse.png"))
    Image.fromarray(mask).save(os.path.join(d, f"{item}_L1.0_mask.png"))


def m5_cfg(root: str, data_source: str = "auto"):
    from h3d.core import PipelineConfig

    cfg = PipelineConfig()
    cfg.project.raw_dir = os.path.join(root, "00_raw")
    cfg.project.formats = ["ply"]
    cfg.report.enabled = False
    cfg.runner.manifest = os.path.join(root, "90_meta", "manifest.json")
    cfg.runner.data_source = data_source
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
    return cfg


def m5_project(tmp_path, *, data_source: str = "auto",
               m1_dense_source: str | None = None, has_raw: bool = True,
               m4_metrics: dict | None = None):
    """最小 M5 输入：30_dense 网格 + M2 参考点云 + 图集 + 上游指标。"""
    import open3d as o3d

    from h3d.runner.task import StageResult, Task, TaskStatus

    root = str(tmp_path)
    mesh = o3d.geometry.TriangleMesh.create_sphere(radius=1.0)
    mesh.compute_vertex_normals()
    d = os.path.join(root, "30_dense", "r1")
    os.makedirs(d, exist_ok=True)
    o3d.io.write_triangle_mesh(os.path.join(d, "r1.ply"), mesh)
    reg = os.path.join(root, "20_registered", "r1")
    os.makedirs(reg, exist_ok=True)
    ref = os.path.join(reg, "r1_s01.ply")
    o3d.io.write_point_cloud(ref, mesh.sample_points_uniformly(8000))
    write_atlas(root)

    cfg = m5_cfg(root, data_source)
    task = Task(item_id="r1", root=root,
                raw_paths=["dummy.ply"] if has_raw else [])
    task.results["M2"] = StageResult("M2", TaskStatus.DONE, [ref], {})
    if m1_dense_source:
        task.results["M1"] = StageResult(
            "M1", TaskStatus.DONE, [], {"dense_source": m1_dense_source})
    if m4_metrics is not None:
        task.results["M4"] = StageResult(
            "M4", TaskStatus.DONE, [], dict(m4_metrics))
    return task, cfg
