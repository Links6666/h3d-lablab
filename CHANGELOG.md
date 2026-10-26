# CHANGELOG

## v1.1.3-r7.3 (2026-10-23) —— 端到端巡检（bug-bash）修 6 处
按 e2e skill 的 bug-bash 流程（charter → 并行 fan-out → 合并 → triage → 每条写
复现测试），对 CLI / 任务发现 / manifest / 配置 / 打包版契约做了一轮真实巡检，
**每条缺陷都先写出失败的复现测试再修**（`tests/test_bugbash_20261023.py`，9 项）。

### 修复
1. **打包版只认 `--batch` / `--report`**：`H3D.exe --version`、`doctor`、`validate`、
   `init` 全部落进 GUI 分支 —— 脚本里表现为**永久挂起**（实测卡死 10 分钟）。
   现按 CLI 子命令原样透传，无参数仍进 GUI。
2. **manifest 非原子写 + 零容错**：硬杀/断电留下截断 JSON 后，之后每次加载都抛
   `JSONDecodeError`，项目从此再也跑不起来（GUI 只弹崩溃框）。改为
   `tmp + fsync + os.replace` 原子写；损坏时备份 `<name>.corrupt-<时间戳>` +
   WARNING + 按空 manifest 重跑；`stages` 类型异常就地纠正，不再把
   `AttributeError` 推到很远的地方。
3. **系统目录被当成文物**：`00_raw/.git`、`__MACOSX`、`$RECYCLE.BIN`、
   `System Volume Information` 以及平铺模式的 `._xxx.ply`（AppleDouble）都会
   各自成为一件任务，随后在 M1 报"点云为空"（实测任务数 1 → 3）。
4. **配置存成 ANSI/GBK 时报 Python 栈**：中文 Windows 记事本"ANSI"就是 GBK，
   `UnicodeDecodeError` 直穿到界面。改为 `[E001] 配置文件编码不是 UTF-8 …请另存为
   UTF-8`；同时用 `utf-8-sig` 读，BOM 不再变成「未知参数: ['\ufeffproject']」。
5. **`validate` 在 0 件文物时 rc=0**：拿它当 CI 门禁时空项目会蒙混过关。
   现在明确失败并给出排查方向（目录名/扩展名/纯照片开关）。

### 测试
- 新增 `tests/test_bugbash_20261023.py`（9 项，修复前 7 红；含"写 manifest 中途
  失败原文件必须完好""截断文件必须留证并恢复""无参数仍启动 GUI"等反向约束）。

## v1.1.2-r7.2 (2026-10-18) —— 新增启动自检（boot / project 两套）
### 为什么
历史上大半故障不是算法错，而是「环境或状态已经坏了，但开跑前没人看一眼」：
同一数据根挂着上次没退干净的进程（互删产物、争抢 GPU，损失 3 小时）、崩溃
残留的锁把人挡在门外、manifest 写着 done 但产物已被删（续跑静默跳过）、
`_MEI` 解包目录被父进程提前删除、照片与位姿尺寸不同源导致贴图取错区域……
这些**开跑前 300 ms 就能查出来**。

### 新增
- `src/h3d/health/`：自检内核（检查注册表 + 并行执行器 + 单项/整体预算 +
  一行可解析摘要日志 + 结果落盘 `90_meta/preflight_project.json`）。
- 两套作用域，侧重不同：
  - `boot`（软件启动，≤900 ms）：解释器/依赖、`_MEIPASS` 可写与父子共享、
    COLMAP 路径、GPU 与显存、磁盘、临时目录、**遗留 H3D/COLMAP 进程**、
    历史项目活锁、上次崩溃记录；
  - `project`（项目运行前，≤1400 ms）：数据根可写、输入是否为空、**数据根
    活锁**、`done` 阶段产物是否还在、稠密深度图残留、照片与位姿尺寸同源、
    跨件同名照片、磁盘空间。
- 接入点：`launcher` GUI 启动前（早于 QApplication）、`cli batch` 前置、
  `BatchProcessor.run` 前置（只记录不阻断，避免改变 API 行为）、GUI「开始
  运行」前（阻断项弹窗，可「仍然运行」）。
- `h3d doctor [-c config] [--fix] [--json] [--all] [--deep]`：命令行自检，
  退出码 0/1 可直接用于脚本判断。
- `batch --no-preflight` / `--fix`、`H3D_SKIP_PREFLIGHT=1`：明确的应急通道。

### 性能（实测本机）
| 项 | 优化前 | 优化后 |
|---|---|---|
| boot 整体 | 2156 ms（超预算） | **210~240 ms** |
| 进程表 | `tasklist` 1594 ms | `psutil` 17 ms（缺失时 ctypes 兜底 ~25 ms） |
| GPU | `nvidia-smi` 两次 2015 ms | 一次 **219 ms** |
| COLMAP | 实跑 `-h` 1235 ms | 文件大小判定 15 ms（深度校验移到 `doctor --deep`） |
| project 整体 | — | **592~672 ms**（瓶颈是数据根写探测，Windows 实时防护下必需） |

