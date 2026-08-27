"""h3d.preprocess.downsample : 体素降采样。

设计依据：
    《设计书》32.2 —— M1 预处理用 VoxelDownSample 下采样，尺度自适应控制点数与内存；
    《教学版》15.1 —— 体素降采样 voxel_down_sample；
    《骨架分析》4.2 —— downsample.py 承担「降采样」职责。

职责边界：只对统一数据模型（PointCloud）做体素降采样，输出仍为 PointCloud；文件读写由 io
层负责。体素过小导致点数不降或结果为空时给出告警 / 抛 InputDataError(E100)。
"""

from __future__ import annotations

from h3d.core import InputDataError, get_logger

log = get_logger("preprocess.downsample")


def _o3d():
    import open3d as o3d

    return o3d


def voxel_down_sample(pcd, voxel_size: float):
    """体素降采样（Open3D voxel_down_sample）。

    把点云按 ``voxel_size`` 的体素栅格划分，每个体素用一个质心点代表，从而在
    保留整体形状的前提下显著减少点数、降低内存占用。

    参数
    ----
    voxel_size : 体素边长（米，配置项 preprocess.voxel_down_sample，默认 0.005）。

    返回
    ----
    降采样后的 PointCloud。
    """
    if pcd is None or not pcd.has_points():
        raise InputDataError("输入点云为空（0 点），无法降采样")
    if voxel_size is None or float(voxel_size) <= 0:
        raise InputDataError(f"体素边长非法: {voxel_size!r}（须 > 0）")
    before = len(pcd.points)
    down = pcd.voxel_down_sample(float(voxel_size))
    if down is None or not down.has_points():
        raise InputDataError(f"体素降采样后点云为空（voxel={voxel_size}）")
    if len(down.points) >= before:
        log.warning("体素降采样未减少点数（%d -> %d），建议增大 voxel_size",
                    before, len(down.points))
    else:
        log.info("体素降采样：%d -> %d 点（voxel=%.4f）",
                 before, len(down.points), voxel_size)
    return down


__all__ = ["voxel_down_sample"]
