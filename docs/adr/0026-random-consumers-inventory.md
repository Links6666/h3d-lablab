# ADR-0026：随机消费者清点完毕 —— 补上 M1 的平面剔除，并把守卫升到「随机 API 必须定种子」

- 状态：已实装（2026-11-06）
- 相关：ADR-0020（采样定种子）、ADR-0023（采样入口唯一）、ADR-0024（配准定种子）
- 代码：`h3d.preprocess.background._BG_SEED`、`h3d.register.ransac.RANSAC_SEED`、
  `tests/test_sampling_single_source.py::TestStochasticCallsAreSeeded`
- 触发：r28/r29 收尾时问「还有没有别的随机消费者没定种子」

## 清点结果：四个消费者，三处已修，一处潜伏

| 消费者 | 随机 API | 状态 |
|---|---|---|
| M5 采样 | `sample_points_uniformly` | r24 修（`io.sample_surface`） |
| 对齐精化 / X1 / hausdorff | 同上 | r27 收敛（4 处） |
| M2 配准 | `registration_ransac_based_on_feature_matching` | r28 修 |
| **M1 平面剔除** | `segment_plane` | **这次修** |
| M3 泊松 | `create_from_point_cloud_poisson` | 早已按 `n_threads=1` 处理并留档（多线程不可复现，配置可调，后果写在 docstring 里） |

## M1 这一处为什么是"潜伏"的

`remove_background` 的默认参数是 **`max_planes=0`（不剔平面）**——
所以默认配置下这条 RANSAC 根本不跑，跑测试、跑真机都看不出来。
但 `preprocess.bg_max_planes` 一开就走这条路，而后处理是**喂给 M2 的输入**。

跨进程实测（地面 3 万点 + 悬空团块 4 千点，`max_planes=1`）：

| | 内点数 | 剩余点数 |
|---|---|---|
| 修前 · 进程 1 | **29999** | 3940 |
| 修前 · 进程 2 | 30000 | 3939 |
| 修前 · 进程 3 | 30000 | 3939 |
| 修后（三次） | 30000 | **3939 三次相同哈希** |

平面模型几乎一样（`n≈[0, 0, 1]`，`d≈1.5e-5`），差的只是**一个点**被判在面内还是面外。

**这正是本项目最防的那类漂移**：小到不触发任何断言，又足以让两次运行的点数、
覆盖率、报表对不上。和 M5 那 86.78/87.42/87.01 是同一个病，只是量级更小。

## 顺带把种子下沉到真正的消费者

原来（r28）种子定在 `register.run()` 编排层。这次把 `RANSAC_SEED` 移进
`register/ransac.py::global_registration`——**谁消费随机，谁负责定种子**。

理由不是洁癖：`global_registration` 是公开函数，未来任何新调用方（探针脚本、
别的阶段）都会绕开编排层的种子。种子跟着消费者走，才不会被"换个入口就漏定"。

（`_RANSAC_SEED` 常量随之从 `register/__init__.py` 删掉，避免两处维护——
删的时候一度把注释块复制成了三份，已清理。）

## 守卫升级：从「禁止直调」到「用了随机 API 就必须定种子」

r27 的守卫只禁止 `sample_points_uniformly` 直调。但**直调被禁了、忘了定种子照样会漂**
（M2 与 M1 都不是"直调采样"的问题）。

新守卫（`TestStochasticCallsAreSeeded`）：任何源码文件里出现下列 API 之一，
同一文件必须出现 `seed_rng(` 或 `sample_surface(`：

```
segment_plane(  registration_ransac  sample_points_uniformly(
sample_points_poisson_disk(  cluster_dbscan(
```

跑起来第一件事就抓到了 `register/ransac.py`——种子当时在编排层，消费者自己没有。
这正是这条守卫的价值：**它检查的是"随机与种子是否在同一个文件里"，而不是
"有没有人记得在某个地方调过种子"**。

（`cluster_dbscan` 目前是确定性的，列进去是防御性的：它一旦变成并行实现，
这里会替我们发现。）

## 没做的事

- **没有把 `bg_max_planes` 默认值改掉**。它默认 0 有真机理由（剔错器物自身的平面），
  这次只保证"开了也可复现"。
- **没有给泊松加种子**。它的问题是多线程归约，不是种子，已在 docstring 里说明，
  且默认单线程。
- **没有在 CI 里跑跨进程对比**。目前靠单测（同进程）+ 本 ADR 的实测记录；
  跨进程那条要靠 `scripts/verify_*` 手工验。加自动化要引入 subprocess 编排，
  收益/复杂度暂时不划算。
