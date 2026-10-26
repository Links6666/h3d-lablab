"""h3d.gui.runner : 后台运行线程（子进程调度 h3d.cli batch + 信号）。

解析 CLI stdout 行：
  ▶ S1 开始 / S1 完成：...             -> 阶段状态（running / done）
  [INFO] [h3d.sfm_solve] S1 dense[2/3] -> 子步骤进度
  [INFO] ... 批量处理完成：total=..    -> 完成
  失败隔离/异常 -> 阶段失败
信号：stage_updated(stage_id, status)、progress(int)、log_line(str)、
      finished(ok, summary)。

实机修复（2026-10-17，56 分钟后失败）
-------------------------------------
PyInstaller onefile 用环境变量 ``_PYI_*`` / ``_MEIPASS2`` 把**父进程的解包
目录**传给同族子进程，让子进程复用 ``%TEMP%\\_MEIxxxxxx`` 而不再解包一次。
这本身是优化，但 GUI 一旦退出（关窗口、点左上角关闭），bootloader 会删除
那个临时目录，而仍在运行的 ``H3D.exe --batch`` 子进程还在使用它。

实机表现：30 张照片跑到 56 分钟、dense 全部完成，最后一步
``load_point_cloud(fused.ply)`` 才 import open3d，此时
``os.add_dll_directory(_MEI.../open3d)`` 报
``FileNotFoundError: .../_MEI000065282/open3d`` —— 前面所有阶段都不用
open3d，所以崩溃点被推迟到了最后。

修复：spawn 前剔除这些变量，让子进程**独立解包**一份（多花 3~8 秒、多占一份
临时空间，换取与父进程生命周期完全解耦）。
"""

from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import QThread, Signal

from h3d.core import no_window_kwargs
from h3d.gui.logparse import (ProgressModel, STAGE_ORDER, batch_summary,
                              items_total_of, item_of, stage_done_of,
                              stage_failed_of, stage_skip_of, stage_start_of,
                              substep_of)

#: PyInstaller onefile 用于「子进程复用父进程解包目录」的环境变量
_PYI_SHARED_ENV = ("_MEIPASS2", "_PYI_APPLICATION_HOME_DIR",
                   "_PYI_ARCHIVE_FILE", "_PYI_PARENT_PROCESS_LEVEL")


def child_env() -> dict:
    """子进程环境：剔除 PyInstaller 的解包目录共享变量 + 强制 UTF-8 输出。

    剔除 ``_PYI_*`` 后子进程会自己解包到独立的 ``_MEIxxxxxx``，父进程退出/
    被关不再影响它。

    ``PYTHONIOENCODING`` / ``PYTHONUTF8``：打包版（``console=False``）子进程的
    ``sys.stdout`` 默认按系统代码页（cp936）编码，而父进程按 UTF-8 解码，
    中文行会变成 ``[H3D] ���������� 26048 ...`` 这样的乱码。固定成 UTF-8 后
    GUI 日志区中文正常（实测 2026-10-17）。
    """
    env = dict(os.environ)
    for key in _PYI_SHARED_ENV:
        env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


