# ADR-0017：交付核验的判据与单一出口

- 状态：已实装（2026-11-03）
- 相关：ADR-0010（中文路径 GLB 静默失败）、ADR-0007（阶段清单单一真源）
- 代码：`src/h3d/delivery_verify.py`、`h3d.cli verify`、`scripts/verify_delivery.py`

## 背景

`scripts/verify_delivery.py` 是上一片为了核验交付链路写的，当时只在单件项目
（`relic_001`）上跑过。多件批是唯一没验的路径——而它最容易静默丢东西：
**清单说交付了 3 件、磁盘只有 2 件**，脚本照样 `PASS`。

真机 `batch3`（3 件、顶点色）第一次跑就暴露了三个问题，全都在「脚本以为
只有一件、且一定有图集」这个默认假设上。

## D1：件数覆盖 —— M6 done 的件必须有交付子目录

```python
m6_done = {k.split("/")[0] for k, e in stages.items()
           if k.endswith("/M6") and e.get("status") == "done"}
missing = m6_done - set(交付目录下的子目录名)
```

判据取**五态契约**：只有 M6 `done` 的件才要求交付；M6 `skipped`/`failed` 不是
缺陷。反向多出来的件目录只 `WARN`（上一次运行的残留，不该判失败——它不在
本次清单里，用户可能就是要留着）。

## D2：图集该不该有，问 M4 的记录，不能一律要求

第一版直接 `check(有 PNG)`。顶点色项目（`paint_mode=vertex`，没有照片的激光件）
本来就没有图集，于是 `batch3` 报 `[FAIL] 交付包里没有图集 PNG`。

**假 FAIL 比缺检查更糟**：它教会人「FAIL 可以先放过去」，那真 FAIL 会被一起忽略。

但只改用 `uv_baked` 判断也不对——照片模式的老 manifest **没有这个键**
（真机 `relic_001` 就是），那样会把照片项目误判成顶点色，「交付包丢了图集」
这件真缺陷被降级成「顶点色不需要图集」。**假阴性比假阳性更危险。**

`atlas_expectation(m4_entries)` 的优先级（每条都来自真机 manifest 的实际形状）：

| 序 | 判据 | 结论 |
|---|---|---|
| 1 | `paint_mode_effective` / `paint_mode` == `photo` | 要 |
| 2 | `uv_baked is True` | 要 |
| 3 | `atlas_png` 有值（正面证据，最硬） | 要 |
| 4 | `paint_mode` == `vertex` 或 `uv_baked is False` | 不要 |
| 5 | 以上都没有 | **unknown**，不猜 |

多件混合（一批里既有照片件又有激光件）取「要」：只要一件需要图集，交付包里
就该有，否则那一件的纹理就丢了。

「没证据」与「证据显示有问题」必须分开：`colored` 字段缺失只 `WARN`，
只有显式 `False` 才 `FAIL`（顶点色模式下没着色 = 交付灰模）。

## D3：每件都要查，不能只看第一份

`acc = [f for f in files if basename == "accuracy.csv"]` 然后 `acc[0]` ——
多件批下第二、三件的表结构坏了完全不会被发现。改为逐份检查：

- 每份自身行列一致、至少有数据行（只有表头 = 空表）
- 各件的**表头列数一致**（不一致时论文表格合并会错位，逐件点名）

## D4：读 M6 自己记的账

`manifest 产物都在磁盘` 这一层看不见**根本没产出的格式**——它不在 `outputs`
里，无从比对。r16 就是这么漏的：`publish.formats` 要了 glb，M4 没产，
`formats_missing` 记下来了，没人读。现在读：

- `formats_missing` 非空 → `FAIL` 并点名「哪件缺哪个格式」
- `texture_orphan is True` → `FAIL`（有图集但 obj/glb 引不到）
- 字段不存在 → `WARN`（老 manifest 不硬判）

## D5：布局两种都认

新布局 `<root>/阶段成果/90_meta/` + `<root>/交付成果/`；
旧布局 `<root>/90_meta/` + `<root>/80_export/`。

第一版只找 `root/90_meta`，`batch3` 直接报 `[FAIL] 找不到 manifest`——
**把正常项目报成坏的**。判据同 `paths.py`：看这一层是否叫 `阶段成果`。

## D6：规则只有一个出口

核验逻辑移到 `src/h3d/delivery_verify.py`，两个入口共用：

```
h3d.cli verify <项目目录>        # 产品入口
h3d verify -c config.yaml       # 从 raw_dir 推项目根
scripts/verify_delivery.py      # 仓库里的薄包装
```

