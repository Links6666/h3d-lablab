# -*- coding: utf-8 -*-
"""生成「四顶开发：CG GPU 化 / 增量 dense 受限分析 / X1 入 accuracy / 色卡鲁棒化+novel 回写」+ 实机数据要求。"""
import json

content = [
    {"type": "h1", "text": "一、开发总览"},
    {"type": "body",
     "text": "本批四项开发：<b>三项成功、一项部分受阻（如实分析）</b>。成功项：<b>① CG GPU 化</b>"
             "（cupy.sparse.linalg.cg，4K 稳态 0.54s、结果与 CPU 完全一致）；<b>② X1 形变指标入 "
             "accuracy.csv</b>（论文实验表格追加列，端到端生效）；<b>③ 色卡定位鲁棒化 + novel 在线标定"
             "回写</b>（亮框回退/单应校验/3 点仿射 + 指数平滑持久化）。部分受阻项：<b>④ 增量 dense 子集 "
             "PMS 实机验证</b>——COLMAP undistorter 对「模型图像集≠照片目录」场景崩溃（实测 exit "
             "3221226505），子集机制受限（详细分析见第五章）。全量 <b>325 项测试通过</b>。"},
    {"type": "callout",
     "text": "<b>结论</b>：GPU CG 兑现（0.54s）；X1 进入 accuracy.csv（论文表格完整）；色卡/novel 鲁棒机制"
             "就绪；增量 dense 子集受 COLMAP 限制（替代方案：子集模型构建 + 增量 fusion，待真实数据"
             "验证）。"},

    {"type": "h1", "text": "二、① CG GPU 化（cupy.sparse.linalg.cg）"},
    {"type": "h2", "text": "2.1 实现"},
    {"type": "code", "language": "text",
     "text": "blend_cg.py::poisson_blend_cg 增加 GPU 分支：\n"
             "  cupy 可用 -> A 转 cupyx.scipy.sparse.csr_matrix\n"
             "            -> cupyx.scipy.sparse.linalg.cg 逐通道求解\n"
             "            -> 结果 asnumpy 回传（idx_r/idx_c fancy 索引写入）\n"
             "  cupy 不可用/失败 -> 自动降级 CPU（scipy.sparse.linalg.cg）"},
    {"type": "h2", "text": "2.2 验证（4K=4096² 基准）"},
    {"type": "table",
     "headers": ["实现", "4K 耗时", "备注"],
     "rows": [
         ["CPU CG（scipy）", "0.64s", "已达 <1s 实时"],
         ["GPU CG（cupy）", "0.54s（稳态）", "首次含 cupy 初始化 ~1s"],
         ["结果一致性", "与 CPU 平均差 0.00", "求解器数值等价 ✅"],
         ["降级安全", "cupy 失败自动 CPU", "失败隔离 ✅"],
     ]},
    {"type": "body",
     "text": "GPU CG 稳态 0.54s vs CPU 0.64s（稀疏矩阵-向量乘是 GPU 大算子，收益 16%）；首次调用含 "
             "cupy 初始化开销（~0.5s），管线内单次纹理输出无感知差异。"},

    {"type": "h1", "text": "三、② X1 形变指标入 accuracy.csv（论文实验表格）"},
    {"type": "h2", "text": "3.1 实现"},
    {"type": "code", "language": "text",
     "text": "runner/__init__.py::_append_x1_to_accuracy(acc_csv, item_id, metrics)\n"
             "  X1 stage 完成后读 70_report/<item>/accuracy.csv（M5 产物）\n"
             "  → 表头追加「X1最大形变(mm)」「X1变化占比」两列\n"
             "  → 数据行追加（nan → N/A，容错）；缺失/异常跳过不阻断"},
    {"type": "h2", "text": "3.2 验证"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["追加列（表头+数据）", "X1最大形变=12.50 / X1变化占比=3.0% ✅"],
         ["nan 安全", "max_change=nan → N/A ✅"],
         ["端到端（photo_e2e）", "accuracy.csv 追加 N/A,0.0% ✅"],
         ["论文实验表格", "M6 归档 accuracy.csv 含多时相列 ✅"],
     ]},

    {"type": "h1", "text": "四、③ 色卡定位鲁棒化 + novel 在线标定回写"},
    {"type": "h2", "text": "4.1 色卡定位鲁棒化（P3）"},
    {"type": "code", "language": "text",
     "text": "checker_locator.py::locate_colorchecker_robust\n"
             "  分层策略：\n"
             "    1) >=4 ArUco → 透视单应（校验 cond<1e4，退化拒绝）\n"
             "    2) 3 ArUco → 平行四边形补齐第 4 角（仿射近似）\n"
             "    3) 无 ArUco → 亮框检测（二值化最大白矩形）→ minAreaRect 4 角\n"
             "  单应条件数校验防退化；透视反投提取 4×6 色块"},
    {"type": "h2", "text": "4.2 novel 在线标定回写（P3）"},
    {"type": "code", "language": "text",
     "text": "novel_calib.py::update_novel_threshold(prev, cur, lr=0.3)  # 指数平滑\n"
             "novel_calib.py::save_novel_threshold(root, item, novel, thr_m)\n"
             "  # 90_meta/novel_threshold.json 跨运行持久化\n"
             "  # 每次 M1 dense 合并后回写（lr=0.3 收敛跨场景稳定值）\n"
             "runner M1：在线标定 → 回写（novel_threshold_file 记录）"},
    {"type": "h2", "text": "4.3 验证"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["novel 平滑更新（0.4→0.2 → 0.34）", "✅"],
         ["跨运行持久化（0.35→0.15 → 0.29）", "✅ 90_meta JSON 读写"],
         ["色卡分层定位（3 ArUco 仿射 / 亮框回退）", "代码就绪（真实色卡照片验证待数据）"],
         ["单应退化校验", "cond>1e4 拒绝 ✅"],
     ]},

    {"type": "h1", "text": "五、④ 部分受阻分析：增量 dense 子集 PMS 实机验证"},
    {"type": "h2", "text": "5.1 受阻原因（实测）"},
    {"type": "body",
     "text": "子集 dense 实测两次失败：① undistorter 对「模型图像集 ⊄ 照片目录」场景崩溃（exit "
             "3221226505，COLMAP 栈溢出）——COLMAP 设计假定 photo_dir 与模型全集一致；② 合成照片子集"
             "（新视角 2+参考 4）mapper 找不到 good initial pair（合成照片特征稀疏、匹配率低）。结论："
             "<b>COLMAP 官方工具链不支持「仅新视角」稠密化</b>——patch_match_stereo 无按图过滤，"
             "undistorter 对子集模型脆弱。"},
    {"type": "h2", "text": "5.2 替代方案（可落地）"},
    {"type": "table",
     "headers": ["方案", "说明", "状态"],
     "rows": [
         ["子集模型构建", "model_converter 提取 6 图子模型（手工过滤 images.txt）→ 全量照片目录 dense", "待实现（COLMAP 兼容路径）"],
         ["增量 fusion", "新 fused 与旧 fused 体素去重合并（已实现 _incremental_dense_metrics）", "✅ 已就绪"],
         ["性能理论", "子集 6/60 视角 → 耗时 ~44s vs 全量 440s（10 倍节省）", "待实机确认"],
         ["自研多视角立体（论文）", "按新视角深度图增量计算（绕开 COLMAP 限制）", "研究探索方向"],
     ]},
    {"type": "callout",
     "text": "<b>影响评估</b>：增量 dense 是数字孪生维护的优化项（非核心链路）——全量 dense 已闭环（S1 "
             "dense → M1 合并 → 全链 done=1）；子集加速待真实数据 + 子集模型方案落地后验证。"},

    {"type": "h1", "text": "六、下一步发展方向"},
    {"type": "table",
     "headers": ["优先级", "方向", "目标"],
     "rows": [
         ["P1", "真实文物数据全链路实机闭环", "S1 标定/色卡定位/X1 形变/novel 阈值全部实证"],
         ["P1", "子集模型构建方案落地（COLMAP 兼容）", "增量 dense 真子集（绕过 undistorter 崩溃）"],
         ["P2", "CG GPU 全链替换（大纹理默认 GPU）", "4K 纹理管线级实时"],
         ["P2", "X1/X2 指标进论文实验表格定稿", "多时相+色差数据表"],
         ["P3", "自研增量深度图（论文创新点）", "绕开 COLMAP 限制的增量稠密化"],
     ]},

    {"type": "h1", "text": "七、实机验证数据要求与获取方法"},
    {"type": "h2", "text": "7.1 数据要求（T-16/T-17 用例）"},
    {"type": "table",
     "headers": ["数据项", "要求", "用途"],
     "rows": [
         ["照片集", "1 尊文物 60~120 张（≥60% 重叠，环绕+多高度，含暗部细节）", "S1 位姿/特征标定/P1 投影"],
         ["色卡照片", "ColorChecker24 置于文物旁同框拍摄（≥3 张，含正视）", "X2 色差自动定位"],
         ["控制点", "6~10 个 ArUco 靶标（laser_xyz + photo_uv）", "创新点 2 对齐精度实证"],
         ["激光点云（可选）", "1~5 站 PLY（若仅测照片链路可省略）", "M1~M3 几何 + 双源融合"],
         ["00_ref 参考期（可选）", "上期 dense/模型 PLY（同文物多时相）", "X1 形变检测实证"],
     ]},
    {"type": "h2", "text": "7.2 获取方法（推荐流程）"},
    {"type": "body",
     "text": "<b>设备</b>：单反/微单（RAW+JPG）或高像素手机；三脚架 + 旋转台；ArUco 打印（DICT_4X4_50，"
             "10cm×10cm 防水）；ColorChecker24 标准色卡。<b>拍摄</b>：① 文物不转、相机环绕 30° 步进（12"
             "方位 × 3 高度）；② 靶标粘贴于稳定位置（激光系坐标用扫描仪靶标测量或手工在点云中标注）；"
             "③ 色卡同框 3 张正视。<b>数据组织</b>：按管线目录规范放入\n"
             "    00_raw/<item>/*.ply（激光）\n"
             "    00_photo/<item>/*.jpg（照片）\n"
             "    00_ctrl/<item>/control_points.json（控制点）\n"
             "    00_ref/<item>/*.ply（可选，上期 dense）\n"
             "然后运行：h3d.cli batch -c config.yaml（S1+M1~M6+P1+X1+X2 全链）。"},
    {"type": "h2", "text": "7.3 验收标准"},
    {"type": "table",
     "headers": ["指标", "阈值", "说明"],
     "rows": [
         ["S1 注册率", "≥60%（36/60 张）", "特征标定生效"],
         ["P1 投影覆盖率", "≥2%（联动阈值判定）", "控制点对齐精度"],
         ["dense 点数", "≥50 万（60 张）", "场景②闭环"],
         ["X2 色差", "ΔE 均值 ≤ 5（真实色卡）", "色彩保真"],
         ["X1 形变", "已知 3cm 形变被检出（max_change ≈30mm）", "多时相监测"],
     ]},
    {"type": "callout",
     "text": "<b>数据获取提醒</b>：真实文物采集需文保单位许可与专业设备（激光扫描仪可选）；若仅验证照片"
             "链路（S1/P1/X2），相机 + 色卡 + 靶标即可（激光点云可用 00_raw 任意几何替代，或由 dense "
             "产物承担几何——场景②纯照片路线）。数据就绪后按 7.2 目录组织即可一键运行。"},

    {"type": "h1", "text": "附录：产物与验证汇总"},
    {"type": "table",
     "headers": ["产物", "路径", "说明"],
     "rows": [
         ["新源码", "blend_cg.py（GPU 分支）/ runner（_append_x1_to_accuracy + novel 回写）/ checker_locator.py（robust）/ novel_calib.py（回写）", "三项成功开发"],
         ["新测试", "test_novel_calib（+2）/ test_texture_quality（X1 追加 2）", "4 项新增（累计 325 项）"],
         ["端到端", "accuracy.csv 追加 X1 列（N/A,0.0%）+ 4K GPU 0.54s", "实证"],
         ["受阻记录", "增量 dense 子集（COLMAP 限制）——第五章", "部分受阻分析"],
     ]},
]

with open("content.json", "w", encoding="utf-8") as f:
    json.dump(content, f, ensure_ascii=False, indent=2)
print("content.json 生成完成，块数 =", len(content))
