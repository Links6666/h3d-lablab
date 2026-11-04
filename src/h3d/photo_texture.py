"""h3d.photo_texture : P1 可选 stage —— 照片投影纹理（场景①自动化闭环）。

设计依据：
    《照片导入方案》—— photo_texture 自研纹理烘焙：UV 展开 → Z-buffer 可见性
    → 多视角投影采样 → 泊松融合，输出 obj+mtl+diffuse；几何来自激光扫描
    （M1~M3），色彩来自照片（S1 位姿 + 00_photo）。
    《路线 R2》—— photo-texture 是核心创新点 1；配合 S1 sfm_solve 与
    控制点 ICP 对齐（创新点 2）构成场景①「照片放入 00_photo → 管线自动运行」。

对外：
    run_photo_texture(task, cfg) —— P1 stage（注册于 M5 之前，消费 M3 网格
    + capture_meta.json + 00_photo 照片，输出 50_tex 带纹理模型）。
"""

from __future__ import annotations

import json
import os

import numpy as np

from h3d.core import get_logger

log = get_logger("photo_texture")


def load_capture_meta(meta_path: str) -> tuple:
    """读取 capture_meta.json -> (cameras, images)；images 含 R/t/cam_id。

    utf-8-sig 读取：兼容文件 BOM（跨工具链写入的 JSON）。

    **这里不做位姿约定转换**（2026-11-03）。det(R) 看似能区分「COLMAP 原生
    ``X_cam = R_co·X + tvec``（前方 +Z）」与「本管线 ``pc = R·(p-C)``（前方
    -Z）」，但 ``R`` 与 ``-R`` 给出**完全相同的 u/v**、只翻转 z 的符号——一个
    手写的合法管线位姿完全可以 det=+1。用 det 猜会误伤正常文件。

    真正的判据是**数据**：把 SfM 稀疏点按两种解释各投一次，看哪种落进画幅
    （见 :func:`normalise_pose_convention`）。没有稀疏点时无从判断，按调用方
    给的约定原样使用。
    """
    with open(meta_path, encoding="utf-8-sig") as fh:
        meta = json.load(fh)
    cameras = {int(k): v for k, v in meta.get("cameras", {}).items()}
    images = [{"name": im["name"],
               "R": np.array(im["R"], dtype=np.float64),
               "t": np.array(im["t"], dtype=np.float64),
               "cam_id": im["cam_id"]} for im in meta.get("images", [])]
    return cameras, images


def normalise_pose_convention(images: list, cameras: dict, probe_points,
                              min_frac: float = 0.05) -> dict:
    """用「稀疏点能不能投进画幅」判定位姿约定，必要时就地转换。

    真机事故（2026-11-03）
    ---------------------
    ``90_meta/relic_001_capture_meta.json`` 写于 2026-09-15「坐标系修复」之前，
    存的是 **COLMAP 原生格式**（``R_co`` + ``tvec``，前方 **+Z**）。而管线按
    「``t`` = 相机中心、前方 −Z」读它，于是把 ``tvec`` 当成相机位置：
    30 台里 **19 台一条纹素都投不上**，剩下 11 台纯属巧合。

    这个错误最阴的地方是**它有产出**：那 11 台偶然投上的相机让
    ``in_frame_fraction`` 报出 86.7%，于是「几何已在画幅内，跳过对齐」这道
    闸门正当地放行了——判据在垃圾数据上触发，产出看着合理。

    判据
    ----
    用 SfM 稀疏点（``points3D.txt``，与位姿同系）当探针：两种解释各投一遍，
    取「落在任一画幅内的点更多」的那个。真机实测：原生解释 95.6%、管线解释
    几乎为 0，区分度极大。

    转换公式（与 :func:`h3d.sfm_solve.parse_images` 一致）::

        C = -R_co^T · tvec ;  R = -R_co

    返回 metrics（``pose_convention`` / ``pose_probe_frac`` / ``pose_converted``），
    没有可判定的数据时返回 ``pose_convention="unknown"`` 且**不动 images**——
    判不出来就不猜（猜错方向比不改更糟：会把本来对的位姿改坏）。
    """
    pts = np.asarray(probe_points, dtype=np.float64) if probe_points is not None \
        else np.zeros((0, 3))
    if len(pts) > 20000:
        pts = pts[np.random.default_rng(0).choice(len(pts), 20000, replace=False)]

    def hit_frac(imgs) -> float:
        """探针点落在**任一**相机画幅内的比例（与累积 in_frame 同口径）。"""
        if len(pts) == 0 or not imgs:
            return 0.0
        hit = np.zeros(len(pts), dtype=bool)
        for im in imgs:
            cam = cameras[int(im["cam_id"])]
            pc = (pts - im["t"]) @ im["R"].T
            z = pc[:, 2]
            with np.errstate(divide="ignore", invalid="ignore"):
                u = cam["fx"] * pc[:, 0] / z + cam["cx"]
                v = cam["fy"] * pc[:, 1] / z + cam["cy"]
            hit |= ((z < -1e-9) & (u >= 0) & (u < cam["w"])
                    & (v >= 0) & (v < cam["h"]))
        return float(hit.mean())

    as_is = hit_frac(images)
    converted = [{"name": im["name"], "cam_id": im["cam_id"],
                  "R": -np.asarray(im["R"], dtype=np.float64),
                  "t": (-np.asarray(im["R"], dtype=np.float64).T
                        @ np.asarray(im["t"], dtype=np.float64))}
                 for im in images]
    flipped = hit_frac(converted)

    metrics = {"pose_probe_as_is": round(as_is, 4),
               "pose_probe_converted": round(flipped, 4),
               "pose_convention": "pipeline", "pose_converted": False}

    if as_is >= min_frac or flipped >= min_frac:
        # 谁投得上用谁；差距不明显时不换（保守：换错 = 把对的改坏）
        if flipped > as_is * 2.0 and flipped >= min_frac:
            images[:] = converted
            metrics.update({"pose_convention": "colmap_raw",
                            "pose_converted": True})
            log.warning(
                "位姿是**原生 COLMAP 格式**（R_co + tvec，前方 +Z），已就地转换"
                "为管线约定（相机中心、前方 −Z）：稀疏点入画 %.1f%% → %.1f%%。"
                "这类文件通常是旧版本或手工写出——重跑 S1 可得到规范格式。",
                as_is * 100, flipped * 100)
    else:
        metrics["pose_convention"] = "unknown"
        log.warning(
            "位姿约定判不出来（两种解释下稀疏点入画都 <%d%%：原样 %.1f%% / "
            "转换后 %.1f%%）——按原样使用。若纹理大面积落空，请核对 "
            "capture_meta 的 R/t 约定或重跑 S1。",
            int(min_frac * 100), as_is * 100, flipped * 100)
    return metrics


def visible_score(verts, tris, images, cameras, sample: int = 6000,
                  seed: int = 0) -> float:
    """对齐候选的判据：有多少比例的**面**能被任一相机正面看到。

    为什么不用 ``in_frame_ratio`` 判断对齐好坏（2026-11-03）
    ------------------------------------------------------
    真机：激光系里根本没有相机，对齐前 `in_frame` 恒为 0；对齐后**任何**把物体
    搬到相机前方的解都报 1.0——它区分不了 0° 和 90°。而控制点那条实际解出了
    约 90° 的旋转错（与自动 ICP 的形状主轴 |cos|=0.001），照样被判通过。

    「能不能被正面看到」才有区分度：真机同一次对齐，自动 ICP 0.98、控制点 0.69。

    口径取 ``max(正向, 反向)``：网格法线可能整体朝内（Poisson 常见），那只是一种
    符号约定，不该被择优惩罚——真正要罚的是**朝向错**，那种情况两个方向都不行。
    """
    pts_v = np.asarray(verts, dtype=np.float64)
    t = np.asarray(tris)
    if len(pts_v) == 0 or len(t) == 0 or not images:
        return 0.0
    if len(t) > sample:
        idx = np.random.default_rng(seed).choice(len(t), sample, replace=False)
        t = t[idx]
    a, b, c = pts_v[t[:, 0]], pts_v[t[:, 1]], pts_v[t[:, 2]]
    fn = np.cross(b - a, c - a)
    ln = np.linalg.norm(fn, axis=1, keepdims=True)
    if not np.all(ln > 0):
        ln = np.maximum(ln, 1e-12)
    fn = fn / ln
    cen = (a + b + c) / 3.0

    best = np.full(len(t), -1.0)
    for im in images:
        C = np.asarray(im["t"], dtype=np.float64)
        vec = C - cen
        d = np.maximum(np.linalg.norm(vec, axis=1, keepdims=True), 1e-12)
        best = np.maximum(best, ((vec / d) * fn).sum(axis=1))
    fwd = float((best > 0.3).mean())
    rev = float((-best > 0.3).mean())
    return max(fwd, rev)


def choose_alignment(candidates, images, cameras):
    """在多个对齐候选里按 :func:`visible_score` 取优。

    ``candidates`` 是 ``[(name, verts, tris, metrics), ...]``。
    返回 ``(name, verts, metrics)``：metrics 是**选中候选**那份，外加
    ``align_candidates``（每个候选的名字与分数）——把落选的分数也留下，
    否则下次争论"为什么没选另一条"就没有依据。

    2026-11-03 之前 M4 的策略是「控制点优先、失败才回退自动 ICP」。判据换成
    择优之后，两条都跑（各几秒），谁把几何摆对了用谁。
    """
    if not candidates:
        raise ValueError("choose_alignment 需要至少一个候选")
    scored = []
    for name, verts, tris, metrics in candidates:
        scored.append((float(visible_score(verts, tris, images, cameras)),
                       name, verts, dict(metrics or {})))
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_name, best_verts, best_metrics = scored[0]
    best_metrics["align_candidates"] = [
        {"name": n, "visible": round(sc, 4)} for sc, n, _v, _m in scored]
    best_metrics["align_chosen"] = best_name
    if len(scored) > 1:
        log.info("对齐择优：%s（%s）—— 候选得分 %s",
                 best_name, "%.3f" % best_score,
                 "，".join("%s=%.3f" % (n, sc) for sc, n, _v, _m in scored))
    return best_name, best_verts, best_metrics


