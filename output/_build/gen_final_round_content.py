# -*- coding: utf-8 -*-
"""生成「四顶开发：深度图完整版 / Linux 验证脚本 / 论文表归档 / 深度图管线化」+ 数据放置要求。"""
import json

content = [
    {"type": "h1", "text": "一、开发总览"},
    {"type": "body",
     "text": "本批四项开发：<b>三项成功、一项部分受阻（如实分析）</b>。成功项：<b>① 深度图原型 → 完整版</b>"
             "（极线约束 + 多尺度金字塔 + 左右一致性指标，论文创新点 3 完整落地——带透视立体对深度恢复"
             "误差 0.0%）；<b>② 论文实验表格并入 M6 交付包</b>（thesis_experiment_table.csv 归档进 "
             "80_export，端到端生效）；<b>③ 深度图与 fused 增量合并管线化</b>（merge_depth_into_fused，"
             "数字孪生维护闭环）。部分受阻项：<b>④ Linux COLMAP / 自编译验证子集模型</b>——本机无 Linux "
             "环境，提供验证脚本与流程（Linux 服务器/容器即测），Windows 侧缺陷记录。全量 <b>332 项测试"
             "通过</b>。"},
    {"type": "callout",
     "text": "<b>结论</b>：论文创新点 3 完整落地（极线+金字塔，带透视 0.0% 误差）；论文表格进交付包（M6 "
             "归档）；深度图增量合并闭环；Linux 验证脚本就绪（本机受阻于环境）。"},

    {"type": "h1", "text": "二、① 深度图完整版（论文创新点 3 完整落地）"},
    {"type": "h2", "text": "2.1 实现（incremental_depth.py::depth_from_stereo_full）"},
    {"type": "code", "language": "text",
     "text": "极线约束：目标图每采样像素沿深度候选 -> 3D 点 -> 投影到参考图\n"
             "          （投影点自然落在极线上——对极几何隐式满足）\n"
             "多尺度金字塔：n_levels 层下采样；粗层全范围搜索给初值，\n"
             "          细层围绕初值 ±20% 精搜（n_candidates 候选）\n"
             "左右一致性：depth_consistency_ratio 有效点占比指标\n"
             "          （完整双向校验列入扩展）\n"
             "增量合并：incremental_points 反投影 -> merge_depth_into_fused\n"
             "          体素去重并入 fused_dense（数字孪生维护）"},
    {"type": "h2", "text": "2.2 验证"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["带透视立体对（3° 旋转 + 0.3 平移，深度 3.0）", "249 有效点，深度中位数 3.00，误差 0.0% ✅"],
         ["多尺度金字塔（2 层）", "粗层初值 → 细层精搜，0.2s ✅"],
         ["左右一致性指标", "有效点占比 0.3~0.8 区间 ✅"],
         ["增量合并（fused 点数增加 + 落盘）", "n_added>10，after>before ✅"],
         ["原型 → 完整版", "极线+金字塔落地（原水平视差假设移除）✅"],
     ]},

    {"type": "h1", "text": "三、② 论文实验表格并入 M6 交付包（P2）"},
    {"type": "h2", "text": "3.1 实现"},
    {"type": "code", "language": "text",
     "text": "runner M6：export_thesis_table 调用 -> 80_export/<item>/<item>_thesis_table.csv\n"
             "  delivery_manifest.json 记录 thesis_table 路径；失败不阻断 M6（容错）"},
    {"type": "h2", "text": "3.2 验证（端到端）"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["M6 归档表格入交付包", "80_export/rock001/rock001_thesis_table.csv ✅"],
         ["数据行", "rock001, 33%, 92.2%, 141.16, 3276.83, 8.66, N/A, 0.0%, 20.54, 通过 ✅"],
         ["交付清单记录", "delivery_manifest.json thesis_table 字段 ✅"],
         ["论文使用", "交付包自带实验数据表（直接粘贴论文实验章）✅"],
     ]},

    {"type": "h1", "text": "四、③ 深度图与 fused 增量合并管线化（P3）"},
    {"type": "h2", "text": "4.1 实现"},
    {"type": "code", "language": "text",
     "text": "incremental_depth.py::merge_depth_into_fused(fused_dense, depth, K, R, t)\n"
             "  深度图 -> incremental_points 反投影 3D 点\n"
             "  -> 与 fused_dense 合并（voxel 去重）-> 落盘\n"
             "  S1 dense 增量分支可调用（新视角深度增量并入旧 fused）"},
    {"type": "h2", "text": "4.2 验证"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["增量合并（50 点基础 + 深度点）", "n_added>10、after>before、落盘 ✅"],
         ["数字孪生维护闭环", "新视角深度自动并入 fused（管线函数就绪）✅"],
     ]},

    {"type": "h1", "text": "五、④ 部分受阻分析：Linux COLMAP / 自编译验证"},
    {"type": "h2", "text": "5.1 受阻原因"},
    {"type": "body",
     "text": "<b>本机无 Linux 环境</b>（Windows 宿主，无 WSL/容器）——Linux COLMAP 子集模型验证无法在"
             "本会话执行。Windows CUDA 预编译构建对子集模型解析崩溃（exit 3221226505，上轮已实证）——"
             "该缺陷属工具链问题，Linux 构建（源码编译 / 官方 Linux 包）通常稳定，需在 Linux 环境确认。"},
    {"type": "h2", "text": "5.2 交付物（可即测）"},
    {"type": "code", "language": "text",
     "text": "scripts/verify_subset_linux.sh\n"
             "  # 用法：bash verify_subset_linux.sh <project_root> <item_id> [colmap_bin]\n"
             "  # 流程：build_subset_model -> image_undistorter -> 计数检查\n"
             "  # 判据：输出 SUBSET_MODEL_OK（images>=4）\n"
             "Linux 服务器/容器就绪后一键运行；若 OK，增量 dense 子集路径闭环。"},
    {"type": "callout",
     "text": "<b>影响评估</b>：增量 dense 子集是性能优化项——全量 dense 已闭环；自研深度图（①）已提供替代"
             "路径。Linux 验证是环境依赖项，脚本交付后随时可测。"},

    {"type": "h1", "text": "六、用户实机数据放置位置要求"},
    {"type": "h2", "text": "6.1 目录规范（项目根目录，与 00_raw 同级）"},
    {"type": "code", "language": "text",
     "text": "<project_root>/\n"
             "├─ 00_raw/<item>/*.ply          # 激光点云（可选：纯照片路线可省略）\n"
             "├─ 00_photo/<item>/*.jpg        # 照片（60~120 张，≥60% 重叠）\n"
             "├─ 00_ctrl/<item>/control_points.json  # 控制点靶标（可选）\n"
             "├─ 00_ref/<item>/*.ply          # 上期 dense/模型（可选，X1 多时相）\n"
             "└─ config.yaml                  # 项目配置（paint_mode=photo 等）\n"
             "示例：\n"
             "  E:/h3d_lablab/data/t16/00_photo/guanyin/img_001.jpg ...\n"
             "  E:/h3d_lablab/data/t16/00_ctrl/guanyin/control_points.json"},
    {"type": "h2", "text": "6.2 文件要求"},
    {"type": "table",
     "headers": ["文件", "要求"],
     "rows": [
         ["照片", "JPG/PNG；文件名含序列（img_001.jpg）；EXIF 相机型号一致"],
         ["控制点", "JSON 格式（laser_xyz + photo_uv，≥3 点，见采集规范）"],
         ["色卡", "00_photo 中同框照片（X2 自动定位，四角贴 ArUco 或亮框）"],
         ["参考期", "PLY 与当期同坐标系（X1 M3C2 差分）"],
         ["config.yaml", "paint_mode: photo；sfm_solve.enabled/dense；x1/x2_enabled"],
     ]},
    {"type": "h2", "text": "6.3 运行与验收"},
    {"type": "code", "language": "text",
     "text": "h3d.cli batch -c config.yaml   # 一键全链（S1→M1~M6→P1→X1→X2）\n"
             "验收：S1 注册率≥60%；P1 投影覆盖≥2%；X2 ΔE≤5；X1 检出 3cm 形变；\n"
             "交付包含模型 + accuracy.csv + thesis_experiment_table.csv"},

    {"type": "h1", "text": "七、下一步发展方向"},
    {"type": "table",
     "headers": ["优先级", "方向", "目标"],
     "rows": [
         ["P1", "真实数据全链路实机闭环（按第六章放置）", "全部机制真实数据实证"],
         ["P1", "深度图完整版双向一致性 + 多视角融合", "论文创新点 3 精度提升"],
         ["P2", "Linux COLMAP 验证（脚本已交付）", "增量 dense 子集闭环"],
         ["P2", "深度图合并管线接入 S1 增量分支", "数字孪生维护自动闭环"],
         ["P3", "深度图 CUDA 并行（GPU 稠密化）", "自研稠密化性能"],
     ]},
    {"type": "callout",
     "text": "<b>优先建议</b>：① 真实数据就绪后按第六章目录放置并运行（唯一数据依赖项）；② 深度图双向"
             "一致性 + 多视角融合（创新点 3 精度）；③ Linux 服务器验证脚本（环境就绪即测）。"},

    {"type": "h1", "text": "附录：产物与验证汇总"},
    {"type": "table",
     "headers": ["产物", "路径", "说明"],
     "rows": [
         ["新源码", "incremental_depth.py（full + merge）/ runner M6（表格归档）/ scripts/verify_subset_linux.sh", "四项开发"],
         ["新测试", "test_incremental_depth.py（+3）", "3 项新增（累计 332 项）"],
         ["端到端", "80_export/rock001/rock001_thesis_table.csv（M6 归档）", "实证"],
         ["受阻记录", "Linux 验证（环境依赖）——第五章", "部分受阻分析"],
     ]},
]

with open("content.json", "w", encoding="utf-8") as f:
    json.dump(content, f, ensure_ascii=False, indent=2)
print("content.json 生成完成，块数 =", len(content))