关键实现细节：**绝不能用 `with ThreadPoolExecutor(...)`** —— 其 `__exit__` 是
`shutdown(wait=True)`，会把已超预算的自检再拖住一整个慢检查（实测 900→2156 ms）；
`run_external` 的 `resolve_cwd` 回退到 `~` 时写探测要 750 ms（用户主目录被实时
扫描），探测类子进程一律显式给 `cwd=%TEMP%`。

### 测试
新增 `tests/test_health/test_preflight.py`（27 项：模型/执行器异常与超时/
探测原语/九类项目场景坏-好对照/CLI 阻断与绕过）。全量 **655 passed**。

## v1.1.1-r7.1 (2026-10-18) —— 修「3D 预览无颜色 / 效果糟糕」
### 背景
用户以 30 张 4496×3000 照片（纯照片场景②）实机跑出一件，交付模型**几乎全黑、
碎片化、预览里器物缩成一个小点**。逐层复现后定位到 4 个代码级根因。

### 根因与修复
| 编号 | 层 | 缺陷 | 修复 |
|---|---|---|---|
| E1 | M4 贴图 | 主循环视线方向写成 `X−C`（法线自检却用 `C−X`）→ `cos>0.3` 判据只在**背面**成立 → 每个纹素都去器物背后采背景 | 统一为 `C−X` |
| E2 | M4 贴图 | z-buffer 传 `z`（`z<0` 为前方、越远越负），"最小值=最近"不成立 → 遮挡判据恒假 | 传 `-z` 正值深度 |
| E3 | M4 贴图 | 碎块网格局部法线符号不一致，全局 ±1 翻转救不了（正向 61.6% / 取反 79.9%） | 按"能看到更多纹素"选符号 + 残余纹素按 `-n` 二次询问且**要求深度可见** |
| E4 | M1 几何 | 背景被切成零散小片，把包围盒从 4.86 撑到 14.64 → 预览里器物缩在角落 | 新增 `h3d/preprocess/background.py`：DBSCAN 主簇保留 + 可选平面剔除 + 双重安全网 |

### 指标（relic_001 实测）
- 贴图岛内填充率 55.0% → **94.5%**；图集像素利用率 33.7% → 59.5%；
- 几何 60258 点 → 保留主簇 42990 点（27 簇取 1），重建出一件完整器物。

### 其他修复
- `dense_gate` 密度过滤重建点云时**保留 colors/normals**（原先只剩 xyz，
  COLMAP 的 RGB 与法线 100% 丢失）+ `has_colors_out` 指标 + 无颜色 WARNING。
- 断点续跑 `restore_stage()`：`is_done()` 命中时恢复 outputs/metrics，
  修复 M5 `[E100] 参考点云为空`；M2 单站也落 `20_registered/<item>/<item>_s01.ply`。
- 无度量基准（纯照片）不再按 SfM 任意单位做尺度缩放（`uncalibrated_sfm_units`）。
- 报表：未执行阶段成行、`nan`→`N/A`、新增「几何与尺度」「纹理质量」两节 +
  自动诊断；CSV 增「产物数」「异常/备注」列；耗时图自动对数坐标 + 数值标注。
- `h3d_gui.spec` 于 `hiddenimports` 增加 `h3d.preprocess.background` 等
  延迟 import 模块（不声明则打包版在 M1 阶段 ModuleNotFoundError）。
- 测试 628 项全通过（新增 `tests/test_preprocess/test_background.py` 6 项）。

## v1.1.0-r7 (2026-10-18) —— 重编发行（含 r6.3~r6.6 全部修复）
### 产物
- `H3D.exe`（onefile，新图标 v3，版本资源随 spec）；
- `H3D-Setup-v1.1.0.exe`（Inno Setup，UTF-8 BOM 脚本）；
- `H3D_v1.1.0_发行包_20261018.zip`（含 exe / 安装包 / configs / docs / source / tools）。
### 打包口径变化
1. 构建目录换新（`pyi_dist_v4` + `build_work_r7`），不复用旧 `build/` 缓存 ——
   PyInstaller 曾因残留 `.pyc` 把已删模块重新打进包。
2. `h3d_gui.spec` 的 `datas` / `icon` 全部指向 `h3d_icon_v3.*`，
   旧的 `h3d_icon_v2.*` / `h3d_icon.ico` 不再进包（避免两套图标并存）。
3. `hiddenimports` 增 `h3d.gui.resources`、`h3d.core.lockfile`、`trimesh` 四模块。
4. 安装脚本合并为单一 `packaging/H3D_installer.iss`（此前 1.0.0 与 1.1.0 两份并存，
   改一份漏一份）。
5. 发行元数据统一由 `scripts/update_release_meta.py` 生成（幂等），
   大小与 SHA-1 由文件实测回填，不再手抄。
6. **可执行文件写入版本资源**：`packaging/version_info.txt`（exe 的
   FileVersion/ProductName）与 iss 的 `VersionInfoVersion`（安装包）。
   此前两者属性页都是空的/显示 `X`，用户拿到包无法判断版本，只能看图标。
7. **报表目录统一为 `<项目根>/report`**：内核默认值原为 `output/report`，
   而 GUI、README、结果页都按 `report/` 查找 —— 用 `configs/example.yaml`
   跑 CLI 的用户找不到报表。现内核默认、示例配置、GUI 三者一致。