def in_frame_ratio(verts, images, cameras, sample: int = 20000,
                   seed: int = 0) -> float:
    """有多少比例的顶点能投进**至少一台**相机的画幅（对齐验收用）。

    为什么单独一个量（ADR-0009 D3）
    ------------------------------
    ``align_points_to_mesh`` 的 ``accepted`` 闸门看的是「包围盒剩几倍 / 几何离
    点云多远」——量的是**几何之间的相似度**，量不到**几何还在不在相机视野里**。
    真机 2026-11-01 上它就放过了一次：对齐后 coverage 从 21.22% 掉到 1.16%、
    用上的相机从 20 台掉到 3 台，而闸门判「accepted」。

    这个函数把顶点直接投到每台相机，问「落在画幅内吗」。它与
    ``texture_mesh_from_photos`` 里的 ``in_frame_fraction`` 是同一个物理量，
    区别只在这里用**顶点**（对齐前后都在顶点域比较，口径一致），那里用**纹素**。

    相机约定与投影阶段严格一致：``pc = R @ (p - t)``，``z < 0`` 表示点在相机前方。
    两处写反任何一处，判据就整体反号（ADR-0005 踩过同款）。

    用内参的 ``w/h`` 当画幅尺寸，不读实际照片：这里只需**相对比较**（对齐前后
    用同一把尺子），绝对像素尺寸被统一的尺度因子缩放，不影响两者之差。
    """
    pts = np.asarray(verts, dtype=np.float64)
    n = len(pts)
    if n == 0 or not images:
        return 0.0
    if n > sample:
        idx = np.random.default_rng(seed).choice(n, sample, replace=False)
        pts = pts[np.sort(idx)]

    covered = np.zeros(len(pts), dtype=bool)
    for im in images:
        cam = cameras.get(im.get("cam_id"))
        if not cam or not cam.get("w") or not cam.get("h"):
            continue
        R = np.asarray(im["R"], dtype=np.float64)
        t = np.asarray(im["t"], dtype=np.float64)
        pc = (pts - t) @ R.T
        z = pc[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = cam["fx"] * pc[:, 0] / z + cam["cx"]
            v = cam["fy"] * pc[:, 1] / z + cam["cy"]
        covered |= ((z < -1e-9) & (u >= 0) & (u < cam["w"])
                    & (v >= 0) & (v < cam["h"]))
        if covered.all():
            break
    return float(covered.mean())


def alignment_needed(in_frame: float, threshold: float = 0.5) -> bool:
    """还需不需要对齐？只看「几何在不在相机画幅里」。

    比 ``already_aligned``（比的是「几何 vs 稀疏点云」）更本质：它直接问
    「还需不需要对齐」。真机 `10_clean` 对 30 台相机 in_frame = 86.7%，已经
    与位姿同系，再对齐纯属净损失（实测：跑对齐 → UV 光栅化为空 → M4 降级顶点色；
    跳过 → coverage 21.2% → **56.8%**、岛内填充 31.1% → **87.6%**）。

    阈值 0.5：超过一半顶点已经落在画幅内，说明这是同一套坐标系。
    """
    return in_frame < threshold


def alignment_regressed(before: float, after: float,
                        min_keep: float = 0.5) -> bool:
    """对齐后投影覆盖是否**显著变差**（相对判据，无绝对阈值）。

    为什么用相对而不是绝对（ADR-0009 D3）
    ------------------------------------
    真机「不对齐」的基线只有 14.5%、旧版更低——任何绝对阈值在这个数据上都没有
    可用门槛。而「拒绝」永远是安全侧：拒绝 = 保持原坐标系 = 等于不做对齐，
    所以判据只要问「比不做好了多少」。

    ``before <= 0`` 时不拦：基线为 0 说明几何本来就不在任何画幅里，此时没法比较，
    而对齐可能是唯一的出路（拦掉等于永远禁止对齐）。
    """
    if before <= 0.0:
        return False
    return after < before * min_keep


def apply_align_status(metrics: dict | None, applied: bool | None) -> dict:
    """把对齐结论收敛成**三态** ``status``，并让 ``accepted`` 与它自洽。

    为什么需要（2026-11-01）
    ----------------------
    M4 有四条对齐路径，各自往外吐不同形状的 metrics：``geometry_is_sfm`` 和
    ``already_aligned`` 给 ``status="skipped"``；控制点两阶段只给
    ``method/n_control_points/scale_s/rmse_after``；自动 ICP 只给
    ``accepted/reject_reason``。报表按 ``status`` 取结论，于是**两条真跑过的路
    都印成空值**——读者无法区分「没对齐」「对齐了」「对齐了但被拒」。

    这与 ADR-0009 D3 的投影验收直接冲突：验收写进 ``accepted`` 和
    ``reject_reason``，报表却看不到。

    三态（与项目其它终态同一套词汇）：``applied`` / ``rejected`` / ``skipped``。

    参数
    ----
    metrics: 对齐函数返回的 metrics（不就地修改，返回副本）。
    applied: True=已应用；False=判无效、保持/回退原坐标系；
             None=**没有尝试对齐**（already_aligned / geometry_from_sfm），
             此时保留原有的 ``skipped`` 语义，不写 ``accepted``——没尝试过就
             没有「接受/拒绝」这回事。
    """
    out = dict(metrics or {})
    if applied is None:
        out.setdefault("status", "skipped")
        return out
    out["status"] = "applied" if applied else "rejected"
    out["accepted"] = bool(applied)
    return out


def _load_photos(images, photo_dir: str, budget: int = 2_000_000_000):
    """载入照片为 uint8 常驻（超预算等比缩小），返回 (photos, factor, scales)。

    实机教训（2026-10-18）：原实现把**原图**转成 float64 常驻——30 张
    4496×3000 需 30 × 310 MB ≈ **9.7 GB**，M4 必爆内存。现按 RGB 像素预算
    等比缩小（只缩内存表示，不改变投影几何——比例在 ``scales`` 里被吸收）。
    """
    from PIL import Image as _Img

    sizes: dict[str, tuple[int, int]] = {}
    for im in images:
        p = os.path.join(photo_dir, im["name"])
        if not os.path.exists(p):
            continue
        try:
            with _Img.open(p) as fh:
                sizes[im["name"]] = fh.size
        except Exception:  # noqa: BLE001 - 坏图跳过（按"可用照片"统计）
            continue
    total_bytes = sum(w * h * 3 for w, h in sizes.values())
    factor = 1.0
    if total_bytes > budget:
        factor = (budget / float(total_bytes)) ** 0.5
        log.warning("照片总量 %.1f GB 超内存预算，统一缩至 %.0f%% 载入",
                    total_bytes / 1e9, factor * 100)

    photos: dict[str, np.ndarray] = {}
    for name, (w0, h0) in sizes.items():
        try:
            with _Img.open(os.path.join(photo_dir, name)) as fh:
                rgb = fh.convert("RGB")
                if factor < 0.999:
                    rgb = rgb.resize((max(1, int(w0 * factor)),
                                      max(1, int(h0 * factor))), _Img.LANCZOS)
                photos[name] = np.asarray(rgb, dtype=np.uint8)
        except Exception as exc:  # noqa: BLE001
            log.warning("照片读取失败（跳过）：%s（%s）", name, exc)
    if len(photos) < 2:
        raise ValueError("可用照片不足（需 >=2 张）")
    return photos, factor, sizes


def _bilinear(img: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """双线性采样（u 右 / v 下，像素中心为 +0.5）。

    原实现用 ``img[int(v), int(u)]`` 最近邻取色：纹理放大 2 倍以上就直接
    出锯齿色块，且三角形之间没有过渡。改为双线性后，贴图在同等分辨率下
    肉眼可见地连续。
    """
    h, w = img.shape[:2]
    u = np.asarray(u, dtype=np.float64) - 0.5
    v = np.asarray(v, dtype=np.float64) - 0.5
    u0 = np.floor(u)
    v0 = np.floor(v)
    fu = (u - u0).astype(np.float32)[:, None]
    fv = (v - v0).astype(np.float32)[:, None]
    i0 = np.clip(u0.astype(np.int64), 0, w - 1)
    j0 = np.clip(v0.astype(np.int64), 0, h - 1)
    i1 = np.clip(i0 + 1, 0, w - 1)
    j1 = np.clip(j0 + 1, 0, h - 1)
    return (img[j0, i0] * ((1 - fu) * (1 - fv))
            + img[j0, i1] * (fu * (1 - fv))
            + img[j1, i0] * ((1 - fu) * fv)
            + img[j1, i1] * (fu * fv))


def _rasterize_uv(verts, normals, uv, faces, size):
    """UV 空间三角形光栅化：返回逐纹素的 (flat_idx, 3D 位置, 法线)。

    这是「3D 预览一片黑/麻点」的根因修复。原实现对每个三角形只做
    ``samples_per_tri``（4 个）重心采样点、把它们各自写成**单个像素**——
    2048² 图集下每面平均覆盖约 9 个纹素，只点亮 4 个，于是 UV 岛内部
    全是黑点；交付模型看起来"没有颜色"（实测着色率 4.6%，图集还能看到
    密密麻麻的黑色针孔）。此处按纹素中心做真实的重心坐标内插，岛内
    每个纹素都拿到自己的 3D 位置/法线，后续逐纹素选相机。
    """
    tri = np.asarray(faces, dtype=np.int64)
    f = len(tri)
    empty = (np.zeros(0, np.int64), np.zeros((0, 3)), np.zeros((0, 3)), 0.0)
    if f == 0:
        return empty
    uv_t = np.asarray(uv, dtype=np.float64)[tri]          # (F,3,2)，v 向上
    px = uv_t[:, :, 0] * size                              # 像素系 x
    py = (1.0 - uv_t[:, :, 1]) * size                      # 像素系 y（翻转到图像系）
    x0, x1, x2 = px[:, 0], px[:, 1], px[:, 2]
    y0, y1, y2 = py[:, 0], py[:, 1], py[:, 2]
    lo_x = np.clip(np.floor(np.minimum(np.minimum(x0, x1), x2)).astype(np.int64),
                   0, size - 1)
    hi_x = np.clip(np.ceil(np.maximum(np.maximum(x0, x1), x2)).astype(np.int64),
                   0, size - 1)
    lo_y = np.clip(np.floor(np.minimum(np.minimum(y0, y1), y2)).astype(np.int64),
                   0, size - 1)
    hi_y = np.clip(np.ceil(np.maximum(np.maximum(y0, y1), y2)).astype(np.int64),
                   0, size - 1)

    idx_parts: list[np.ndarray] = []
    tri_parts: list[np.ndarray] = []
    lam_parts: list[np.ndarray] = []
    for i in range(f):
        ax, ay, bx, by, cx, cy = x0[i], y0[i], x1[i], y1[i], x2[i], y2[i]
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        ix0, ix1, iy0, iy1 = lo_x[i], hi_x[i], lo_y[i], hi_y[i]
        if abs(d) < 1e-12 or ix1 < ix0 or iy1 < iy0:
            # 退化 / 亚纹素三角形：至少点亮质心一个纹素，否则会出现针孔
            gx = int(np.clip((ax + bx + cx) / 3.0, 0, size - 1))
            gy = int(np.clip((ay + by + cy) / 3.0, 0, size - 1))
            idx_parts.append(np.array([gy * size + gx], dtype=np.int64))
            tri_parts.append(np.array([i], dtype=np.int64))
            lam_parts.append(np.array([[1 / 3, 1 / 3, 1 / 3]], dtype=np.float32))
            continue
        xs = np.arange(ix0, ix1 + 1)
        ys = np.arange(iy0, iy1 + 1)
        X = xs[None, :].astype(np.float64) + 0.5
        Y = ys[:, None].astype(np.float64) + 0.5
        w0 = ((by - cy) * (X - cx) + (cx - bx) * (Y - cy)) / d
        w1 = ((cy - ay) * (X - cx) + (ax - cx) * (Y - cy)) / d
        w2 = 1.0 - w0 - w1
        m = (w0 >= -1e-4) & (w1 >= -1e-4) & (w2 >= -1e-4)
        if not m.any():
            continue
        yy, xx = np.nonzero(m)
        idx_parts.append(ys[yy] * size + xs[xx])
        tri_parts.append(np.full(yy.size, i, dtype=np.int64))
        lam_parts.append(np.stack([w0[m], w1[m], w2[m]], axis=1).astype(np.float32))
    if not idx_parts:
        return empty
    flat = np.concatenate(idx_parts)
    tri_idx = np.concatenate(tri_parts)
    lam = np.concatenate(lam_parts)
    # 后写覆盖：同一纹素可能被相邻岛/退化三角形重复覆盖，保留首个即可
    uniq, first = np.unique(flat, return_index=True)
    # **利用率必须用去重后的纹素数**（ADR-0009）：`flat` 含重复覆盖，重复面
    # 或共边三角形会让它偏大——铺满 UV 的正方形会算出 1.0156、重复面算出 2.03
    # （比值 >1 显然是算错了）。这个数是 island_fill_ratio 的分母，虚高会让人
    # 误判为「岛内没填满」，把排查引去法线/遮挡。
    utilization = float(len(uniq)) / float(size * size)
    tri_u = tri_idx[first]
    w = lam[first].astype(np.float64)
    vv = np.asarray(verts, dtype=np.float64)[tri[tri_u]]        # (n,3,3)
    nn = np.asarray(normals, dtype=np.float64)[tri[tri_u]]
    pos = np.einsum("nk,nkc->nc", w, vv)
    nrm = np.einsum("nk,nkc->nc", w, nn)
    return uniq, pos, nrm, utilization


def _shift(a: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """带零边界的平移（避免 ``np.roll`` 跨边界回卷串色）。"""
    out = np.zeros_like(a)
    h, w = a.shape[:2]
    ys_src = slice(max(0, -dy), h - max(0, dy))
    ys_dst = slice(max(0, dy), h - max(0, -dy))
    xs_src = slice(max(0, -dx), w - max(0, dx))
    xs_dst = slice(max(0, dx), w - max(0, -dx))
    out[ys_dst, xs_dst] = a[ys_src, xs_src]
    return out


def balance_photo_exposure(photos: dict) -> dict:
    """逐相机灰度归一化：把每张照片的亮度均值/标准差对齐到全局中位数。

    为什么（2026-11-01）：真机 `multiview_color_std = 32.67`（0~255 尺度），
    同一纹素在不同相机下的颜色差得离谱——**不同照片的曝光/白平衡不一致**。
    融合后相邻 UV 岛用了不同照片组合，色调就跳变，X3 把跳变当异常。
    实测：把纹理按岛内强平滑（抹掉色调台阶）后，X3 异常 **0.5179% → 0.1086%
    （降 79%）**，处数 9 → 4 —— 色调跳变确认是主因。

    这只解决**曝光**那一半（灰度增益/偏置），解决不了白平衡（通道间比例）。
    白平衡要色卡，代码补不了。

    **默认关闭**：会改变绝对色彩，而 X2 的 ΔE 测量需要真实颜色。
    只在「纹理连续性优先（展示/异常筛查）」时开。
    """
    out = {}
    stats = []
    for name, img in photos.items():
        if img is None:
            out[name] = img
            continue
        gray = img.astype(np.float32).mean(axis=2)
        stats.append((gray.mean(), gray.std()))
    if not stats:
        return dict(photos)
    tm = float(np.median([m for m, _ in stats]))
    ts = float(np.median([s for _, s in stats])) or 1.0

    for (name, img), (m, sd) in zip(photos.items(), stats):
        if img is None:
            out[name] = img
            continue
        f = ts / (sd or ts)
        adj = (img.astype(np.float32) - m) * f + tm
        out[name] = np.clip(adj, 0, 255).astype(np.uint8)
    return out


def _bleed(image: np.ndarray, painted: np.ndarray, iterations: int = 3,
           limit: np.ndarray | None = None):
    """迭代四邻域均值扩散：岛外缘外扩（gutter）+ 岛内空洞填充。

    传 ``limit`` 时**只在给定掩膜内扩散**。这是「填岛内洞」与「补岛外 gutter」
    的分界：岛外空白不是表面，填了等于伪造纹理；岛内空洞是没拍到的表面，可以填。

    为什么需要（2026-11-01）：真机 `island_fill_ratio` 87.64%，岛内还有 12.4%
    纹素没着色（黑色空洞），而 X3 的 9 处图斑全落在这些空洞边缘——「异常」里
    一大半是洞的边界。原来的 `iterations=pad`（3）只够 gutter，够不到岛内部；
    不限范围地加迭代又会把岛外空白一起填满。
    """
    img = image.astype(np.float32)
    m = painted.copy()
    for _ in range(max(0, iterations)):
        acc = np.zeros_like(img)
        cnt = np.zeros(m.shape, dtype=np.float32)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            sm = _shift(m, dy, dx)
            si = _shift(img, dy, dx)
            acc += si * sm[:, :, None]
            cnt += sm
        new = (~m) & (cnt > 0)
        if limit is not None:
            new &= limit
        if not new.any():
            break
        img[new] = acc[new] / cnt[new][:, None]
        m |= new
    return img, m


def project_photos_to_texture(mesh, uv, faces, images, cameras, photo_dir,
                              size=1024, top_k=3, samples_per_tri=4,
                              pad=3, occlusion=True, chunk=60_000,
                              want_mask=False, hole_fill_px: int = 0,
                              want_camera_map: bool = False,
                              balance_exposure: bool = False):
    """照片投影纹理：UV 光栅化 → 逐纹素多视角加权采样 → 纹理图。

    与验证版一致的核心判据（正面 cos>0.3 + cos²/距离加权 + top-k 平均抗接缝），
    但**采样单元从「每面 4 个点」改为「每个纹素」**：

    1. :func:`_rasterize_uv` 把每个三角形光栅化到纹素，插值出逐纹素 3D 位置
       与法线（岛内无空洞，纹素数 ≈ 图集面积 × 利用率）；
    2. 每台相机对全部纹素做投影，得到 (u,v,深度,视角余弦)；
    3. **Z-buffer 遮挡**：按分块最小深度剔除被前表面挡住的纹素（凹陷/背面
       不再从对侧"透色"）；
    4. 逐纹素取 top-k 相机做加权平均（双线性采样照片），并统计多视角一致性；
    5. 岛外缘做 ``pad`` 次色彩外扩（gutter），避免双线性在边界取到黑底。

    返回 (texture_img, metrics)。``samples_per_tri`` 仅为接口兼容保留——旧实现
    的"每面 N 点"是本函数曾经的致命缺陷，新的采样密度由图集分辨率决定。
    """
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    if mesh.has_vertex_normals():
        normals = np.asarray(mesh.vertex_normals, dtype=np.float64)
    else:  # 兜底：缺法线时现算法线（否则逐纹素判据全失效）
        mesh.compute_vertex_normals()
        normals = np.asarray(mesh.vertex_normals, dtype=np.float64)
    faces_a = np.asarray(faces, dtype=np.int64)
    uv_a = np.asarray(uv, dtype=np.float64)

    cam_centers = np.array([im["t"] for im in images], dtype=np.float64)
    cam_rots = np.stack([np.asarray(im["R"], dtype=np.float64) for im in images])
    K4 = np.array([[cameras[im["cam_id"]]["fx"], cameras[im["cam_id"]]["fy"],
                    cameras[im["cam_id"]]["cx"], cameras[im["cam_id"]]["cy"]]
                   for im in images], dtype=np.float64)
    names = [im["name"] for im in images]

    photos, factor, sizes = _load_photos(images, photo_dir)
    if balance_exposure:
        photos = balance_photo_exposure(photos)
        log.info("照片曝光已逐相机归一化：%d 张（解决融合后岛间亮度跳变；"
                 "绝对色彩会变，做色差测量时请关掉）", len(photos))

    # 逐图「实际像素尺寸 / 内参坐标系尺寸」比例（==1 时无副作用）
    scales = []
    for im in images:
        img = photos.get(im["name"])
        cam = cameras[im["cam_id"]]
        if img is None or not cam.get("w") or not cam.get("h"):
            scales.append((1.0, 1.0))
            continue
        scales.append((img.shape[1] / float(cam["w"]),
                       img.shape[0] / float(cam["h"])))
    scales = np.array(scales, dtype=np.float64)
    ratios = sorted({round(float(s), 4) for s in scales[:, 0]})
    if ratios and (len(ratios) > 1 or abs(ratios[0] - 1.0) > 1e-6):
        log.info("照片投影像素换算：内参系 %dx%d -> 实际图像，比例 %s",
                 cameras[images[0]["cam_id"]].get("w", 0),
                 cameras[images[0]["cam_id"]].get("h", 0), ratios)

    flat, pos, nrm, utilization = _rasterize_uv(verts, normals, uv_a, faces_a, size)
    n = len(flat)
    if n == 0:
        raise ValueError("UV 光栅化为空（三角形退化或图集尺寸过小）")
    nrm_len = np.linalg.norm(nrm, axis=1, keepdims=True)
    nrm = nrm / np.maximum(nrm_len, 1e-12)
    # 法线朝向自检（M4 独立兜底）：M1 若未按相机校正法线符号，网格会是翻面的，
    # 此时「正面 cos>0.3」会选到**背面**相机 → 采样落到器物外的背景上（实机
    # 表现为贴图大面积泛白、"没有颜色"）。
    # 判据只看「有多少纹素能被至少一台相机正面看到」：不能用「朝向相机的相机
    # 比例」，环形布光下正/反面都约等于一半，那个判据没有区分度（踩过）。
    # 判决规则是「正/反两种符号各算一遍，取能看到更多纹素的那个」——旧实现只在
    # 正向比例 <25% 时才翻转，遇上"部分翻面"的碎块网格（本批 25 块）会两边不讨好：
    # 实测正向 61.6% / 反向 79.9%，旧规则判"不用翻"，等于放弃了 18 个百分点。
    sub = np.linspace(0, n - 1, min(20000, n)).astype(np.int64)
    pos_s, nrm_s = pos[sub], nrm[sub]
    best_fwd = np.full(len(sub), -1.0)
    # 「能被看到」要同时满足两件事：①视线与法线夹角 < 73°（cos>0.3）；
    # ②这个点**真的投影在该相机的画幅内**。旧实现只判 ①，于是 1.1.12 真机
    # 报出「92% 纹素可被正面看到」而岛内只有 57.5% 着色，把排查方向引向法线，
    # 实际是几何被搬错后大面积投到画幅外。两个数分开落，谁低就查谁。
    fwd_in = np.zeros(len(sub), dtype=bool)
    rev_in = np.zeros(len(sub), dtype=bool)
    for k in range(len(images)):
        v = cam_centers[k] - pos_s
        d = np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
        cos_k = ((v / d) * nrm_s).sum(axis=1)
        best_fwd = np.maximum(best_fwd, cos_k)
        img = photos.get(names[k])
        if img is None:
            continue
        pc_k = (pos_s - cam_centers[k]) @ cam_rots[k].T
        z_k = pc_k[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u_k = (K4[k, 0] * pc_k[:, 0] / z_k + K4[k, 2]) * scales[k, 0]
            v_k = (K4[k, 1] * pc_k[:, 1] / z_k + K4[k, 3]) * scales[k, 1]
        h_k, w_k = img.shape[:2]
        okp = (z_k < -1e-9) & (u_k >= 0) & (u_k < w_k) & (v_k >= 0) & (v_k < h_k)
        if not okp.any():
            continue
        fwd_in |= okp & (cos_k > 0.3)
        rev_in |= okp & (cos_k < -0.3)
    frac_fwd = float((best_fwd > 0.3).mean())
    frac_rev = float((-best_fwd > 0.3).mean())
    normal_flipped = bool(frac_rev > frac_fwd)
    if normal_flipped:
        nrm = -nrm
        log.warning("纹素法线整体符号与多数相机相悖（正向可正面看到 %.0f%%、"
                    "取反 %.0f%%）——按 -n 处理；建议检查 M1 法线朝向"
                    "（Poisson 生成的网格常是翻面的）",
                    frac_fwd * 100, frac_rev * 100)
    normal_visible_fraction = round(max(frac_fwd, frac_rev), 4)
    in_frame_fraction = round(float(
        (rev_in if normal_flipped else fwd_in).mean()), 4)
    # 可见性偏低有两种病因，**必须分开报**（2026-11-03）：
    #   (a) 法线局部翻面 —— 相邻面法线反向比例高；
    #   (b) 几何对齐错了 —— 法线一致，但整个网格被搬到相机看不到的地方/尺度。
    # 旧实现在这里直接断言「网格法线局部方向不一致」，真机实测 0.74 而相邻面
    # 反向只有 0.08%：**误诊**。把 (b) 说成 (a) 会把排查引向法线，而真正该看的是
    # 对齐——那条路我按这个告警查了一整轮才发现是几何搬歪了。
    nrm_consistency = None
    if normal_visible_fraction < 0.90:
        try:
            from h3d.geo_align import normal_consistency
            nrm_consistency = normal_consistency(mesh)
        except Exception as exc:  # noqa: BLE001 - 诊断失败不影响主流程
            log.debug("法线一致性诊断失败：%s", exc)
        flip = (nrm_consistency or {}).get("flip_fraction")
        if flip is None:
            log.warning("仅 %.0f%% 纹素能被任一台相机正面看到（不足 90%%），"
                        "且法线一致性无法判定——请检查对齐结果与采集覆盖",
                        normal_visible_fraction * 100)
        elif flip >= 0.05:
            log.warning("仅 %.0f%% 纹素能被任一台相机正面看到（不足 90%%），"
                        "且相邻面法线反向 %.1f%%——**法线局部翻面**，"
                        "请检查 M1/M3 的法线朝向；剩余部分将按 -n 且要求深度"
                        "可见后补色", normal_visible_fraction * 100, flip * 100)
        else:
            log.warning("仅 %.0f%% 纹素能被任一台相机正面看到（不足 90%%），"
                        "但相邻面法线反向只有 %.2f%%——**法线是好的**，问题在"
                        "几何与位姿的对齐（位置/尺度），请核对 M4 的 icp_align "
                        "结果；把成品网格投到照片上看一眼最快",
                        normal_visible_fraction * 100, flip * 100)
    elif in_frame_fraction < 0.85:
        log.warning("法线朝向正常（%.0f%% 可正面看到）但只有 %.0f%% 纹素"
                    "落在任一图像画幅内——问题在**几何与位姿的尺度/平移对齐**，"
                    "不在法线；请核对 M4 的 icp_align 结果（accepted）与 "
                    "scale.reference_diag", normal_visible_fraction * 100,
                    in_frame_fraction * 100)
    log.info("UV 光栅化：%d 面 -> %d 纹素（图集利用率 %.1f%%）",
             len(faces_a), n, utilization * 100)

    # ---- 遮挡判定用的分块最小深度（每相机一份；只保留缓冲，不留投影结果）----
    tolv = 0.02 * float(np.linalg.norm(pos.max(axis=0) - pos.min(axis=0)))
    zbufs: list[np.ndarray | None] = []
    for k in range(len(images)):
        img = photos.get(names[k])
        if img is None:
            zbufs.append(None)
            continue
        pc = (pos - cam_centers[k]) @ cam_rots[k].T
        z = pc[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = (K4[k, 0] * pc[:, 0] / z + K4[k, 2]) * scales[k, 0]
            v = (K4[k, 1] * pc[:, 1] / z + K4[k, 3]) * scales[k, 1]
        h, wd = img.shape[:2]
        zbufs.append(_build_zbuf(u, v, -z, wd, h) if occlusion else None)

    image = np.zeros((size * size, 3), dtype=np.float32)
    painted = np.zeros(size * size, dtype=bool)
    # 逐纹素「胜出相机」下标（-1 = 未着色）。诊断用：判断 X3 的图斑是不是
    # 「同一个岛内、不同相机拼出来的色调台阶」——台阶跟着相机分界走，不跟岛
    # 边界走（ADR-0016 的测量排除了岛边界假设）。只读诊断，不参与着色。
    cam_map = np.zeros(size * size, dtype=np.int16) - 1
    # 去重口径：back_filled / occluded 是「相机×纹素」的事件计数（一台相机
    # 判一次就 +1），30 台相机能把 7 万纹素刷成 200 万。报表若直接拿它当
    # 纹素数，会读出「77.8% 靠反面填充」这种与事实相反的结论。这两个掩膜
    # 给出去重后的真实纹素数。
    back_mask = np.zeros(size * size, dtype=bool)
    occl_mask = np.zeros(size * size, dtype=bool)
    usage = np.zeros(len(images), dtype=np.int64)
    # 每台相机"看到多少纹素"。全 0 = 它没投影到模型上（位姿无效 / 没拍到）。
    # 2026-11-02 真机：30 台里 19 台的 R/t 在任何一张照片上都投不出模型，
    # 权重恒为 0；但系统只报 projected_cameras=11，看不出"19 台白拍了"。
    seen_per_cam = np.zeros(len(images), dtype=np.int64)
    # 每个纹素**有几个相机给出了非零权重**（M5「纹理重叠度」的数据源，
    # 设计书 §17.3）。与 ``occluded`` 那类「相机×纹素」事件计数不同，
    # 这是**按纹素去重**的：一个纹素被 5 台相机看到就记 5，被 1 台记 1。
    #
    # 为什么非要这个数：``multiview_color_std`` 只说明"融合进去的几台相机
    # 意见差多少"，读不出"到底有没有第二台相机参与"。整片纹素全靠单相机
    # 硬投影时，色差恒等于 0（只有一台，没有分歧）——那是最该报警的情况，
    # 却也是这个指标最漂亮的情况。两者必须配对读。
    contrib = np.zeros(size * size, dtype=np.int16)
    cons_sum = 0.0
    cons_n = 0
    occluded = 0
    back_filled = 0

    for start in range(0, n, chunk):
        stop = min(start + chunk, n)
        m = stop - start
        p_c = pos[start:stop]
        n_c = nrm[start:stop]
        W = np.zeros((m, len(images)), dtype=np.float32)
        Cc = np.zeros((m, len(images), 3), dtype=np.float32)
        for k in range(len(images)):
            img = photos.get(names[k])
            if img is None:
                continue
            pc = (p_c - cam_centers[k]) @ cam_rots[k].T
            z = pc[:, 2]
            with np.errstate(divide="ignore", invalid="ignore"):
                u = (K4[k, 0] * pc[:, 0] / z + K4[k, 2]) * scales[k, 0]
                v = (K4[k, 1] * pc[:, 1] / z + K4[k, 3]) * scales[k, 1]
            h, wd = img.shape[:2]
            ok = (z < -1e-9) & (u >= 0) & (u < wd) & (v >= 0) & (v < h)
            seen_per_cam[k] += int(ok.sum())
            if not ok.any():
                continue
            # 视线方向必须是「点 -> 相机」（= C - X）。写成 (X - C) 会整体反号，
            # cos>0.3 的判据就变成"只在背面成立"——采样全落到器物背后的背景上，
            # 实机表现正是"有几何、无颜色"且不报错。法线自检用的是 C-X，
            # 两处必须一致（此不一致曾同时骗过自检与渲染验证）。
            view = cam_centers[k] - p_c
            dist = np.linalg.norm(view, axis=1)
            cos = np.clip((view * n_c).sum(axis=1) / np.maximum(dist, 1e-12), -1, 1)

            # 深度测试先算：下面「按反法线补色」必须拿到它才能决定能否采信。
            buf = zbufs[k]
            if buf is not None:
                cell = _zbuf_cell(wd, h)
                ncol = _zbuf_ncol(wd, h, cell)
                key = (np.clip(v, 0, h - 1) // cell).astype(np.int64) * ncol \
                    + (np.clip(u, 0, wd - 1) // cell).astype(np.int64)
                vis = -z <= buf[key] + tolv
            else:
                vis = ok  # 未启用遮挡：全部视为可见

            # 逐纹素双朝向判别（2026-10-18 实机关键修正）：
            # 稠密重建网格往往**碎成多块**（本批 25 块，最大块仅占 27.8%），
            # 各块法线符号互不一致 —— 全局 ±1 翻转无论选哪个都救不全
            # （实测正号 61.6%、取反 79.9% 的纹素能找到正面相机）。
            # 因此在「正法线」不成立时允许按 -n 再问一次，但**必须真实可见**，
            # 否则会穿透器物取到对侧的颜色（比黑斑更难排查）。
            front = cos > 0.3
            back = (~front) & (-cos > 0.3) & vis
            cos_eff = np.where(front, cos, np.where(back, -cos, 0.0))
            back_filled += int((back & ok).sum())
            if (back & ok).any():
                back_mask[flat[start:stop][back & ok]] = True

            ok = ok & (cos_eff > 0)
            if not ok.any():
                continue
            pen = 1.0
            if buf is not None:
                occluded += int((ok & ~vis).sum())
                if (ok & ~vis).any():
                    occl_mask[flat[start:stop][ok & ~vis]] = True
                # 遮挡只做**降权**，不做硬剔除：硬剔除实测把岛内着色率从
                # 79.7% 打到 47.8%（Poisson 闭合壳的背面/凹陷纹素完全取不到
                # 颜色 → 贴图重新出现黑斑）。降权等价于"优先可见视角、无可见
                # 视角时退回最佳斜视视角"，既不漏色也不会让对侧透色占上风。
                pen = np.where(vis, 1.0, 0.05).astype(np.float32)
            w = (np.where(ok, cos_eff ** 2 / (1.0 + dist), 0.0) * pen).astype(np.float32)
            W[:, k] = w
            Cc[:, k] = _bilinear(img, u, v)
        tot = W.sum(axis=1)
        if not (tot > 0).any():
            continue
        kk = max(1, min(int(top_k), W.shape[1]))
        order = np.argsort(-W, axis=1)[:, :kk]
        rows = np.arange(m)[:, None]
        ws = np.take_along_axis(W, order, axis=1)
        ws[ws <= 0] = 0.0
        cs = Cc[rows, order]
        wsum = ws.sum(axis=1)
        valid = wsum > 1e-9
        if not valid.any():
            continue
        cam_map[flat[start:stop][valid]] = order[valid, 0].astype(np.int16)
        col = (cs * ws[:, :, None]).sum(axis=1) / np.maximum(wsum, 1e-9)[:, None]
        # 多视角一致性：同一纹素在 top-k 相机下的颜色离散度（越小越好）
        dev = np.sqrt(((cs - col[:, None, :]) ** 2).mean(axis=2))
        cons_sum += float(dev[valid].mean())
        cons_n += 1
        np.add.at(usage, order[:, 0][valid], 1)
        # 按纹素去重：这一批里每个着色纹素有**几台**相机给了非零权重
        contrib[flat[start:stop][valid]] = (
            (W[valid] > 0).sum(axis=1).astype(np.int16))
        idx = flat[start:stop]
        image[idx[valid]] = col[valid]
        painted[idx[valid]] = True

    tmap = image.reshape(size, size, 3)
    pre_mask = painted.reshape(size, size).copy()      # 真实着色区（未外扩）
    pmap = pre_mask.copy()
    tmap, pmap = _bleed(tmap, pmap, iterations=pad)

    # ---- 岛内空洞填充（2026-11-01）------------------------------------------
    # 岛外空白不是表面，填了等于伪造纹理；岛内没拍到的纹素是**真表面**，留黑
    # 会让 X3 把「洞的边界」当成异常（真机 9 处图斑全在空洞边缘）。
    # 只填 `hole_fill_px` 半径内的小洞：大洞填出来是猜测，宁可留黑并报数。
    island_flat = np.zeros(size * size, dtype=bool)
    island_flat[flat] = True
    island_2d = island_flat.reshape(size, size)
    hole_px = int(hole_fill_px or 0)
    hole_before = int((island_2d & ~pre_mask).sum())
    if hole_px > 0 and hole_before:
        tmap, pmap = _bleed(tmap, pmap, iterations=hole_px, limit=island_2d)
    hole_left = int((island_2d & ~pmap).sum())

    painted_ratio = float(pre_mask.mean())
    fill_ratio = (painted_ratio / utilization) if utilization > 0 else 0.0
    luma = float(tmap[pre_mask].mean()) if pre_mask.any() else 0.0
    # 纹理重叠度（设计书 §17.3）：只在**真着色**纹素上统计，不外扩/不填洞——
    # 填出来的纹素没有相机看过，算进去会把重叠度稀释成好看的假数。
    _c = contrib[painted].astype(np.float64)
    overlap_mean = round(float(_c.mean()), 2) if _c.size else None
    overlap_multi = round(float((_c >= 2).mean()), 4) if _c.size else None
    if painted_ratio < 0.6 * utilization:
        log.warning("UV 岛内着色率仅 %.1f%%（图集利用率 %.1f%%）——"
                    "存在大面积无可见相机的纹素，请检查位姿/遮挡",
                    fill_ratio * 100, utilization * 100)
    metrics = {
        "projected_cameras": int((usage > 0).sum()),
        # 一台都没投影到的相机数。>0 说明这些条目的位姿与照片不对应——
        # 权重虽然为 0（不污染颜色），但等于这部分视角白拍了，覆盖会变差。
        "cameras_blind": int((seen_per_cam < 50).sum()),
        "cameras_total": int(len(images)),
        # coverage_ratio 保留旧键名，语义收敛为「图集有效像素占比」
        "coverage_ratio": round(painted_ratio, 4),
        "atlas_utilization": round(float(utilization), 4),
        "island_fill_ratio": round(float(min(fill_ratio, 1.0)), 4),
        # 岛内空洞：`_before` 是填充前的黑纹素数，`_left` 是填完还剩多少（大洞
        # 不填）。两个数一起看才知道「洞被填了多少、还剩多少是猜不得的」。
        "hole_texels_before": hole_before,
        "hole_texels_left": hole_left,
        "hole_fill_px": hole_px,
        "texels": int(n),
        "texture_size": size,
        "triangles": len(faces_a),
        "pixel_scale": ratios or [1.0],
        "photo_load_factor": round(float(factor), 4),
        "photos_used": int(len(photos)),
        "top_k": int(top_k),
        "occlusion": bool(occlusion),
        "normal_flipped": normal_flipped,
        "normal_visible_fraction": normal_visible_fraction,
        # 相邻面法线反向比例。与上一行配对读：可见性低时，它高=法线坏，
        # 它低=对齐坏。None 表示没量（可见性正常时不算，省时间）。
        "normal_flip_fraction": (nrm_consistency or {}).get("flip_fraction"),
        "in_frame_fraction": in_frame_fraction,
        "back_facing_filled": int(back_filled),
        "occlusion_rejected": int(occluded),
        "back_facing_texels": int(back_mask.sum()),
        "occluded_texels": int(occl_mask.sum()),
        "multiview_color_std": round(cons_sum / cons_n, 2) if cons_n else None,
        # 纹理重叠度：平均每个着色纹素有**几台**相机参与，以及有 ≥2 台的纹素占比。
        # 与上一行配对读：``multiview_color_std`` 小 + 重叠度高 = 融合得稳；
        # ``multiview_color_std`` 小 + 重叠度≈1 = **只有一台相机在看**（没有分歧
        # 也就没有色差），这是单视角硬投影，接缝与漏色都无从缓解。
        "texture_overlap": overlap_mean,
        "texture_multi_view_ratio": overlap_multi,
        "texture_mean_luma": round(float(luma), 2),
        "camera_hist": {names[i]: int(usage[i]) for i in np.argsort(-usage)[:5]
                        if usage[i] > 0},
    }
    return_mask = pre_mask
    img = np.clip(tmap, 0, 255).astype(np.uint8)
    metrics["camera_id_used"] = int(len(np.unique(cam_map[cam_map >= 0])))
    if want_camera_map:
        return img, metrics, return_mask, cam_map.reshape(size, size)
    if want_mask:
        # 着色掩膜：M5 用它算精确"图集填充率"，不再靠亮度阈值猜
        # （暗色器物如青铜/漆木整片亮度 <20，用亮度当掩膜会把好纹理判成没贴图）
        return img, metrics, return_mask
    return img, metrics


def _zbuf_cell(w: int, h: int) -> int:
    """遮挡缓冲的格子边长（长边约 160 格；点数远多于格子，最小深度可靠）。"""
    return max(1, int(round(max(w, h) / 160.0)))


def _zbuf_ncol(w: int, h: int, cell: int) -> int:
    return (w + cell - 1) // cell


def _build_zbuf(u, v, depth, w, h):
    """由投影后的纹素构建分块最小深度缓冲。

    ``depth`` 必须是**正值**（= -pc.z，0 在相机中心、越大越远）。z 为负时
    "最小值 = 最近"这一条不成立（越远越负），会把最远的点当成最近，
    于是 ``depth <= buf + tol`` 几乎恒为假 —— 遮挡判据整体失效。
    """
    cell = _zbuf_cell(w, h)
    ncol = _zbuf_ncol(w, h, cell)
    nrow = (h + cell - 1) // cell
    buf = np.full(ncol * nrow, np.inf, dtype=np.float32)
    ok = (depth > 1e-9) & (u >= 0) & (u < w) & (v >= 0) & (v < h)
    if not ok.any():
        return buf
    key = (np.clip(v[ok], 0, h - 1) // cell).astype(np.int64) * ncol \
        + (np.clip(u[ok], 0, w - 1) // cell).astype(np.int64)
    np.minimum.at(buf, key, depth[ok].astype(np.float32))
    return buf


def _save_atlas_png(image, path: str) -> bool:
    """把烘焙好的纹理图集写成 PNG（M4 主产物，**无条件**落盘）。

    为什么必须与 ``export_formats`` 解耦：图集是逐纹素多视图采样的成果，
    只有 obj/gltf（外挂 mtl+png）或 glb（内嵌）能引用它；若配置只写了 ply，
    旧实现连 PNG 都不写，于是 2048² 图集只存在于缓存目录，交付包与 M5
    质检都拿不到（实机 2026-10-26：2,639,926 纹素 → 85,420 顶点色）。

    返回是否写出成功（失败只告警，不阻断几何/顶点色交付）。
    """
    try:
        from PIL import Image as _Image

        arr = np.asarray(image)
        if arr.dtype != np.uint8:
            arr = np.clip(arr, 0, 255).astype(np.uint8)
        _Image.fromarray(arr).save(path)
        return True
    except Exception as exc:  # noqa: BLE001 - 图集落盘失败不阻断交付
        log.warning("图集 PNG 落盘失败：%s（%s）", path, exc)
        return False


def _export_vertex_colored_ply(mesh, uv, faces, image, out_dir: str,
                               item_id: str):
    """用已烘焙的纹理给顶点上色并导出 PLY（照片模式的兜底彩模）。"""
    import open3d as o3d

    size = image.shape[0]
    uv_a = np.asarray(uv, dtype=np.float64)
    u = np.clip(uv_a[:, 0] * size, 0, size - 1)
    v = np.clip((1.0 - uv_a[:, 1]) * size, 0, size - 1)
    col = _bilinear(image, u, v) / 255.0
    out = o3d.geometry.TriangleMesh(mesh)
    out.vertex_colors = o3d.utility.Vector3dVector(np.clip(col, 0, 1))
    path = os.path.join(out_dir, f"{item_id}_L1.0.ply")
    if not o3d.io.write_triangle_mesh(path, out):
        log.warning("顶点色 PLY 写出失败：%s", path)
        return None
    log.info("顶点色 PLY 兜底导出：%s", path)
    return path



def resolve_photo_dir(task_root: str, item_id: str, cfg) -> str:
    """定位本件照片目录——与 S1（:func:`h3d.sfm_solve._photo_dir_for`）同源。

    实机问题（2026-10-18）：S1 允许用户把照片**直接平铺在 00_photo/ 下**（不建
    ``<item>`` 子目录），但纹理阶段只认 ``00_photo/<item>`` → 「S1 识别到 30 张、
    M4 却说照片目录不存在」→ 静默降级顶点色 → 交付一个没有照片纹理的模型。
    两处解析必须同源，否则永远是「能解算、不能贴图」。
    """
    base = (cfg.sfm_solve.photo_dir or cfg.photo_texture.photo_dir or "")
    if not base:
        cand = os.path.join(task_root, "00_photo")
        if not os.path.isdir(cand):
            cand = os.path.join(os.path.dirname(os.path.abspath(task_root)),
                                "00_photo")
        base = cand
    item_dir = os.path.join(base, item_id)
    if not os.path.isdir(item_dir):
        from h3d.core.media import list_images
        if list_images(base):
            return base      # 平铺布局
    return item_dir


def texture_mesh_from_photos(mesh, task_root: str, item_id: str, cfg,
                             out_dir: str, geometry_is_sfm: bool = False):
    """照片投影纹理核心（M4 paint_mode='photo' 与 P1 共享）。

    流程：读 capture_meta → （可选）坐标系对齐（控制点引导优先，回退自动
    ICP）→ UV 展开 → 照片投影 → 泊松融合（接缝平滑 + 空洞填充）→ 导出。

    geometry_is_sfm: 网格本身已由 SfM 稠密重建得到（场景②：无激光扫描）。
        此时网格与相机位姿**同处 SfM 坐标系**，再做 ICP 相似变换只会引入
        无谓的旋转/缩放漂移（实测会估出非 1.0 的 scale_s），故跳过对齐。

    返回 (outputs, metrics)。
    """
    from h3d.core import RecoverableError
    from h3d.texture.uv import attach_texture, export_textured, unwrap_mesh

    meta_path = os.path.join(task_root, "90_meta",
                             f"{item_id}_capture_meta.json")
    if not os.path.exists(meta_path):
        raise RecoverableError(
            f"照片纹理缺少 S1 位姿产物（请先启用 sfm_solve 或放置 "
            f"90_meta/{item_id}_capture_meta.json）: {meta_path}")
    photo_dir = resolve_photo_dir(task_root, item_id, cfg)
    if not os.path.isdir(photo_dir):
        raise RecoverableError(f"照片目录不存在: {photo_dir}")

    cameras, images = load_capture_meta(meta_path)
    if not images:
        raise RecoverableError("位姿元数据为空（无注册图像）")

    # 坐标系对齐（创新点 2 完整落地）：控制点引导（RANSAC 三角化 + 最近邻
    # 精化两阶段）优先，回退自动 ICP
    align_metrics = {}
    verts0 = np.asarray(mesh.vertices, dtype=np.float64).copy()  # 激光系
    sparse_pts = None
    # ---- 位姿约定归一（2026-11-03）----
    # 必须在**任何投影之前**。真机 capture_meta 是 2026-09-15「坐标系修复」前的
    # 旧格式（R_co + tvec，前方 +Z），管线按「t=相机中心、前方 -Z」读它 →
    # 30 台里 19 台一条纹素都投不上。**最阴的是它有产出**：剩下 11 台偶然投上
    # 让 in_frame 报出 86.7%，于是下面「几何已在画幅内 → 跳过对齐」的闸门正当
    # 地放行了——判据在垃圾数据上触发，结果看着合理（coverage 57%）。
    # 判据用 SfM 稀疏点（与位姿同系）试两种解释，取投得上的那个。
    sparse_txt = os.path.join(cfg.sfm_solve.workspace or
                              os.path.join(task_root, "99_runs",
                                           "sfm_tmp", item_id),
                              "sparse", "0_txt")
    pts3d_path = os.path.join(sparse_txt, "points3D.txt")
    if os.path.exists(pts3d_path):
        from h3d.sfm_solve import read_points3d
        sparse_pts = read_points3d(pts3d_path)
    pose_metrics = normalise_pose_convention(images, cameras, sparse_pts)
    # 投影级预检（2026-11-01，覆盖率 21%→57% 的那一刀）：
    # **几何本来就在相机画幅里就没必要对齐**。真机 `10_clean`（激光件 6.34）对
    # 30 台相机算 in_frame=86.7%——它已经与位姿同系；再跑一次对齐纯属净损失：
    # 两阶段对齐把中位距离从 42.9% 拉到 1.0% 后被拒、回退恒等，但回退**没有
    # 真正恢复顶点**（UV 光栅化仍为空 → M4 降级顶点色）。
    # 这条判据比 `already_aligned`（比的是「几何 vs 稀疏点云」）更本质：它直接
    # 问「还需不需要对齐」。
    _need_align = True
    if cfg.photo_texture.icp_align:
        try:
            _pre_ratio = in_frame_ratio(verts0, images, cameras, sample=20000)
            if not alignment_needed(_pre_ratio):
                _need_align = False
                log.info("照片纹理跳过对齐：几何已有 %.1f%% 顶点落在相机画幅内"
                         "（阈值 50%%）——它本来就是同系的，对齐只会引入损失",
                         _pre_ratio * 100)
        except Exception as exc:  # noqa: BLE001 - 预检失败按需要对齐处理
            log.debug("对齐预检失败（按需要对齐处理）：%s", exc)

    if (geometry_is_sfm or not _need_align) and cfg.photo_texture.icp_align:
        align_metrics = apply_align_status(
            {"reason": ("geometry_from_sfm（同坐标系，无需对齐）"
                        if geometry_is_sfm else
                        "already_in_frame（几何已在相机画幅内，无需对齐）")},
            None)
    elif cfg.photo_texture.icp_align and len(images) >= 2:
        # ---- 候选**无条件**生成，再统一择优（2026-11-03）--------------------
        # 旧结构是「控制点优先、失败才回退自动 ICP」——于是「磁盘上有没有文件」
        # 决定了走哪条路：一次成功的 ICP 会把控制点导出到 00_ctrl/*.json，**下次
        # 运行就走控制点那条**，而它实测解出 90° 旋转错。同一份输入连跑三次
        # normal_visible 得 0.96 / 0.69 / 0.58，就是这么来的（真机 control_points
        # .json 的 mtime 与其中一次 ICP 运行对得上，只有 3 个点）。
        #
        # 现在两条都算（各几秒），按 visible_score 取优；闸门结论只作指标记录，
        # 不再一票否决——否决权交给「对齐后到底能不能被相机看到」。
        # 所有候选都从**同一份原始几何** verts0 出发，杜绝「上游改过 mesh」。
        import open3d as _o3d_pick

        from h3d.ctrl_points import (ControlPointError, align_two_stage,
                                     find_ctrl_file, load_control_points)
        from h3d.geo_align import align_points_to_mesh, already_aligned

        tris_full = np.asarray(mesh.triangles).copy()

        def _mk_mesh(v):
            mm = _o3d_pick.geometry.TriangleMesh()
            mm.vertices = _o3d_pick.utility.Vector3dVector(
                np.asarray(v, dtype=np.float64))
            mm.triangles = _o3d_pick.utility.Vector3iVector(tris_full)
            return mm

        skip_align = False
        if sparse_pts is not None and len(sparse_pts) >= 8:
            pre = already_aligned(verts0, sparse_pts)
            if pre["aligned"]:
                skip_align = True
                align_metrics = apply_align_status(
                    {"reason": "already_aligned"
                               "（几何与 SfM 点云同系，中位距离 "
                               f"{pre['norm_median'] * 100:.2f}% 对角线）",
                     "quality": pre}, None)
                log.info("照片纹理对齐跳过：几何已与 SfM 点云同系"
                         "（中位距离 %.2f%% 对角线，阈值 %.0f%%）",
                         pre["norm_median"] * 100, pre["tolerance"] * 100)

        if not skip_align:
            cands = [("identity", verts0.copy(), tris_full,
                      {"method": "none", "reason": "未对齐（基准候选）"})]
            gate_note = {}

            ctrl_path = find_ctrl_file(task_root, item_id)
            if ctrl_path and sparse_pts is not None:
                try:
                    controls = load_control_points(ctrl_path)
                    images_map = {im["name"]: im for im in images}
                    _before_ts = in_frame_ratio(verts0, images, cameras)
                    cp_mesh, m_cp = align_two_stage(
                        _mk_mesh(verts0), controls, images_map, cameras,
                        sparse_pts)
                    v_cp = np.asarray(cp_mesh.vertices, dtype=np.float64)
                    m_cp = dict(m_cp)
                    m_cp["in_frame_before"] = round(_before_ts, 4)
                    m_cp["in_frame_after"] = round(
                        in_frame_ratio(v_cp, images, cameras), 4)
                    cands.append(("control_points", v_cp, tris_full, m_cp))
                except ControlPointError as exc:
                    gate_note["control_points_error"] = str(exc)
                    log.warning("控制点对齐失败（不参与择优）：%s", exc)
                except Exception as exc:  # noqa: BLE001
                    gate_note["control_points_error"] = str(exc)
                    log.warning("控制点对齐异常（不参与择优）：%s", exc)

            if sparse_pts is not None and len(sparse_pts) >= 4:
                s_i, R_i, T_i, m_i = align_points_to_mesh(
                    sparse_pts, _mk_mesh(verts0))
                m_i = dict(m_i)
                if m_i.get("accepted") is False:
                    # 闸门判无效时不把它的结果当候选（返回的是恒等变换，等同于
                    # identity），但把理由留下来——它是"为什么没有 ICP 候选"。
                    gate_note["auto_icp_reject"] = m_i.get("reject_reason", "")
                else:
                    v_i = (R_i.T @ (verts0 - T_i).T).T / s_i
                    m_i["in_frame_before"] = round(
                        in_frame_ratio(verts0, images, cameras), 4)
                    m_i["in_frame_after"] = round(
                        in_frame_ratio(v_i, images, cameras), 4)
                    cands.append(("auto_icp", v_i, tris_full, m_i))

            _picked, _pverts, align_metrics = choose_alignment(
                cands, images, cameras)
            align_metrics.update(gate_note)
            mesh.vertices = _o3d_pick.utility.Vector3dVector(
                np.asarray(_pverts, dtype=np.float64).copy())
            mesh.triangles = _o3d_pick.utility.Vector3iVector(tris_full)
            mesh.compute_vertex_normals()

            if _picked == "identity":
                align_metrics["reject_reason"] = (
                    "所有对齐候选都不如不对齐（可见性未提升），保持原坐标系")
                align_metrics = apply_align_status(align_metrics, False)
                log.warning("照片纹理：对齐候选全部落选，保持原坐标系（%s）",
                            align_metrics.get("align_candidates"))
            else:
                align_metrics = apply_align_status(align_metrics, True)
                log.info("照片纹理对齐（择优=%s）：%s", _picked, align_metrics)

            # 自动导出有效控制点（创新点 2 实证）：激光系顶点 + 真实照片 uv。
            # **只在择优选中 auto_icp 时导出**：把控制点那条自己的结果再导出成
            # 控制点，等于让错误答案自我强化（2026-11-03 就是这么把一个 90° 错
            # 解固化下来的）。
            if _picked == "auto_icp":
                try:
                    from h3d.ctrl_auto import export_control_points_from_align
                    out_ctrl = os.path.join(task_root, "00_ctrl", item_id,
                                            "control_points.json")
                    res = export_control_points_from_align(
                        verts0, np.asarray(_pverts, dtype=np.float64),
                        images, cameras, out_ctrl, n=8)
                    if res:
                        align_metrics["auto_control_points"] = res["n_points"]
                        log.info("自动控制点导出：%d 点 -> %s",
                                 res["n_points"], out_ctrl)
                except Exception as exc:  # noqa: BLE001
                    log.warning("自动控制点导出失败（跳过）：%s", exc)

    # UV 展开 + 照片投影 + 泊松融合 + 导出
    #
    # 跨件复用（2026-10-24 实机 5 件）：同批照片的件经 S1 复用后几何逐位相同，
    # xatlas 展开（33 s）与逐纹素采样（50 s）却被重复做了 5 次。此处按「几何
    # 指纹」「五项纹理指纹」两级命中直接取图集，导出仍按 item_id 重做。
    # 复用判据与失效边界见 h3d.texture.atlas_cache 模块文档。
    import time as _time
    from h3d.texture.atlas_cache import (AtlasCache, mesh_fingerprint,
                                         params_fingerprint, photo_fingerprint,
                                         pose_fingerprint)
    from h3d.texture.uv import rebuild_with_uv

    cache = AtlasCache(task_root,
                       enabled=bool(getattr(cfg.photo_texture,
                                            "reuse_atlas", True)))
    reuse = {"uv": "disabled" if not cache.enabled else "miss",
             "texture": "disabled" if not cache.enabled else "miss",
             "uv_from": None, "texture_from": None}

    t_uv = _time.time()
    geom_fp = mesh_fingerprint(mesh)
    hit = cache.get_uv(geom_fp)
    if hit is not None:
        vmap, indices_c, uvs, uv_meta = hit
        # 形状自洽校验：指纹相同理论上必自洽，但缓存文件可能被外部改写；
        # 越界下标会让 Open3D 直接抛 C++ 异常（比 Python 异常难查）。
        ok_shape = (int(vmap.max(initial=-1)) < len(mesh.vertices)
                    and int(indices_c.max(initial=-1)) < len(vmap)
                    and int(vmap.min(initial=0)) >= 0)
        if ok_shape:
            new_mesh, uv, faces = rebuild_with_uv(mesh, vmap, indices_c, uvs)
            reuse["uv"] = "hit"
            reuse["uv_from"] = uv_meta.get("source_item")
            log.info("UV 图集复用：%s（几何指纹 %s，来自 %s，跳过 xatlas 展开）",
                     item_id, geom_fp[:12], reuse["uv_from"])
        else:
            log.warning("UV 缓存下标越界（几何指纹 %s），按未命中重算", geom_fp[:12])
            hit = None
    if hit is None:
        new_mesh, uv, faces, vmap = unwrap_mesh(mesh, return_vmap=True)
        cache.put_uv(geom_fp, vmap, faces, uv, item_id)
    uv_s = round(_time.time() - t_uv, 2)

    t_tex = _time.time()
    nrm_fp = mesh_fingerprint(new_mesh, with_normals=True)
    tex_key = cache.texture_key(geom_fp, nrm_fp,
                               pose_fingerprint(cameras, images),
                               photo_fingerprint(photo_dir),
                               params_fingerprint(cfg))
    tex_hit = cache.get_texture(tex_key)
    if tex_hit is not None:
        image, painted, tex_meta = tex_hit
        expected = (int(cfg.photo_texture.texture_size),) * 2
        if image.shape[:2] != expected:
            log.warning("纹理缓存尺寸 %s 与配置 %s 不符，按未命中重算",
                        image.shape[:2], expected)
            tex_hit = None
        else:
            metrics = dict(tex_meta.get("metrics") or {})
            reuse["texture"] = "hit"
            reuse["texture_from"] = tex_meta.get("source_item")
            log.info("纹理图集复用：%s（来自 %s，跳过逐纹素采样与融合）",
                     item_id, reuse["texture_from"])
    cam_map = None          # 命中纹理缓存时保持 None：诊断图不重算（缺图 ≠ 无台阶）
    if tex_hit is None:
        image, metrics, painted, cam_map = project_photos_to_texture(
            new_mesh, uv, faces, images, cameras, photo_dir,
            size=cfg.photo_texture.texture_size,
            top_k=cfg.photo_texture.top_k,
            hole_fill_px=int(getattr(cfg.photo_texture, "hole_fill_px", 0) or 0),
            balance_exposure=bool(
                getattr(cfg.photo_texture, "balance_exposure", False)),
            samples_per_tri=cfg.photo_texture.samples_per_tri,
            want_mask=True, want_camera_map=True)
        if cfg.photo_texture.poisson_blend:
            # 泊松融合（P1 GPU 形态改造）：共轭梯度一次求解——4K <1s（实测
            # 0.82s，比 Jacobi 迭代快 33x）；失败回退多分辨率迭代。
            #
            # 掩膜必须用**真实着色掩膜**：旧实现写 `image.sum(axis=2) > 20`，
            # 对暗色器物（青铜/漆木，整片亮度 <20）会把已着色的纹素判成空洞，
            # 等于把好不容易采到的颜色当成"待填补区域"重新解算。
            try:
                from h3d.texture.blend_cg import poisson_blend_cg
                image = poisson_blend_cg(image, painted)
                metrics["poisson_blended"] = True
                metrics["poisson_method"] = "cg"
            except Exception as exc:  # noqa: BLE001 - 降级多分辨率迭代
                from h3d.texture.blend import poisson_blend_multires
                log.warning("CG 融合失败（降级多分辨率迭代）：%s", exc)
                max_level = 2 if cfg.photo_texture.texture_size >= 1024 else 1
                image = poisson_blend_multires(
                    image, painted, max_level=max_level,
                    iterations_coarse=cfg.photo_texture.blend_iterations)
                metrics["poisson_blended"] = True
                metrics["poisson_method"] = "multires"
        cache.put_texture(tex_key, image, painted, metrics, item_id)
    tex_s = round(_time.time() - t_tex, 2)
    reuse["state"] = ("hit" if reuse["uv"] == "hit" and reuse["texture"] == "hit"
                      else "partial" if "hit" in (reuse["uv"], reuse["texture"])
                      else "miss")
    metrics.update({"atlas_reuse": reuse, "atlas_cache": reuse["state"],
                    "uv_s": uv_s, "texture_s": tex_s})

    t_exp = _time.time()
    attach_texture(new_mesh, uv, faces, image)
    os.makedirs(out_dir, exist_ok=True)
    # ---- UV 布局落盘（B1 图集继承的唯一权威载体）-------------------------
    # 在此之前 UV 只活在内存里：跑完即丢，第二次烘焙只能重新展开，而「重展开
    # 是否与上次逐 texel 对齐」没有任何保证。图集继承要的正是「同一套 UV」，
    # 所以必须落成一个可复算指纹的文件，并由 archive 的 atlas 记录冻结版本。
    # 幂等：内容没变时不重写（mtime 是「图集被重算过」的证据）。
    try:
        from h3d.archive.atlas import save_uv_layout
        # 必须连**展开后拓扑的顶点与法线**一起存：uv/faces 的索引属于 new_mesh
        # （接缝处顶点被拆分，顶点数 ≥ 原始 PLY），只存 uv/faces 的话继承端拿
        # 原始网格承接会面索引越界。
        metrics["uv_layout"] = save_uv_layout(
            os.path.join(out_dir, f"{item_id}_uv.npz"), uv, faces,
            verts=np.asarray(new_mesh.vertices),
            normals=np.asarray(new_mesh.vertex_normals))
        log.info("UV 布局：%s（%d 顶点 / %d 面，指纹 %s%s）",
                 os.path.basename(metrics["uv_layout"]["path"]),
                 metrics["uv_layout"]["vertices"], metrics["uv_layout"]["faces"],
                 metrics["uv_layout"]["digest"][:12],
                 "" if metrics["uv_layout"]["written"] else "，内容未变未重写")
    except Exception as exc:  # noqa: BLE001 - 布局落盘失败不阻断贴图交付
        metrics["uv_layout_error"] = str(exc)[:200]
        log.warning("UV 布局落盘失败（图集将无法继承）：%s", exc)
    # 着色掩膜落盘：M5 以它判填充率（比亮度阈值可靠），同时也是排障依据
    # 逐纹素「胜出相机」诊断图（2026-11-03，ADR-0016）：判 X3 图斑是不是
    # 岛内相机拼接台阶。存 16 位 PNG：0 = 未着色，k+1 = 第 k 台相机胜出。
    # 复用缓存时不重算，此时不写（缺图不等于"没有台阶"）。
    if cam_map is not None:
        # 落 99_runs/ 而不是交付目录：诊断产物不进交付包（同 _uv.npz 的定位）。
        # 交付目录只放用户要的东西，多一张 4MB 的诊断图就是噪声。
        _cmap_dir = os.path.join(task_root, "99_runs", "camera_id")
        os.makedirs(_cmap_dir, exist_ok=True)
        cmap_path = os.path.join(_cmap_dir, f"{item_id}_camera_id.png")
        try:
            from PIL import Image as _Image
            _Image.fromarray((cam_map.astype(np.int32) + 1).astype(np.uint16)) \
                .save(cmap_path)
            metrics["camera_id_map"] = cmap_path
        except Exception as exc:  # noqa: BLE001 - 诊断图写失败不影响交付
            metrics["camera_id_map_error"] = str(exc)[:200]
            log.warning("相机 id 诊断图写出失败（跳过）：%s", exc)
    mask_path = os.path.join(out_dir, f"{item_id}_L1.0_mask.png")
    try:
        from PIL import Image as _Image
        _Image.fromarray((painted.astype(np.uint8) * 255)).save(mask_path)
        metrics["texture_mask"] = mask_path
    except Exception as exc:  # noqa: BLE001 - 掩膜写失败不影响贴图交付
        metrics["texture_mask_error"] = str(exc)[:200]
        log.warning("着色掩膜写出失败（M5 将退回亮度阈值）：%s", exc)
    # uv/image 必须一并传入：GLB 的纹理只能由 trimesh 写出（Open3D 静默丢图），
    # 且 diffse PNG 由导出器统一落为 <item>_L1.0_diffuse.png 并改写 MTL。
    #
    # ---- 图集落盘（无条件）+ 纹理承载格式自检（2026-10-26 实机）----------
    # 实机：texture/photo_texture.export_formats=[ply] → export_textured 只写 PLY，
    # 而 **PLY 无法引用图像纹理**。于是 2048² 图集（2,639,926 纹素、逐纹素采样
    # 50.18 s）只留在 99_runs/atlas_cache/ 里，交付包只剩 85,420 个顶点色
    # （有效色彩采样差 31 倍）；同时 M5 找不到 ``*_diffuse.png``，五项纹理指标
    # 全 N/A，与 M4 自报的岛内填充率 94.89% 自相矛盾。
    # 结论：**图集是 M4 的主产物，与"导出哪些格式"无关**——必须先无条件落盘，
    # 再单独判断有没有模型引用它。
    requested = [str(f).lstrip(".").lower()
                 for f in (cfg.photo_texture.export_formats or [])]
    formats = list(requested)
    external = [f for f in formats if f in ("obj", "gltf")]
    embedded = "glb" in formats
    auto_added: list[str] = []
    if not external and not embedded:
        base_msg = ("照片纹理已烘焙 %d² 图集，但 export_formats=%s 里没有任何格式能"
                    "承载图像纹理（PLY 只能存顶点色）——图集将成为孤立文件")
        if bool(getattr(cfg.photo_texture, "auto_textured_format", True)):
            auto_added = ["obj"]
            formats.append("obj")
            external = ["obj"]
            log.warning(base_msg + "；已自动补导 OBJ（obj+mtl+diffuse），"
                        "如需禁止请设 photo_texture.auto_textured_format=false",
                        int(cfg.photo_texture.texture_size), requested)
        else:
            log.warning(base_msg + "；auto_textured_format=false，按配置原样导出，"
                        "图集仅单独落盘、不被任何模型引用",
                        int(cfg.photo_texture.texture_size), requested)

    atlas_rel = f"{item_id}_L1.0_diffuse.png"
    atlas_path = os.path.join(out_dir, atlas_rel)
    # OBJ 导出会把图集写成同名 PNG（_normalize_obj_texture），此时不重复写盘
    if "obj" not in formats:
        _save_atlas_png(image, atlas_path)
    outputs = export_textured(new_mesh, out_dir, item_id, "L1.0", formats,
                              uv=uv, image=image)
    if not os.path.exists(atlas_path):
        # 防御：只有 glb（纹理内嵌）或导出器改名时，图集仍须独立可查
        _save_atlas_png(image, atlas_path)
    atlas_ok = os.path.exists(atlas_path)
    if atlas_ok and os.path.abspath(atlas_path) not in outputs:
        outputs.append(os.path.abspath(atlas_path))
    elif not atlas_ok:
        log.warning("图集 PNG 落盘失败（M5 纹理质检将不可用）：%s", atlas_path)
    # 顶点色 PLY 兜底：照片纹理只有 obj/glb 能承载，而 publish.formats 里
    # 通常含 ply。缺 PLY 时交付包会少一个"任何软件都能打开"的彩模，
    # 且用户排查贴图问题时没有独立参照。此处按同一套视角加权把颜色写到顶点上。
    try:
        ply_out = _export_vertex_colored_ply(
            new_mesh, uv, faces, image, out_dir, item_id)
        if ply_out:
            outputs.append(ply_out)
            metrics["vertex_ply"] = os.path.basename(ply_out)
    except Exception as exc:  # noqa: BLE001 - 兜底产物失败不阻断交付
        log.warning("顶点色 PLY 兜底导出失败（跳过）：%s", exc)
    metrics.update({
        "icp_align": align_metrics or {"status": "skipped"},
        # 位姿约定（2026-11-03）：`pose_convention` 取 pipeline/colmap_raw/unknown，
        # `pose_probe_*` 是两种解释下稀疏点的入画比例。三者一起看才知道「位姿到底
        # 是不是被改过」——只看覆盖率看不出来（旧格式下仍有 11 台偶然投上）。
        **pose_metrics,
        "n_outputs": len(outputs),
        "paint_mode": "photo",
        # formats = 实际导出格式（含自动补导）；requested = 配置原值。
        # 两个都落盘，报表/元数据才能自证「配置 ≠ 产物」是不是有意为之。
        "formats": formats,
        "formats_requested": requested,
        "auto_added_formats": auto_added,
        "texture_carrier": ("obj" if "obj" in formats
                            else "gltf" if "gltf" in formats
                            else "glb" if "glb" in formats else None),
        "atlas_png": os.path.abspath(atlas_path) if atlas_ok else None,
        "atlas_size_px": int(image.shape[0]),
        "atlas_bytes": (int(os.path.getsize(atlas_path)) if atlas_ok else 0),
        "export_s": round(_time.time() - t_exp, 2),
    })
    return outputs, metrics


def run_photo_texture(task, cfg):
    """P1 stage：M3 网格 + capture_meta + 00_photo 照片 → 50_tex 带纹理模型。

    默认关闭（cfg.photo_texture.enabled=false）。复用核心
    :func:`texture_mesh_from_photos`（对齐 → UV → 投影 → 泊松融合 → 导出）；
    当 M4 已用 paint_mode='photo' 处理时（同目录输出）跳过，避免重复。
    """
    from h3d.core import InputDataError, RecoverableError
    from h3d.io import load_mesh
    from h3d.runner.task import StageResult, TaskStatus

    if not cfg.photo_texture.enabled:
        return StageResult("P1", TaskStatus.SKIPPED, [],
                           {"skip_reason": "photo_texture.enabled=false"}, 0.0)
    if cfg.texture.paint_mode == "photo":
        # M4 已以 photo 模式完成纹理（统一颜色源开关），P1 不重复执行
        return StageResult("P1", TaskStatus.SKIPPED, [], {
            "skip_reason": "M4 已按 paint_mode='photo' 输出，不重复投影"}, 0.0)
    import time
    t0 = time.time()
    mesh_path = os.path.join(task.root, "30_dense", task.item_id,
                             f"{task.item_id}.ply")
    if not os.path.exists(mesh_path):
        raise InputDataError(f"P1 缺少 M3 网格产物: {mesh_path}")
    mesh = load_mesh(mesh_path)
    out_dir = os.path.join(task.root, "50_tex", task.item_id)
    outputs, metrics = texture_mesh_from_photos(
        mesh, task.root, task.item_id, cfg, out_dir,
        geometry_is_sfm=not task.raw_paths)
    elapsed = time.time() - t0
    metrics["elapsed_s"] = round(elapsed, 2)
    log.info("P1 完成：%s（%s，%d 文件，%.1fs）", task.item_id, metrics,
             len(outputs), elapsed)
    return StageResult("P1", TaskStatus.DONE, outputs, metrics, elapsed)


__all__ = ["load_capture_meta", "resolve_photo_dir",
           "project_photos_to_texture", "texture_mesh_from_photos",
           "run_photo_texture"]
