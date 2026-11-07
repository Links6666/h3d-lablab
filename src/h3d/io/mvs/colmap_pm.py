"""COLMAP PatchMatch 后端：``image_undistorter`` → ``patch_match_stereo`` → ``stereo_fusion``。

这里是**唯一**拼 COLMAP 稠密命令的地方。业务层（``sfm_solve.run_dense_stereo``）
只执行命令、管超时、做诊断，不再知道 COLMAP 有哪些参数。

为什么命令要一字不差
--------------------
``fused.ply`` 的哈希取决于「命令 + 输入」。命令改一个参数，点云就变。所以
``tests/test_io/test_mvs_contract.py`` 把重构前的三条命令逐字钉住了——那是
「哈希相同」这个最终判据最便宜的代理。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from functools import lru_cache

from .base import MvsBackend, MvsPlan, MvsRequest, MvsStep

__all__ = ["ColmapPatchMatch", "count_depth_maps",
           "PHOTOMETRIC_SUFFIX", "GEOMETRIC_SUFFIX"]

#: 深度图文件后缀。两遍的名称由 COLMAP 决定，不是我们起的
PHOTOMETRIC_SUFFIX = ".photometric.bin"
GEOMETRIC_SUFFIX = ".geometric.bin"


def count_depth_maps(dense_dir: str, suffix: str) -> int:
    """统计已生成的深度图数量（超时诊断用：完成多少、还要多少）。

    目录不存在时返回 0 而不是抛——诊断路径崩掉会掩盖真正的原因。
    """
    depth_maps = os.path.join(dense_dir, "stereo", "depth_maps")
    try:
        return sum(1 for f in os.listdir(depth_maps) if f.endswith(suffix))
    except OSError:
        return 0


@lru_cache(maxsize=8)
@lru_cache(maxsize=8)
def _probe_version(exe: str) -> str:
    """问一次 ``colmap --version``；任何异常都降级成 ``unknown``。

    不落盘、不阻断：版本只是元数据（P0-3 用来判「两期是不是同一后端版本」），
    拿不到时宁可标 unknown，也不能让稠密重建因为探测失败而跑不起来。

    进程内缓存（``lru_cache``）：批量跑时每件都会问一次身份，每次都起一个
    ``colmap -h`` 子进程会让批量白等十几秒。
    """
    path = shutil.which(exe) or (exe if os.path.exists(exe) else "")
    if not path:
        return "unknown"
    for flag in ("--version", "-h"):
        try:
            proc = subprocess.run([path, flag], capture_output=True, text=True,
                                  timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        text = ((proc.stdout or "") + (proc.stderr or "")).strip()
        for line in text.splitlines():
            line = line.strip()
            if line and any(ch.isdigit() for ch in line):
                return line[:120]
    return "unknown"


class ColmapPatchMatch(MvsBackend):
    backend_id = "colmap_patchmatch"

    def __init__(self, exe: str = "colmap"):
        self.exe = exe or "colmap"
        self._version: str | None = None

    @property
    def backend_version(self) -> str:
        if self._version is None:
            self._version = _probe_version(self.exe)
        return self._version

    def plan(self, req: MvsRequest) -> MvsPlan:
        exe = req.exe or self.exe
        dense = os.path.join(req.workspace, "dense")
        geom = bool(req.opt("geom_consistency", True))
        max_image = int(req.opt("max_image_size", 0) or 0)
        min_px = int(req.opt("min_pixels", 2))
        max_px = int(req.opt("max_pixels", 20))

        patch_match = [
            exe, "patch_match_stereo",
            "--workspace_path", dense,
            "--workspace_format", "COLMAP",
            "--PatchMatchStereo.geom_consistency", "true" if geom else "false",
            "--PatchMatchStereo.gpu_index", "0",
        ]
        if max_image > 0:
            patch_match += ["--PatchMatchStereo.max_image_size", str(max_image)]

        fused = os.path.join(dense, "fused.ply")
        steps = (
            MvsStep((exe, "image_undistorter",
                     "--image_path", req.photo_dir,
                     "--input_path", req.sparse_model,
                     "--output_path", dense,
                     "--output_type", "COLMAP"), "image_undistorter"),
            MvsStep(tuple(patch_match), "patch_match_stereo", heavy=True),
            MvsStep((exe, "stereo_fusion",
                     "--workspace_path", dense,
                     "--workspace_format", "COLMAP",
                     "--input_type", "geometric" if geom else "photometric",
                     "--output_path", fused,
                     "--StereoFusion.min_num_pixels", str(min_px),
                     "--StereoFusion.max_num_pixels", str(max_px)),
                    "stereo_fusion"),
        )

        return MvsPlan(dense_dir=dense, steps=steps, fused_path=fused,
                       progress=lambda: self._progress(dense))

    @staticmethod
    def _progress(dense_dir: str) -> str:
        """一句话说清「算到哪了」。两遍都报，因为第二遍才是慢的那遍。"""
        return (f"photometric {count_depth_maps(dense_dir, PHOTOMETRIC_SUFFIX)}、"
                f"geometric {count_depth_maps(dense_dir, GEOMETRIC_SUFFIX)}")
