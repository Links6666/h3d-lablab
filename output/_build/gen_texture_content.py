# -*- coding: utf-8 -*-
"""生成 texture（M4 网格化与纹理）代码说明书 content.json。

含模块划分、纹理化数据流、LOD/格式配置、错误码、测试、完成度更新与下一步方向。
"""
import json

content = [
    # ---------------------------------------------------------------------
    {"type": "h1", "text": "一、概述"},
    {"type": "body",
     "text": "本文档是《造像类文物三维建模自动化系统（路线 E）》<b>texture 子包（M4 网格化与纹理，"
             "L2 业务层 / S4 阶段）</b>的代码说明书。texture 负责<b>带色模型交付</b>：把 M3 网格产物"
             "与带色参考点云（M2 配准结果）合成带纹理模型——顶点色纹理映射 + 多分辨率 LOD + "
             "PLY/OBJ/GLB 多格式导出到 50_tex。该子包接入 M0 编排内核（@register_stage(\"M4\")）后，"
             "管线<b>「点云 -> 模型 -> 带色模型 -> 质检」完整链路正式打通</b>。"},
    {"type": "callout",
     "text": "设计要点：<b>顶点色纹理</b>是带色模型交付的核心工程路径——网格每个顶点取参考点云最近邻点"
             "颜色（scipy.cKDTree 批量查询，毫秒级），Open3D 原生读写顶点色且 PLY/OBJ/GLB 全格式支持"
             "（OBJ 写 v x y z r g b 扩展行）。设计书 §35 的 UV 图集（xatlas）路径因 Open3D/trimesh "
             "无内建 unwrap 列为增强预留，未实装时降级顶点色并警告（软著方案 2.6「单项缺失 -> 注明」）。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "二、模块划分"},
    {"type": "body",
     "text": "texture 子包由顶点色映射、LOD 生成与多格式导出三部分组成："},
    {"type": "code", "language": "text",
     "text": "src/h3d/texture/\n"
             "+-- __init__.py     # 包导出 + run(mesh, ref, cfg, out_dir, item) 纹理化流水线\n"
             "+-- paint.py       # 顶点色映射（scipy cKDTree 最近邻取色）\n"
             "+-- lod.py         # 多分辨率 LOD（QEM 简化，保留顶点色）\n"
             "+-- export.py      # 带色模型多格式导出（ply / obj / glb）"},
    {"type": "table",
     "headers": ["模块文件", "核心职责", "对外关键符号"],
     "rows": [
         ["paint.py", "顶点色纹理映射（参考点云颜色 -> 网格顶点）", "has_colors, paint_vertex_colors"],
         ["lod.py", "多分辨率 LOD（level=1.0 原样 / <1.0 按比例简化）", "generate_lods"],
         ["export.py", "带色模型导出（PLY/OBJ/GLB，LOD 标签命名）", "export_colored"],
         ["__init__.py", "组合纹理化流水线（映射 -> LOD -> 导出）", "run"],
     ]},
    {"type": "body",
     "text": "paint 提供颜色、lod 提供分辨率、export 提供格式——三者通过 <b>run()</b> 串接成"
             "「带色模型交付」流水线，配置取自 <b>TextureConfig</b>（软著方案 2.4 texture 段）。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "三、依赖关系与数据流"},
    {"type": "h2", "text": "3.1 依赖方向"},
    {"type": "body",
     "text": "texture 属 L2 业务层，依赖 core 底座、io（网格/点云读写）与 scipy（cKDTree 批量最近邻），"
             "不反向；Open3D 惰性导入："},
    {"type": "code", "language": "text",
     "text": "package-level deps (A -> B means A imports B):\n"
             "  texture     -->  core.exceptions (InputDataError, ExportError)\n"
             "  texture     -->  core.config     (TextureConfig)\n"
             "  texture     -->  core.logging    (get_logger)\n"
             "  texture     -->  Open3D          (顶点色读写 / 导出，惰性导入)\n"
             "  texture     -->  scipy.spatial.cKDTree  (批量最近邻取色)\n"
             "  runner M4   -->  h3d.texture + h3d.io  (阶段接入，惰性导入)\n"
             "  core / io / preprocess / register / mesh / quality  -->  (NONE; 不反向)"},
    {"type": "h2", "text": "3.2 纹理化数据流"},
    {"type": "body",
     "text": "M4 阶段 + texture.run 的完整流转（自顶向下）："},
    {"type": "flowchart",
     "nodes": [
         {"id": "in", "label": "M3 网格（30_dense）+ 带色参考点云（M2 产物）", "shape": "parallelogram"},
         {"id": "paint", "label": "顶点色映射（scipy cKDTree 最近邻取色）", "shape": "rect"},
         {"id": "deg", "label": "点云无颜色 -> 降级几何模型并标注", "shape": "diamond"},
         {"id": "lod", "label": "generate_lods：按 lod_levels 生成多分辨率（保留顶点色）", "shape": "rect"},
         {"id": "exp", "label": "export_colored：每档 LOD 导出 ply / obj / glb", "shape": "rect"},
         {"id": "out", "label": "50_tex/<item>/<item>_L{level}.{fmt} 带色模型", "shape": "parallelogram"},
         {"id": "rec", "label": "manifest 记录 colored / n_outputs / paint_mode", "shape": "rect"},
     ],
     "edges": [
         {"from": "in", "to": "paint"},
         {"from": "paint", "to": "deg", "label": "无颜色"},
         {"from": "paint", "to": "lod", "label": "有颜色"},
         {"from": "deg", "to": "lod"},
         {"from": "lod", "to": "exp"},
         {"from": "exp", "to": "out"},
         {"from": "out", "to": "rec"},
     ],
     "caption": "M4 纹理化数据流：顶点色映射（无颜色降级）→ LOD 多分辨率 → 多格式导出 → 50_tex 带色模型 → manifest 指标"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "四、模块详细设计"},
    {"type": "h2", "text": "4.1 paint.py — 顶点色纹理映射"},
    {"type": "body",
     "text": "带色模型交付的核心：网格每个顶点查询参考点云中最近邻点的 RGB 颜色（scipy.cKDTree "
             "批量查询，1 万顶点毫秒级）。点云无颜色时返回 False，由调用方降级输出几何模型并标注"
             "（软著方案 2.4「无纹理 -> 输出几何模型」）。"},
    {"type": "code", "language": "python",
     "text": "def paint_vertex_colors(mesh, pcd) -> bool:\n"
             "    if not has_colors(pcd) or len(mesh.vertices) == 0:\n"
             "        return False\n"
             "    tree = cKDTree(np.asarray(pcd.points))\n"
             "    _, idx = tree.query(np.asarray(mesh.vertices), k=1)\n"
             "    mesh.vertex_colors = Vector3dVector(colors[idx])  # 0~1 RGB\n"
             "    return True"},
    {"type": "h2", "text": "4.2 lod.py — 多分辨率 LOD"},
    {"type": "body",
     "text": "按软著方案 2.4 的 lod_levels（默认 [1.0, 0.3, 0.1]）生成多分辨率：level=1.0 返回原始"
             "网格（存档用最高精度），level<1.0 用 QEM 二次误差简化（教学版 18.1，保留顶点色）。"
             "max_vertices 为工程上限：简化后仍超限时继续压到上限（避免超大网格导出失败）。"},
    {"type": "h2", "text": "4.3 export.py — 带色模型多格式导出"},
    {"type": "body",
     "text": "PLY / OBJ / GLB 三格式均支持顶点色：OBJ 写 ``v x y z r g b`` 扩展行，GLB 内嵌顶点色"
             "缓冲，PLY 写 vertex red/green/blue。命名约定 ``<item>_<LOD>.<fmt>``（如 "
             "rock001_L1.ply）便于工具链按通配符批量消费。不支持的格式自动跳过并警告。"},
    {"type": "h2", "text": "4.4 __init__.py — run 纹理化流水线"},
    {"type": "body",
     "text": "run(mesh, ref_pcd, cfg, out_dir, item_id) 串接三模块：顶点色映射 -> LOD 多分辨率 -> "
             "每档多格式导出。paint_mode='uv'（UV 图集增强）未实装时降级顶点色并警告。空网格抛 "
             "InputDataError(E100)；导出失败抛 ExportError(E601)。"},
    {"type": "table",
     "headers": ["配置项", "默认值", "用途"],
     "rows": [
         ["texture.paint_mode", "vertex", "纹理方式：vertex 顶点色 / uv 图集（增强预留）"],
         ["texture.export_formats", "[ply, obj, glb]", "带色模型导出格式"],
         ["texture.lod_levels", "[1.0, 0.3, 0.1]", "多分辨率 LOD 比例（1.0 原始精度）"],
         ["texture.max_vertices", "0", "LOD 简化顶点数上限（0 = 仅按比例简化）"],
     ]},
    {"type": "h2", "text": "4.5 runner M4 阶段接入（STAGE_REGISTRY）"},
    {"type": "body",
     "text": "M4 通过装饰器插入 M3 与 M5 之间（注册顺序 = 执行顺序），无需改动编排内核："},
    {"type": "code", "language": "python",
     "text": "@register_stage('M4')\n"
             "def _stage_texture(task, cfg):\n"
             "    mesh = load_mesh(f'{task.root}/30_dense/{task.item_id}/{task.item_id}.ply')\n"
             "    ref = 合并(task.outputs_of('M2') or task.outputs_of('M1'))\n"
             "    outputs, metrics = texture_run(mesh, ref, cfg.texture,\n"
             "                                    f'{task.root}/50_tex/{task.item_id}',\n"
             "                                    task.item_id)\n"
             "    return StageResult('M4', TaskStatus.DONE, outputs, metrics)"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "五、接口契约与错误码"},
    {"type": "table",
     "headers": ["错误码", "异常类", "触发场景", "处理策略"],
     "rows": [
         ["E001", "ConfigError", "paint_mode 非法 / 参数越界（配置层拦截）", "启动即报错"],
         ["E100", "InputDataError", "输入网格为空 / 参考点云为空", "失败隔离，记录后跳过"],
         ["E601", "ExportError", "带色模型导出失败（格式不支持 / 写盘失败）", "失败隔离，记录后跳过"],
     ]},
    {"type": "body",
     "text": "无颜色点云不抛异常：降级输出几何模型并标注（colored=False，软著方案 2.4「无纹理 -> "
             "输出几何模型」）。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "六、测试与验证"},
    {"type": "body",
     "text": "texture 配套 20 项单元测试 + 端到端 1 项新增（M4 带色交付），全量 <b>197 项</b> 通过："
             "29 core + 20 io + 16 preprocess + 11 register + 21 mesh + 20 texture + 22 quality + "
             "21 report + 31 runner + 6 cli。端到端（2 尊 × 2 站带色扫描）：50_tex 6 文件/尊"
             "（2 LOD × 3 格式）全部带色，PLY 读回 has_colors=True。"},
    {"type": "table",
     "headers": ["测试文件", "覆盖点"],
     "rows": [
         ["test_paint.py", "顶点色映射成功 / 参考无颜色返回 False / 空网格"],
         ["test_lod.py", "L1.0 原样 / 低档简化保留顶点色 / max_vertices 上限 / 去重"],
         ["test_export.py", "三格式导出、PLY 往返带色（8-bit 量化容差）、OBJ 顶点行 7 列、不支持格式跳过"],
         ["test_pipeline.py", "run() 带色全流程 / 无颜色降级 / uv 模式降级 / 空网格 E100"],
         ["test_runner/test_pipeline.py", "M1→M2→M3→M4→M5 全链路、50_tex 带色产物、LOD 简化、manifest M4 指标"],
     ]},
    {"type": "code", "language": "bash",
     "text": "$ python -m pytest -q\n"
             "........................................................................ [ 73%]\n"
             ".....................................................                [100%]\n"
             "197 passed in 31.86s\n"
             "\n"
             "$ python -m h3d.cli batch -c demo.yaml\n"
             "M4 完成：rock001（vertex-color，2 LOD x 3 格式）\n"
             "M5 完成：rock001（coverage=69.4%, rmse=219.31mm -> pass）\n"
             "批量处理完成：total=2 done=2 failed=0 skipped=0"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "七、开发进度与下一步方向（更新）"},
    {"type": "h2", "text": "7.1 主线完成度（M4 已交付）"},
    {"type": "table",
     "headers": ["层级 / 子包", "状态", "测试覆盖"],
     "rows": [
         ["L0 core 底座", "已交付", "29"],
         ["L1 io 适配层", "已交付", "20"],
         ["L2 preprocess（M1 接入）", "已交付", "16"],
         ["L2 register（M2 配准）", "已交付", "11"],
         ["L2 mesh（M3 网格重建）", "已交付", "21"],
         ["<b>L2 texture（M4 网格化与纹理）</b>", "<b>本次交付</b>", "<b>20</b>"],
         ["L2 quality（M5 质检质量门）", "已交付", "22"],
         ["L2 report（M6 报表）", "已交付", "21"],
         ["L3 runner（M0 编排）", "已交付", "31"],
         ["L4 cli（命令入口）", "已交付", "6"],
         ["L2 change（X1 M3C2）/ colour（X2）", "未开始", "—"],
     ]},
    {"type": "body",
     "text": "<b>「点云 -> 模型 -> 带色模型 -> 质检」完整链路已打通</b>：M1 预处理 → M2 配准 → "
             "M3 重建 → M4 纹理（带色模型交付）→ M5 质检。主线模块（M0-M6 中除 M6 发布外）"
             "完成度约 <b>95%</b>；可选模块（X1/X2）0%；测试 197 项 100% 通过；许可证全部 "
             "MIT/BSD（新增 scipy BSD-3 依赖白名单）。"},
    {"type": "h2", "text": "7.2 下一步开发方向（M4 完成后更新）"},
    {"type": "table",
     "headers": ["优先级", "模块", "目标", "关键点"],
     "rows": [
         ["P1", "M6 发布（publish）", "交付包整理：成品模型 + 质检报告 + manifest 归档",
          "@register_stage('M6') 接入；设计书 §37 发布规范"],
         ["P1", "UV 图集增强", "texture.paint_mode='uv' 实装：xatlas 展开 + 纹理图烘焙",
          "python-xatlas（MIT）依赖；M5 overlap 指标随之启用"],
         ["P2", "并发调度", "cfg.runner.workers 进程池并行独立 item",
          "现串行保证可复现；需处理 open3d 子进程与 manifest 写冲突"],
         ["P2", "HTML 报表", "report.formats 增加 html",
          "jinja2 + matplotlib SVG 嵌入"],
         ["P3", "X1 change（M3C2）/ X2 colour（CIEDE2000）", "多时相形变 / 色彩量化",
          "py4dgeo / colour-science 已在 venv 可装"],
     ]},
    {"type": "callout",
     "text": "<b>建议路径</b>：主线 M1~M5 全部就绪，下一步首选 <b>M6 发布（交付包归档）</b>收尾主线闭环，"
             "或按需启用 X2 colour（CIEDE2000 色彩保真度，承接 M4 的带色模型）做彩塑造像专项。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "附录 A：50_tex 带色模型产物示例"},
    {"type": "code", "language": "text",
     "text": "50_tex/rock001/\n"
             "+-- rock001_L1.ply       # 原始精度带色模型（13272 顶点，has_colors=True）\n"
             "+-- rock001_L1.obj       # OBJ（v x y z r g b 扩展顶点行）\n"
             "+-- rock001_L1.glb       # GLB（内嵌顶点色缓冲，Web 轻量化）\n"
             "+-- rock001_L0.3.ply     # 30% 面数 LOD（QEM 简化，保留顶点色）\n"
             "+-- rock001_L0.3.obj\n"
             "+-- rock001_L0.3.glb\n"
             "\n"
             "run_manifest.json 中 M4 阶段条目：\n"
             "  {status: done, colored: true, paint_mode: vertex,\n"
             "   formats: [ply, obj, glb], lod_levels: [1.0, 0.3],\n"
             "   n_lods: 2, n_outputs: 6}"},
]

with open("content.json", "w", encoding="utf-8") as f:
    json.dump(content, f, ensure_ascii=False, indent=2)
print("content.json 生成完成，块数 =", len(content))