8. `scripts/sync_source_snapshot.py` 改为**镜像同步**（先清空再复制）：
   此前只覆盖不清理，已删除/改名的模块会残留在快照里，评审看到两份同名
   文件；且快照缺 `assets/`，拿到源码也重打包失败（spec 引用图标）。

## v1.1.0-r6.6 (2026-10-18) —— 全链路审计：跨层对齐七处缺陷
### 背景
连续多轮只校验了「纯照片」场景，且从未拿到过一次完整模型输出。从链路末端
（导出/预览）向上逐层复核，发现底层算法、中层工程路径与 GUI 表现三层之间存在
契约不一致 —— 单层看都能自测通过，串起来就断。

### 缺陷与修复
| 编号 | 层 | 缺陷 | 修复 |
|---|---|---|---|
| D1 | 中层与 GUI | GUI 用项目名当文物编号拼产物路径 | 新增 `h3d/gui/items.py` 反查编号，六处调用点改用 |
| D2 | 内核与 GUI | 单站 M2 返 DONE 却不打完成日志 | 补完成日志，灯能熄灭、进度计入 |
| D3 | 内核与 GUI | 真跳过只 `log.debug` | 各 SKIPPED 点补 `skip_reason`，编排层打 INFO 行 |
| D4 | GUI 与内核 | `STAGE_ORDER` 漏 P1（10 vs 9） | 补 P1；`_on_stage` 对未登记阶段只记日志不抛 |
| D5 | 全局 | 版本号四处不一致（含模板写死 0.1.0） | `h3d.__version__` 单一来源，报表 `{{ version }}` |
| D6 | 算法与 GUI | 小件上体素 < 点间距致降采样静默失效 | WARNING「降采样实际不生效」+ 档位语义改「基准器物取值」 |
| D7 | 工程卫生 | `history.__all__` 指向不存在名字 / `Optional` 未导入 / 阈值回写静默 pass | 逐项修正，阈值回写失败写 metrics |

### 验证
- 端到端（源码态，16 张 640px）：S1 16/16 → M1 4363 点 → M2 完成 →
  M3 19829 面 → M4 photo 15 s → M5 coverage 100% / 27.39 mm → M6 四件套。
- 把真实 `run.log` 回灌界面状态机：修复前 2 盏灯永不熄灭，修复后 0 盏。
- 测试 622 项通过（新增 20 项）。

## v1.1.0-r6.5 (2026-10-18) —— 定位转向中小型文物：术语统一 + 应用图标更换
### 背景
应用对象由「造像类文物（米级）」转为「中小型器物类文物（铜镜／印章／小件陶瓷
玉器，5~30 cm）」，但界面标题、报表页头、安装包名称与代码注释仍沿用旧表述，
与产品定位冲突；应用图标也仍是旧版圆标。

### 改动
1. **术语统一**（`scripts/rename_statue_terms.py`，43 个文件 129 处）
   - 产品全名 → 「H3D 中小型文物三维建模自动化系统」；
   - 「造像类文物」→「中小型文物」、「彩塑造像」→「彩塑文物」、
     「示例造像」→「示例文物」；
   - 量词「尊」→「件」（佛像用尊、器物用件），报表「各尊处理状态」→
     「各件处理状态」、CLI「发现 N 尊造像」→「发现 N 件文物」。
   - **替换必须「长串在前、单字在后」**：若先做 `造像→文物`，则
     `造像类文物` 会被拆成 `文物类文物`。脚本支持 `--dry-run` 先出清单。
   - 发行快照 `release/`、历史日志与 CHANGELOG 本身不改写（历史事实不动）。

2. **应用图标更换**（`scripts/make_icon.py`）
   - 源图 1442×1462 JPG：裁方（去掉底部投影）→ 圆角 alpha 掩膜
     （半径 0.20 边长，4× 超采样抗锯齿）→ 输出
     `assets/h3d_icon_v3.png`(512) 与 `.ico`(16/24/32/48/64/128/256)。
   - 直接把 JPG 转 ICO 会有两个问题：卡片外的白底变成不透明方块（深色任务栏
     上很脏）、底部投影被缩进小尺寸后糊成灰边。掩膜半径刻意取大一点，
     宁可多切进米色卡面，也不留白牙。

3. **运行期窗口图标**（新增 `src/h3d/gui/resources.py`）
   - `asset_path()` 依次查 `sys._MEIPASS/assets` → 源码树 `<repo>/assets` →
     cwd。**不能**用 `__file__/../..` 硬拼：打包后 `__file__` 在临时解包目录，
     层级关系与源码树不同。
   - `run_gui()` 统一 `QApplication.setWindowIcon()`，欢迎页/向导/主窗口/
     所有对话框自动继承——逐窗口设置必然漏一个，出现「一个窗口一个图标」。
   - 资源缺失只降级为默认图标并返回 False，不影响启动。

4. **欢迎页页头**：左侧 84 px 应用图标 + 右侧标题/副标题（图标缺失时只显示文字）。

