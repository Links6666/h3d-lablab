# h3d‑lab｜造像类文物三维建模自动化系统（路线E）
> 骨架版本，仅占位stub，业务逻辑待填充。
参考文档：造像类文物三维建模自动化系统_管线骨架构建分析_2026.pdf

## 模块
- src/h3d/core        核心底座：配置 / 日志 / 溯源元数据 / 异常
- src/h3d/io          点云网格IO读写
- src/h3d/preprocess  点云预处理
- src/h3d/register    FPFH‑RANSAC+ICP异源配准
- src/h3d/change      X1 M3C2多时相形变检测(可选)
- src/h3d/colour      X2 CIEDE2000色彩量化(可选)
- src/h3d/runner      批量调度 / 断点续跑 / 失败隔离
- src/h3d/report      CSV、图表报表输出
- src/h3d/cli.py      命令行入口

## 开发快速启动
1. 创建conda环境：
conda env create -f environment.yml
conda activate h3d‑env

2. 以可编辑模式安装本包：
pip install -e ".[dev]"

3. 执行pytest收集测试用例
pytest

## 合规提示
- 核心依赖全部MIT/BSD许可证；CloudCompare‑CLI仅以外部子进程调用。
- data/ output/ 已加入gitignore，文物原始数据禁止提交git。
- 软件著作权登记版本需要人工重写全部业务实现，不可直接提交stub占位代码。