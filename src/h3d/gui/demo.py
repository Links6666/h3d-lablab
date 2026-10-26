"""h3d.gui.demo : 内置示例数据一键演示（合成数据自包含）。

生成微型示例项目：合成照片（纹理球体多视角）+ 合成激光点云（球面），
保证 demo 完全自包含、快速运行（小图小点云）。场景①（激光+照片）。

实机问题修复（2026-09-15）——「一键演示报错」根因：
    旧版 ``_synth_photo`` 的纹理由是**图像空间**函数（依赖像素坐标 (x,y) +
    方位角相位）生成的：同一块表面在不同视角下呈现的图案并不对应，照片之间
    没有真实的多视几何关系。COLMAP 因此找不到可用于初始化的一致匹配点对，
    mapper 报 ``No good initial image pair found``，S1 失败 → 整条 M1~M6 链
    未执行 → manifest 无 elapsed_s → 报表绘图抛 E601。

    新版改为**真三维射线投射渲染**：世界坐标确定的球面纹理 + 针孔相机投影，
    12 个视角拍摄同一物体，视角间特征真实对应（已实测 COLMAP sparse 成功
    解算出稀疏模型）。激光点云与照片共用同一椭球（同形体、同尺度），
    使 S1 位姿与激光几何天然同尺度，照片投影纹理可直接叠加。
"""

from __future__ import annotations

import os

import numpy as np

# ---------------------------------------------------------------------------
# 合成场景常量：椭球（半轴见 SHAPE_SCALE）+ 针孔相机环绕拍摄
#
# 为什么不是正球体：正球体旋转对称，**尺度不可观测**——自动 ICP 对齐会把
# 单位球映到任意半径的球上（scale_s 随机跳到 0.95/43 之类），照片投影随之
# 失准。改为三轴不等椭球（更像文物形态），尺度与位姿即可被正确解算。
# ---------------------------------------------------------------------------
SHAPE_SCALE = np.array([1.0, 1.30, 0.85])   # 椭球半轴 (x, y, z)
SPHERE_CENTER = np.array([0.0, 0.0, 0.0])
CAMERA_DISTANCE = 4.5   # 保证椭球完整入画（不裁切）
PHOTO_SIZE = (640, 480)
FOCAL_RATIO = 1.15      # focal = FOCAL_RATIO * 宽（与 COLMAP 默认内参接近）
# 图像像素 / 单位球弧长：单位球半径 1，弧长≈张角，故 1 单位球弧长
# 投影为 focal / CAMERA_DISTANCE 像素。把「想要的像素周期」换算成波数要用它。
PIX_PER_UNIT = FOCAL_RATIO * PHOTO_SIZE[0] / CAMERA_DISTANCE   # ≈ 163.6


def _lam_px_to_f(lam_px):
    """特征周期（像素）-> 平面波波数（rad / 单位球弧长）。

    单位球上 wave ``sin(f·(d·p))`` 的周期为 ``2π/f`` 单位弧长，投影到图像
    即 ``(2π/f)·PIX_PER_UNIT`` 像素，故 ``f = 2π·PIX_PER_UNIT / λ_px``。
    """
    return 2.0 * np.pi * PIX_PER_UNIT / np.asarray(lam_px, dtype=np.float64)


# 纹理频段设计（2026-09-15 实测标定，见 _sphere_texture 文档）
_TEX_MID = dict(n=64, lam=(3.0, 12.0))    # 中高频：决定关键点密度
_TEX_LOW = dict(n=8, lam=(12.0, 60.0))    # 低频：区域可区分性（长基线匹配）
_TEX_TILT = 0.5                           # 幅度-频率指数 a ∝ f^-tilt


