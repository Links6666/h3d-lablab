# ADR-0029：把重建后端身份写进元数据与内容键

- 状态：已采纳
- 日期：2026-11-07
- 相关：ADR-0028（MVS 后端契约）、ADR-0001（档案层）、
  `docs/文献分析-法向量引导MVS对管线的优化方向.md` P0-3

## 背景

ADR-0028 把稠密重建做成了可替换的后端，并让 `MvsBackend` 暴露 `backend_id` /
`backend_version`。但**暴露 ≠ 落盘**：这两个字段当时没有任何消费者，重跑一次就丢。

跨期比对的前提是「同一条对比链内重建后端版本一致」。理由不是洁癖：不同后端
（甚至同一后端的不同版本）在弱纹理区「补点」的方式不同，产生的偏差是**系统性偏置**。
M3C2 的 LOD 按局部粗糙度与配准误差估计，处理的是随机噪声——它吃不掉系统性偏置，
结果就是整片弱纹理区被判成「变化」，且**无法事后分离**（真实劣化与算法换代混在一起）。

所以必须在做真实两期数据之前，先把「这期是哪个后端哪版跑的」写下来。

## 决策

三处落，缺一处就是假落地：

| # | 位置 | 写什么 | 给谁用 |
|---|---|---|---|
| 1 | `S1` 阶段的 `metrics` | `backend_id` / `backend_version` | 账本查得到 |
| 2 | `CanonicalAsset.params` | 同上 | 人查（档案、报表） |
| 3 | `content_key` 的 params | 同上 | 机器判「能不能复用/对比」 |
| 4 | `delivery_verify` | 缺标识 → WARN | 交付时拦住 |

**只写第 2 处等于没写**：报表上看得见，但复用与对比仍按旧键命中——缓存会返回
另一份点云，而没有任何报错。这与 r16 那个手写白名单是同一种失效形态。

### 为什么不动 `stage_input_key`

第一反应是「版本变了就该重算」，所以要进 `stage_input_key`。**没有这么做**，两个理由：

1. **代价与收益不对称**：进 `stage_input_key` 会让所有存量项目的 `input_key` 一次性
   失配，下次运行全量重算 S1——真机 `relic_001` 是 30 分钟。而它防的风险（换 COLMAP
   版本后仍命中旧缓存）在单机场景下极低频。
2. **语义上它不是「输入」**：`stage_input_key` 描述「进来的数据 + 参数变了没有」。
   编译器版本不属于输入。真要管「环境变了」，正确的位置是 `CACHE_VERSION` 那种显式
   版本开关，而不是把它塞进输入指纹。

所以本次只让身份进**归档层**的 `content_key`。这条与 ADR-0026（随机消费者定种子）
的分寸一致：先解决「能不能追溯」，再谈「要不要自动重算」。

### 账本有两种形状，两种都得认

`RunManifest.record(**metrics)` 把指标**扁平**写进 entry：
`{"status": "done", "backend_id": ..., "coverage_ratio": ...}`。

而 `delivery_verify` 里读的是嵌套形状 `entry["metrics"]["coverage_ratio"]`——那是
兼容更早的账本。两种都在真实项目里出现过，所以读取一律先看嵌套、再退回扁平。

只认一种的后果是「跑过但读不到」，而那恰好是最需要追溯的场景（续跑、换机器后复查）。

### 已知影响：既有资产的 content_key 会变一次

`content_key` 的 params 多了两个键 → 同一份输入算出的键与 r32 之前不同 → 下次登记
会新建 `version 2` 而不是命中 `version 1`。

这是**版本化的正常语义**（不是 bug）：内容键的成分变了，旧键就不该再被复用。代价是
既有项目会多一个版本记录，收益是以后能分出「同一件的哪一版是哪个后端跑的」。

如果需要保留旧键的可比性，做法是不要把 `backend_*` 放进 `content_key`，只放
`params`——但那会让「换后端仍命中旧缓存」，正是本 ADR 要治的病。不采用。

## 验证

| 判据 | 手段 | 结果 |
|---|---|---|
| 同一项目两次得到同一版本 | `backend_info` 两次调用 + 进程内缓存 | 一致：`COLMAP 4.1.1 (Commit a0d785f on 2026-07-17 with CUDA)` |
| 改一位则内容键变 | `content_key` 对 `backend_version` 敏感 | 变；同身份幂等 |
| 缺标识能被发现 | `delivery_verify.dense_backend_gaps` | 真机 `relic_001` 如实报 WARN |
| 不误报 | 无 S1（激光件）/ 无 dense 产物 | 跳过，不告警 |

第四条是这次特意加的：纯激光件项目（`batch3` 三件）没有 S1，不该被要求标后端；
只跑稀疏（`dense=false`）的项目也不该。判据取「有 `fused_dense.ply` **且** 有 S1 记录」。

### 真机现状：那条 WARN 是真的

`D:/h3d_relic_test` 的 S1 记于 r32 之前，账本里没有 `backend_id`。新检查对它报 WARN，
`verify` 从 22/1 变成 **22/2**。

**没有去改数据让它变绿**：缺失是事实，补法是重跑 S1（或接受它在报表上一直显示）。
这一项是 WARN 而不是 FAIL——它不阻止交付，只阻止「不知道自己在比什么就交付」。

## 后果

- `run_sfm_solve` 在 dense 成功后写两个 metrics 键（探不到版本落 `"unknown"`，不抛）；
  结构守卫 `TestSfmStageRecordsIdentity` 用 AST 盯住「这两个键必须写进 metrics」。
- `_dense_backend_identity(task, manifest)` 是唯一读取入口，两个来源都认：
  本次运行的 `task.results["S1"].metrics`，以及账本里的 `S1` 记录（续跑时后者才有）。
- `delivery_verify.dense_backend_gaps(root, man)` 是纯函数，四条单测钉住边界。
- `ColmapPatchMatch._probe_version` 加 `lru_cache`：批量跑时每件都问一次身份，
  每次都起 `colmap -h` 子进程会让批量白等十几秒。
