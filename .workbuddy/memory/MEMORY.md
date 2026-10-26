# H3D 长期记忆

## 环境
- 中小型文物（5~30cm）三维建模自动化系统，硕论项目，全开源。术语「中小型文物」/量词「件」。
  架构 P0→L1(io)→L2(preprocess)→M2；阶段 M1-M6 + S1(COLMAP) + P1/X1/X2(可选)。业务层禁 import Open3D。
- python `E:\h3d_lablab\.venv_lab\Scripts\python.exe`（PySide6 6.11/open3d 0.19/trimesh）；colmap
  `E:\colmap\colmap-x64-windows-cuda\bin\colmap` 4.1.1；打包 `h3d_gui.spec`（PyInstaller onefile，launcher
  双模式 GUI/`--batch`）；Inno `D:\h3d_relic_test\InnoSetup7\ISCC.exe`。当前版本 1.1.5（r9）。

## 交付
- 每任务必出中文结构化 PDF：`gen_*_content.py`→content.json→`render_pdf.py`，存 `output/`，名
  `中小型文物三维建模自动化系统_<主题>_<YYYYMMDD>.pdf`；出稿后 pymupdf 校验（页数/CJK/`\u25a1`、`\ufffd`=0）
  + fontTools 查 msyh cmap 缺字形。验证阶段少改代码控 token，末尾附「阶段分析 + 下一步」。

## COLMAP / 稠密
1. mapper 多子重建，`sparse/0` 未必最大（2+16>16）：取图像最多者归一化；图像数读 `images.bin` 头 8B 小端
   uint64。`--output_path` 有 ExistsDir 校验，目录须先建。
2. 可复现靠线程数不靠种子：`FeatureExtraction/FeatureMatching/mapper .num_threads 1`。
3. `tvec`≠相机中心：`C=-R_co^T@tvec`、`R=-R_co`（前 -Z）；错用致 M4 `projected_cameras=0`、纹理全空。
   capture_meta：R 世界→相机、t 相机中心，`pc=R@(X-C)`、z<0 前向、`u=fx*x/z+cx`（中位误差 0.4px）。
4. 两遍稠密各从 1 数到 N，判据 grep=2N；按序号做进度须识别遍数（否则假倒退）。
5. `patch_match_stereo` 跳过已存在 photometric.bin：位姿变而深度图残留→点云错乱但 rc=0。全量重建须连
   `<ws>/dense`（含 `stereo`）清；增量路径不传 dense。
6. 同根两批处理互毁（损 3h）：清 dense 与写入并发→`Check failed (file).is_open`；GPU 争抢 45→145s；
   manifest 交替覆写。`core/lockfile.py`（O_EXCL + PID 存活）接管残留锁。判据先 tasklist。
7. 超时须覆盖两遍：30 张 3.84MP 实测 6815s；`dense_timeout_budget=views×MP×45×passes`（下限 1800）。
   续算靠 `_pose_fp`（cameras/images/points3D size+mtime）：未变保留、变了整棵清。
8. 超时异常跳过落盘→残留上轮 CUDA 日志→manifest 误写「需 CUDA」：`TimeoutExpired(output=已收集文本)`
   + 超时也落盘（EXIT CODE: TIMEOUT）。异常路径日志落盘不可跳过。
9. 取消须杀进程树：Windows `taskkill /F /T`，POSIX killpg + start_new_session（否则 colmap 成孤儿占 GPU）。
10. `dense_max_image_size` 默认 1600（2400→1600 为 ×0.44），与稀疏解耦；关 geom_consistency 后 fusion
    须 `--input_type photometric`。

## 贴图 / 几何
- **像素系必须换算**：内参属 `photos_small`（2400 长边），M4 读 `00_photo` 原图，实测 1.874×；旧实现直索引
  →颜色全取左上角 53% 且无报错。写 `pixel_scale`，凡「内参 vs 图像」不同源必须显式换算并落指标。
