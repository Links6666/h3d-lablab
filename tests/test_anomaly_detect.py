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


class TestDetectionFloorIsReported:
    """「灵敏度不足」要变成一个可读的数字，而不是一句免责声明。

    真机诊断（2026-10-31，1.1.12）：80 texel / ΔE≈120 的斑块在三个位置只中一个，
    漏掉的两处 W=201 尺度上局部纹理尺度 σ_L 分别是 30.3 与 53.2，而中的那处只有
    15.7 —— 斑块残差都是 97~113，z 全被 σ 吃掉。这不是估计器坏了，是那两处的
    局部纹理本来就有那么大。

    所以指标里必须带「本件在这个尺度上的纹理尺度有多大」：同一个 ΔE，在 σ_L=15
    的地方是 6σ 异常，在 σ_L=53 的地方只有 2σ。不报这个数，用户只能看到「没检出」
    却不知道是「没有」还是「看不出来」。
    """

    def test_sigma_median_follows_texture_strength(self):
        """σ_L 必须跟着「这块表面有多不平」走，而不是一个常数。

        改按岛归一化之后，σ_L 的口径从「局部块残差的分位数」变成「本岛自身的
        离散度」——它包含岛上的明暗渐变（真实存在，但局部中位数背景会把它减掉）。
        所以这里用三种底分别锁：**纯色**落在地板上、**渐变**明显更高、**条纹**
        最高。只断言「纯色 == 2.0」会漏掉后两者，只断言单调又锁不住地板。
        """
        from h3d.anomaly import detect_atlas_anomalies

        flat = np.full((512, 512, 3), 128, np.uint8)
        s_flat = detect_atlas_anomalies(flat, _round_island(512)).metrics["sigma_median"]
        s_grad = detect_atlas_anomalies(
            _gradient(512), _round_island(512)).metrics["sigma_median"]
        s_stripe = detect_atlas_anomalies(
            _stripes(512, amp=10), _round_island(512)).metrics["sigma_median"]

        assert s_flat == pytest.approx(2.0, abs=0.1), (
            f"纯色底的 σ_L 应落在地板 2.0（刚可辨 ΔE），实得 {s_flat}")
        assert s_grad > 2.5, f"渐变底的 σ_L 只有 {s_grad}，没反映岛上的明暗变化"
        assert s_stripe > s_grad, f"条纹 {s_stripe} 不该低于渐变 {s_grad}"


def _mosaic(size: int = 512, cell: int = 112, pad: int = 14,
            seed: int = 20261031) -> tuple[np.ndarray, np.ndarray]:
    """多岛拼贴图集：每块岛底色不同、各自带一点渐变与采集噪声，**全都没有病害**。

    为什么必须用这个夹具而不是「一个大圆」：真机图集（1.1.12）是 **3690 个 UV 岛**
    拼成的马赛克，中位 69 texel，全图集**没有任何纹素离岛边界超过 75 texel** ——
    最小的 31 窗口对一半以上的纹素都跨岛，最大的 201 窗口则处处跨岛。用单岛
    大圆测，「跨岛比较」这条错误在夹具里根本不存在，测试永远是绿的。

    每块岛的底色相差几十 Lab 单位（石像不同部位、不同光照烘焙），这在实物上
    完全正常；把它们拿来互相比较，必然把「这块岛比邻居深」记成异常。
    """
    rng = np.random.default_rng(seed)
    img = np.zeros((size, size, 3), np.float32)
    island = np.zeros((size, size), bool)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    k = 0
    for y0 in range(pad, size - cell, cell):
        for x0 in range(pad, size - cell, cell):
            k += 1
            sl = (slice(y0, y0 + cell - pad), slice(x0, x0 + cell - pad))
            base = rng.uniform(70, 200, 3)
            # 岛内自己的平缓渐变（模拟一件器物上的光照/曲率），幅度很小
            gy = rng.uniform(-6, 6) * (yy[sl] - yy[sl].mean()) / cell
            gx = rng.uniform(-6, 6) * (xx[sl] - xx[sl].mean()) / cell
            patch = base[None, None, :] + (gx + gy)[..., None]
            patch = patch + rng.normal(0, 1.5, patch.shape)      # 采集噪声
            img[sl] = patch
            island[sl] = True
    return np.clip(img, 0, 255).astype(np.uint8), island