5. **打包与安装**
   - `h3d_gui.spec`：`icon=` 与 `datas` 换 v3；hiddenimports 登记
     `h3d.gui.resources`（`run_gui` 内是函数级 import）。
   - 两个 `.iss` 的 `SetupIconFile` 同步换 v3；`H3D_installer.iss` 原本无 BOM，
     已补 UTF-8 BOM（Inno 按 ANSI 解析会中文乱码）。

### 验证
| 项目 | 结果 |
|---|---|
| 三处窗口标题（欢迎页/主窗口/向导） | 均为「H3D 中小型文物三维建模自动化系统」 |
| `QApplication.windowIcon()` | 非空，512×512 |
| ICO 内嵌尺寸 | 16/24/32/48/64/128/256 共 7 档 |
| 欢迎页渲染 | 图标与标题正常（`output/_welcome_preview2.png`） |
| 全量测试 | 602 项全通过 |

## v1.1.0-r6.2 (2026-10-17) —— 稠密阶段耗时归因：并发互删 + 超时预算 + 断点续算
### 实机现象
用户 21:40 起跑、22:56 取消，等待 1.5 小时仍无产出。日志显示
`patch_match_stereo ... timed out after 3600 seconds`，界面/manifest 却写
「dense 需要可用 CUDA GPU」。

### 根因（六条，按影响排序）
1. **两个批处理进程并发**（PID 26048 / 26332）。新进程 21:40:11 清
   `<ws>/dense`，旧进程 21:45:49 写 `DSC_0037.JPG.geometric.bin` 时报
   `Check failed: (file).is_open()` + `Could not create logging file`
   （cwd 被删）→ 旧进程 69 min 计算作废；两边抢 GPU 使单张深度图从 45 s
   退化到 87 s（第一遍）/ 145 s（第二遍）；`run_manifest.json` 被交替覆写。
2. **4 尊造像 = 同一批 30 张照片**（`model_analyzer` 三份 sparse 逐位相同：
   Points 13990 / Obs 55242 / 0.496871 px）→ 计算量 ×4。
3. `geom_consistency=true` 使 `patch_match_stereo` **跑两遍**，总需求 6815 s。
4. 稠密沿用 2400 长边（3.84 MP），而 1600 长边仅 1.71 MP（×0.44）。
5. 超时预算 `timeout=3600` 写死且覆盖**整条命令**（含两遍）→ 必然腰斩。
6. 超时后不可续算：`run_dense_stereo` 入口无条件 `rmtree(stereo)`，
   35 个 bin（30 photometric + 5 geometric）被下一轮删光。

### 修复
- **单实例锁**（新增 `core/lockfile.py`）：数据根 `<root>/.h3d_run.lock`，
  `O_EXCL` 抢占 + PID 存活检测；崩溃残留自动接管；`release` 只删自己那份。
  同一目录第二个进程启动即被拒，消息含占用者 PID。
- **动态超时预算**：`dense_timeout_budget(views, MP, passes)` =
  `views × MP × 45 s × passes`（下限 1800 s）；30 张 3.84 MP 双遍 → 10368 s。
  可用 `dense_timeout_s` 显式覆盖。
- **位姿指纹续算**：`<dense>/.h3d_pose_fp` 记录 cameras/images/points3D 的
  size+mtime；位姿未变**保留**深度图（COLMAP 自动跳过已完成视图），
  变了才整棵清。`force_redense=true` 强制重算。
- **稠密分辨率解耦**：新增 `dense_max_image_size`（默认 1600），
  走 `--PatchMatchStereo.max_image_size`；稀疏仍用 2400。
- **两遍可关**：`dense_geom_consistency=false` 时只跑 photometric，
  fusion 自动改 `--input_type photometric`。
- **超时可诊断**：`run_external` 的 `TimeoutExpired` 携带已收集输出；
  超时也落 `colmap_step12.log`（`EXIT CODE: TIMEOUT`）；错误文案给出
  「已完成 photometric N/M、geometric K/M / 重跑会断点续算 / 三条加速建议」。
  同时避免用旧日志误判「需要 CUDA GPU」。
- **取消杀进程树**：Windows `taskkill /F /T`、POSIX `killpg`
  （`start_new_session=True`），不再遗留 `colmap.exe` 占满 GPU。
- `h3d_gui.spec` 增加 `h3d.core.lockfile` hiddenimports（函数内延迟 import）。

### 校验
- 新增 `tests/test_runner/test_run_lock.py`（7 项）、
  `tests/test_runner/test_dense_budget.py`（14 项）。
- 全量 **568 passed / 0 failed**（基线 547 + 21）。
- 端到端冒烟（3 张 1200 px 真 COLMAP）：S1 70.0 s，3 photometric + 3 geometric，
  指纹落盘，62268 点 → M1→M4 全通，锁无残留；M5 因 coverage 6.3% 按阈值失败（预期）。
- 诊断文档：`output/造像类文物三维建模自动化系统_稠密阶段耗时归因与并发互删诊断_20261017.pdf`
  （14 页 / 3412 字 / 0 豆腐块）。

## v1.1.0-r6.1 (2026-10-17) —— 两遍稠密区分 + 中文乱码
### 补充修复：patch_match_stereo 实际跑两遍
- **日志证据**：`colmap_step12.log` 里 `Processing view n / 30` 出现 **60 次**，
  序号 1..30 之后再次 1..30。第一遍 photometric，第二遍 `geom_consistency`
  校验，分别产出 `*.photometric.bin` 与 `*.geometric.bin`，耗时各约 23 分钟。
