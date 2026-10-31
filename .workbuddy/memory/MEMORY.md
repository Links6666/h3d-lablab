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
  当前 1.1.13（r17），旧发行版只留最近 3 个。

## 待办
- **真实扫描件仍缺**：异源配准/双源融合/毫米级精度都要它背书；现有端到端只是**同源功能性验证**。
- GUI 在 `00_raw` 只有 1 个文件时应提示「本件无法验证配准」（单站直通正确，用户会误判）。
- 约束面板加「数据来源」（激光件/照片重建件）：勾后者即不当米制、不自称激光锚定。
- **X3 病害语义分割未开工**：H3D-Lab 掩膜 → 复用 M4 位姿/`pixel_scale`/Z-buffer 加权票决 → UV
  类别图 → 每纹素 3D 面积积分。坑：单视图硬投影 = 整件都有病害。
- **X4 病害时序追踪**：图斑关联（IoU+质心+面积比→匈牙利）、状态机五态；UV 对齐优先、几何 ICP
  兜底；显著性用 M3C2 LoD95 + 最小图斑面积。
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
- **「合格」的对齐也可能不如「不做」**：先问 `already_aligned`（中位距离 <1% 对角线即跳过）。
  被拒 → **回退恒等**，且**不得**从失败对齐里导出控制点。
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
  `metric_units`，只降级表述 + 退出毫米门。
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
  `apply()` 不写的控件就是假控件。改界面自查：值进配置了吗？终态认全了吗？连点两次会怎样？
- **产物路径的键是 `item_id`（00_raw 子目录名）不是项目名**（用项目名拼 → 示例项目全绿、真实空）。
- **断点续跑跳过 ≠ 无产物**：`is_done()` 命中须 `restore_stage()` 恢复 outputs/metrics。
- **续跑判据是输入指纹不只是状态**（`runner/fingerprint.py`→manifest `input_key`）=外部输入摘要+
  上游阶段键+参数快照，**配置用减法**（只剔 `project`/`runner`；白名单漏一段就是「改参数不重跑」）。
  旧记录无 key → 旧规则跳过。**并行 `workers>1` 不做续跑判定**（已补 WARNING 未修）。
- **同照片多件**按「名+大小+SHA-1」比对，命中复制 `fused_dense.ply`+`capture_meta.json`（S1→0.2s）；
  开关 `sfm_solve.reuse_identical_photos`。
- **禁静默降级/`except: pass`**：兜底须同时写 metrics+WARNING（novel 曾被吞数周）。配置读
  `utf-8-sig`（→`[E001]`）；`_is_junk_dir()` 过滤；`validate` 0 件 rc=1；子进程走
  `proc.run_external`；派生状态原子写；`STAGE_ORDER` 必须与 `STAGE_REGISTRY` 一致（漏 P1→
  KeyError 被 Qt 吞）。

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
  命中复用 vmap/uvs+贴图+掩膜+**首件 metrics**；导出不缓存。

## GUI 工程
- **读模型必须容错**：Open3D 0.19 的 OBJ/GLB 走 ASSIMP，路径按 ANSI(GBK) → **中文路径必抛
  `UnicodeDecodeError`**；PLY 走原生 reader 无碍。统一走 `gui/meshload`（非 ASCII 复制模型+mtl+
  贴图到临时目录重读，缓存，贴图按字节复制，失败返 None）。
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
  `W_SCAN` 归一；显示「第 k/N 件」。预览 **PLY 优先**；>6 万面先体素简化；打开项目即刷新。

## 档案层与图集（B0–B1，r18）
- 布局 `archive/{artifact.json, epochs/<id>.json, assets/<id>__v<N>.json, atlas/<ver>.json}`；
  **包裹不改** `阶段成果/`；`CanonicalAsset` 只存相对路径+哈希（换盘符不断）。**只读靠代码强制**
  （`open(mode)` 含 `w/a/x/+` 抛 `ReadOnlyViolation`，相对路径锁在项目根内）。
- **`looks_like_project()` 只认旧布局**，新布局在 `阶段成果/` 下会被判否 → 迁移/GUI 建档用
  `looks_like_h3d_project()`（都认，仍排除 `output`/`report`）。
- `content_key` 必须嵌套+`sort_keys`，禁止拼字符串（`{a:1,b:23}` 与 `{a:12,b:3}` 撞车 → 缓存返回
  错误产物且不报错）；`KEY_SCHEMA` 进 payload。基准期首个时相自动，显式改换必须 WARNING。
- **迁移的 `route` 只是布局推断**（00_raw 有 .ply 就是 A），不是来源验证（1.1.12 有 SfM 冒充
  激光件）；B3 须补真实核验。runner **只在已建档项目**登记产物。
- **建档入口在 GUI「打开项目」**（`ensure_archive` 四态），并在 `_stage_inputs()` **之后**再补一次
  （那一步才把数据复制进 00_raw/00_photo）；`read_status()` 只读刷新绝不建档。
