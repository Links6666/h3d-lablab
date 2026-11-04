# ADR-0020：数据来源必须被声明，采样的种子必须被固定

- 状态：已实装（2026-11-04）
- 相关：ADR-0011（双源同源闸门）、ADR-0014（ICP 多初值）、ADR-0017（交付核验）
- 代码：`h3d.core.config.RunnerConfig.data_source`、`h3d.quality.gate.resolve_metric_units`、
  `h3d.io.sample_surface`、`h3d.gui.constraints`
- 触发：给约束面板加「数据来源」下拉

## 起点：一个开关，两种诚

`M5` 里只有一句判据：

```python
metric_units = bool(task.raw_paths)
```

`00_raw` 里**有个文件**，就被当成**有激光基准**：报表印「激光扫描 / 中误差
3.21 mm」，这个数进 5 mm 毫米门，然后被抄进论文。

可那个文件完全可能是**别的软件照片重建**出来的（Metashape / RealityCapture
导出）——坐标是任意单位，那个「mm」不成立。

r17 补过两个自证检测（`laser_unverified` / `dual_source_duplicate`），但它们都是
**事后推断**：必须同时存在 SfM 稠密模型才判得出。而用户自己知道 `00_raw` 里放的
是什么——界面上却没有地方说。

## D1：`runner.data_source`（三档）

| 取值 | 语义 | `metric_units` |
|---|---|---|
| `auto`（默认） | 旧行为：有 `00_raw` 即按米制 | `bool(raw_paths)` |
| `laser` | 用户声明是激光扫描件 | 见下（可被证据否决） |
| `photogrammetry` | 用户声明是照片重建件 | **恒 False**，与有无文件无关 |

`auto` 必须与旧实现**逐字等价**——否则老项目重跑会出现「同一份数据换了数字」，
而这种变化比缺陷更难查。

## D2：证据压过声明（硬红线）

声明是**把判断权交回用户**，不是给一个绕过诚实性检查的后门。所以
`laser` 要两个条件同时成立：

1. 真的有 `00_raw`（声明激光件却没输入 = 自相矛盾）；
2. M1 没有把它判成照片重建（`dense_source != raw_looks_like_sfm`）。

不成立时 `metric_units=False` + `declared_source_conflict=True` + WARN。

**为什么方向是「降级」而不是「报错」**：不主张米制是保守侧（少说一句），
错标米制是危险侧（多说一句且被引用）。两边代价不对称，所以错的声明也走保守侧，
但必须喊出来——否则用户以为自己勾的选项没生效，下次还会这么配。

`photogrammetry` 撞上 `raw_looks_like_sfm` 不报警：两者说的是同一件事，
一个来自用户、一个来自证据，没有矛盾。

## D3：判据名要和布尔一起落盘

只落 `metric_units` 一个布尔，`auto`（系统猜的）与 `photogrammetry`（用户声明的）
在事后**长得一模一样**。而这两者的可信度差一个量级，处理方式也相反：
前者该补数据，后者本就该如此。

所以 `metric_units_reason` 进 manifest 与 `qc_metrics.json`：

| reason | 含义 |
|---|---|
| `auto_with_raw` / `auto_without_raw` | 旧口径，系统按输入推断 |
| `declared_photogrammetry` | 用户声明，不主张米制 |
| `declared_laser` | 用户声明 + 无冲突证据 |
| `declared_laser_without_raw` | 声明与输入矛盾 |
| `declared_laser_conflicts_with_evidence` | 声明与证据矛盾 |

`qc_metrics.json` 这一份尤其重要：判门失败时 manifest 不写，它是唯一能回答
「这个 N/A 是没测，还是按声明不主张米制」的地方。

## D4：采样必须定种子（本次的意外发现）

做 `auto` ↔ `laser` 的对照实验时，两个**等价**分支算出的中误差竟然不同：

| 运行 | 声明 | 中误差（mm） |
|---|---|---|
| 1 | auto | 86.7811 |
| 2 | auto | 87.4155 |
| 3 | laser | 87.0148 |

