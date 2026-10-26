"""h3d.core.metadata : SHA256 校验与 run_manifest 溯源元数据。

设计依据：
    《管线骨架构建分析》6.4 —— run_manifest 是「可复现 / 可审计 / 可软著溯源」的核心，
    记录输入哈希、参数快照、各阶段耗时与产物路径；
    《设计书》12.1 / 41.1 —— run_manifest 字段字典（run_id / created_at /
    config_sha256 / stages / software）；
    《教学版》12.3 —— Manifest 状态机支撑「断点续跑 + 失败隔离」。

本模块提供两类能力：
    1. 哈希工具：``sha256_file`` / ``sha256_text`` / ``sha256_bytes`` —— 输入指纹。
    2. ``RunManifest``：阶段状态登记、断点判断、序列化落盘，是编排层（M0）的真相来源。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import string
from datetime import datetime
from importlib import metadata as _importlib_metadata
from typing import Any, Optional

CHUNK_SIZE = 1024 * 1024  # 1 MiB 分块读，避免大文件整体载入内存

_STATUS_DONE = "done"
_STATUS_FAIL = "fail"
_STATUS_RUNNING = "running"


def _logger():
    """延迟取 logger（``h3d.core`` 会先导入本模块，顶层 import 易成环）。"""
    try:
        from h3d.core.logging import get_logger

        return get_logger("core.manifest")
    except Exception:  # noqa: BLE001 - 日志不可用不应影响 manifest 恢复
        return None


# ---------------------------------------------------------------------------
# 哈希工具
# ---------------------------------------------------------------------------
def sha256_file(path: str, chunk_size: int = CHUNK_SIZE) -> str:
    """计算文件 SHA256 摘要（分块流式计算，适配大点云 / 网格文件）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """计算字节串 SHA256 摘要。"""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """计算文本 SHA256 摘要（按 UTF-8 编码）。"""
    return sha256_bytes(text.encode("utf-8"))


def generate_run_id(prefix: str = "run", now: Optional[datetime] = None) -> str:
    """生成运行唯一标识，形如 ``20260824-rock001-a1b2``。"""
    now = now or datetime.now()
    stamp = now.strftime("%Y%m%d")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{stamp}-{prefix}-{suffix}"


def software_versions() -> dict[str, str]:
    """采集依赖版本快照（best-effort），供 manifest 的 ``software`` 字段溯源。"""
    result: dict[str, str] = {}
    for pkg in ("open3d", "numpy", "py4dgeo", "colour-science", "PyYAML", "h3d"):
        try:
            result[pkg] = _importlib_metadata.version(pkg)
        except _importlib_metadata.PackageNotFoundError:
            continue
    return result