- **空时相必须允许**（`migrate(allow_no_asset=True)`，CLI 默认仍拒）：`_register_archive` 要求
  `baseline()` 非空 → 新建项目首跑前登记永远不发生。`migrate._first_item` 必须探 `00_photo`。
- **「机制写好了但没人调用」**是 B1 最大坑：`AtlasRegistry.register` 零调用 → inherit 永远报「没有
  图集记录」。查这类先 `grep -rn "\.register(" src/`。
- `register_layout(arch, uv_path, item_id=…)`：幂等、变了自动 `atlas-vN+1`+WARNING、坏文件返
  `None`。**图集是件级**（`item_id` 决定幂等范围，否则多件项目第二件判成换版、第一件引用被改写）；
  登记位在 runner `_register_archive`，**先图集后资产**，`size_px` 取 `results["M4"].metrics`。
- **引用必须来自登记者，不能编造**（r16 同类）：曾写死 `uv_atlas_ref="atlas-v1"`。改写入端只对
  新建档生效 → 存量靠 `h3d archive refs` 体检（五态 ok/empty/external/unverified/dangling，判据是
  **档案里的实体**）：`dangling` 置空+原值写 `params.ref_repair`，`unverified`（记录晚于资产）只
  报不修；退出码当门且要重算**盘上现在**的结论。**修复不能走 `register()`**（命中缓存返回旧记录）
  → 走 `Archive.rewrite_asset()`，只许改 `uv_atlas_ref`/`params`。
- 只重跑 M4：删 manifest 里 `M4/M5/M6` 三条（先备份）；**S1 无 `input_key`→`RESUME_LEGACY` 跳过**。
- **继承不重展开 UV**：用基准 mesh/uv/faces + 复测相机调同一采样器；前提 M4 无条件写
  `50_tex/<item>/<item>_uv.npz`。**uv/faces 属「展开后」拓扑**（xatlas 拆点，顶点数 ≥ 原始 PLY）
  → npz 必须连 `verts/normals` 存，否则越界（index 880 vs size 762）。
- **相机变换** `C'=s·R·C+t`、`R'=R_cam·Rᵀ/s`；`R'` 含 1/s **不是正交阵**（去掉=视野缩放 1/s）。
  判据用**投影恒等式**，不用覆盖率（球各向同性：反向变换 in_frame 0.799→0.717 而纹素差个位数→30+）。
- **版本冻结**：同版同 `uv_digest` 幂等、不同则 `AtlasFrozen`；`uv_digest` 先降 float32 再序列化
  （否则 npz 往返判成「图集变了」）。跨项目引用写**限定名** `relic_001@atlas-v1`。
- **五条失败必须显式**（不烘图）：无图集记录/布局缺失损坏/布局与拓扑不符/自动配准被闸门拒/未建档。
  GUI 回调「先写结论再 emit」会被 `apply()` 覆盖 → **先 emit 再落结论**；测试素材别用纯噪声照片
  （2 px 错位就跳几百灰度）→ 渐变+棋盘。
- **判据要带反向对照**：岛内逐 texel 中位|Δ| 正确 6.33 / 上下翻转 29.0 / 左右 31.7 / 旋转 180°
  31.7 / 平移 16 px 10.0 / 纯灰 62.7 → 只有「错时明显变大」才算有分辨力。

## 单期异常检测（B2/X3，r19）
- **背景必须是局部中位数**（`cv2.medianBlur`，8 位量化 O(1)/纹素，2048² 核 201 实测 99 ms 且与
  核长无关；**上限 255**，OpenCV 5.0 大核路径断言 `k<16`）。均值/裁剪均值都失效：窗内病变占
  16% 时正常纹素残差被抬到 0.16ΔE、斑块内只剩 0.84ΔE → 外面糊一圈、内部 z=2.79 漏检。
- **逐像素二分求局部中位数不成立**：二分让每像素各带阈值而窗口计数须同一阈值 → `(a<=mid)` 里
  a(q) 配 mid(q)，计数恒 0.538 不动，二分滑到边界、背景为 ∓amp、全图 z 归零。
- **尺度用块内 75 分位**（`q75/1.1503`）不是中位数：双峰残差（条纹）的中位数落到 0 → 尺度被
  地板接管 → 每条纹一半误报（6.3%）。**脏块（>2× 邻域）用邻域值替换**不是封顶（封顶只压到
  2 倍，粗糙区 ΔE=120 只得 z=2）。
- 多尺度（31/81/201）**取最大分**；颜色与纹理两路也**取最大不取平均**（平均会被另一路正常
  稀释：120 texel 色斑 10.9→2.96 漏检）。
- **纹理用结构张量不用 LBP**（popcount 丢方向，条纹与噪点分布重叠；一致性 1.00 vs 0.10 才分得开）。
  三闸门：当前有结构 + 基线有健康证据 + 基线本身一致（`|均值|≥0.5`）。
- **方向基线要排除可疑区**再估计（否则两侧都判「方向变了」，掩膜外糊 100 texel、IoU 0.61）；
  纹理证据只在颜色没响应的位置补位（颜色逐纹素精确，纹理只到 16 纹素块）。
