"""io.pointcloud_io 单元测试：格式路由、缺失/空文件/不支持格式报错、往返读写。

采用真实 Open3D / laspy 生成合成点云验证读写正确性；错误路径断言 E100 / E601 错误码。
"""
import numpy as np
import open3d as o3d
import pytest

from h3d.io import (
    POINTCLOUD_READ_EXT,
    POINTCLOUD_WRITE_EXT,
    load_all,
    load_point_cloud,
    save_point_cloud,
)
from h3d.core import ExportError, InputDataError


def _make_pcd(n=100, seed=0):
    rng = np.random.default_rng(seed)
    pts = rng.uniform(-1.0, 1.0, size=(n, 3))
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts)
    return pcd, pts


# ---------------------------------------------------------------------------
# 读取：格式路由与错误路径
# ---------------------------------------------------------------------------
def test_load_missing_file_raises_e100():
    with pytest.raises(InputDataError) as ei:
        load_point_cloud("no_such_dir/a.ply")
    assert ei.value.code == "E100"


def test_load_unsupported_ext_raises_e100(tmp_path):
    p = tmp_path / "a.foo"
    p.write_bytes(b"whatever")
    with pytest.raises(InputDataError) as ei:
        load_point_cloud(str(p))
    assert ei.value.code == "E100"


def test_load_empty_ply_raises_e100(tmp_path):
    p = tmp_path / "empty.ply"
    p.write_bytes(b"")
    with pytest.raises(InputDataError) as ei:
        load_point_cloud(str(p))
    assert ei.value.code == "E100"


def test_load_all_no_match_raises_e100(tmp_path):
    with pytest.raises(InputDataError):
        load_all(str(tmp_path / "nothing_*.ply"))


# ---------------------------------------------------------------------------
# 往返读写
# ---------------------------------------------------------------------------
def test_ply_roundtrip(tmp_path):
    pcd, pts = _make_pcd(200)
    path = tmp_path / "in.ply"
    save_point_cloud(pcd, str(path))
    back = load_point_cloud(str(path))
    assert len(back.points) == 200
    np.testing.assert_allclose(np.asarray(back.points), pts, atol=1e-6)


def test_xyz_roundtrip(tmp_path):
    pcd, pts = _make_pcd(50)
    path = tmp_path / "in.xyz"
    save_point_cloud(pcd, str(path))
    back = load_point_cloud(str(path))
    assert len(back.points) == 50
    np.testing.assert_allclose(np.asarray(back.points), pts, atol=1e-6)


def test_pcd_roundtrip(tmp_path):
    pcd, pts = _make_pcd(30)
    path = tmp_path / "in.pcd"
    save_point_cloud(pcd, str(path))
    back = load_point_cloud(str(path))
    assert len(back.points) == 30


def test_load_all_glob_returns_ordered(tmp_path):
    for i in range(3):
        pcd, _ = _make_pcd(10, seed=i)
        save_point_cloud(pcd, str(tmp_path / f"s{i}.ply"))
    pairs = load_all(str(tmp_path / "s*.ply"))
    assert len(pairs) == 3
    names = [p.rsplit("\\", 1)[-1] for p, _ in pairs]
    assert names == ["s0.ply", "s1.ply", "s2.ply"]  # glob 排序
    for p, pc in pairs:
        assert len(pc.points) == 10


# ---------------------------------------------------------------------------
# 写出：格式路由
# ---------------------------------------------------------------------------
def test_save_unsupported_ext_raises_e601(tmp_path):
    pcd, _ = _make_pcd(5)
    with pytest.raises(ExportError) as ei:
        save_point_cloud(pcd, str(tmp_path / "out.foo"))
    assert ei.value.code == "E601"


def test_save_creates_parent_dir(tmp_path):
    pcd, _ = _make_pcd(5)
    target = tmp_path / "sub" / "deep" / "out.ply"
    save_point_cloud(pcd, str(target))
    assert target.exists()


def test_supported_ext_constants():
    assert ".ply" in POINTCLOUD_READ_EXT
    assert ".las" in POINTCLOUD_READ_EXT
    assert ".ply" in POINTCLOUD_WRITE_EXT
    assert ".las" not in POINTCLOUD_WRITE_EXT  # LAS 只读不写


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