class TestIslandsAreNotComparedToEachOther:
    """跨岛不做比较：**相邻纹素在实物上并不相邻**。

    真机裁图核对（`x3_top3.png`）发现排在最前的三处「异常」都是「一块亮 UV 岛
    被一圈暗小岛包围」—— 那不是病害，是岛间反照率差。这一类误报不是阈值能调掉
    的：它来自把图集当成了连续图像。
    """

    def test_mosaic_of_clean_islands_has_no_anomaly(self):
        from h3d.anomaly import detect_atlas_anomalies

        img, island = _mosaic()
        res = detect_atlas_anomalies(img, island)

        assert res.metrics["anomalous_ratio"] < 0.005, (
            f"干净的拼贴图集被判 {res.metrics['anomalous_ratio']:.2%} 异常 —— "
            f"跨岛比较把底色差记成了病害")
        assert res.metrics["n_regions"] == 0, (
            f"报了 {res.metrics['n_regions']} 处异常，实际一处病害都没有")

    def test_blob_inside_one_island_is_still_found(self):
        """归一化不能靠「把灵敏度调死」换来干净。

        反向对照：同一条管线上，岛内注入的斑块必须仍然被抓到。只测「干净时
        不报」，加个永久返回空掩膜的实现也能过。
        """
        from h3d.anomaly import detect_atlas_anomalies

        img, island = _mosaic()
        # 取第一块岛的中心（cell 起点 pad=14，岛宽 98）
        cy = cx = 14 + 49
        img[cy - 10:cy + 10, cx - 10:cx + 10] = np.array([240, 232, 210], np.uint8)

        res = detect_atlas_anomalies(img, island)
        assert res.mask[cy, cx], "岛内注入的斑块没被抓到 —— 归一化把灵敏度做没了"

    def test_per_island_switch_actually_diffs(self):
        """``per_island=False`` 必须真的退回旧行为（否则这个开关是假的）。

        留开关只为一件事：让 ``scripts/verify_x3_islands.py`` 能给出 A/B 数字。
        一个「设了也不生效」的配置项，比没有更糟 —— 报表会拿它当依据。
        """
        from h3d.anomaly import AnomalyConfig, detect_atlas_anomalies

        img, island = _mosaic()
        off = detect_atlas_anomalies(img, island,
                                     config=AnomalyConfig(per_island=False))
        on = detect_atlas_anomalies(img, island)
        assert off.metrics["anomalous_ratio"] > 3 * on.metrics["anomalous_ratio"], (
            f"关掉逐岛归一化后误报没有明显变大（关 {off.metrics['anomalous_ratio']:.2%}"
            f" vs 开 {on.metrics['anomalous_ratio']:.2%}）—— 开关没接线")
        # 岛数由夹具布局决定，不写死数字：写死一个数就变成「测夹具」了，
        # 真正要锁的是「两种模式下都如实报出来，且报的是同一批岛」。
        assert off.metrics["island_count"] == on.metrics["island_count"] > 1


def _mosaic(size: int = 512, cell: int = 112, pad: int = 14,
            seed: int = 20261031) -> tuple[np.ndarray, np.ndarray]:
    """多岛拼贴图集：每块岛底色不同、各自带一点渐变与采集噪声，**全都没有病害**。

    为什么必须用这个夹具而不是「一个大圆」：真机图集（1.1.12）是 **3690 个 UV 岛**
    拼成的马赛克，中位 69 texel，全图集**没有任何纹素离岛边界超过 75 texel** ——
    最小的 31 窗口对一半以上的纹素都跨岛，最大的 201 窗口则处处跨岛。用单岛
    大圆测，「跨岛比较」这条错误在夹具里根本不存在，测试永远是绿的。

    每块岛的底色相差几十 Lab 单位（石像不同部位、不同光照烘焙），这在实物上
    完全正常；把它们拿来互相比较，必然把「这块岛比邻居深」记成异常。
    """
    rng = np.random.default_rng(seed)
    img = np.zeros((size, size, 3), np.float32)
    island = np.zeros((size, size), bool)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    k = 0
    for y0 in range(pad, size - cell, cell):
        for x0 in range(pad, size - cell, cell):
            k += 1
            sl = (slice(y0, y0 + cell - pad), slice(x0, x0 + cell - pad))
            base = rng.uniform(70, 200, 3)
            # 岛内自己的平缓渐变（模拟一件器物上的光照/曲率），幅度很小
            gy = rng.uniform(-6, 6) * (yy[sl] - yy[sl].mean()) / cell
            gx = rng.uniform(-6, 6) * (xx[sl] - xx[sl].mean()) / cell
            patch = base[None, None, :] + (gx + gy)[..., None]
            patch = patch + rng.normal(0, 1.5, patch.shape)      # 采集噪声
            img[sl] = patch
            island[sl] = True
    return np.clip(img, 0, 255).astype(np.uint8), island


