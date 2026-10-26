"""h3d.preprocess.background : 背景 / 台面剔除（纯照片场景专用）。

设计依据：
    《设计书》32.2 —— M1 预处理负责把点云整理成"只有文物本体"的几何；
    《教学版》15.3 —— 平面分割 segment_plane + 聚类 cluster_dbscan。

为什么需要这一步（2026-10-18 实机，relic_001 纯照片重建）：

    30 张照片绕拍一件小器物，COLMAP 稠密重建把**白色衬板与桌面**一起建了出来。
    M3 Poisson 之后得到 25 个连通块，最大块仅占 27.8% 面积；M4 贴图本身正常
    （岛内填充率 94.5%），但整片"白色"的几何其实就是衬板 —— 用户看到的
    "3D 预览非常糟糕、看不出颜色"里，很大一部分是**几何选错了对象**，
    不是贴图没贴上。

    实测（relic_001，60258 点，30 视角）：
      * DBSCAN(eps=2% 对角线) 得 27 个簇，**最大簇 42990 点 = 71.3%**，
        extent 4.86×2.73×2.62、平面度 λ3/λ1 = 0.088 —— 这就是器物本身；
      * 而整个点云的包围盒是 14.64×…（被零散碎片撑大 3 倍），
        于是 3D 预览里"器物缩成角落一个小点 + 一堆白片"。
    所以处方是「保留主簇」，不是「剔平面」（详见 :func:`remove_background` 的
    「实测结论」段）。

职责边界：只做「平面剔除 + 主簇保留」，输出仍是 PointCloud；
阈值全部以包围盒对角线为基准（与器物绝对尺寸无关）。
业务层不许 top-level import open3d，故沿用本包 ``normal_estimate`` 的
``_o3d()`` 惰性导入惯例。
"""

from __future__ import annotations

import numpy as np

from h3d.core import get_logger

log = get_logger("preprocess.background")


def _o3d():
    import open3d as o3d

    return o3d


def mean_point_spacing(points, sample: int = 20000) -> float:
    """平均点间距 MPS = 采样点到最近邻距离的中位数。

    以**点间距**而不是包围盒对角线作尺度基准，是聚类半径能自适应任意点云
    密度的前提（见 :func:`remove_background` 的 2026-10-23 实机说明）。
    """
    pts = np.asarray(points, dtype=np.float64)
    n = len(pts)
    if n < 2:
        return 0.0
    from scipy.spatial import cKDTree

    idx = np.linspace(0, n - 1, min(int(sample), n)).astype(np.int64)
    d, _ = cKDTree(pts).query(pts[idx], k=2)
    return float(np.median(d[:, 1]))


def _decimate_to(points, target: int, mps: float, diag: float):
    """体素降采样把点数压到 ``target`` 以内，返回 ``(index, voxel)``。

    ``index`` 是保留下来的点索引（相对原始点云），用于把聚类标签回投。
    """
    idx = np.arange(len(points), dtype=np.int64)
    if len(points) <= target:
        return idx, 0.0
    # 面状点云点数 ∝ 1/voxel²，先按面积律估一版，再按实测迭代
    voxel = max(mps * np.sqrt(len(points) / float(target)), diag * 1e-4)
    for _ in range(4):
        keep = _voxel_index(points, voxel)
        if len(keep) <= target:
            return keep, voxel
        voxel *= 1.4
    return keep, voxel


def _voxel_index(points, voxel: float):
    """返回体素降采样后的代表点索引（取每个体素内点的中位位置）。"""
    import open3d as o3d

    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
    down, _, trace = pcd.voxel_down_sample_and_trace(
        voxel, pcd.get_min_bound(), pcd.get_max_bound(), False)
    # trace[i] = 落入该体素的原点索引（至少一个）
    return np.asarray([int(t[0]) for t in trace], dtype=np.int64)