- **若不区分**：界面出现「深度图 30/30 → 深度图 1/30」的**进度倒退**；
  文件计数因取最大值而恒为 30 不动；进度条映射到顶后回退。
- **修复**：`_ProgressLogger` 增加遍数状态机（`cur == 1 and max_view >= total`
  判定进入下一遍），第二遍标注「几何校验」；界面按 `phase` 分别统计
  photometric / geometric，切换时归零；进度区间重划为
  深度图 42→65%、几何校验 65→90%、融合 92→99%。
- 测试 +4（`TestTwoPassDepthMaps`），全量 **547 项 0 失败**。

### 中文乱码
- 打包版子进程 stdout 默认 cp936，GUI 按 UTF-8 解码，
  `[H3D] 批处理进程 N 解包目录` 显示成 `[H3D] ���������� 26048 ���Ŀ¼`。
- **`PYTHONIOENCODING` 在 PyInstaller 环境里不生效**（bootloader 在 Python
  初始化前就按控制台代码页建好流），必须在进程内
  `sys.stdout.reconfigure(encoding="utf-8")`。
- 新增 `launcher._force_utf8_stdio()`，`main()` 与 `--batch` 都调用。

## v1.1.0-r6 (2026-10-17) —— 进度页假死根治：漏导入致解析线程静默死亡 + 深度图计数串造像
### 致命回归（r5 引入，实机：30 张兵马俑照片，18 分钟界面零更新）
- **现象**：日志区只有 3 行（项目 / 配置 / 解包目录），状态行一直停在
  「深度图 30/30」，进度条不动，18 分钟后用户判断为卡死。
- **现场核对**：子进程 `batch_gui.log` 有 115 行完整进度，`patch_match_stereo`
  正从 1/30 稳定推进到 11/30（每张约 46 秒）——**管线一直是好的，坏的是界面**。
- **真因一：`runner.py` 调用了 `substep_of()` 但没有导入**。`BatchRunner.run()`
  在转发第一行日志后抛 `NameError`，`QThread` 静默结束。Qt 不打印
  Python 线程异常，界面表现与卡死完全一致。
  → 补导入；并把整个解析循环包进 `try/except`，异常转为
  `[错误] 日志解析线程异常` + 失败汇总，不再静默。
- **真因二：`_depth_progress()` 遍历 `99_runs/sfm_tmp` 下**所有**造像取最大值**。
  上一轮残留的 `relic_666`（真有 30 张深度图）让界面在稀疏阶段刚起步就显示
  「深度图 30/30」，之后纹丝不动。
  → 只统计当前造像（编号由 `▶ S1 开始：<编号>` 解析，新增 `item_of()`）；
  分母改用 `photos_small` 照片数，与 COLMAP 的 `Processing view n / N` 同源。

### 生成页动态表达（用户诉求：突出加载情况）
- **进度条在 S1 内部推进**：S1 通常占总时长 80%，原先只在 S1 完成时从 0 跳
  到 11%，观感就是「一小时不动」。新增 `s1_fraction()` 把
  特征提取 → 匹配 → 稀疏 → 去畸变 → 深度图 → 融合 映射到 0~11% 连续区间，
  且 **只增不减**。
- **心跳**：连续 30 秒无日志时状态行追加「最后输出 Ns 前（计算中）」。
  COLMAP 单张深度图约 46 秒、sparse BA 十几分钟可能零输出，不提示就与卡死
  无法区分。
- **流水号贯穿**：进度条文字带当前阶段（`37% · S1`），状态行
  `● S1 运行中 · 深度图 11/30：DSC_0018.JPG · 已运行 00:18:42 · 最后输出 24s 前`。
- **日志着色优先级重排**：加载进度（深度图/去畸变/融合/特征提取/匹配块/
  注册图像）提到阶段切换之前，长日志里一眼可见。
- **失败时进度条归零**：原先置 100% 会造成「跑完了但报错」的错觉。
- **中文乱码**：打包版子进程 `sys.stdout` 默认 cp936，GUI 按 UTF-8 读，
  `[H3D] 批处理进程 N 解包目录` 显示成 `����������`。
  → `child_env()` 固定 `PYTHONIOENCODING=utf-8` / `PYTHONUTF8=1`。
- **S1 结束清空深度图计数**：否则 M1~M6 阶段状态行会一直挂着 `深度图 30/30`。

### 测试
- 新增 11 项（`tests/test_gui_progress.py` 共 30 项），其中
  `TestRunnerImportCompleteness` 用 AST 锁住「runner 用到的每个 logparse
  函数都必须在 import 列表中」；`TestRunnerStreamsSignals` 用假 Popen 跑完整
  `run()`，断言造像编号、子步骤、阶段、进度单调不减全部到达界面。
- 全量 **541 项 0 失败 0 错误**（基线 530 + 11）。

