"""h3d.texture.blend : 泊松融合（接缝平滑 + 空洞填充）。

设计依据：
    《照片导入方案》—— photo_texture 自研流程末段「泊松融合（接缝消隐）」；
    《路线 R2》—— P1 照片投影纹理的质量提升：多视角采样在 UV 接缝处
    存在颜色不连续，稀疏采样存在空洞；泊松融合在梯度域平滑接缝并用
    拉普拉斯约束填充空洞，提升纹理视觉连续性与覆盖率。

实现（简化梯度域融合，纯 numpy）：
    poisson_blend —— Jacobi 迭代求解拉普拉斯（Dirichlet 边界=已知像素）：
      对空洞/接缝像素 f，迭代 f = 邻域均值，直至收敛——等价于对空洞
      求解 Δf = 0（调和延拓），对已有像素保持原值（边界固定）。
    seam_smooth  —— 接缝轻平滑（单次邻域平均，低通去不连续）。
"""

from __future__ import annotations

import numpy as np

from h3d.core import get_logger

log = get_logger("texture.blend")


def poisson_blend(image: np.ndarray, mask: np.ndarray,
                  iterations: int = 200, tol: float = 1e-3,
                  boundary: float = 0.35) -> np.ndarray:
    """拉普拉斯调和延拓：填充 mask 空洞 + 平滑接缝（泊松融合简化版）。

    参数
    ----
    image:  (H,W,3) float 纹理图（未填充像素可为 0）。
    mask:   (H,W) bool，True = 已有有效颜色（Dirichlet 边界）。
    iterations: Jacobi 最大迭代次数。
    tol:    收敛判据（相对变化 < tol 提前停止）。
    boundary: 只处理 mask 邻域内的像素（空洞填充范围），避免全图扩散。

    返回
    ----
    融合后的 (H,W,3) 图像（空洞被调和填充，接缝被平滑）。
    """
    if image.ndim != 3 or mask.shape != image.shape[:2]:
        raise ValueError("image 应为 (H,W,3)，mask 应为 (H,W)")
    out = image.astype(np.float64).copy()
    known_b = mask.astype(bool)

    # 待修复区域：mask 的空洞 + 邻域边界（扩散 2 像素，避免全图拉普拉斯）
    dil = _dilate_mask(mask, 2)
    repair = dil & ~mask
    if not repair.any():
        return image  # 无空洞，无需融合

    # GPU 可选加速（P2）：cupy 可用时 Jacobi 迭代在 GPU 执行；
    # 未安装自动降级 numpy（依赖可选、失败隔离）。
    xp, on_gpu = _get_array_module()
    if on_gpu:
        try:
            out_g = xp.asarray(out)
            repair_g = xp.asarray(repair)
            known_g = xp.asarray(known_b)
            img_g = xp.asarray(image)
            for _it in range(iterations):
                acc = xp.zeros_like(out_g)
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    acc += xp.roll(out_g, (dy, dx), axis=(0, 1))
                acc /= 4.0
                out_g = xp.where(repair_g[..., None], acc, out_g)
                out_g = xp.where(known_g[..., None], img_g, out_g)
            return xp.clip(xp.asnumpy(out_g), 0, 255).astype(np.uint8)
        except Exception as exc:  # noqa: BLE001 - GPU 失败降级 CPU
            log.warning("GPU 融合失败（降级 CPU）：%s", exc)

    # Jacobi 迭代求解 Δf = 0（Dirichlet 边界 = 已知像素）：
    #   每轮 f_new = 四邻域均值；已知像素每轮恢复为原值。
    for it in range(iterations):
        old = out.copy()
        acc = np.zeros_like(out)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            acc += np.roll(old, (dy, dx), axis=(0, 1))
        acc /= 4.0
        out[repair] = acc[repair]
        out[known_b] = image[known_b]
        if it % 10 == 9:
            diff = np.abs(out[repair] - old[repair]).max()
            if diff < tol * 255:
                break
    return np.clip(out, 0, 255).astype(np.uint8)


def _dilate_mask(mask: np.ndarray, radius: int = 2) -> np.ndarray:
    """mask 膨胀（四邻域扩张 radius 轮）。"""
    out = mask.copy()
    for _ in range(radius):
        nxt = out.copy()
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nxt |= np.roll(out, (dy, dx), axis=(0, 1))
        out = nxt
    return out


