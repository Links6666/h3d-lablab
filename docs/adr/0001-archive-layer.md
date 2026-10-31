# ADR-0001：文物档案层落地形态（B0）

- 状态：已接受
- 日期：2026-10-31
- 决策人：项目负责人（四问四答）+ 工程侧推荐值
- 相关：`GLOSSARY.md`、《彩绘文物数字化管线_入口重构与模块构建指南》第 1/2/9 章

## 背景

指南提出「Artifact / Epoch / CanonicalAsset」三级模型，让跨时相分析只读引用档案，
从而把「重新采集」与「重新计算」解耦。当前代码是「一件文物 = 一个项目目录」的扁平
`Task(item_id, root)`：执行器作用域以**文物**为单位，每次从零跑全流程，`paths.py`
的阶段布局被 200+ 处 `task.root/<阶段>` 硬编码拼路径。

落地前必须定四件事，否则数据结构会返工。

## 决策

### D1 本批只做 B0，不夹带 B1

只交付：公共数据契约、三级模型、只读挂载、内容哈希缓存、存量迁移脚本。
图集继承（B1）等 B0 验收后单独一批。

理由：B0 要动执行作用域与产物登记，风险最集中；B1 涉及重烘焙，其前置正是 B0 的
只读产物与哈希缓存。混在一批里出问题无法定位。

### D2 存储用文件系统 + JSON 清单，不用 SQLite

```
<项目>/
  archive/
    artifact.json              # Artifact 一条
    epochs/<epoch_id>.json     # 每时相一条
    assets/<epoch_id>__v<N>.json   # 每次登记一个不可变版本
    atlas/<atlas_version>.json     # 档案级图集登记
```

理由：
1. 已有 `paths.py` 作为路径唯一出口与原子写纪律（tmp + fsync + `os.replace`）；
2. 档案要跟项目目录一起拷贝、归档到移动硬盘 —— SQLite 会变成「目录 + 数据库」
   两套状态，异地打开必然不同步；
3. 无并发多写场景。

### D3 包裹现有布局，不替换

`阶段成果/` 与 `交付成果/` 一行不动。`CanonicalAsset` 里存的是**指向现有产物的相对
路径 + 哈希**，不是产物本身。

理由：替换式改造要同时动 `runner` / `report` / `gui` 三层，而这三层被 898 项测试覆盖。
包裹式把改动收敛在新增的 `h3d/archive/` 包内。

### D4 代号：代码冻结，新模块用新字母

见 `GLOSSARY.md` 第 1–3 节。三处冲突（`S1`、`X2`、`P0/P1`）一律以代码为准：
- 指南 S0/S1/S2 → `A0/A1/A2`
- 指南 X2（异常检测）→ `X3`（代码 `X2` 已是色差评估）
- 指南构建里程碑 P0–P6 → `B0–B6`（代码 `P1` 已是照片纹理阶段）

理由：代号已写进历史 `run_manifest.json`、`metrics_summary.csv`、论文表与项目目录名。
改代号让 1.1.0–1.1.13 的产物不可读，代价远大于新模块换个字母。

## 后果

**正面**
- 档案层可独立测试，不触碰已发版路径；`python -m pytest tests/test_archive.py` 即可验收。
- 档案目录可整包随项目迁移（`archive/` 自带全部 JSON，无外部状态）。

**负面 / 需接受**
- 执行作用域收敛到「时相」这一条工作量最大：现有 `batch_processor` 按件跑全流程，
  B0 先做**登记与缓存命中**，真正的「只跑本期」留到 B0 第二阶段（见下）。

**B0 内部切片顺序（垂直切片，每片配测试）**
1. `content_key` 内容哈希（纯函数，无 IO）
2. 三级模型 + JSON 往返（`archive/` 读写）
3. `Archive.register()` 版本自增与不可变
4. 只读挂载（写操作抛 `ReadOnlyViolation`）
5. 存量项目迁移脚本（读现有 `阶段成果/` 推断一个 Epoch）
6. 缓存命中接入 `runner`（`is_done` 之外的第二条命中路径）

## 未决（留给下一轮）

- **迁移得到的 `route` 只是布局推断，不是来源验证**。`00_raw` 里有 `.ply` 就判 A，
  但 1.1.12 真实发生过「把 SfM 稠密产物塞进 `00_raw` 冒充激光件」，于是 A 路线
  被伪造成「有绝对尺度」。迁移如实记 `scale_ref.kind="unknown"`、`qc_status="unknown"`，
  但 B3（X1 前置校验）必须补真实来源核验（复用 `dual_source_duplicate` /
  `laser_unverified` 判据），否则档案层会成为新一轮自证闭环的入口。
- Epoch 粒度：暂定「一次建模入口批处理 = 一个 Epoch」。若后续要支持「同一批数据用两套
  参数各出一版」，应改为 Epoch 下挂 `run_id`，届时需 ADR-0002。
- 基准期由谁指定：暂定「首个时相自动为基准期，可显式覆盖并留 WARNING」（已实现）。
- 图集版本冻结与失效标记：B1 落地，`CanonicalAsset.uv_atlas_ref` 已预留字段。

## 进度（B0 切片）

| 切片 | 内容 | 状态 | 测试 |
|---|---|---|---|
| 1 | `content_key` / `file_digest` 内容哈希 | ✅ | `tests/test_archive.py` 7 项 |
| 2 | 三级模型 + JSON 往返 + 字段校验 | ✅ | 同文件 8 项 |
| 3 | `Archive` 读写 / 基准期规则 / 版本自增 / 只读挂载 | ✅ | 同文件 18 项 |
| 4 | 存量迁移 `h3d.archive.migrate` + CLI `archive migrate/status` | ✅ | `tests/test_archive_migrate.py` 11 项 |
| 5 | 内容哈希接入 runner（缓存命中绕过重算） | ⬜ 待做 | — |
| 6 | GUI 打开项目时自动建档 / 时相列表 | ⬜ 待做 | — |

真机冒烟（2026-10-31，`E:\兵马俑照片\1.1.12`）：建档成功（`relic_001` / `E1` / 路线 A /
基准期），`30_dense/relic_001.ply` 的哈希 + mtime + size 前后完全一致，只读挂载可读、
`open("wb")` 抛 `ReadOnlyViolation`，二次迁移返回 `already`。