## v1.1.0-r5 (2026-10-17) —— 打包版 56 分钟后崩溃根治 + 生成页进度可视化
### 致命缺陷（实机：30 张兵马俑照片，dense 全部完成后在最后一步失败）
- **现象**：S1 稀疏+稠密全部跑完（15:22→16:18，56 分钟），最后报
  `FileNotFoundError: ...\_MEI000065282\open3d`，两个造像全部失败，
  界面无任何产物、报表只有失败行。
- **误判排除**：同一 exe 跑最小点云 M1~M6 全通（EXIT=0），证明 open3d
  本身在包里是完整的（`open3d/cpu/pybind.cp312-win_amd64.pyd` 与
  `open3d/tbb12.dll` 均在 CArchive 内）。
- **真因：PyInstaller onefile 的解包目录共享**。GUI 通过 `_PYI_*` /
  `_MEIPASS2` 环境变量让 `H3D.exe --batch` 子进程复用父进程的
  `%TEMP%\_MEIxxxxxx`，父进程退出（关窗口）时 bootloader 删除该目录，
  而子进程还在跑。崩溃点被推迟到最后，是因为 **S1 全程不 import
  open3d**（照片预处理用 PIL、位姿解析用 numpy、模型筛选用 struct），
  直到 `load_point_cloud(fused.ply)` 才第一次 import。
- **修复**：`runner.child_env()` 剔除 `_MEIPASS2` /
  `_PYI_APPLICATION_HOME_DIR` / `_PYI_ARCHIVE_FILE` /
  `_PYI_PARENT_PROCESS_LEVEL`，子进程独立解包（多 3~8 秒启动，换彻底解耦）。
- **配套**：`closeEvent` 在运行中关窗时先确认再终止子进程，不再留下
  失去解包目录的孤儿进程。`--batch` 启动时打印 `[H3D] 批处理进程 N
  解包目录：...`，下次出问题一眼能看出是否复用。

### 生成页进度可视化（用户反馈"生成页面卡住"）
- **阶段灯此前只在完成时变色**：`▶ S1 开始` 这类开始日志从未输出，
  运行中状态灯一直是"等待"。现由 `batch_processor._log_stage_start()`
  输出，GUI 置橙。
- **状态行**：`● S1 运行中 · 深度图 12/30` + `已运行 00:12:34`，每秒刷新。
- **深度图计数**：每 3 秒扫 `99_runs/sfm_tmp/*/dense/stereo/depth_maps`，
  给出可验证的真实进度（`patch_match_stereo` 唯一能看出"在动"的地方）。
- **COLMAP 输出实时转发**：`run_external(on_line=...)` 新增流式模式，
  `_ProgressLogger` 把 COLMAP 原始行清洗成中文后转发：
  `=== Processing view 12 / 30 for DSC_0019.JPG ===` → `深度图 12/30：DSC_0019.JPG`；
  另有 `去畸变 3/30` / `融合 5/30` / `匹配块 1/1` / `注册图像 #3` /
  `全局光束法平差`。长步骤期间界面不再 50 分钟无变化。
- **日志着色**：错误红、警告橙、阶段切换青、完成绿、加载/进度浅蓝加粗，
  长日志里能一眼看到进度行。
- **报表**：`metrics_summary.csv` 写出失败时带上系统原文（此前只有路径，
  无法区分"目录不存在 / 文件被 Excel 占用 / 权限不足"）。

### 验证
- 新增 `tests/test_gui_progress.py`（19 项）：解包目录剔除、阶段开始解析、
  子步骤/逐视图进度解析、日志着色、流式回调（含回调抛异常不影响主流程、
  超时仍抛 TimeoutExpired）、进度行过滤与去重。
- 全量测试 **530 passed**（基线 511 + 19）。
- 端到端（3 张照片，dense 关）：日志按预期输出
  `▶ S1 开始` → `匹配块 1/1` → `全局光束法平差` → `注册图像 #3` →
  `S1 完成：3/3 张注册，1 相机，18.8s` → `▶ M1 开始`。

## v1.1.0-r4.1 (2026-10-17) —— 全量重建未清 dense 深度图（静默错误）
### 缺陷修复（实机：patch_match_stereo 中断后重跑，产物错但不报错）
- **现象**：30 张照片跑到 dense 第 2 步（patch_match_stereo）被中断，磁盘留下
  30 个 `photometric.bin` + 22 个 `geometric.bin`。此时重新跑全流程，S1 稀疏面
  会清库重算，却没有任何机制处理旧的 `dense/stereo`。
- **根因**：`patch_match_stereo` **跳过已存在的 photometric.bin**，只补齐缺的
  几张。新旧两套位姿（sparse 重建后位姿会变）的深度图混在一起做
  `stereo_fusion`，点云是错的，但全程退出码 0、无异常、无警告——
  **典型的静默降级**，比直接崩溃更难发现。
- **修复 1**：`_reset_sparse_state()` 增加 `dense` 参数，全量重建时一并清空
  `<workspace>/dense`（保留目录本身）。增量注册路径不传 `dense`，不影响
  复用旧产物。
- **修复 2**：`run_dense_stereo()` 进入前先弃用 `<dense>/stereo`。该函数语义是
  「按当前 sparse 重建 dense」，旧深度图在任何调用路径下都无效，不再依赖
  上层是否清过。
- 顺带抽出 `_clear_tree()`，统一「清空目录内容、保留目录」的语义。

