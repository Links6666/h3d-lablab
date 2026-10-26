"""tests.test_gui_config : GUI 配置读回与「假控件」回归。

实机背景（2026-10-25）
----------------------
用户第一天把项目调好（``sfm_solve.dense: true``、``max_image_size: 2400``、
``photo_texture.top_k: 5``），第二天从「最近项目」打开，点「一键生成」——
``MainWindow`` 只拿到 root/name/scene，``ProjectConfig`` 是全新默认值，
``to_yaml`` 又**无条件用模板重写整份 config.yaml**：

* ``sfm_solve.dense`` true → false（直接跳过稠密重建，几何退回稀疏点云）；
* ``max_image_size`` / ``top_k`` / ``workers`` 等非控件参数全部回默认；
* ``quality.coverage_threshold`` 被硬编码写成 0.30。

另外主界面的「接缝平滑」「几何质检」与高级面板的 top-k / 每面采样 /
密度过滤从未回写到配置——改了不生效，是纯装饰控件。
"""
from __future__ import annotations

import yaml

from h3d.gui.config_edit import ProjectConfig


def _write_cfg(root, **over):
    data = {
        "project": {"name": "relic_BMY", "raw_dir": str(root / "00_raw"),
                    "formats": ["ply"]},
        "scale": {"enabled": True, "reference_diag": 3.7},
        "preprocess": {"voxel_down_sample": 0.02, "remove_outlier_std": 2.0},
        "mesh": {"poisson_depth": 8, "density_ratio": 0.02,
                 "poisson_threads": 1},
        "texture": {"paint_mode": "photo", "export_formats": ["obj", "glb"]},
        "photo_texture": {"enabled": False, "texture_size": 2048, "top_k": 5,
                          "samples_per_tri": 4, "poisson_blend": True,
                          "icp_align": True},
        "sfm_solve": {"enabled": True, "max_image_size": 2400, "dense": True,
                      "dense_gate": True, "max_features": 8192,
                      "peak_threshold": 0.004},
        "quality": {"enabled": True, "coverage_threshold": 0.30},
        "runner": {"workers": 1, "x1_enabled": False, "x2_enabled": False},
        "report": {"enabled": True, "output_dir": str(root / "report")},
    }
    for key, val in over.items():
        data[key] = val
    (root / "config.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    return data


def test_from_root_reads_widget_fields(tmp_path):
    """读回后，界面控件对应的字段必须与 config.yaml 一致。"""
    (tmp_path / "00_raw" / "relic_BMY").mkdir(parents=True)
    (tmp_path / "00_raw" / "relic_BMY" / "a.ply").write_bytes(b"x")
    _write_cfg(tmp_path)
    cfg = ProjectConfig.from_root(str(tmp_path), "relic_BMY", "scene1")
    assert cfg.name == "relic_BMY"
    assert cfg.dense is True               # 关键：不能被默认值 false 顶掉
    assert cfg.texture_size == 2048
    assert cfg.poisson_depth == 8
    assert cfg.voxel == 0.02
    assert cfg.top_k == 5
    assert cfg.item_id == "relic_BMY"


def test_to_yaml_keeps_non_widget_fields(tmp_path):
    """非控件参数（max_features / poisson_threads / workers）必须原样保留。"""
    (tmp_path / "00_raw" / "relic_BMY").mkdir(parents=True)
    _write_cfg(tmp_path)
    cfg = ProjectConfig.from_root(str(tmp_path), "relic_BMY", "scene1")
    cfg.to_yaml(str(tmp_path / "config.yaml"))
    out = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert out["sfm_solve"]["dense"] is True
    assert out["sfm_solve"]["max_features"] == 8192
    assert out["sfm_solve"]["max_image_size"] == 2400
    assert out["mesh"]["poisson_threads"] == 1
    assert out["runner"]["workers"] == 1
    assert out["quality"]["coverage_threshold"] == 0.30


def test_to_yaml_writes_widget_fields(tmp_path):
    """改动控件值后必须真的落盘（原来 top_k / 每面采样根本不写）。"""
    (tmp_path / "00_raw" / "relic_BMY").mkdir(parents=True)
    _write_cfg(tmp_path)
    cfg = ProjectConfig.from_root(str(tmp_path), "relic_BMY", "scene1")
    cfg.top_k = 3
    cfg.samples_per_tri = 6
    cfg.poisson_blend = False
    cfg.quality_enabled = False
    cfg.density_ratio = 0.05
    cfg.dense = False
    cfg.to_yaml(str(tmp_path / "config.yaml"))
    out = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert out["photo_texture"]["top_k"] == 3
    assert out["photo_texture"]["samples_per_tri"] == 6
    assert out["photo_texture"]["poisson_blend"] is False
    assert out["quality"]["enabled"] is False
    assert out["mesh"]["density_ratio"] == 0.05
    assert out["sfm_solve"]["dense"] is False


def test_from_root_tolerates_broken_yaml(tmp_path):
    """损坏 / 空配置不得让「打开项目」崩掉。"""
    (tmp_path / "config.yaml").write_text("project: [unclosed",
                                          encoding="utf-8")
    cfg = ProjectConfig.from_root(str(tmp_path), "x", "scene1")
    assert cfg.root_dir == str(tmp_path)
    assert cfg.base is None            # 解析失败不留半份底稿
    assert cfg.dense is False          # 回落默认值，可正常开界面


def test_from_root_without_yaml_uses_defaults(tmp_path):
    cfg = ProjectConfig.from_root(str(tmp_path), "proj", "scene2")
    assert cfg.name == "proj"
    assert cfg.scene == "scene2"
    assert cfg.base is None
