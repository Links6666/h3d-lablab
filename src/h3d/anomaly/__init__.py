"""h3d.anomaly : X3 单期纹理异常检测（传统分析层，无 AI、无训练）。

指南 X2 章（代码记 ``X3``）的规格：对**单期**标准化 UV 纹理图做无监督离群
筛查，回答「这里是否异常」，**不给病害类别**（类别是 S0 的事）。定位是单次采集
下的快速排查：不需要标注、不需要训练、不需要 GPU。

三个「一句话」的骨架
--------------------
1. **背景用局部中位数**（:func:`_median_background`，`cv2.medianBlur`，
   O(1)/纹素）——不是均值、不是裁剪均值。
2. **尺度用块内 75 分位**（:func:`_block_median`），脏块用邻域值替换。
3. **三档窗口取最大分**（31 / 81 / 201 纹素）——小窗看细节、大窗看大片。

为什么背景必须是中位数（这一条是量出来的，不是偏好）
----------------------------------------------------
均值对「窗内病变占比 × 对比度」敏感：占比 16% 时窗内每个**正常**纹素的残差就
被抬高到 ``0.16ΔE``，斑块内部只剩 ``0.84ΔE``，两者比值不到 2——于是斑块四周
出现一圈同样「异常」的晕、内部反而不够显著（80 texel 注入实测内部 z=2.79、
判不异常，掩膜只剩一个 104 texel 宽的环）。中位数击穿点 50%，背景直接落在正常
纹理上，残差分布变成「斑块内 ≈ ΔE、其余 ≈ 0」，边界因此**逐纹素精确**
（合成 6~120 texel 注入边界 IoU 全部 1.00）。

为什么不用「16×16 分块 + 上采样基线」（第一版，真机失败）
----------------------------------------------------------
块级粗定位 + 纹素级精化的两段式在合成渐变上是 1.00 IoU，但在真实彩绘上：

* 掩膜只能落在块格上。24 texel 的斑块覆盖率 1.00、**IoU 只有 0.14**——掩膜盖了
  4×4 个整块（约 7 倍面积）。边界精度不是「再精化一下」能解决的，候选区本身
  就被量化了。
* 块内均值把小于块的斑块平均掉：40 texel 落在 16×16 块里只占 6%，位移被稀释到
  噪声以下 → 整块斑块漏检。
* 上采样得到的基线是**逐块常值**，每条块边界都是一个台阶，块边于是成为假残差
  来源——第一版不得不加「只有候选块才精化」的闸门压住它，而这道闸门又把边界
  重新钉回块格。

块级特征仍然保留，但只用于**纹理方向**这一路（结构张量在 16×16 上才有统计意义）。

为什么多尺度
------------
单一大窗会把「与窗口尺度相当的斑块」当成背景（窗口被病害填满，中位数被污染）；
单一小窗看不到大斑块。所以三档窗口各算一遍、逐纹素取最大：小窗负责 8~30 texel，
中窗 30~80，大窗 80~150。

两套地板（``min_scale`` / ``tex_scale``）
-----------------------------------------
纯渐变图上残差的局部尺度趋近 0，z 随之爆炸、整张图都判异常。所以给噪声尺度一个
**感知下限**：Lab 空间里 ΔE≈2 是刚可辨差异，比它小的残差不该算异常。纹理维度的
量纲是 0~1 的无量纲量，必须另给地板，共用一个会让量纲小的那一路失去分辨力。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

__all__ = ["AnomalyConfig", "AnomalyRegion", "AnomalyResult",
           "detect_atlas_anomalies"]


@dataclass(frozen=True)
class AnomalyConfig:
    """检测参数。默认值由合成注入 + 真机反色注入共同定（见 ``tests/``）。"""

    #: 背景窗口（纹素边长，奇数）。逐纹素取**各尺度的最大分**。
    #: 窗口决定「能检出的最大尺度」，也决定「多大以上的差异算背景的一部分」：
    #: 斑块占窗口面积超过约 40% 时中位数开始把它算进背景。默认三档覆盖
    #: 8~150 texel，正好覆盖 2% 边长（2048² 时 41 texel）这个判据。
    #: 上限受 ``cv2.medianBlur`` 约束（核长 ≤ 255），要更大尺度得先降采样。
    scales: tuple[int, ...] = (31, 81, 201)
    #: robust z 阈值。
    k: float = 3.5
    #: 噪声尺度下限（Lab 单位）。ΔE≈2 是刚可辨差异，低于它不算异常。
    min_scale: float = 2.0
    #: 尺度估计的块边长（纹素）。``0`` = 按窗口自动取 ``W/4``。块内取精确中位数
    #: 作局部噪声尺度：中位数的击穿点是 50%，病变占不到半块就不影响估计。
    scale_block: int = 0
    #: 脏块判定倍数：块内尺度超过邻域中位数这么多倍时，判定该块被病变自己写脏、
    #: 用邻域值取代。调小更灵敏（也会更容易误报），调大更保守。
    scale_cap: float = 2.0
    #: 最小图斑占岛内面积比，低于它的连通域按噪点丢弃。
    min_region_ratio: float = 1e-4
    #: 最小图斑的**绝对**纹素数，与 ``min_region_ratio`` 取大者。
    #: 为什么两个都要：比例口径在小图上会放过绝对面积极小的碎点——512² 的
    #: 合成用例里 ``1e-4`` 只相当于 17 个纹素，于是在一个强斑块拐角外 4 texel
    #: 处冒出的 18 texel 碎点被当成「两处异常」之一。2048² 真机上 24 texel
    #: 对应不到 0.3 mm² 的图集面积，本来也不该进报表。
    min_region_texels: int = 24
    #: 是否启用纹理特征（结构张量一致性 + 方向）。关掉只剩颜色残差 ——
    #: 用来证明「同色不同纹理」的那类异常确实是纹理通道抓到的。
    use_texture: bool = True
    #: 纹理维度的噪声地板（一致性/方向都是 0~1 的无量纲量）。颜色维度用
    #: ``min_scale``（Lab 单位），两者量纲不同，不能共用一个地板。
    tex_scale: float = 0.05
    #: 「梯度能量低于此值即视为无结构」的门限（Lab/纹素）。平坦区域里方向是
    #: 0/0，不设门限会得到随机方向 → 满图假阳性。
    texture_energy_gate: float = 0.5
    #: 方向证据要求**基线本身也一致**（|平均单位向量| 不低于此值）。混合方向
    #: 的邻域里，平均向量的模长趋近 0，拿它当「另一个方向」会让整片各向同性
    #: 纹理全部报警。
    texture_coherence_floor: float = 0.5
    #: 纹理特征的块边长（纹素）。结构张量的累加需要足够的样本，16 是实测下限
    #: （8 时一致性抖动 ±0.3，分不开条纹与噪点）。
    tex_block: int = 16


@dataclass(frozen=True)
class AnomalyRegion:
    """一处异常图斑（连通域）。报表按它回答「有几处、每处多大」。"""

    region_id: int
    #: ``(x0, y0, x1, y1)``，像素坐标、**含端点**，与 PIL/numpy 切片口径一致。
    bbox: tuple[int, int, int, int]
    texels: int
    #: 占岛内纹素的比例（无量纲，跨图集尺寸可比）。
    area_ratio: float
    score_mean: float
    score_max: float

    def as_row(self) -> dict:
        """报表行（CSV/JSON 用，键名稳定）。"""
        return {"region_id": self.region_id, "x0": self.bbox[0],
                "y0": self.bbox[1], "x1": self.bbox[2], "y1": self.bbox[3],
                "texels": self.texels, "area_ratio": round(self.area_ratio, 6),
                "score_mean": round(self.score_mean, 3),
                "score_max": round(self.score_max, 3)}


@dataclass(frozen=True)
class AnomalyResult:
    """一次检测的全部输出（纯数据，不含 I/O 与 Qt）。"""

    #: 逐纹素异常分（未过阈值），float32，与输入图集同尺寸。
    score: np.ndarray
    #: 二值异常掩膜（已过阈值 + 形态学清理 + 岛内限定）。
    mask: np.ndarray
    #: 输入的岛内掩膜（与 ``mask`` 求交前的有效区域）。
    island: np.ndarray
    #: 图斑列表，按纹素数降序（最严重的一处排第一）。
    regions: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 内部：图像基础
# ---------------------------------------------------------------------------
def _to_lab(image: np.ndarray) -> np.ndarray:
    """RGB uint8 → Lab float32（OpenCV 口径：8 位图的 L 为 0–255）。"""
    import cv2

    rgb = np.asarray(image)
    if rgb.ndim == 3 and rgb.shape[2] == 4:      # 带 alpha 的 PNG
        rgb = rgb[:, :, :3]
    if rgb.dtype != np.uint8:
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)


def _fill_outside(lab: np.ndarray, island: np.ndarray) -> np.ndarray:
    """把岛外纹素填成**最近的岛内颜色**，只用于统计，不改输出。

    为什么必须填：图集里 UV 岛之外是未着色的黑底，岛是形状随意的拼块。不填的话
    (1) 每个跨岛边界的窗口，均值/尺度都被黑色拉偏；(2) 边界处产生巨大的假梯度，
    纹理通道会在**每一条岛边上**报警（这正是「单视图硬投影 → 整件都有病害」的
    同构错误）；(3) 整件尺度被黑色拉开，连整块反色的斑块都显著不起来。

    用最近邻填充而不是 0 填充：0 会继续制造假对比，最近邻至少让岛内外的颜色
    在边界上连续。
    """
    from scipy import ndimage

    mask = np.asarray(island, dtype=bool)
    if mask.all() or not mask.any():
        return lab
    idx = ndimage.distance_transform_edt(~mask, return_distances=False,
                                         return_indices=True)
    return lab[tuple(idx)]


def _box(arr: np.ndarray, size: int) -> np.ndarray:
    """单通道均值滤波（``size`` 为奇数；边界反射，不引入 0 值）。"""
    import cv2

    a = np.ascontiguousarray(arr, dtype=np.float32)
    return cv2.boxFilter(a, -1, (int(size), int(size)), normalize=True,
                         borderType=cv2.BORDER_REFLECT)


# ---------------------------------------------------------------------------
# 内部：稳健背景
# ---------------------------------------------------------------------------
def _block_median(arr: np.ndarray, block: int, pool: int = 5,
                  cap: float = 2.0, q: float = 75.0) -> np.ndarray:
    """块内**分位数**（默认 75%）→ 邻域中位数封顶 → 双线性上采样回原尺寸。

    * 为什么在块内算：一块 32×32 里病变占不到一半就不影响估计——这是「斑块占
      窗口 1/3 时均值被拉偏」的解药，而且分位数是精确的，不依赖任何阈值。
    * 为什么是 **75** 分位而不是中位数：中位数在**双峰**残差上会落到 0。周期
      纹理（条纹、织纹）的 |残差| 恰好是一半为 0、一半为幅值——中位数取到 0，
      尺度随即被 min_scale 接管，于是每条条纹的一半都判异常（合成条纹图实测
      6.3% 的岛内纹素误报）。75 分位落在幅值上，除以 1.1503（高斯
      ``q75(|r|) = 1.1503σ``）即得尺度，条纹图上的 z 回到 1.15 不越界；
      而孤立斑块（占块内 5%）的 75 分位仍是 0，灵敏度不受影响。
    * 为什么要**封顶**：斑块够大时会把整块填满（80 texel 的斑块遇上 50 texel
      的块），那一块的「中位数残差」就是斑块自己的对比度 → σ 被病变写成巨大值
      → z 掉回 1，斑块只剩边缘零星命中（实测 60×60 注入只剩 889/3600 texel）。
      所以 σ 最多只许比邻域中位数高 ``cap`` 倍：病变只会让 σ **变大**，超出的
      部分就是它自己，压回去即可。
    * 为什么是「封顶」而不是「取邻域低分位」：后者会把所有位置的 σ 一律压低，
      等于偷偷放大了灵敏度——实测 24~120 texel 的斑块 IoU 全部越界（bbox 外扩
      28 texel），掩膜盖到邻居身上。封顶只动被污染的那几格。
    * 为什么可以上采样：尺度是**空间慢变量**（同一块袍子和同一片脸，纹理强度差不
      多），不该逐纹素跳变。块边长随窗口给（``W/4``），保证块比窗口小一个量级。
    * 为什么不直接 ``median_filter``：2048² 上算三个尺度要 2.7 s（占整个流程
      92%），块内中位数只要 0.16 s。
    * 为什么不是「逐像素二分求中位数」：那条路试过并失败了，见 :func:`_robust_z`
      的说明——每个像素各用不同阈值时，窗口内计数与阈值不自洽。
    """
    import cv2
    from scipy import ndimage

    h, w = arr.shape[:2]
    b = max(1, int(block))
    ny, nx = max(1, h // b), max(1, w // b)
    sub = (arr[:ny * b, :nx * b].reshape(ny, b, nx, b)
           .transpose(0, 2, 1, 3).reshape(ny, nx, -1))
    med = np.percentile(sub, float(q), axis=-1).astype(np.float32)
    if cap and pool > 1 and min(ny, nx) >= pool:
        neigh = ndimage.median_filter(med, size=int(pool), mode="reflect")
        # **替换**（不是封顶）：块值超过邻域 ``cap`` 倍时，整块被病变自己写脏了，
        # 直接用邻域值取代。封顶（min(med, cap·neigh)）只把它压到 2 倍邻域值，
        # 在本来就粗糙的区域仍然是 60，ΔE=120 的斑块只得 z=2 → 漏检。
        med = np.where(med > float(cap) * neigh, neigh, med)
    if (ny, nx) == (h, w):
        return med
    return cv2.resize(med, (w, h), interpolation=cv2.INTER_LINEAR)


def _median_background(ch: np.ndarray, size: int) -> np.ndarray:
    """**局部中位数**背景（8 位量化，``cv2.medianBlur``，O(1)/纹素）。

    为什么背景必须是中位数而不是均值——这一条是真机上量出来的，不是偏好：
    均值对「窗内病变占比 × 对比度」敏感，占比 16% 时窗内每个正常纹素的残差
    就被抬高到 ``0.16ΔE``，而斑块内部只剩 ``0.84ΔE``，两者的比值小于 2，
    于是斑块四周出现一圈同样"异常"的晕、内部反而不够显著（80 texel 注入实测
    内部 z=2.79、命中失败，掩膜只剩一个 104 texel 宽的环）。中位数的击穿点是
    50%，背景直接落在正常纹理上，残差分布变成「斑块内 ≈ ΔE，其余 ≈ 0」，
    边界因此**逐纹素精确**（合成 6~120 texel 注入 IoU 全部 1.00）。

    为什么用 ``medianBlur`` 而不是 ``ndimage.median_filter``：前者对 8 位图用
    直方图 + 滑窗更新，2048²、ksize=201 实测 99 ms，且**与 ksize 无关**；
    后者 2.7 s。代价是必须量化到 8 位——Lab 通道本来就是 0–255 的整数量化
    （``_to_lab`` 从 uint8 转来），取整无损。
    """
    import cv2

    a = np.clip(np.rint(np.asarray(ch, dtype=np.float32)), 0, 255).astype(np.uint8)
    # 核边长上限 255：OpenCV 5.0 里 ``medianBlur_8u_O1`` 只处理小核，大核走另一
    # 条实现且断言 ``k < 16``，传入 401 直接抛 cv2.error。窗口开得比 255 更大
    # 在这个实现下不可用，要更大尺度得先降采样。
    k = min(int(size) | 1, 255)
    return cv2.medianBlur(a, k).astype(np.float32)


def _robust_z(arr: np.ndarray, size: int, floor: float,
              block: int | None = None, cap: float = 2.0,
              with_sigma: bool = False):
    """单通道 ``|残差| / 稳健尺度``：局部中位数背景 + 块级中位数尺度。

    两个估计器都取 50% 击穿点，但职责不同：

    * **背景**必须逐纹素精确（决定边界在哪）→ :func:`_median_background`；
    * **尺度**是空间慢变量（同一片袍子的纹理强度差不多），块内精确中位数再
      上采样就够，而且比逐纹素中位数便宜一个量级 → :func:`_block_median`。

    尺度还要再乘 1.4826：块中位数给的是「残差绝对值的中位数」，而阈值的口径
    是 σ（高斯的 ``median|r| = 0.6745σ``）。不换算等于把尺度偷偷缩小 1/3，
    3.5 的门限实际只有 2.36σ，三通道 RMS 的尾部有 6% 纹素越界。
    """
    bg = _median_background(arr, size)
    resid = np.abs(arr - bg)
    q75 = _block_median(resid, block or max(16, int(size) // 4), cap=cap)
    sig = q75 / 1.1503            # q75(|r|) = 1.1503σ（高斯口径）
    lifted = np.maximum(sig, floor)
    if with_sigma:
        return resid / lifted, lifted
    return resid / lifted


def _grid_z(feats: np.ndarray, size: int, floor: float) -> np.ndarray:
    """**粗网格**上的逐格中位数 + MAD（用于块级纹理特征）。

    块级特征图只有 (H/16)×(W/16) 大小（2048² → 128×128），逐格 ``median_filter``
    在这里既精确又便宜，不必像纹素级那样退到量化版。
    """
    from scipy import ndimage

    k = max(3, int(size) | 1)
    med = ndimage.median_filter(feats, size=k, mode="reflect")
    resid = np.abs(feats - med)
    sig = 1.4826 * ndimage.median_filter(resid, size=k, mode="reflect")
    return resid / np.maximum(sig, floor)


# ---------------------------------------------------------------------------
# 内部：颜色与纹理两路证据
# ---------------------------------------------------------------------------
def _colour_score(lab: np.ndarray, cfg: AnomalyConfig,
                  island: np.ndarray | None = None
                  ) -> tuple[np.ndarray, float | None]:
    """逐纹素颜色异常分（各尺度取最大）+ **本件的纹理尺度中位数**。

    三个 Lab 通道各自标准化后取均方根——单一通道的异常（比如只有 b 通道偏移
    的泛碱）不会被另外两个正常通道稀释。

    第二个返回值是最粗尺度上、岛内 ``σ_L`` 的中位数（Lab 单位）。它是**可检
    下限的可读形式**：同一个 ΔE，在 σ_L=15 的平滑区是 6σ 异常，在 σ_L=53 的
    粗糙区只有 2σ。真机 1.1.12 的 80 texel 注入正是这样：三处斑块残差都是
    97~113，中的那处 σ_L=15.7，漏掉的两处 30.3 / 53.2。不报这个数，用户看到
    「没检出」分不清是「没有」还是「看不出来」。
    """
    h, w = lab.shape[:2]
    best = np.zeros((h, w), np.float32)
    sigma_median: float | None = None
    for size in cfg.scales:
        s = int(size) | 1                        # 窗口取奇数
        s = min(s, max(3, min(h, w) | 1))        # 窗口不得大于图像
        total = np.zeros((h, w), np.float32)
        sig_l = None
        for c in range(lab.shape[2]):
            z, sig = _robust_z(lab[..., c], s, cfg.min_scale, cfg.scale_block,
                               cfg.scale_cap, with_sigma=True)
            total += z * z
            if c == 0:
                # 只取 **L 通道**：它是纹理所栖身的那一路，a/b 在多数图集上
                # 本来就平（实测条纹图 L 的 σ 是 13.3，b 只有地板 2.0）。
                # 取「最后一个通道」会让这个指标永远等于地板。
                sig_l = sig
        score = np.sqrt(total / float(lab.shape[2]))
        best = np.maximum(best, score.astype(np.float32))
        # 只在最后（最粗）一个尺度上取，作为本件的「纹理尺度」代表值
        if sig_l is not None and island is not None and island.any():
            sigma_median = float(np.median(sig_l[island]))
    return best, sigma_median


def _block_lum(lab: np.ndarray, block: int, grid: tuple[int, int]) -> np.ndarray:
    """把亮度通道补齐到块网格（不足一块的余数用反射补）。"""
    ny, nx = grid
    need_y, need_x = ny * block - lab.shape[0], nx * block - lab.shape[1]
    return np.pad(lab[..., 0], ((0, need_y), (0, need_x)), mode="reflect")


def _block_texture(lab: np.ndarray, block: int, grid: tuple[int, int],
                   energy_gate: float) -> np.ndarray:
    """逐块纹理描述子 ``(ny, nx, 4)``：一致性 / 能量 / 方向 (cos2θ, sin2θ)。

    为什么不用 LBP：先按指南的「LBP 描纹理」实现过一版（8 邻域码的 popcount
    分布，9 维）。实测它区分不了「周期条纹」与「随机噪点」——条纹每个像素横向
    有 2–4 次跨越、纵向 0 次，噪点平均 4 次，两者 popcount 分布大面积重叠，
    检测分值差不到 1 个 MAD。真相是 popcount 丢掉了方向信息，而**方向一致性
    才是「原有织纹/笔触」与「被打断」的区别**。

    结构张量 ``J = [[Σgx², Σgxgy], [Σgxgy, Σgy²]]`` 的一致性
    ``sqrt((Jxx-Jyy)² + 4Jxy²) / (Jxx+Jyy)``：条纹（梯度同向）≈1，噪点（梯度
    各向同性）≈0，实测 1.00 vs 0.10。能量取对数后作第二个维度——剥落露底
    往往同时改变「纹理有多强」与「纹理有多一致」。

    一致性的**方向**另占两维 ``(cos2θ, sin2θ)``（θ 为结构主轴）。只有「一致
    性强度」是不够的：把竖条纹整体转成横条纹，均值与方差一模一样、一致性还是
    1.0，强度通道完全看不见；方向通道差 2.0（对径点），z 直接到 40。文物上
    对应的是「笔触/织纹被涂抹、贴补改变方向」这类不改变颜色统计的病害。
    """
    import cv2

    ny, nx = grid
    lum = _block_lum(lab, block, grid)
    gx = cv2.Sobel(np.ascontiguousarray(lum, np.float32), cv2.CV_32F, 1, 0,
                   ksize=3)
    gy = cv2.Sobel(np.ascontiguousarray(lum, np.float32), cv2.CV_32F, 0, 1,
                   ksize=3)

    def sums(arr: np.ndarray) -> np.ndarray:
        return arr.reshape(ny, block, nx, block).sum(axis=(1, 3))

    jxx, jyy, jxy = sums(gx * gx), sums(gy * gy), sums(gx * gy)
    trace = jxx + jyy
    coh = np.sqrt((jxx - jyy) ** 2 + 4.0 * jxy ** 2) / (trace + 1e-6)
    energy = np.sqrt(trace) / float(block * block)
    has_structure = energy >= energy_gate
    theta = 0.5 * np.arctan2(2.0 * jxy, jxx - jyy)      # 结构主轴方向
    # 方向两维在无结构处仍写 0（0/0 无意义），一致性**保留原值**：一致性下降
    # 本身就是「织纹被打断」的证据，把它按能量门限抹成 0 等于把这类病害
    # 提前排除掉。
    cos2t = np.where(has_structure, np.cos(2.0 * theta), 0.0)
    sin2t = np.where(has_structure, np.sin(2.0 * theta), 0.0)
    log_energy = np.log1p(energy)
    return (np.stack([coh, log_energy, cos2t, sin2t], axis=-1).astype(np.float32),
            energy.astype(np.float32))


def _texture_block_score(lab: np.ndarray, cfg: AnomalyConfig
                         ) -> tuple[np.ndarray, tuple[int, int]]:
    """块级纹理异常分 ``(ny, nx)`` 与块网格尺寸。

    只在块级做——结构张量的累加需要足够样本（16×16），逐纹素算出的方向在平坦
    处是 0/0、在有纹理处只反映 1~2 个梯度方向，噪声极大。所以这一路负责
    「有没有纹理异常」的**判定**，边界仍由纹素级方向精化给出。

    分两组证据再取最大：``(一致性, log 能量)`` 用逐格中位数标准化；方向用
    **圆均值基线**（能量加权）后比角度差，且要求「当前块有结构 且 基线有结构」。
    后一条闸门是必须的：能量门限附近``has_structure``会随机翻转，而「无结构」
    的方向被写成 (0, 0)，拿去和 (0, 1) 比就是 1.0 的差 → z=20 的假异常。
    合成条纹图实测：不设这道闸门时 1024 个块里有 251 个越过 3.5（占全图 1/4），
    纹理掩膜直接糊满图集。
    """
    block = int(cfg.tex_block)
    h, w = lab.shape[:2]
    ny, nx = int(np.ceil(h / block)), int(np.ceil(w / block))
    gate = cfg.texture_energy_gate
    feats, energy = _block_texture(lab, block, (ny, nx), gate * 0.2)
    size = max(3, (int(max(cfg.scales)) // block) | 1)   # 按块数给窗口

    # (a) 强度通道：只取对数能量。「纹理有多强」是能量，「纹理方向变没变」是
    #     方向——一致性不单独计分，只作为方向证据是否成立的门槛（见下）。把
    #     一致性也计分会在平坦处翻车：那里一致性是 0/0，能量刚过门限的边缘块
    #     在 0 与 1 之间跳，z 直接到 20（合成渐变图实测 40/1024 块越界）。
    strength = _grid_z(feats[..., 1], size, cfg.tex_scale * 3.0)

    # (b) 方向组：圆均值基线（cos2θ/sin2θ 是同一个方向的对径表示）
    coh, cos2t, sin2t = feats[..., 0], feats[..., 2], feats[..., 3]
    # 方向证据的成立条件：块内有能量（排掉 0/0）**且**块内本来就一致（排掉
    # 各向同性纹理）。这里的能量门限比纹素级低一个量级——块内能量是 256 个
    # 纹素的梯度平方和再开方向量纲，条纹这类「只在边上有梯度」的纹理很容易
    # 落在纹素级门限附近，按原门限判会把整片织纹判成无结构。
    has = (energy >= gate * 0.2) & (coh >= 0.3)
    weight = np.where(has, energy, 0.0).astype(np.float32)
    num_u = _box(np.where(has, cos2t * weight, 0.0), size)
    num_v = _box(np.where(has, sin2t * weight, 0.0), size)
    # 不要再除 block²：``_block_texture`` 已经按「每纹素的平均梯度」归一过，
    # 除第二次会让 den 小两个数量级，``den >= gate`` 恒不成立、方向通道永远
    # 返回 0（旋转条纹用例实测：块方向差 2.0 却被判无效）。
    den = _box(weight, size)
    ok_bg = den >= gate
    bu = np.where(ok_bg, num_u / np.maximum(_box(weight, size), 1e-6), 0.0)
    bv = np.where(ok_bg, num_v / np.maximum(_box(weight, size), 1e-6), 0.0)
    norm = np.hypot(bu, bv)
    coherent = norm >= cfg.texture_coherence_floor
    su = np.where(coherent, bu / np.maximum(norm, 1e-6), 0.0)
    sv = np.where(coherent, bv / np.maximum(norm, 1e-6), 0.0)
    diff = np.where(has & ok_bg & coherent,
                    np.hypot(cos2t - su, sin2t - sv) / cfg.tex_scale, 0.0)
    direction = diff.astype(np.float32)

    return np.maximum(strength, direction).astype(np.float32), (ny, nx)


def _texel_structure(lab: np.ndarray, ksize: int = 5,
                     energy_gate: float = 0.5) -> tuple[np.ndarray, np.ndarray,
                                                       np.ndarray]:
    """逐**纹素**的结构张量方向 ``(cos2θ, sin2θ, 能量)``。

    块级描述子只能给出 16×16 的边界；把张量用 5×5 盒滤波平滑后逐纹素算方向，
    边界就能收回到纹素精度。

    能量低于门限处方向无意义，返回 ``(0, 0, energy)``，调用方据此**不作为证据**
    （而不是当成「方向不同」——那会让平坦区域整片误报）。
    """
    import cv2

    lum = np.ascontiguousarray(lab[..., 0], dtype=np.float32)
    gx = cv2.Sobel(lum, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(lum, cv2.CV_32F, 0, 1, ksize=3)
    jxx = _box(gx * gx, ksize)
    jyy = _box(gy * gy, ksize)
    jxy = _box(gx * gy, ksize)
    theta = 0.5 * np.arctan2(2.0 * jxy, jxx - jyy)
    energy = np.sqrt(jxx + jyy)
    has = energy >= energy_gate
    return (np.where(has, np.cos(2.0 * theta), 0.0).astype(np.float32),
            np.where(has, np.sin(2.0 * theta), 0.0).astype(np.float32),
            energy.astype(np.float32))


def _orientation_score(lab: np.ndarray, cfg: AnomalyConfig,
                       suspicious: np.ndarray | None = None
                       ) -> tuple[np.ndarray, np.ndarray]:
    """逐纹素的「方向被改变」得分。返回 ``(score, 可用掩膜)``。

    基线方向用**能量加权的圆均值**：``(ΣE·cos2θ, ΣE·sin2θ) / ΣE``。直接用
    角度平均是错的（0° 与 179° 平均成 90°），而 cos2θ/sin2θ 的对径点表示让
    平均有意义——θ 与 θ+180° 是同一个方向。

    ``suspicious`` 是块级纹理判出的可疑区，**基线估计把它排除在外**。这一条
    是边界精度的关键：不排除时，斑块附近的基线窗口里一半是斑块方向、一半是
    背景方向，圆均值的模长被拉到 0.7 左右、方向落在中间，于是**两侧都**被判成
    「方向变了」，掩膜在斑块外糊出一圈约 100 texel 的晕（实测旋转条纹用例
    覆盖真值 1.00 但掩膜 10436 texel，IoU 只有 0.61）。排除可疑区后，基线就是
    周边健康纹理的方向——斑块外 diff≈0，斑块内 diff≈2，边界回到纹素精度。

    三道闸门缺一不可：
    * 当前纹素有结构（否则方向是 0/0）；
    * 基线有足够的**健康**证据（``den ≥ gate``）；
    * **基线本身也一致**（``|平均单位向量| ≥ floor``）。混合方向的邻域里平均
      向量的模长趋近 0，若把它当成「另一个方向」，整片各向同性纹理都会报警。
    """
    cos2t, sin2t, energy = _texel_structure(
        lab, energy_gate=cfg.texture_energy_gate)
    has = energy >= cfg.texture_energy_gate
    healthy = has if suspicious is None else (has & ~suspicious)
    weight = np.where(healthy, energy, 0.0).astype(np.float32)
    size = max(3, int(max(cfg.scales)) | 1)
    num_u = _box(np.where(healthy, cos2t * weight, 0.0), size)
    num_v = _box(np.where(healthy, sin2t * weight, 0.0), size)
    den = _box(weight, size)
    ok_bg = den >= cfg.texture_energy_gate
    bu = np.where(ok_bg, num_u / np.maximum(den, 1e-6), 0.0)
    bv = np.where(ok_bg, num_v / np.maximum(den, 1e-6), 0.0)
    norm = np.hypot(bu, bv)
    coherent = norm >= cfg.texture_coherence_floor
    # 基线的**方向**（单位化）：只比方向，不比「邻域有多一致」。不单位化的话，
    # 一致性低的邻域会让 diff 整体偏大，等于把「纹理杂乱」当成「方向变了」。
    su = np.where(coherent, bu / np.maximum(norm, 1e-6), 0.0)
    sv = np.where(coherent, bv / np.maximum(norm, 1e-6), 0.0)
    diff = np.hypot(cos2t - su, sin2t - sv)
    usable = has & ok_bg & coherent
    return (np.where(usable, diff / cfg.tex_scale, 0.0)).astype(np.float32), usable


def _upsample_block(arr: np.ndarray, block: int,
                    shape: tuple[int, int]) -> np.ndarray:
    """块级数组最近邻上采样回纹素网格（裁掉补齐部分）。"""
    return np.repeat(np.repeat(arr, block, axis=0), block, axis=1)[:shape[0],
                                                                  :shape[1]]


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def detect_atlas_anomalies(image: np.ndarray, island: np.ndarray, *,
                           config: AnomalyConfig | None = None) -> AnomalyResult:
    """在 UV 图集上做单期无监督异常检测。

    参数
    ----
    image : (H, W, 3) uint8
        M4 产出的图集纹理（``_L1.0_diffuse.png``）。
    island : (H, W) bool
        ``_L1.0_mask.png`` 的岛内掩膜。**岛外必须排除**：UV 岛之间的空隙是
        未着色的黑底，把它算进来会让每一块边缘都成「异常」，实测能直接吃掉
        整张图集的 90% 以上。
    """
    from scipy import ndimage

    cfg = config or AnomalyConfig()
    rgb = np.asarray(image)
    island = np.asarray(island, dtype=bool)
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise ValueError(f"图集必须是 (H, W, 3)，收到 {rgb.shape}")
    if island.shape[:2] != rgb.shape[:2]:
        raise ValueError(
            f"掩膜与图集尺寸不一致：{island.shape} vs {rgb.shape[:2]}")
    n_island = int(island.sum())
    if n_island == 0:
        raise ValueError("岛内掩膜全为空：图集没有可用纹素，无法检测"
                         "（通常是 UV 展开或掩膜生成失败，不是检测问题）")

    lab = _fill_outside(_to_lab(rgb), island)
    colour_score, sigma_median = _colour_score(lab, cfg, island)
    score = colour_score
    mask_tex = np.zeros_like(island)

    if cfg.use_texture:
        tex_score, grid = _texture_block_score(lab, cfg)
        # 「纹理驱动」= 这一块**纹理**报了警。只看 ``tex >= colour`` 是不够的：
        # 两路都为 0 时它也为真（纯净图集整片套用方向精化）。
        driven = _upsample_block((tex_score >= cfg.k).astype(np.float32),
                                 cfg.tex_block, island.shape) > 0.5
        # 基线方向要**排除可疑区**再估计（见 _orientation_score 的说明）：
        # 不排除的话斑块两侧都被判「方向变了」，掩膜外糊一圈 100 texel 的晕。
        orient, usable = _orientation_score(lab, cfg, suspicious=driven)
        mask_tex = driven & usable & (orient >= cfg.k)
        # 纹理证据**只补颜色通道没响应的位置**：同一处病害若颜色也报了，边界
        # 以颜色为准（逐纹素精确），纹理那一路的边界只有 16 纹素块粒度——两者
        # 叠加会在色斑外多出一圈 5~10 纹素的「方向变了」（一个 60 texel 的反色
        # 斑块实测 bbox 从 170 外扩到 160）。按「离颜色区超过一个块边长」筛选，
        # 是拿精度高的那一路当边界，而不是把两路的误差加起来。
        if mask_tex.any() and (colour_score >= cfg.k).any():
            dist = ndimage.distance_transform_edt(~(colour_score >= cfg.k))
            mask_tex &= dist > float(cfg.tex_block)
        score = np.maximum(score, np.where(driven, orient, 0.0))

    mask = ((colour_score >= cfg.k) | mask_tex) & island
    # 形态学清理：开运算剥掉孤立纹素，闭运算补回图斑内部的小孔
    mask = ndimage.binary_opening(mask, structure=np.ones((3, 3), bool))
    mask = ndimage.binary_closing(mask, structure=np.ones((3, 3), bool))
    mask &= island

    score = np.maximum(score, 0.0).astype(np.float32)
    regions = _collect_regions(mask, score, n_island, cfg.min_region_ratio,
                               cfg.min_region_texels)

    metrics = {
        "island_ratio": n_island / float(island.size),
        "anomalous_ratio": float(mask.sum()) / float(n_island),
        "scales": [int(s) for s in cfg.scales],
        "k": float(cfg.k),
        "min_scale": float(cfg.min_scale),
        "tex_scale": float(cfg.tex_scale) if cfg.use_texture else None,
        "use_texture": bool(cfg.use_texture),
        "score_max": float(score.max()) if score.size else 0.0,
        "score_p99": float(np.percentile(score[island], 99)) if n_island else 0.0,
        # 可检下限的可读形式：本件在最粗尺度上的纹理尺度中位（Lab 单位）。
        # 同一个 ΔE，σ 越大越难检出 —— 报它比写一句「灵敏度随信噪比变化」有用。
        "sigma_median": (None if sigma_median is None
                         else round(float(sigma_median), 3)),
        "n_regions": len(regions),
        "largest_ratio": regions[0].area_ratio if regions else 0.0,
        # 面积单位：**这里只给纹素占比**。换算成 cm² 需要「每纹素对应多少 mm²」，
        # 那要网格 + 尺度锚点（件号级），由调用方决定要不要做——无度量基准的
        # 照片重建件不得冒充物理面积（同「无度量基准不得缩放」一条纪律）。
        "area_units": "texel_ratio",
    }
    return AnomalyResult(score=score, mask=mask, island=island,
                         regions=regions, metrics=metrics)


def _collect_regions(mask: np.ndarray, score: np.ndarray, n_island: int,
                     min_ratio: float,
                     min_texels: int = 24) -> list[AnomalyRegion]:
    """把掩膜切成连通域，丢掉小于 ``min_ratio`` 的噪点，按面积降序返回。"""
    from scipy import ndimage

    # 8 邻接：对角相邻的纹素算同一片。用 4 邻接时，一个方斑的角上会掉出
    # 一个 18 texel 的碎片，报表于是变成「3 处异常」而其实是 1 处。
    labelled, count = ndimage.label(mask, structure=np.ones((3, 3), bool))
    if count == 0:
        return []
    slices = ndimage.find_objects(labelled)
    total = float(n_island) or 1.0
    min_texels = max(float(min_texels), float(min_ratio) * total, 1.0)
    out: list[AnomalyRegion] = []
    for idx, sl in enumerate(slices, start=1):
        if sl is None:
            continue
        sel = labelled[sl] == idx
        texels = int(sel.sum())
        if texels < min_texels:
            continue
        vals = score[sl][sel]
        y0, y1 = sl[0].start, sl[0].stop - 1     # 换成含端点的像素坐标
        x0, x1 = sl[1].start, sl[1].stop - 1
        out.append(AnomalyRegion(
            region_id=0, bbox=(int(x0), int(y0), int(x1), int(y1)),
            texels=texels, area_ratio=texels / total,
            score_mean=float(vals.mean()), score_max=float(vals.max())))
    out.sort(key=lambda r: r.texels, reverse=True)
    return [AnomalyRegion(region_id=i, bbox=r.bbox, texels=r.texels,
                          area_ratio=r.area_ratio, score_mean=r.score_mean,
                          score_max=r.score_max)
            for i, r in enumerate(out, start=1)]