### 验证
- 新增 3 项测试（`test_clears_stale_dense_depth_maps` /
  `test_omitting_dense_keeps_it` / `test_drops_old_depth_maps_before_patch_match`），
  `test_sfm_reset.py` 共 11 项；`tests/test_runner` 全套 121 passed。
- **实机**（30 张兵马俑照片，手工制造 db + `sparse/0` + 5 组旧深度图的脏状态）：
  437 s（7.3 min），dense 清空 ✓、`photos_small` 保留 ✓、注册 30/30 ✓、
  `cameras=1` ✓ → **PASS**。

## v1.1.0-r4 (2026-10-17) —— 实机 S1 崩溃（0xC0000409）根治 + 失败可视化
### 缺陷修复（实机：30 张兵马俑照片，纯照片模式 S1 失败，报表全空）
- **根因：复用残留 database.db**。`run_colmap_sparse` 每次直接打开
  `<workspace>/database.db`，用户点重试/断点续跑时用的是上一次的半写入库。
  实测残留库呈 `cameras=3 / images=30`，前 4 张绑 camera 1、其余绑 camera 3；
  再次 feature_extractor 触发 `SQLite error: constraint failed` →
  COLMAP abort（exit=3221226505 / 0xC0000409）。
  同一批照片在**空库**上 30/30 全部成功（全部 Camera #1），证明与照片、
  中文路径、线程参数无关。
- **每次全量重建前清库**：新增 `_reset_sparse_state()`，删除
  `database.db` / `-wal` / `-shm` / `-journal` 与 `sparse/` 下旧模型；
  **保留 `photos_small`**（预处理成果，避免每次重跑重新缩放）。
  增量注册（`run_incremental_sparse`）不受影响。
- **COLMAP 输出完整落盘**：新增 `_dump_step_log()`，每步写
  `99_runs/sfm_tmp/<item>/logs/colmap_stepN.log`（命令 + 退出码 + 完整
  stdout/stderr）。此前只把 stderr 末尾 600 字符塞进 failures.csv。
- **失败信息可诊断**：`_error_tail()` 提取尾部 14 行，并针对
  `constraint failed` / `0xC0000409` / CUDA / 路径缺失给出对应建议，
  同时附日志绝对路径。
- **失败耗时**：`RunManifest.record_fail()` 支持 `elapsed_s`，`_isolate()`
  记录真实耗时（此前恒为 0.00，掩盖"卡很久才失败"）。

### 报表修复（用户看到"report 显示失败但全是空表"）
- 状态表**纳入 S1/P1**。原实现只遍历 M1~M6，S1 失败时表格完全空白。
- 新增"产物 / 异常摘要"列：失败显示原因首行，成功显示产物数量。
- 顶部增加阶段统计（成功/失败/未执行）与整体判定横幅。
- 失败清单：新增"耗时(s)"列；异常信息 `white-space: pre-wrap` 保留换行。
- 新增"诊断建议"块（按失败特征生成，最多 4 条）。
- 章节编号修正（原有两个"五、"）。
- `failures.csv` 增加 `耗时(s)` 列。

### GUI 修复（"输出界面没有任何可视化产物"）
- `_on_finished` 预览回退：`80_export` → `30_dense`（M3 网格）→
  `10_dense`（S1 稠密）→ `99_runs/.../dense/fused.ply`。中间产物也能看。
- 无模型时预览区显示失败阶段与原因（`MeshPreview.show_text`），不再是空白。
- 失败时日志区打印：原因、`report/report.html` 路径、COLMAP 日志目录。
- 「打开 open3d」按钮按实际存在的文件启用（此前失败也会启用，点击报错）。
- matplotlib 配置中文字体，避免提示文字渲染为方块。

### 验证
- 新增 `tests/test_runner/test_sfm_reset.py`（11 项）与
  `tests/test_report/test_report_failure.py`（8 项），锁清库、日志落盘、
  失败信息结构、S1 进表、章节编号、耗时记录。
- 空库实测：30 张 2400px 照片 feature_extractor 1.6 分钟、30/30 Camera #1。

## v1.1.0-r3 (2026-10-17) —— 文件选择对话框缺陷修复
### 缺陷修复（实机：文件选择板块无法选中 JPG/PNG 等照片）
- **根因**：`DropArea._browse` 只用 `accept_dirs` 单一开关分流，而照片区
  `accept_dirs` 默认为 `True` → 双击进入 `getExistingDirectory`。该对话框
  **只列目录、不列文件**，因此任何 JPG/PNG 都不会显示。且文件对话框未设过滤器。
- **浏览模式解耦**：新增 `browse_mode`（`files` / `dirs`），不再由 `accept_dirs`
  推断。照片区固定 `files`，双击即打开**多选文件**对话框；右键菜单与
  「选择文件夹…」按钮保留整目录导入路径。
- **过滤器与格式同源**：新增 `h3d.core.media.image_dialog_filter()`，由
  `IMAGE_EXTS` 生成 `图像文件 (*.jpg *.jpeg *.jpe *.jfif *.png *.tif *.tiff)`，
  新增格式时不会再出现"校验认识、对话框不显示"。
