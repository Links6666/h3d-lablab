# H3D 长期记忆

> 代号权威是 `GLOSSARY.md`（指南 S0/S1/S2→`A0/A1/A2`、指南 X2（异常检测）→代码 `X3`、
> 里程碑 P0–P6→`B0–B6`；对外口径一律用指南编号，见 ADR-0027）。运维（出 PDF/打包/
> 沙箱删除/git push 代理）见 `MEMORY_OPS.md`。过程与逐版本基线数字见 `docs/adr/`
> 与 `.workbuddy/memory/<日期>.md`，此处只留**不能再踩的判据**。

## 环境

- 文物三维建模自动化系统（5~30cm），硕论，全开源。阶段 M1–M6 +
  S1(COLMAP) + P1/X1/X2(可选)；业务层禁 import Open3D（走 `h3d.io`）。
- `.venv_lab\Scripts\python.exe`（PySide6 6.11 / open3d 0.19 / trimesh / cv2 5.0）；
  colmap `E:\colmap\colmap-x64-windows-cuda\bin\colmap` 4.1.1；打包 `h3d_gui.spec`
  （onefile+launcher），打包版批量 `H3D.exe --batch <cfg>`（**不带 `-c`**）；
  Inno `D:\h3d_relic_test\InnoSetup7\ISCC.exe`。
- 当前 **1.1.25（r31）**，测试 **1462** 全绿（≈2.5 min），旧发行版留最近 3 个。
  `git commit` 要带 `-c user.name=". Link" -c user.email=1530800288@qq.com`
  （仓库里 `user.name` 是空的）。

## 待办（都是**数据**阻塞，不是代码）

- **真实扫描件缺**：异源配准/双源融合/毫米级精度都靠它背书；端到端只是同源功能性验证。
- **真实两期数据缺**：X1 与 X4 都是唯一缺口。X1 侧另欠 `30_dense` 反向 6895mm 的内容核对。
- **色卡缺**：X2 阻塞在采集规范（ArUco 贴几个/贴哪、色卡与文物相对位置、要不要灰卡）。
  「ArUco 中心=色卡角」不成立（标记对称，中心在角上必压色块）；格点 vs 格中心待定。
- **病害样本缺**：A0 **卡在数据不在代码**。16 个候选区裁图全是「岛边界/材质交界」，
  残存彩绘 1.89%，硬标只得「彩绘/胎体」= 域分类。判据：先能指出 ≥10 处「这就是
  裂隙/起翘」再装 torch/MobileSAM。规范已出：`docs/采集与标注规范-病害语义.md` +
  `scripts/check_damage_labels.py`（每条约束对应一条检查）。
- **S1 已知缺陷**：写死 `single_camera 1`（多机位误合并）、`SIMPLE_RADIAL`（换镜头失准）；
  质检判别力不足（`coverage_threshold=0.3` 实测 81.8% 形同虚设）；镂空/内侧缺覆盖 →
  泊松留洞 + 22.3% 纹素靠反面填充，需补低机位/俯拍；稀疏特征弱（30 张 13,990 点、
  track 3.95）→ 补侧向硬光/偏振。**对标文献每类 100–200 张，30 张是最大单点差距。**
- **`io/mvs` 契约已建**（r32，ADR-0028）：`plan(req) -> MvsPlan(steps/fused_path/
  progress)`，命令只在 `io/mvs` 里拼（AST 守卫拦回填）。
- **后端身份已落盘**（r33，ADR-0029）：S1 metrics → 资产 params → `content_key` params
  → verify（缺标识 WARN）。**只写 params 不写 content_key = 假落地**（复用仍按旧键命中）。
  **不动 `stage_input_key`**：会让存量项目全量重跑，且版本不是「输入」。账本有**扁平
  （record）与嵌套（entry["metrics"]）两种形状**，两种都要认。
