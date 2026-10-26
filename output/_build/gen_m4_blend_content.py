# -*- coding: utf-8 -*-
"""生成「M4 photo 模式 + 泊松融合」开发说明 content.json。"""
import json

content = [
    {"type": "h1", "text": "一、功能概述"},
    {"type": "body",
     "text": "本开发完成两项照片纹理链路的质量与统一性升级：<b>① M4 颜色源切换开关统一（paint_mode="
             "'photo'）</b>——M4 支持以照片投影作为颜色源（替代点云 RGB），与 P1 共享同一核心实现，"
             "颜色源切换收敛为单一配置项；<b>② P1 泊松融合提升接缝质量</b>——照片多视角投影的稀疏"
             "采样与 UV 接缝不连续，通过拉普拉斯调和延拓（Jacobi 迭代求解 Δf=0）填补空洞、平滑接缝，"
             "提升纹理覆盖率与视觉连续性。"},
    {"type": "callout",
     "text": "<b>核心结论</b>：M4 paint_mode='photo' 端到端成功（done=1，M5 pass）；P1 在 M4 photo 模式下"
             "自动跳过（避免重复输出）；泊松融合把纹理非黑占比从 ~1% 提升至 <b>3.88%</b>（空洞填充）；"
             "全量 <b>284 项测试通过</b>（279 + 5 blend 新增）。"},

    {"type": "h1", "text": "二、M4 颜色源切换开关统一（paint_mode='photo'）"},
    {"type": "h2", "text": "2.1 设计"},
    {"type": "body",
     "text": "TextureConfig.paint_mode 取值扩展为 <b>vertex / uv / photo</b>（统一颜色源开关）： vertex=点云 RGB 顶点色（默认）；uv=点云颜色 UV 图集烘焙；<b>photo=照片投影纹理</b> （消费 S1 位姿 + 00_photo 照片）。M4 检测 paint_mode='photo' 时委托共享核心 :func:`texture_mesh_from_photos`，输出 50_tex 带纹理模型；P1 stage 在 M4 已 photo 输出时自动跳过（避免同目录重复产物），场景①可经「M4 photo 模式」或「P1 独立 stage」 两种方式达成。"},
    {"type": "flowchart",
     "nodes": [
         {"id": "cfg", "label": "texture.paint_mode: photo（颜色源开关）", "shape": "parallelogram"},
         {"id": "m4", "label": "M4：委托 texture_mesh_from_photos", "shape": "rect"},
         {"id": "core", "label": "共享核心：对齐 → UV → 投影 → 泊松融合 → 导出", "shape": "rect"},
         {"id": "out", "label": "50_tex 带纹理 obj/glb/diffuse", "shape": "parallelogram"},
         {"id": "p1", "label": "P1（photo_texture.enabled）", "shape": "rect"},
     ],
     "edges": [{"from": "cfg", "to": "m4"}, {"from": "m4", "to": "core"},
               {"from": "core", "to": "out"}, {"from": "cfg", "to": "p1"}],
     "caption": "M4 photo 模式：统一颜色源开关，P1 在 M4 已输出时跳过"},
    {"type": "h2", "text": "2.2 实现"},
    {"type": "code", "language": "text",
     "text": "src/h3d/photo_texture.py\n"
             "  +-- texture_mesh_from_photos  # 共享核心（M4/P1 复用）：\n"
             "  |     capture_meta -> 对齐（控制点优先/自动 ICP）\n"
             "  |     -> xatlas UV -> 照片投影 -> 泊松融合 -> 导出\n"
             "  +-- run_photo_texture          # P1 stage：M4 photo 模式下跳过\n"
             "src/h3d/runner/__init__.py      # M4：paint_mode='photo' 委托核心\n"
             "src/h3d/core/config.py          # TextureConfig.paint_mode += 'photo'"},
    {"type": "code", "language": "yaml",
     "text": "texture:\n"
             "  paint_mode: photo   # vertex / uv / photo（统一颜色源开关）\n"
             "  export_formats: [ply, obj, glb]\n"
             "photo_texture:\n"
             "  enabled: true       # P1 辅助（M4 photo 模式下自动跳过）\n"
             "  poisson_blend: true # 泊松融合开关"},
    {"type": "h2", "text": "2.3 验证"},
    {"type": "table",
     "headers": ["环节", "结果"],
     "rows": [
         ["M4 paint_mode='photo'", "✅ M4 完成（paint_mode=photo，3 文件：obj+mtl+png）"],
         ["P1 跳过逻辑", "✅ 'P1 跳过：M4 已按 paint_mode=photo 输出'"],
         ["M5 质检", "✅ coverage=100.0%, rmse=41.10mm → pass"],
         ["全链路", "✅ total=1 done=1 failed=0"],
         ["全量回归", "<b>284 项通过</b>"],
     ]},

    {"type": "h1", "text": "三、泊松融合提升接缝质量"},
    {"type": "h2", "text": "3.1 实现原理"},
    {"type": "body",
     "text": "照片投影在 UV 空间产生稀疏采样（每面 4 采样点 → 覆盖率 ~1%）与多视角接缝不连续。泊松 融合在<b>梯度域</b>平滑：对待修复区域（mask 邻域空洞）求解拉普拉斯方程 Δf=0（调和延拓）， 以已知像素为 Dirichlet 边界，Jacobi 迭代 f = 四邻域均值至收敛——空洞被平滑填充、接缝 颜色过渡连续；另提供 seam_smooth 对 mask 边界单次低通。"},
    {"type": "flowchart",
     "nodes": [
         {"id": "in", "label": "投影纹理（稀疏 ~1% + 接缝）", "shape": "parallelogram"},
         {"id": "mask", "label": "有效像素 mask + 邻域膨胀（修复区）", "shape": "rect"},
         {"id": "jacobi", "label": "Jacobi 迭代 Δf=0（调和延拓，Dirichlet 边界）", "shape": "rect"},
         {"id": "out", "label": "平滑纹理（覆盖率 3.88%，接缝连续）", "shape": "parallelogram"},
     ],
     "edges": [{"from": "in", "to": "mask"}, {"from": "mask", "to": "jacobi"},
               {"from": "jacobi", "to": "out"}],
     "caption": "泊松融合：拉普拉斯调和延拓填补空洞 + 平滑接缝"},
    {"type": "code", "language": "text",
     "text": "src/h3d/texture/blend.py\n"
             "  +-- poisson_blend(image, mask, iterations=150, tol=1e-3)\n"
             "  |     # Jacobi 求解 Δf=0；修复区=空洞+邻域 2 像素；已知像素保持\n"
             "  +-- seam_smooth(image, mask, radius=1)\n"
             "  |     # mask 边界（邻接非 mask 像素）单次邻域低通\n"
             "  +-- _dilate_mask(mask, radius)   # 四邻域膨胀"},
    {"type": "h2", "text": "3.2 验证"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["空洞填充（覆盖率提升）", "✅ 0.098 → 0.138（测试） / ~1% → 3.88%（端到端）"],
         ["已知像素保持", "✅ 原值不变（Dirichlet 边界）"],
         ["修复区平滑（梯度不增）", "✅ 修复区梯度 ≤ 原边界 1.5×"],
         ["mask 边界平滑（seam_smooth）", "✅ 边界 200 → 160（邻域平均）"],
         ["输入校验", "✅ 形状非法抛 ValueError"],
         ["全量回归", "<b>284 项通过</b>"],
     ]},

    {"type": "h1", "text": "四、下一步开发方向"},
    {"type": "table",
     "headers": ["优先级", "方向", "目标", "关键点"],
     "rows": [
         ["P1", "真实文物照片实机测试（T-16/T-17）", "验证 S1+P1+M4 photo 在真实数据上的投影命中率", "真实照片注册稳定、位姿精度高——预期显著优于合成场景"],
         ["P1", "三角化 RANSAC 抗噪", "控制点三角化对外点鲁棒（提升对齐精度）", "RANSAC + 鲁棒 Umeyama"],
         ["P2", "控制点粗对齐 + 最近邻精化融合", "先控制点闭合解、再自动 ICP 微调", "两阶段对齐提升残差"],
         ["P2", "泊松融合质量评估指标", "量化接缝平滑度（接缝梯度/PSNR）", "M5 新增纹理质量维度"],
         ["P2", "照片重建网格化（dense→mesh）", "场景② fused.ply → M3 网格 → M4 photo 纹理闭环", "dense 已打通，接入 M1~M6"],
         ["P3", "3DGS/NeRF 展示层（路线 C）", "数字展陈资产", "论文探索实验"],
     ]},
    {"type": "callout",
     "text": "<b>优先建议</b>：① 真实文物照片实机测试是当前最大杠杆（合成场景 SfM 注册不稳定、位姿误差大 ——真实高质照片将同时改善 S1 注册数与 P1 投影命中率）；② 三角化 RANSAC 与两阶段对齐 进一步提升创新点 2 的鲁棒性；③ 泊松融合质量指标可并入 M5 质检，形成「纹理清晰度/接缝 质量」可量化维度。"},
]

with open("content.json", "w", encoding="utf-8") as f:
    json.dump(content, f, ensure_ascii=False, indent=2)
print("content.json 生成完成，块数 =", len(content))
