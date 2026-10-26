# -*- coding: utf-8 -*-
"""生成「五项开发：M5 纹理入报表 / RANSAC 标定 / dense 质量门 / 多分辨率融合 / 双源融合」content.json。"""
import json

content = [
    {"type": "h1", "text": "一、开发总览"},
    {"type": "body",
     "text": "本批完成五项开发，照片纹理链路与控制点体系收尾闭环：<b>① M5 纹理质量并入 70_report 报表</b>"
             "（accuracy.csv 新增纹理清晰度/接缝/覆盖率/达标四列，进入交付文档与论文实验表格）；<b>② "
             "RANSAC 参数自适应标定</b>（依视图数与噪声自动确定迭代数与阈值）；<b>③ dense 产物质量门</b>"
             "（M1 合并前统计离群剔除 + 密度过滤）；<b>④ 泊松融合多分辨率加速</b>（2048²+ 大纹理）；<b>⑤ "
             "场景①+②双源融合</b>（激光几何 + dense 几何覆盖互补评估与合并）。全量 <b>305 项测试通过</b>；"
             "端到端 dense 闭环 done=1，五项能力全部落地实证。"},
    {"type": "callout",
     "text": "<b>结论</b>：五项开发全部成功。关键实证：accuracy.csv 纹理四列落盘（sharpness=3074.61 / "
             "seam=8.39 / coverage=0.33% / 达标 N）；dense 质量门移除 2.1%（538,413→527,132）；RANSAC "
             "自适应标定在外点下自动收敛；多分辨率融合 2048² 大纹理 <30s；双源合并 M1 done=1。"},

    {"type": "h1", "text": "二、① M5 纹理质量并入 70_report 报表"},
    {"type": "h2", "text": "2.1 实现"},
    {"type": "code", "language": "text",
     "text": "src/h3d/quality/gate.py\n"
             "  ACCURACY_HEADERS += [纹理清晰度, 纹理接缝, 纹理覆盖率, 纹理达标]\n"
             "  build_report：record 增加 texture_sharpness/seam/coverage/ok\n"
             "  _write_accuracy_csv：纹理列输出（N/A=未评估；Y/N=达标判定）"},
    {"type": "h2", "text": "2.2 验证（端到端 accuracy.csv）"},
    {"type": "code", "language": "text",
     "text": "文物编号,中误差,覆盖率,边界边比例,平均点间距,纹理清晰度,纹理接缝,纹理覆盖率,纹理达标,判定\n"
             "rock001,132.82,86.4%,0.0%,0.035,3074.61,8.39,0.0033,N,pass\n"
             "  # 纹理达标=N：coverage 0.33% < coverage_min 0.5%（合成场景稀疏采样）——判定逻辑正确"},
    {"type": "body",
     "text": "纹理四列随质检报告进入交付文档（M6 归档 accuracy.csv）与论文实验表格；无纹理图时输出 N/A，"
             "不阻塞几何质检（回退兼容）。"},

    {"type": "h1", "text": "三、② 控制点 RANSAC 参数自适应标定"},
    {"type": "h2", "text": "3.1 实现"},
    {"type": "code", "language": "text",
     "text": "ctrl_points.py::calibrate_ransac_params(photo_uv, images, cameras)\n"
             "  n_trials     = clamp(8×视图数, 64, 192)   # 视图越多采样越充分\n"
             "  noise_est    = 全量 LS 三角化的重投影误差中位数\n"
             "  threshold_px = clamp(noise_est × 2.5, 2.0, 6.0)  # 噪声大放宽\n"
             "triangulate_sfm_xyz_ransac：n_trials<=0 或 threshold<=0 时自动标定"},
    {"type": "h2", "text": "3.2 验证"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["无噪声：标定阈值 2~6px、噪声估计近 0", "✅"],
         ["噪声 0.8px：噪声被估计（>0.3）、阈值自适应放宽", "✅"],
         ["缺省参数（自动标定）+ 3 外点视图", "误差 <0.10，内点 >=3 ✅"],
     ]},

    {"type": "h1", "text": "四、③ dense 产物质量门"},
    {"type": "h2", "text": "4.1 实现"},
    {"type": "code", "language": "text",
     "text": "src/h3d/dense_gate.py::run_dense_gate(pcd, std_ratio, nb_neighbors,\n"
             "                                     min_density_ratio, voxel_size)\n"
             "  ① 统计离群剔除（k 邻域距离 > mean+std×ratio）\n"
             "  ② 密度过滤（体素密度 < 峰值×ratio 的孤点剔除，尺度自适应）\n"
             "  ③ 可选 voxel 降采样\n"
             "sfm_solve.py：S1 dense 步骤后调用（config: sfm_solve.dense_gate）\n"
             "配置：dense_gate / dense_gate_std=2.0 / dense_gate_density=0.01"},
    {"type": "h2", "text": "4.2 验证"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["离群点剔除（球面 + 5% 离群）", "removed_stat >= 50% 离群 ✅"],
         ["密度过滤（低密度噪声区）", "removed_density >=0，输出非空 ✅"],
         ["点云过小跳过", "n_out == n_in ✅"],
         ["端到端（真实 dense）", "538,413 → 527,132 点（移除 2.1%）✅"],
     ]},

    {"type": "h1", "text": "五、④ 泊松融合多分辨率加速（2048²+）"},
    {"type": "h2", "text": "5.1 实现"},
    {"type": "code", "language": "text",
     "text": "src/h3d/texture/blend.py::poisson_blend_multires(image, mask, max_level)\n"
             "  粗层下采样 2^max_level 做低分辨率调和延拓（迭代少、像素少）\n"
             "  → 逐层上采样作为细层初始 → 原分辨率仅精修 iterations_fine 轮\n"
             "  → 修复区用 上采样×0.5 + 精修×0.5 混合（保平滑又保细节）\n"
             "photo_texture.py：texture_size>=1024 自动 max_level=2"},
    {"type": "h2", "text": "5.2 验证"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["空洞填充（多分辨率）", "覆盖率提升 ✅"],
         ["已知像素保持", "原值不变 ✅"],
         ["2048² 大纹理性能", "<30s（多分辨率）✅"],
         ["level=1 与单分辨率一致性", "平均差异 <30 ✅"],
         ["端到端（photo_e2e, 512²）", "M5 seam=8.39（接缝平滑）✅"],
     ]},

    {"type": "h1", "text": "六、⑤ 场景①+②双源融合（激光 + dense 互补）"},
    {"type": "h2", "text": "6.1 实现"},
    {"type": "code", "language": "text",
     "text": "M1（runner/__init__.py）：\n"
             "  S1 dense 产物（10_dense/fused_dense.ply，质量门后）自动合并进第一站\n"
             "  合并前评估 dense_novel_ratio（距既有几何 >5cm 的 dense 点占比 =\n"
             "  dense 对激光空洞区域的覆盖互补度）\n"
             "  dense 合并进第一站（不新增站点，避免触发无意义多站配准）\n"
             "  合并后统一 preprocess（voxel 降采样自然融合双源）"},
    {"type": "h2", "text": "6.2 验证（端到端）"},
    {"type": "table",
     "headers": ["环节", "结果"],
     "rows": [
         ["S1 dense + 质量门", "fused_dense 538,413 → 527,132 点 ✅"],
         ["M1 双源合并", "+527,132 点并入第一站（529,180 → 23,361 降采样）✅"],
         ["dense_novel_ratio", "0.0（合成场景 dense 与激光同源——指标机制正确）✅"],
         ["全链路", "M2~M6 全跑通，done=1 failed=0 ✅"],
     ]},
    {"type": "callout",
     "text": "<b>关键修复</b>：① M1 dense 曾作为「第二站」输出 → 触发同源几何的无意义双站配准（M2 "
             "RegistrationError）→ 改为合并进第一站；② M1 metrics 在 dense 块后才定义 → 提前初始化。"},

    {"type": "h1", "text": "七、未来开发方向"},
    {"type": "table",
     "headers": ["优先级", "方向", "目标", "关键点"],
     "rows": [
         ["P1", "真实文物照片实机测试（T-16/T-17）", "验证合成场景外的三大假设（S1 注册稳定/投影命中/dense 质量）", "真实数据是当前最大杠杆"],
         ["P1", "纹理达标阈值标定", "coverage_min 与采样策略联动（每面采样数→预期覆盖）", "避免合成场景误判 N"],
         ["P2", "双源融合自适应加权", "dense_novel_ratio 驱动合并权重（低重叠时增强 dense）", "场景①+②真正互补"],
         ["P2", "泊松融合 GPU（cupy/numba 可选）", "4K 纹理实时", "依赖可选、自动降级"],
         ["P2", "M5 纹理质量并入 report.html", "HTML 报表可视化纹理维度", "build_report html 模板扩展"],
         ["P3", "增量式 dense 更新", "多时相 dense 只补新视角", "数字孪生维护场景"],
     ]},
    {"type": "callout",
     "text": "<b>优先建议</b>：① 真实文物实机测试（决定 S1/P1/dense 三大核心假设的最终结论）；② 纹理达标"
             "阈值与采样策略联动标定（让 M5 纹理维度对真实数据给出可靠判定）；③ 双源融合自适应加权"
             "（dense_novel_ratio 驱动——让场景①+②在真实激光空洞场景发挥覆盖互补价值）。"},

    {"type": "h1", "text": "附录：产物与日志"},
    {"type": "table",
     "headers": ["产物", "路径", "说明"],
     "rows": [
         ["新源码", "src/h3d/quality/gate.py（纹理列）/ src/h3d/ctrl_points.py（calibrate）/ src/h3d/dense_gate.py / src/h3d/texture/blend.py（multires）/ src/h3d/runner/__init__.py（双源合并）", "五项开发"],
         ["新测试", "test_dense_gate.py（3）/ test_blend_multires.py（4）/ test_ctrl_points_ext.py::TestCalibrate（3）", "10 项新增"],
         ["端到端报表", "photo_e2e/70_report/rock001/accuracy.csv（纹理四列）", "M5 纹理入报表实证"],
         ["端到端日志", "photo_e2e/batch5.log / 10_dense/rock001/fused_dense.ply（质量门后）", "全链路记录"],
     ]},
]

with open("content.json", "w", encoding="utf-8") as f:
    json.dump(content, f, ensure_ascii=False, indent=2)
print("content.json 生成完成，块数 =", len(content))