- 照片不可 float64 常驻（9.7GB）：uint8 + 2GB 预算等比缩小（因子被 pixel_scale 吸收）。
- **UV 必须逐纹素光栅化**（原「每面 4 点溅射」→图集 96% 黑洞）；AABB+重心掩膜→44.9%、岛内填充 73.1%；
  退化/亚纹素三角形回退点质心 1 纹素。**遮挡降权不硬剔除**（硬剔除 79.7%→47.8%），0.05 降权，cell≈长边/160。
- **法线朝向须相机投票**（`consistent_tangent_plane` 只保相对一致，实测 68% 顶点反向）→ M1
  `orient_normals_by_cameras`（读 capture_meta `images[].t`）。判据坑：环形布光下「朝向相机比例<0.5」正反
  都≈0.5 会误翻；改「能被至少一台相机正面看到的纹素比例」实测 63.3%（阈值 0.25）。
- **泊松掩膜不可用亮度阈值**（青铜/漆木整片<20 被判空洞）→ 用真实 `painted` 掩膜（`_L1.0_mask.png`）；
  `_bleed` 须零边界平移（`np.roll` 跨边界串色）。
- **质量口径**：`coverage_ratio`=图集像素利用率、`atlas_utilization`=UV 岛占图集、`fill_ratio=coverage/
  atlas_utilization`（掩膜优先兜底 luma）。1024² 图集 5.87% 属正常，引用须写清口径。
- **dense_gate 密度过滤曾只赋 points**→fused_dense 只有 xyz；现同筛 colors/normals + `has_colors_out`+WARNING。
- **OBJ 贴图旁挂**（obj→mtl→`map_Kd`）：只 copy2 .obj 缺贴图，`_copy_obj_sidecars` 逐层跟随并改写引用。
- **Open3D 0.19 写 GLB 静默丢纹理**→必须 trimesh：`baseColorFactor=[255,255,255,255]`（默认 0.4 灰压暗
  60%）、metallic 0、roughness 1、doubleSided；trimesh 要**逐顶点** UV（N×2），Open3D 逐角（3F×2）按面回填。
- Open3D 0.19 本机**无 EGL 离屏**，目视验证只能自写软件光栅器；无色彩顶点全 0→黑模（刷 `default_gray`）。
- **M4 图集复用**（1.1.6）：`texture/atlas_cache.py` 两级键——UV 级=几何指纹（float32 顶点+面，
  顺序参与）、纹理级=几何+法线+位姿(名排序 R/t)+照片 SHA-1+采样参数+CACHE_VERSION。命中复用
  `vmap/indices/uvs`（`rebuild_with_uv`）与贴图+掩膜+**首件 metrics**（漏了就报 0 覆盖率）。
  导出不缓存（含件号、要改 MTL）；缺一不复用/参数变即重算/三处留痕/损坏改名 `.corrupt-<ts>`/
  留 24 套。实测 22.5~24.4→0.5~0.6 s/件，贴图 SHA-1 与不复用逐字节一致。
- **Open3D 写带纹理 OBJ 慢 25 倍**（5.34 s/74k 面）：逐角重复写 vt（221k 条）。手写
  `_write_obj_textured`（vt 逐顶点、**V 不翻转**、MTL+PNG 直接落）=0.21 s；回读等价已验证。
- 碎片化：`component_metrics`（`n_components`/`largest_component_ratio`，>3 块且最大<60% WARNING）；
  RANSAC 三平面剔除把最大块 27.8%→86.6%，**未实装**。

## 双场景
- ①（带扫描件）≠②（纯照片）：S1/P1/X1/X2 在①失败只降级；在② S1 是唯一几何源、失败即整件失败。判据
  `stage in {S1,P1,X1,X2} and bool(task.raw_paths)`；一刀切 break 会让只有扫描件的用户建不出几何。
