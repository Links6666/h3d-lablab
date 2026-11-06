"""h3d.register : 异源点云配准（L2 业务层 / S2 阶段）—— FPFH+RANSAC 粗配准 + ICP 精配准。

设计依据：
    《设计书》15 / 33 —— M2 配准：FPFH+RANSAC 全局粗配准 + point-to-plane ICP 精配准，
        输出配准残差 RMS 作为质量指标；
    《教学版》16 —— 两段式配准思路（全局粗配准 + ICP 精配准）；
    《骨架分析》4.2 —— register 子包承担「FPFH 特征 / RANSAC / ICP / 误差评估」职责。

对外暴露四个子模块的函数，并提供 :func:`run` 两段式配准流水线（配置驱动）。输入为两片
异源点云（Open3D PointCloud），输出统一配准结果字典（变换矩阵 + fitness + inlier_rmse）。
文件读写由 io 层负责，本层只做几何配准。

典型用法::

    from h3d.io import load_point_cloud
    from h3d.core import load
    from h3d.register import run

    cfg = load("configs/example.yaml")
    src = load_point_cloud("10_clean/luohan_001_s01.ply")
    tgt = load_point_cloud("10_clean/luohan_001_s02.ply")
    res = run(src, tgt, cfg.register)          # {"transformation": T, "fitness": ..., ...}
"""

from __future__ import annotations

import numpy as np

from h3d.core import RegistrationError, RegisterConfig, get_logger

from .feature import compute_fpfh, ensure_normals
from .ransac import global_registration
from .icp import icp_refine
from .evaluate import (
    evaluate_registration,
    fitness,
    inlier_rmse,
    correspondence_count,
)

log = get_logger("register")

# 最低 fitness 阈值（内点占比）。低于此视为「重叠不足 / 配准失败」，见《设计书》33.4。
# 正确配准 ICP fitness 通常接近 1.0，无重叠时 < 0.2，阈值取 0.2 可稳健区分二者
# （并与 WW/T 0117「相邻站重叠度 ≥ 30%」的精度基线同量级）。
_MIN_FITNESS = 0.2
# 粗配准失败回退时，对应距离放大倍数（《设计书》33.4「回退更粗半径重试一次」）
_FALLBACK_DISTANCE_RATIO = 2.0

#: 「配准可信度」告警阈值（不等于失败阈值）。两站同源扫描的正确配准 fitness
#: 通常在 0.8 以上；落到 0.3~0.5 多是**近似对称物体被解成翻转解**——几何看着
#: 贴合、方向却差 180°，拼出来的模型是"镜像"的。1.1.12 验证实测：注入 30°，
#: RANSAC 给出 166.6° 的翻转解、fitness 只有 0.336，而旧实现只判 < 0.2 就
#: 当成功，一路合格到交付。
_LOW_CONFIDENCE_FITNESS = 0.55

#: RANSAC 的随机种子（2026-11-05）。**不定种子会让同一份输入跑出不同结果**：
#: 真机点云注入 120° 旋转，17 次里 3 次解出 179° 翻转解（fitness 0.52），
#: 其余 14 次正确（fitness 1.00）。配准结果会被写进报表与交付元数据，
#: 不可复现就等于不可引用（与 M5 采样定种子同一个理由，ADR-0020/0023）。
_RANSAC_SEED = 20261105

#: 朝向判据的**死区**：法线一致性 < −0.2 才判「方向相反」。
#: 不取 0 的原因：这个量是逐点法线点积的均值，在小型/近球状的合成点云上会在
#: 0 附近抖（构件噪声、法线定向不稳），拿 0 当门会误杀正确解（实测一次端到端
#: 测试因此偶发失败）。而真机翻转解是 −0.37 ~ −0.60，正确解是 +0.85 ~ +0.98，
#: 中间这条 0.2 的死区只是「分不清」，不该拦。
_FLIP_MARGIN = 0.2

