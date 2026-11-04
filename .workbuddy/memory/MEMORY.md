# H3D 长期记忆

> 代号权威是 `GLOSSARY.md`（代码阶段号已冻结：指南 S0/S1/S2→`A0/A1/A2`、指南 X2
> （异常检测）→`X3`、里程碑 P0–P6→`B0–B6`）。运维（出 PDF/打包/沙箱删除/git push 代理）
> 见 `MEMORY_OPS.md`。设计与失败过程、逐版本真机基线数字见 `docs/adr/` 与
> `.workbuddy/memory/<日期>.md`，此处只留**不能再踩的判据**。

## 环境
- 中小型文物（5~30cm）三维建模自动化系统，硕论，全开源，量词「件」。阶段 M1–M6 +
  S1(COLMAP) + P1/X1/X2(可选)；业务层禁 import Open3D（走 `h3d.io`）。
- `.venv_lab\Scripts\python.exe`（PySide6 6.11 / open3d 0.19 / trimesh / cv2 5.0）；colmap
  `E:\colmap\colmap-x64-windows-cuda\bin\colmap` 4.1.1；打包 `h3d_gui.spec`（onefile+launcher），
  打包版批量 `H3D.exe --batch <cfg>`（**不带 `-c`**）；Inno `D:\h3d_relic_test\InnoSetup7\ISCC.exe`。
  当前 **1.1.18（r24）**，测试 1403 全绿（≈2.5 min），旧发行版留最近 3 个。
  `git commit` 要带 `-c user.name=". Link" -c user.email=1530800288@qq.com`
  （仓库里 `user.name` 是空的，历史提交靠环境变量）。

## 待办
- **真实扫描件仍缺**：异源配准/双源融合/毫米级精度都要它背书；现有端到端只是**同源功能性验证**。
- GUI 在 `00_raw` 只有 1 个文件时应提示「本件无法验证配准」（单站直通正确，用户会误判）。
- ~~约束面板加「数据来源」~~ **r24 已做**（`runner.data_source`，ADR-0020）。剩余：
  GUI 在 `00_raw` 只有 1 个文件时提示「本件无法验证配准」（报表侧 M2 已说，运行前没说）。
- **S0 病害语义（AI）**：代码/环境随时能开（4GB 够 MobileSAM+YOLOv8n-seg），**卡在数据**：
  1.1.12 的 16 个候选区裁图全是「岛边界/材质交界」，无裂隙/起翘/脱落形态，残存彩绘仅 1.89%，
  硬标只得「彩绘/胎体」= D15 域分类。需一件病害形态明确的文物（2~3 类各若干实例）。
  先验证「有没有东西可标」再装 torch/MobileSAM（2.5GB）。
- **X1 已定案（ADR-0005 + ADR-0006）**：首验四个缺陷（硬拼 `10_dense`；网格被读成顶点；
  核心点铺 XY 平面；`estimate_mps` 抽样放大 `sqrt(n/m)`）已修。**177mm 不是尺度问题**——
  合成已知形变标定线性度 1.000/0.998/1.001、同源报 0.000mm，尺子一直对；根因是候选链把
  `10_dense/fused_dense.ply`（照片重建）与激光件当两期（互最近邻中位 **5541mm**）。
  已加两期同源闸门 `_epoch_consistency`（对角线比 + 双向互最近邻中位 ≤2%），不通过不出数。
  仍未做：**真实两期数据**（唯一还缺的东西）、`30_dense` 反向 6895mm 的内容核对。
- **X3 病害语义分割未开工**：H3D-Lab 掩膜 → 复用 M4 位姿/`pixel_scale`/Z-buffer 加权票决 → UV
  类别图 → 每纹素 3D 面积积分。坑：单视图硬投影 = 整件都有病害。
- **X4 时序追踪已做（切片 1，ADR-0004）**：细节见 `MEMORY_X3_X4.md`。未做：真实两期数据、
  M3C2 几何通道。