- 双源几何不无条件融合：只 novel≥0.15 并入（novel=0 照合→面数+70%、rmse+34%、纹理覆盖−60%）。
- **无度量基准不得缩放**：`_has_metric_anchor()=bool(task.raw_paths)`，无锚点 factor=1.0 +
  `reason=uncalibrated_sfm_units`。几何尚未生成属正常态（INFO），不是「探测失败」。
- 照片目录须同源：S1/P1 优先 `task.root/00_photo` 再回退上级；X2 曾写 `dirname(task.root)` 多一层→色卡不生效。

## 工程
- **单元测试 + 源码基准全绿 ≠ 端到端可用**：缓存类优化必须在**打包版多件批处理**里验证命中率；
  手工喂同一份中间产物会绕开上游的非确定性（M3 泊松踩过）。
- **打包版与源码行为可能不同，按 exe 实测**：launcher 白名单透传 CLI；凡"exe 才有的入口"必须带 `timeout`。
  `H3D.exe --batch` 只认 `--batch <配置>`（位置参数），写 `--batch -c x.yaml` 会把 `-c` 当配置路径。
- **派生状态原子写 + 损坏可恢复**：manifest `tmp+fsync+os.replace`、损坏备份 `.corrupt-<ts>` + WARNING +
  空 manifest 重跑、`stages` 非 dict 就地纠正（taskkill 是常态，否则项目永久跑不起来）。
- 配置读 `utf-8-sig` + 捕获 `UnicodeDecodeError`→`[E001] 请另存为 UTF-8`（BOM 本身 PyYAML 能容忍）。
- 任务发现过滤 `_is_junk_dir()`：`.git`/`__MACOSX`/`$RECYCLE.BIN`/`System Volume Information`/`._xxx.ply`。
  `validate` 0 件必须 rc=1。外部子进程统一 `proc.run_external`（隐藏控制台+显式 cwd+容错解码）。
- **`QThread.run()` 异常不外传**（打 stderr 静默结束=假卡死）：线程+解析外层 try/except→可见错误 +
  `finished_ok(False)`；Qt 槽禁未捕获异常。
- **进度分母必须是工作量，不是种类**：旧式 `len(stages_done)/len(STAGE_ORDER)` 中 stages_done 按阶段 id
  去重、无「件」维度，分母又含从不启用的 P1/X1/X2 → 第 1 件跑完即 7/10=**70% 锁死**。现用 `ProgressModel`：
  每件独立计分、整批平均、阶段按耗时加权（S1 .90 / M4 .06 / 其余<.01），S1 子步骤线性映射；S1 跳过的件换
  `W_SCAN` 权重归一（否则开跑白拿 90%）；界面显示「第 k/N 件」。进度统计也须绑当前任务（别「取最大」）。
- 界面进度四层：子步骤+文件名 / n/N / 已运行时长 / 心跳（>30s 显示「最后输出 Ns 前」）。
- **断点续跑跳过 ≠ 无产物**：`is_done()` 命中须 `task.restore_stage()` 恢复 outputs/metrics。
- **同照片多件**：5 件 00_photo 内容相同却各解 SfM（1801s×5）。按「名+大小+内容 SHA-1」比对，命中复制
  `fused_dense.ply` + `capture_meta.json`（S1 →0.2s）。三边界：**缺一不复用**（只有 ply 会让 M4 崩）、
  **参数不一致不复用**、**日志+manifest `s1_reuse`+报表三处留痕**；无索引时扫同根 ≤12 件、参数未知降
  WARNING。开关 `sfm_solve.reuse_identical_photos`（默认 true）。
- 报表属派生视图，绘图失败降 warning，不推翻主数据。**禁静默降级 / `except: pass`**：兜底须同时写
  metrics + WARNING（novel 曾被吞数周）。测试断言锁行为本质，不只断言「不报错」。
- **「能点选」≠「能导入」**：先分 GUI 选不中还是任务发现丢弃；同控件两入口（`browse_mode`）；过滤器与校验
  白名单同源（`media.image_dialog_filter()`）。
