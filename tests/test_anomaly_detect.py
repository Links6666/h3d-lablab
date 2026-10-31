"""tests.test_anomaly_detect : X3 单期纹理异常检测（B2）。

背景（指南 X2 章，代码记 X3）：单期标准化 UV 纹理图的无监督离群筛查，回答
「这里是否异常」，不给病害类别。输入只用 M4 已经产出的一对图——
``_L1.0_diffuse.png``（多视图加权票决的图集）与 ``_L1.0_mask.png``（岛内掩膜）。

为什么判据必须是「注入 + 反向对照」而不是「看着像」：无监督检测最典型的失败
是**整件都异常**——热力图色彩丰富、看着挺专业，而其实什么都没检测出来。所以
每个正向用例都配一条「异常面积占比必须很小」的界，反向用例则要求平滑纹理的
异常面积接近 0。

这一组测试锁住的第一件事：**注入的色斑要被抓到，邻居不能被连坐**。
"""

from __future__ import annotations

import numpy as np
import pytest


def _gradient(size: int = 512) -> np.ndarray:
    """空间连续的三通道渐变（蓝绿偏青的陶俑底色）。

    不能用随机噪声做底：噪声会让分块特征本身就是「高频」，于是阈值被迫抬高，
    注入的平涂色斑反而不显著——测出来的东西与真实场景无关。
    """
    y, x = np.mgrid[0:size, 0:size].astype(np.float64)
    r = 120.0 + 40.0 * x / size
    g = 110.0 + 30.0 * y / size
    b = 100.0 + 20.0 * (x + y) / (2.0 * size)
    return np.clip(np.stack([r, g, b], axis=-1), 0, 255).astype(np.uint8)


def _round_island(size: int = 512, radius_ratio: float = 0.45) -> np.ndarray:
    """圆形「岛内」掩膜：模拟 UV 展开后的有效区域（岛外是空隙，不参与判定）。"""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    c = (size - 1) / 2.0
    return ((xx - c) ** 2 + (yy - c) ** 2) <= (size * radius_ratio) ** 2


def _inject(img: np.ndarray, cx: int, cy: int, half: int,
            colour=(238, 230, 196)) -> None:
    """在 (cx, cy) 贴一个 (2·half)² 的平涂色斑（就地修改）。"""
    img[cy - half:cy + half, cx - half:cx + half] = np.array(colour, np.uint8)


class TestInjectedBlobIsFound:
    def test_block_colour_jump_is_located(self):
        """平滑底 + 一个 25×25 色斑（≈5% 边长）→ 斑心必须被判异常。

        同时钉住两件事：
        * ``anomalous_ratio``（岛内异常纹素占比）不能接近整件 —— 否则「整件
          都异常」这个失败模式会以「检测成功」的样子通过；
        * 离斑块很远的正常区域不能被连坐。
        """
        from h3d.anomaly import detect_atlas_anomalies

        img = _gradient(512)
        island = _round_island(512)
        _inject(img, 256, 256, 12)

        res = detect_atlas_anomalies(img, island)

        assert res.mask[256, 256], "注入斑块中心没被判异常"
        assert res.metrics["anomalous_ratio"] < 0.05, (
            f"岛内 {res.metrics['anomalous_ratio']:.2%} 被判异常 —— 整件异常")
        # 远离斑块的岛内点（半径 ~180 texel）不该报警
        assert not res.mask[256, 76], "正常区域被连坐"


class TestCleanTextureStaysClean:
    def test_smooth_gradient_has_no_anomaly(self):
        """平滑渐变（无注入）→ 异常面积必须接近 0。

        这是反向对照。无监督检测最典型的失败不是「漏检」，而是**整件都异常**：
        热力图色彩丰富、看着挺专业，其实什么都没检测出来。纯渐变上块间差异
        在几个 Lab 单位量级，若没有「尺度下限」这条地板，robust z 会把它放大成
        全图报警（MAD→0 时 z 趋于无穷）。
        """
        from h3d.anomaly import detect_atlas_anomalies

        res = detect_atlas_anomalies(_gradient(512), _round_island(512))

        assert res.metrics["anomalous_ratio"] < 0.005, (
            f"纯净图集被判 {res.metrics['anomalous_ratio']:.2%} 异常")
        assert res.metrics["score_max"] < 3.5

    def test_normal_sensor_noise_is_not_an_anomaly(self):
        """渐变 + σ=1.5 的采集噪声 → 仍不该报异常（噪声不是病害）。

        真实图集不会干净得像渲染图：M4 的多视图加权采样本身带 ±1~2 灰度抖动。
        噪声逐纹素独立，块内均值把它平均掉，所以块级分数应当无感。
        """
        from h3d.anomaly import detect_atlas_anomalies

        rng = np.random.default_rng(20261031)
        img = _gradient(512).astype(np.int16)
        img += rng.normal(0.0, 1.5, img.shape).round().astype(np.int16)
        img = np.clip(img, 0, 255).astype(np.uint8)

        res = detect_atlas_anomalies(img, _round_island(512))

        assert res.metrics["anomalous_ratio"] < 0.005, (
            f"采集噪声被判 {res.metrics['anomalous_ratio']:.2%} 异常")


