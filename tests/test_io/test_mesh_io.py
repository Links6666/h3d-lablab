"""io.mesh_io 单元测试：格式路由、缺失/空网格/不支持格式报错、往返读写。"""
import numpy as np
import open3d as o3d
import pytest

from h3d.io import (
    MESH_READ_EXT,
    MESH_WRITE_EXT,
    load_mesh,
    save_mesh,
)
from h3d.core import ExportError, InputDataError


def _make_tetra():
    """构造一个封闭四面体网格（4 顶点 / 4 面）。"""
    mesh = o3d.geometry.TriangleMesh()
    mesh.vertices = o3d.utility.Vector3dVector(np.array(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0],
         [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64))
    mesh.triangles = o3d.utility.Vector3iVector(np.array(
        [[0, 1, 2], [0, 2, 3], [0, 3, 1], [1, 3, 2]], dtype=np.int32))
    return mesh


# ---------------------------------------------------------------------------
# 读取：错误路径
# ---------------------------------------------------------------------------
def test_load_missing_file_raises_e100():
    with pytest.raises(InputDataError) as ei:
        load_mesh("no_such_dir/a.obj")
    assert ei.value.code == "E100"


def test_load_unsupported_ext_raises_e100(tmp_path):
    p = tmp_path / "a.foo"
    p.write_bytes(b"whatever")
    with pytest.raises(InputDataError) as ei:
        load_mesh(str(p))
    assert ei.value.code == "E100"


def test_load_empty_mesh_raises_e100(tmp_path):
    p = tmp_path / "empty.obj"
    p.write_bytes(b"")
    with pytest.raises(InputDataError) as ei:
        load_mesh(str(p))
    assert ei.value.code == "E100"


# ---------------------------------------------------------------------------
# 往返读写
# ---------------------------------------------------------------------------
def test_obj_roundtrip(tmp_path):
    mesh = _make_tetra()
    path = tmp_path / "tetra.obj"
    save_mesh(mesh, str(path))
    back = load_mesh(str(path))
    assert len(back.vertices) == 4
    assert len(back.triangles) == 4
    np.testing.assert_allclose(
        np.asarray(back.vertices), np.asarray(mesh.vertices), atol=1e-6)


def test_ply_roundtrip(tmp_path):
    mesh = _make_tetra()
    path = tmp_path / "tetra.ply"
    save_mesh(mesh, str(path))
    back = load_mesh(str(path))
    assert len(back.vertices) == 4


def test_stl_roundtrip(tmp_path):
    mesh = _make_tetra()
    path = tmp_path / "tetra.stl"
    save_mesh(mesh, str(path))
    back = load_mesh(str(path))
    assert len(back.triangles) == 4


# ---------------------------------------------------------------------------
# 写出：格式路由
# ---------------------------------------------------------------------------
def test_save_unsupported_ext_raises_e601(tmp_path):
    mesh = _make_tetra()
    with pytest.raises(ExportError) as ei:
        save_mesh(mesh, str(tmp_path / "out.foo"))
    assert ei.value.code == "E601"


def test_save_creates_parent_dir(tmp_path):
    mesh = _make_tetra()
    target = tmp_path / "sub" / "deep" / "out.obj"
    save_mesh(mesh, str(target))
    assert target.exists()


def test_supported_ext_constants():
    assert ".obj" in MESH_READ_EXT
    assert ".glb" in MESH_READ_EXT
    assert ".obj" in MESH_WRITE_EXT
    assert ".glb" in MESH_WRITE_EXT


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