- X3 ≥80 texel 大斑块灵敏度不足（需降采样版大窗口）；图斑明细只进 CSV；进度权重未在多件批验证。
- **X2 首验结论（ADR-0008）：缺少色卡，且原来的"能出数"是单位错**
  `colour.run` 要 sRGB 0~1，`_stage_colour_quality` 喂 0~255 → 全钳成纯白；
  标准值×255 落在同一钳位路径上报 **21.42**，与真机那个数只差 0.0007。已删
  `texture-random` 回退（无色卡→SKIPPED，写明理由）、补 `/255`、改用 robust 定位。
  **阻塞在采集规范**：ArUco 贴几个/贴哪、色卡与文物相对位置、要不要灰卡。
  「ArUco 中心=色卡角」这个假设本身不成立（标记对称，中心在角上必压色块）。
  定位几何还有一处（格点 vs 格中心，边缘偏 17% = 一格宽），等真色卡再定。
- S1 写死 `single_camera 1`（多机位误合并）、`SIMPLE_RADIAL`（换镜头失准）；质检判别力不足
  （`samples=4000`/`coverage_threshold=0.3` 实测 81.8% 形同虚设）；镂空/内侧缺覆盖 → 泊松留洞 +
  22.3% 纹素靠反面填充，需补低机位/俯拍，稀疏特征弱（30 张 13,990 点、track 3.95）→ 补侧向硬光/
  偏振。`force_rerun` 是布尔整批；窗口用例未覆盖「拉缩后再跑」/1366×768/150% 缩放。

## 对齐 / 配准（r17 起硬约束）
- **相似变换必须有外部可核对判据**：①两包围盒对角线之比（成功必≈1）；②源→目标单向中位距离
  真变小。**自报 rmse 不可信**（1.1.12 实测 `rmse_after=0.0968` 而几何搬错 50 倍：算的是变换**前**
  距离 + 只统计 p90×2 内对应 + 仅采 3000 点）。
- **尺度初始化禁用 min/max 包围盒**（COLMAP 飞点把 6.19 撑到 47.83）；用 **99% 分位**。
- **「合格」的对齐也可能不如「不做」**：先问 `already_aligned`（中位距离 <1% 对角线跳过）。
  被拒 → **回退恒等**，不得从失败对齐导出控制点。
- **ICP 单初值在轴对称物上解出翻转解**（30°→166.58°，fitness 0.336 还越过 0.2 失败门）→ 多初值
  （RANSAC+绕两主轴 180°）取 fitness 最高；`fitness<0.55` 落 `low_confidence`（不判失败但必须
  可见）。修复后 7/30/90/120/180° ≤0.02°。
- **施加刚体变换 ≠ 造出两站**：M2 判据是**文件数**（`len(cleaned)<2` 单站直通）。验证配准必须
  00_raw 里有两份错位几何 → `scripts/verify_registration_e2e.py --angle 30 --noise 0.002`。
- M4 可见性两数：`normal_visible_fraction`（夹角）vs `in_frame_fraction`（真投进画幅）；差 40+
  百分点 = 几何搬错。`back_facing_filled`/`occlusion_rejected` 是「相机×纹素」事件数，去重看 `*_texels`。
- **M4 对齐要有投影级验收**（ADR-0009 D3）：`accepted` 闸门只管「几何**之间**像不像」，
  管不了「还在不在**相机视野里**」。真机实测走对齐 coverage 21.22%→1.16%、相机 20→3，
  闸门却判通过。已加 `in_frame_ratio` 前后对比，**掉一半以上就回退恒等**（相对判据；
  拒绝=不动=安全侧，不能用绝对阈值——不对齐的基线才 14.5%）。拦住的是**控制点两阶段**
  那条路，不是自动 ICP。**UV 利用率 68% 是正常的**，别因为「图集非黑比例低」去改 unwrap。

## 报表口径（「数字能不能引用」，r16 起）
- **中误差三态** `rmse_scope`=实测判门/不判门（参考稀疏）/不判门（单位存疑）/无量纲；只在
  `metric_units && rmse_reliable && units_plausible` 时参与 5 mm 门（`rmse_reliable=mps*1000<=5`），
  其余在 CSV/HTML/论文表**行内标注**（HTML 打 `†`）。
- **`units_plausible`**：声称米制却算出 0.02~3.0 m 之外的对角线 → 自相矛盾。不改数值、不翻
  `metric_units`，只降级表述+退出毫米门。
- **`metric_units=bool(task.raw_paths)` 是脆假设**：SfM 产物放进 00_raw 就成「激光锚定」（r17 补
  `laser_unverified`）。**自证闭环**：`dense_novel_ratio<0.15` 且有 00_raw →
  `dual_source_duplicate=True`、`dense_source=raw_looks_like_sfm`，配准/精度/覆盖率三项都不可引用。