def remove_background(pcd,
                      max_planes: int = 0,
                      plane_dist_frac: float = 0.005,
                      min_plane_frac: float = 0.10,
                      plane_span_frac: float = 0.50,
                      cluster_eps_frac: float = 0.02,
                      cluster_eps_k: float = 10.0,
                      max_cluster_points: int = 150000,
                      min_cluster_points: int = 10,
                      keep_cluster_frac: float = 0.25,
                      min_remain_frac: float = 0.20):
    """剔除背景平面并只保留主连通簇。

    参数
    ----
    pcd:              ``h3d.core.PointCloud``（输入不被修改）。
    max_planes:       最多剔除几个平面。**默认 0（不剔平面）**——理由见下方实测。
    plane_dist_frac:  RANSAC 距离阈值 = 该值 × 包围盒对角线。
    min_plane_frac:   内点数少于「当前点云 × 该比例」就不再当作背景平面。
    plane_span_frac:  平面内点包围盒的**次大跨度**须 ≥ 该比例 × 对角线，
                      用来排除"器物自身的平面"（平板类文物、底座大平面）。
    cluster_eps_frac: DBSCAN 邻域半径 = min(该值 × 对角线, cluster_eps_k × MPS)。
    cluster_eps_k:    邻域半径的**密度基准**（默认 10 × MPS）。与点云密度解耦，
                      否则稠密点云上 eps 会变成上百倍 MPS，邻域表直接撑爆内存。
    max_cluster_points: 聚类工作副本的点数上限（默认 15 万）；超过就先体素
                      降采样再聚类，最后把标签按最近邻回投到全分辨率点云。
    min_cluster_points: DBSCAN 最小簇点数。
    keep_cluster_frac: 只保留「点数 ≥ 最大簇 × 该比例」的簇（默认 0.25）。
    min_remain_frac:  **安全网**：剔除后剩余点少于原始 × 该比例时整体回滚
                      （宁可不剔，也不能把文物剔没了）。

    实测结论（relic_001，60258 点，务必保留这条经验）
    -------------------------------------------------
    想当然的"RANSAC 连剔 3 个主平面"在这批数据上是**错的**：
      * 主平面内点的次大跨度只有 1.97（对角线 14.64 的 13%），说明背景不是
        一整块衬板，而是被重建切碎的若干小片；
      * 真正被建出来的器物本身是 DBSCAN 的**最大簇**（42990 点 = 71.3%，
        extent 4.86×2.73×2.62、平面度 λ3/λ1 = 0.088，明显不是平面）。
    所以正确动作是「保主簇」而不是「剔平面」：背景碎片的危害不是"变成模型"，
    而是把包围盒从 4.86 撑到 14.64 —— 于是 3D 预览里器物缩成一个角落的小点。
    平面剔除保留为可选步骤（用户确有整块衬板时才开）。

    返回
    ----
    ``(clean_pcd, metrics)``；``metrics`` 含
    ``bg_removed_planes / bg_removed_points / bg_removed_ratio /
    bg_clusters / bg_kept_clusters / bg_kept_points / bg_kept_ratio /
    bg_rollback / bg_note``。
    """
    o3d = _o3d()
    points = np.asarray(pcd.points, dtype=np.float64)
    n0 = len(points)
    metrics = {
        "bg_removed_planes": 0,
        "bg_removed_points": 0,
        "bg_removed_ratio": 0.0,
        "bg_clusters": 0,
        "bg_kept_clusters": 0,
        "bg_kept_points": n0,
        "bg_kept_ratio": 1.0,
        "bg_rollback": False,
        "bg_note": "",
    }
    if n0 < 200:
        metrics["bg_note"] = "点云过小（<200），跳过背景剔除"
        return pcd, metrics

    diag = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
    if diag <= 0:
        metrics["bg_note"] = "包围盒退化，跳过背景剔除"
        return pcd, metrics

    work = o3d.geometry.PointCloud(pcd)
    removed = 0
    for _ in range(int(max(0, max_planes))):
        if len(work.points) < 200:
            break
        try:
            _model, inliers = work.segment_plane(
                distance_threshold=plane_dist_frac * diag,
                ransac_n=3, num_iterations=2000)
        except Exception as exc:  # noqa: BLE001 - 分割失败不影响主流程
            metrics["bg_note"] = f"平面分割失败：{exc}"[:200]
            log.warning("背景平面分割失败（跳过剔除）：%s", exc)
            break
        frac = len(inliers) / max(1, len(work.points))
        if frac < min_plane_frac:
            break
        # 只剔"撑满场景"的平面：器物自身的面跨度有限，衬板/桌面几乎覆盖全图
        pts = np.asarray(work.points)[inliers]
        span = np.sort(pts.max(axis=0) - pts.min(axis=0))[::-1]
        if len(span) < 2 or span[1] < plane_span_frac * diag:
            metrics["bg_note"] = (f"第 {removed + 1} 个候选平面跨度不足"
                                  f"（次大 {span[1] if len(span) > 1 else 0:.3f}"
                                  f" < {plane_span_frac * diag:.3f}），按器物面处理")
            break
        work = work.select_by_index(inliers, invert=True)
        removed += 1

    metrics["bg_removed_planes"] = removed
    metrics["bg_removed_points"] = n0 - len(work.points)
    metrics["bg_removed_ratio"] = round(1.0 - len(work.points) / n0, 4)

    # 主簇保留（无论是否剔平面）：这一步才是"预览里器物缩成小点"的解药。
    # 背景碎片真正危害的是**包围盒**——relic_001 的主簇只有 4.86 宽，
    # 却被零散碎片撑到 14.64，相机自动取景就把器物压成角落一个小点。
    #
    # ===== 2026-10-23 实机致命缺陷：聚类半径与点密度脱钩 =====
    # 原实现 ``eps = cluster_eps_frac * diag`` 只跟包围盒走，完全不管点云有多密：
    # 真实 fused_dense.ply 有 677274 点、MPS=0.0030、diag=15.35，于是
    # eps = 0.02×15.35 = 0.307 = **102×MPS**，每点邻域 279 个点
    # → Open3D 的 cluster_dbscan 是单线程、逐点建完整邻域表的实现，
    #   677k×279 ≈ 1.9 亿条边，实测 4 分 44 秒时 RSS 已 4.6 GB 且仍在涨
    #   （用户实机：M1 跑 50 分钟没结束、内存 10 GB，最后只能取消）。
    # 同一条代码在 6 万点的旧数据上只要 1.4 s——**点数越大越灾难**，
    # 而这恰恰是"照片越多、重建越细"的正常演进方向。
    #
    # 处方（三重保险，缺一不可）：
    #   ① 尺度基准改为 MPS：eps = min(frac×diag, k×MPS)，与密度解耦；
    #   ② 聚类只在**降采样工作副本**上做（≤ max_cluster_points），
    #      再把标签按最近邻回投到全分辨率点云（保留全部原始点）；
    #   ③ 邻域规模看门狗：实测平均邻域 > 128 就收紧 eps，仍超就放弃聚类
    #      只做平面剔除并写 metrics + WARNING（禁止静默降级）。
    from scipy.spatial import cKDTree

    work_pts = np.asarray(work.points, dtype=np.float64)
    metrics["bg_backprojected"] = 0

    # ② 工作副本：先把聚类规模压到常数级（聚类只判"哪些点属同一件器物"，
    # 不需要全分辨率；标签随后回投，原始点一个不丢）
    mps0 = mean_point_spacing(work_pts)
    sel_idx, voxel_used = _decimate_to(work_pts, int(max_cluster_points),
                                       mps0, diag)
    back_to_full = len(sel_idx) < len(work_pts)
    if back_to_full:
        sub = o3d.geometry.PointCloud(
            o3d.utility.Vector3dVector(work_pts[sel_idx]))
        metrics["bg_cluster_voxel"] = round(voxel_used, 6)
    else:
        sub = work
    sub_pts = np.asarray(sub.points, dtype=np.float64)
    metrics["bg_mps"] = round(mps0, 6)
    metrics["bg_cluster_points"] = len(sub_pts)

    # ① 尺度基准改为**工作副本的 MPS**：eps = min(frac×diag, k×MPS)。
    # 密度越高的点云 eps 自动越小，邻域规模被钉在 ~(k 决定的常数) 量级。
    mps = mean_point_spacing(sub_pts) if len(sub_pts) > 2 else mps0
    eps_diag = cluster_eps_frac * diag
    eps_mps = max(float(cluster_eps_k), 1.0) * mps if mps > 0 else eps_diag
    eps = min(eps_diag, eps_mps)
    metrics["bg_cluster_mps"] = round(mps, 6)

    # ③ 邻域看门狗（在真正参与聚类的副本上评估）：抽样 300 点。
    #    判据用**邻域表规模** n_cluster × avg_neighbors 而不是邻居数本身——
    #    内存/耗时都正比于边数；面状点云上 avg_neighbors ≈ πk²（k=10 时约 314），
    #    只卡邻居数会把正常点云误判成"密度异常"而白白跳过聚类。
    _EDGE_BUDGET = 6e7  # 6 千万条边 ≈ 240 MB 邻域表，实测耗时数秒

    def _nbrs(eps_v: float) -> float:
        i_ = np.linspace(0, len(sub_pts) - 1,
                         min(300, len(sub_pts))).astype(np.int64)
        return float(np.mean(tree_probe.query_ball_point(
            sub_pts[i_], eps_v, return_length=True)))

    probe_idx = np.linspace(0, len(sub_pts) - 1,
                            min(300, len(sub_pts))).astype(np.int64)
    tree_probe = cKDTree(sub_pts)
    nbrs = _nbrs(eps)
    cost = len(sub_pts) * nbrs
    # 超预算就逐级收紧（k×MPS → 4×MPS → 2×MPS），而不是一句"密度异常"跳过：
    # 跳过等于背景剔除失效，用户又会看到"白花花一片"，属于静默降级。
    if cost > _EDGE_BUDGET:
        for shrink in (eps_mps, 4.0 * mps, 2.0 * mps):
            if shrink < eps:
                log.warning("背景聚类邻域过大（%.2e 条边，平均 %.0f 邻居），"
                            "eps %.4f -> %.4f 收紧", cost, nbrs, eps, shrink)
                eps = shrink
                nbrs = _nbrs(eps)
                cost = len(sub_pts) * nbrs
                if cost <= _EDGE_BUDGET:
                    break
    metrics["bg_eps"] = round(eps, 6)
    metrics["bg_eps_mps_ratio"] = round(eps / mps, 2) if mps > 0 else None
    metrics["bg_cluster_neighbors"] = round(nbrs, 1)
    metrics["bg_cluster_edges"] = int(cost)
    if cost > _EDGE_BUDGET:
        # 密度极端异常（重复点 / 点云极密）：聚类不可控，只做平面剔除
        metrics["bg_note"] = (f"聚类邻域表 {cost:.2e} 条边过大，跳过主簇保留"
                              "（仅做平面剔除）；可下调 bg_cluster_eps_k")
        log.warning("背景聚类跳过：%d 点 × 平均 %.0f 邻居 = %.2e 条边，"
                    "DBSCAN 在此规模下会耗尽内存", len(sub_pts), nbrs, cost)
        metrics["bg_kept_points"] = len(work.points)
        metrics["bg_kept_ratio"] = round(len(work.points) / n0, 4)
        return work, metrics

    try:
        sub_labels = np.asarray(sub.cluster_dbscan(
            eps=eps, min_points=int(min_cluster_points)))
    except Exception as exc:  # noqa: BLE001
        metrics["bg_note"] = f"聚类失败（仅做平面剔除）：{exc}"[:200]
        log.warning("背景主簇聚类失败（保留全部非平面点）：%s", exc)
        clean = work
        metrics["bg_kept_points"] = len(clean.points)
        metrics["bg_kept_ratio"] = round(len(clean.points) / n0, 4)
        return clean, metrics

    if back_to_full:
        # 标签回投：每个原始点取最近的工作副本点标签
        labels = sub_labels[cKDTree(sub_pts).query(work_pts, k=1)[1]]
        metrics["bg_backprojected"] = 1
    else:
        labels = sub_labels

    valid = labels >= 0
    uniq, counts = np.unique(labels[valid], return_counts=True)
    metrics["bg_clusters"] = int(len(uniq))
    if len(uniq) == 0:
        metrics["bg_note"] = "聚类无有效簇（保留平面剔除后的全部点）"
        clean = work
    else:
        order = np.argsort(-counts)
        keep_ids = [uniq[order[0]]]
        for idx in order[1:]:
            if counts[idx] >= keep_cluster_frac * counts[order[0]]:
                keep_ids.append(uniq[idx])
        mask = np.isin(labels, keep_ids)
        # 索引必须落在 work 上（work 此时仍是全分辨率点云）
        clean = work.select_by_index(np.where(mask)[0])
        metrics["bg_kept_clusters"] = len(keep_ids)

    kept = len(clean.points)
    metrics["bg_kept_points"] = kept
    metrics["bg_kept_ratio"] = round(kept / n0, 4)

    # 安全网：剔过头就整体回滚（对齐口径：宁可留着背景，也不能删掉文物）
    if kept < min_remain_frac * n0:
        metrics["bg_rollback"] = True
        metrics["bg_note"] = (f"剔除后仅剩 {kept}/{n0} 点（<{min_remain_frac:.0%}），"
                              "已回滚为原始点云；请检查 bg_plane_dist_frac / "
                              "bg_max_planes 是否过于激进")
        log.warning("背景剔除过度（剩 %.1f%%），已回滚：%s",
                    kept / n0 * 100, metrics["bg_note"])
        return pcd, metrics

    log.info("背景剔除：%d 个平面 -%d 点（%.0f%%），保留 %d/%d 个簇 -> %d 点（%.0f%%）",
             removed, metrics["bg_removed_points"],
             metrics["bg_removed_ratio"] * 100,
             metrics["bg_kept_clusters"], metrics["bg_clusters"],
             kept, kept / n0 * 100)
    return clean, metrics


__all__ = ["remove_background", "mean_point_spacing"]
