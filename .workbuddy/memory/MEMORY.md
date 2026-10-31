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
  当前 1.1.13（r17），旧发行版留最近 3 个。

## 待办
- **真实扫描件仍缺**：异源配准/双源融合/毫米级精度都要它背书；现有端到端只是**同源功能性验证**。
- GUI 在 `00_raw` 只有 1 个文件时应提示「本件无法验证配准」（单站直通正确，用户会误判）。
- 约束面板加「数据来源」（激光件/照片重建件）：勾后者即不当米制、不自称激光锚定。
- **X3 病害语义分割未开工**：H3D-Lab 掩膜 → 复用 M4 位姿/`pixel_scale`/Z-buffer 加权票决 → UV
  类别图 → 每纹素 3D 面积积分。坑：单视图硬投影 = 整件都有病害。
- **X4 时序追踪已做（切片 1，ADR-0004）**：UV 空间、两期同布局（岛掩膜 IoU<0.99 抛错，靠 B1
  继承）；配对 bbox IoU≥0.1 且质心 ≤1.5×半径和 → 匈牙利；五态（1.3 面积比）；**ΔE≥5 只卡非稳定态**。
  **两期照明对齐已做**：逐通道分位仿射、**在 RGB 码值空间**（Lab 里非线性，残差 4.4），
  真机 +25 时稳定态 ΔE 9.80→0.00；代价：整件均匀色变被当光照减掉、暗部有 2 ΔE 量化下限、
  饱和裁切不可恢复。**X4 不做 runner 阶段**（跨两次运行 + 无基准期时永远 skipped，D3）。
  未做：真实两期数据、M3C2 几何通道。**反向对照判据是「非稳定态为 0」不是「changes 为空」**。
- X3 ≥80 texel 大斑块灵敏度不足（需降采样版大窗口）；图斑明细只进 CSV；进度权重未在多件批验证。
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

## 单期异常检测（B2/X3，r19）
- **背景必须是局部中位数**（`cv2.medianBlur`，8 位量化 O(1)/纹素，核 201 仅 99 ms；**上限 255**）。
  均值/裁剪均值都失效（病变占窗 16% 即把正常残差抬到 0.16ΔE → 外糊一圈、内部 z=2.79 漏检）。
- **逐像素二分求局部中位数不成立**：每像素各带阈值而窗口计数须同一阈值 → 计数恒 0.538 不动、
  背景为 ∓amp、全图 z 归零。
- 尺度**不用中位数**（双峰残差落到 0 → 每条纹一半误报）；换按岛后不再用块级分位数（见下）。
- 多尺度（31/81/201）与颜色/纹理两路都**取最大不取平均**（平均被稀释：10.9→2.96）。
- **纹理用结构张量不用 LBP**（popcount 丢方向；一致性 1.00 vs 0.10）。三闸门：当前有结构 +
  基线有健康证据 + 基线本身一致。
- **方向基线要排除可疑区**（否则两侧都判「方向变了」，外糊 100 texel、IoU 0.61）；纹理证据
  只在颜色没响应处补位。
- **注入方法会骗人**：`255-pixel` 在中间调 ΔE 只有 14 → 用「L 推离中间调 + a/b 取反」并打印 ΔE。
- 真机注入（ΔE=120）：24/40 texel 全覆盖、增量 IoU ≥0.87；80 texel 2/3。**增量 IoU**（扣掉既有
  报警）才是边界精度。
- **80 texel 漏检是 σ 不是信号**（`scripts/verify_x3_bigpatch.py`：三处残差都 97~113，σ_L
  30.3/53.2/15.7 → z 3.21/1.82/6.37）。低信噪比不是估计器缺陷，别用「按面积累加证据」硬拉
  （σ=53 的根源就是材质交界）。改为报下限 `anomaly_sigma_median`（**只取 L 通道**，取错恒为地板 2.0）。
- **图集是 3690 个 UV 岛拼的马赛克**（中位 69 texel、离边界中位 5、最大 75.3）→ 201 窗口能
  整窗落在同一岛内的纹素 **0.0%**：局部空间背景处处不成立。**按岛归一化**（逐岛中位归零 + σ 取本岛离散度 `q75(|L−岛中位|)/1.1503`）
  已实装：真机 **2.18%/35 处 → 0.24%/7 处**、3.5 s→2.9 s、注入仍 9/11。**先归零再填充**
  （反过来岛边界重新长台阶）。不用「块 σ > N×本岛尺度」判脏块：真机 p99=6.07 而 40 texel
  斑块只有 4.7，分不开。已知边界：**岛内病变过半会把病变当中位**（50% 击穿点固有）。
  「N 处」只能读作**待复核处数不是病害数**。**阈值方向是死路**：全局 k 3.5→4.5 图斑 7→4
  但 80 texel 注入覆盖 0.92→0；按岛自适应 `clip(岛内 z 的 p99, k, 6)` 图斑 7→2 但召回
  9/11→4/11（自我指涉：缺陷就是那条尾巴）。自适应已实现、**默认关**，报表渲染 k 区间。
  要真提升得用与 z 无关的第二类证据（形状/连续性/跨期 X4）。
- 合成 12 项：6~120 texel 边界 IoU 全 1.00；纯净/σ=1.5 噪声 0.000%；条纹旋转关纹理不命中、开 ≥0.7。
- **已接进编排层**：阶段 `X3` 在 `M4→P1→X3→M5`，顺序只在 `_reorder_ext_stages` 一处钉（报表在
  M5 里生成，排后面指标永远进不了报表）。默认开（`runner.x3_enabled=True`）但归
  `OPTIONAL_STAGES`（坏图只降级）；**不进质量门**（「病害多」≠「建模失败」）。指标三出口同步：
  `manifest_metrics` → `accuracy.csv` → HTML/论文表，少一个就是 r16 那个坑。结论绑
  `anomaly_atlas_digest`（`atlas-vN` 只有档案继承时才有）。
- **GUI「异常筛查」页（切片 12）**：读模型 `h3d/anomaly/summary.py`（无 Qt，三态
  ok/missing/corrupt）+ 面板薄装配。`missing` 必须印「未筛查」**绝不说成 0 处**。并排
  「原图｜热力图」（热力图说哪里、原图说是什么）；裁剪边距按 `max(w,h)` 给（长条裂隙
  否则被裁成横线）。**验收看截图不看测试**：坐标列默认 100 px 会折三行、占比两位小数
  把 7 处压成同一格——都是测试全绿而截图不可读。已知：**同一岛内彩绘边界也会被报**
  （局部中位数把深褐当背景），单期分不出「掉彩」与「图案边缘」。

## 测试坑
- `--basetemp=<项目内路径>` 会让 runner/texture 测试互相污染 → 用默认 `%TEMP%`；**exit=1 多为
  沙箱 safe-delete 拦截**，拿失败清单唯一稳手段是 `--junitxml`。全量 1169 项 ≈2.2 min。