- **`texture_carrier`≠`_delivered`**：M4 自动补导 OBJ（carrier 恒 obj），M6 只复制
  `publish.formats`；只勾 ply 时图集 PNG 孤立（报表须显示「无（图集孤立）」）。
- **诊断文本走 `|safe`**：`autoescape` 配 `{{ f.text }}` 会把 `<b>` 印成实体；外部字符串过 `_esc`，
  文本里禁裸 `<`。
- **手写白名单陷阱**：`runner.manifest_metrics` 是手写 dict，上游算出的键不写进去 → 报表全读不到
  （r16 七处缺陷根因）。新增指标三处同步：计算处 → 白名单 → 模板。

## 数据来源与可复现（r24 起，ADR-0020）
- **「00_raw 有文件」≠「有激光基准」**。`metric_units` 由 `runner.data_source`
  （auto/laser/photogrammetry）+ M1 证据共同决定（`quality.gate.resolve_metric_units`）。
  `auto` 必须与旧实现逐字等价；`photogrammetry` 恒不主张米制。
  **证据压过声明**：声明 laser 而 M1 判出 `raw_looks_like_sfm` → 仍不主张米制 +
  `declared_source_conflict` + WARN。声明是把判断权交回用户，**不是**后门。
- `metric_units_reason` 必须与布尔一起落盘（manifest + `qc_metrics.json`）：只落
  布尔的话，「机器猜的 auto」与「人声明的 photogrammetry」事后长得一模一样，而
  两者可信度差一个量级。判门失败时 manifest 不写，`qc_metrics.json` 是唯一留档。
- **表面采样一律走 `h3d.io.sample_surface(mesh, n, seed)`**：
  `mesh.sample_points_uniformly` **没有 seed 参数**（Open3D 0.19），同一输入两次
  结果不同。真机 M5 中误差 86.78 / 87.42 / 87.01（三次同一份数据）。
  **<1% 的漂移最危险**——不触发任何断言，又让两天前的报表对不上。
- 核验账本候选 = `90_meta/` **和** `阶段成果/output/run_manifest.json`（后者是
  `RunnerConfig.manifest` 默认值，GUI 一键生成也写这里）。只搜前者会把健康项目
  判成坏的，而且**只在项目"健康"时才报错**（有问题的项目先因别的原因失败，被盖住）。
- 口径文字单一实现 = `quality.gate.scope_text`（原来 `quality.run` 与
  `build_report` 各写一遍，加声明那一档时只改了 fallback 那份）。

## 跨层契约
- **终态五态** done/fail/skipped(+`skip_kind`)/disabled/键不存在=未执行；分母=件数×阶段数−未启用。
  全跳过 `done=0 skipped=N` 是**正确终态**，判据 `failed==0 and rc==0 and done+skipped>0`。
- **件级终态必须收敛、RUNNING 绝不外泄**：可选阶段先 `begin_stage` 后判 disabled，`mark_disabled`
  不动任务级状态 → 停 RUNNING → 误判失败。修法：`begin_stage` 记 `_status_before` +
  `mark_disabled` 回退 + `_finalize_task_status()` 逆推。
- **完成行 ≠ 进程结束**：CLI 打完「批量处理完成」还要写 3 个报表；GUI 须读完 stdout 并 `wait()`。
  **QThread 覆盖即崩**：新建前查 `isRunning()`+`deleteLater()`。
- **配置必须「读改写」**：`from_root()` 留原文件作底稿只覆盖控件字段，无控件参数 `setdefault`；
  `apply()` 不写的控件就是假控件。自查：值进配置了吗？终态认全了吗？连点两次会怎样？
- **产物路径的键是 `item_id`（00_raw 子目录名）不是项目名**（用项目名拼 → 示例项目全绿、真实空）。
- **断点续跑跳过 ≠ 无产物**：`is_done()` 命中须 `restore_stage()` 恢复 outputs/metrics。
- **续跑判据是输入指纹不只是状态**（`runner/fingerprint.py`→manifest `input_key`）=外部输入摘要+
  上游阶段键+参数快照，**配置用减法**（只剔 `project`/`runner`；白名单漏一段就是「改参数不重跑」）。
  **并行 `workers>1` 不做续跑判定**（已补 WARNING 未修）。
