"""h3d.runner.fingerprint : 阶段输入内容指纹（断点续跑的真正判据）。

为什么需要
----------
改造前续跑判据是 ``manifest.is_done(key) and 产物还在``。它**只看状态，不看输入**，
于是出现一类静默错误：

    换了照片 / 改了 ``poisson_depth`` / 换了配准阈值 → manifest 里那条记录仍然是
    ``done``、产物文件也还在 → 该阶段被判定「已完成」直接跳过，用户拿到的仍是
    上一组参数的结果，而报表、耗时、日志全都正常。

唯一的逃生口是手动开 ``runner.force_rerun``，但它是布尔、整批生效：为了重算一件，
其余九件一起重跑（M4 单件约 80 秒，十件就是十几分钟）。

本模块给每个阶段计算一个**输入内容指纹**：外部输入（原始几何、照片）与上游阶段
产物的内容哈希，加上本阶段的参数快照，合成一个 64 位十六进制键。续跑时比对
「manifest 里记录的键」与「当下算出的键」：

* 相同 → 跳过（真的没变，省下算力）；
* 不同 → 重跑，并在日志里写明为什么（不再是静默跳过）。

键的构成
--------
::

    key = sha256({
        "schema": FINGERPRINT_SCHEMA,
        "inputs": {"raw/0": <digest>, "photos/3": <digest>, "up:M1": <阶段键>…},
        "params": {"schema": …, "stage": "M3", "config": {<参与指纹的配置段>}},
    })

三个刻意的设计选择：

1. **配置用「减」不用「加」**：只剔除 ``project`` / ``runner`` 两个执行控制段，
   其余全部参与。反向的「白名单式只取认识的段」会在新增配置段时漏算 ——
   那正是 r16「修了但没效果」的同构错误。漏算的代价是重跑一次，漏判的代价是
   悄悄给出旧结果。
2. **上游键逐级累积**：M4 的键里含 M1→M2→M3 的全部键，于是任何一级上游变化
   都会自动传导到下游，不必逐个阶段声明依赖。
3. **参数不做四舍五入**：``0.1000001`` 与 ``0.1`` 必须是两个键。宁可多算一次，
   也不能把「改了参数」判成「没改」。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping

from h3d.archive import content_key

__all__ = [
    "FINGERPRINT_SCHEMA", "NON_CONTENT_SECTIONS",
    "RESUME_OK", "RESUME_LEGACY", "RESUME_NO_RECORD", "RESUME_NOT_DONE",
    "RESUME_MISSING", "RESUME_CHANGED",
    "clear_digest_cache", "content_params", "digest_path", "external_inputs",
    "is_skippable", "resume_decision", "resume_reason_text", "stage_input_key",
]

log = logging.getLogger(__name__)

#: 指纹口径版本。键的**结构或语义**一变就必须提升，否则新旧键会在同一个
#: manifest 里互相比较，得出「没变」的错误结论。
FINGERPRINT_SCHEMA = 1

#: 不参与指纹的配置段：纯执行控制，与产物内容无关。
#:
#: * ``project``：名称、操作员、数据目录、扩展名 —— 决定「去哪找数据」，
#:   而数据本身已经由 :func:`external_inputs` 按内容哈希进入键。
#: * ``runner``：并发数、续跑开关、manifest 路径、失败隔离 —— 决定「怎么跑」。
#:
#: 改 ``workers`` 让整批重算 80 秒的 M4 是纯粹的浪费；而这两个段里没有任何
#: 影响几何/纹理结果的计算参数（``x1_enabled`` 之类只决定阶段是否执行，
#: 不执行时 manifest 记的是 disabled/skipped，根本走不到这里的 done 判定）。
NON_CONTENT_SECTIONS = frozenset({"project", "runner"})

# -- 续跑判定码 -------------------------------------------------------------
#: 输入未变化 → 跳过（这是理想路径）
RESUME_OK = "ok"
#: 记录存在但没有指纹（1.1.13 及以前的 manifest）→ 按旧规则跳过
RESUME_LEGACY = "legacy"
#: 没有该阶段记录 → 必须执行
RESUME_NO_RECORD = "no_record"
#: 有记录但状态不是 done → 必须执行
RESUME_NOT_DONE = "not_done"
#: 记录说 done 但产物文件已不在 → 必须执行
RESUME_MISSING = "missing_outputs"
#: 输入指纹变了（换数据、改参数、上游重算）→ 必须执行
RESUME_CHANGED = "input_changed"

_SKIPPABLE = frozenset({RESUME_OK, RESUME_LEGACY})

#: 判定码 → 给人看的原因（日志用）。措辞要能区分「没变所以省了」与「变了所以重跑」，
#: 否则用户看到一行「跳过」无从判断是缓存命中还是又踩了静默跳过。
_REASON_TEXT = {
    RESUME_OK: "输入未变化",
    RESUME_LEGACY: "该记录无输入指纹（旧版 manifest，按旧规则续跑；"
                   "要让后续按内容判定，需对这件强制重算一次以建立指纹）",
    RESUME_NO_RECORD: "无已完成记录",
    RESUME_NOT_DONE: "上次未完成",
    RESUME_MISSING: "产物缺失",
    RESUME_CHANGED: "输入已变化（数据/参数/上游产物）",
}


# ---------------------------------------------------------------------------
# 文件摘要（进程内缓存）
# ---------------------------------------------------------------------------
#: 摘要缓存：``{(abspath, size, mtime_ns): sha256}``。
#:
#: 为什么要缓存：一件 30 张 4.5 MP 的照片集约 150 MB，十件批量就是 1.5 GB；
#: 而「跳过判定」本身是为了省时间，不能反过来花几秒钟哈希。缓存只在本进程内
#: 有效（跨进程复用收益抵不过失效正确性的风险）。
#:
#: 已知限制：内容变了但 size 与 mtime 都没变（极小概率，需要工具刻意保留时间戳）
#: 会命中旧摘要。可接受 —— 那种情况下连 ``is_done`` 的产物校验也一起被骗。
_DIGEST_CACHE: dict[tuple[str, int, int], str] = {}


def clear_digest_cache() -> None:
    """清空摘要缓存（测试用；也供「明知文件被外部改写」的调用方使用）。"""
    _DIGEST_CACHE.clear()


def digest_path(path: str) -> str:
    """文件内容 sha256（带缓存）。文件不存在时抛 :class:`FileNotFoundError`。

    不复用 ``archive.file_digest`` 是为了加缓存 —— 那个函数每次真读盘，
    在「每件 × 每阶段」的判定里会变成可观的固定开销。
    """
    ap = os.path.abspath(path)
    try:
        st = os.stat(ap)
    except OSError as exc:
        raise FileNotFoundError(path) from exc
    if not os.path.isfile(ap):
        raise FileNotFoundError(path)
    ck = (ap, int(st.st_size), int(st.st_mtime_ns))
    hit = _DIGEST_CACHE.get(ck)
    if hit is not None:
        return hit

    import hashlib

    h = hashlib.sha256()
    with open(ap, "rb") as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    digest = h.hexdigest()
    _DIGEST_CACHE[ck] = digest
    return digest


# ---------------------------------------------------------------------------
# 配置快照
# ---------------------------------------------------------------------------
def content_params(cfg_dict: Mapping[str, Any]) -> dict[str, Any]:
    """取出参与指纹的配置段（全量减去 :data:`NON_CONTENT_SECTIONS`）。"""
    return {k: v for k, v in (cfg_dict or {}).items()
            if k not in NON_CONTENT_SECTIONS}


# ---------------------------------------------------------------------------
# 外部输入
# ---------------------------------------------------------------------------
def external_inputs(task) -> dict[str, str]:
    """本件的**外部**输入内容指纹：原始几何 + 照片。

    只列「进入管线的数据」，不含任何阶段产物 —— 产物通过上游阶段键传递。
    文件在发现之后被删掉时不炸整条管线（那是用户环境问题），但必须留 WARNING：
    静默把「文件没了」算成「输入没变」会让下游拿旧产物当真。
    """
    out: dict[str, str] = {}
    missing: list[str] = []
    for i, p in enumerate(sorted(task.raw_paths or [])):
        try:
            out[f"raw/{i}"] = digest_path(p)
        except FileNotFoundError:
            missing.append(p)
    for i, p in enumerate(sorted(task.photo_paths or [])):
        try:
            out[f"photos/{i}"] = digest_path(p)
        except FileNotFoundError:
            missing.append(p)
    if missing:
        log.warning("%s：%d 个输入文件不存在，未计入指纹（%s）",
                    task.item_id, len(missing),
                    "、".join(os.path.basename(m) for m in missing[:3]))
    return out


# ---------------------------------------------------------------------------
# 阶段输入键
# ---------------------------------------------------------------------------
def stage_input_key(stage_id: str, task, cfg_dict: Mapping[str, Any],
                    upstream: Mapping[str, str]) -> str:
    """某阶段的输入内容键。

    参数
    ----
    stage_id:  阶段代号（``M1`` … ``X2``）。
    task:      :class:`~h3d.runner.task.Task`。
    cfg_dict:  本次运行的**生效**配置（已含尺度缩放），``cfg.to_dict()`` 的结果。
    upstream:  {阶段代号: 该阶段的输入键}，覆盖本阶段之前的所有阶段。
               逐级累积，任一上游变化都会传导下来。
    """
    inputs = dict(external_inputs(task))
    for sid in sorted(upstream):
        inputs[f"up:{sid}"] = str(upstream[sid])
    params = {
        "schema": FINGERPRINT_SCHEMA,
        "stage": stage_id,
        "config": content_params(cfg_dict),
    }
    return content_key(inputs, params)


# ---------------------------------------------------------------------------
# 续跑判定
# ---------------------------------------------------------------------------
def resume_decision(entry: Mapping[str, Any] | None, current_key: str,
                    outputs_exist: bool) -> str:
    """决定某阶段能否跳过，返回判定码（见 ``RESUME_*``）。

    ``current_key`` 为空表示指纹算不出来（磁盘异常等）—— 此时**一律判为变化**。
    把「算不出来」当成「没变」，等价于关掉整个机制却仍然显示「已跳过」。
    """
    if not entry:
        return RESUME_NO_RECORD
    if entry.get("status") != "done":
        return RESUME_NOT_DONE
    if not outputs_exist:
        return RESUME_MISSING
    recorded = str(entry.get("input_key") or "")
    if not recorded:
        return RESUME_LEGACY
    if not current_key or recorded != current_key:
        return RESUME_CHANGED
    return RESUME_OK


def is_skippable(code: str) -> bool:
    """该判定码是否意味着「可以跳过」。只有输入真的没变（或旧记录兼容）才允许。"""
    return code in _SKIPPABLE


def resume_reason_text(code: str) -> str:
    """判定码 → 给人看的一句原因。"""
    return _REASON_TEXT.get(code, code)
