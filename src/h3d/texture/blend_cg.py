"""h3d.texture.blend_cg : 泊松融合共轭梯度求解（GPU 形态改造，P1）。

背景：roll-Jacobi 迭代形态在 GPU 上无加速（实测 0.8x，小算子内核启动
开销主导）。本模块改为**稀疏线性系统一次求解**：
    Δf = 0（Dirichlet 边界 = 已知像素）
    对修复区（repair）像素建立拉普拉斯稀疏矩阵 A，已知像素移入右端 b，
    用共轭梯度（CG）求解——迭代数远少于 Jacobi（几十次 vs 数百次），
    且 CG 天然适合 GPU（稀疏矩阵-向量乘是单一大算子）。

性能目标：4K（4096²）稀疏纹理 < 1s（CPU CG）+ GPU CG 进一步加速。
"""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from h3d.core import get_logger

log = get_logger("texture.blend_cg")


def poisson_blend_cg(image: np.ndarray, mask: np.ndarray,
                     tol: float = 1e-5, max_iter: int = 200,
                     dilate: int = 2) -> np.ndarray:
    """共轭梯度泊松融合：构建稀疏拉普拉斯系统一次求解。

    参数
    ----
    image:  (H,W,3) 纹理图。
    mask:   (H,W) bool 有效像素（Dirichlet 边界）。
    tol/max_iter: CG 收敛参数。
    dilate: 修复区扩张半径。

    返回
    ----
    融合后 (H,W,3) 图像（空洞调和填充、接缝平滑）。
    """
    h, w = image.shape[:2]
    known = mask.astype(bool)
    if not known.any():
        return image
    dil = _dilate(mask, dilate)
    repair = dil & ~known
    if not repair.any():
        return image

    # repair 像素编号（1D 索引）
    idx = np.flatnonzero(repair)
    n = len(idx)
    pos = {p: i for i, p in enumerate(idx)}
    repair_flat = repair.ravel()
    rows, cols, vals = [], [], []
    for i, p in enumerate(idx):
        r, c = divmod(p, w)
        rows.append(i); cols.append(i); vals.append(4.0)  # 对角
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if not (0 <= nr < h and 0 <= nc < w):
                continue
            q = nr * w + nc
            if repair_flat[q]:
                rows.append(i); cols.append(pos[q]); vals.append(-1.0)
            # Dirichlet 已知像素在 _build_rhs 中处理
    A = sp.csr_matrix((vals, (rows, cols)), shape=(n, n))

    out = image.astype(np.float64).copy()
    idx_r, idx_c = divmod(idx, w)  # repair 像素的行/列索引

    # GPU 求解（P1）：cupy.sparse.linalg.cg——稀疏矩阵-向量乘是单一大算子，
    # GPU 化收益显著；cupy 不可用/失败自动降级 CPU 求解。
    gpu_ok = False
    try:
        import cupy as cp
        import cupyx.scipy.sparse as cpsp
        import cupyx.scipy.sparse.linalg as cpspla
        A_g = cpsp.csr_matrix(A)
        out_g = cp.asarray(out)
        for ch in range(3):
            rhs = _build_rhs(idx, h, w, image[:, :, ch], repair)
            x_g, _info = cpspla.cg(A_g, cp.asarray(rhs),
                                   atol=tol * 255, maxiter=max_iter)
            out_g[idx_r, idx_c, ch] = cp.clip(x_g, 0, 255)
        out = cp.asnumpy(out_g)
        gpu_ok = True
    except Exception as exc:  # noqa: BLE001 - GPU 失败降级 CPU
        log.warning("CG GPU 求解失败（降级 CPU）：%s", exc)
    if not gpu_ok:
        for ch in range(3):
            rhs = _build_rhs(idx, h, w, image[:, :, ch], repair)
            x, _info = spla.cg(A, rhs, atol=tol * 255, maxiter=max_iter)
            # 注意：out[:,:,ch].ravel() 对非连续切片返回副本——用 fancy 索引直接写
            out[idx_r, idx_c, ch] = np.clip(x, 0, 255)
    out[known] = image[known]
    return np.clip(out, 0, 255).astype(np.uint8)

def _build_rhs(idx, h, w, channel, repair):
    """构建右端向量：已知像素四邻域求和（Dirichlet 边界）。"""
    flat = channel.ravel()
    repair_flat = repair.ravel()
    b = np.zeros(len(idx))
    for i, p in enumerate(idx):
        r, c = divmod(p, w)
        s = 0.0
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if not (0 <= nr < h and 0 <= nc < w):
                continue
            q = nr * w + nc
            if not repair_flat[q]:
                s += flat[q]
        b[i] = s
    return b


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    out = mask.copy()
    for _ in range(radius):
        nxt = out.copy()
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nxt |= np.roll(out, (dy, dx), axis=(0, 1))
        out = nxt
    return out


__all__ = ["poisson_blend_cg"]
