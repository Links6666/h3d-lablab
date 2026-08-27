# -*- coding: utf-8 -*-
"""生成 preprocess（M1 预处理）代码说明书 content.json。"""
import json

content = [
    # ---------------------------------------------------------------------
    {"type": "h1", "text": "一、概述"},
    {"type": "body",
     "text": "本文档是《造像类文物三维建模自动化系统（路线 E）》<b>preprocess 子包（M1 预处理，L2 业务层 / "
             "S1 阶段）</b>的代码说明书。preprocess 负责把 io 层读入的统一数据模型（Open3D PointCloud）"
             "归一为干净、可重建的点云：<b>体素降采样</b>控制点数与内存、<b>统计 / 半径离群去噪</b>移除飞点、"
             "<b>法线估计与一致化</b>为后续 Poisson 重建与 point-to-plane ICP 提供一致法线。本说明书严格遵循"
             "《软件开发项目设计书》第 32 章（M1 详细设计）、《管线骨架构建分析》第 4~5 章（模块划分与依赖"
             "约束）及《开发指南（教学版）》第 15 章的接口约定。"},
    {"type": "callout",
     "text": "设计铁律：preprocess 是纯几何处理层——输入输出均为 PointCloud 对象，<b>不直接读写文件</b>"
             "（由 io 层负责），也不直接 import open3d 做文件 I/O。这保证了「业务层只做算法、适配层只做格式」"
             "的职责分离，与《软著方案》2.3.1 的适配器分层一致。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "二、模块划分"},
    {"type": "body", "text": "preprocess 子包由 3 个业务模块与 1 个组合流水线构成，目录结构与职责如下："},
    {"type": "code", "language": "text",
     "text": "src/h3d/preprocess/\n"
             "+-- __init__.py          # 包导出 + run(pcd, cfg) 组合预处理流水线\n"
             "+-- filter.py           # 去噪：统计 / 半径离群点去除\n"
             "+-- downsample.py       # 降采样：体素降采样\n"
             "+-- normal_estimate.py  # 法线估计 + 法线一致化"},
    {"type": "table",
     "headers": ["模块文件", "核心职责", "对外关键符号"],
     "rows": [
         ["filter.py", "去噪滤波（统计 / 半径离群点去除）", "statistical_outlier_removal, radius_outlier_removal"],
         ["downsample.py", "体素降采样", "voxel_down_sample"],
         ["normal_estimate.py", "法线估计与一致化", "estimate_normals, orient_normals_consistent"],
         ["__init__.py", "包导出 + 组合流水线（配置驱动）", "run(pcd, cfg)"],
     ]},
    {"type": "body",
     "text": "三个业务模块各司其职：<b>filter</b> 只管「去噪」、<b>downsample</b> 只管「降采样」、"
             "<b>normal_estimate</b> 只管「法线」。三者通过 <b>run()</b> 以固定顺序（降采样 → 去噪 → 法线）"
             "串接成预处理流水线，参数全部取自 <b>PreprocessConfig</b>（配置驱动，见《设计书》7.4）。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "三、依赖关系与数据流"},
    {"type": "h2", "text": "3.1 依赖方向"},
    {"type": "body", "text": "依据《管线骨架构建分析》5.2，preprocess 属 L2 业务层，仅依赖 core 底座，"
             "输入数据经 io 层（L1）读入，本层不直接依赖 io 与任何第三方库的文件 I/O："},
    {"type": "code", "language": "text",
     "text": "package-level deps (A -> B means A imports B):\n"
             "  preprocess  -->  core.exceptions (InputDataError)\n"
             "  preprocess  -->  core.config    (PreprocessConfig)\n"
             "  preprocess  -->  core.logging   (get_logger)\n"
             "  preprocess  -->  Open3D         (仅几何运算，惰性导入)\n"
             "  preprocess  -->  (数据经 io 层读入，不直接读写文件)\n"
             "  core / io  -->  (NONE; 不反向依赖 preprocess)"},
    {"type": "callout",
     "text": "Open3D 在 preprocess 中仅用于<b>几何运算</b>（voxel_down_sample / remove_statistical_outlier / "
             "estimate_normals），且经 _o3d() 惰性导入；文件格式适配完全交给 io 层。这一边界使业务层可独立于"
             "具体点云读写实现进行单元测试。"},
    {"type": "h2", "text": "3.2 预处理流水线数据流"},
    {"type": "body", "text": "run(pcd, cfg) 组合预处理流水线的完整流转（自顶向下，任一步结果为空均抛 "
             "InputDataError E100）："},
    {"type": "flowchart",
     "nodes": [
         {"id": "in", "label": "原始 PointCloud（io 层读入）", "shape": "parallelogram"},
         {"id": "voxel", "label": "体素降采样 voxel_down_sample", "shape": "rect"},
         {"id": "denoise", "label": "统计离群去噪（std_ratio）", "shape": "rect"},
         {"id": "normal", "label": "法线估计 estimate_normals", "shape": "rect"},
         {"id": "orient", "label": "法线一致化 orient_normals", "shape": "rect"},
         {"id": "out", "label": "干净点云（降采样+去噪+一致法线）", "shape": "parallelogram"},
     ],
     "edges": [
         {"from": "in", "to": "voxel"},
         {"from": "voxel", "to": "denoise"},
         {"from": "denoise", "to": "normal"},
         {"from": "normal", "to": "orient"},
         {"from": "orient", "to": "out"},
     ],
     "caption": "run() 预处理流水线：降采样 → 去噪 → 法线估计 → 法线一致化（参数来自 PreprocessConfig）"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "四、模块详细设计"},
    {"type": "h2", "text": "4.1 filter.py — 去噪滤波"},
    {"type": "body",
     "text": "提供两种离群点去除：<b>统计离群去除</b>按 K 近邻距离均值与标准差剔除离群点（适合扫描噪声 / 飞点），"
             "<b>半径离群去除</b>按球体内近邻数剔除孤立点（适合明显悬浮飞点）。去噪后点云为空则抛 E100。"},
    {"type": "table",
     "headers": ["函数", "签名", "职责"],
     "rows": [
         ["statistical_outlier_removal", "(pcd, nb_neighbors=20, std_ratio=2.0) -> PointCloud", "统计离群去噪（Open3D remove_statistical_outlier）"],
         ["radius_outlier_removal", "(pcd, nb_points=16, radius=0.01) -> PointCloud", "半径离群去噪（Open3D remove_radius_outlier）"],
     ]},
    {"type": "code", "language": "python",
     "text": "def statistical_outlier_removal(pcd, nb_neighbors=20, std_ratio=2.0):\n"
             "    if pcd is None or not pcd.has_points():\n"
             "        raise InputDataError(\"输入点云为空（0 点），无法去噪\")\n"
             "    filtered, _ = pcd.remove_statistical_outlier(\n"
             "        nb_neighbors=int(nb_neighbors), std_ratio=float(std_ratio))\n"
             "    if not filtered.has_points():\n"
             "        raise InputDataError(\"统计离群去噪后点云为空（0 点）\")\n"
             "    return filtered"},
    {"type": "h2", "text": "4.2 downsample.py — 体素降采样"},
    {"type": "body",
     "text": "按体素栅格划分点云，每个体素用质心代表，在保留整体形状的前提下显著减少点数。体素边长取自配置项 "
             "<b>preprocess.voxel_down_sample</b>（默认 0.005 米）；体素非法或结果为空抛 E100，点数未下降时给出告警。"},
    {"type": "code", "language": "python",
     "text": "def voxel_down_sample(pcd, voxel_size: float):\n"
             "    if voxel_size is None or float(voxel_size) <= 0:\n"
             "        raise InputDataError(f\"体素边长非法: {voxel_size!r}（须 > 0）\")\n"
             "    down = pcd.voxel_down_sample(float(voxel_size))\n"
             "    if down is None or not down.has_points():\n"
             "        raise InputDataError(f\"体素降采样后点云为空（voxel={voxel_size}）\")\n"
             "    return down"},
    {"type": "h2", "text": "4.3 normal_estimate.py — 法线估计"},
    {"type": "body",
     "text": "法线是 Poisson 重建与 point-to-plane ICP 的必需属性。本模块用 Hybrid 搜索估计法线（radius 未提供时"
             "退化为 KNN），再用切平面传播统一法线朝向，避免重建「翻面」。调用顺序约束：先 estimate_normals 再 "
             "orient_normals_consistent，违反则抛 E100。"},
    {"type": "table",
     "headers": ["函数", "签名", "职责"],
     "rows": [
         ["estimate_normals", "(pcd, max_nn=30, radius=None) -> PointCloud", "法线估计（原地修改）"],
         ["orient_normals_consistent", "(pcd, k=15) -> PointCloud", "法线一致化（原地修改，需先估法线）"],
     ]},
    {"type": "h2", "text": "4.4 __init__.py — run 组合流水线"},
    {"type": "body",
     "text": "run(pcd, cfg) 是 preprocess 的对外入口，按「降采样 → 去噪 → 法线」固定顺序串接三模块，参数全部取自"
             " <b>PreprocessConfig</b>。法线搜索半径取体素边长的 3 倍（经验值），一致化近邻取 15。"},
    {"type": "code", "language": "python",
     "text": "def run(pcd, cfg: PreprocessConfig):\n"
             "    voxel = cfg.voxel_down_sample\n"
             "    pcd = voxel_down_sample(pcd, voxel)\n"
             "    pcd = statistical_outlier_removal(\n"
             "        pcd, nb_neighbors=20, std_ratio=cfg.remove_outlier_std)\n"
             "    pcd = estimate_normals(\n"
             "        pcd, max_nn=cfg.normal_estimate_k, radius=voxel * 3.0)\n"
             "    pcd = orient_normals_consistent(pcd, k=15)\n"
             "    return pcd"},
    {"type": "table",
     "headers": ["配置项", "默认值", "用途"],
     "rows": [
         ["preprocess.voxel_down_sample", "0.005", "体素降采样边长（米）"],
         ["preprocess.remove_outlier_std", "2.0", "统计去噪标准差倍数"],
         ["preprocess.normal_estimate_k", "30", "法线估计近邻点上限"],
     ]},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "五、接口契约与错误码"},
    {"type": "body",
     "text": "preprocess 对外暴露纯函数式接口（PointCloud 进、PointCloud 出），只抛一类异常："
             "<b>InputDataError(E100)</b>。这与设计书 13.3 的 E100「输入缺失 / 损坏」对齐，供 M0 编排器以 "
             "<b>except RecoverableError</b> 做失败隔离。"},
    {"type": "table",
     "headers": ["错误码", "异常类", "触发场景", "处理策略"],
     "rows": [
         ["E100", "InputDataError", "输入点云为空 / 去噪后为空 / 降采样后为空 / 法线未估先定向 / 体素非法", "标记跳过，记入 manifest（可恢复）"],
     ]},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "六、测试与验证"},
    {"type": "body",
     "text": "preprocess 配套单元测试位于 <b>tests/test_preprocess/</b>，用合成点云（含人为飞点）验证去噪、降采样、"
             "法线生成与组合流水线，共 16 项，全部通过。"},
    {"type": "table",
     "headers": ["测试文件", "覆盖点"],
     "rows": [
         ["test_filter.py", "统计 / 半径离群去噪移除飞点、空输入 → E100"],
         ["test_downsample.py", "降采样点数下降、包围盒形状保留、非法体素 → E100"],
         ["test_normal_estimate.py", "法线生成、radius/KNN 两种搜索、未估法线先定向 → E100"],
         ["test_pipeline.py", "run 组合流水线、配置驱动、io 读取→预处理→写出端到端"],
     ]},
    {"type": "code", "language": "bash",
     "text": "$ python -m pytest tests/test_preprocess -q\n"
             "................            [100%]\n"
             "16 passed in 3.84s\n"
             "\n"
             "$ python -m pytest -q   # 全量（core + io + preprocess）\n"
             ".................................................................   [100%]\n"
             "65 passed in 3.48s"},
    {"type": "callout",
     "text": "运行方式：在 .venv_lab 虚拟环境执行 pytest；或 python -c \"from h3d.io import load_point_cloud; "
             "from h3d.preprocess import run; from h3d.core import load\" 冒烟验证。核心依赖全部 MIT/BSD。"},
]

with open("content.json", "w", encoding="utf-8") as f:
    json.dump(content, f, ensure_ascii=False, indent=2)
print("content.json 生成完成，块数 =", len(content))