- **单张照片边界**：只选一张照片时 `os.path.commonpath` 返回文件本身，
  导致校验报「照片位置不是文件夹」；现统一上提一级到所在目录。
- **照片区单值语义**：改选照片即替换旧目录，避免 `paths()[0]` 取到上次的目录。
- **项目根推断收敛**：抽出 `infer_root_from_photo()`，向导与 `_finish` 共用；
  普通照片目录取**上级**（产物与素材并列，不污染照片目录），父级为盘符根时
  才退回目录本身。

### 验证
- 新增 `tests/test_gui_picker.py`（16 项），覆盖浏览模式、过滤器、单张/多张收敛、
  改选替换、根目录推断五种布局。

## v1.1.0-r2 (2026-10-17) —— 纯照片模式缺陷修复
### 缺陷修复（实机：场景②选「纯照片三维重建」后照片不被识别）
- **根因**：任务发现 `discover_items` 只扫描 `00_raw` 下的点云/网格文件，
  `00_photo/<item>/*.jpg` 从未参与任务构建 → `tasks=[]`，管线空跑。
  新增 `photo_dir` / `allow_photo_only` 参数，与 `sfm_solve.enabled` 联动：
  无几何但有条目照片时生成"纯照片任务"（`raw_paths` 为空、`photo_paths` 记录图像）。
- **图像口径统一**：新增 `h3d.core.media`（`IMAGE_EXTS` / `is_image_file` /
  `list_images` / `count_images`），覆盖 `.jpg/.jpeg/.jpe/.jfif/.png/.tif/.tiff`。
  消除此前 7 处各自硬编码导致的问题：`.jfif` 被判非照片、`.tif` 导入通过却被 S1 丢弃。
- **向导导入容错**：照片区支持"全选多张 JPG 直接拖入"，自动收敛为公共父目录；
  原实现会把第一张图片当文件夹，报「照片位置不是文件夹」——表现为"JPG 不被识别"。
- **项目根目录推断**：普通照片目录以自身为根，修复可能在 `D:\` 根下创建
  `00_raw/00_photo` 的问题。
- **S1 平铺兜底**：照片直接放在 `00_photo/` 根下（无 `<item>` 子目录）时不再报
  "照片目录为空"。
- **CLI 一致性**：`validate` / `batch --dry-run` 与实跑统一走
  `discover_from_config`，杜绝"预览 0 尊、实跑有任务"的分叉。

### 验证
- 新增 `tests/test_runner/test_photo_only.py`（30 项），全量 473 passed。
- 纯照片端到端：`00_raw` 为空 + 16 张 JPG → S1 注册 16/16 → dense → M1~M6 全通。

## v1.0.0 (2026-08-30) —— 首个正式版
### 核心管线（M1~M6 + S1 + P1 + X1/X2）
- P0 核心底座：统一数据结构、异常体系（ConfigError/RecoverableError/SfmError/QualityGateError）、
  结构化日志、run manifest 溯源
- L1 io 适配层：Open3D 收敛于 io 层，业务层仅经接口访问（适配器模式）
- L2 preprocess：降采样/离群点去除/法线估计
- M1 预处理：双源融合（激光 + dense，novel 在线标定加权）
- M2 配准：FPFH + RANSAC + ICP 组合（鲁棒初始化 + 精化）
- M3 网格：Poisson 重建 + 密度过滤
- M4 纹理：照片投影（S1 位姿 + 控制点两阶段对齐/自动 ICP）+ CG 顶点色（GCN 梯度场）
- M5 质检：几何覆盖/中误差 + 纹理质量门
- M6 交付：多格式导出 + 元数据快照 + 交付清单 + 论文实验表格归档
- S1 位姿解算：COLMAP 封装 + 特征参数自动标定 + 照片预处理（大图稳定）+ 增量注册
- P1 照片纹理 stage（capture_meta 位姿投影）
- X1 多时相形变（M3C2）/ X2 色彩色差（ΔE）
- 创新点 3：深度图完整版（极线约束 + 多尺度金字塔 + 双向一致性 + 多视角融合）

### GUI（PySide6）
- 欢迎页（新建向导 / 历史项目 / 示例演示）
- 三步向导（场景选择 / 数据拖拽导入 / 参数确认）
- 主界面（配置区 + 高级折叠面板 + 3D 预览 + 报告页）
- 一键生成（进度条 + 9 阶段状态灯 + 实时日志 + 暂停）
- 结果浏览（3D 预览 / open3d 交互窗口 / X1 形变热力图 / 纹理 / 交付包导出）

### 发布
- PyInstaller 打包（H3D.exe，双模式 GUI/--batch）
- requirements 锁定（PySide6/cupy-cuda12x/open3d 等）

## v0.x（2026-08 开发迭代）
- v0.1：P0 底座 + L1 io + L2 preprocess
- v0.2：M2 配准 + M1-M6 合成验证
- v0.3：COLMAP 开源路线迁移（R2）+ 双源在线标定
- v0.4：照片纹理（P1/M4 photo）+ 控制点两阶段对齐
- v0.5：X1/X2 评估 + 论文表格
- v0.6：真实文物数据实机闭环（S1 100% 注册）
- v0.7：GUI 核心版 + 增强
- v0.8：打包与收尾