class TestIslandsAreNotComparedToEachOther:
    """跨岛不做比较：**相邻纹素在实物上并不相邻**。

    真机裁图核对（`x3_top3.png`）发现排在最前的三处「异常」都是「一块亮 UV 岛
    被一圈暗小岛包围」—— 那不是病害，是岛间反照率差。这一类误报不是阈值能调掉
    的：它来自把图集当成了连续图像。
    """

    def test_mosaic_of_clean_islands_has_no_anomaly(self):
        from h3d.anomaly import detect_atlas_anomalies

        img, island = _mosaic()
        res = detect_atlas_anomalies(img, island)

        assert res.metrics["anomalous_ratio"] < 0.005, (
            f"干净的拼贴图集被判 {res.metrics['anomalous_ratio']:.2%} 异常 —— "
            f"跨岛比较把底色差记成了病害")
        assert res.metrics["n_regions"] == 0, (
            f"报了 {res.metrics['n_regions']} 处异常，实际一处病害都没有")

    def test_blob_inside_one_island_is_still_found(self):
        """归一化不能靠「把灵敏度调死」换来干净。

        反向对照：同一条管线上，岛内注入的斑块必须仍然被抓到。只测「干净时
        不报」，加个永久返回空掩膜的实现也能过。
        """
        from h3d.anomaly import detect_atlas_anomalies

        img, island = _mosaic()
        # 取第一块岛的中心（cell 起点 pad=14，岛宽 98）
        cy = cx = 14 + 49
        img[cy - 10:cy + 10, cx - 10:cx + 10] = np.array([240, 232, 210], np.uint8)

        res = detect_atlas_anomalies(img, island)
        assert res.mask[cy, cx], "岛内注入的斑块没被抓到 —— 归一化把灵敏度做没了"

    def test_per_island_switch_actually_diffs(self):
        """``per_island=False`` 必须真的退回旧行为（否则这个开关是假的）。

        留开关只为一件事：让 ``scripts/verify_x3_islands.py`` 能给出 A/B 数字。
        一个「设了也不生效」的配置项，比没有更糟 —— 报表会拿它当依据。
        """
        from h3d.anomaly import AnomalyConfig, detect_atlas_anomalies

        img, island = _mosaic()
        off = detect_atlas_anomalies(img, island,
                                     config=AnomalyConfig(per_island=False))
        on = detect_atlas_anomalies(img, island)
        assert off.metrics["anomalous_ratio"] > 3 * on.metrics["anomalous_ratio"], (
            f"关掉逐岛归一化后误报没有明显变大（关 {off.metrics['anomalous_ratio']:.2%}"
            f" vs 开 {on.metrics['anomalous_ratio']:.2%}）—— 开关没接线")
        # 岛数由夹具布局决定，不写死数字：写死一个数就变成「测夹具」了，
        # 真正要锁的是「两种模式下都如实报出来，且报的是同一批岛」。
        assert off.metrics["island_count"] == on.metrics["island_count"] > 1


