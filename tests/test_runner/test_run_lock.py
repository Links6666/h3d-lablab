"""tests.test_runner.test_run_lock : 数据根单实例锁回归。

实机背景（2026-10-17，损失约 3 小时）
--------------------------------------
同一数据根 ``E:\\兵马俑照片`` 上先后起了两个 ``H3D.exe --batch``（PID 26048 /
26332）。新进程进 S1 时清 ``dense``，把旧进程正在写的深度图目录删掉，旧进程
``patch_match_stereo`` 在最后一张报::

    Check failed: (file).is_open() Could not open ".../DSC_0037.JPG.geometric.bin"

69 分钟稠密计算作废；两个进程还同时抢 GPU，单张深度图耗时翻倍；两者交替
覆写 ``run_manifest.json``，失败原因互相污染。

本测试锁三条本质行为：
1. 同一数据根第二次 ``acquire`` 必须被拒绝（且消息含 PID，可直接展示）；
2. 持有者已死（崩溃残留）时必须自动接管，不能把用户永久锁在门外；
3. ``release`` 只删自己那份，不误删接管者的锁。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from h3d.core.lockfile import LOCK_NAME, LockBusyError, acquire_run_lock


class TestAcquireRelease:

    def test_acquire_creates_lockfile_with_own_pid(self, tmp_path):
        root = str(tmp_path / "data")
        lock = acquire_run_lock(root)
        try:
            path = os.path.join(root, LOCK_NAME)
            assert os.path.exists(path)
            holder = json.load(open(path, encoding="utf-8"))
            assert holder["pid"] == os.getpid()
            assert holder["token"]
        finally:
            lock.release()
        assert not os.path.exists(path)

    def test_release_is_idempotent(self, tmp_path):
        """GUI 里取消/异常退出可能重复 release，不得抛异常。"""
        lock = acquire_run_lock(str(tmp_path / "d"))
        lock.release()
        lock.release()

    def test_creates_root_when_missing(self, tmp_path):
        root = str(tmp_path / "not" / "yet" / "here")
        with acquire_run_lock(root) as lock:
            assert os.path.isdir(root)
        assert not os.path.exists(lock.path)


class TestBusyDetection:

    def test_rejects_when_another_live_process_holds(self, tmp_path):
        """存活的其他进程持锁 -> 必须拒绝，并把占用者信息带给用户。"""
        root = str(tmp_path / "data")
        os.makedirs(root)
        other = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            with open(os.path.join(root, LOCK_NAME), "w",
                      encoding="utf-8") as fh:
                json.dump({"pid": other.pid, "started_at": "2026-10-17T21:39:00",
                           "token": "other"}, fh)
            with pytest.raises(LockBusyError) as ei:
                acquire_run_lock(root)
            msg = str(ei.value)
            assert str(other.pid) in msg, "消息必须指明是哪个进程在占用"
            assert "GPU" in msg, "消息必须解释为什么不能同时跑"
        finally:
            other.kill()
            other.wait(timeout=10)

    def test_takes_over_stale_lock(self, tmp_path):
        """崩溃残留（PID 已不存在）必须自动接管——否则用户被永久锁死。"""
        root = str(tmp_path / "data")
        os.makedirs(root)
        with open(os.path.join(root, LOCK_NAME), "w", encoding="utf-8") as fh:
            json.dump({"pid": 999999, "started_at": "2026-10-17T00:00:00",
                       "token": "dead"}, fh)
        lock = acquire_run_lock(root)
        try:
            holder = json.load(open(lock.path, encoding="utf-8"))
            assert holder["pid"] == os.getpid()
        finally:
            lock.release()

    def test_takes_over_corrupt_lock(self, tmp_path):
        """锁文件被写坏（断电/半写）时不得让用户无法启动。"""
        root = str(tmp_path / "data")
        os.makedirs(root)
        with open(os.path.join(root, LOCK_NAME), "w", encoding="utf-8") as fh:
            fh.write("{not-json")
        with acquire_run_lock(root) as lock:
            assert os.path.exists(lock.path)


class TestReleaseDoesNotSteal:
    def test_release_keeps_other_holders_lock(self, tmp_path):
        """旧句柄 release 时若锁已被新进程接管，不得删掉新锁。"""
        root = str(tmp_path / "data")
        first = acquire_run_lock(root)
        # 模拟接管：另一个持有者重写锁文件
        with open(first.path, "w", encoding="utf-8") as fh:
            json.dump({"pid": 4242, "started_at": "x", "token": "newer"}, fh)
        first.release()
        assert os.path.exists(first.path), "不能误删接管者的锁"