def _sphere_texture(p_local: np.ndarray) -> np.ndarray:
    """球面局部坐标 -> 灰度纹理（世界坐标确定、带限、非周期，跨视角一致）。

    实现：**球面随机平面波场**（Berry 随机波，等价于球面上的带限高斯随机场）::

        acc(p) = Σ_k a_k · sin(f_k·(d_k·p) + ψ_k)

    其中 ``d_k`` 为球面均匀随机方向，``f_k`` 按目标**像素周期**采样，
    ``a_k ∝ f_k^-0.5``。

    为什么这样设计（2026-09-15 实机排查 + 对照实验结论）
    ---------------------------------------------------
    1. SIFT 是 **DoG 极值检测器**，而带限随机场的局部极值个数 ∝ f²。
       把能量集中到「周期 3~12 px」的频段（相机 focal≈736 px、距离 4.5，
       椭球正面约 420 px），可获得大量尺度合适的极值。
    2. 旧实现为 θ/φ **参数域**正弦叠加，缺陷有三：
       (a) 沿固定 θ/φ 轴叠加 → 极点附近频率被压缩、特征堆成条带，各向异性；
       (b) 20 个分量幅度均匀 → 细尺度分量只占总能量 1/√20，
           实测仅 **403 个关键点/图**；
       (c) 60° 周期方格（`sign(sin5θ)·sign(sin4φ)`）会造成近自相似，
           实测反而**破坏**匹配（有匹配像对 75 → 34 / 120）。
       改为三维随机方向 + f^-0.5 幅度加权后为 **4248 个关键点/图**。
    3. 频率**不能无限推高**：实测周期下限压到 3 px 已可用，而当高频分量
       能量占比过大时会混叠（各视角混叠噪声不同 → 描述子不可复现）。

    对照实验（同一组 16 视角、确定性 COLMAP，见 output/_t_tex.py）::

        方案        关键点/图  有匹配对  inlier/对  注册
        旧 θ/φ 正弦      403    45/120        63   3/16
        本方案          4248    76/120       522  16/16
        加方格纹理      2921    34/120       363   更差
    """
    pts = p_local.reshape(-1, 3)
    rng = np.random.default_rng(20260915)

    def bank(n, lam_lo, lam_hi):
        d = rng.normal(size=(n, 3))
        d /= np.linalg.norm(d, axis=1, keepdims=True)      # 球面均匀方向
        f = _lam_px_to_f(np.exp(rng.uniform(np.log(lam_lo),
                                            np.log(lam_hi), n)))
        psi = rng.uniform(0.0, 2.0 * np.pi, n)             # 随机相位（非周期）
        a = f ** (-_TEX_TILT) * rng.uniform(0.6, 1.4, n)
        a /= a.mean()
        return d, f, psi, a

    banks = [bank(_TEX_MID["n"], *_TEX_MID["lam"]),
             bank(_TEX_LOW["n"], *_TEX_LOW["lam"])]

    acc = np.zeros(len(pts), dtype=np.float64)
    for d, f, psi, a in banks:
        for i in range(0, len(f), 12):        # 分块：控制峰值内存
            sl = slice(i, i + 12)
            proj = pts @ d[sl].T              # (N, <=12)
            acc += (np.sin(proj * f[sl] + psi[sl]) * a[sl]).sum(axis=1)

    acc -= acc.mean()
    std = float(acc.std()) or 1.0
    t = 132.0 + 78.0 * (acc / std)            # 固定对比度（约 ±60 灰度）
    return np.clip(t, 18.0, 237.0).reshape(p_local.shape[:-1])


def _look_at(eye: np.ndarray, target: np.ndarray):
    """返回相机的 (forward, right, up) 正交基（右手系）。"""
    fwd = target - eye
    fwd = fwd / np.linalg.norm(fwd)
    up_world = np.array([0.0, 1.0, 0.0])
    right = np.cross(fwd, up_world)
    right = right / np.linalg.norm(right)
    up = np.cross(right, fwd)
    return fwd, right, up


