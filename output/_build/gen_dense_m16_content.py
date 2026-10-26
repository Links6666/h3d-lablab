# -*- coding: utf-8 -*-
"""生成「dense 接入 M1~M6 全链路执行报告」content.json。"""
import json

content = [
    {"type": "h1", "text": "一、执行总览"},
    {"type": "body",
     "text": "本报告记录两大执行任务：<b>① dense 断点续跑确认</b>——COLMAP patch_match_stereo 在驱动 "
             "616.56（CUDA 13.4）下完整输出深度图与稠密点云；<b>② fused.ply 接入 M1~M6 链路</b>——将"
             "稠密重建产物（602,736 点带色点云）作为 M1 输入，完整跑通预处理 → 配准 → 网格 → 纹理 → "
             "质检 → 发布 → 报表，并输出交付包与报表产物。两条链路全部成功，重建结果正确传导至 PDF 输出。"},
    {"type": "callout",
     "text": "<b>结论先行</b>：dense 断点产物完整（40 深度图 + fused.ply 16.3MB）；fused.ply 接入后 "
             "M1~M6 全链路 done=1 failed=0（M1 降采样 602,736→11,208 点 → M3 泊松网格 14,041 顶点 → "
             "M5 质检 pass（覆盖率 99.6%）→ M6 交付 3 格式模型 + 报表 csv/png/html）。"},

    {"type": "h1", "text": "二、dense 重建输出路径与参数配置（任务①）"},
    {"type": "h2", "text": "2.1 输出路径"},
    {"type": "code", "language": "text",
     "text": "output/_colmap_demo/colmap_ws/dense/\n"
             "├─ images/                 # 去畸变图像（20 张注册）\n"
             "├─ sparse/                 # 稀疏模型（位姿，未参与 dense 重建的稀疏相机）\n"
             "└─ stereo/\n"
             "    ├─ depth_maps/         # 40 个深度图（20 photometric + 20 geometric）\n"
             "    ├─ normal_maps/        # 法线图\n"
             "    ├─ consistency_graphs/ # 几何一致性图\n"
             "    ├─ patch-match.cfg     # PMS 邻域配置（每图 20 邻域）\n"
             "    ├─ fusion.cfg          # 融合图像列表\n"
             "    └─ fused.ply           # 稠密点云（16.3MB，602,736 点，带色+法线）"},
    {"type": "h2", "text": "2.2 关键命令与参数"},
    {"type": "code", "language": "bash",
     "text": "# patch_match_stereo（GPU，CUDA 13.4 驱动 616.56）\n"
             "colmap patch_match_stereo --workspace_path colmap_ws/dense \\\n"
             "  --workspace_format COLMAP --PatchMatchStereo.geom_consistency true \\\n"
             "  --PatchMatchStereo.gpu_index 0\n"
             "  # 结果：单图 13.45s，全部 20 张 7.55 分钟\n"
             "\n"
             "# stereo_fusion（稠密点云融合，CPU）\n"
             "colmap stereo_fusion --workspace_path colmap_ws/dense \\\n"
             "  --workspace_format COLMAP --input_type geometric \\\n"
             "  --output_path colmap_ws/dense/fused.ply \\\n"
             "  --StereoFusion.min_num_pixels 2 --StereoFusion.max_num_pixels 20\n"
             "  # 结果：602,736 点（稀疏 367 → 稠密 60.3 万，×1600 稠密化）"},

    {"type": "h1", "text": "三、M1 读取 fused.ply 的接口与后续处理（任务②）"},
    {"type": "h2", "text": "3.1 输入组织与 M1 接口"},
    {"type": "body",
     "text": "fused.ply 作为采集站点文件放入项目 <b>00_raw/rock001/fused.ply</b>（标准带色 PLY）。M1 经 "
             "io 层 ``load_point_cloud`` 读取（支持 PLY/PCD/OBJ，业务层不直接触碰 Open3D——适配器约束），"
             "随后执行预处理三件套：体素降采样（voxel_down_sample=0.08 → 602,736→11,208 点）、统计去噪"
             "（remove_outlier_std=2.0）、法线估计（normal_estimate_k=20）。"},
    {"type": "h2", "text": "3.2 后续阶段处理"},
    {"type": "table",
     "headers": ["阶段", "处理", "结果"],
     "rows": [
         ["M1 预处理", "读 fused.ply → 降采样/去噪/法线", "602,736 → 11,208 点（2.48s）"],
         ["M2 配准", "单站恒等配准（无多站可对齐）", "done（0.0s，fitness N/A 单站）"],
         ["M3 网格", "泊松重建（depth=7, density_ratio=0.02）", "14,041 顶点 / 27,816 面（0.46s）"],
         ["M4 纹理", "顶点色模式（fused.ply 自带 RGB），2 LOD × 3 格式", "ply/obj/glb（1.95s）"],
         ["M5 质检", "覆盖率/中误差评估", "coverage=99.6%, rmse=47.26mm → <b>pass</b>（0.14s）"],
         ["M6 发布", "交付包归档 + 元数据 + 清单", "3 格式模型 + 3 份元数据（0.02s）"],
     ]},
    {"type": "h2", "text": "3.3 配置要点（config.yaml）"},
    {"type": "code", "language": "yaml",
     "text": "project:\n"
             "  name: dense_demo\n"
             "  raw_dir: .../h3d/00_raw      # fused.ply 位于 00_raw/rock001/\n"
             "  formats: [ply]\n"
             "preprocess:\n"
             "  voxel_down_sample: 0.08      # 60 万点 -> 1.1 万点（控制重建规模）\n"
             "  remove_outlier_std: 2.0\n"
             "texture:\n"
             "  paint_mode: vertex           # fused.ply 自带 RGB -> 顶点色\n"
             "  export_formats: [ply, obj, glb]\n"
             "  lod_levels: [1.0, 0.5]\n"
             "mesh:\n"
             "  poisson_depth: 7\n"
             "report:\n"
             "  formats: [csv, png, html]"},

    {"type": "h1", "text": "四、各阶段输出指标汇总"},
    {"type": "table",
     "headers": ["阶段", "状态", "关键指标", "耗时"],
     "rows": [
         ["M1", "done", "输入 602,736 点 → 输出 11,208 点（voxel 0.08）", "2.48s"],
         ["M2", "done", "单站恒等（无多站配准）", "0.00s"],
         ["M3", "done", "14,041 顶点 / 27,816 面，watertight=False", "0.46s"],
         ["M4", "done", "vertex-color，2 LOD × 3 格式 = 6 文件", "1.95s"],
         ["M5", "done", "coverage=99.6% / rmse=47.26mm → pass", "0.14s"],
         ["M6", "done", "3 格式模型 + 元数据快照 + 交付清单", "0.02s"],
         ["报表", "done", "metrics_summary.csv + stage_timings.png + report.html", "—"],
     ]},
    {"type": "h1", "text": "五、交付产物与报表"},
    {"type": "table",
     "headers": ["产物", "路径", "说明"],
     "rows": [
         ["交付模型", "80_export/rock001/{ply, obj, glb}", "rock001.ply 1.08MB / obj 2.21MB / glb 0.90MB"],
         ["质检报告", "80_export/rock001/accuracy.csv", "覆盖率/中误差等指标"],
         ["元数据", "90_meta/rock001_meta.json", "各阶段指标快照"],
         ["交付清单", "90_meta/delivery_manifest.json", "n_items=1, n_published=3"],
         ["运行溯源", "90_meta/run_manifest.json", "M1~M6 全部 done"],
         ["报表", "report/{metrics_summary.csv, stage_timings.png, report.html}", "批次报表产物"],
     ]},
    {"type": "h1", "text": "六、总结"},
    {"type": "body",
     "text": "两条任务全部达成：dense 断点续跑产物完整（40 深度图 + fused.ply 60.3 万点），fused.ply 作为 "
             "M1 输入成功走通 M1~M6 全链路并产出规范交付包（3 格式模型 + 质检报告 + 元数据 + 交付清单）"
             "与报表（csv/png/html）。重建结果（几何 + 颜色）完整传导：fused.ply 带色点云 → M4 顶点色"
             "纹理 → 交付模型；M5 质检 coverage=99.6% 通过。该链路验证了<b>场景②纯照片重建的完整闭环</b>"
             "（照片 → COLMAP dense → h3d M1~M6 → 交付），可与场景①（激光几何 + 照片纹理）互补，构成"
             "路线 R2 的完整能力矩阵。"},
    {"type": "callout",
     "text": "<b>后续建议</b>：① 更高分辨率 dense（照片 800×600 → 原生 4000×3000）将显著提升纹理清晰度；"
             "② fused.ply 可与激光几何融合（几何-色彩双源，coverage 互补）；③ M1 降采样参数按目标顶点数"
             "自适应（当前 0.08 保持网格 1.4 万顶点）；④ 场景②产物可直接作为 X1 多时相/ X2 色差评估输入。"},
]

with open("content.json", "w", encoding="utf-8") as f:
    json.dump(content, f, ensure_ascii=False, indent=2)
print("content.json 生成完成，块数 =", len(content))
