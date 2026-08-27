# -*- coding: utf-8 -*-
"""生成 mesh（M3 稠密重建）代码说明书 content.json。

含模块划分、依赖与数据流、详细设计、接口契约、测试验证、开发进度更新与下一步方向。
"""
import json

content = [
    # ---------------------------------------------------------------------
    {"type": "h1", "text": "一、概述"},
    {"type": "body",
     "text": "本文档是《造像类文物三维建模自动化系统（路线 E）》<b>mesh 子包（M3 稠密重建，L2 "
             "业务层 / S4 阶段）</b>的代码说明书。mesh 负责把 M2 配准后的多站稠密点云重建为"
             "水密表面网格（TriangleMesh），支持 <b>泊松（Poisson）/ 球旋转（Ball-Pivoting）/ "
             "Alpha-shape</b> 三种算法，并附带简化、补洞、Hausdorff 度量等后处理。该子包接入"
             " M0 编排内核的 <b>STAGE_REGISTRY（@register_stage(\"M3\")）</b> 后，管线"
             "<b>「点云 -> 模型」核心闭环正式打通</b>：00_raw → M1 预处理 → M2 配准 → M3 重建 → "
             "30_dense 网格产物。"},
    {"type": "callout",
     "text": "设计要点：<b>「点云 -> 模型」闭环</b>——M1 提供干净带一致法线的点云，M2 把多站对齐到"
             "统一坐标系（参考站 s01 也已落盘，20_registered 为完整配准结果），M3 合并全部站点后"
             "按 cfg.mesh.method 重建水密网格；法线缺失自动兜底估计（设计书 §34.4）。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "二、模块划分"},
    {"type": "body",
     "text": "mesh 子包由 3 个重建算法模块、1 个后处理模块与 1 个组合流水线构成（骨架分析 §4.2 建议"
             "新增子包，此处按《软著方案》2.4 采用 mesh/ 命名）："},
    {"type": "code", "language": "text",
     "text": "src/h3d/mesh/\n"
             "+-- __init__.py        # 包导出 + run(pcd, cfg) 组合重建流水线\n"
             "+-- poisson.py        # 泊松重建（密度裁剪剔离群面，TC-303）+ 清理链\n"
             "+-- ball_pivoting.py  # 球旋转重建（多半径尝试）\n"
             "+-- alpha_shape.py    # Alpha-shape 重建（外包络轮廓）\n"
             "+-- postprocess.py    # QEM 简化 / trimesh 补洞 / Hausdorff / 指标汇总"},
    {"type": "table",
     "headers": ["模块文件", "核心职责", "对外关键符号"],
     "rows": [
         ["poisson.py", "泊松表面重建（法线前提 + 密度裁剪 + 清理链）", "poisson_reconstruct, clean_mesh"],
         ["ball_pivoting.py", "球旋转重建（均匀点云优先）", "ball_pivoting_reconstruct"],
         ["alpha_shape.py", "Alpha-shape 重建（外包络）", "alpha_shape_reconstruct"],
         ["postprocess.py", "简化 / 补洞 / Hausdorff / 指标", "simplify_mesh, fill_holes, hausdorff_distance, mesh_metrics"],
         ["__init__.py", "组合流水线 run() + 法线兜底 ensure_normals", "run, ensure_normals"],
     ]},
    {"type": "body",
     "text": "三个算法模块通过 <b>run()</b> 按 ``cfg.mesh.method`` 分发；poisson 的清理链 "
             "``clean_mesh`` 被 ball / alpha 复用，保证全管线网格口径一致。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "三、依赖关系与数据流"},
    {"type": "h2", "text": "3.1 依赖方向"},
    {"type": "body",
     "text": "mesh 属 L2 业务层，依赖 core 底座与 io（网格读写），不反向；postprocess 的补洞"
             "惰性依赖 trimesh（MIT，白名单），未安装时降级警告并跳过（《软著方案》2.5）："},
    {"type": "code", "language": "text",
     "text": "package-level deps (A -> B means A imports B):\n"
             "  mesh        -->  core.exceptions (InputDataError)\n"
             "  mesh        -->  core.config     (MeshConfig)\n"
             "  mesh        -->  core.logging    (get_logger)\n"
             "  mesh        -->  Open3D          (重建几何，惰性导入)\n"
             "  mesh        -->  trimesh         (fill_holes 可选，MIT，惰性导入)\n"
             "  runner M3   -->  h3d.mesh + h3d.io  (阶段接入，惰性导入)\n"
             "  core / io / preprocess / register  -->  (NONE; 不反向依赖 mesh)"},
    {"type": "h2", "text": "3.2 重建流水线数据流"},
    {"type": "body",
     "text": "M3 阶段 + run() 组合流水线的完整流转（自顶向下）："},
    {"type": "flowchart",
     "nodes": [
         {"id": "in", "label": "20_registered 多站配准点云（io 读入）", "shape": "parallelogram"},
         {"id": "merge", "label": "M3 阶段合并全部站点为稠密点云", "shape": "rect"},
         {"id": "norm", "label": "ensure_normals 法线兜底（估计 + 一致化）", "shape": "rect"},
         {"id": "method", "label": "按 cfg.mesh.method 分发", "shape": "diamond"},
         {"id": "poisson", "label": "泊松重建 + 密度裁剪（TC-303）", "shape": "rect"},
         {"id": "ball", "label": "球旋转重建（多半径）", "shape": "rect"},
         {"id": "alpha", "label": "Alpha-shape 重建", "shape": "rect"},
         {"id": "clean", "label": "清理链：退化面/重复顶点/重复面/法线", "shape": "rect"},
         {"id": "post", "label": "可选 QEM 简化 + trimesh 补洞", "shape": "rect"},
         {"id": "out", "label": "save_mesh -> 30_dense/<item>.ply", "shape": "parallelogram"},
         {"id": "rec", "label": "manifest 记录 n_vertices/n_triangles", "shape": "rect"},
     ],
     "edges": [
         {"from": "in", "to": "merge"},
         {"from": "merge", "to": "norm"},
         {"from": "norm", "to": "method"},
         {"from": "method", "to": "poisson", "label": "poisson"},
         {"from": "method", "to": "ball", "label": "ball_pivoting"},
         {"from": "method", "to": "alpha", "label": "alpha_shape"},
         {"from": "poisson", "to": "clean"},
         {"from": "ball", "to": "clean"},
         {"from": "alpha", "to": "clean"},
         {"from": "clean", "to": "post"},
         {"from": "post", "to": "out"},
         {"from": "out", "to": "rec"},
     ],
     "caption": "M3 重建数据流：合并多站 → 法线兜底 → 按 method 重建 → 清理/简化/补洞 → 30_dense 落盘 → manifest 指标"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "四、模块详细设计"},
    {"type": "h2", "text": "4.1 poisson.py — 泊松重建"},
    {"type": "body",
     "text": "泊松重建（教学版 17.1）通过隐式指示函数拟合点云表面，是造像重建的首选：对细节丰富"
             "的表面效果好、能自然闭合孔洞。输入必须带一致法线（本子包自动兜底）。<b>密度裁剪</b>"
             "依据泊松输出的逐顶点 densities（采样密度场），剔除低于分位数的低置信区域——对应"
             "设计书 TC-303「离群漂浮面被剔除」。depth 控制细节：越大越细但越慢越易出噪，"
             "千万级点云取 8-10。"},
    {"type": "code", "language": "python",
     "text": "def poisson_reconstruct(pcd, depth=8, density_ratio=0.05):\n"
             "    mesh, densities = TriangleMesh.create_from_point_cloud_poisson(\n"
             "        pcd, depth=depth)\n"
             "    if density_ratio > 0 and len(densities):      # TC-303 离群面剔除\n"
             "        dens = np.asarray(densities)\n"
             "        thr = np.quantile(dens, density_ratio)\n"
             "        mesh.remove_vertices_by_mask(dens < thr)\n"
             "    clean_mesh(mesh)   # 退化面/重复顶点/重复面/法线\n"
             "    return mesh"},
    {"type": "h2", "text": "4.2 ball_pivoting.py — 球旋转重建"},
    {"type": "body",
     "text": "Ball-Pivoting（教学版 17.2）用一个虚拟球在点云表面滚动，球过三点成面。适合法线良好、"
             "分布均匀的点云，速度通常快于泊松。多半径策略：小半径先补细节，大半径再连接大区域"
             "（教学版示例 radii=[2.0, 4.0, 6.0]）。"},
    {"type": "h2", "text": "4.3 alpha_shape.py — Alpha-shape 重建"},
    {"type": "body",
     "text": "Alpha-shape（《软著方案》2.4 提及）以 alpha 为球半径刻画外包络：过大丢失细节、"
             "过小出现孔洞。无需一致法线（几何法），适合轮廓清晰的造像，与泊松互为兜底。"},
    {"type": "h2", "text": "4.4 postprocess.py — 后处理与度量"},
    {"type": "body",
     "text": "教学版第 18 章：QEM 二次误差简化（在保形与省存储间取舍）、Hausdorff 距离（重建网格 "
             "vs 参考网格的几何误差指标）、精度报告字段。补洞用 trimesh（MIT，避开 GPL 的 "
             "PyMeshLab）——未安装时降级警告并原样返回（《软著方案》2.6「单项缺失 -> 注明」）。"},
    {"type": "table",
     "headers": ["函数", "职责"],
     "rows": [
         ["simplify_mesh(mesh, target)", "QEM 简化到目标面数（>= 当前面数时原样返回）"],
         ["fill_holes(mesh, max_hole_size)", "trimesh 补洞（可选依赖；兼容 4.x/5.x 签名）"],
         ["hausdorff_distance(a, b, samples)", "两向采样 + 最近邻距离取最大（几何误差指标）"],
         ["mesh_metrics(mesh, **extra)", "顶点数 / 三角面数 / 水密性（供 manifest 与报表）"],
     ]},
    {"type": "h2", "text": "4.5 __init__.py — run 组合流水线"},
    {"type": "body",
     "text": "run(pcd, cfg) 是 mesh 的对外入口：法线兜底 -> 按 method 分发 -> 清理链 -> 可选简化"
             "-> 可选补洞。空输入抛 InputDataError(E100)；非法 method 在配置层（choices 校验）"
             "即被拦截。"},
    {"type": "code", "language": "python",
     "text": "def run(pcd, cfg: MeshConfig):\n"
             "    if pcd is None or not pcd.has_points():\n"
             "        raise InputDataError('网格重建输入点云为空（0 点）')\n"
             "    ensure_normals(pcd)                    # 设计书 §34.4 法线兜底\n"
             "    if cfg.method == 'poisson':\n"
             "        mesh = poisson_reconstruct(pcd, cfg.poisson_depth, cfg.density_ratio)\n"
             "    elif cfg.method == 'ball_pivoting':\n"
             "        mesh = ball_pivoting_reconstruct(pcd, cfg.ball_radii)\n"
             "    elif cfg.method == 'alpha_shape':\n"
             "        mesh = alpha_shape_reconstruct(pcd, cfg.alpha)\n"
             "    if cfg.simplify_target and len(mesh.triangles) > cfg.simplify_target:\n"
             "        mesh = simplify_mesh(mesh, cfg.simplify_target)\n"
             "    if cfg.fill_holes:\n"
             "        mesh = fill_holes(mesh)\n"
             "    return mesh"},
    {"type": "table",
     "headers": ["配置项", "默认值", "用途"],
     "rows": [
         ["mesh.method", "poisson", "重建方法（poisson / ball_pivoting / alpha_shape）"],
         ["mesh.poisson_depth", "8", "泊松八叉树深度（细节与性能平衡，8-10 稳妥）"],
         ["mesh.density_ratio", "0.05", "密度裁剪分位数（TC-303 离群面剔除）"],
         ["mesh.ball_radii", "[2.0, 4.0, 6.0]", "球旋转多半径列表"],
         ["mesh.alpha", "0.05", "Alpha-shape 球半径"],
         ["mesh.simplify_target", "0", "目标三角面数（0 = 不简化）"],
         ["mesh.fill_holes", "false", "是否补洞（需 trimesh，MIT）"],
     ]},
    {"type": "h2", "text": "4.6 runner M3 阶段接入（STAGE_REGISTRY）"},
    {"type": "body",
     "text": "M3 通过装饰器无缝接入编排内核（无需改动 BatchProcessor 一行代码）："},
    {"type": "code", "language": "python",
     "text": "@register_stage('M3')\n"
             "def _stage_mesh(task, cfg):\n"
             "    sources = task.outputs_of('M2') or task.outputs_of('M1') or task.raw_paths\n"
             "    combined = None\n"
             "    for p in sources:                     # 合并 20_registered 多站\n"
             "        pc = load_point_cloud(p)\n"
             "        combined = pc if combined is None else combined + pc\n"
             "    mesh = mesh_run(combined, cfg.mesh)   # h3d.mesh.run\n"
             "    save_mesh(mesh, f'{task.root}/30_dense/{task.item_id}/{task.item_id}.ply')\n"
             "    return StageResult('M3', TaskStatus.DONE, [out],\n"
             "                       mesh_metrics(mesh, n_input_points=len(combined.points)))"},
    {"type": "callout",
     "text": "<b>闭环达成</b>：STAGE_REGISTRY 现为 M1 → M2 → M3，`h3d batch` 一次跑完「点云 -> 模型」"
             "全流程。配套修复：M2 参考站（s01）此前未落盘，现以恒等变换写入 20_registered，"
             "保证 M3 输入是完整的多站配准结果。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "五、接口契约与错误码"},
    {"type": "body",
     "text": "mesh 对外暴露纯函数式接口（PointCloud 进、TriangleMesh 出），错误码与设计书 13.3 "
             "对齐；非法 method 由配置层 choices 校验拦截（E001）："},
    {"type": "table",
     "headers": ["错误码", "异常类", "触发场景", "处理策略"],
     "rows": [
         ["E001", "ConfigError", "method 非法 / 参数越界（配置层拦截）", "启动即报错退出"],
         ["E100", "InputDataError", "输入点云为空（0 点）", "标记跳过，记入 manifest（可恢复）"],
     ]},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "六、测试与验证"},
    {"type": "body",
     "text": "mesh 配套 21 项单元测试 + runner 端到端 2 项新增（M3 闭环），全量 <b>152 项</b> 通过："
             "core 29 + io 20 + preprocess 16 + register 11 + mesh 21 + report 21 + runner 28 + "
             "cli 6。端到端验证「点云 -> 模型」：2928 点合并重建出 12232 顶点 / 24342 面网格。"},
    {"type": "table",
     "headers": ["测试文件", "覆盖点"],
     "rows": [
         ["test_reconstruct.py", "泊松（密度裁剪 / depth 细节）、球旋转、Alpha-shape 三算法重建"],
         ["test_postprocess.py", "QEM 简化、Hausdorff（同网格≈0、简化小误差）、补洞、指标"],
         ["test_pipeline.py", "run() 分发、法线兜底、空输入 E100、非法 method E001"],
         ["test_runner/test_pipeline.py", "M1→M2→M3 端到端闭环、单站回退、manifest M3 指标"],
     ]},
    {"type": "code", "language": "bash",
     "text": "$ python -m pytest -q\n"
             "........................................................................ [ 47%]\n"
             "........................................................................ [ 94%]\n"
             "........                                                             [100%]\n"
             "152 passed in 17.60s\n"
             "\n"
             "# CLI 冒烟（2 尊 × 2 站）：M1 -> M2 -> M3 全流程 + 报表\n"
             "$ python -m h3d.cli batch -c demo.yaml\n"
             "M3 完成：rock001（2928 点 -> 12232 顶点 / 24342 面）\n"
             "批量处理完成：total=2 done=2 failed=0 skipped=0"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "七、开发进度与下一步方向（更新）"},
    {"type": "h2", "text": "7.1 主线完成度（M3 已交付）"},
    {"type": "table",
     "headers": ["层级 / 子包", "状态", "测试覆盖"],
     "rows": [
         ["L0 core 底座", "已交付", "29"],
         ["L1 io 适配层", "已交付", "20"],
         ["L2 preprocess（M1 接入）", "已交付", "16"],
         ["L2 register（M2 配准）", "已交付", "11"],
         ["<b>L2 mesh（M3 网格重建）</b>", "<b>本次交付</b>", "<b>21</b>"],
         ["L2 report（M6 报表）", "已交付", "21"],
         ["L3 runner（M0 编排）", "已交付", "28"],
         ["L4 cli（命令入口）", "已交付", "6"],
         ["L2 texture（M4 网格纹理）", "未开始", "—"],
         ["L2 quality（M5 质检质量门）", "未开始", "—"],
         ["L2 change（X1 M3C2）/ colour（X2）", "未开始", "—"],
     ]},
    {"type": "body",
     "text": "<b>「点云 -> 模型」核心闭环已达成</b>：M1 预处理 → M2 配准 → M3 重建 → 30_dense "
             "网格产物，端到端可复现。主线模块（M0-M3 + M6）完成度约 <b>80%</b>；可选模块 "
             "（X1/X2）0%；测试 152 项 100% 通过；许可证全部 MIT/BSD（新增 trimesh MIT 白名单）。"},
    {"type": "h2", "text": "7.2 下一步开发方向（M3 完成后更新）"},
    {"type": "table",
     "headers": ["优先级", "模块", "目标", "关键点"],
     "rows": [
         ["P0", "M5 质检（quality）", "网格中误差 / 覆盖率 / 孔洞率，质量门 QualityGateError(E500)",
          "对 30_dense 网格与配准指标做质检，失败进 failures.csv（可恢复）"],
         ["P1", "M4 纹理（texture）", "UV 展开 + 纹理映射 + 多分辨率 LOD 输出 OBJ/GLB/PLY",
          "LOD_levels / export_formats 配置驱动；@register_stage('M4')"],
         ["P1", "run_manifest 增强", ".done 标记文件幂等性（软著 §2.7.3）",
          "manifest 已是续跑真相源，.done 作冗余校验"],
         ["P2", "并发调度", "cfg.runner.workers 进程池并行独立 item",
          "现串行保证可复现；需处理 open3d 子进程与 manifest 写冲突"],
         ["P2", "HTML 报表", "report.formats 增加 html",
          "jinja2 + matplotlib SVG 嵌入"],
         ["P3", "X1 change（M3C2）/ X2 colour（CIEDE2000）", "多时相形变 / 色彩量化",
          "py4dgeo / colour-science 已在 venv 可装"],
     ]},
    {"type": "callout",
     "text": "<b>建议路径</b>：先 M5 质检（把「几何质量」变成可报告数字）→ M4 纹理（带色模型）"
             "→ 再视场景启用 X1/X2 与并发调度。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "附录 A：30_dense 网格指标示例"},
    {"type": "code", "language": "json",
     "text": "run_manifest.json 中 M3 阶段条目：\n"
             "{\n"
             "  \"rock001/M3\": {\n"
             "    \"status\": \"done\",\n"
             "    \"outputs\": [\".../30_dense/rock001/rock001.ply\"],\n"
             "    \"elapsed_s\": 0.349,\n"
             "    \"n_input_points\": 2928,\n"
             "    \"n_vertices\": 12232,\n"
             "    \"n_triangles\": 24342,\n"
             "    \"watertight\": false\n"
             "  }\n"
             "}"},
]

with open("content.json", "w", encoding="utf-8") as f:
    json.dump(content, f, ensure_ascii=False, indent=2)
print("content.json 生成完成，块数 =", len(content))