#: 朝向判据的**死区**：法线一致性 < −0.2 才判「方向相反」。
#: 不取 0 的原因：这个量是逐点法线点积的均值，在小型/近球状的合成点云上会在
#: 0 附近抖（构件噪声、法线定向不稳），拿 0 当门会误杀正确解（实测一次端到端
#: 测试因此偶发失败）。而真机翻转解是 −0.37 ~ −0.60，正确解是 +0.85 ~ +0.98，
#: 中间这条 0.2 的死区只是「分不清」，不该拦。
_FLIP_MARGIN = 0.2

#: RANSAC 的随机种子（2026-11-05）。**不定种子会让同一份输入跑出不同结果**：
#: 真机点云注入 120° 旋转，17 次里 3 次解出 179° 翻转解（fitness 0.52），
#: 其余 14 次正确（fitness 1.00）。配准结果会被写进报表与交付元数据，
#: 不可复现就等于不可引用（与 M5 采样定种子同一个理由，ADR-0020/0023）。
_RANSAC_SEED = 20261105

#: 朝向判据的**死区**：法线一致性 < −0.2 才判「方向相反」。
#: 不取 0 的原因：这个量是逐点法线点积的均值，在小型/近球状的合成点云上会在
#: 0 附近抖（构件噪声、法线定向不稳），拿 0 当门会误杀正确解（实测一次端到端
#: 测试因此偶发失败）。而真机翻转解是 −0.37 ~ −0.60，正确解是 +0.85 ~ +0.98，
#: 中间这条 0.2 的死区只是「分不清」，不该拦。
_FLIP_MARGIN = 0.2

#: 朝向判据的**死区**：法线一致性 < −0.2 才判「方向相反」。
#: 不取 0 的原因：这个量是逐点法线点积的均值，在小型/近球状的合成点云上会在
#: 0 附近抖（构件噪声、法线定向不稳），拿 0 当门会误杀正确解（实测一次端到端
#: 测试因此偶发失败）。而真机翻转解是 −0.37 ~ −0.60，正确解是 +0.85 ~ +0.98，
#: 中间这条 0.2 的死区只是「分不清」，不该拦。
_FLIP_MARGIN = 0.2


def _rotation_about(center: np.ndarray, axis: np.ndarray,
                    theta: float) -> np.ndarray:
    """绕给定点与轴旋转 ``theta`` 的 4x4 齐次矩阵。"""
    a = np.asarray(axis, dtype=np.float64)
    n = np.linalg.norm(a)
    if n < 1e-12:
        return np.eye(4)
    a = a / n
    K = np.array([[0.0, -a[2], a[1]],
                  [a[2], 0.0, -a[0]],
                  [-a[1], a[0], 0.0]])
    R = np.eye(3) + np.sin(theta) * K + (1.0 - np.cos(theta)) * (K @ K)
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = np.asarray(center, dtype=np.float64) - R @ np.asarray(
        center, dtype=np.float64)
    return T


def _principal_axes(points: np.ndarray, k: int = 2) -> list[np.ndarray]:
    """点云主惯性轴（按方差降序），用于生成翻转候选初值。"""
    P = np.asarray(points, dtype=np.float64)
    if len(P) < 8:
        return []
    P = P - P.mean(axis=0)
    w, V = np.linalg.eigh(P.T @ P)
    order = np.argsort(-w)
    return [V[:, i] for i in order[:k]]