- 沙箱删中文名文件触发 safe-delete 失败；zip 用 `zipfile.ZipFile(dst,"w")`；禁 PowerShell `Add-Type`。
- PDF 渲染器：`_inline` 只支持 `<b>`（+`<code>`）；`flowchart` 用 `nodes`(`label`)+`edges`；`build_doc` 页眉
  右侧固定传 date。

## 打包 / 发行
- 顺序：改码→重打包 exe→建发行目录→update_release_meta→build_installer→**最后 sync 快照**→zip。
  PyInstaller ≈3.5min、Inno ≈1min、zip 3s（465MB）。iss/README/VERSION 必须 UTF-8 **with BOM**。
- `PYTHONIOENCODING/PYTHONUTF8` 打包环境无效，须进程内 `reconfigure`；**windowed 下 `sys.stdout/stderr`
  为 None**，库的 `stderr.write` 抛 AttributeError 被 Qt 槽吞→预览空白，`_force_utf8_stdio()` 置哑流。
- **`_MEI` 父子共享**：GUI 退出即删，后台 `--batch` 后续 import 全炸；shell out 自家 exe 须清 `_MEIPASS2`
  等 `_PYI_*`。
- spec excludes 无 open3d（145M pybind + `tbb12.dll` 正常进包）；`--batch` stdout 双写（`_Tee`）；延迟
  import 必须进 hiddenimports，改后 `pyi-archive_viewer -r -b` 复核。资源走 `asset_path()`。
- **exe 图标/版本资源编译期烧进 PE**，改 assets 必重打包；验新版看 exe FileVersion、欢迎页、报表页脚。
- **版本号单一来源扩到发行层**：`make_version_info.py` 由 `__version__` 生成版本资源、spec 打包开始强制
  重算；`_relver.py` 推导 `REL/VTAG/REL_NAME`；iss 用 `#ifndef MyAppVersion` 兜底 + `build_installer.py`
  以 `/D` 注入（编译前校验 exe PE 版本==`__version__`）。
- `update_release_meta.py` 幂等自愈：章节块首尾分隔线、删旧块保留下一章分隔线、术语整词替换；章节标题
  是前缀匹配（「修订九」下有「修订九之二」）→ `count()` 用唯一标记；VERSION 清单行按 `H3D-Setup-v` 前缀；
  SHA 段标签行随版本走。改后连跑三次字符数一致。
- Inno：不写 `VersionInfoVersion` 属性页显示 `X`；用 `{#MyAppVersion}.0` + ASCII `H3D Project`。属性页尾部
  空格是定长填充，PowerShell 拼接会误读成 `X`，判真假用 VerQueryValue。安装包 SHA 无法自指（先占位后回填）。
- `release/H3D_v<v>/` = exe + Setup + README + VERSION(SHA-1) + `configs/` + `docs/` + `source/`（排
  `packaging/Output`）+ `tools/`；zip 用 `make_release_zip.py`（testzip + 必须项清单，>20MB `ZIP_STORED`）。
- `sync_source_snapshot.py` 先清空再复制（只覆盖会残留已删模块），带 `assets/`；**必须最后同步**（先 sync
  后改 iss，快照里仍是旧 iss 且缺新脚本，zip 已打出只能重做）。报表目录统一 `<项目根>/report`。

## 跨层契约
- **终态必须四态**：完成 / 跳过 / 失败 / 未执行。断点续跑全跳过时 `done=0 skipped=N` 是
  **正确终态**；`ok = failed==0 and done>0` 会把幂等重跑判成失败（实测弹「运行未完成」
  + 进度归零）。判据须 `failed==0 and rc==0 and done+skipped>0`；日志解析要读 `skipped=`
  （旧日志按 `total-done-failed` 兜底）。
- **完成行不等于进程结束**：CLI 打印「批量处理完成」之后还要生成 3 个报表文件。GUI 见到
  完成行只能 `break`，必须读完余下 stdout 并 `wait()`，否则报表半写、用户此刻再点会撞数据根锁。
