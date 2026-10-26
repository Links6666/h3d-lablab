"""h3d.subset_model : 子集 sparse 模型构建（P1，绕过 undistorter 崩溃）。

COLMAP undistorter 假定 photo_dir 与模型图像全集一致——对「子集照片」会
崩溃（实测 exit 3221226505）。本模块从完整 TXT 模型中过滤出「子集图像 +
其观测的 3D 点 + 相机」，生成合法子集 TXT 模型；undistorter 读子集模型 +
全量照片目录即不崩溃（子集图像 ⊂ 照片目录）。

对外：build_subset_model(model_txt, keep_images, out_dir) -> 子集模型目录。
"""

from __future__ import annotations

import os
import shutil
from typing import List, Optional

import numpy as np

from h3d.core import get_logger

log = get_logger("subset_model")


def build_subset_model(model_txt: str, keep_images: List[str],
                       out_dir: str) -> Optional[str]:
    """构建子集 TXT 模型（图像/点/相机一致过滤）。

    参数
    ----
    model_txt:    完整 sparse TXT 模型目录（含 cameras.txt/images.txt/
                  points3D.txt）。
    keep_images:  保留的图像文件名列表（如 ["img_00_00.jpg", ...]）。
    out_dir:      输出目录（子集模型）。

    返回
    ----
    子集模型目录；保留图像不足 2 张或模型不可读返回 None。
    """
    os.makedirs(out_dir, exist_ok=True)
    im_path = os.path.join(model_txt, "images.txt")
    pt_path = os.path.join(model_txt, "points3D.txt")
    cam_path = os.path.join(model_txt, "cameras.txt")
    if not all(os.path.exists(p) for p in (im_path, pt_path, cam_path)):
        log.warning("模型文件缺失（无法构建子集）: %s", model_txt)
        return None
    keep = set(keep_images)

    # 1) images.txt：保留 keep 图像（位姿行 + keypoints 行），收集引用点
    used_pts = set()
    im_lines, im_header = [], []
    last_pose_kept = False
    with open(im_path, encoding="utf-8") as fh:
        lines = fh.readlines()
    for line in lines:
        if line.startswith("#"):
            im_header.append(line)
            continue
        parts = line.split()
        if len(parts) == 10 and parts[9] in keep:  # 位姿行（保留）
            im_lines.append(line)
            last_pose_kept = True
            continue
        if len(parts) == 10:  # 位姿行（非保留）——其 keypoints 行一并跳过
            last_pose_kept = False
            continue
        if last_pose_kept and len(parts) >= 3 and len(parts) % 3 == 0 and \
                parts[2].lstrip("-").isdigit():
            # keypoints 行（单行多三元组 x y point3D_id）——收集引用点
            for j in range(2, len(parts), 3):
                pid = int(parts[j])
                if pid > 0:
                    used_pts.add(pid)
            im_lines.append(line)

    # 2) points3D.txt：保留被引用点
    pt_out = []
    with open(pt_path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            pid = int(line.split()[0])
            if pid in used_pts:
                pt_out.append(line)

    # 3) cameras.txt：保留（原样）
    cam_lines = [l for l in open(cam_path, encoding="utf-8")
                 if not l.startswith("#")]

    # 4) 写子集模型（更新头注释）
    n_im = sum(1 for l in im_lines if len(l.split()) == 10)
    with open(os.path.join(out_dir, "images.txt"), "w",
              encoding="utf-8") as fh:
        fh.write("# Number of images: %d (subset)\n" % n_im)
        fh.writelines(im_lines)
    with open(os.path.join(out_dir, "points3D.txt"), "w",
              encoding="utf-8") as fh:
        fh.write("# Number of points: %d (subset)\n" % len(pt_out))
        fh.writelines(pt_out)
    with open(os.path.join(out_dir, "cameras.txt"), "w",
              encoding="utf-8") as fh:
        fh.writelines(cam_lines)

    log.info("子集模型构建：%d 图像 / %d 点 -> %s", n_im, len(pt_out),
             out_dir)
    if n_im < 2:
        return None
    return out_dir


__all__ = ["build_subset_model"]