- **同照片多件**按「名+大小+SHA-1」比对，命中复制 `fused_dense.ply`+`capture_meta.json`（0.2s）；
  开关 `sfm_solve.reuse_identical_photos`。
- **禁静默降级/`except: pass`**：兜底须同时写 metrics+WARNING（novel 曾被吞数周）。配置读
  `utf-8-sig`（→`[E001]`）；`validate` 0 件 rc=1；子进程走 `proc.run_external`；派生状态原子写；
  `STAGE_ORDER` 必须与 `STAGE_REGISTRY` 一致（漏 P1→KeyError 被 Qt 吞）。
- **阶段清单唯一真源 = `h3d.core.stages.STAGE_ORDER`**（ADR-0007）：
  `S1→M1→M2→M3→M4→P1→X3→X1→X2→M5→M6`。收敛前有**八份手写副本**（runner 顺序、
  html/csv/plot 三套 `_STAGE_ORDER`、gui logparse+timing、一个测试死常量、
  `h3d_gui.spec` 的 hiddenimports），其中四份不认识 X3 → 状态表没 X3 行、完成率分母
  少一个阶段而不报错（r16 同构，第三次踩）。**改顺序动的是分母、GUI 阶段条、续跑上游键。**
- **X1 的两列由 M5 的 `build_report` 直接写**（不再事后追加）：X1 移到 M5 之前后
  「M5 已生成 accuracy.csv」这个前提反了，补丁会静默跳过 → 数字反而进不了表。
  三态：`未标定` / `N/A`（没跑）/ 数字。

## 输出布局（paths.py 唯一出口）
- `阶段成果/`（12 子目录 + `99_runs` + `output`）+ `交付成果/`（`report/`+`<件号>/`）+`输出说明.txt`；
  GUI/CLI/runner/report 不许自己拼路径。
- **`task.root` 下沉为「阶段成果」**。新老判据看「这层是否叫 `阶段成果`」（**不能用 00_raw 在不在**）。
- **迁移必须有 `looks_like_project()` 闸门**：`output`/`report` 是通用名，否则会把仓库 `output/`
  整棵搬走（167 个 D，踩过）。

## 贴图 / 几何
- **像素系必须换算**：内参属 `photos_small`（2400 长边），M4 读原图，实测 1.874×（直索引 → 颜色
  全取左上角 53% 且无报错）。写 `pixel_scale`；照片 uint8 + 2GB 预算等比缩小。
- UV 逐纹素光栅化；遮挡**降权不硬剔除**（0.05，硬剔除 79.7%→47.8%）；退化三角形回退点质心。
  **法线朝向须相机投票**（`consistent_tangent_plane` 只保相对一致，68% 反向）；泊松掩膜不可用
  亮度阈值，用 `_L1.0_mask.png`；`_bleed` 须零边界平移。
- **质量口径**：`coverage_ratio`=图集像素利用率、`atlas_utilization`=岛占比、`fill_ratio=`两者之比；
  真判据看**岛内填充率**（<85% 才是法线/遮挡/位姿问题）。碎片化 `component_metrics`（>3 块且最大
  <60% WARNING）；三平面 RANSAC 27.8%→86.6%（未实装）。
- **Open3D 0.19 写 GLB 静默丢纹理** → 必须 trimesh（`baseColorFactor=[255,255,255,255]`、
  metallic 0、roughness 1、doubleSided；trimesh 要**逐顶点** UV，Open3D 逐角）。带纹理 OBJ 慢
  25 倍 → 手写 `_write_obj_textured`（vt 逐顶点、**V 不翻转**）。本机无 EGL 离屏。
- **M4 图集复用**两级键（UV 级=几何指纹；纹理级=+法线+位姿+照片 SHA-1+参数+`CACHE_VERSION`）；
  命中复用 vmap/uvs+贴图+掩膜+**首件 metrics**（漏它会报 0 覆盖率）。

## GUI 工程
- **读模型必须容错**：Open3D 0.19 的 OBJ/GLB 走 ASSIMP，路径按 ANSI(GBK) → **中文路径必抛
  `UnicodeDecodeError`**；PLY 无碍。统一走 `gui/meshload`（非 ASCII 复制到临时目录重读，缓存，
  失败返 None）。
