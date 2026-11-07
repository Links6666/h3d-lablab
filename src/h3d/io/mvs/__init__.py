"""``h3d.io.mvs`` : 稠密重建后端的适配层（L1）。

业务层通过 :func:`get_backend` 拿到后端，调 :meth:`MvsBackend.plan` 得到一份
:class:`MvsPlan`，然后只负责执行——不再知道 COLMAP 有哪些命令行参数。

典型用法::

    from h3d.io.mvs import MvsRequest, get_backend

    backend = get_backend("colmap_patchmatch", exe="/path/to/colmap")
    plan = backend.plan(MvsRequest(photo_dir=..., sparse_model=...,
                                   workspace=..., exe="/path/to/colmap",
                                   options={"geom_consistency": True}))
    for step, budget in zip(plan.steps, plan.budgets(3600)):
        run_external(list(step.argv), timeout=budget)
    # 产物在 plan.fused_path

现有后端：
    ``colmap_patchmatch`` —— COLMAP ``patch_match_stereo`` + ``stereo_fusion``。
"""

from .base import MvsBackend, MvsPlan, MvsRequest, MvsStep
from .colmap_pm import (GEOMETRIC_SUFFIX, PHOTOMETRIC_SUFFIX, ColmapPatchMatch,
                        count_depth_maps)

__all__ = [
    "MvsBackend", "MvsPlan", "MvsRequest", "MvsStep",
    "ColmapPatchMatch", "count_depth_maps",
    "GEOMETRIC_SUFFIX", "PHOTOMETRIC_SUFFIX",
    "get_backend", "backend_info", "BACKENDS",
]

#: 名字 → 后端类。新增后端只在这里挂一行，业务层不动。
BACKENDS = {"colmap_patchmatch": ColmapPatchMatch}


def backend_info(name: str = "colmap_patchmatch", **kwargs) -> dict:
    """后端的「身份证」：``{"backend_id", "backend_version"}``。

    P0-3 用它写两处：``CanonicalAsset.params``（给人查）与 ``content_key`` 的
    params（给机器判能不能复用）。**只写 id 不写 version 等于没写**——同一后端
    的不同版本「补点」方式不同，产生的偏差是系统性偏置。

    两个字段必须成对出现，所以只留这一个入口。
    """
    backend = get_backend(name, **kwargs)
    return {"backend_id": backend.backend_id,
            "backend_version": backend.backend_version}


def backend_info(name: str = "colmap_patchmatch", **kwargs) -> dict:
    """后端的「身份证」：``{"backend_id", "backend_version"}``。

    P0-3 用它写两处：``CanonicalAsset.params``（给人查）与 ``content_key`` 的
    params（给机器判能不能复用）。**只写 id 不写 version 等于没写**——同一后端
    的不同版本「补点」方式不同，产生的偏差是系统性偏置。

    两个字段必须成对出现，所以只留这一个入口。
    """
    backend = get_backend(name, **kwargs)
    return {"backend_id": backend.backend_id,
            "backend_version": backend.backend_version}


def get_backend(name: str, **kwargs) -> MvsBackend:
    """按名字取后端实例。

    未知名字**立刻抛**而不是回退到默认：静默回退会让「配置写错」表现为
    「用了别的后端跑出别的点云」，事后极难查（本项目对静默降级的一贯态度）。
    """
    try:
        cls = BACKENDS[name]
    except KeyError:
        raise ValueError(
            f"未知的 MVS 后端：{name!r}；可用：{', '.join(sorted(BACKENDS))}"
        ) from None
    return cls(**kwargs)