def _two_islands_with_tails(size: int = 512) -> tuple[np.ndarray, np.ndarray]:
    """两块岛：左岛平滑、右岛**岛内就有一处强异常区**（z 的尾巴很重）。

    真机 1.1.12 上 7 处待复核所在的岛，z 的 p99 分别是 4.06 / 5.66 / 6.96 / 10.22
    ——不是「有纹理」（周期纹会被本岛 σ 吸收，z 只有 1.3），而是岛内存在**局部
    强差异区**。按岛自适应阈值只对后者起作用：把门槛抬到这块岛自己的尾巴上沿。
    """
    rng = np.random.default_rng(7)
    img = np.zeros((size, size, 3), np.float32)
    island = np.zeros((size, size), bool)
    mid = size // 2
    gap = 16
    # 左岛：平滑底 + 一处强斑（σ≈2，斑块 z 会到 20 以上）
    left = np.array([110, 105, 95], np.float32)
    img[:, :mid - gap // 2] = left
    img[:, :mid - gap // 2] += rng.normal(0, 1.5, (size, mid - gap // 2, 3))
    img[240:290, 100:150] = np.array([190, 185, 175], np.float32)
    island[:, :mid - gap // 2] = True
    # 右岛：底色偏亮但本身起伏大（σ≈12），再叠一处 4~5σ 的局部差异
    right = np.array([150, 145, 135], np.float32)
    img[:, mid + gap // 2:] = right
    img[:, mid + gap // 2:] += rng.normal(0, 12.0, (size, size - mid - gap // 2, 3))
    img[240:290, 380:430] = np.array([205, 198, 188], np.float32)
    island[:, mid + gap // 2:] = True
    return np.clip(img, 0, 255).astype(np.uint8), island


class TestIslandAdaptiveThreshold:
    """阈值按岛自适应：``k_i = clip(岛的 z 的 p99, 全局 k, k_max)``。

    动机是语义对齐——同一个 k 意味着「比本岛常见起伏猛 3.5 倍」，但岛内 z 的尾巴
    长度千差万别（真机 p99 从 4.06 到 10.22）。只**抬不降**（低于全局 k 的一律用
    全局 k），所以平坦岛上的灵敏度一点不损失。
    """

    def test_threshold_is_lifted_on_the_island_with_a_tail(self):
        from h3d.anomaly import AnomalyConfig, detect_atlas_anomalies

        img, island = _two_islands_with_tails()
        off = detect_atlas_anomalies(img, island)          # 默认关闭
        on = detect_atlas_anomalies(img, island,
                                    config=AnomalyConfig(adaptive_k=True))

        assert off.metrics["k_max"] == off.metrics["k"], "关掉时不该有抬升"
        assert on.metrics["k_max"] > on.metrics["k"], (
            f"尾巴重的岛没有抬升门槛（k_max={on.metrics['k_max']}）")
        assert on.metrics["k_max"] <= on.metrics["k_max_cap"], "抬升越过了上限"

    def test_flat_island_keeps_the_global_threshold(self):
        """没有尾巴的岛必须原样用全局 k——否则等于偷偷降了灵敏度。"""
        from h3d.anomaly import detect_atlas_anomalies

        from h3d.anomaly import AnomalyConfig

        res = detect_atlas_anomalies(_gradient(512), _round_island(512),
                                     config=AnomalyConfig(adaptive_k=True))
        assert res.metrics["k_max"] == pytest.approx(res.metrics["k"], abs=1e-6), (
            f"平坦图集被抬到了 {res.metrics['k_max']}")

    def test_large_blob_is_not_eaten_by_the_island_tail(self):
        """**上限是必要条件，不是保险**：没有上限时「取岛内 p99」会吃掉大斑块。

        实测（未加上限）：120 texel 注入的 IoU 从 1.00 掉到 0.13、60 texel 掉到
        0.66——因为斑块占岛面积 10%，岛的 p99 就落在斑块自己的分数上，
        阈值等于「删掉这个岛最极端的 1%」，而斑块正是那 1%。
        """
        from h3d.anomaly import AnomalyConfig, detect_atlas_anomalies

        for half in (30, 60):
            img = _gradient(512)
            island = _round_island(512)
            _inject(img, 300, 200, half)
            truth = np.zeros_like(island)
            truth[200 - half:200 + half, 300 - half:300 + half] = True
            res = detect_atlas_anomalies(img, island,
                                         config=AnomalyConfig(adaptive_k=True))
            iou = (res.mask & truth).sum() / max((res.mask | truth).sum(), 1)
            assert iou >= 0.7, (
                f"{half * 2} texel 注入在自适应阈值下 IoU 只有 {iou:.2f} —— "
                f"岛内尾巴把斑块吃掉了")
            assert res.metrics["adaptive_k"] is True

    def test_adaptive_is_off_by_default(self):
        """默认关闭，且**不是因为没做完**，是因为它在这件文物上是零和的。

        实测（真机 1.1.12，11 处 ΔE=120 注入）：上限取 4.0/4.5/5.0/6.0 时，图斑
        7→5/4/3/2，召回 9/11→8/11、8/11、7/11、**4/11**。每减一处待复核都要掉
        召回——带缺陷的岛本身尾巴就重，把门槛抬到自己的尾巴上等于让真信号自己
        盖住自己。留着开关是为了让后人能重新量，不是为了打开它。
        """
        from h3d.anomaly import AnomalyConfig

        assert AnomalyConfig().adaptive_k is False