class TestScaleCoverageAndBoundary:
    @pytest.mark.parametrize("half", [3, 6, 12, 20, 40, 60])
    def test_blob_sizes_from_6_to_120_texels_are_located(self, half):
        """边长 6～120 texel 的斑块都要命中，且边界 IoU ≥ 0.7。

        两个数值各自钉住一个曾经的失败：
        * 单尺度（window=5 块 = 80 texel）时，**80 texel 的斑块完全检测不到**
          —— 它把邻域填满了（16/25 块），中位数被病害自己污染。修法是把邻域
          放大到 15 块（240 texel），异常块最多占 1/3，击穿点 50% 仍有余量。
        * 只按块出掩膜时，24 texel 的斑块 IoU 只有 0.38（掩膜盖了 4 个整块、
          1024 texel，而真值 576）。修法是纹素级精化：用邻域中位色作基线，
          在候选块内逐纹素重判，边界回到纹素精度。
        """
        from h3d.anomaly import detect_atlas_anomalies

        img = _gradient(512)
        island = _round_island(512)
        _inject(img, 300, 200, half)

        res = detect_atlas_anomalies(img, island)

        truth = np.zeros_like(island)
        truth[200 - half:200 + half, 300 - half:300 + half] = True
        inter = int((res.mask & truth).sum())
        union = int((res.mask | truth).sum())
        iou = inter / max(union, 1)

        assert res.mask[200, 300], f"{2 * half}² 斑块中心未命中"
        assert iou >= 0.7, f"{2 * half}² 斑块 IoU={iou:.2f}（边界过粗或漏检）"


def _stripes(size: int = 512, amp: int = 10, vertical: bool = True) -> np.ndarray:
    """渐变底 + 周期 6 的条纹（模拟织纹 / 笔触的方向性纹理）。"""
    g = _gradient(size).astype(np.float64)
    t = np.arange(size)
    pat = amp * np.sign(np.sin(2 * np.pi * t / 6.0))
    add = pat[None, :, None] if vertical else pat[:, None, None]
    return np.clip(g + add, 0, 255).astype(np.uint8)


class TestTextureBreakIsDetected:
    def test_rotated_texture_is_found_only_by_texture_channel(self):
        """竖条纹底里贴一块**横条纹**：颜色矩一模一样，只有纹理方向变了。

        这条测试是 Q1 选 B 路线（纹理特征）的唯一理由，判据写成双向的：

        * 关掉纹理通道 → 检测不到（证明颜色矩对这类异常**原理上**无能为力：
          条纹周期与幅度都相同，块均值、块标准差、Lab 分布全部一致）；
        * 打开纹理通道 → 检测得到，且边界 IoU ≥ 0.7。

        为什么不是「贴一块噪点」：条纹底上叠噪点，条纹还在，结构一致性只从
        1.00 掉到 0.88，测出来的其实是「能量变高」而不是「纹理被打断」。转
        90° 才是干净的对照。同理，也试过指南推荐的 LBP popcount 分布——条纹
        与噪点的 popcount 分布大面积重叠（都集中在 2–4），压根分不开。
        """
        from h3d.anomaly import AnomalyConfig, detect_atlas_anomalies

        img = _stripes(512).copy()
        half = 40
        rotated = _stripes(512, vertical=False)
        img[200 - half:200 + half, 300 - half:300 + half] = \
            rotated[200 - half:200 + half, 300 - half:300 + half]
        island = _round_island(512)

        off = detect_atlas_anomalies(
            img, island, config=AnomalyConfig(use_texture=False))
        on = detect_atlas_anomalies(
            img, island, config=AnomalyConfig(use_texture=True))

        assert not off.mask[200, 300], "颜色矩不该看见方向变化（说明用例没构造对）"
        assert on.mask[200, 300], "纹理通道没抓到方向变化"

        truth = np.zeros_like(island)
        truth[200 - half:200 + half, 300 - half:300 + half] = True
        iou = (on.mask & truth).sum() / max((on.mask | truth).sum(), 1)
        assert iou >= 0.7, f"纹理异常边界 IoU={iou:.2f}"


class TestRegionList:
    def test_two_blobs_yield_two_regions_ordered_by_area(self):
        """两个大小不同的色斑 → 两个图斑，按面积降序，bbox 与纹素数对得上。

        报表要报「有几处异常、每处多大」，只给一张掩膜是不够的；而按面积降序
        是有意的——最严重的那处必须排第一，用户才不用自己找。
        """
        from h3d.anomaly import detect_atlas_anomalies

        img = _gradient(512)
        island = _round_island(512)
        _inject(img, 200, 220, 30)     # 60×60
        _inject(img, 340, 300, 12)     # 24×24

        res = detect_atlas_anomalies(img, island)

        assert len(res.regions) == 2, [r.texels for r in res.regions]
        big, small = res.regions
        assert big.texels > small.texels
        assert big.area_ratio > small.area_ratio
        # bbox 是 (x0, y0, x1, y1)，含端点，误差容许 2 texel（形态学清理边缘）
        assert abs(big.bbox[0] - 170) <= 2 and abs(big.bbox[2] - 230) <= 2
        assert abs(big.bbox[1] - 190) <= 2 and abs(big.bbox[3] - 250) <= 2
        assert big.score_mean >= 3.5
        assert res.metrics["n_regions"] == 2

    def test_speck_below_min_region_is_dropped(self):
        """小于 ``min_region_ratio`` 的连通域按噪点丢弃，不进图斑列表。

        真实图集的边角总有零星几十个纹素的抖动；逐纹素判定会把它留下，
        报表就会变成「检测到 137 处异常」——数量本身失去了信息量。
        """
        from h3d.anomaly import AnomalyConfig, detect_atlas_anomalies

        img = _gradient(512)
        island = _round_island(512)
        _inject(img, 256, 256, 4)      # 8×8 = 64 texel
        _inject(img, 330, 210, 30)     # 60×60 = 3600 texel

        loose = detect_atlas_anomalies(img, island,
                                       config=AnomalyConfig(min_region_ratio=1e-6))
        strict = detect_atlas_anomalies(img, island,
                                        config=AnomalyConfig(min_region_ratio=1e-3))

        assert len(loose.regions) == 2
        assert len(strict.regions) == 1
        assert strict.regions[0].texels > 2000