历史上「阶段清单」被手抄成八份、四份漏了 X3（ADR-0007），核验逻辑不该重演
同一件事——它恰恰是**发现这类遗漏的那把尺子**，自己漂移了就没人管得住。

`project_root_from_config(cfg)` 同样按「上一级是否叫 阶段成果」判新旧布局。

## 验证

真机（`h3d verify`）：

| 项目 | 件数 | 模式 | 结果 |
|---|---|---|---|
| `output/_build/batch3` | 3 | 顶点色（新布局） | **PASS 22 / WARN 0 / FAIL 0** |
| `D:/h3d_relic_test` | 1 | 照片（旧布局） | **PASS 22 / WARN 0 / FAIL 0** |

破坏性验证（把 `ping_c.glb` 移走再放回）：核验报
`[FAIL] manifest 有 1 个产物不在磁盘上（断点续跑会永远失效）：ping_c/M6→ping_c.glb`，
还原后回到 `PASS 22`。**尺子有牙，不是印着好看。**

测试 1323 项绿（新增 23 项：件数覆盖 5、图集判据 4、逐份表检查 2、
格式完整性 2、新布局 1、CLI 入口 4、photo 无 uv_baked 1、数值核对 5）。

## D7：数值也要核，不只核结构

结构对得上不代表数对得上。这张表是**论文直接引用**的那张：手改一个数、或者某次
重算后 CSV 没重新生成，结构检查全都看不见。

`ACCURACY_NUMERIC_SOURCES` 只收「manifest 里有唯一来源」的 8 列：

| 列 | 来源 | 百分比 |
|---|---|---|
| 中误差(mm) | M5 `rmse_mm` | 否 |
| 覆盖率 | M5 `coverage` | 是 |
| 相对中误差(%对角线) | M5 `rmse_rel` | 是 |
| 纹理覆盖率 | M4 `coverage_ratio` | 否 |
| 异常面积(岛内) | M5 `anomaly_ratio` | 是 |
| 图斑数 | M5 `anomaly_regions` | 否 |
| 纹理清晰度 | M5 `texture_sharpness` | 否 |
| 纹理接缝 | M5 `texture_seam` | 否 |

后两列（2026-11-04 补）在顶点色项目里是 `N/A`，读不成数会自动跳过——
这正是「`N/A` 不是对不上」那条判据的用处。

`中误差口径` / `判定` / `纹理达标` 是文字与**派生结论**，由口径逻辑决定，不是可
独立复算的数——把它们也纳入核对等于把同一段判断抄两遍（r16 那类「手写副本」）。

三条判据细节，每条都对应一种会误伤的写法：

1. **容差跟着单元格自己的显示精度走**（半个最小刻度）。报表写的是四舍五入后的
   值，拿全精度去比一定对不上——`62.11` vs `62.1%` 会把每条都报成假 FAIL。
2. **`N/A` / `未筛查` / `未标定` 不是「对不上」**。它们是合法状态；把「没数」
   当「数错了」会逼着人去凑数，而那正是伪造数据的起点。
3. **列名漂移要主动查**：`accuracy_column_drift()` 对照 `ACCURACY_HEADERS`，
   名字对不上就 `WARN`。列一改，数值核对会**静默跳过**——那是最难发现的失效
   （r16 的根因就是「改了产出的键、忘了改消费方」）。

真机：`relic_001` 核过 **12** 个数值、`batch3` 核过 **9** 个，都是
`PASS 23 / WARN 0 / FAIL 0`。

破坏性验证（把 `relic_001` 的覆盖率 62.1% 改成 75.0%）：

```
[FAIL] accuracy.csv 数值与 manifest 对不上：relic_001 的「覆盖率」写 75.0%，manifest 是 62.1
```

还原后回到 `PASS 23`。提示里的期望值按**单元格自己的精度**印（写 62.1 而不是
62.11），否则读的人会去找一个不存在的偏差。

## 未决

- GUI 里还没有「核验交付」按钮（CLI 已可用，GUI 接入是纯界面工作）
- **X1 两列不核**：manifest 的 `X1` 条目在本批数据里是空的（两期不同源被闸门
  挡下 → 阶段 SKIPPED），没有唯一来源。它由 M5 的 `_fmt_x1` 三态写出
  （`未标定` / `N/A` / 数字），核对它等于把派生逻辑抄两遍
- CSV 有多条数据行时只核「第一列等于目录名」那一行；一件一表的前提下够用，
  真出现一表多行要改