def _icp_multi_init(source, target, init: np.ndarray, cfg: RegisterConfig):
    """以 RANSAC 解 + 主轴 180° 翻转解为初值分别做 ICP，取 fitness 最高者。

    为什么要多初值：中小型造像（俑、佛、瓶）近似**轴对称**，FPFH+RANSAC 在
    大角度错位时极易解到「转 180° 的翻版」——它让尽量多的点撞上对侧表面，
    fitness 仍能过 0.2 的失败门。多给两个翻转初值再各自 ICP，正确朝向通常
    能拿到显著更高的 fitness，从而被选出来。
    """
    tgt_pts = np.asarray(target.points, dtype=np.float64)
    center = tgt_pts.mean(axis=0)
    cands: list[tuple[str, np.ndarray]] = [("ransac", np.asarray(init, np.float64))]
    for i, axis in enumerate(_principal_axes(tgt_pts)):
        cands.append((f"flip180_axis{i + 1}",
                      _rotation_about(center, axis, np.pi) @ np.asarray(
                          init, np.float64)))

    best = None
    best_tag = "ransac"
    tried = []
    for tag, T0 in cands:
        try:
            r = icp_refine(source, target, T0, cfg.icp_distance,
                           cfg.max_iter, point_to_plane=True)
        except Exception as exc:  # noqa: BLE001 - 单个初值失败不影响其它候选
            log.warning("ICP 初值 %s 失败（跳过）：%s", tag, exc)
            continue
        tried.append({"init": tag, "fitness": round(float(r.fitness), 4),
                      "rmse": round(float(r.inlier_rmse), 6)})
        if best is None or (r.fitness, -r.inlier_rmse) > (best.fitness,
                                                          -best.inlier_rmse):
            best, best_tag = r, tag
    return best, best_tag, tried


def _ransac_with_fallback(source, target, source_fpfh, target_fpfh,
                          max_correspondence_distance: float):
    """RANSAC 粗配准，失败时回退更粗对应距离重试一次。"""
    result = global_registration(source, target, source_fpfh, target_fpfh,
                                 max_correspondence_distance)
    if result.fitness < _MIN_FITNESS:
        coarser = max_correspondence_distance * _FALLBACK_DISTANCE_RATIO
        log.warning("粗配准 fitness=%.3f 低于阈值，回退更粗距离 %.4f 重试",
                    result.fitness, coarser)
        result = global_registration(source, target, source_fpfh, target_fpfh,
                                     coarser)
    return result