- **COLMAP 稠密重建不可逐位复现**（r32）：同输入同代码两次，点数 130,921 vs **130,929**、
  1.87% 点位置不同、max **1.0e-3 × 对角线** → `fused.ply` **哈希相同不能当判据**（正常输入
  上恒假）；改统计口径（点数差 ≤0.1% + 中位距离 0 + max ≤2e-3 diag，
  `scripts/compare_point_sets.py`）。**X1 硬约束**：300mm 件 = 0.3mm 噪声（X1 检 0.5mm
  级形变）→ **不能靠重跑某期修正数据**。
- **未覆盖用例**：`force_rerun` 是布尔整批；窗口缺「拉缩后再跑」/1366×768/150% 缩放。

## 配准 / 对齐（硬约束）

- **相似变换必须有外部可核对判据**：①两包围盒对角线之比（成功必≈1）；②源→目标单向
  中位距离真变小。**自报 rmse 不可信**（1.1.12 实测 `rmse_after=0.0968` 而几何搬错 50 倍：
  算的是变换**前**距离 + 只统计 p90×2 内对应 + 仅采 3000 点）。
- **尺度初始化禁用 min/max 包围盒**（COLMAP 飞点把 6.19 撑到 47.83）；用 **99% 分位**。
- **「合格」的对齐也可能不如「不做」**：先问 `already_aligned`（中位距离 <1% 对角线跳过）。
  被拒 → **回退恒等**，不得从失败对齐导出控制点。
- **施加刚体变换 ≠ 造出两站**：M2 判据是**文件数**（`len(cleaned)<2` 单站直通）。验证配准
  必须 00_raw 里有两份错位几何 → `scripts/verify_registration_e2e.py --angle 30 --noise 0.002`。
- **单站直通 ≠ 配准通过**：日志「M2 完成」在单站时意味着一次变换都没做。r25 已在开跑前
  显式提示（ADR-0021，对话框 + 日志各一版，份数口径复用 `runner.discover_items`）。
- **随机消费者已清点完毕**（ADR-0026）：M5 采样 / 对齐精化 / M2 RANSAC / M1 平面剔除
  （`segment_plane`，默认 `max_planes=0` 所以「潜伏」，跨进程实测差 1 个内点）/ M3 泊松
  （早已 `n_threads=1`）。**种子跟消费者走**（`RANSAC_SEED` 在 `ransac.py` 不在编排层）。
  守卫 `TestStochasticCallsAreSeeded`：文件里出现随机 API，**同一文件**必须有
  `seed_rng(`/`sample_surface(`——它第一次跑就抓到 `ransac.py`。
- **RANSAC 定种子前 17 次里 3 次解出 179° 翻转解**（ADR-0024）：fitness 0.52、中位间距
  仍只 0.75% 所以间距判据放行。**翻转候选从 RANSAC 解派生** → 初值一歪三个候选一起歪。
  已 `io.rng.seed_rng` 定种子（错解 5 次未再复现），但**初值选择仍有进程间差异**。
- **朝向用「法线一致性」拦，不拿 fitness 一刀切**（ADR-0025，用户选的 C 方案）：
  `normal_agreement` = ICP 内点上两云法线（各自归一到外向约定）点积均值；
  闸门 = `na < -0.2 且 fitness < 0.55`。真机正解 +0.98/+0.85（低重叠 0.23）、翻转 −0.37~−0.60。
  **必须先做外向归一**（实测符号可整体相反，不归一会把正解算成 −0.98）。
  **近凸/轴对称物上判不出**（翻转与正解几何等价）——已知边界，不拦。
  查回归时 3 次样本不够（本例失败率 18%，r23 的 4 次全对只是运气）。
- **ICP 单初值在轴对称物上解出翻转解**（30°→166.58°）→ 多初值（RANSAC + 绕两主轴 180°）
  取 fitness 最高；`fitness<0.55` 落 `low_confidence`（不判失败但必须可见）。
