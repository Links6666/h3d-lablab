# ADR-0007：阶段清单收敛到 core 单一真源，X1/X2 移到 M5 之前

- 状态：已接受
- 日期：2026-11-01
- 关联：ADR-0003（X3 插到 M5 之前的历史原因）、ADR-0005（X1/X2 顺序未决项）、
  ADR-0006（X1 尺度标定）

## 背景

两件事叠在一起，都指向同一个根源。

**一、X1/X2 排在 M6 之后，报表永远慢一轮。** `_reorder_ext_stages` 把它们放在最后，
而报表在 M5、论文表在 M6 生成——本轮跑完 X1/X2，报表读到的还是上一轮。ADR-0005
实测过：`accuracy.csv` 的 X1 列要跑**第二次**才更新，第一次仍是上一轮的 177.33。

**二、阶段清单在仓库里有七份手写副本。** 做这件事时把每一份都查了一遍：

| # | 位置 | 原内容 | 问题 |
|---|---|---|---|
| 1 | `runner._reorder_ext_stages` | `(...,"M5","M6","X1","X2")` | 决定真实执行顺序 |
| 2 | `report/html_export._STAGE_ORDER` | 8 项，`(...,"M5","M6")` | **缺 X3/X1/X2**，且是完成率分母 |
| 3 | `report/csv_export._STAGE_ORDER` | 10 项 | **缺 X3** |
| 4 | `report/plot_gen._STAGE_ORDER` | 10 项 | **缺 X3** |
| 5 | `gui/logparse.STAGE_ORDER` | 11 项，X1/X2 在尾 | 缺移序 |
| 6 | `gui/timing.DEFAULT_STAGES` | 10 项 | **缺 X3** |
| 7 | `tests/test_report/test_terminal_states.py::_ALL` | 8 项 | 定义了**从未使用**，注释还谎称「与 `_STAGE_ORDER` 一致」 |

X3 从 r19 起**默认启用、一直在跑**，而三份报表副本和一份 GUI 副本都不认识它。
后果：HTML 状态表里没有 X3 那一行，完成率分母 `len(items) * len(_STAGE_ORDER)`
少算一个阶段——「件数 × 阶段数」的跨层契约对不上，而且**不会报错**。

这与 r16 的「手写白名单漏键」是同构缺陷，第三次踩。

## 决策

### D1：清单下沉到 `h3d.core.stages.STAGE_ORDER`，七份副本收敛成一份

放在 `core` 而不是 `runner`：core 是零依赖底座，`report` 与 `gui` 都已从它 import
（`RunManifest` / `PipelineConfig`），不会形成 `report → runner` 的反向依赖。
`gui/timing.py` 原注释写「此处不 import 以免循环」——那个担心不成立，代价是这份
清单单独漂移、缺了 X3。

`runner._reorder_ext_stages` 也改成读这个常量，于是"真实执行顺序"与"报表列顺序"
从**两份可能不一致的清单**变成同一个元组。

### D2：顺序改为 `S1 M1 M2 M3 M4 P1 X3 X1 X2 M5 M6`

排布理由写进了常量注释，关键是三条：

- `X3`/`X1`/`X2` 夹在 `P1` 与 `M5` 之间 —— 三者输入分别只到 M4 / M3 / M4，都不
  依赖 M5，而报表在 M5 生成；排在 M5 之后等于指标永远进不了报表；
- `M6` 最后 —— 它复制交付物，之后跑的任何阶段都进不了交付包（原先 X1/X2 在
  M6 之后，所以它们的 JSON 从来没进过交付包）；
- `S1` 最先、`P1` 紧跟 `M4` —— 与原设计一致，未动。

### D3：`accuracy.csv` 的 X1 两列改由 `build_report` 直接写

移序带出一个**必须一起修**的连带问题：X1 进 `accuracy.csv` 原先靠
`_append_x1_to_accuracy` 事后追加列，前提是「M5 已经把文件生成好了」。移序后这个
前提反了——X1 跑完时文件还不存在，`if os.path.exists` 直接跳过，**X1 数字反而
进不了表**。

改法：`ACCURACY_HEADERS` 加 `x1_max_change_mm` / `x1_changed_ratio` 两列，
M5 从 manifest 读 X1 指标合并进 `metrics`（与 X3 的 `anomaly_*` 同一条路），
`_write_accuracy_csv` 格式化成三态：`未标定` / `N/A` / 数字。补丁函数整个删掉。

数据流单向：manifest 是唯一来源，报表读它。不要靠「谁先跑完谁追加」的时序耦合。

## 已知影响

**老项目重生成报表会多出「未执行 N」。** 五态契约里「键不存在 = 未执行」是既定
语义，所以 X3/X1/X2 在那个项目的 manifest 里确实没有记录时，报表会把它们列成
未执行，`整体判定` 从「通过」可能变成「部分完成」。这是**诚实的**（那些阶段确实
没在这份 manifest 里执行过），但会让旧项目看起来退步了。

处置：这是版本升级的已知一次性观感变化，不特殊处理。真要重出数，重跑一遍即可
（X3 默认开，跑完就有记录）。

**续跑指纹会变。** X1/X2 的上游阶段键从「M1–M6 全部」缩小为「M4/P1/X3/M1–M3」，
旧档案里 X1/X2 已 done 的件会重跑一次。对单期项目无代价（它们本来就 skipped）。

## 验证

- `tests/test_report/test_stage_order.py`（6 项）：状态表含 X3/X1/X2 行、
  报表清单集合等于注册表、分母按注册表阶段数、X1/X2 在 M4 之后 M5 之前、
  X3 仍在 P1 与 M5 之间、M6 最后、GUI 顺序等于 runner 顺序。
- `tests/test_quality/test_accuracy_x1.py`（5 项）：两列存在、已标定印数字、
  未标定印「未标定」、没跑印 N/A（不是 0）、nan → N/A。
- 全量 **1215 项**绿。

## 未决

- X2 的 ΔE 还没有进 `accuracy.csv`。不是漏做：X2 从未真机跑过，而它现在的
  `checker_source` 在找不到色卡时会退回 **`texture-random`**（随机采 24 个像素
  与 ColorChecker24 比对），那种 ΔE 是无意义的。等 X2 首验确认色卡定位链路，
  再决定这一列怎么写（大概率要按 `checker_source` 分三态）。
- `plot_gen` 的阶段色板仍按 10 个阶段硬编码，加了 X3 后可能有重复色。未核实。
