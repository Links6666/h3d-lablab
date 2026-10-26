"""h3d.health._boot : 软件启动自检（这台机器 + 这套程序能不能用）。

侧重点（与 ``project`` 的分工）
------------------------------
``boot`` 只回答「**程序本身**和**运行环境**是否健全」，不碰任何具体项目：

* 解释器 / 关键依赖是否齐（打包版缺模块 → 一开跑就 ImportError）；
* 打包解包目录 ``_MEIPASS`` 是否可写、是否正被父进程"共享"（``_MEI`` 坑）；
* COLMAP / NVIDIA 驱动 / 磁盘 / 临时目录是否可用；
* 有没有**上次遗留的进程或崩溃**在悄悄吃掉 GPU、锁住数据目录。

``project`` 侧才回答「这个项目的输入、产物、锁、manifest 是否自洽」。

预算
----
整体 900 ms（:data:`h3d.health.BUDGET_MS`）。所有检查并行，最慢的是
``tasklist``（~120 ms）与 ``nvidia-smi``（~200 ms），实测合计约 260 ms。

注意：这里**不做** ``import open3d`` —— 单次 import 约 1.2 s，一旦 import
就等于自检本身拖慢了启动。依赖是否可导入用 ``find_spec`` 判断。
"""

from __future__ import annotations

import os
import sys

from h3d.core import get_logger
from h3d.health import CheckResult, Context, check
from h3d.health import _probe as P

log = get_logger("health.boot")

#: 运行时**必需**的第三方依赖（缺任一 → 管线必然在某阶段崩）
_REQUIRED_MODULES = ("numpy", "scipy", "open3d", "trimesh", "yaml", "PIL")
#: 仅特定功能需要（缺了只影响 GUI / 报表 / 演示）
_OPTIONAL_MODULES = ("PySide6", "matplotlib", "jinja2", "sklearn")

#: 常见 COLMAP 安装位置（用户的机器上装在 E: 盘，见 README）
_COLMAP_HINTS = (
    r"E:\colmap\colmap-x64-windows-cuda\bin\colmap.exe",
    r"C:\Program Files\COLMAP\COLMAP.bat",
    r"C:\Program Files\COLMAP\colmap.exe",
)


def _find_colmap() -> str | None:
    """按 环境变量 → PATH → 常见安装位置 的顺序找 COLMAP。"""
    env = os.environ.get("H3D_COLMAP", "").strip().strip('"')
    if env and os.path.isfile(env):
        return env
    found = P.exe_path("colmap")
    if found:
        return found
    for cand in _COLMAP_HINTS:
        if os.path.isfile(cand):
            return cand
    return None


@check("boot", "interpreter", title="Python 解释器", order=10)
def _check_interpreter(ctx: Context) -> CheckResult:
    ver = "%d.%d.%d" % sys.version_info[:3]
    frozen = bool(getattr(sys, "frozen", False))
    if sys.version_info < (3, 10):
        return CheckResult("interpreter", "Python 解释器", "blocker",
                           f"Python {ver} 过低（需要 ≥ 3.10）",
                           "请使用项目要求的 Python 3.10+ 环境（见 environment.yml）")
    return CheckResult("interpreter", "Python 解释器", "ok",
                       f"Python {ver}" + ("（打包版）" if frozen else ""))


@check("boot", "deps", title="关键依赖", order=20)
def _check_deps(ctx: Context) -> CheckResult:
    missing = [m for m in _REQUIRED_MODULES if not P.module_available(m)]
    soft = [m for m in _OPTIONAL_MODULES if not P.module_available(m)]
    if missing:
        return CheckResult(
            "deps", "关键依赖", "blocker",
            "缺少必需依赖：%s" % ", ".join(missing),
            "开发环境：pip install -r requirements.txt\n"
            "打包版：说明打包漏收模块，请重新打包（检查 hiddenimports）")
    detail = "必需依赖齐全（%d 项）" % len(_REQUIRED_MODULES)
    if soft:
        detail += "；可选缺失：%s" % ", ".join(soft)
        return CheckResult("deps", "关键依赖", "info", detail,
                           "缺少的可选依赖只影响对应功能（GUI/报表/图表）")
    return CheckResult("deps", "关键依赖", "ok", detail)


