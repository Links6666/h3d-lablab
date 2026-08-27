"""h3d.register.evaluate : RMSE / fitness 配准误差评估。

设计依据：
    《设计书》15.3 —— 输出配准误差矩阵，供 M5 汇总；残差 RMS 作为配准质量指标；
    《骨架分析》4.2 —— evaluate.py 承担「RMSE / fitness 误差评估」职责。

从 Open3D RegistrationResult 提取质量指标：fitness（内点占比）、inlier_rmse
（内点均方根误差）、对应点数量。配准误差是后续质检（M5）与 WW/T 0117 精度
对标的关键输入。
"""

from __future__ import annotations

from typing import Any


def fitness(result: Any) -> float:
    """配准内点占比（0~1，越高越好）。"""
    return float(result.fitness)


def inlier_rmse(result: Any) -> float:
    """配准内点均方根误差（单位同输入点云，越高越差）。"""
    return float(result.inlier_rmse)


def correspondence_count(result: Any) -> int:
    """对应点数量（correspondence_set 长度）。"""
    cs = getattr(result, "correspondence_set", None)
    return len(cs) if cs is not None else 0


def evaluate_registration(result: Any) -> dict:
    """汇总配准质量指标，返回结构化 dict。

    形如 ``{"fitness": 0.98, "inlier_rmse": 0.0012, "correspondence_count": 2048}``。
    """
    return {
        "fitness": fitness(result),
        "inlier_rmse": inlier_rmse(result),
        "correspondence_count": correspondence_count(result),
    }


__all__ = ["fitness", "inlier_rmse", "correspondence_count",
           "evaluate_registration"]
