"""tests.test_colour.test_stage_x2_units : X2 的「数字能不能信」两条底线。

真机 1.1.12 的 X2 状态是 ``disabled``，从未跑过。2026-11-01 首验，三十张照片
**一个色卡都没有**（ArUco 0 个、亮框 0 个），而代码在这种情况下退回
``texture-random``（从纹理图随机采 24 个像素跟 ColorChecker24 比），仍然
``DONE`` 并报出 ``mean ΔE = 21.42``。

两条缺陷，各自有独立证据：

1. **单位没换算**。``colour.run`` 要 sRGB 0~1，而 ``_stage_colour_quality``
   把 PIL 读出的 0~255 直接喂进去，全部越界值被 ``np.clip`` 钳到 1.0（纯白）。
   证据：把 ColorChecker 标准值 **×255** 再喂进去，落在同一个钳位路径上，报出
   **21.4193**——与真机那个 21.42 只差 0.0007。这不是「有色偏」，是「所有颜色
   都变成白」。
2. **没有色卡仍出数**。ΔE 的定义是「实测色块 vs ColorChecker24 标准值」。
   没有色卡就没有「实测的是哪 24 块」这回事，随机采纹理算出来的是「文物颜色
   与色卡颜色的差」，物理上无意义。一个看起来正常、还能进报表的数，比没有数更糟。
"""
from __future__ import annotations

import os

import numpy as np
import pytest

from h3d.core import ColourConfig, PipelineConfig
from h3d.colour import COLORCHECKER24_SRGB


def _cfg(root):
    cfg = PipelineConfig()
    cfg.runner.x2_enabled = True
    cfg.report.enabled = False
    cfg.runner.manifest = os.path.join(str(root), "90_meta", "manifest.json")
    os.makedirs(os.path.dirname(cfg.runner.manifest), exist_ok=True)
    return cfg


def _task(root):
    from h3d.runner.task import Task

    return Task(item_id="relic_001", raw_paths=[], root=str(root))


def _texture(root, size=(256, 256), value=128):
    """写一张纹理图（X2 的输入；没有它连 SKIPPED 的理由都不一样）。"""
    from PIL import Image

    d = root / "50_tex" / "relic_001"
    os.makedirs(str(d), exist_ok=True)
    img = np.full((size[0], size[1], 3), value, np.uint8)
    p = d / "relic_001_L1.0_diffuse.png"
    Image.fromarray(img).save(str(p))
    return str(p)


class TestUnits:
    """色块值域：0~255 → 0~1 必须换算，且只能换算一次。"""

    def test_standard_values_roundtrip_is_zero(self):
        """基准：同一组标准值自比对必须是 0（ΔE 链路本身正确）。"""
        from h3d.colour import run as colour_run

        r = colour_run(COLORCHECKER24_SRGB, COLORCHECKER24_SRGB, ColourConfig())
        assert r["mean_de"] == pytest.approx(0.0, abs=1e-6)

    def test_unconverted_0_255_is_the_21_constant(self):
        """反证：忘掉换算时，默认配置（校准开）得到 ≈21.4，与真机那个数对得上。

        这条测试的存在意义是**留下证据**：以后有人看到 X2 报 21 左右，能立刻
        认出这是单位错，而不是「色彩偏差有点大」。
        """
        from h3d.colour import run as colour_run

        r = colour_run(COLORCHECKER24_SRGB * 255.0, COLORCHECKER24_SRGB,
                       ColourConfig())          # 默认 use_calibration=True
        assert 21.0 < r["mean_de"] < 22.0, r["mean_de"]

    def test_calibration_masks_the_unit_error(self):
        """关掉校准才看得见真相：同一个单位错是 46.99 而不是 21.4。

        ``use_calibration=True`` 的线性拟合会把「全被钳成白」的实测值往参考值上
        拉，于是 46.99 变成 21.42——**校准掩盖了单位错误**，让这个假数看起来更
        像个正常测量结果。这也是为什么真机那个 21.42 那么有迷惑性。
        """
        from h3d.colour import run as colour_run

        r = colour_run(COLORCHECKER24_SRGB * 255.0, COLORCHECKER24_SRGB,
                       ColourConfig(use_calibration=False))
        assert 45.0 < r["mean_de"] < 49.0, r["mean_de"]

    def test_convert_then_compare(self):
        """正确路径：把 0~255 的色块除以 255 再比，ΔE 应为 0。"""
        from h3d.colour import run as colour_run

        measured_0_255 = COLORCHECKER24_SRGB * 255.0
        r = colour_run(measured_0_255 / 255.0, COLORCHECKER24_SRGB,
                       ColourConfig(use_calibration=False))
        assert r["mean_de"] == pytest.approx(0.0, abs=1e-6)


class TestNoColorChecker:
    """没有色卡时必须不出数——随机采纹理的 ΔE 没有物理意义。"""

    def test_missing_texture_skips(self, tmp_path):
        from h3d.runner import _stage_colour_quality
        from h3d.runner.task import TaskStatus

        root = tmp_path / "阶段成果"
        res = _stage_colour_quality(_task(root), _cfg(root))
        assert res.status == TaskStatus.SKIPPED
        assert "纹理" in res.metrics["skip_reason"]

    def test_no_colorchecker_photo_skips(self, tmp_path):
        """有纹理、无照片 → SKIPPED，且理由必须指向「采集时没放色卡」。"""
        from h3d.runner import _stage_colour_quality
        from h3d.runner.task import TaskStatus

        root = tmp_path / "阶段成果"
        _texture(root)
        res = _stage_colour_quality(_task(root), _cfg(root))
        assert res.status == TaskStatus.SKIPPED, (
            f"无色卡却给出 {res.metrics.get('mean_de')} —— 这个数没有物理意义")
        assert "色卡" in res.metrics["skip_reason"]
        assert "mean_de" not in res.metrics

    def test_photos_without_checker_skip(self, tmp_path):
        """有照片但没有色卡（真机 30 张全是这种）→ 同样 SKIPPED。"""
        from PIL import Image

        from h3d.runner import _stage_colour_quality
        from h3d.runner.task import TaskStatus

        root = tmp_path / "阶段成果"
        _texture(root)
        d = root / "00_photo" / "relic_001"
        os.makedirs(str(d), exist_ok=True)
        # 纯色照片：没有 ArUco、没有白框
        Image.fromarray(np.full((120, 160, 3), 90, np.uint8)).save(
            str(d / "DSC_0001.JPG"))
        res = _stage_colour_quality(_task(root), _cfg(root))
        assert res.status == TaskStatus.SKIPPED
        assert "色卡" in res.metrics["skip_reason"]
