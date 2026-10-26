"""h3d.colour.checker_locator : ColorChecker 色卡自动定位（P3，替代随机采样）。

方案：色卡四角粘贴 ArUco 编码标记 → 检测 4 角 → 单应变换 → 提取 4×6
色块中心 → 每块平均色（sRGB）。真实采集时在色卡旁贴 4 个 ArUco，或
色卡本身带定位角标。无 ArUco 时回退启发式（亮框检测，可扩展）。

对外：locate_colorchecker(image_path, aruco_ids=(1,2,3,4)) -> 24×3 sRGB。
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np

from h3d.core import get_logger

log = get_logger("colour.checker")


def locate_colorchecker(image_path: str, grid: tuple = (4, 6),
                        aruco_ids: tuple = (1, 2, 3, 4)) -> Optional[np.ndarray]:
    """在照片中定位色卡并提取 4×6=24 色块平均色（sRGB 0~255）。

    返回 (24,3) 数组；定位失败返回 None。
    """
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover
        log.warning("需要 opencv-python-headless 进行色卡定位: %s", exc)
        return None
    img = cv2.imread(image_path)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    aruco = cv2.aruco
    aruco_dict = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
    detector = aruco.ArucoDetector(aruco_dict, aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is None or len(ids) < 4:
        log.warning("照片中未检测到 >=4 个 ArUco 标记（色卡定位失败）")
        return None
    # 按目标 id 顺序收集角点（缺 id 跳过）
    pts = {}
    for c, mid in zip(corners, ids.flatten()):
        if int(mid) in aruco_ids:
            pts[int(mid)] = c.reshape(-1, 2).mean(axis=0)
    if len(pts) < 4:
        log.warning("色卡定位标记不足（%d/4）", len(pts))
        return None
    # 用 4 个标记中心做仿射/透视近似：排序后按四角顺序建立 4×6 网格
    centers = np.array([pts[i] for i in sorted(pts)])
    tl = centers[np.argmin(centers.sum(axis=1))]
    br = centers[np.argmax(centers.sum(axis=1))]
    tr = centers[np.argmin(centers[:, 0] - centers[:, 1])]
    bl = centers[np.argmax(centers[:, 0] - centers[:, 1])]
    src = np.float32([tl, tr, bl, br])
    rows, cols = grid
    dst = np.float32([[0, 0], [cols - 1, 0], [0, rows - 1],
                      [cols - 1, rows - 1]])
    H = cv2.getPerspectiveTransform(src, dst)
    patches = []
    for r in range(rows):
        for c in range(cols):
            # 色块中心（格点间 0.5 偏移），再透视反投回图像坐标
            x, y = c, r
            inv = np.linalg.inv(H)
            v = np.array([x, y, 1.0])
            u = inv @ v
            u = u[:2] / u[2]
            # 取该格点邻域平均色（半径 8px，模拟色块内部）
            u0, v0 = int(u[0]), int(u[1])
            roi = img[max(0, v0 - 4):v0 + 5, max(0, u0 - 4):u0 + 5]
            if roi.size == 0:
                patches.append(np.zeros(3))
            else:
                bgr = roi.reshape(-1, 3).mean(axis=0)
                patches.append([bgr[2], bgr[1], bgr[0]])  # BGR->RGB
    return np.clip(np.array(patches), 0, 255)


__all__ = ["locate_colorchecker"]


def locate_colorchecker_robust(image_path: str, grid: tuple = (4, 6),
                               aruco_ids: tuple = (1, 2, 3, 4),
                               min_aruco: int = 3) -> Optional[np.ndarray]:
    """色卡定位鲁棒化（P3）：ArUco 不足时亮框回退 + 单应条件校验。

    分层策略：
      1) >=4 个 ArUco：透视单应（校验条件数）；
      2) 3 个 ArUco：仿射近似（3 点）——对轻微透视仍可用；
      3) 无/少 ArUco：亮度阈值检测白色外框矩形 → 4 角 → 仿射。
    返回 (24,3) 或 None。
    """
    try:
        import cv2
    except ImportError:
        return None
    img = cv2.imread(image_path)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    aruco = cv2.aruco
    det = aruco.ArucoDetector(aruco.getPredefinedDictionary(aruco.DICT_4X4_50),
                              aruco.DetectorParameters())
    corners, ids, _ = det.detectMarkers(gray)
    pts = {}
    if ids is not None:
        for c, mid in zip(corners, ids.flatten()):
            if int(mid) in aruco_ids:
                pts[int(mid)] = c.reshape(-1, 2).mean(axis=0)
    centers = None
    if len(pts) >= 4:
        centers = np.array([pts[i] for i in sorted(pts)][:4])
    elif len(pts) >= min_aruco:
        # 3 点仿射：补齐第 4 角（平行四边形）
        c = np.array(list(pts.values())[:3])
        centers = np.vstack([c, c[0] + c[1] - c[2]])
    else:
        centers = _detect_white_frame(gray)
    if centers is None or len(centers) < 4:
        log.warning("色卡定位鲁棒化失败（ArUco %d 个 + 无亮框）", len(pts))
        return None

    # 单应条件校验：退化（近共线/奇异）拒绝
    tl = centers[np.argmin(centers.sum(axis=1))]
    br = centers[np.argmax(centers.sum(axis=1))]
    tr = centers[np.argmin(centers[:, 0] - centers[:, 1])]
    bl = centers[np.argmax(centers[:, 0] - centers[:, 1])]
    src = np.float32([tl, tr, bl, br])
    rows, cols = grid
    dst = np.float32([[0, 0], [cols - 1, 0], [0, rows - 1], [cols - 1, rows - 1]])
    try:
        H = cv2.getPerspectiveTransform(src, dst)
    except cv2.error:
        return None
    if np.linalg.cond(H) > 1e4:  # 近退化单应拒绝
        log.warning("色卡单应退化（cond=%.0f），定位失败", np.linalg.cond(H))
        return None
    patches = []
    inv = np.linalg.inv(H)
    for r in range(rows):
        for ccol in range(cols):
            u = inv @ np.array([ccol, r, 1.0])
            u = u[:2] / u[2]
            u0, v0 = int(u[0]), int(u[1])
            roi = img[max(0, v0 - 4):v0 + 5, max(0, u0 - 4):u0 + 5]
            if roi.size == 0:
                patches.append(np.zeros(3))
            else:
                bgr = roi.reshape(-1, 3).mean(axis=0)
                patches.append([bgr[2], bgr[1], bgr[0]])
    return np.clip(np.array(patches), 0, 255)


def _detect_white_frame(gray) -> Optional[np.ndarray]:
    """亮框检测：二值化找最大白色矩形（色卡白边）→ 4 角。"""
    import cv2
    _, bw = cv2.threshold(gray, 230, 255, cv2.THRESH_BINARY)
    cnts, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    big = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(big) < gray.size * 0.01:
        return None
    rect = cv2.minAreaRect(big)
    box = cv2.boxPoints(rect)
    return box.astype(np.float64)


__all__ = ["locate_colorchecker", "locate_colorchecker_robust"]