三者 `metric_units` 都是 True，差异全部来自
`mesh.sample_points_uniformly(n)` —— 它**没有 `seed` 参数**（Open3D 0.19 经查：
`sample_points_uniformly(self, number_of_points=100, use_triangle_normal=False)`），
用进程级随机源。每次重采样到不同表面点，最近邻距离分布就变。

**小于 1% 的漂移最危险**：小到不会触发任何断言，又大到让「昨天那张表和今天这张
对不上」。而这两个数正是 `accuracy.csv` / HTML / 论文表里要被人追问
「怎么重跑出来」的。

同一个坑 2026-11-03 在**对齐精化**里已经踩过（`ctrl_points._REFINE_SEED`），
当时只修了那一条路径。所以修法不是「在 M5 里也调一下 `_seed_random`」，
而是把采样入口收敛掉：

```python
# h3d.io.mesh_io
SAMPLE_SEED = 20261104
def sample_surface(mesh, n_samples, seed=SAMPLE_SEED) -> PointCloud
def seed_sampling(seed=SAMPLE_SEED) -> None   # 只给「先定种子后自己采」的老调用点
```

「定种子 + 采样」绑在一个函数里，**没法只做一半**。接上的调用点：

| 调用点 | 为什么会漂 |
|---|---|
| `quality.metrics.surface_coverage` | 覆盖率每次重跑不同（真机 0.6335 / 0.6538 / 0.6305） |
| `quality.metrics.qc_rmse_units` | 中误差每次重跑不同（上表） |
| `runner._stage_multitemporal`（mesh→点云） | M3C2 的尺度全相对 MPS，样本换了 MPS 也换 |
| `mesh.postprocess.hausdorff_distance` | 取**最大值**，对采样位置最敏感 |
| `geo_align._seed_random` | 改为委托 `io.seed_sampling`（副本收敛） |

修后：`auto` 与 `laser` 两个等价分支**逐字相同**（87.4019），跨进程重跑也相同。

**代价**：已有项目的覆盖率/中误差会微调（<1%）。这是把「不可复现」换成
「可复现」的必然结果，写进 CHANGELOG 了。

## D5：端到端跑一遍，顺带抓到验收工具自己的两个缺陷

声明做完，用打包版跑一次整批（3 件，`data_source: photogrammetry`）验证它真的
生效。跑完立刻 `h3d verify` —— **FAIL「找不到 manifest」**。

| 缺陷 | 为什么难发现 |
|---|---|
| 核验器只搜 `90_meta`，而默认账本在 `阶段成果/output/run_manifest.json` | **它只在项目"健康"的时候报错**：有问题的项目会先因别的原因失败，把这条盖住 |
| 口径文字把「声明」混成「SfM 任意单位」 | 两种情况下读者该做的事相反，写同一句话等于把「你少跑了一步」和「你本来就没有这个东西」混为一谈 |

后半句尤其典型：`quality.run` 和 `build_report` **各写了一遍**这段措辞（注释还
写着"单一文字结论"）。加声明这一档时只改了 fallback 那一份，真正生效的那份没改
——**「同口径两处实现」就是这次要消灭的那类副本**。已收敛到
`quality.gate.scope_text(metric_units, units_plausible, rmse_reliable, reason)`，
两处都调它。

## 没做的事

- **不按 `scene` 自动推 `data_source`**。场景①②说的是技术路线，`data_source`
  说的是采集事实，两者可以交叉（例如场景①里放的是 Metashape 导出件）。
  自动推会造出第四种"系统猜"，正是这次要消灭的东西。
- **不给 `auto` 做「更多推断」**。推断链再长也判不出「用户知道但没说」的事。
- **不动 `units_plausible`**（ADR 里记过的量级自检）。它是独立的一层保险：
  声明 laser、证据也不冲突、但对角线 6.78 m —— 那时该报「单位存疑」，
  这与「来源是什么」是两个问题。真机 batch3 正好落在这一档
  （`不判门（单位存疑）`），两条判据同时生效、互不干扰。