- **M4 对齐要有投影级验收**（ADR-0009 D3）：`accepted` 只管「几何**之间**像不像」，管不了
  「还在不在**相机视野里**」。真机实测走对齐 coverage 21.22%→1.16%、相机 20→3，闸门却判通过。
  已加 `in_frame_ratio` 前后对比，**掉一半以上就回退恒等**（相对判据；不对齐的基线才 14.5%）。
  拦住的是控制点两阶段那条路，不是自动 ICP。**UV 利用率 68% 是正常的**，别去改 unwrap。

## 数字口径（「能不能引用」）

- **中误差三态** `rmse_scope` = 实测判门 / 不判门（参考稀疏）/ 不判门（单位存疑）/ 无量纲；
  只在 `metric_units && rmse_reliable && units_plausible` 时参与 5 mm 门
  （`rmse_reliable = mps*1000 <= 5`），其余在 CSV/HTML/论文表**行内标注**（HTML 打 `†`）。
- **`units_plausible`**：声称米制却算出 0.02~3.0 m 之外的对角线 → 自相矛盾。不改数值、
  不翻 `metric_units`，只降级表述 + 退出毫米门。
- **「00_raw 有文件」≠「有激光基准」**（ADR-0020）：`metric_units` 由 `runner.data_source`
  （auto/laser/photogrammetry）+ M1 证据共同决定（`quality.gate.resolve_metric_units`）。
  `auto` 必须与旧实现逐字等价；`photogrammetry` 恒不主张米制。**证据压过声明**：声明 laser
  而 M1 判出 `raw_looks_like_sfm` → 仍不主张米制 + `declared_source_conflict` + WARN。
  声明是把判断权交回用户，**不是**后门。
- `metric_units_reason` 必须与布尔一起落盘（manifest + `qc_metrics.json`）：只落布尔的话，
  「机器猜的 auto」与「人声明的 photogrammetry」事后长得一模一样。判门失败时 manifest 不写，
  `qc_metrics.json` 是唯一留档。
- **自证闭环**：`dense_novel_ratio<0.15` 且有 00_raw → `dual_source_duplicate=True`、
  `dense_source=raw_looks_like_sfm`，配准/精度/覆盖率三项都不可引用。
- **`texture_carrier` ≠ `_delivered`**：M4 自动补导 OBJ（carrier 恒 obj），M6 只复制
  `publish.formats`；只勾 ply 时图集 PNG 孤立（报表须显示「无（图集孤立）」）。
- **诊断文本走 `|safe`**：`autoescape` 配 `{{ f.text }}` 会把 `<b>` 印成实体；外部字符串过
  `_esc`，文本里禁裸 `<`。
- **手写白名单陷阱**（r16 根因，已踩三次）：`runner.manifest_metrics`、M2 的 metrics、
  阶段清单都是**手写挑选**，上游算得再对，不写进白名单就等于没算，而报表照样正常。
  新增指标三处同步：计算处 → 白名单 → 模板。`normal_agreement` 第一次就漏了白名单，闸门成摆设。
- **口径文字单一实现** = `quality.gate.scope_text`（原来 `quality.run` 与 `build_report`
  各写一遍，加声明那一档时只改了 fallback 那份）。
- 核验账本候选 = `90_meta/` **和** `阶段成果/output/run_manifest.json`（GUI 一键生成写这里）。
  只搜前者会把健康项目判成坏的。
- **外部文献的 mm 与我们的 % 不可比**（2026-11-07）：NG-MVSNet 的 0.515 mm 是「到
  HandySCAN 700 真值的距离」，我们的 `rmse_rel` 9.07% 是「模型到自己点云的自洽度」；
  差距首先是**有没有尺子**，不是算法好坏。它 Final RMS 0.3–0.8 mm 说明连评估本身都带
  0.3 mm 不确定度 → 被测差异小于它时「提升 0.006 mm」不成立。**先解决尺子再谈精度。**
  另：文献评估出的行动项**不带判据就会消失**（v1.1.11 期提 5 条，只落地 1 条）。