def seam_smooth(image: np.ndarray, mask: np.ndarray,
                radius: int = 1) -> np.ndarray:
    """接缝轻平滑：对 mask 边界像素做邻域平均（低通去不连续）。"""
    out = image.astype(np.float64).copy()
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        out += np.roll(image.astype(np.float64), (dy, dx), axis=(0, 1))
    out /= 5.0
    # mask 边界 = mask 中邻接非 mask 的像素（低通处理消除硬接缝）
    edge = mask & _dilate_mask(~mask, 1)
    result = image.astype(np.float64).copy()
    result[edge] = out[edge]
    return np.clip(result, 0, 255).astype(np.uint8)


__all__ = ["poisson_blend", "seam_smooth"]


def poisson_blend_multires(image: np.ndarray, mask: np.ndarray,
                           max_level: int = 2, iterations_coarse: int = 120,
                           iterations_fine: int = 30,
                           tol: float = 1e-3) -> np.ndarray:
    """多分辨率泊松融合（P2，2048²+ 大纹理加速）。

    粗层（下采样 2^max_level）先做低分辨率调和延拓（Jacob 迭代少、像素少，
    快速收敛到整体平滑场）→ 逐层上采样并作为细层初始值 → 原分辨率仅精修
    iterations_fine 轮。相比直接在原分辨率迭代（像素多、需大量轮次才扩散
    远距），多分辨率显著降低大纹理耗时。

    返回融合后的 (H,W,3) 图像。
    """
    import numpy as np

    if max_level < 1:
        return poisson_blend(image, mask, iterations=iterations_fine,
                             tol=tol)
    # 递归：先对 1/2 分辨率融合，再上采样作为本层初始
    h, w = image.shape[:2]
    h2, w2 = max(1, h // 2), max(1, w // 2)
    small = _downsample(image, (h2, w2))
    small_mask = _downsample_bool(mask, (h2, w2))
    if max_level > 1:
        small = poisson_blend_multires(small, small_mask,
                                       max_level - 1, iterations_coarse,
                                       iterations_fine, tol)
    else:
        small = poisson_blend(small, small_mask, iterations=iterations_coarse,
                              tol=tol)
    # 上采样初始 + 原分辨率精修（已知像素恢复）
    from PIL import Image as _Image
    up = np.asarray(_Image.fromarray(small.astype(np.uint8)).resize(
        (w, h), _Image.BILINEAR), dtype=np.float64)
    out = image.astype(np.float64).copy()
    known_b = mask.astype(bool)
    for _it in range(iterations_fine):
        old = out.copy()
        acc = np.zeros_like(out)
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            acc += np.roll(old, (dy, dx), axis=(0, 1))
        acc /= 4.0
        out = acc
        out[known_b] = image[known_b]
    # 只保留修复区（空洞+邻域）的平滑值，其余用原值+上采样混合
    from h3d.texture.blend import _dilate_mask
    repair = _dilate_mask(mask, 2) & ~mask
    if repair.any():
        out[repair] = 0.5 * out[repair] + 0.5 * up[repair]
    out[known_b] = image[known_b]
    return np.clip(out, 0, 255).astype(np.uint8)


def _downsample(img: np.ndarray, shape: tuple) -> np.ndarray:
    """双线性下采样（PIL）。"""
    from PIL import Image as _Image
    return np.asarray(_Image.fromarray(
        np.clip(img, 0, 255).astype(np.uint8)).resize(shape, _Image.BILINEAR),
        dtype=np.float64)


def _downsample_bool(mask: np.ndarray, shape: tuple) -> np.ndarray:
    """布尔 mask 下采样（任一 True 保留）。"""
    h, w = shape
    out = np.zeros((h, w), dtype=bool)
    hs = np.linspace(0, mask.shape[0], h + 1).astype(int)
    ws = np.linspace(0, mask.shape[1], w + 1).astype(int)
    for i in range(h):
        for j in range(w):
            out[i, j] = mask[hs[i]:hs[i + 1], ws[j]:ws[j + 1]].any()
    return out


def _get_array_module():
    """返回 (xp, on_gpu)：cupy 可用返回 (cupy, True)，否则 (numpy, False)。"""
    try:
        import cupy as cp  # noqa: F401
        return cp, True
    except ImportError:
        return np, False


def poisson_blend_gpu(image: np.ndarray, mask: np.ndarray,
                      iterations: int = 150, tol: float = 1e-3) -> np.ndarray:
    """显式 GPU 入口：poisson_blend 内部自动检测 cupy，未安装自动降级 CPU。"""
    return poisson_blend(image, mask, iterations=iterations, tol=tol)


__all__ = ["poisson_blend", "seam_smooth", "poisson_blend_multires",
           "_dilate_mask", "poisson_blend_gpu"]
