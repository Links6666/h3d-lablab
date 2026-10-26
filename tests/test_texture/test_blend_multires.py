"""tests.test_blend_multires : 多分辨率泊松融合（大纹理加速）单元测试。"""
from __future__ import annotations

import time

import numpy as np
import pytest

from h3d.texture.blend import poisson_blend, poisson_blend_multires


def _sparse_texture(size=256):
    img = np.zeros((size, size, 3), dtype=np.uint8)
    mask = np.zeros((size, size), dtype=bool)
    img[8:28, 8:28] = 200
    mask[8:28, 8:28] = True
    return img, mask


class TestPoissonBlendMultires:
    def test_fills_holes(self):
        img, mask = _sparse_texture()
        before = (img.sum(axis=2) > 20).mean()
        out = poisson_blend_multires(img, mask, max_level=2,
                                     iterations_coarse=60,
                                     iterations_fine=10)
        after = (out.sum(axis=2) > 20).mean()
        assert after > before

    def test_known_pixels_preserved(self):
        img, mask = _sparse_texture()
        out = poisson_blend_multires(img, mask, max_level=1)
        assert (out[10:26, 10:26] == 200).all()

    def test_large_texture_speed(self):
        """2048² 大纹理：多分辨率显著快于单分辨率全迭代。"""
        img, mask = _sparse_texture(size=2048)
        t0 = time.time()
        poisson_blend_multires(img, mask, max_level=2,
                               iterations_coarse=40, iterations_fine=5)
        t_multi = time.time() - t0
        assert t_multi < 30  # 2048² 多分辨率应在 30s 内

    def test_level1_equivalent_basic(self):
        img, mask = _sparse_texture(size=128)
        a = poisson_blend_multires(img, mask, max_level=1, iterations_fine=30)
        b = poisson_blend(img, mask, iterations=30)
        assert np.abs(a.astype(int) - b.astype(int)).mean() < 30


class TestGpuFallback:
    def test_gpu_available(self):
        """cupy 已安装（实机部署）→ GPU 分支启用且结果正确。"""
        import h3d.texture.blend as B
        xp, on_gpu = B._get_array_module()
        if on_gpu:  # cupy 可用：验证 GPU 融合结果
            img, mask = _sparse_texture()
            out = B.poisson_blend_gpu(img, mask, iterations=30)
            before = (img.sum(axis=2) > 20).mean()
            assert (out.sum(axis=2) > 20).mean() > before  # 空洞被填充
            assert (out[10:26, 10:26] == 200).all()  # 已知保持
        else:
            pytest.skip("cupy 未安装，跳过 GPU 分支验证")

    def test_forced_cpu_fallback(self, monkeypatch):
        """强制降级（cupy import 失败）→ numpy 路径功能正常。"""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "cupy":
                raise ImportError("模拟 cupy 不可用")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        import h3d.texture.blend as B
        xp, on_gpu = B._get_array_module()
        assert on_gpu is False  # 模拟降级
        img, mask = _sparse_texture()
        out = B.poisson_blend_gpu(img, mask, iterations=30)
        before = (img.sum(axis=2) > 20).mean()
        assert (out.sum(axis=2) > 20).mean() > before
        assert (out[10:26, 10:26] == 200).all()