- **QThread 覆盖即崩**：`self.runner = BatchRunner(...)` 前必须查 `isRunning()`，旧对象
  `deleteLater()` 延迟释放；按钮禁用只是 UI 约定，函数入口要自己判（`_busy()` + `_starting`）。
- **配置必须「读改写」而非「模板重建」**：GUI 打开历史项目只传 root/name/scene 时，
  `to_yaml` 会用模板覆盖 `sfm_solve.dense`（true→false，直接跳过稠密）等非控件字段。
  正确做法：`from_root()` 留原文件作底稿，只覆盖控件字段；无控件的专业参数用 `setdefault`。
- **控件必须有回写通道**：`apply()` 不写的控件就是假控件（接缝平滑/几何质检/top-k/每面采样/
  密度过滤曾全部无效，改了不生效且无告警）。改界面后自查三问：值进配置了吗？终态认全了吗？连点两次会怎样？
- **产物路径的键是 `item_id`（`00_raw` 子目录名），不是项目名 `cfg.name`**：GUI 曾用项目名拼
  `80_export/<name>/`，示例项目同名自测全绿、真实项目必然预览空白。反查走 `h3d.gui.items`。
- **阶段终态必须一行可解析日志**（界面唯一输入）：`M2 完成：<item>（…）` / `○ <SID> 跳过：<item>（原因）`。
- `STAGE_ORDER` 必须与 `STAGE_REGISTRY` 一致（漏 P1→`lamps["P1"]` KeyError 被 Qt 吞、界面停住）。
- 小件「体素<点间距」降采样是空操作：内核 WARNING 建议 ≥2×点间距；界面档位语义=基准器物(3.7m)的米制值。
- 术语替换脚本长串在前单字在后；`--dry-run` 先出清单；不改 `release/` 快照、memory 历史、CHANGELOG 历史。

## 示例数据
- 球面随机平面波场 `Σa·sin(f·(d·p)+ψ)`，a∝f^-0.5，能量 3~12px；`PIX_PER_UNIT≈163.6`；带限纹理 1× 求值后
  `np.repeat` 广播（快 4×）。反例：参数域正弦、周期方格、高斯斑点（85s/图+3.3GB）。形体用三轴不等椭球
  `SHAPE_SCALE=[1.0,1.30,0.85]`（正球尺度不可观测，ICP 会乱缩）。

## 待办
- **M5 中误差口径**：`qc_rmse()` 末尾 `*1000` 在纯照片任意单位下等于「1 单位当 1 米」，
  实测报 41.16「mm」而包围盒对角线只有 6.06 —— 真值是 0.041 单位（对角线 0.68%）。
  `metric_units=False` 时应改报 `rmse_rel = rmse/diag`，mm 只在有激光锚点时显示。论文可信度问题。
- **GUI 修复未发版**：15 项已修 12 项（源码 717 passed），exe 仍 1.1.6 行为，
  需走 v1.1.7 完整打包流程（version_info → PyInstaller → installer → --fill → sync → zip）。
- **M3 泊松必须 `n_threads=1`**（多线程逐位不可复现：同点云三次三份面表，破坏论文复现与缓存判等）；
  代价 37k 点 0.84→1.51 s，可 `mesh.poisson_threads` 换速度但要接受不可复现。大点云待测。
- 缓存命中率只在**真实多件批处理**里才暴露：源码基准喂同一份 30_dense 会绕开 M3（踩过）。
- GLB 仍由 trimesh 写（0.97 s/件），手写可再省 0.2 s；纹理级判据可从「法线指纹」放宽为「朝向投票结果」。
- 镂空/手臂内侧缺照片覆盖，Poisson 留空洞，需补拍。
- S1 写死 `single_camera 1`，多机位混拍会误合并；稠密失败用 sparse points3D 兜底。
- 真机待验：GUI 弹窗点选、多进程抢锁、v1.1.6 真实 5 件复测（预期 7 min → 1.6 min）。
