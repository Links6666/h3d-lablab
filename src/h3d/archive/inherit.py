"""h3d.archive.inherit : 跨时相相机位姿变换与图集继承（B1）。

问题
----
基准期与复测期各自有自己的世界坐标系。要「把复测期影像烘到基准图集」，
必须先把复测期的相机搬到基准坐标系里 —— 然后复用同一个逐纹素采样器
（``project_photos_to_texture``）把照片投到基准网格的 UV 上。

**UV 不重新展开**：继承的全部意义就在于 texel 一一对应，重展开即失效。

相机约定（这一段错了不会报错，只会整片错位）
--------------------------------------------
``capture_meta.json`` 里每台相机是 ``{"R": world→cam, "t": 相机中心}``，
投影写法是 ``pc = (X - t) @ R.T``，可见判定用 ``pc.z < 0``（朝 -Z）。
注意 ``t`` **不是** OpenCV 的 tvec（那个是 ``-R @ C``）。本项目在 S1 里做过
一次这个换算，错用会导致 ``projected_cameras=0``、纹理全空。

推导
----
设复测世界 → 基准世界的相似变换为::

    X_base = s·R·X_rec + t          （R 正交，det=+1，s>0）

相机在其自坐标系里的观测为::

    pc = R_cam·(X_rec - C_rec)
       = R_cam·(Rᵀ(X_base - t)/s - C_rec)
       = (R_cam·Rᵀ/s)·(X_base - (s·R·C_rec + t))

于是::

    C_base = s·R·C_rec + t
    R_base = R_cam·Rᵀ / s

``R_base`` 含 ``1/s`` 因子，**不是正交矩阵**。这是刻意的：投影只看
``x/z``、``y/z`` 的比值与符号，整体乘一个正的常数不改变任何像素坐标。
若把 ``1/s`` 去掉，等于把相机视野整体缩放 1/s 倍，投影像素全错。
:func:`transform_cameras` 的返回值只应交给投影使用，不要拿它当旋转矩阵
做 det/正交性判断；``s == 1`` 时它自然退化为正交阵（有测试守着）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import numpy as np

__all__ = ["Similarity", "transform_cameras", "inherit_texture"]

#: 判定「均匀缩放」的相对容差。各轴缩放不同就不是相似变换（会拉伸几何）。
_UNIFORM_TOL = 1e-6

#: det(R) 与 +1 的容差。det=-1 是镜像，会让整件几何翻面、法线朝向全反。
_DET_TOL = 1e-6


@dataclass(frozen=True)
class Similarity:
    """相似变换 ``X' = s·R·X + t``（R 正交、det=+1、s>0）。"""

    R: np.ndarray
    t: np.ndarray
    s: float = 1.0

    def __post_init__(self) -> None:
        R = np.asarray(self.R, dtype=np.float64)
        t = np.asarray(self.t, dtype=np.float64).reshape(3)
        if R.shape != (3, 3):
            raise ValueError(f"R 必须是 3×3，收到 {R.shape}")
        if float(self.s) <= 0:
            raise ValueError(f"相似变换的尺度必须为正，收到 {self.s}")
        object.__setattr__(self, "R", R)
        object.__setattr__(self, "t", t)
        object.__setattr__(self, "s", float(self.s))

    # -- 构造 ---------------------------------------------------------------
    @classmethod
    def identity(cls) -> "Similarity":
        return cls(R=np.eye(3), t=np.zeros(3), s=1.0)

    @classmethod
    def from_rotation(cls, axis: Iterable[float], angle_rad: float,
                      translation: Iterable[float] = (0.0, 0.0, 0.0),
                      scale: float = 1.0) -> "Similarity":
        """绕轴旋转 ``angle_rad`` + 平移 + 缩放的相似变换（合成数据用）。"""
        a = np.asarray(axis, dtype=np.float64).reshape(3)
        n = float(np.linalg.norm(a))
        if n < 1e-12:
            raise ValueError("旋转轴不能是零向量")
        a = a / n
        K = np.array([[0.0, -a[2], a[1]],
                      [a[2], 0.0, -a[0]],
                      [-a[1], a[0], 0.0]])
        R = np.eye(3) + np.sin(angle_rad) * K + (1.0 - np.cos(angle_rad)) * (K @ K)
        return cls(R=R, t=translation, s=scale)

    @classmethod
    def from_matrix(cls, M: Any) -> "Similarity":
        """从 4×4 齐次矩阵构造；镜像或非均匀缩放一律拒绝。"""
        M = np.asarray(M, dtype=np.float64)
        if M.shape != (4, 4):
            raise ValueError(f"相似变换矩阵必须是 4×4，收到 {M.shape}")
        A = M[:3, :3]
        t = M[:3, 3]
        # 各列范数（= 各轴缩放）必须一致，否则不是相似变换
        norms = np.linalg.norm(A, axis=0)
        if float(norms.max() - norms.min()) > _UNIFORM_TOL * max(1.0, float(norms.max())):
            raise ValueError(f"不是相似变换（各轴缩放不一致）：{norms}")
        s = float(norms.mean())
        if s <= 0:
            raise ValueError(f"不是相似变换（缩放为 {s}）")
        R = A / s
        det = float(np.linalg.det(R))
        if det < 0:
            raise ValueError("相似变换含镜像（det<0），会让几何翻面，拒绝")
        if abs(det - 1.0) > 1e-3:
            raise ValueError(f"旋转部分不正交（det={det:.6f}）")
        if abs(float(M[3, 3]) - 1.0) > 1e-9 or np.abs(M[3, :3]).max() > 1e-9:
            raise ValueError("齐次矩阵最后一行必须是 [0,0,0,1]")
        return cls(R=R, t=t, s=s)

    # -- 运算 ---------------------------------------------------------------
    def matrix(self) -> np.ndarray:
        M = np.eye(4)
        M[:3, :3] = self.s * self.R
        M[:3, 3] = self.t
        return M

    def apply_points(self, X: Any) -> np.ndarray:
        """``X' = s·R·X + t``（X 可以是 (N,3) 或 (3,)）。"""
        arr = np.asarray(X, dtype=np.float64)
        return self.s * (arr @ self.R.T) + self.t

    def inverse(self) -> "Similarity":
        Rt = self.R.T
        return Similarity(R=Rt, t=-(Rt @ self.t) / self.s, s=1.0 / self.s)


