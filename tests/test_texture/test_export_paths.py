"""tests.test_texture.test_export_paths : 中文路径下的 GLB 必须真的落盘。

真机发现（2026-11-01）
---------------------
`paths.py` 把项目根定名为 **`阶段成果`**，所以中文 Windows 用户的输出目录
**天生带中文**。而 Open3D 0.19 写 GLB 走 ASSIMP，在非 ASCII 路径下：

    o3d.io.write_triangle_mesh(".../阶段成果/50_tex/r1/r1_L1.glb", mesh)
    -> 返回 True，但**文件根本不存在**（大小 0，os.path.exists=False）

不抛异常、不打 warning。后果是三重的：

1. **交付包里没有 GLB**——用户勾了 glb 却拿不到，且界面/日志一切正常；
2. `outputs` 里记着这个不存在的路径 → ``_outputs_exist()`` 永远 False →
   **断点续跑在 M4 上完全失效**，每次都重做纹理（实测 3 件批 ×3 次运行，
   M1/M2/M3 都跳过了，只有 M4 每次重跑）；
3. 诊断这个缺陷花的时间，全部来自「返回码说成功」这一个谎。

同一模型写到纯 ASCII 目录就正常；换 trimesh 写也正常（与 ADR-0001 里
「Open3D 写 GLB 会静默丢纹理，必须 trimesh」是同一条根因的另一种表现）。
"""
from __future__ import annotations

import os

import numpy as np
import open3d as o3d
import pytest

from h3d.texture.export import export_colored


def _cube(size: float = 0.1):
    mesh = o3d.geometry.TriangleMesh.create_box(size, size, size)
    mesh.compute_vertex_normals()
    mesh.paint_uniform_color([0.72, 0.70, 0.66])
    return mesh


def test_glb_lands_under_non_ascii_dir(tmp_path):
    """中文目录（真实默认布局）下 GLB 必须存在且非空。"""
    d = tmp_path / "阶段成果" / "50_tex" / "r1"
    d.mkdir(parents=True)
    outs = export_colored(_cube(), str(d), "r1", "L1", ["glb"])
    assert len(outs) == 1, outs
    assert os.path.isfile(outs[0]), (
        "中文路径下 GLB 没落盘 —— Open3D 返回 True 但什么都没写")
    assert os.path.getsize(outs[0]) > 0


def test_all_formats_land_under_non_ascii_dir(tmp_path):
    d = tmp_path / "阶段成果" / "50_tex" / "r1"
    d.mkdir(parents=True)
    outs = export_colored(_cube(), str(d), "r1", "L1", ["ply", "obj", "glb"])
    assert len(outs) == 3, outs
    for p in outs:
        assert os.path.isfile(p) and os.path.getsize(p) > 0, p


def test_ascii_dir_still_works(tmp_path):
    """纯英文路径不能因为换了写出方式而退化。"""
    d = tmp_path / "stage" / "50_tex" / "r1"
    d.mkdir(parents=True)
    outs = export_colored(_cube(), str(d), "r1", "L1", ["ply", "obj", "glb"])
    assert len(outs) == 3
    assert all(os.path.isfile(p) for p in outs)


def test_outputs_only_lists_files_that_exist(tmp_path, monkeypatch):
    """返回的清单必须等于磁盘事实。

    这一条直接锁续跑：`_outputs_exist()` 拿这个清单去判定「产物还在不在」，
    列进一个不存在的文件就等于宣布这个阶段永远没完成。
    """
    d = tmp_path / "阶段成果"
    d.mkdir(parents=True)

    real_write = o3d.io.write_triangle_mesh

    def fake_write(path, mesh, *a, **kw):
        # 模拟 Open3D 的谎：说成功，什么都不写
        return True

    monkeypatch.setattr(o3d.io, "write_triangle_mesh", fake_write)
    try:
        outs = export_colored(_cube(), str(d), "r1", "L1", ["ply"])
    finally:
        monkeypatch.setattr(o3d.io, "write_triangle_mesh", real_write)

    assert outs == [], f"写失败的文件仍进了产物清单：{outs}"
