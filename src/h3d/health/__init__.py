"""h3d.health : 启动自检（preflight）—— 软件启动与项目运行前的**第一道工序**。

为什么要有它
------------
本项目的历史故障里，有一半以上不是算法错，而是**"环境/状态已经坏了，但没人
在开跑前看一眼"**：

* 同一数据根上还挂着一个上次没退干净的 ``H3D.exe --batch`` / ``colmap.exe``
  → 两个进程互删 ``99_runs/sfm_tmp/<item>/dense``，69 分钟稠密计算作废，
  单张深度图从 45 s 退化到 145 s（2026-10-17，代价 3 小时）。
* 崩溃留下的陈旧锁 ``.h3d_run.lock`` 把用户永久挡在门外。
* ``run_manifest.json`` 写着 ``done``，产物却被手工删了 → 续跑静默跳过，
  下游 M5 报「参考点云为空」，用户完全看不出因果。
* 打包版 ``_MEIxxxx`` 解包目录被父进程提前删除 → 后台 ``--batch`` 跑到一半
  ``add_dll_directory`` FileNotFoundError（56 分钟后才炸）。
* 照片与 ``capture_meta`` 的尺寸不同源（2400 长边 vs 原图 4496）→ 贴图颜色
  全取左上角且**不报错**。

这些错误的共同点：**开跑前 300 毫秒就能查出来，跑起来之后要几小时才发现**。
本模块把这类检查收敛成两道工序：

====================================  ==============================  ==============
作用域 ``scope``                        侧重                             预算
====================================  ==============================  ==============
``boot``（软件启动）                    这台机器 + 这套程序本身能不能用    ≤ 900 ms
``project``（项目运行前）               这个项目现在能不能跑、会不会撞车    ≤ 1400 ms
====================================  ==============================  ==============

设计要点（都是硬要求，改之前先读）
----------------------------------
1. **不拖慢**：全部检查并行，单项与整体都有预算；超时的检查记 ``TIMEOUT``
   （WARNING），**绝不等待**。依赖探测只用 ``find_spec``，不 import 重模块
   （``import open3d`` 单次就要 1.2 s，一旦 import 就等于自检失败）。
2. **不误伤**：任何检查内部异常都被收敛成 WARNING 并写明异常，不允许抛出
   中断启动；查不出来 = 未知，不等于通过。
3. **不静默**：结论一定落成一行可解析日志（界面唯一输入）::

       自检完成：project · 阻断 0 · 警告 1 · 提示 2 · 318 ms

   同时 ``project`` 作用域把完整结果落盘到 ``<root>/90_meta/preflight_*.json``，
   事后能复查"当时到底查到了什么"。
4. **可修**：``fix=True`` 时自动清理**确定安全**的残留（陈旧锁、已死进程的
   ``_MEI`` 目录）。杀进程、改 manifest 这类不可逆动作只给命令、不代做。

用法
----
::

    from h3d.health import run_preflight, format_report

    rep = run_preflight("project", root=task_root, cfg=cfg)
    if not rep.ok:
        print(format_report(rep))
        raise RecoverableError(rep.blocker_text())
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from h3d.core import get_logger

log = get_logger("health")

#: 各作用域的整体预算（毫秒）。实测：boot 约 260 ms、project 约 420 ms，
#: 预算留 2~3 倍余量，正常情况不会触发 TIMEOUT。
BUDGET_MS = {"boot": 900, "project": 1400}

#: 单项检查的默认上限（毫秒）。子进程类探测另有自己的 1.5 s 超时。
DEFAULT_CHECK_MS = 700

_SEVERITY_RANK = {"ok": 0, "info": 1, "warning": 2, "blocker": 3}
_SEVERITY_CN = {"ok": "正常", "info": "提示", "warning": "警告", "blocker": "阻断"}


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    """单项检查结论。

    字段
    ----
    name:    英文标识（日志 / JSON / 测试断言用，**改它等于改契约**）。
    title:   中文标题（界面展示）。
    severity: ``ok`` / ``info`` / ``warning`` / ``blocker``。
    detail:  一句话结论（用户能看懂，含关键数字）。
    hint:    可执行的修复建议（多行用 ``\\n``）。
    fix:     本次已自动执行的动作描述（空表示没做）。
    elapsed_ms: 该项耗时。
    """

    name: str
    title: str
    severity: str = "ok"
    detail: str = ""
    hint: str = ""
    fix: str = ""
    elapsed_ms: int = 0

    @property
    def is_blocker(self) -> bool:
        return self.severity == "blocker"

    def to_dict(self) -> dict:
        return {
            "name": self.name, "title": self.title,
            "severity": self.severity, "detail": self.detail,
            "hint": self.hint, "fix": self.fix,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class HealthReport:
    """一次自检的完整结论。"""

    scope: str
    results: list[CheckResult] = field(default_factory=list)
    elapsed_ms: int = 0
    skipped: bool = False
    skip_reason: str = ""

    # -- 汇总 --------------------------------------------------------------
    def by_severity(self, severity: str) -> list[CheckResult]:
        return [r for r in self.results if r.severity == severity]

    @property
    def blockers(self) -> list[CheckResult]:
        return self.by_severity("blocker")

    @property
    def warnings(self) -> list[CheckResult]:
        return self.by_severity("warning")

    @property
    def infos(self) -> list[CheckResult]:
        return self.by_severity("info")

    @property
    def ok(self) -> bool:
        """无阻断项即视为可继续（警告不拦，但会展示）。"""
        return not self.blockers

    def blocker_text(self) -> str:
        """给异常/弹窗用的中文摘要（只列阻断项）。"""
        if not self.blockers:
            return ""
        lines = ["开跑前自检发现 %d 个必须先处理的问题：" % len(self.blockers)]
        for r in self.blockers:
            lines.append(f"  · {r.title}：{r.detail}")
            if r.hint:
                lines.extend("      " + ln for ln in r.hint.splitlines())
        return "\n".join(lines)

    def summary_line(self) -> str:
        """一行可解析日志（界面据此判定，勿改格式）。"""
        if self.skipped:
            return f"自检跳过：{self.scope}（{self.skip_reason}）"
        return ("自检完成：%s · 阻断 %d · 警告 %d · 提示 %d · %d ms"
                % (self.scope, len(self.blockers), len(self.warnings),
                   len(self.infos), self.elapsed_ms))

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "elapsed_ms": self.elapsed_ms,
            "ok": self.ok,
            "n_blocker": len(self.blockers),
            "n_warning": len(self.warnings),
            "n_info": len(self.infos),
            "results": [r.to_dict() for r in self.results],
        }


@dataclass
class Context:
    """检查函数拿到的上下文（同一次自检内共享 ``cache``）。"""

    scope: str
    root: Optional[str] = None
    cfg: Any = None
    fix: bool = False
    budget_ms: int = 900
    started: float = field(default_factory=time.monotonic)
    cache: dict = field(default_factory=dict)

    def left_ms(self) -> int:
        """本项还能用多少毫秒（整体预算的剩余）。"""
        used = (time.monotonic() - self.started) * 1000.0
        return max(0, int(self.budget_ms - used))

    def probe_timeout(self, cap: float = 1.5) -> float:
        """给子进程探测用的超时：不超过剩余预算，也不超过 ``cap``。"""
        return max(0.2, min(cap, self.left_ms() / 1000.0))


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
@dataclass
class _Spec:
    fn: Callable[[Context], CheckResult]
    scope: str
    name: str
    title: str
    order: int
    needs_cfg: bool
    needs_root: bool


_REGISTRY: list[_Spec] = []


def check(scope: str, name: str, *, title: str, order: int = 100,
          needs_cfg: bool = False, needs_root: bool = False):
    """把检查函数登记进自检表。

    参数
    ----
    scope:      ``boot`` / ``project``。
    name:       英文标识（稳定，用作日志与 JSON 键）。
    title:      中文标题。
    order:      执行/展示顺序（小的在前）。
    needs_cfg:  需要 ``cfg`` 才执行（缺配置时该项跳过，不计入结果）。
    needs_root: 需要 ``root`` 才执行。
    """
    def deco(fn: Callable[[Context], CheckResult]):
        _REGISTRY.append(_Spec(fn=fn, scope=scope, name=name, title=title,
                               order=order, needs_cfg=needs_cfg,
                               needs_root=needs_root))
        return fn
    return deco


#: 进程内"已跑过"标记：BatchProcessor 与 CLI 都可能触发 project 自检，
#: 同一进程内只跑一次（GUI 子进程是独立进程，各跑各的，符合预期）。
_RAN: dict[str, HealthReport] = {}


def already_ran(scope: str, root: Optional[str] = None) -> bool:
    """同一进程内该作用域（+数据根）是否已自检过。"""
    return _key(scope, root) in _RAN


def last_report(scope: str, root: Optional[str] = None) -> Optional[HealthReport]:
    return _RAN.get(_key(scope, root))


def reset_cache() -> None:
    """清掉进程内缓存（测试与"重新自检"按钮用）。"""
    _RAN.clear()


def _key(scope: str, root: Optional[str]) -> str:
    return scope if not root else f"{scope}:{os.path.normcase(os.path.abspath(root))}"


def _disabled() -> Optional[str]:
    """应急开关：``H3D_SKIP_PREFLIGHT=1`` 时整体跳过（返回原因）。"""
    flag = os.environ.get("H3D_SKIP_PREFLIGHT", "").strip()
    if flag and flag not in ("0", "false", "False"):
        return f"H3D_SKIP_PREFLIGHT={flag}"
    return None


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------
def run_preflight(scope: str, *, root: Optional[str] = None, cfg: Any = None,
                  fix: bool = False, budget_ms: Optional[int] = None,
                  force: bool = False) -> HealthReport:
    """执行一次自检并返回结论（**本函数绝不抛异常**）。

    参数
    ----
    scope:     ``boot`` 或 ``project``。
    root:      项目数据根（``project`` 作用域必需）。
    cfg:       已加载的 ``PipelineConfig``（可选，缺省时部分检查跳过）。
    fix:       自动清理确定安全的残留（陈旧锁 / 已死进程的 _MEI 目录）。
    budget_ms: 覆盖默认整体预算。
    force:     忽略进程内缓存，强制重跑。

    返回
    ----
    :class:`HealthReport`；结论会打一行可解析日志，``project`` 作用域另存
    ``<root>/90_meta/preflight_<scope>.json``。
    """
    if scope not in BUDGET_MS:
        raise ValueError(f"未知自检作用域：{scope}")

    key = _key(scope, root)
    if not force and key in _RAN:
        return _RAN[key]

    reason = _disabled()
    if reason:
        rep = HealthReport(scope=scope, skipped=True, skip_reason=reason)
        log.warning(rep.summary_line())
        _RAN[key] = rep
        return rep

    # 延迟导入：注册表填充发生在 import 子模块时；放函数内避免循环依赖，
    # 也保证 `import h3d.health` 本身零成本。
    import h3d.health._boot  # noqa: F401
    import h3d.health._project as _proj  # noqa: F401

    # 只给了 cfg 没给 root 时，按数据根推导（00_raw 的父目录）。否则
    # needs_root 的检查会被整批跳过 —— 那会让 project 自检只剩"manifest 与
    # 照片"两项，看起来"全绿"其实什么都没查（自检最危险的失效模式）。
    if root is None and cfg is not None and scope == "project":
        try:
            root = _proj.data_root(cfg)
        except Exception:  # noqa: BLE001 - 推导失败按"无 root"处理
            root = None

    budget = int(budget_ms or BUDGET_MS[scope])
    ctx = Context(scope=scope, root=root, cfg=cfg, fix=bool(fix),
                  budget_ms=budget)
    specs = [s for s in _REGISTRY if s.scope == scope]
    specs = [s for s in specs
             if not (s.needs_cfg and cfg is None)
             and not (s.needs_root and not root)]
    specs.sort(key=lambda s: (s.order, s.name))

    results: list[CheckResult] = []
    t0 = time.monotonic()
    if specs:
        pool = ThreadPoolExecutor(max_workers=min(8, len(specs)),
                                  thread_name_prefix=f"preflight-{scope}")
        futures = {pool.submit(_run_one, s, ctx): s for s in specs}
        try:
            done, pending = wait(futures, timeout=budget / 1000.0)
            for fut in done:
                results.append(fut.result())
            for fut in pending:
                spec = futures[fut]
                # 不取消、不等待：线程会自然结束（子进程自带超时）。这里只
                # 记录"没在预算内给出结论"，避免启动被未知原因的卡顿拖住。
                #
                # 关键：绝不能用 `with ThreadPoolExecutor(...)` —— 它的
                # __exit__ 是 shutdown(wait=True)，会把本已超预算的启动
                # 再拖住一整个慢检查的时间（实测 boot 因此从 900 ms 变成
                # 2156 ms，自检本身成了最慢的启动环节）。
                fut.cancel()
                results.append(CheckResult(
                    name=spec.name, title=spec.title, severity="warning",
                    detail=f"检查超时（>{budget} ms 预算内未返回）",
                    elapsed_ms=budget))
        finally:
            # wait=False：主流程立刻返回；后台线程自行收尾（子进程都有超时，
            # 最迟 1.5 s 内结束，不会拖住解释器退出）。
            pool.shutdown(wait=False, cancel_futures=True)
    # 兜底：注册表为空时也要有结论（否则"自检通过"是假的）
    if not results:
        results.append(CheckResult(
            name="no_checks", title="检查项加载",
            severity="warning", detail="未注册任何检查项（包内模块可能缺失）"))

    results.sort(key=lambda r: (-_SEVERITY_RANK.get(r.severity, 0), r.name))
    rep = HealthReport(scope=scope, results=results,
                       elapsed_ms=int((time.monotonic() - t0) * 1000))
    _RAN[key] = rep
    _emit(rep, root)
    return rep


def _run_one(spec: _Spec, ctx: Context) -> CheckResult:
    """执行单项检查：任何异常都收敛成 WARNING（绝不外抛）。"""
    t0 = time.monotonic()
    try:
        res = spec.fn(ctx)
        if not isinstance(res, CheckResult):
            res = CheckResult(name=spec.name, title=spec.title,
                              severity="warning",
                              detail="检查函数返回类型非法（应为 CheckResult）")
    except Exception as exc:  # noqa: BLE001 - 自检自身绝不允许中断启动
        res = CheckResult(name=spec.name, title=spec.title, severity="warning",
                          detail=f"检查执行异常：{type(exc).__name__}: {exc}")
    res.name = res.name or spec.name
    res.title = res.title or spec.title
    if not res.elapsed_ms:
        res.elapsed_ms = int((time.monotonic() - t0) * 1000)
    return res


def _emit(rep: HealthReport, root: Optional[str]) -> None:
    """打一行可解析日志 + 落盘完整结果（落盘失败只记 debug，不影响结论）。"""
    line = rep.summary_line()
    if rep.ok:
        log.info(line)
    else:
        log.error(line)
    for r in rep.results:
        if r.severity == "warning":
            log.warning("[自检] %s：%s", r.title, r.detail)
        elif r.severity == "blocker":
            log.error("[自检] %s：%s", r.title, r.detail)
        if r.fix:
            log.info("[自检] 已自动修复（%s）：%s", r.title, r.fix)

    if rep.scope != "project" or not root:
        return
    out_dir = os.path.join(root, "90_meta")
    try:
        os.makedirs(out_dir, exist_ok=True)
        import json
        path = os.path.join(out_dir, f"preflight_{rep.scope}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rep.to_dict(), fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        log.debug("自检结果落盘失败（不影响结论）：%s", exc)


# ---------------------------------------------------------------------------
# 展示
# ---------------------------------------------------------------------------
def format_report(rep: HealthReport, *, only_problems: bool = True) -> str:
    """把结论排成中文文本块（GUI 弹窗 / CLI 输出共用）。

    ``only_problems=True`` 时省略 ``ok`` 项——启动弹窗只该说"哪里不对"，
    全绿时返回空串，调用方据此不打扰用户。
    """
    rows = [r for r in rep.results
            if (not only_problems) or r.severity != "ok"]
    if not rows:
        return ""
    head = ("系统自检（%s）：阻断 %d · 警告 %d · 提示 %d"
            % (rep.scope, len(rep.blockers), len(rep.warnings),
               len(rep.infos)))
    out = [head]
    for r in rows:
        out.append(f"[{_SEVERITY_CN.get(r.severity, r.severity)}] {r.title} —— "
                   f"{r.detail}")
        if r.fix:
            out.append(f"    已自动修复：{r.fix}")
        elif r.hint:
            out.extend("    " + ln for ln in r.hint.splitlines())
    return "\n".join(out)


__all__ = [
    "BUDGET_MS", "CheckResult", "Context", "HealthReport", "already_ran",
    "check", "format_report", "last_report", "reset_cache", "run_preflight",
]
