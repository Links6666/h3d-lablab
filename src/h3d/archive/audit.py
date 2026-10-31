"""h3d.archive.audit : 资产里的图集引用体检与修复。

为什么需要这一层
----------------
``CanonicalAsset.uv_atlas_ref`` 是**引用**，它只有在档案里查得到对应
``archive/atlas/<ref>.json`` 时才有意义。而 B1 切片 7 之前，``migrate`` 与
runner 都会往这里写死 ``"atlas-v1"``——实体从来不存在。当时没人发现，是因为
没有任何代码去核对这条引用：报表照常出、耗时照常记、日志照常干净。

这就是 r16「``manifest_metrics`` 手写白名单」的同构缺陷：**引用没有实体**。
修掉写入口只对新建档生效，存量档案里那批假引用仍然在，所以需要一次体检 +
一次显式修复。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .store import Archive

__all__ = ["RefFinding", "STATE_DANGLING", "STATE_EMPTY", "STATE_EXTERNAL",
           "STATE_OK", "STATE_UNVERIFIED", "audit_atlas_refs"]

log = logging.getLogger(__name__)

#: 档案里查不到该图集记录 —— 引用指向空气，必须修复。
STATE_DANGLING = "dangling"
#: 记录存在，且登记时间早于资产创建 —— 写引用那一刻实体就在。
STATE_OK = "ok"
#: 无引用（迁移后新建档的正常态，不是缺陷）。
STATE_EMPTY = "empty"
#: 跨项目限定名 ``<artifact>@atlas-vN``：图集在**基准项目**里，本档案无从判定。
STATE_EXTERNAL = "external"
#: 记录晚于资产创建：现在查得到，但写引用那一刻实体还不存在（时间倒挂）。
STATE_UNVERIFIED = "unverified"


@dataclass(frozen=True)
class RefFinding:
    """一条资产引用的体检结论。"""

    epoch_id: str
    version: int
    ref: str
    state: str
    detail: str


def audit_atlas_refs(arch: Archive) -> list[RefFinding]:
    """逐条核对资产的 ``uv_atlas_ref``，返回体检结论（按登记顺序）。

    每条资产都落一行 —— 「0 行」既可能是「全部合格」，也可能是「一条都没查」，
    体检表不能让自己有这种歧义。
    """
    from .atlas import AtlasRegistry

    registry = AtlasRegistry(arch)
    out: list[RefFinding] = []
    for epoch in arch.epochs():
        for asset in arch.assets(epoch.epoch_id):
            ref = (asset.uv_atlas_ref or "").strip()
            out.append(_judge(registry, asset, ref))
    return out


def _judge(registry, asset, ref: str) -> RefFinding:
    """单条引用的判据。只认档案里的实体，不认「长得像版本号」。"""
    def finding(state: str, detail: str) -> RefFinding:
        return RefFinding(epoch_id=asset.epoch_id, version=asset.version,
                          ref=ref, state=state, detail=detail)

    if not ref:
        return finding(STATE_EMPTY, "未引用图集（迁移后新建档的正常态）")
    if "@" in ref:
        artifact, _, ver = ref.partition("@")
        return finding(
            STATE_EXTERNAL,
            f"跨项目引用 {artifact} 的 {ver}：记录在基准项目里，本档案无从判定")

    record = registry.get(ref)
    if record is None:
        return finding(
            STATE_DANGLING,
            f"档案里没有 {ref} 的图集记录（archive/atlas/{ref}.json）")
    frozen_at = (record.frozen_at or "").strip()
    created_at = (asset.created_at or "").strip()
    if not frozen_at or not created_at:
        return finding(
            STATE_OK,
            f"记录存在（冻结于 {frozen_at or '未记录'}，资产创建于 "
            f"{created_at or '未记录'}），两者缺时间戳，时间先后不可判")
    if frozen_at > created_at:
        return finding(
            STATE_UNVERIFIED,
            f"图集记录冻结于 {frozen_at}，晚于资产创建时间 {created_at}："
            f"写引用那一刻 {ref} 还不存在，无法证明该纹理由本版图集烘出")
    return finding(STATE_OK, f"记录存在（冻结于 {frozen_at}）")


@dataclass(frozen=True)
class RepairReport:
    """一次修复的全部结论（``repaired`` 在 ``dry_run`` 下也只列出「打算改谁」）。"""

    findings: list[RefFinding]
    repaired: list[RefFinding]
    dry_run: bool = False

    @property
    def problems(self) -> list[RefFinding]:
        """真正需要动作的那些（目前只有 dangling）。"""
        return [f for f in self.findings if f.state == STATE_DANGLING]


def repair_atlas_refs(arch: Archive, *, dry_run: bool = False) -> RepairReport:
    """把「查无实体」的图集引用置空，并在 ``params`` 里留痕。

    只处理 :data:`STATE_DANGLING`：

    * ``STATE_UNVERIFIED``（记录晚于资产）**不修** —— 引用现在确实指得到实体，
      清掉反而低估；这条痕迹留在报告里，供人判断要不要重跑。
    * ``STATE_EXTERNAL``（跨项目限定名）**不修** —— 实体在基准项目里，本档案
      没有资格判定。

    置空而不是「按现有记录回填」：能证明才算回填，而存量资产既没有自己的
    ``_uv.npz`` 指纹，纹理文件也可能已被后来的重跑覆盖 —— 回填出来的「正确」
    是现在对、当时不对。
    """
    findings = audit_atlas_refs(arch)
    done: list[RefFinding] = []
    for f in findings:
        if f.state != STATE_DANGLING:
            continue
        done.append(f)
        if dry_run:
            continue
        asset = _asset_of(arch, f.epoch_id, f.version)
        if asset is None:                       # 检查完到修复之间被删了
            log.warning("资产 %s v%d 在体检后消失，跳过修复",
                        f.epoch_id, f.version)
            continue
        import dataclasses

        new = dataclasses.replace(
            asset, uv_atlas_ref="",
            params={**asset.params,
                    "ref_repair": {"dropped": f.ref, "reason": f.state,
                                   "detail": f.detail}})
        arch.rewrite_asset(new)
        log.warning("已置空 %s v%d 的图集引用（原值 %s）：%s",
                    f.epoch_id, f.version, f.ref, f.detail)
    return RepairReport(findings=findings, repaired=done, dry_run=dry_run)


def _asset_of(arch: Archive, epoch_id: str, version: int):
    for asset in arch.assets(epoch_id):
        if asset.version == version:
            return asset
    return None
