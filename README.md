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
python -m h3d.cli gui             # 启动图形界面
```

## 文档与测试

- 开发文档/PDF 交付物：output/ 目录
- 测试：`python -m pytest tests/`（355 项）
- 变更记录：CHANGELOG.md

## 开源说明

- COLMAP（BSD-3）：位姿解算/稠密重建（外部调用）
- Open3D（MIT）：几何处理
- py4dgeo（MIT）：M3C2 形变分析
- 核心管线与 GUI 为自研（可引用，遵循开源协议）

## 免责声明

文物数字化成果仅用于学术研究与保护记录，请遵守所在国文物保护法规。
