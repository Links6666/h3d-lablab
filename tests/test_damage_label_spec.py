"""`scripts/check_damage_labels.py` 的自测：每条检查都要真能触发。

为什么要给它写测试
------------------
这是"规范的可执行部分"。规范写了 4 条拍摄约束、6 项字段要求，如果脚本里
某条检查其实永远不会触发（写错了键名、条件反了），那规范就退化成一段散文——
本项目的头号教训就是"看起来有约束、实际没有"（`alignment_quality` 里那行空种子）。

所以这里**故意造三种坏包**，逐一确认对应的检查会报出来。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np
import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                      "check_damage_labels.py")


def _photo(path: str, size=(800, 600)) -> None:
    from PIL import Image

    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, (120, 120, 120)).save(path)


def _make_project(tmp_path, item="relic_002", n_photos=6):
    """造一个最小项目：00_photo + capture_meta（环绕一圈的相机）。"""
    proj = tmp_path / "proj"
    pdir = proj / "阶段成果" / "00_photo" / item
    names = []
    cameras = {1: {"w": 800, "h": 600, "fx": 800.0, "fy": 800.0,
                   "cx": 400.0, "cy": 300.0}}
    images = []
    for i in range(n_photos):
        name = f"IMG_{i:04d}.JPG"
        _photo(str(pdir / name))
        names.append(name)
        az = 2 * np.pi * i / n_photos
        eye = np.array([3.0 * np.cos(az), 3.0 * np.sin(az), 0.2])
        f = -eye / np.linalg.norm(eye)
        up = np.array([0.0, 0.0, 1.0])
        s = np.cross(f, up); s /= np.linalg.norm(s)
        u = np.cross(s, f)
        R = np.stack([s, u, -f], axis=1).T
        images.append({"name": name, "R": R, "t": eye, "cam_id": 1})
    meta = proj / "阶段成果" / "90_meta"
    os.makedirs(meta, exist_ok=True)
    from h3d.sfm_solve import write_capture_meta

    write_capture_meta(cameras, images,
                       str(meta / f"{item}_capture_meta.json"))
    return proj, names


def _bundle(tmp_path, item="relic_002", *, classes=("crack",) * 10,
            views_per=3, photos=None, polygon=None):
    """造标注包：classes 的长度 = 病害处数，值 = 类别。"""
    root = tmp_path / "bundle"
    d = root / item
    d.mkdir(parents=True)
    photos = photos or ["IMG_0000.JPG", "IMG_0001.JPG", "IMG_0002.JPG"]
    damages = []
    for i, cls in enumerate(classes, start=1):
        views = []
        for j in range(views_per):
            views.append({
                "photo": photos[j % len(photos)],
                "polygon": polygon or [[100, 100], [140, 105], [135, 160]],
            })
        damages.append({"id": f"D{i:03d}", "class": cls, "views": views})
    payload = {"schema": "h3d-damage-labels/1", "item_id": item,
               "classes": sorted(set(classes)), "damages": damages}
    (d / "labels.json").write_text(json.dumps(payload), encoding="utf-8")
    return root


def _run(*args):
    return subprocess.run([sys.executable, SCRIPT, *args],
                          capture_output=True, text=True, encoding="utf-8")


class TestChecksFire:
    def test_missing_photo_is_hard_failure(self, tmp_path):
        proj, _names = _make_project(tmp_path)
        root = _bundle(tmp_path, photos=["IMG_0000.JPG", "NOPE.JPG", "IMG_0002.JPG"])
        r = _run(str(root), "--project", str(proj))
        assert r.returncode == 1
        assert "照片不存在" in r.stdout

    def test_polygon_outside_frame_is_reported(self, tmp_path):
        proj, _names = _make_project(tmp_path)
        root = _bundle(tmp_path, polygon=[[100, 100], [9999, 105], [135, 160]])
        r = _run(str(root), "--project", str(proj))
        assert r.returncode == 1
        assert "出画" in r.stdout

    def test_too_few_views_is_counted_out(self, tmp_path):
        proj, _names = _make_project(tmp_path)
        root = _bundle(tmp_path, views_per=2)
        r = _run(str(root), "--project", str(proj))
        assert r.returncode == 1
        assert "只有 2 个视角" in r.stdout

    def test_same_camera_position_is_caught(self, tmp_path):
        """三张都用同一张照片 = 同一机位，夹角 0° —— 必须被判出去。"""
        proj, _names = _make_project(tmp_path)
        root = _bundle(tmp_path, photos=["IMG_0000.JPG"])
        r = _run(str(root), "--project", str(proj))
        assert r.returncode == 1
        assert "同一机位" in r.stdout

    def test_bad_class_is_hard_failure(self, tmp_path):
        proj, _names = _make_project(tmp_path)
        root = _bundle(tmp_path, classes=("scratch",) * 3)
        r = _run(str(root), "--project", str(proj))
        assert r.returncode == 1
        assert "不在白名单" in r.stdout

    def test_item_id_mismatch_is_hard_failure(self, tmp_path):
        proj, _names = _make_project(tmp_path)
        root = _bundle(tmp_path)
        p = root / "relic_002" / "labels.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["item_id"] = "someone_else"
        p.write_text(json.dumps(data), encoding="utf-8")
        r = _run(str(root), "--project", str(proj))
        assert r.returncode == 1
        assert "与目录名不一致" in r.stdout

    def test_enough_samples_passes(self, tmp_path):
        """3 类 × 10 处、每处 3 个不同视角 → 通过。"""
        proj, _names = _make_project(tmp_path)
        classes = tuple(["crack"] * 10 + ["detachment"] * 10
                        + ["efflorescence"] * 10)
        root = _bundle(tmp_path, classes=classes)
        r = _run(str(root), "--project", str(proj))
        assert r.returncode == 0, r.stdout
        assert "通过" in r.stdout

    def test_without_project_degrades_loudly(self, tmp_path):
        """不给项目根时位姿检查跳过，但必须**说出来**，不能静默通过。"""
        root = _bundle(tmp_path)
        r = _run(str(root))
        assert "跳过" in r.stdout