- **覆盖产物前先确认账本非空**：`RunManifest.load` 对坏路径静默返回空账本，用它重生成报表
  会把好报表覆盖成空表（r31 踩过，verify 从 23/0/0 掉到 21/2）。

## 跨层契约

- **终态五态** done / fail / skipped(+`skip_kind`) / disabled / 键不存在=未执行；
  分母 = 件数 × 阶段数 − 未启用。全跳过 `done=0 skipped=N` 是**正确终态**，
  判据 `failed==0 and rc==0 and done+skipped>0`。
- **件级终态必须收敛、RUNNING 绝不外泄**：可选阶段先 `begin_stage` 后判 disabled，
  `mark_disabled` 不动任务级状态 → 停 RUNNING → 误判失败。修法：`begin_stage` 记
  `_status_before` + `mark_disabled` 回退 + `_finalize_task_status()` 逆推。
- **完成行 ≠ 进程结束**：CLI 打完「批量处理完成」还要写 3 个报表；GUI 须读完 stdout 并
  `wait()`。**QThread 覆盖即崩**：新建前查 `isRunning()` + `deleteLater()`。
- **配置必须「读改写」**：`from_root()` 留原文件作底稿只覆盖控件字段，无控件参数
  `setdefault`；`apply()` 不写的控件就是假控件。自查：值进配置了吗？终态认全了吗？
  连点两次会怎样？
- **产物路径的键是 `item_id`（00_raw 子目录名）不是项目名**（用项目名拼 → 示例项目全绿、真实空）。
- **断点续跑跳过 ≠ 无产物**：`is_done()` 命中须 `restore_stage()` 恢复 outputs/metrics。
- **续跑判据是输入指纹不只是状态**（`runner/fingerprint.py` → manifest `input_key`）=
  外部输入摘要 + 上游阶段键 + 参数快照，**配置用减法**（只剔 `project`/`runner`；
  白名单漏一段就是「改参数不重跑」）。**并行 `workers>1` 不做续跑判定**（已补 WARNING 未修）。
- **同照片多件**按「名+大小+SHA-1」比对（`sfm_solve.reuse_identical_photos`）。
- **禁静默降级 / `except: pass`**：兜底须同时写 metrics + WARNING（novel 曾被吞数周）。
  配置读 `utf-8-sig`（→`[E001]`）；`validate` 0 件 rc=1；子进程走 `proc.run_external`；
  派生状态原子写；`STAGE_ORDER` 必须与 `STAGE_REGISTRY` 一致（漏 P1→KeyError 被 Qt 吞）。
- **阶段清单唯一真源 = `h3d.core.stages.STAGE_ORDER`**（ADR-0007）：
  `S1→M1→M2→M3→M4→P1→X3→X1→X2→M5→M6`。收敛前有**八份手写副本**，其中四份不认识 X3
  → 状态表没 X3 行、分母少一个阶段而不报错。**改顺序动的是分母、GUI 阶段条、续跑上游键。**
- **X1 的两列由 M5 的 `build_report` 直接写**（不事后追加）：X1 移到 M5 之前后
  「M5 已生成 accuracy.csv」这个前提反了。三态：`未标定` / `N/A`（没跑）/ 数字。

## 输出布局（paths.py 唯一出口）

- `阶段成果/`（12 子目录 + `99_runs` + `output`）+ `交付成果/`（`report/`+`<件号>/`）；
  GUI/CLI/runner/report 不许自己拼路径。
- **`task.root` 下沉为「阶段成果」**。新老判据看「这层是否叫 `阶段成果`」（**不能用 00_raw 在不在**）。
- **迁移必须有 `looks_like_project()` 闸门**：`output`/`report` 是通用名，否则会把仓库
  `output/` 整棵搬走（167 个 D，踩过）。

## 贴图 / 几何

