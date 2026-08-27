"""core.config 单元测试：默认值、yaml 解析、描述符校验、序列化往返。"""
import pytest

from h3d.core import (
    PipelineConfig,
    PreprocessConfig,
    ConfigError,
    load,
    loads,
)

YAML = """
project:
  name: rock001
  raw_dir: data/raw
preprocess:
  voxel_down_sample: 0.003
  normal_estimate_k: 20
register:
  max_iter: 1000
"""


def test_defaults():
    cfg = PipelineConfig()
    assert cfg.preprocess.voxel_down_sample == 0.005
    assert cfg.register.max_iter == 2000
    assert cfg.project.formats == ["ply", "pcd", "obj"]
    assert cfg.change.enabled is False
    assert cfg.colour.enabled is False
    assert cfg.runner.fail_isolation is True


def test_loads_parses_sections():
    cfg = loads(YAML)
    assert cfg.project.name == "rock001"
    assert cfg.preprocess.voxel_down_sample == 0.003
    assert cfg.register.max_iter == 1000


def test_load_from_file(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text(YAML, encoding="utf-8")
    cfg = load(str(p))
    assert cfg.project.name == "rock001"


def test_missing_file():
    with pytest.raises(ConfigError):
        load("no_such_dir/no_such_file.yaml")


def test_unknown_section_field_raises():
    with pytest.raises(ConfigError):
        loads("preprocess:\n  no_such_field: 1\n")


def test_unknown_top_section_raises():
    with pytest.raises(ConfigError):
        PipelineConfig(no_section={})


def test_out_of_range_raises():
    with pytest.raises(ConfigError):
        loads("preprocess:\n  voxel_down_sample: -1\n")


def test_descriptor_validates_on_assignment():
    cfg = PreprocessConfig()
    with pytest.raises(ConfigError):
        cfg.voxel_down_sample = -0.1
    cfg.voxel_down_sample = 0.01
    assert cfg.voxel_down_sample == 0.01


def test_to_dict_from_dict_roundtrip():
    cfg = loads(YAML)
    data = cfg.to_dict()
    assert data["project"]["name"] == "rock001"
    cfg2 = PipelineConfig.from_dict(data)
    assert cfg2 == cfg


def test_type_coercion():
    # yaml 可能给出整型，应安全转换为 float
    cfg = loads("preprocess:\n  voxel_down_sample: 2\n")
    assert cfg.preprocess.voxel_down_sample == 2.0
    assert isinstance(cfg.preprocess.voxel_down_sample, float)