- **Qt 槽里异常不外传**（windowed 版 stderr=None 全静默）→ 槽内关键路径必须 try/except+可见日志。
  踩过：预览抛错 → 槽内后续全不执行 =「跑完了但界面空」。`QThread.run()` 同理。
- **窗口跳变 = 最小尺寸随文字变**：`QLabel.minimumSizeHint()`=整段文字宽，`QProgressBar.setFormat`
  格式串**也计入 sizeHint** → 可变文字用 `ElidedLabel`，进度条只写 `%p%`，配置区进 `QScrollArea`，
  按 `availableGeometry()` 94%/92% 定型且封顶。
- **PySide6 6.11：无 windowHandle 时调 `QWidget.screen()` 破坏堆**（`0xc0000374`）→ 有 windowHandle
  才问它，否则 `primaryScreen()`。诊断序：faulthandler 抓原生栈 → RSS 探针 → 组合二分。
- **约束先确认后运行**：`_confirm_constraints()` 是 `_start_run_inner()` 第 0 步，取消=不建目录
  不写 yaml 不起子进程；落 `runner.constraints`+`confirmed`；运行期输入区隐藏。
- **交付格式 ⊂ M4 产出格式**：`publish.formats` 要跟 `texture.export_formats` 同步（M6 只从
  50_tex 复制，请求 M4 没产的 → `formats_missing` 假告警）。
- **阶段耗时双源**：运行中「▲开始/√完成」计墙钟，结束用 manifest `elapsed_s` 覆盖；多件**求和**
  并与墙钟分开标注；`runner.py` 阶段事件**不能去重**。
- **进度分母是工作量不是种类**：每件独立计分、整批平均、按耗时加权（S1 .90/M4 .06）；S1 跳过换
  `W_SCAN` 归一。预览 **PLY 优先**；>6 万面先体素简化；打开项目即刷新。

## 拆分出去的细节
- **B1 档案层与图集**（布局 / 只读强制 / 空时相 / 图集件级幂等 / 引用体检 / 继承不重展开 UV /
  相机变换 / 版本冻结）与 **S1 双场景、COLMAP 稠密**的具体判据 → `.workbuddy/memory/MEMORY_S1_B1.md`。
  动那两块之前先读它。
- **X3/X4**（按岛归一化、纹理结构张量、色度分域 D15、五态配对、照明对齐）的全部数字与
  失败路径 → `.workbuddy/memory/MEMORY_X3_X4.md`。

## 单期异常检测（B2/X3，r19）与 X4

> 细节（按岛归一化、纹理结构张量、色度分域、五态配对、照明对齐的数字与失败路径）
> → `.workbuddy/memory/MEMORY_X3_X4.md`。动 X3/X4 之前先读它。

- 三句话：**UV 图集是 3690 个岛的马赛克 → 局部空间背景处处不成立 → 必须按岛归一化**
  （真机 2.18%/35 处 → 0.24%/7 处）。**阈值方向是死路**（提高 k 会把真信号一起盖掉）。
  要提升得换与 z 无关的第二类证据（形状/连续性/跨期 X4）。
- 阶段 `X3` 在 `M4→P1→X3→M5`，顺序只在 `_reorder_ext_stages` 一处钉；默认开但归
  `OPTIONAL_STAGES`，**不进质量门**。「N 处」只能读作**待复核处数不是病害数**。
- 色度分域：`domain(paint|bare|unknown)`，**看环不看内部**，**必须用原始 Lab**
  （归零后的 a/b 不在 128 中心）。真机 paint_ratio 1.89%、彩绘区 0 处。
- X4：五态配对、**ΔE≥5 只卡非稳定态**、照明对齐**必须在 RGB 码值空间**。不做 runner 阶段。

## 测试坑
- `--basetemp=<项目内路径>` 会让 runner/texture 测试互相污染 → 用默认 `%TEMP%`；**exit=1 多为
  沙箱 safe-delete 拦截**，拿失败清单唯一稳手段是 `--junitxml`。全量 **1208 项 ≈2.4 min**。
- **pytest-timeout 没装**（`--timeout=` 直接 unrecognized argument）。
- **尺度类测试的容差按 MPS 表达**，不要硬编码毫秒数：M3C2 的法向搜索半径是 `10×MPS`，
  曲面上邻域把法向拉平 → 固有偏差 ≈0.4×MPS（R=100mm 实测偏 4%）。硬编码容差会在换
  点密度时假红。