# ---------------------------------------------------------------------------
# RunManifest 溯源元数据
# ---------------------------------------------------------------------------
class RunManifest:
    """run_manifest.json 的内存表示与持久化。

    每次运行一个实例，记录各阶段（M1~M6 / X1 / X2）的状态、产物与关键指标，
    既是断点续跑的判据，也是软著「实质 coding」的溯源证据。
    """

    def __init__(
        self,
        run_id: Optional[str] = None,
        config_sha256: str = "",
        software: Optional[dict[str, str]] = None,
        created_at: Optional[str] = None,
    ) -> None:
        self.run_id = run_id or generate_run_id()
        self.created_at = created_at or datetime.now().isoformat(timespec="seconds")
        self.config_sha256 = config_sha256
        self.software = software if software is not None else software_versions()
        self.stages: dict[str, dict[str, Any]] = {}

    # -- 状态查询 ----------------------------------------------------------
    def is_done(self, stage: str) -> bool:
        """某阶段是否已完成（断点续跑判据）。"""
        return self.stages.get(stage, {}).get("status") == _STATUS_DONE

    def status(self, stage: str) -> Optional[str]:
        """返回某阶段状态：done / fail / running / None（未开始）。"""
        return self.stages.get(stage, {}).get("status")

    # -- 状态登记 ----------------------------------------------------------
    def record(
        self,
        stage: str,
        status: str = _STATUS_DONE,
        outputs: Optional[list[str]] = None,
        elapsed_s: Optional[float] = None,
        **metrics: Any,
    ) -> None:
        """登记阶段结果：状态、产物路径、耗时与任意关键指标（如 rmse_mm）。

        设计书 40.3 约定「先记 manifest 再落盘」——编排器应先调用本方法，
        再写产物文件，保证中断可恢复。
        """
        entry: dict[str, Any] = {"status": status}
        if outputs is not None:
            entry["outputs"] = outputs
        if elapsed_s is not None:
            entry["elapsed_s"] = round(float(elapsed_s), 3)
        entry.update(metrics)
        self.stages[stage] = entry

    def record_fail(self, stage: str, reason: str,
                    elapsed_s: Optional[float] = None) -> None:
        """登记阶段失败原因（失败隔离：仅跳过该件，不中断队列）。

        ``elapsed_s`` 记录失败前已消耗的时间——报表"耗时"列若对失败任务
        恒为 0，会掩盖"在该阶段卡了很久才失败"这一关键诊断信息。
        """
        entry: dict[str, Any] = {"status": _STATUS_FAIL, "reason": reason}
        if elapsed_s is not None:
            entry["elapsed_s"] = round(float(elapsed_s), 3)
        self.stages[stage] = entry

    def record_running(self, stage: str) -> None:
        """标记阶段进入运行态（防止并发 / 重入时状态丢失）。"""
        self.stages[stage] = {"status": _STATUS_RUNNING}

    # -- 汇总 --------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        """输出运行摘要：完成 / 失败阶段计数与逐阶段状态。"""
        done = sum(1 for s in self.stages.values() if s.get("status") == _STATUS_DONE)
        failed = sum(1 for s in self.stages.values() if s.get("status") == _STATUS_FAIL)
        return {
            "run_id": self.run_id,
            "done": done,
            "failed": failed,
            "stages": {k: v.get("status") for k, v in self.stages.items()},
        }

    # -- 序列化 ------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "config_sha256": self.config_sha256,
            "stages": self.stages,
            "software": self.software,
        }

    def save(self, path: str) -> None:
        """落盘为 run_manifest.json（UTF-8、缩进 2、不转义中文）。

        **必须原子写**（临时文件 + ``os.replace``）。manifest 是断点续跑的
        唯一真相来源，而这条管线随时可能被硬杀：超时 ``taskkill /F /T``、
        取消运行、断电、磁盘满。就地 ``open(path, "w")`` 会先把目标清空再写，
        中途死掉就留下一个**截断的 JSON** —— 之后每次启动都按损坏文件解析，
        项目再也跑不起来（实测巡检 2026-10-23：截断后 ``json.load`` 抛
        ``JSONDecodeError``，GUI 只弹崩溃框，用户无从恢复）。
        """
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        tmp = f"{path}.tmp{os.getpid()}"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)  # 同盘替换是原子的，读者要么见旧版要么见新版
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    @classmethod
    def load(cls, path: str) -> "RunManifest":
        """从已有 run_manifest.json 恢复（断点续跑入口）。

        文件损坏（截断 / 非 UTF-8 / 结构不是对象）时**不抛异常**：留存一份
        ``<name>.corrupt-<时间戳>`` 备查，记 WARNING，返回空 manifest 让本次
        从头跑。理由：manifest 是可重建的派生状态，而"项目从此打不开"是
        不可接受的失败模式 —— 宁可多跑一遍，也不能让人去手工删文件。
        """
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return cls._recover(path, f"内容无法解析（{exc}）")
        except OSError as exc:
            return cls._recover(path, f"无法读取（{exc}）")

        if not isinstance(data, dict):
            return cls._recover(path, f"顶层不是对象（{type(data).__name__}）")

        manifest = cls(
            run_id=data.get("run_id"),
            config_sha256=data.get("config_sha256", ""),
            software=data.get("software"),
            created_at=data.get("created_at"),
        )
        stages = data.get("stages") or {}
        if not isinstance(stages, dict):
            # 早年/手改过的 manifest 可能把 stages 写成 list。若原样保留，
            # 后续 .get 会在很远的地方抛 AttributeError —— 就地纠正并留痕。
            log = _logger()
            if log:
                log.warning("manifest 的 stages 不是字典（%s），已按空表处理：%s",
                            type(stages).__name__, path)
            stages = {}
        manifest.stages = {
            str(k): v for k, v in stages.items() if isinstance(v, dict)
        }
        return manifest

    @classmethod
    def _recover(cls, path: str, why: str) -> "RunManifest":
        """损坏 manifest 的恢复路径：备份留证 + WARNING + 空实例。"""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = f"{path}.corrupt-{stamp}"
        try:
            shutil.copy2(path, backup)
            kept = backup
        except OSError:
            kept = "(备份失败)"
        log = _logger()
        if log:
            log.warning("run_manifest 损坏：%s；%s。已备份为 %s，本次按空 manifest 重跑"
                        "（已完成的阶段需要重做）", why, path, kept)
        return cls()

    @classmethod
    def load_or_create(cls, path: str, **kwargs: Any) -> "RunManifest":
        """文件存在则加载，否则新建实例（不立即落盘，由调用方决定保存时机）。"""
        import os

        if os.path.exists(path):
            return cls.load(path)
        return cls(**kwargs)


__all__ = [
    "sha256_file",
    "sha256_bytes",
    "sha256_text",
    "generate_run_id",
    "software_versions",
    "RunManifest",
]