@check("boot", "runtime_dir", title="运行目录（打包解包）", order=30)
def _check_runtime_dir(ctx: Context) -> CheckResult:
    """检查 ``_MEIPASS`` 可写 + 是否处于"父子共享"的危险状态。

    实机背景：GUI 退出会删掉自己的 ``_MEIxxxx``；若后台 ``--batch`` 子进程
    复用了同一个解包目录（``_PYI_PARENT_PROCESS_LEVEL`` 未清），GUI 一关，
    子进程后续 import 全炸（56 分钟后 Open3D ``add_dll_directory``
    FileNotFoundError）。这里只在**启动时**给出预警，实际清理由
    ``h3d.gui.runner.child_env`` 负责。
    """
    mei = getattr(sys, "_MEIPASS", "")
    if not getattr(sys, "frozen", False):
        # 开发模式不写探测文件：实测在 E: 盘 write+delete 要 750 ms（杀软扫描），
        # 而开发模式下 cwd 不可写只影响"COLMAP 日志落盘位置"，proc.resolve_cwd
        # 已自动回退到临时目录，不值得为它付这笔时间。
        cwd = os.getcwd()
        if not os.path.isdir(cwd):
            return CheckResult("runtime_dir", "运行目录（打包解包）", "warning",
                               f"当前工作目录已失效：{cwd}",
                               "从存在的目录重新启动（快捷方式可能指向了已删除的目录）")
        return CheckResult("runtime_dir", "运行目录（打包解包）", "ok",
                           f"开发模式：{cwd}")
    if not mei or not os.path.isdir(mei):
        return CheckResult("runtime_dir", "运行目录（打包解包）", "blocker",
                           f"打包解包目录不存在：{mei or '(未设置 _MEIPASS)'}",
                           "请重新运行 exe；若反复出现，检查杀毒软件是否拦截了临时目录")
    ok, why = P.write_probe(mei)
    lvl = os.environ.get("_PYI_PARENT_PROCESS_LEVEL", "").strip()
    shared = bool(lvl and lvl != "0")
    if not ok:
        return CheckResult("runtime_dir", "运行目录（打包解包）", "warning",
                           f"解包目录不可写（{why}）：部分功能（日志/缓存）会失败",
                           "检查磁盘空间与杀毒软件；必要时换一个临时目录启动")
    if shared:
        return CheckResult(
            "runtime_dir", "运行目录（打包解包）", "warning",
            f"当前进程复用了父进程的解包目录（_PYI_PARENT_PROCESS_LEVEL={lvl}）",
            "GUI 退出后该目录会被删除，后台批处理会在中途 import 失败。\n"
            "应通过 h3d.gui.runner.child_env() 启动子进程（清掉 "
            "_PYI_* 与 _MEIPASS2 变量）")
    return CheckResult("runtime_dir", "运行目录（打包解包）", "ok",
                       f"解包目录可写：{mei}")


@check("boot", "assets", title="资源文件", order=40)
def _check_assets(ctx: Context) -> CheckResult:
    try:
        from h3d.gui.resources import ICON_PNG, asset_path
        path = asset_path(ICON_PNG)
    except Exception as exc:  # noqa: BLE001 - 资源模块缺失不该拦住启动
        return CheckResult("assets", "资源文件", "info",
                           f"资源定位模块不可用：{exc}")
    if not path:
        return CheckResult("assets", "资源文件", "info",
                           "未找到图标资源（界面会用系统默认图标）",
                           "打包时确认 assets/ 已随 datas 打入")
    return CheckResult("assets", "资源文件", "ok", f"图标：{os.path.basename(path)}")


