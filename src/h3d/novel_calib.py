"""h3d.novel_calib : 双源融合 novel 阈值在线标定（P3，真实数据自适应）。

设计依据：固定切换阈值 0.15 经合成实验标定（对应 dense 与激光重叠 ≈85%
红线）。真实场景的 dense 密度/噪声不同，阈值应随数据分布自适应——
本模块按「dense 点距激光表面的距离分布」在线估计切换阈值：

    1) 距离直方图（对数空间）；
    2) 两峰之间找谷（简化 Otsu 分割：距离 <thr 视为重叠点，>thr 为新信息）；
    3) novel 阈值 = Otsu 分割出的新信息占比（clamp 0.05~0.40）。

对外：online_novel_threshold(base_pts, dense_pts) -> (threshold, novel_ratio)。
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from h3d.core import get_logger

log = get_logger("novel_calib")


def online_novel_threshold(base_pts: np.ndarray, dense_pts: np.ndarray,
                           bins: int = 64,
                           clamp: tuple = (0.05, 0.40)) -> tuple:
    """在线标定 novel 切换阈值。

    返回 (threshold, novel_ratio)：novel_ratio 为按 Otsu 分割计算的
    dense 新信息占比（切换阈值）；threshold 为对应距离（米）。
    """
    from scipy.spatial import cKDTree
    if len(base_pts) == 0 or len(dense_pts) == 0:
        return clamp[1], 0.0
    d, _ = cKDTree(np.asarray(base_pts)).query(np.asarray(dense_pts), k=1)
    d = np.asarray(d, dtype=np.float64)
    if d.max() <= 0:
        return clamp[0], 0.0
    # Otsu 分割（距离阈值）：类间方差最大化
    hist, edges = np.histogram(np.clip(d, 0, np.percentile(d, 98)), bins=bins)
    total = hist.sum()
    if total == 0:
        return clamp[1], 0.0
    w1 = np.cumsum(hist)
    w2 = total - w1
    m1 = np.cumsum(hist * np.arange(bins)) / np.maximum(w1, 1e-9)
    m2 = (np.cumsum(hist * np.arange(bins))[-1] -
          np.cumsum(hist * np.arange(bins))) / np.maximum(w2, 1e-9)
    var = w1 * w2 * (m1 - m2) ** 2
    k = int(np.argmax(var))
    thr_dist = float(edges[k + 1])
    novel = float((d > thr_dist).mean())
    novel = float(np.clip(novel, *clamp))
    return thr_dist, novel


__all__ = ["online_novel_threshold"]


def update_novel_threshold(prev: Optional[float], current: float,
                           lr: float = 0.3) -> float:
    """novel 阈值在线回写（P3）：指数平滑更新跨场景切换阈值。

    prev=None（首次）时直接采用 current；否则 prev*(1-lr)+current*lr。
    目标：真实数据多次运行后切换阈值收敛到该场景的稳定值。
    """
    if prev is None:
        return float(current)
    return float(prev * (1.0 - lr) + current * lr)


def save_novel_threshold(task_root: str, item_id: str,
                         novel: float, thr_m: float) -> str:
    """把在线标定结果回写到 90_meta/novel_threshold.json（跨运行持久化）。"""
    import json
    import os
    meta_dir = os.path.join(task_root, "90_meta")
    os.makedirs(meta_dir, exist_ok=True)
    path = os.path.join(meta_dir, "novel_threshold.json")
    data = {"item_id": item_id, "novel_ratio": novel,
            "threshold_m": thr_m}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
            prev = old.get("novel_ratio")
            if prev is not None:
                data["novel_ratio"] = update_novel_threshold(prev, novel)
        except Exception:  # noqa: BLE001
            pass
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return path


__all__ = ["online_novel_threshold", "update_novel_threshold",
           "save_novel_threshold"]