- **像素系必须换算**：内参属 `photos_small`（2400 长边），M4 读原图，实测 1.874×
  （直索引 → 颜色全取左上角 53% 且无报错）。写 `pixel_scale`；照片 uint8 + 2GB 预算等比缩小。
- UV 逐纹素光栅化；遮挡**降权不硬剔除**（0.05，硬剔除 79.7%→47.8%）；退化三角形回退点质心。
  **法线朝向须相机投票**（`consistent_tangent_plane` 只保相对一致，68% 反向）；
  泊松掩膜不可用亮度阈值，用 `_L1.0_mask.png`；`_bleed` 须零边界平移。
- **表面采样一律走 `h3d.io.sample_surface(mesh, n, seed)`**（守卫：除 `io/mesh_io.py` 外
  不许出现 `sample_points_uniformly(`；`utility.random.seed` 只许在 `io/rng.py`）。
  `mesh.sample_points_uniformly` **没有 seed 参数**（Open3D 0.19），同一输入两次结果不同：
  真机 M5 中误差 86.78 / 87.42 / 87.01。r27 扫出 4 处，其中 `alignment_quality` 那行
  `_seed_random` 是**空的**（那里是 linspace 抽样）——「看起来有约束、实际没有」比缺失更糟。
  **<1% 的漂移最危险**：不触发任何断言，又让两天前的报表对不上。
- **`texture_overlap` 必须与 `multiview_color_std` 配对读**（ADR-0022）：色差小 + 重叠度高
  = 融合稳；色差小 + 重叠度≈1 = **只有一台相机在看**（没分歧自然没色差）——最该报警的
  情况恰好是色差最漂亮的时候。按纹素去重（不是相机×纹素事件），只在真着色纹素上算
  （不外扩不填洞），缺键是 None 不是 0。
- **质量口径**：`coverage_ratio`=图集像素利用率、`atlas_utilization`=岛占比、
  `fill_ratio`=两者之比；真判据看**岛内填充率**（<85% 才是法线/遮挡/位姿问题）。
  碎片化 `component_metrics`（>3 块且最大 <60% WARNING）；三平面 RANSAC 27.8%→86.6%（未实装）。
- **Open3D 0.19 写 GLB 静默丢纹理** → 必须 trimesh（`baseColorFactor=[255,255,255,255]`、
  metallic 0、roughness 1、doubleSided；trimesh 要**逐顶点** UV，Open3D 逐角）。带纹理 OBJ
  慢 25 倍 → 手写 `_write_obj_textured`（vt 逐顶点、**V 不翻转**）。本机无 EGL 离屏。
- **M4 图集复用**两级键（UV 级=几何指纹；纹理级=+法线+位姿+照片 SHA-1+参数+`CACHE_VERSION`）；
  命中复用 vmap/uvs + 贴图 + 掩膜 + **首件 metrics**（漏它会报 0 覆盖率）。**键集合变了必须
  升 `CACHE_VERSION`**，否则新指标永远是 None 且不报错（踩过两次）。
- **M4 可见性两数**：`normal_visible_fraction`（夹角）vs `in_frame_fraction`（真投进画幅）；
  差 40+ 百分点 = 几何搬错。`back_facing_filled`/`occlusion_rejected` 是「相机×纹素」事件数，
  去重看 `*_texels`。

## GUI 工程

- **界面文案改完要截图看一眼**：QLabel 是纯文本，`**粗体**` 会原样印星号；
  `ElidedLabel.sizeHint` 在构造未完成/析构途中被 Qt 问到时抛 AttributeError 往 stderr 喷栈
  （windowed 版 stderr=None = 静默）。已各加守卫测试（纯文本 / 可重入）。
- **读模型必须容错**：Open3D 0.19 的 OBJ/GLB 走 ASSIMP，按 ANSI(GBK) 解路径 → **中文路径必抛
  `UnicodeDecodeError`**；PLY 无碍。统一走 `gui/meshload`（非 ASCII 复到临时目录重读，失败返 None）。