def run(source, target, cfg: RegisterConfig) -> dict:
    """两段式异源点云配准（FPFH+RANSAC 粗配准 + ICP 精配准）。

    流程：法线补齐 + FPFH 特征 → RANSAC 全局粗配准（失败回退更粗距离）→ ICP
    精配准 → 质量评估。粗配准 fitness 仍低于阈值时抛
    :class:`RegistrationError`（E200，重叠不足）。

    参数
    ----
    source / target : 源 / 目标点云（Open3D PointCloud）。
    cfg             : RegisterConfig（fpfh_radius / ransac_distance /
                      icp_distance / max_iter）。

    返回
    ----
    dict，含 ``transformation``（4x4 刚体变换）、``fitness``、``inlier_rmse``、
    ``correspondence_count`` 与 ``ransac_fitness``。
    """
    if source is None or target is None or not source.has_points() \
            or not target.has_points():
        from h3d.core import InputDataError
        raise InputDataError("配准输入点云为空（0 点）")

    # 0) 定种子：RANSAC 内部用 Open3D 的进程级随机源。不定种子时同一份
    #    输入两次可能给出**不同的初值**，进而选出不同的翻转解。
    from h3d.io import seed_rng

    seed_rng(_RANSAC_SEED)

    # 0) 定种子：RANSAC 内部用 Open3D 的进程级随机源。不定种子时同一份
    #    输入两次可能给出**不同的初值**，进而选出不同的翻转解。
    from h3d.io import seed_rng

    seed_rng(_RANSAC_SEED)

    # 1) FPFH 特征（内部自动补齐法线）
    f_src = compute_fpfh(source, cfg.fpfh_radius)
    f_tgt = compute_fpfh(target, cfg.fpfh_radius)

    # 2) RANSAC 全局粗配准（失败回退更粗距离，best-effort）
    ransac = _ransac_with_fallback(source, target, f_src, f_tgt,
                                   cfg.ransac_distance)

    # 3) ICP 精配准（point-to-plane；多初值：RANSAC 解 + 主轴 180° 翻转解）
    #
    # 单一 RANSAC 初值在近似轴对称物体上会解到「翻转 180° 的版本」：点位对得
    # 上、方向反了。1.1.12 端到端验证实测注入 30°，恢复出 166.6°、fitness
    # 0.336，而失败门 0.2 —— 一路"合格"。多初值让正确朝向凭 fitness 胜出。
    icp, icp_init, icp_trials = _icp_multi_init(
        source, target, ransac.transformation, cfg)
    if icp is None:  # 所有初值都抛错：退回单次，让下面按失败处理
        icp = icp_refine(source, target, ransac.transformation,
                         cfg.icp_distance, cfg.max_iter, point_to_plane=True)
        icp_init, icp_trials = "ransac", []

    # 4) 质量判定：精配准后 fitness 过低 = 重叠不足 / 配准失败（《设计书》33.4）
    if icp.fitness < _MIN_FITNESS:
        raise RegistrationError(
            f"配准失败（重叠不足，ICP fitness={icp.fitness:.3f} < "
            f"{_MIN_FITNESS}）")

    # 5) 质量评估
    metrics = evaluate_registration(icp)
    # 朝向判据（2026-11-05，ADR-0025）：fitness 只回答「贴不贴」，回答不了
    # 「方向对不对」。近似轴对称的造像被解成 179° 翻转时，几何贴合得很好
    # （中位间距 0.75% 对角线）、fitness 也可能不低，只有法线方向是反的。
    from .evaluate import normal_agreement

    metrics["normal_agreement"] = normal_agreement(
        source, target, icp.transformation, cfg.icp_distance,
        cfg.fpfh_radius)
    metrics["transformation"] = np.asarray(icp.transformation, dtype=np.float64)
    metrics["ransac_fitness"] = float(ransac.fitness)
    metrics["icp_init"] = icp_init
    if icp_trials:
        metrics["icp_trials"] = icp_trials
    # 朝向闸门：法线一致性为负 = 解到了「翻转的版本」。这是 fitness 看不见的
    # 那一维——真机 179° 翻转解 fitness 0.52、中位间距仅 0.75% 对角线，两个
    # 距离类判据都放行，只有方向是反的。宁可判失败，也不让反向几何进下游
    # （M3 会按错位姿合并点云）。
    # 两个条件**同时**成立才判失败，这是 C 与 A 的分界：
    #   fitness 低 + 方向反 → 是翻转解（真机：0.52 / −0.52）→ 拒绝
    #   fitness 低 + 方向正 → 合法的低重叠（实测 0.23 / +0.85）→ 只标 low_confidence
    #   fitness 高 + 方向反 → 物体本身近似对称，翻转与正解不可区分 → 不拦
    #                        （拦了也只是二选一，没有更好的解可选）
    _na = metrics.get("normal_agreement")
    if (_na is not None and _na < -_FLIP_MARGIN
            and metrics.get("fitness", 1.0) < _LOW_CONFIDENCE_FITNESS):
        raise RegistrationError(
            f"配准方向相反（法线一致性 {_na:+.3f} < -{_FLIP_MARGIN}）："
            f"几何贴合但朝向被解成"
            f"翻转版本（ICP 初值={icp_init}，fitness={metrics['fitness']:.3f}）。"
            "请检查两站点云的法线朝向，或提供控制点（X2）约束朝向")

    if metrics["fitness"] < _LOW_CONFIDENCE_FITNESS:
        # 不判失败（真实低重叠场景确实存在），但必须留痕：报表与交付元数据
        # 要能看出"这次配准可信度低"，而不是只看到一个漂亮的 rmse。
        metrics["low_confidence"] = True
        log.warning("配准可信度偏低：fitness=%.3f < %.2f（ICP 初值=%s，"
                    "试过 %d 个初值）——近似对称物体可能被解成翻转解，"
                    "请人工核对拼接方向", metrics["fitness"],
                    _LOW_CONFIDENCE_FITNESS, icp_init, len(icp_trials) or 1)
    log.info("配准完成：fitness=%.3f, inlier_rmse=%.5f, 对应点=%d, 初值=%s",
             metrics["fitness"], metrics["inlier_rmse"],
             metrics["correspondence_count"], icp_init)
    return metrics


__all__ = [
    "compute_fpfh",
    "ensure_normals",
    "global_registration",
    "icp_refine",
    "evaluate_registration",
    "fitness",
    "inlier_rmse",
    "correspondence_count",
    "run",
]
