"""CLI ``h3d anomaly``（B2 / X3 第一轮）。

只测命令行这一层：找输入、退出码、产物落位。判据在
``tests/test_anomaly_detect.py`` 里已被单测覆盖，这里不重复造 —— 两处判据
必然漂移，漂移的后果是「命令行说没问题、报表里没有产物」。
"""

from __future__ import annotations

import csv
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

ITEM = "r1"


def _write_png(path: str, arr: np.ndarray) -> None:
    from PIL import Image

    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.fromarray(arr).save(path)


def _gradient(size: int = 256) -> np.ndarray:
    y, x = np.mgrid[0:size, 0:size].astype(np.float64)
    r = 120 + 40 * x / size
    g = 110 + 30 * y / size
    b = 100 + 20 * (x + y) / (2 * size)
    return np.clip(np.stack([r, g, b], -1), 0, 255).astype(np.uint8)


def _island(size: int = 256) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    c = (size - 1) / 2
    return (((xx - c) ** 2 + (yy - c) ** 2) <= (size * 0.45) ** 2
            ).astype(np.uint8) * 255


def _project(tmp_path, *, with_blob: bool = True, mask_size: int = 256):
    """造一个只有 50_tex 图集与掩膜的项目（M4 之后的真实形态）。"""
    root = tmp_path / "proj"
    tex = root / "阶段成果" / "50_tex" / ITEM
    img = _gradient(256)
    if with_blob:
        img[118:138, 118:138] = np.array([238, 230, 196], np.uint8)
    _write_png(str(tex / f"{ITEM}_L1.0_diffuse.png"), img)
    _write_png(str(tex / f"{ITEM}_L1.0_mask.png"),
               _island(mask_size))
    return root


class TestAnomalyCommand:
    def test_missing_atlas_returns_1_with_hint(self, tmp_path, capsys):
        from h3d.cli import main

        rc = main(["anomaly", "-p", str(tmp_path / "empty")])

        assert rc == 1
        err = capsys.readouterr().err
        assert "50_tex" in err and "M4" in err

    def test_runs_and_writes_four_outputs(self, tmp_path, capsys):
        """跑通 → 四件产物齐全，metrics.json 里的图斑数与 regions.csv 对得上。"""
        from h3d.cli import main

        root = _project(tmp_path)
        rc = main(["anomaly", "-p", str(root)])
        out = capsys.readouterr().out

        assert rc == 0
        assert ITEM in out
        anom_dir = root / "阶段成果" / "60_qc" / ITEM / "anomaly"
        for name in ("anomaly_heat.png", "anomaly_mask.png", "regions.csv",
                     "metrics.json"):
            assert (anom_dir / name).is_file(), f"缺产物 {name}"

        metrics = json.load(open(anom_dir / "metrics.json", encoding="utf-8"))
        assert metrics["metrics"]["n_regions"] == 1
        assert metrics["metrics"]["area_units"] == "texel_ratio"
        assert metrics["config"]["k"] == 3.5
        rows = list(csv.DictReader(open(anom_dir / "regions.csv",
                                        encoding="utf-8-sig")))
        assert len(rows) == 1
        assert int(rows[0]["texels"]) > 100

    def test_json_output_is_machine_readable(self, tmp_path, capsys):
        from h3d.cli import main

        root = _project(tmp_path)
        rc = main(["anomaly", "-p", str(root), "--no-write", "--json"])

        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["errors"] == []
        item = payload["items"][0]
        assert item["item_id"] == ITEM
        assert item["metrics"]["n_regions"] == 1
        assert "output_dir" not in item          # --no-write 不该报产物目录
        assert not (root / "阶段成果" / "60_qc").exists()

    def test_mask_size_mismatch_returns_1(self, tmp_path, capsys):
        """掩膜与图集尺寸不一致 → rc=1 且说明两边的尺寸（现场才查得动）。"""
        from h3d.cli import main

        root = _project(tmp_path, mask_size=128)
        rc = main(["anomaly", "-p", str(root)])

        assert rc == 1
        err = capsys.readouterr().err
        assert "尺寸" in err