@check("boot", "version", title="版本一致性", order=50)
def _check_version(ctx: Context) -> CheckResult:
    import h3d

    ver = getattr(h3d, "__version__", "?")
    pyproject = None
    for cand in (os.path.join(os.getcwd(), "pyproject.toml"),
                 os.path.abspath(os.path.join(
                     os.path.dirname(h3d.__file__), "..", "..", "pyproject.toml"))):
        if os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8") as fh:
                    for line in fh:
                        if line.strip().startswith("version"):
                            pyproject = line.split("=", 1)[1].strip().strip('"\'')
                            break
            except OSError:
                pass
            break
    if pyproject and pyproject != ver:
        return CheckResult("version", "版本一致性", "warning",
                           f"代码版本 {ver} 与 pyproject {pyproject} 不一致",
                           "版本号唯一来源是 h3d.__version__，请同步 pyproject")
    return CheckResult("version", "版本一致性", "ok", f"v{ver}")


@check("boot", "colmap", title="COLMAP 可执行", order=60)
def _check_colmap(ctx: Context) -> CheckResult:
    """只做"文件在 + 不像占位文件"的**零开销**判断。

    为什么不在这里跑 ``colmap -h``：实测冷启动 1235 ms（要加载 CUDA 运行时），
    单这一项就吃满整个 boot 预算。启动阶段只需要回答"配置里那条路径是不是
    错的"，深度校验（能否真正跑 sparse 流程）放到 ``h3d doctor --deep`` ——
    那里不在启动关键路径上，慢一点没关系。
    """
    path = _find_colmap()
    if not path:
        return CheckResult(
            "colmap", "COLMAP 可执行", "info",
            "未在 PATH / 常见位置找到 colmap（配置里可显式指定路径）",
            "纯激光场景（有扫描件）不需要 COLMAP；\n"
            "纯照片场景必须在 config.yaml 的 sfm_solve.colmap_path 填绝对路径")
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return CheckResult("colmap", "COLMAP 可执行", "warning",
                           f"路径存在但读不到：{path}（{exc}）",
                           "检查文件权限，或换一个安装位置")
    if size < 512 * 1024:
        return CheckResult(
            "colmap", "COLMAP 可执行", "warning",
            f"{os.path.basename(path)} 只有 {size / 1024:.0f} KB，不像 COLMAP 本体",
            "配置里可能填成了启动脚本/包装器（如 COLMAP.bat）或下载未完成。\n"
            "应指向 bin/colmap.exe（完整程序约 30~90 MB）")
    return CheckResult("colmap", "COLMAP 可执行", "ok",
                       f"{os.path.basename(path)}（{size / 1024 / 1024:.0f} MB）")


@check("boot", "gpu", title="GPU 与显存", order=70)
def _check_gpu(ctx: Context) -> CheckResult:
    info = P.nvidia_smi(ctx.cache)
    if not info.get("available"):
        return CheckResult("gpu", "GPU 与显存", "info",
                           f"未检测到可用 NVIDIA GPU（{info.get('error') or '无'}）",
                           "无独显时稠密重建会退化为 CPU，耗时可增加 5~10 倍")
    g0 = info["gpus"][0]
    total = g0.get("mem_total_mb") or 0
    used = g0.get("mem_used_mb") or 0
    # "谁在占 GPU" 用进程表判断（psutil 17 ms），不再额外调一次 nvidia-smi
    busy = P.find_processes(("colmap",), ctx.cache)
    detail = "%s · 显存 %.0f/%.0f MB · 占用 %s%%" % (
        g0.get("name", "GPU"), used, total, g0.get("util_pct", "?"))
    if busy:
        detail += "；COLMAP 正在运行（PID %s）" % ", ".join(
            str(p["pid"]) for p in busy)
        return CheckResult(
            "gpu", "GPU 与显存", "warning", detail,
            "有 COLMAP 正在跑：可能是上一个项目没退干净，也可能是别的程序在用。\n"
            "两个稠密任务同时跑会让单张深度图从 45 s 退化到 145 s，请先确认。")
    if total and (total - used) < 1500:
        return CheckResult("gpu", "GPU 与显存", "warning",
                           detail + "（剩余不足 1.5 GB）",
                           "显存不足会让 patch_match_stereo 直接失败，先关掉占用显存的程序")
    return CheckResult("gpu", "GPU 与显存", "ok", detail)


