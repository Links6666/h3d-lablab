"""S1「同照片多件」复用回归（2026-10-24 实机）。

实机背景
--------
``E:\\兵马俑照片`` 下 5 件文物，``00_photo/<件>/`` 各放 30 张
``DSC_0008.JPG``…，**内容完全相同**（同一批照片被复制成 5 份）。
旧实现按件逐次解 SfM：单件 1801 s（稠密占 76%），5 件就是 2.5 小时，
而位姿/点云按定义完全一致 —— 纯浪费。

现在按「文件名 + 字节数 + 内容 SHA-1」比对照片集，命中则复制
``fused_dense.ply`` 与 ``capture_meta.json``，S1 从 30 min 降到约 2 s。
"""

from __future__ import annotations

import json
import os
import types

from h3d.sfm_solve import photo_set_fingerprint, reuse_sfm_result


def _cfg(**over):
    s = dict(reuse_identical_photos=True, force_redense=False, dense=True,
             max_image_size=0, dense_max_image_size=1600,
             dense_geom_consistency=True, dense_gate=True,
             dense_gate_std=2.0, dense_gate_density=0.01,
             dense_min_pixels=2, dense_max_pixels=20)
    s.update(over)
    return types.SimpleNamespace(sfm_solve=types.SimpleNamespace(**s))


def _task(root, item):
    return types.SimpleNamespace(root=str(root), item_id=item)


def _photos(root, item, n=3, tag=b"same"):
    d = root / "00_photo" / item
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"DSC_{i:04d}.JPG").write_bytes(tag + bytes([i]))
    return d


def _donor_outputs(root, item, n_images=3):
    dense = root / "10_dense" / item
    dense.mkdir(parents=True, exist_ok=True)
    (dense / "fused_dense.ply").write_bytes(b"ply\nfake\n")
    meta = root / "90_meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / f"{item}_capture_meta.json").write_text(
        json.dumps({"n_cameras": 1, "n_images": n_images, "images": []}),
        encoding="utf-8")


def test_fingerprint_ignores_directory_but_not_content(tmp_path):
    a = _photos(tmp_path, "a", 3, b"x")
    b = _photos(tmp_path, "b", 3, b"x")
    c = _photos(tmp_path, "c", 3, b"y")
    fa = photo_set_fingerprint(str(a))
    assert fa == photo_set_fingerprint(str(b)), "同内容不同目录必须同指纹"
    assert fa != photo_set_fingerprint(str(c)), "内容不同必须不同指纹"


def test_reuse_copies_outputs_and_records_metrics(tmp_path):
    _photos(tmp_path, "donor", 3, b"x")        # 同一批照片
    _donor_outputs(tmp_path, "donor")
    _photos(tmp_path, "new", 3, b"x")
    # 先让索引认识 donor（正常解算完成后会登记）
    idx = {"version": 1, "entries": {}}
    fp = photo_set_fingerprint(str(tmp_path / "00_photo" / "donor"))
    from h3d.sfm_solve import _sfm_params_key
    idx["entries"][fp] = {"item": "donor", "params": _sfm_params_key(_cfg())}
    p = tmp_path / "99_runs" / "s1_reuse_index.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(idx), encoding="utf-8")

    got = reuse_sfm_result(_task(tmp_path, "new"), _cfg(),
                           str(tmp_path / "00_photo" / "new"))
    assert got is not None
    assert got["metrics"]["s1_reuse"]["from"] == "donor"
    assert got["metrics"]["s1_reuse"]["params_match"] is True
    assert got["metrics"]["n_registered"] == 3
    assert (tmp_path / "10_dense" / "new" / "fused_dense.ply").exists()
    assert (tmp_path / "90_meta" / "new_capture_meta.json").exists()
    assert got["elapsed"] < 5.0, "复用必须是秒级"


def test_reuse_falls_back_to_scan_without_index(tmp_path):
    """升级前已解算的 donor 没有索引，必须靠扫描发现（参数一致性未知）。"""
    _photos(tmp_path, "donor", 3, b"x")
    _donor_outputs(tmp_path, "donor")
    _photos(tmp_path, "new", 3, b"x")
    got = reuse_sfm_result(_task(tmp_path, "new"), _cfg(),
                           str(tmp_path / "00_photo" / "new"))
    assert got is not None
    assert got["metrics"]["s1_reuse"]["from"] == "donor"
    assert got["metrics"]["s1_reuse"]["params_match"] is False


def test_different_photos_are_not_reused(tmp_path):
    _photos(tmp_path, "donor", 3, b"x")
    _donor_outputs(tmp_path, "donor")
    _photos(tmp_path, "other", 3, b"y")
    assert reuse_sfm_result(_task(tmp_path, "other"), _cfg(),
                            str(tmp_path / "00_photo" / "other")) is None


def test_switches_disable_reuse(tmp_path):
    _photos(tmp_path, "donor", 3, b"x")
    _donor_outputs(tmp_path, "donor")
    _photos(tmp_path, "new", 3, b"x")
    photo = str(tmp_path / "00_photo" / "new")
    assert reuse_sfm_result(_task(tmp_path, "new"),
                            _cfg(reuse_identical_photos=False), photo) is None
    assert reuse_sfm_result(_task(tmp_path, "new"),
                            _cfg(force_redense=True), photo) is None
    assert reuse_sfm_result(_task(tmp_path, "new"),
                            _cfg(dense=False), photo) is None


def test_partial_donor_outputs_are_not_reused(tmp_path):
    """只有 ply 没有 capture_meta：下游 M4 会崩，不能被当成可复用。"""
    _photos(tmp_path, "donor", 3, b"x")
    _photos(tmp_path, "new", 3, b"x")
    dense = tmp_path / "10_dense" / "donor"
    dense.mkdir(parents=True, exist_ok=True)
    (dense / "fused_dense.ply").write_bytes(b"ply\n")
    assert reuse_sfm_result(_task(tmp_path, "new"), _cfg(),
                            str(tmp_path / "00_photo" / "new")) is None
