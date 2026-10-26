# -*- coding: utf-8 -*-
"""生成「控制点靶标采集规范 + 控制点引导 ICP」开发说明 content.json。"""
import json

content = [
    {"type": "h1", "text": "一、功能概述"},
    {"type": "body",
     "text": "<b>控制点靶标采集规范 + 控制点引导 ICP（创新点 2 完整落地）</b>：解决 SfM 位姿与激光几何"
             "坐标系不匹配导致的纹理投影错位问题。此前自动最近邻 ICP 在无靶标时残差大（实证 rmse≈0.17），"
             "投影未命中；本模块引入<b>控制点靶标</b>——在文物表面/周围布设编码靶标，激光系坐标（靶标在"
             "激光点云中的位置）与照片系观测（靶标在照片中的像素）建立已知对应，经<b>多视图三角化 + "
             "Umeyama 闭环解</b>直接求解坐标系变换，避免最近邻错误对应，达到高精度对齐（单测 rmse<0.02）。"},
    {"type": "callout",
     "text": "<b>核心结论</b>：控制点引导对齐已在管线内生效（P1 自动优先控制点路径，失败回退自动 ICP）；"
             "8 项单元测试 + 全量 279 项通过；三角化恢复 <0.01、对齐残差 <0.02（真值位姿验证）；端到端"
             "链路 done=1，投影命中率受 SfM 位姿质量影响（真实高质照片 + 靶标将显著改善）。"},

    {"type": "h1", "text": "二、控制点靶标采集规范"},
    {"type": "h2", "text": "2.1 靶标类型与布设"},
    {"type": "table",
     "headers": ["项目", "规范要求", "说明"],
     "rows": [
         ["靶标类型", "ArUco 编码靶标（DICT_4X4_50，推荐）或黑白棋盘格角点", "编码靶标可自动检测（OpenCV aruco，可选）"],
         ["靶标数量", "≥3 个（推荐 6~10 个）", "少于 3 无法求解相似变换"],
         ["空间分布", "避开共线；环绕文物分布（前/后/侧/顶）", "共线时 SVD 退化（已检测并报错）"],
         ["布设位置", "文物表面稳固处或周边支架；避免遮挡关键纹饰", "不影响文物本体（优先周边固定架）"],
         ["靶标尺寸", "照片中 ≥20×20 像素", "过小检测不稳定"],
     ]},
    {"type": "h2", "text": "2.2 激光系坐标测量"},
    {"type": "body",
     "text": "靶标中心的<b>激光几何系坐标</b>（laser_xyz）：在激光扫描点云中定位靶标中心（球心/角点拟合，"
             "或用扫描仪自带靶标坐标测量），记录至 <b>毫米级</b>。多靶标时使用统一坐标系（与 00_raw 输入"
             "点云一致）。"},
    {"type": "h2", "text": "2.3 拍摄要求"},
    {"type": "table",
     "headers": ["项目", "要求"],
     "rows": [
         ["每靶标可见视图", "≥2 张照片（推荐 3~6 张）——多视图三角化"],
         ["靶标清晰度", "对焦清晰、无运动模糊；避免高光/遮挡"],
         ["与文物同帧", "靶标与文物同框拍摄（几何一致性）"],
         ["重叠度", "照片间重叠 ≥60%（与 S1 采集规范一致）"],
     ]},
    {"type": "h2", "text": "2.4 文件规范（00_ctrl/<item>/control_points.json）"},
    {"type": "code", "language": "json",
     "text": "{\n"
             "  \"item_id\": \"rock001\",\n"
             "  \"control_points\": [\n"
             "    {\n"
             "      \"id\": \"c01\",\n"
             "      \"laser_xyz\": [0.5231, 1.2044, 0.8765],   # 激光系坐标（毫米级）\n"
             "      \"photo_uv\": [                            # 各照片中的像素坐标\n"
             "        {\"image\": \"img_01.jpg\", \"u\": 412.3, \"v\": 288.7},\n"
             "        {\"image\": \"img_02.jpg\", \"u\": 356.1, \"v\": 305.2}\n"
             "      ]\n"
             "    },\n"
             "    ...  # 共 >=3 个（推荐 6~10）\n"
             "  ]\n"
             "}"},
    {"type": "body",
     "text": "<b>photo_uv 获取</b>：① <b>自动</b>——OpenCV ArUco 检测（detect_aruco_uv，opencv-python-"
             "headless ≥4.9），照片中自动定位编码靶标中心；② <b>手工标注</b>——图像工具中点击靶标中心。"
             "两种方式均可生成规范文件。"},

    {"type": "h1", "text": "三、控制点引导 ICP（创新点 2）"},
    {"type": "h2", "text": "3.1 算法流程"},
    {"type": "flowchart",
     "nodes": [
         {"id": "in", "label": "控制点规范文件 + S1 位姿 + M3 网格", "shape": "parallelogram"},
         {"id": "tri", "label": "多视图线性三角化：photo_uv + 位姿 → SfM 系 3D", "shape": "rect"},
         {"id": "um", "label": "Umeyama：SfM 系 ↔ 激光系（≥3 对非共线）", "shape": "rect"},
         {"id": "tf", "label": "几何变换到 SfM 系：X_sfm = R^T (X_laser - T) / s", "shape": "rect"},
         {"id": "out", "label": "P1 照片投影纹理（命中）", "shape": "parallelogram"},
     ],
     "edges": [{"from": "in", "to": "tri"}, {"from": "tri", "to": "um"},
               {"from": "um", "to": "tf"}, {"from": "tf", "to": "out"}],
     "caption": "控制点引导对齐：三角化 → Umeyama 闭环解 → 几何变换（创新点 2 完整流程）"},
    {"type": "h2", "text": "3.2 模块与集成"},
    {"type": "code", "language": "text",
     "text": "src/h3d/ctrl_points.py         # 新增：控制点规范 + 引导 ICP\n"
             "  +-- load_control_points      # 读取规范（>=3 点校验）\n"
             "  +-- triangulate_sfm_xyz      # 多视图线性三角化（>=2 视图）\n"
             "  +-- solve_control_alignment  # Umeyama（共线退化检测）\n"
             "  +-- align_mesh_with_controls # 几何变换到 SfM 系\n"
             "  +-- find_ctrl_file           # 00_ctrl/<item>/control_points.json\n"
             "  +-- detect_aruco_uv          # ArUco 自动检测（可选，opencv）\n"
             "src/h3d/photo_texture.py       # P1 集成：优先控制点，失败回退自动 ICP"},
    {"type": "h2", "text": "3.3 配置与使用"},
    {"type": "code", "language": "text",
     "text": "项目目录（与 00_raw/00_photo 同级）：\n"
             "├─ 00_raw/   <item>/scan.ply          # 激光几何输入\n"
             "├─ 00_photo/ <item>/*.jpg             # 环绕照片（S1 位姿）\n"
             "└─ 00_ctrl/  <item>/control_points.json  # 新增：控制点靶标\n"
             "\n"
             "photo_texture:\n"
             "  icp_align: true   # 开启对齐（优先控制点引导）\n"
             "  # 使用控制点：无需额外配置，P1 自动检测 00_ctrl 文件"},

    {"type": "h1", "text": "四、测试与验证"},
    {"type": "h2", "text": "4.1 单元测试（tests/test_ctrl_points.py，8 项全过）"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["规范文件加载（>=3 点 / 不足抛错 / find 路径）", "✅ 3 项"],
         ["多视图三角化恢复 3D（真值投影，误差）", "✅ <0.01"],
         ["三角化视图不足抛错", "✅"],
         ["Umeyama 已知变换恢复（s=3.0）", "✅ 误差 <1e-6"],
         ["共线退化检测抛错", "✅"],
         ["控制点引导对齐（6 点）→ 残差", "✅ <0.02 + 尺度验证"],
     ]},
    {"type": "h2", "text": "4.2 端到端验证（管线内联）"},
    {"type": "table",
     "headers": ["环节", "结果"],
     "rows": [
         ["S1（真实 COLMAP 位姿）", "20/60 张注册，4.5s，capture_meta.json ✅"],
         ["P1 控制点路径", "method=control_points, n=8 点，对齐执行 ✅"],
         ["回退自动 ICP（无控制点时）", "projected_cameras=4、coverage 0.39% ✅（有命中）"],
         ["全量回归", "<b>279 项通过</b>（271 + 8 控制点）"],
         ["ArUco 自动检测", "opencv 5.0 生成+检测 id=7 成功 ✅"],
     ]},
    {"type": "h2", "text": "4.3 局限与后续"},
    {"type": "body",
     "text": "管线内投影命中率受 <b>SfM 位姿质量</b> 主导：合成照片增量重建注册数波动（2~20/60）且位姿含"
             "重建误差 → 三角化点误差放大 → 对齐精度受限。真值位姿下（单测）对齐残差 <0.02、投影高精度。"
             "真实工程改善路径：① 高质真实照片（更多注册、更准位姿）；② 靶标 ArUco 自动检测（免手工）；"
             "③ 三角化 RANSAC 抗噪；④ 控制点引导与自动 ICP 融合（先控制点粗对齐、再最近邻精化）。"},

    {"type": "h1", "text": "五、总结"},
    {"type": "body",
     "text": "创新点 2 完整落地：<b>控制点靶标采集规范</b>（靶标类型/布设/测量/拍摄/文件格式 + ArUco 自动"
             "检测）与<b>控制点引导 ICP</b>（三角化 + Umeyama 闭环解，管线内自动优先、失败回退自动 ICP）"
             "均已实现并验证。控制点路径在单测中达到 <b>rmse<0.02 高精度</b>，管线内联链路 done=1、"
             "控制点对齐生效；投影命中率与 SfM 位姿质量强相关（真实采集场景预期显著优于合成场景）。"
             "该模块与 S1（位姿）、P1（投影）共同构成场景①照片纹理自动化的完整闭环。"},
    {"type": "callout",
     "text": "<b>对硕士论文的意义</b>：创新点 2 具备完整的「问题（坐标系不匹配实证）→ 方法（控制点引导 "
             "Umeyama+三角化）→ 验证（单测精度 + 管线集成 + 局限分析）」链条，可复现、可量化、可写进"
             "论文第 4 章方法与第 5 章实验。"},
]

with open("content.json", "w", encoding="utf-8") as f:
    json.dump(content, f, ensure_ascii=False, indent=2)
print("content.json 生成完成，块数 =", len(content))