@check("boot", "disk", title="磁盘空间", order=80)
def _check_disk(ctx: Context) -> CheckResult:
    import tempfile

    targets = [("临时目录", tempfile.gettempdir()), ("当前目录", os.getcwd())]
    seen, rows, worst = set(), [], ("ok", "")
    for label, path in targets:
        vol = os.path.splitdrive(os.path.abspath(path))[0] or "/"
        if vol in seen:
            continue
        seen.add(vol)
        free = P.disk_free(path)
        if free is None:
            continue
        gb = free / (1024 ** 3)
        rows.append(f"{label} {gb:.1f} GB")
        if gb < 0.5:
            worst = ("blocker", f"{label}仅剩 {gb:.2f} GB")
        elif gb < 2.0 and worst[0] == "ok":
            worst = ("warning", f"{label}仅剩 {gb:.2f} GB")
    if not rows:
        return CheckResult("disk", "磁盘空间", "info", "无法读取磁盘空间")
    detail = "可用：" + " · ".join(rows)
    if worst[0] == "ok":
        return CheckResult("disk", "磁盘空间", "ok", detail)
    return CheckResult("disk", "磁盘空间", worst[0], detail + f"（{worst[1]}）",
                       "稠密重建中间产物可达数 GB/件，空间不足会在中途写失败。\n"
                       "先清理 99_runs/sfm_tmp 下已完成项目的残留。")


@check("boot", "temp", title="临时目录可写", order=90)
def _check_temp(ctx: Context) -> CheckResult:
    import tempfile

    tmp = tempfile.gettempdir()
    ok, why = P.write_probe(tmp)
    if not ok:
        return CheckResult("temp", "临时目录可写", "warning",
                           f"临时目录不可写：{tmp}（{why}）",
                           "设置 TMPDIR / TEMP 指向可写目录后重启程序")
    stale = 0
    try:
        with os.scandir(tmp) as it:
            for e in it:
                if e.name.startswith("_MEI") and e.is_dir():
                    age = P.age_seconds(e.path) or 0
                    if age > 86400:
                        stale += 1
    except OSError:
        pass
    fix = ""
    if stale and ctx.fix:
        # 只提示不代删：无法确认哪个仍在用（父进程删除子进程解包目录是本项目
        # 已踩过的坑，反向误删同样致命）。
        fix = f"发现 {stale} 个超过 1 天的 _MEI 残留（未删除，需手动清理）"
    detail = f"可写：{tmp}"
    if stale:
        detail += f"；{stale} 个陈旧 _MEI 残留"
        return CheckResult("temp", "临时目录可写", "info", detail,
                           "陈旧 _MEI 目录会占用数百 MB：确认没有 H3D 在运行后，\n"
                           "可删除 %TEMP% 下超过 1 天的 _MEI* 目录", fix=fix)
    return CheckResult("temp", "临时目录可写", "ok", detail)


@check("boot", "leftover_process", title="遗留进程占用", order=100)
def _check_leftover(ctx: Context) -> CheckResult:
    """上次没退干净的 ``H3D.exe`` / ``colmap.exe``。

    这是"之前项目错误占用"最直接的形态：残留的 colmap 会一直占着 GPU，
    残留的 H3D 可能还持有某个数据根的锁。
    """
    h3d_procs = P.find_processes(("H3D",), ctx.cache)
    colmap_procs = P.find_processes(("colmap",), ctx.cache)
    if not h3d_procs and not colmap_procs:
        return CheckResult("leftover_process", "遗留进程占用", "ok",
                           "无遗留 H3D / COLMAP 进程")
    bits = []
    if h3d_procs:
        bits.append("H3D.exe PID %s" % ", ".join(str(p["pid"]) for p in h3d_procs))
    if colmap_procs:
        bits.append("colmap.exe PID %s" % ", ".join(str(p["pid"]) for p in colmap_procs))
    detail = "；".join(bits) + " 仍在运行"
    return CheckResult(
        "leftover_process", "遗留进程占用", "warning", detail,
        "若是上一个项目没退干净：确认没有正在跑的任务后执行\n"
        "  taskkill /F /PID <PID>   （或任务管理器结束进程）\n"
        "两个批处理同时跑会互删中间产物、争抢 GPU（实机已损失 3 小时）")