- **Qt 槽里异常不外传**（windowed 版 stderr=None 全静默）→ 槽内关键路径必须 try/except +
  可见日志。踩过：预览抛错 → 槽内后续全不执行 =「跑完了但界面空」。`QThread.run()` 同理。
- **窗口跳变 = 最小尺寸随文字变**：`QLabel.minimumSizeHint()`=整段文字宽，`QProgressBar.setFormat`
  格式串**也计入 sizeHint** → 可变文字用 `ElidedLabel`，进度条只写 `%p%`，配置区进 `QScrollArea`，
  按 `availableGeometry()` 94%/92% 定型且封顶。
- **PySide6 6.11：无 windowHandle 时调 `QWidget.screen()` 破坏堆**（`0xc0000374`）→
  有 windowHandle 才问它，否则 `primaryScreen()`。诊断序：faulthandler 抓原生栈 → RSS 探针
  → 组合二分。
- **约束先确认后运行**：`_confirm_constraints()` 是 `_start_run_inner()` 第 0 步，取消 = 不建
  目录不写 yaml 不起子进程；落 `runner.constraints` + `confirmed`。
- **交付格式 ⊂ M4 产出格式**：`publish.formats` 要跟 `texture.export_formats` 同步
  （M6 只从 50_tex 复制，请求 M4 没产的 → `formats_missing` 假告警）。
- **阶段耗时双源**：运行中计墙钟，结束用 `elapsed_s` 覆盖；多件求和、与墙钟分开标注；
  阶段事件不能去重。**进度分母是工作量不是种类**（按耗时加权；预览 PLY 优先、>6 万面先简化）。

## X3 / X4（单期异常与时序）

> 细节（按岛归一化、纹理结构张量、色度分域、五态配对、照明对齐的数字与失败路径）
> → `.workbuddy/memory/MEMORY_X3_X4.md`。动 X3/X4 之前先读它。

- **UV 图集是 3690 个岛的马赛克 → 局部空间背景处处不成立 → 必须按岛归一化**
  （真机 2.18%/35 处 → 0.24%/7 处）。**阈值方向是死路**，要换与 z 无关的第二类证据。
- `X3` 在 `M4→P1→X3→M5`，顺序只在 `_reorder_ext_stages` 钉；归 `OPTIONAL_STAGES`、
  **不进质量门**。「N 处」只能读作**待复核处数不是病害数**。
- X4：五态配对、**ΔE≥5 只卡非稳定态**、照明对齐**必须在 RGB 码值空间**。不做 runner 阶段。

## 测试坑

- `--basetemp=<项目内路径>` 会让 runner/texture 测试互相污染 → 用默认 `%TEMP%`；
  **exit=1 多为沙箱 safe-delete 拦截**，拿失败清单唯一稳手段是 `--junitxml`。
- **pytest-timeout 没装**（`--timeout=` 直接 unrecognized argument）。
- **尺度类测试的容差按 MPS 表达**，不要硬编码毫米数：M3C2 的法向搜索半径是 `10×MPS`，
  曲面上邻域把法向拉平 → 固有偏差 ≈0.4×MPS（R=100mm 实测偏 4%）。
- **磁盘满会让 `test_batch_dry_run` 挂**（assert 1 == 0，日志是 `h3d.health`
  「盘仅剩 x GB」）→ **先清 C 盘别改代码**；`rm -rf $TEMP/...` 被 safe-delete 拦，
  用 `[System.IO.Directory]::Delete($p,$true)`。
- **flaky 比失败更危险**：会训练人「红了就重跑」。判据用 `atol`（浮点末位 1e-32 不是「结果不同」），
  且同一用例至少跑 3 次再下结论。

## 拆出去的细节
- B1 档案/图集、S1 双场景与 COLMAP 稠密 → `MEMORY_S1_B1.md`；X3/X4 全部数字 →
  `MEMORY_X3_X4.md`；PDF 渲染/打包/push 代理 → `MEMORY_OPS.md`。