- **注入方法本身会骗人**：`255-pixel` 在中间调上 ΔE 只有 14 → 必须用「L 推离中间调 + a/b 取反」
  并打印实际 ΔE。
- 真机（1.1.12/relic_001/2048²/岛内 61.5%，3.3 s）：ΔE=120 时 24 texel 4/4（增量 IoU
  0.96~1.00）、40 texel 4/4（0.71~1.00）、80 texel 1/3；基线 2.18%/35 处（k=3.5），k=4.5→
  1.12%/17、k=6→0.42%/8。**增量 IoU**（扣掉既有报警）才是边界精度。
- **80 texel 漏检是 σ 不是信号**（回路 `diag_x3_bigpatch.py`：三处残差都 97~113，σ_L
  30.3/53.2/15.7 → z 3.21/1.82/**6.37**，只最后那处中）。低信噪比不是估计器缺陷，
  别用「按面积累加证据」硬拉（σ=53 的根源就是材质交界，一拉就炸假阳性）。改为报下限：
  `anomaly_sigma_median`（最粗尺度岛内 σ_L 中位，**只取 L 通道**，取错通道恒为地板 2.0）。
- **该图集是上千个小 UV 岛拼的马赛克**：热力图最亮一圈压在岛边界（最近邻填充的固有偏差）；
  腐蚀 0/2/3/5/8 只把误报压到 1.77/1.56/1.35/1.21% 却赔 23%~64% 面积 → 不腐蚀，注明「岛边缘
  1~2 纹素内的结论不可靠」。连通域散布（641 个，≥24 纹素 425 个），真起过滤作用的是**面积比**
  （1e-4）不是绝对纹素数。
- 合成 12 项：6~120 texel 边界 IoU 全 1.00；纯净渐变/σ=1.5 噪声 0.000%；竖条纹贴横条纹关纹理
  不命中、开则 IoU≥0.7。
- **已接进编排层**（切片 5）：阶段 `X3` 在 `M4→P1→`**`X3`**`→M5`，顺序只在
  `_reorder_ext_stages` 一处钉（报表在 M5 里生成，排后面指标永远进不了报表）。默认开
  （`runner.x3_enabled=True`）但归 `OPTIONAL_STAGES`（坏图只降级）；**不进质量门**——「病害多」
  ≠「建模失败」。指标三个出口都要同步：`manifest_metrics` → `accuracy.csv` →
  HTML/论文表，少一个就是 r16 那个坑。结论绑 `anomaly_atlas_digest`（图集内容摘要前 12 位，
  因为 `atlas-vN` 只有档案继承时才有）。

## 双场景 / 尺度
- ①（带扫描件）≠②（纯照片）：S1/P1/X1/X2/X3 在①失败只降级，在② S1 是唯一几何源、失败即整件
  失败；判据 `stage in OPTIONAL_STAGES and bool(task.raw_paths)`。双源几何只 `novel≥0.15` 并入
  （novel=0 照合 → 面数+70%、rmse+34%、覆盖−60%）。
- **无度量基准不得缩放**：`_has_metric_anchor()=bool(task.raw_paths)`，无锚点 factor=1.0 +
  `reason=uncalibrated_sfm_units`；**SfM 的 PLY 塞进 00_raw 会被当锚点 → 假毫米数**。
- 照片目录须同源：优先 `task.root/00_photo` 再回退上级（X2 曾多写一层 `dirname` → 色卡不生效）。

## COLMAP / 稠密
1. mapper 多子重建，`sparse/0` 未必最大（取图像最多者，图像数读 `images.bin` 头 8B uint64）；
   `--output_path` 有 ExistsDir 校验，目录须先建。
2. 可复现靠 `num_threads 1`；两遍稠密序号各 1..N，判据 grep=2N，进度须识别遍数。
3. `tvec`≠相机中心：`C=-R_co^T@tvec`、`R=-R_co`（前 −Z）；错用致 `projected_cameras=0`、纹理全空。
4. `patch_match_stereo` 会跳过已有 photometric.bin（位姿变→点云错乱但 rc=0）；全量重建须连
   `<ws>/dense` 清。
5. 同根两批互毁（`is_open` 失败、GPU 争抢 45→145s）；`core/lockfile.py` 接管残留锁。
6. 超时覆盖两遍（30 张 3.84MP 实测 6815 s，`views×MP×45×passes` 下限 1800）；续算靠 `_pose_fp`；
   **超时也必须落盘**（否则残留上轮日志 → 误写「需 CUDA」）；取消须杀进程树。
7. `dense_max_image_size` 默认 1600；关 geom_consistency 后 fusion 须 `--input_type photometric`。

## 测试坑
- `--basetemp=<项目内路径>` 会让 36 项 runner/texture 测试互相污染 → 用默认 `%TEMP%`；**exit=1
  多为沙箱 safe-delete 拦截**，拿失败清单唯一稳手段是 `--junitxml`。全量 1103 项 ≈2.3 min。