@check("boot", "recent_locks", title="历史项目锁", order=110)
def _check_recent_locks(ctx: Context) -> CheckResult:
    """扫最近项目的数据根，看有没有**存活持有者**的锁。

    陈旧锁（持有进程已死）会被 ``acquire_run_lock`` 自动接管，因此只报
    "有活锁"这一种真问题；陈旧锁仅提示"下次运行会自动接管"，不打扰。
    """
    try:
        from h3d.gui.history import HistoryStore
        roots = [r.get("path") for r in HistoryStore().list()]
    except Exception as exc:  # noqa: BLE001 - 历史记录不可读不影响启动
        return CheckResult("recent_locks", "历史项目锁", "info",
                           f"无法读取最近项目列表（{type(exc).__name__}），跳过")
    from h3d.core.lockfile import LOCK_NAME, _pid_alive, _read_holder

    alive, stale = [], 0
    for root in [r for r in roots if r and os.path.isdir(r)][:20]:
        path = os.path.join(root, LOCK_NAME)
        if not os.path.isfile(path):
            continue
        holder = _read_holder(path)
        pid = int(holder.get("pid") or 0)
        if pid and _pid_alive(pid):
            alive.append((root, pid, holder.get("started_at", "?")))
        else:
            stale += 1
            if ctx.fix:
                try:
                    os.remove(path)
                except OSError as exc:
                    log.debug("陈旧锁删除失败（忽略）：%s（%s）", path, exc)
    if alive:
        rows = "\n".join(f"  · {r}\n    被 PID {p}（启动于 {t}）占用"
                         for r, p, t in alive)
        return CheckResult(
            "recent_locks", "历史项目锁", "warning",
            f"{len(alive)} 个历史项目仍被运行中的进程占用",
            rows + "\n这些项目此刻不能重复启动；确认那个进程该关掉后结束它，"
                   "或改用另一个数据根。")
    detail = "历史项目无活锁"
    if stale:
        detail += f"（{stale} 个陈旧锁，下次运行会自动接管）"
        if ctx.fix:
            return CheckResult("recent_locks", "历史项目锁", "ok", detail,
                               fix=f"已清理 {stale} 个陈旧锁")
    return CheckResult("recent_locks", "历史项目锁", "ok", detail)


@check("boot", "crash_log", title="上次崩溃记录", order=120)
def _check_crash_log(ctx: Context) -> CheckResult:
    path = os.path.join(os.path.expanduser("~"), ".workbuddy",
                        "h3d_gui_crash.log")
    age = P.age_seconds(path)
    if age is None:
        return CheckResult("crash_log", "上次崩溃记录", "ok", "无崩溃日志")
    if age > 86400 * 7:
        return CheckResult("crash_log", "上次崩溃记录", "ok",
                           f"崩溃日志超过 7 天（{age / 86400:.0f} 天前）")
    tb = ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-40:]
        hit = [ln.strip() for ln in lines
               if "Traceback" in ln or "启动失败" in ln or "Error" in ln]
        tb = hit[-1][:160] if hit else ""
    except OSError:
        pass
    if not tb:
        return CheckResult("crash_log", "上次崩溃记录", "ok",
                           f"有崩溃日志但未见异常条目（{age / 3600:.0f} 小时前）")
    return CheckResult(
        "crash_log", "上次崩溃记录", "warning",
        f"上次启动/运行有异常（{age / 3600:.0f} 小时前）：{tb}",
        f"完整日志：{path}\n若问题已修复可忽略；反复出现请附该文件排查")


__all__ = []