class BatchRunner(QThread):
    """后台执行 h3d.cli batch，解析输出并发射进度/阶段/日志信号。"""

    stage_updated = Signal(str, str)  # stage_id, status(waiting/running/done/failed)
    progress = Signal(int)            # 0~100
    log_line = Signal(str)
    finished_ok = Signal(bool, str)   # ok, summary
    substep = Signal(str, str)        # stage_id, 子步骤描述（如 "dense 2/3"）
    item_changed = Signal(str)        # 当前文物编号（进度页据此定位产物目录）
    items_total = Signal(int)         # 本批文物总数（进度条显示「第 k/N 件」）

    def __init__(self, config_path: str, parent=None):
        super().__init__(parent)
        self.config_path = config_path
        self._proc: subprocess.Popen | None = None
        self._cancelled = False

    def cancel(self) -> None:
        """取消：终止批处理进程**及其整棵子树**。

        实机缺陷（2026-10-17）：原实现只 ``terminate()`` 批处理进程本身。
        ``H3D.exe --batch`` 下面是 ``colmap.exe patch_match_stereo``，
        Windows 不会级联终止 —— 用户点「取消」后 GUI 显示"已请求取消"，
        但两个 ``colmap.exe`` 仍在后台跑满 GPU（实测残留 3 个 H3D + 2 个
        colmap），下次启动继续争抢。改用 ``taskkill /T`` 按进程树杀。
        """
        self._cancelled = True
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        pid = proc.pid
        try:
            if sys.platform.startswith("win"):
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    **no_window_kwargs())
            else:
                import os
                import signal

                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except OSError:
                    proc.terminate()
        except Exception:  # noqa: BLE001 - 取消失败不得再抛
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    def run(self) -> None:  # noqa: D401
        if getattr(sys, "frozen", False):
            # 打包环境：子进程 = exe --batch <config>
            cmd = [sys.executable, "--batch", self.config_path]
        else:
            cmd = [sys.executable, "-m", "h3d.cli", "batch", "-c",
                   self.config_path]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                cwd=os.path.dirname(os.path.abspath(self.config_path)) or None,
                env=child_env(),        # 关键：解除 _MEI 共享
                # POSIX：独立进程组，取消时整组终止（Windows 用 taskkill /T）
                start_new_session=not sys.platform.startswith("win"),
                **no_window_kwargs())   # 实机修复：不再弹出黑色命令行窗口
        except Exception as exc:  # noqa: BLE001
            self.log_line.emit(f"[错误] 无法启动批处理：{exc}")
            self.finished_ok.emit(False, f"启动失败：{exc}")
            return
        stages_done: set[str] = set()
        last_progress = 0
        #: 「批量处理完成」行的解析结果（见到即 break，收尾统一判定）
        summary: dict | None = None
        # 进度必须按「件 × 阶段」累计：原实现按阶段 id 去重 + 分母含 P1/X1/X2，
        # 5 件批处理跑到第 1 件结束就锁死在 7/10 = 70%（实机 2026-10-24）。
        model = ProgressModel()

        def _bump(value: int) -> None:
            """进度只增不减（COLMAP 子步骤与阶段完成两套口径会交替触发）。"""
            nonlocal last_progress
            value = max(last_progress, min(99, value))
            if value != last_progress:
                last_progress = value
                self.progress.emit(value)

        try:
            for line in self._proc.stdout:
                line = line.rstrip()
                self.log_line.emit(line)

                item = item_of(line)
                if item:
                    self.item_changed.emit(item)

                n_all = items_total_of(line)
                if n_all:
                    self.items_total.emit(n_all)

                sid = stage_start_of(line)
                if sid and sid not in stages_done:
                    self.stage_updated.emit(sid, "running")

                sub = substep_of(line)
                if sub:
                    self.substep.emit(*sub)

                # 整批进度：内部含 S1 子步骤线性映射（稠密 30 分钟不再原地不动）
                pct = model.feed(line)
                if pct is not None:
                    _bump(pct)

                sid = stage_done_of(line)
                if sid and sid not in stages_done:
                    stages_done.add(sid)
                    self.stage_updated.emit(sid, "done")

                # 真跳过：灯置灰（进度由 model 按件计分，不再按灯数）
                sk = stage_skip_of(line)
                if sk and sk not in stages_done:
                    stages_done.add(sk)
                    self.stage_updated.emit(sk, "skipped")

                mf = stage_failed_of(line)
                if mf:
                    self.stage_updated.emit(mf, "failed")

                mt = batch_summary(line)
                if mt:
                    # 只 break 不 return：子进程在打印这一行之后**还要生成报表**
                    # （csv/png/html，实测 1~2 s）。提前 return 会让管道无人读，
                    # 子进程写满缓冲区即阻塞，报表停在半写状态；GUI 又已经宣布
                    # 「完成」并放开按钮，用户此刻再点一次就可能撞上数据根锁。
                    summary = mt
                    break
        except Exception as exc:  # noqa: BLE001
            # 关键：QThread.run 里未捕获的异常会让线程**静默结束**——GUI 侧
            # 只是"日志不再刷新、进度条不动"，看起来和卡死完全一样。
            # r5 曾因漏导入 substep_of 触发 NameError，导致 30 张照片全程
            # 只显示 2 行日志（子进程其实跑得好好的）。
            import traceback
            self.log_line.emit(f"[错误] 日志解析线程异常：{exc}")
            self.log_line.emit(traceback.format_exc())
            self.finished_ok.emit(False, f"日志解析中断：{exc}")
            return

        if summary is not None:
            # 读完余下输出（报表产物行），再等进程真正退出——否则「完成」
            # 只是个早到的宣告，用户接着点按钮会与仍在写的报表抢同一个目录。
            try:
                for line in self._proc.stdout:
                    self.log_line.emit(line.rstrip())
            except Exception:  # noqa: BLE001 - 收尾阶段的读失败不改变结论
                pass
            rc = self._proc.wait()
            total = summary["total"]
            done = summary["done"]
            failed = summary["failed"]
            skipped = summary["skipped"]
            # 「跳过」≠「失败」：断点续跑整批跳过时 done=0、skipped=N，
            # 这是**正确终态**。原判据 `done > 0` 会把一次幂等重跑判成失败
            # （实机 2026-10-25：跑完后再点一次「一键生成」→ 弹「运行未完成」）。
            ok = (failed == 0 and rc == 0 and done + skipped > 0)
            text = (f"完成：total={total} done={done} failed={failed} "
                    f"skipped={skipped}")
            if skipped and not done:
                text = (f"无需重跑：total={total} 全部 {skipped} 件已是最新"
                        f"（断点续跑跳过，产物未变）")
            elif skipped:
                text += f"（其中 {skipped} 件已是最新）"
            self.progress.emit(100)
            self.finished_ok.emit(ok, text)
            return

        rc = self._proc.wait()
        self.progress.emit(100)
        self.finished_ok.emit(
            rc == 0, f"批处理结束（exit={rc}）——请查看日志")


__all__ = ["BatchRunner", "STAGE_ORDER", "child_env"]

