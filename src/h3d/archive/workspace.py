"""h3d.archive.workspace : 「打开项目」时的建档入口 + 界面要的时相行。

为什么需要这一层
----------------
切片 5 让 runner 在**已建档**的项目上登记产物，但建档只有 ``h3d archive
migrate`` 一条路 —— GUI 用户永远走不到，于是「档案登记」在界面上等于不存在。
本模块把建档接到「打开项目」这个动作上，同时给界面提供一份与 Qt 无关的
时相行数据，这样展示逻辑可以脱离 GUI 单测。

三条纪律（对应 ADR-0001）
------------------------
1. **闸门**：只对 ``looks_like_h3d_project`` 认可的目录建档。``output`` /
   ``report`` 这类通用名不算证据（2026-10-26 把仓库 ``output/`` 整棵搬走的事故）。
2. **幂等**：GUI 每次打开项目都会调一次 ``ensure_archive``。已有档案一律
   ``existing``，一个字节都不重写。
3. **允许空时相**：新建项目在第一次运行前只有采集数据。若此时不建时相，
   runner 的登记会因为「无基准期」永久跳过，跑完也补不上。

不做什么
--------
不搬迁、不改写 ``阶段成果/`` 里的任何产物，也不碰交付目录 —— 建档只是新增
``archive/`` 这一棵子树。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from ..core import paths as _paths
from .migrate import migrate
from .store import ARCHIVE_DIR, Archive

__all__ = ["ArchiveStatus", "EpochRow", "ensure_archive", "epoch_rows",
           "read_status"]

log = logging.getLogger(__name__)

#: 建档结果的三态 + 失败态。界面按这个分派文案与颜色。
STATE_CREATED = "created"
STATE_EXISTING = "existing"
STATE_SKIPPED = "skipped"
STATE_FAILED = "failed"

#: 路线代号 → 中文说明。与 ``models.ROUTES`` 一一对应，新增路线必须同步。
_ROUTE_CN = {
    "A": "A 扫描件锚定",
    "B": "B 纯照片重建",
}


@dataclass(frozen=True)
class EpochRow:
    """时相表格的一行（纯数据，不含任何 Qt 类型）。

    ``versions`` 是**该时相已登记的产物版本数**，不是「跑了多少次」：
    同一份数据重跑会因内容哈希命中而不新增版本，这是设计而非缺陷。
    """

    epoch_id: str
    captured_on: str
    route: str
    is_baseline: bool
    versions: int
    latest_version: int = 0
    scale_kind: str = "unknown"
    qc_status: str = "unknown"
    device: str = ""

    def route_text(self) -> str:
        return _ROUTE_CN.get(self.route, self.route)

    def baseline_text(self) -> str:
        return "基准期" if self.is_baseline else ""

    def versions_text(self) -> str:
        if self.versions <= 0:
            return "尚无产物"
        if self.versions == 1:
            return f"v{self.latest_version}"
        return f"{self.versions} 版（最新 v{self.latest_version}）"

    def scale_text(self) -> str:
        """尺度来源。``unknown`` 必须显示出来 —— 迁移推断的路线不代表有绝对尺度。"""
        return {"unknown": "未记录", "": "未记录"}.get(self.scale_kind,
                                                       self.scale_kind)


@dataclass(frozen=True)
class ArchiveStatus:
    """一次「打开项目」的建档结果，界面直接用。"""

    state: str
    root: str
    archive_dir: str
    artifact_id: str = ""
    title: str = ""
    rows: tuple[EpochRow, ...] = field(default_factory=tuple)
    message: str = ""

    def is_open(self) -> bool:
        """档案是否真的可用（建好了 / 本来就有）。"""
        return self.state in (STATE_CREATED, STATE_EXISTING)

    def epoch_count(self) -> int:
        return len(self.rows)

    def total_versions(self) -> int:
        return sum(r.versions for r in self.rows)


# ---------------------------------------------------------------- 只读装配
def epoch_rows(arch: Archive) -> list[EpochRow]:
    """把档案里的时相 + 产物版本摊平成界面行（按采集日期升序）。"""
    rows: list[EpochRow] = []
    for ep in arch.epochs():
        assets = arch.assets(ep.epoch_id)
        scale = ep.scale_ref if isinstance(ep.scale_ref, dict) else {}
        rows.append(EpochRow(
            epoch_id=ep.epoch_id,
            captured_on=ep.captured_on or "—",
            route=ep.route,
            is_baseline=bool(ep.is_baseline),
            versions=len(assets),
            latest_version=assets[-1].version if assets else 0,
            scale_kind=str(scale.get("kind") or "unknown"),
            qc_status=str(ep.qc_status or "unknown"),
            device=str(ep.device or ""),
        ))
    return rows


def _status(proj: str, state: str, message: str) -> ArchiveStatus:
    """按现有档案文件装配状态；档案读不出来时降级为 ``failed``。"""
    arch_dir = os.path.join(proj, ARCHIVE_DIR)
    try:
        arch = Archive.open(proj)
    except FileNotFoundError:
        return ArchiveStatus(state=state, root=proj, archive_dir=arch_dir,
                             message=message)
    except (OSError, ValueError) as exc:
        log.warning("档案读取失败：%s（%s）", proj, exc)
        return ArchiveStatus(state=STATE_FAILED, root=proj,
                             archive_dir=arch_dir,
                             message=f"档案读取失败：{exc}")
    art = arch.artifact()
    return ArchiveStatus(state=state, root=proj, archive_dir=arch_dir,
                         artifact_id=art.artifact_id, title=art.title,
                         rows=tuple(epoch_rows(arch)), message=message)


def read_status(root: str) -> ArchiveStatus | None:
    """只读查询：已有档案就返回状态，没有则 ``None``。**绝不建档**。

    界面刷新（例如跑完一批后想更新版本数）走这里，避免「刷新一下就把档案建了」
    这种隐式副作用。
    """
    proj = _paths.project_root_of(os.path.abspath(root))
    if not os.path.isfile(os.path.join(proj, ARCHIVE_DIR, "artifact.json")):
        return None
    return _status(proj, STATE_EXISTING, "档案已存在")


# ---------------------------------------------------------------- 建档入口
def ensure_archive(root: str, *, title: str = "",
                   epoch_id: str = "E1") -> ArchiveStatus:
    """打开项目时调用：能建就建，已有就复用，不该建就明确跳过。

    返回的状态永远可用（不抛）—— 建档是增量能力，不能反过来让「项目打不开」。
    """
    proj = _paths.project_root_of(os.path.abspath(root))
    if not os.path.isdir(proj):
        return ArchiveStatus(state=STATE_SKIPPED, root=proj,
                             archive_dir=os.path.join(proj, ARCHIVE_DIR),
                             message="目录不存在，未建档")
    if not _paths.looks_like_h3d_project(proj):
        return ArchiveStatus(
            state=STATE_SKIPPED, root=proj,
            archive_dir=os.path.join(proj, ARCHIVE_DIR),
            message="这不是一个 H3D 项目（缺少 config.yaml 或阶段目录），未建档")

    if os.path.isfile(os.path.join(proj, ARCHIVE_DIR, "artifact.json")):
        st = _status(proj, STATE_EXISTING, "档案已存在")
        log.debug("档案已存在：%s（%d 个时相）", proj, st.epoch_count())
        return st

    try:
        res = migrate(proj, epoch_id=epoch_id, title=title,
                      allow_no_asset=True)
    except (ValueError, OSError) as exc:
        log.debug("未建档：%s（%s）", proj, exc)
        return ArchiveStatus(state=STATE_SKIPPED, root=proj,
                             archive_dir=os.path.join(proj, ARCHIVE_DIR),
                             message=f"未建档：{exc}")

    ver = res.get("version")
    if ver:
        msg = f"已建档：{res['artifact_id']} / {res['epoch_id']} / 产物 v{ver}"
    else:
        msg = (f"已建档：{res['artifact_id']} / {res['epoch_id']}"
               "（尚无产物，跑完自动登记）")
    st = _status(proj, STATE_CREATED, msg)
    if st.state == STATE_FAILED:
        return st
    log.info("%s", msg)
    return st
