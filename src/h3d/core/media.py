"""h3d.core.media : 图像文件识别（全管线统一口径）。

背景（2026-10-17 缺陷修复）
--------------------------
此前扩展名白名单在 7 处模块各自硬编码，口径不一致：

    config_edit / app / sfm_solve(calibrate)  → jpg/jpeg/png/tif
    sfm_solve(_prepare_photos / incremental)  → jpg/jpeg/png（缺 tif）
    ctrl_auto / incremental_depth             → jpg/jpeg/png（缺 tif）

由此产生两类实机问题：
1. 合法 JPEG 变体（``.jfif`` / ``.jpe``，Windows 与浏览器常见）被判定为"非照片"；
2. 同一张 ``.tif`` 在导入阶段通过、到 S1 预处理阶段却被静默丢弃。

本模块提供唯一事实源，所有"这是不是照片"的判断必须经由此处，
不得再在业务代码里写 ``endswith((".jpg", ...))``。
"""

from __future__ import annotations

import os
from typing import Iterable

#: 管线接受的图像扩展名（小写，含 JPEG 全部常见变体）。
IMAGE_EXTS: tuple[str, ...] = (
    ".jpg", ".jpeg", ".jpe", ".jfif",   # JPEG 家族
    ".png",
    ".tif", ".tiff",
)


def is_image_file(name: str) -> bool:
    """按扩展名判断是否为受支持图像（大小写不敏感）。

    >>> is_image_file("IMG_0001.JPG"), is_image_file("a.jfif"), is_image_file("a.txt")
    (True, True, False)
    """
    return os.path.splitext(name)[1].lower() in IMAGE_EXTS


def list_images(directory: str, recursive: bool = False) -> list[str]:
    """列出目录内的图像文件**绝对路径**（按文件名排序，保证可复现）。

    参数
    ----
    directory: 目标目录；不存在或不是目录时返回空列表（不抛异常）。
    recursive: 是否递归子目录（照片按采集批次分子目录时使用）。

    说明
    ----
    排序使用 ``os.path.normcase`` 作为 key 的补充：Windows 下文件系统不区分
    大小写，直接 ``sorted`` 在混合大小写文件名时顺序不稳定，进而影响
    COLMAP 图像注册顺序与结果可复现性。
    """
    if not directory or not os.path.isdir(directory):
        return []
    found: list[str] = []
    if recursive:
        for root, _dirs, files in os.walk(directory):
            for fn in files:
                if is_image_file(fn):
                    found.append(os.path.join(root, fn))
    else:
        for fn in os.listdir(directory):
            path = os.path.join(directory, fn)
            if os.path.isfile(path) and is_image_file(fn):
                found.append(path)
    found.sort(key=lambda p: os.path.normcase(os.path.basename(p)))
    return found


def count_images(directory: str, recursive: bool = False) -> int:
    """图像数量（供 GUI 校验快速调用）。"""
    return len(list_images(directory, recursive=recursive))


def first_unsupported(paths: Iterable[str]) -> str:
    """返回第一个非图像路径（供错误提示精确定位），全部合法时返回空串。"""
    for p in paths:
        if not is_image_file(p):
            return p
    return ""


def image_dialog_filter() -> str:
    """生成 Qt 文件对话框的名称过滤器字符串（唯一口径）。

    背景（2026-10-17 实机缺陷）
    --------------------------
    向导照片区双击时走的是 ``getExistingDirectory``（只能选目录），
    且文件对话框未设置过滤器，用户感知为"JPG/PNG 完全不显示"。
    过滤器必须与 :data:`IMAGE_EXTS` 同源，否则新增格式时又会出现
    "校验认识、对话框不显示"的分裂。

    返回
    ----
    形如 ``"图像文件 (*.jpg *.jpeg ...);;所有文件 (*.*)"`` 的字符串。
    """
    exts = " ".join(f"*{e}" for e in IMAGE_EXTS)
    return f"图像文件 ({exts});;所有文件 (*.*)"


__all__ = [
    "IMAGE_EXTS", "is_image_file", "list_images", "count_images",
    "first_unsupported", "image_dialog_filter",
]