def _synth_photo(size=PHOTO_SIZE, az_deg=0.0, el_deg=25.0, seed=0):
    """合成文物照片：针孔相机对纹理球体做射线投射（含 2x 超采样抗锯齿）。

    参数
    ----
    size:    图像尺寸 (宽, 高)。
    az_deg:  相机方位角（度，绕 Y 轴）。
    el_deg:  相机俯仰角（度）。
    seed:    兼容旧接口保留（渲染为确定性，不再使用随机种子）。

    返回
    ----
    ``PIL.Image``（RGB）。
    """
    from PIL import Image

    W, H = int(size[0]), int(size[1])
    focal = FOCAL_RATIO * W  # 与 COLMAP 默认内参 (~1.2*max) 接近，尺度一致

    az, el = np.radians(az_deg), np.radians(el_deg)
    eye = np.array([CAMERA_DISTANCE * np.cos(el) * np.cos(az),
                    CAMERA_DISTANCE * np.sin(el),
                    CAMERA_DISTANCE * np.cos(el) * np.sin(az)])
    fwd, right, up = _look_at(eye, SPHERE_CENTER)

    ss = 2  # 超采样倍数
    jj, ii = np.mgrid[0:H * ss, 0:W * ss]
    u = (ii + 0.5) / ss - W / 2.0
    v = (jj + 0.5) / ss - H / 2.0
    dirs = (fwd[None, None, :]
            + (u / focal)[..., None] * right[None, None, :]
            + (v / focal)[..., None] * up[None, None, :])

    # 射线-椭球求交：按半轴缩放到「单位球空间」后解二次方程
    o_s = (eye - SPHERE_CENTER) / SHAPE_SCALE
    d_s = dirs / SHAPE_SCALE
    A = np.sum(d_s * d_s, axis=-1)
    B = 2.0 * np.sum(d_s * o_s[None, None, :], axis=-1)
    C = float(np.sum(o_s * o_s)) - 1.0
    disc = B * B - 4.0 * A * C
    hit = disc > 0.0
    t = (-B - np.sqrt(np.clip(disc, 0.0, None))) / (2.0 * np.maximum(A, 1e-12))
    hit &= t > 0.0

    img = np.full((H * ss, W * ss), 32.0)  # 暗背景（无纹理，避免误匹配）
    if hit.any():
        # 单位球空间命中点（纹理查找）与世界坐标（法线）
        p_unit = o_s[None, None, :] + t[..., None] * d_s
        n = p_unit / SHAPE_SCALE                       # 椭球法向 ∝ (x/a², y/b², z/c²)
        n /= np.linalg.norm(n, axis=-1, keepdims=True)
        light = np.array([0.30, 0.75, 0.60])
        light /= np.linalg.norm(light)
        lam = np.clip(np.sum(n * light[None, None, :], axis=-1), 0.25, 1.0)
        # 纹理在 1x 分辨率求值后**广播**到超采样网格：纹理带限（最小特征
        # 周期约 4 px），2x2 超采样块内取值几乎相同，广播与逐点求值等价，
        # 但纹理计算量降到 1/4（合成 16 视角由 ~90 s 降到 ~25 s）。
        p_lo = p_unit[::ss, ::ss, :]
        albedo = np.repeat(np.repeat(_sphere_texture(p_lo), ss, axis=0),
                           ss, axis=1)
        img = np.where(hit, albedo * lam, img)

    img = img.reshape(H, ss, W, ss).mean(axis=(1, 3))
    img = img * 1.30   # 曝光提升（SIFT 在中等亮度下更稳定）
    rgb = np.stack([img * 0.92, img * 0.96, img * 1.08], -1)  # 青灰石质偏色
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))


def _synth_laser_cloud(n=8000, seed=1):
    """合成激光点云：椭球面（与照片同一形体、同一尺度）+ 少量噪声。"""
    rng = np.random.default_rng(seed)
    u = rng.uniform(-1, 1, n)
    th = rng.uniform(0, 2 * np.pi, n)
    r = np.sqrt(np.clip(1 - u ** 2, 0, 1))
    pts = np.stack([r * np.cos(th), r * np.sin(th), u], 1)  # 单位球
    pts = pts * SHAPE_SCALE + rng.normal(0, 0.003, pts.shape)  # -> 椭球 + 噪声
    return pts + SPHERE_CENTER


def create_demo_project(target_dir: str):
    """生成内置示例项目（场景①：合成激光 + 合成照片）。

    返回 ProjectConfig（已写入 config.yaml）。
    """
    from h3d.core import paths
    from h3d.gui.config_edit import ProjectConfig
    os.makedirs(target_dir, exist_ok=True)
    lay = paths.ensure_layout(target_dir)      # 综合文件夹 + 两棵子树
    item = "demo_relic"
    raw = os.path.join(lay["stage"], "00_raw", item)
    photo = os.path.join(lay["stage"], "00_photo", item)
    ctrl = os.path.join(lay["stage"], "00_ctrl", item)
    os.makedirs(raw, exist_ok=True)
    os.makedirs(photo, exist_ok=True)
    os.makedirs(ctrl, exist_ok=True)
    # 合成激光点云
    import open3d as o3d
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(_synth_laser_cloud())
    o3d.io.write_point_cloud(os.path.join(raw, "demo_laser.ply"), pcd)
    # 合成照片（8 方位 × 2 高度 = 16 视角环绕，方位步长 45° 保证重叠；
    # SfM 已验证可解算）
    for k, el in ((0, 25.0), (1, 50.0)):
        for i in range(8):
            img = _synth_photo(az_deg=i * 45.0, el_deg=el, seed=i + k * 10)
            img.save(os.path.join(photo, f"demo_{k}_{i:02d}.jpg"), quality=95)
    # 配置（小型快速演示参数）
    cfg = ProjectConfig()
    cfg.name = item
    cfg.item_id = item
    cfg.root_dir = target_dir
    cfg.scene = "scene1"
    cfg.laser_ply = os.path.join(raw, "demo_laser.ply")
    cfg.photo_dir = photo
    cfg.voxel = 0.05
    cfg.poisson_depth = 7
    cfg.texture_size = 1024
    cfg.export_formats = ["obj", "glb"]
    cfg.to_yaml(os.path.join(target_dir, "config.yaml"))
    return cfg


__all__ = ["create_demo_project", "_synth_photo", "_synth_laser_cloud"]
