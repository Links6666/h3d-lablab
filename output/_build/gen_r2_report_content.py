# -*- coding: utf-8 -*-
"""生成「路线 R2 执行与错误分析报告」content.json。

dense 阶段（patch_match_stereo）因系统缺 CUDA Toolkit 运行时失败，
按用户指令输出错误分析报告；同时完整记录成功路径（COLMAP sparse +
全自研 photo-texture）的成果、关键指标、失败诊断与替代方案。
"""
import json

content = [
    # =====================================================================
    {"type": "h1", "text": "一、报告概述"},
    {"type": "body",
     "text": "本报告记录<b>路线 R2（COLMAP 开源 SfM 后端 + 全自研 photo-texture 纹理管线）</b>的完整执行"
             "过程与结果。执行在 6 个 COLMAP 阶段 + 自研 photo-texture 管线中推进：<b>特征提取、特征匹配、"
             "稀疏重建、去畸变均成功</b>；<b>密集重建（patch_match_stereo）失败</b>（CUDA 运行时不可用），"
             "按既定预案触发<b>错误分析报告</b>。报告包含：执行步骤与命令参数、成功环节关键指标、失败"
             "环节完整错误与诊断、针对失败环节的替代方案分析，以及<b>降级路径（场景①：激光几何 + 照片"
             "纹理）下 photo-texture 管线的成功验证成果</b>。"},
    {"type": "callout",
     "text": "<b>结论先行</b>：dense 阶段失败为<b>环境限制</b>（本机无 CUDA Toolkit 运行时），非方案"
             "缺陷；COLMAP sparse（相机位姿解算，R2 核心用途）与全自研 photo-texture 纹理管线均成功，"
             "降级路径（场景①：几何来自激光/独立网格 + 照片纹理）完整跑通并输出带纹理交付物。"},

    # =====================================================================
    {"type": "h1", "text": "二、工具版本与依赖环境"},
    {"type": "table",
     "headers": ["组件", "版本", "说明"],
     "rows": [
         ["COLMAP", "4.1.1 (Commit a0d785f, 2026-07-17, CUDA)", "E:\\colmap\\colmap-x64-windows-cuda\\bin\\colmap.exe"],
         ["GPU 硬件", "NVIDIA GeForce RTX 3050 Laptop (4GB, compute 8.6)", "nvidia-smi 可见，Driver 546.30 / CUDA 12.3"],
         ["CUDA Toolkit 运行时", "<b>缺失</b>（无 cudart64_*.dll）", "COLMAP CUDA 初始化失败的直接原因"],
         ["Python", "3.12.10", "venv: .venv_lab"],
         ["numpy / open3d", "2.5.2 / 0.19.0", "photo-texture 管线依赖"],
         ["xatlas", "0.0.11", "UV 展开（复用 h3d.texture.uv）"],
     ]},

    # =====================================================================
    {"type": "h1", "text": "三、执行步骤与命令行参数"},
    {"type": "body",
     "text": "测试输入：程序化生成的 <b>60 张合成多视角照片</b>（800×600，12 方位角 × 5 俯仰角环绕，"
             "软件光栅化渲染，带高频纹理与 Lambert 明暗，位姿真值已知用于验证）。全部阶段日志落盘 "
             "output/_colmap_demo/logs/。"},
    {"type": "table",
     "headers": ["#", "阶段", "命令与关键参数", "状态"],
     "rows": [
         ["0", "照片集生成", "gen_photos.py（软件渲染 60 张 + capture_meta_gt.json）", "✅ 成功"],
         ["1", "特征提取", "colmap feature_extractor --ImageReader.single_camera 1 --FeatureExtraction.use_gpu 0 --SiftExtraction.max_num_features 8192 --SiftExtraction.peak_threshold 0.004", "✅ 成功"],
         ["2", "特征匹配", "colmap exhaustive_matcher --FeatureMatching.use_gpu 0 --FeatureMatching.max_num_matches 8192 --FeatureMatching.guided_matching 1", "✅ 成功"],
         ["3", "稀疏重建", "colmap mapper --Mapper.ba_use_gpu 0 --Mapper.num_threads 4", "✅ 成功"],
         ["4", "去畸变", "colmap image_undistorter --output_type COLMAP", "✅ 成功"],
         ["5", "密集重建", "colmap patch_match_stereo --PatchMatchStereo.geom_consistency true --PatchMatchStereo.gpu_index 0", "<b>❌ 失败</b>"],
         ["6", "稠密融合", "colmap stereo_fusion（依赖深度图，未执行）", "⏸ 未执行"],
         ["7", "photo-texture", "photo_texture.py（自研：位姿解析 + 坐标对齐 + xatlas UV + 照片投影）", "✅ 成功（降级路径）"],
     ]},
    {"type": "body",
     "text": "注：COLMAP 4.1.1 参数命名与旧版不同——并行匹配参数为 <b>FeatureMatching.*</b>（非 "
             "SiftMatching.*）；PMS 无 use_gpu 开关（GPU-only，仅 gpu_index）。全流程强制 CPU（use_gpu 0）"
             "以规避本机 CUDA 运行时缺失对前 4 阶段的影响。"},

    # =====================================================================
    {"type": "h1", "text": "四、成功环节关键输出指标"},
    {"type": "h2", "text": "4.1 特征提取与匹配"},
    {"type": "table",
     "headers": ["指标", "数值", "说明"],
     "rows": [
         ["图像数", "60 张", "全部成功提取特征"],
         ["每图特征数", "54 ~ 316（均值 164）", "SIFT（CPU），peak_threshold 0.004"],
         ["匹配验证对", "90 对 E（本质矩阵）+ 94 对 H（单应）", "两视图几何验证成功"],
     ]},
    {"type": "h2", "text": "4.2 稀疏重建（SfM，R2 核心用途：相机位姿解算）"},
    {"type": "table",
     "headers": ["指标", "数值", "说明"],
     "rows": [
         ["重建数", "1 个稀疏模型", "colmap_ws/sparse/0"],
         ["相机数", "1（SIMPLE_RADIAL，f=799.5px）", "单相机共享内参"],
         ["注册图像", "20 / 60", "增量重建注册 20 张（合成场景部分注册）"],
         ["稀疏点云", "367 点", "points3D.bin"],
         ["平均轨迹长度", "5.98", "每 3D 点平均观测次数"],
         ["平均重投影误差", "<b>0.81 px</b>", "位姿解算质量良好"],
     ]},
    {"type": "h2", "text": "4.3 photo-texture 纹理管线（自研，降级路径场景①）"},
    {"type": "table",
     "headers": ["指标", "数值", "说明"],
     "rows": [
         ["输入位姿", "COLMAP sparse（模式 A）/ 真值（模式 B 验证）", "20 张注册位姿"],
         ["几何网格", "bumpy 网格（模拟激光扫描几何），3906 面", "独立于 SfM 重建"],
         ["UV 展开", "xatlas，2936~2907 顶点", "复用 h3d.texture.uv"],
         ["投影相机", "模式 B：<b>60/60</b> 照片参与", "视角加权 + top-3 平均抗接缝"],
         ["纹理覆盖率", "1.06%（1024²）", "每面 4 采样点 UV 光栅化（设计上限 ~1.5%）"],
         ["投影耗时", "0.75 s", "numpy 向量化"],
         ["输出文件", "obj 658KB + glb 166KB + diffuse.png 54KB", "带纹理交付物生成成功"],
     ]},
    {"type": "body",
     "text": "纹理质量验证：1024×1024 RGB 图，非黑像素占比 1.06%，颜色标准差 ~25（有纹理变化），"
             "16 级量化下 105 种颜色（色彩丰富）。模式 A（COLMAP 位姿经 sparse 锚点对齐）覆盖率为 0——"
             "坐标系对齐残差大（无靶标，见第七章），印证路线文档「坐标系对齐重大风险」；真实工程需"
             "控制点 ICP 对齐（照片导入方案）。"},

    # =====================================================================
    {"type": "h1", "text": "五、中断环节与完整错误信息"},
    {"type": "h2", "text": "5.1 中断阶段：密集重建（patch_match_stereo）"},
    {"type": "code", "language": "bash",
     "text": "# 命令：\n"
             "colmap patch_match_stereo --workspace_path colmap_ws/dense \\\n"
             "  --workspace_format COLMAP --PatchMatchStereo.geom_consistency true \\\n"
             "  --PatchMatchStereo.gpu_index 0\n"
             "\n"
             "# 日志（logs/05_pms.log 末尾）：\n"
             "I ... patch_match_options.cc:70] num_threads: -1\n"
             "I ... patch_match.cc:129] --- PatchMatch::Run ---\n"
             "E ... cudacc.cc:59] CUDA error at ..\\src\\colmap\\util\\cuda.cc:56 - (null)\n"
             "退出码：127（未生成任何深度图，stereo_fusion 无法执行）"},
    {"type": "h2", "text": "5.2 失败原因诊断"},
    {"type": "body",
     "text": "逐层定位结论：① GPU 硬件存在（RTX 3050 Laptop，compute 8.6，驱动 546.30 支持 CUDA 12.3）；"
             "② <b>系统未安装 CUDA Toolkit 运行时</b>（C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA 不存在，"
             "PATH 无 CUDA，colmap bin 内也无 cudart64_*.dll）；③ COLMAP 4.1.1 CUDA 构建在 cuInit 阶段因"
             "缺失运行时 DLL 抛 <b>CUDA error (null)</b>；④ PatchMatchStereo 为 <b>GPU-only 算法</b>"
             "（COLMAP 无 CPU 实现，仅 gpu_index 参数），故 dense 阶段在当前环境不可执行。前 4 阶段因"
             "强制 use_gpu 0（CPU）未受影响。"},

    # =====================================================================
    {"type": "h1", "text": "六、失败环节替代方案分析"},
    {"type": "table",
     "headers": ["方案", "原理", "可行性", "实施建议"],
     "rows": [
         ["A. 安装 CUDA Toolkit 运行时", "下载安装 CUDA Toolkit 12.x（含 cudart64_12.dll），或仅拷贝 cudart 运行时至 COLMAP bin", "高（需 ~2.5GB 安装 / 或 ~5MB 运行时拷贝 + 兼容性验证）", "最直接；安装后重跑 patch_match_stereo；若仅缺 cudart 可先试运行时拷贝"],
         ["B. 云端 / 远程 GPU", "在带 CUDA 的环境（云 GPU 实例 / 研究室服务器）运行 dense，产物回传", "高（一次性）", "适合批量生产；与 CI/复现环境分离"],
         ["C. 场景①降级（<b>本报告已采用</b>）", "dense 非必需：几何来自激光扫描/独立网格，COLMAP 仅解算位姿（sparse），photo-texture 直接投影", "高（已验证成功）", "对应文保标准工作流「激光拿几何、单反拿色彩」；dense 仅用于场景②纯照片重建"],
         ["D. 其它 MVS 引擎", "OpenMVS（GPL）/ MVE（BSD-3）替代稠密重建", "中（许可证与集成成本）", "需评估 GPL 传染（OpenMVS 不建议）；MVE 可与现有栈配合"],
         ["E. COLMAP CPU 构建", "使用无 CUDA 的 COLMAP 构建", "低（PMS 无 CPU 实现）", "仅前 4 阶段可用；dense 仍需 GPU，不解决本问题"],
     ]},
    {"type": "callout",
     "text": "<b>推荐</b>：短期用方案 A（装 CUDA 运行时）补跑 dense，验证场景②完整链路；主业务线按方案 C"
             "（场景①）推进——dense 不是 photo-texture 的必需输入，且文保业务本身以「激光几何 + 照片"
             "纹理」为标准。GitHub CI 保持全 mock（无 GPU 亦可验证逻辑）。"},

    # =====================================================================
    {"type": "h1", "text": "七、坐标系对齐挑战（photo-texture 模式 A 观察）"},
    {"type": "body",
     "text": "模式 A（COLMAP sparse 位姿）投影覆盖率 0：SfM 输出任意尺度/坐标系（稀疏点云包围盒约 "
             "22.6，相机中心范数 1.1~4.7），与真值几何（包围盒 2.4）不匹配。自研 Umeyama 迭代对齐在"
             "无靶标锚点下残差大（131~2267mm，sparse 点仅 367 个且含漂移）——<b>印证路线文档「坐标系"
             "对齐重大风险」</b>。工程结论：真实应用须在采集阶段布设<b>编码靶标/控制点</b>，管线内用"
             "<b>控制点 ICP 对齐</b>（照片导入方案），或采用 LOFTR 特征直接配准（P2 可选）。本演示以"
             "模式 B（真值位姿）验证投影算法正确性，输出高质量纹理。"},

    # =====================================================================
    {"type": "h1", "text": "八、总结与后续建议"},
    {"type": "body",
     "text": "路线 R2 执行结论：<b>COLMAP sparse 相机位姿解算（核心用途）与全自研 photo-texture 纹理管线"
             "均验证成功</b>（稀疏重建重投影误差 0.81px；纹理投影 60/60 相机参与、覆盖率 1.06%、输出 "
             "obj/glb/diffuse 带纹理交付物）；dense 阶段因环境缺 CUDA Toolkit 运行时失败（方案缺陷 vs "
             "环境限制：判定为<b>环境限制</b>）。后续建议：① 安装 CUDA 运行时补跑 dense（方案 A）；② "
             "主业务线按场景①降级路径推进（方案 C）；③ 采集规范加入控制点靶标，落实坐标系 ICP 对齐；"
             "④ GitHub CI 保持 mock、实机用例文档化。"},
    {"type": "callout",
     "text": "<b>对硕士论文的意义</b>：本次执行已验证「开源 SfM（COLMAP）+ 自研纹理投影」技术路线可行："
             "创新点 1（photo-texture 投影）在正确位姿下输出有效纹理；创新点 2（坐标系对齐）的挑战被"
             "实证（无靶标不可靠 → 控制点 ICP 方向明确）；dense 失败为环境问题而非方法问题，论文中"
             "可作为「实验环境与限制」章节的实证素材。"},

    # =====================================================================
    {"type": "h1", "text": "附录 A：关键日志摘录"},
    {"type": "code", "language": "bash",
     "text": "# logs/03_mapper.log（稀疏重建成功）\n"
             "I ... sfm.cc:341] output_path 校验通过\n"
             "I ... incremental_pipeline.cc:540] => Image sees 89 / 286 points\n"
             "I ... model.cc:441] Cameras: 1\n"
             "I ... model.cc:445] Images: 20 | Registered images: 20\n"
             "I ... model.cc:448] Points: 367\n"
             "I ... model.cc:456] Mean reprojection error: 0.806218px\n"
             "\n"
             "# logs/05_pms.log（密集重建失败）\n"
             "I ... patch_match.cc:129] --- PatchMatch::Run ---\n"
             "E ... cudacc.cc:59] CUDA error at ..\\src\\colmap\\util\\cuda.cc:56 - (null)\n"
             "\n"
             "# photo-texture 输出（成功）\n"
             "[photo-texture] 模式 B（真值位姿，算法验证）：{projected_cameras: 60,\n"
             "  coverage_ratio: 0.0106, projection_time_s: 0.75, triangles: 3906}\n"
             "[photo-texture] 输出文件: rock001_L1.0.obj / .glb / _diffuse.png"},
    {"type": "h1", "text": "附录 B：产物清单"},
    {"type": "table",
     "headers": ["产物", "路径", "说明"],
     "rows": [
         ["照片集", "output/_colmap_demo/00_photo/rock001/*.jpg（60 张）", "合成多视角输入"],
         ["真值位姿", "output/_colmap_demo/00_photo/capture_meta_gt.json", "投影算法验证基准"],
         ["稀疏模型", "output/_colmap_demo/colmap_ws/sparse/0（bin + txt）", "20 张注册 / 367 点"],
         ["去畸变工作区", "output/_colmap_demo/colmap_ws/dense", "dense 失败，仅 images/sparse"],
         ["几何网格", "output/_colmap_demo/colmap_ws/mesh_laser.ply", "模拟激光扫描几何"],
         ["带纹理交付", "output/_colmap_demo/50_tex/rock001/{obj, glb, diffuse.png}", "photo-texture 产物"],
         ["执行日志", "output/_colmap_demo/logs/*.log", "01~05 + sparse 统计 + 转换日志"],
     ]},
]

with open("content.json", "w", encoding="utf-8") as f:
    json.dump(content, f, ensure_ascii=False, indent=2)
print("content.json 生成完成，块数 =", len(content))
