"""h3d.io : IO 模块（L1 适配层）—— 点云 / 网格读写与格式适配。

对外统一暴露读写接口，业务子包（preprocess / register / ...）只需 ``from h3d.io
import ...`` 即可，**不直接 import open3d / laspy**，实现「适配器模式」与许可证隔离
（《软著方案》2.3.1）。

典型用法::

    from h3d.io import load_point_cloud, save_point_cloud, load_mesh, save_mesh

    pcd = load_point_cloud("00_raw/luohan_001.ply")
    save_point_cloud(pcd, "10_clean/luohan_001.ply")
    mesh = load_mesh("30_dense/luohan_001.ply")
    save_mesh(mesh, "80_export/luohan_001.obj")

支持格式：
    点云读取 ply / pcd / xyz / pts / las / laz；写出 ply / pcd / xyz；
    网格读取 obj / ply / stl / glb / gltf；写出 obj / ply / stl / glb。
"""

from .pointcloud_io import (
    POINTCLOUD_READ_EXT,
    POINTCLOUD_WRITE_EXT,
    load_point_cloud,
    load_all,
    save_point_cloud,
)
from .mesh_io import (
    MESH_READ_EXT,
    MESH_WRITE_EXT,
    load_mesh,
    make_mesh,
    save_mesh,
)

__all__ = [
    # 点云
    "POINTCLOUD_READ_EXT",
    "POINTCLOUD_WRITE_EXT",
    "load_point_cloud",
    "load_all",
    "save_point_cloud",
    # 网格
    "MESH_READ_EXT",
    "MESH_WRITE_EXT",
    "load_mesh",
    "save_mesh",
    "make_mesh",
]