def transform_cameras(images: Iterable[Mapping[str, Any]],
                      sim: Similarity) -> list[dict[str, Any]]:
    """把一组相机从复测坐标系搬到基准坐标系。

    返回**新列表**（不就地改入参）：调用方通常还要用原值做别的推算。
    ``R_base = R_cam·Rᵀ/s`` 含 ``1/s`` 是投影不变性所允许的，见模块文档。
    """
    out: list[dict[str, Any]] = []
    Rt = sim.R.T
    for im in images:
        R_cam = np.asarray(im["R"], dtype=np.float64)
        C = np.asarray(im["t"], dtype=np.float64).reshape(3)
        moved = dict(im)
        moved["t"] = sim.s * (sim.R @ C) + sim.t
        moved["R"] = (R_cam @ Rt) / sim.s
        out.append(moved)
    return out


def inherit_texture(mesh, uv, faces, images: Iterable[Mapping[str, Any]],
                    cameras: Mapping[Any, Any], photo_dir: str, sim: Similarity,
                    *, size: int = 2048, top_k: int = 3,
                    occlusion: bool = True, atlas_version: str | None = None,
                    base_epoch: str | None = None,
                    source_epoch: str | None = None):
    """把复测期影像重投影到**基准图集**，返回 ``(image, metrics)``。

    三件事按顺序做，缺一不可：

    1. ``transform_cameras(images, sim)``：复测相机 → 基准坐标系；
    2. ``project_photos_to_texture(mesh, uv, faces, ...)``：**直接用基准期的
       mesh / uv / faces**，只把相机与照片换成复测期的。UV 不重新展开 ——
       重新展开就等于放弃了 texel 一一对应，继承的全部意义都在这里；
    3. metrics 打标：``inherited`` / ``atlas_version`` / 基准与来源时相 /
       位姿变换快照。报表必须能区分「本期独立烘焙」与「继承基准图集」。

    失败不兜底：投影为空（例如变换错到相机全部背对几何）由底层抛
    ``ValueError``，让调用方看到失败，而不是交出一张全黑图集。
    """
    from ..photo_texture import project_photos_to_texture

    moved = transform_cameras(images, sim)
    image, metrics = project_photos_to_texture(
        mesh, uv, faces, moved, cameras, photo_dir,
        size=size, top_k=top_k, occlusion=occlusion)
    metrics.update({
        "inherited": True,
        "atlas_version": atlas_version or "",
        "atlas_base_epoch": base_epoch or "",
        "atlas_source_epoch": source_epoch or "",
        "pose_transform": {
            "R": sim.R.round(9).tolist(),
            "t": sim.t.round(9).tolist(),
            "s": round(sim.s, 9),
        },
    })
    return image, metrics
