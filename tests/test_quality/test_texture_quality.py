"""tests.test_quality.test_texture_quality : M5 纹理质量维度单元测试。"""
from __future__ import annotations

import os

import numpy as np
import pytest
from PIL import Image

from h3d.quality.texture_quality import texture_quality


def _save_texture(tmp_path, img, name="t.png"):
    p = os.path.join(str(tmp_path), name)
    Image.fromarray(img).save(p)
    return p


class TestTextureQuality:
    def test_missing_path(self, tmp_path):
        r = texture_quality(str(tmp_path / "nope.png"))
        assert r["texture_available"] is False and r["texture_ok"] is False

    def test_none_path(self):
        r = texture_quality(None)
        assert r["texture_available"] is False

    def test_sharp_texture_ok(self, tmp_path):
        # 高频纹理：清晰、有边界、覆盖充分
        rng = np.random.default_rng(0)
        img = (rng.random((128, 128)) * 255).astype(np.uint8)
        img[100:120, 100:120] = 0  # 产生边界
        p = _save_texture(tmp_path, img)
        r = texture_quality(p)
        assert r["texture_available"] is True
        assert r["texture_sharpness"] > 3.0
        assert r["texture_coverage"] > 0.8  # 随机噪声图绝大多数像素 >20
        assert r["texture_ok"] is True

    def test_flat_texture_fails(self, tmp_path):
        # 全平（无清晰度）→ ok=False
        img = np.full((64, 64), 128, dtype=np.uint8)
        p = _save_texture(tmp_path, img)
        r = texture_quality(p)
        assert r["texture_available"] is True
        assert r["texture_ok"] is False

    def test_sparse_texture_fails_coverage(self, tmp_path):
        # 覆盖过低 → ok=False
        img = np.zeros((64, 64), dtype=np.uint8)
        img[10:12, 10:12] = 128
        p = _save_texture(tmp_path, img)
        r = texture_quality(p)
        assert r["texture_available"] is True
        assert r["texture_coverage"] < 0.005
        assert r["texture_ok"] is False

    def test_corrupt_file(self, tmp_path):
        p = os.path.join(str(tmp_path), "bad.png")
        with open(p, "wb") as fh:
            fh.write(b"not an image")
        r = texture_quality(p)
        assert r["texture_available"] is False


class TestCoverageMinLinkage:
    def test_expected_coverage(self):
        from h3d.quality.texture_quality import expected_coverage_min
        # 每面 4 采样 × 1 万面 / 1024² 像素 → 期望 ~3.8% → coverage_min ~0.76%
        c = expected_coverage_min(samples_per_tri=4, n_faces=10000,
                                  texture_size=1024)
        assert 0.005 < c < 0.02
        # 采样数增加 → 阈值提高（联动）
        c2 = expected_coverage_min(samples_per_tri=8, n_faces=10000,
                                   texture_size=1024)
        assert c2 > c

    def test_linkage_avoids_false_reject(self):
        """联动阈值随采样策略下调，稀疏采样纹理不再被高固定阈值误判。"""
        import tempfile
        img = np.zeros((128, 128), dtype=np.uint8)
        # 模拟 4 采样/面 × 2000 面 = 8000 采样点（期望覆盖 ~38%）
        rng = np.random.default_rng(3)
        img[rng.integers(0, 128, 8000), rng.integers(0, 128, 8000)] = 180
        p = os.path.join(tempfile.mkdtemp(), "sparse.png")
        Image.fromarray(img).save(p)
        from h3d.quality.texture_quality import expected_coverage_min, texture_quality
        cov_min = expected_coverage_min(samples_per_tri=4, n_faces=2000,
                                        texture_size=128)
        r = texture_quality(p, coverage_min=cov_min)
        assert r["texture_available"] is True
        # 联动阈值（~9.8%）低于期望覆盖（~38%）→ 判定达标，避免误判
        assert r["texture_coverage"] > cov_min
        assert r["texture_ok"] is True


# X1 进 accuracy.csv 的两条已迁到 tests/test_quality/test_accuracy_x1.py。
# 原因：X1 从 M6 之后移到 M5 之前，改用 build_report 一次写全，不再事后追加列
# （追加时 accuracy.csv 还不存在，`if exists` 直接跳过 → 数字反而进不了表）。
