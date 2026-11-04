# H3D 中小型文物三维建模自动化系统

面向中小型器物类文物（铜镜、印章、小件陶瓷与玉器等，典型 5~30 cm）的三维
数字化自动化系统：激光点云几何 + 多视角照片纹理，全程零命令行（GUI 向导式）。
距离类参数按包围盒对角线自动缩放，同一套配置可覆盖厘米级小件与米级大中型文物。
硕士论文配套开源系统。

## 安装说明

### 环境要求
- Windows 10/11 64 位
- Python 3.12+
- NVIDIA GPU + CUDA 12 运行时（稠密重建/GPU 加速；无 GPU 可用 CPU 模式）
- COLMAP（CUDA 版，稠密重建/位姿解算）：放置于任意目录，在配置中指定路径

### 安装步骤
```bash
# 1. 创建虚拟环境（Python 3.12+）
python -m venv venv
venv\Scripts\activate

# 2. 安装依赖（锁定版本见 requirements.txt）
pip install -r requirements.txt

# 3. 安装本项目
pip install -e .

# 4. 启动 GUI
python -m h3d.cli gui
```

### 打包版（免安装）
- 解压后运行 `H3D.exe` 即可（含 GUI 与全部管线）
- `H3D.exe --batch <config.yaml>`：命令行批量处理模式

## 数据放置规范

```
<项目根>/
├─ 00_raw/<文物编号>/*.ply      # 激光点云（场景①必填）
├─ 00_photo/<文物编号>/*.jpg    # 环绕照片 60~120 张（≥60% 重叠）
├─ 00_ctrl/<文物编号>/control_points.json  # 控制点（选填，≥3 点）
├─ 00_ref/<文物编号>/*.ply      # 参考期模型（选填，X1 多时相）
└─ config.yaml                  # 项目配置（GUI 向导自动生成）
```

照片要求：JPG/PNG，文件名含序列（img_001.jpg），同机位相机型号一致；
控制点 JSON 格式：`laser_xyz` + 每点 ≥2 视图 `photo_uv`。

### `00_raw` 里放的不是激光件怎么办

`00_raw` 有文件 **不等于** 有激光基准。若那是别的软件照片重建出来的模型
（Metashape / RealityCapture 导出，坐标是任意单位），请在「确认本次生成约束」
里把 **数据来源** 选成「照片重建件」，或直接写进配置：

```yaml
runner:
  data_source: photogrammetry   # auto（默认） / laser / photogrammetry
```

选了它，中误差会退出 5 mm 毫米门、报表按「模型单位」表述——**这不是数据缺失，
是口径声明**。不选的话系统只能按「有文件即有基准」推断，会印出一个本就不成立
的毫米数。

反过来，声明「激光扫描件」也不是免死金牌：若 M1 判出 `00_raw` 与本系统 SfM
稠密模型近乎重合，系统仍不主张米制并在报表里警告（**证据压过声明**）。

## 快速开始

1. 启动软件 → 欢迎页
2. 「新建项目」→ 三步向导：选场景 → 拖拽数据（激光/照片/控制点）→ 确认参数
3. 主界面 → 一键生成交付物 → 实时进度监控
4. 结果页 → 3D 预览 / 质检报告 / 一键导出交付包
5. 或「运行示例项目」体验完整流程

## 命令行（高级）

```bash
python -m h3d.cli init            # 初始化项目目录
python -m h3d.cli validate -c config.yaml   # 校验配置与数据
python -m h3d.cli batch -c config.yaml      # 批量全链处理
python -m h3d.cli report -c config.yaml     # 仅重生成报表
python -m h3d.cli verify <项目目录>          # 核验交付包是否自洽
python -m h3d.cli gui             # 启动图形界面
```

### 交付核验（`verify`）

跑完之后想知道「交付包到底有没有坏」，用这一条：

```bash
h3d verify D:/my_project          # 直接给项目目录
h3d verify -c config.yaml         # 或从配置的 raw_dir 推出项目根
```

它把三处对一遍：**manifest 记的产物 vs 磁盘 vs 报表**，22 项检查，退出码
0 = 通过、1 = 有 FAIL。查的是历史真踩过的坑：

| 检查 | 对应事故 |
|---|---|
| manifest 记的每个产物都在磁盘上 | 中文路径下 GLB 静默失败（返回 True 却不写文件） |
| 交付件数 = M6 完成件数 | 多件批少交付一件，报表齐全看不出 |
| 每件请求的格式都已交付 | `publish.formats` 要了 glb、M4 没产，日志全绿 |
| 图集有 obj/glb 承载 | 图集孤立：只剩顶点色，PNG 没人引 |
| 每份 accuracy.csv 逐份检查 | 多件批只看第一份，第二件表坏了不报 |

WARN 不影响退出码（例如上次运行的残留件目录）；FAIL 才返回 1。

图形界面里也有同一个按钮（配置面板「核验交付包」）：弹窗给出要动手的那几条，
完整清单写在界面日志里（可滚动、可复制）。

### 并行与断点续跑（**二者互斥**）

`runners.workers > 1` 走进程池按文物并行，适合**首次全量**跑一大批。
但**并行模式下不做断点续跑判定**：worker 之间不共享 manifest，中断后再跑会把
所有阶段重算一遍。日志里会明确提示这一点。

实际用法：

| 场景 | 建议 |
|---|---|
| 首次跑 20 件 | `workers=4`（快），跑完为止 |
| 中途中断要接着跑 | 改回 `workers=1` 再跑（会跳过已完成阶段） |
| 加了新件要补 | `workers=1` 增量补，已完成的件与阶段自动跳过 |
| 只想重出报表 | `h3d report`，不触发任何阶段 |

并行跑完产出的 manifest 仍带完整输入指纹，所以**之后切回 `workers=1` 能正常
接上**（实测：3 件并行全量跑完，切单进程续跑时 M1–M4 全部跳过）。

## 文档与测试

- 开发文档/PDF 交付物：output/ 目录
- 测试：`python -m pytest tests/`（1318 项，约 2.5 分钟）
- 变更记录：CHANGELOG.md

## 开源说明

- COLMAP（BSD-3）：位姿解算/稠密重建（外部调用）
- Open3D（MIT）：几何处理
- py4dgeo（MIT）：M3C2 形变分析
- 核心管线与 GUI 为自研（可引用，遵循开源协议）

## 免责声明

文物数字化成果仅用于学术研究与保护记录，请遵守所在国文物保护法规。
