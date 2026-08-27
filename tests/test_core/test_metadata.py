"""core.metadata 单元测试：SHA256 校验、RunManifest 溯源与断点续跑。"""
import pytest

from h3d.core import (
    sha256_text,
    sha256_file,
    RunManifest,
    generate_run_id,
)

SHA256_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SHA256_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_sha256_empty():
    assert sha256_text("") == SHA256_EMPTY


def test_sha256_text_known():
    assert sha256_text("abc") == SHA256_ABC


def test_sha256_file(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"abc")
    assert sha256_file(str(p)) == SHA256_ABC


def test_generate_run_id_format():
    rid = generate_run_id(prefix="rock001")
    parts = rid.split("-")
    assert len(parts) == 3
    assert len(parts[0]) == 8  # YYYYMMDD
    assert parts[1] == "rock001"


def test_manifest_record_and_resume():
    m = RunManifest(config_sha256="ab12")
    assert not m.is_done("M1")
    m.record("M1", outputs=["10_clean/a.ply"], elapsed_s=1.5, rmse_mm=1.8)
    assert m.is_done("M1")
    m.record_fail("M2", "overlap < threshold")
    assert m.status("M2") == "fail"
    s = m.summary()
    assert s["done"] == 1
    assert s["failed"] == 1


def test_manifest_save_load(tmp_path):
    m = RunManifest(run_id="test-run", config_sha256="ab12")
    m.record("M1", rmse_mm=1.8)
    p = tmp_path / "manifest.json"
    m.save(str(p))
    m2 = RunManifest.load(str(p))
    assert m2.run_id == "test-run"
    assert m2.is_done("M1")
    assert m2.stages["M1"]["rmse_mm"] == 1.8


def test_load_or_create(tmp_path):
    p = tmp_path / "m.json"
    m = RunManifest.load_or_create(str(p), run_id="first")
    assert m.run_id == "first"
    m.save(str(p))
    m2 = RunManifest.load_or_create(str(p))
    assert m2.run_id == "first"


def test_config_sha256_carried():
    m = RunManifest(config_sha256="cafe1234")
    assert m.to_dict()["config_sha256"] == "cafe1234"
