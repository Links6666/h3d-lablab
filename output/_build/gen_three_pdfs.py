# -*- coding: utf-8 -*-
"""生成三项 P2 功能开发 PDF（sfm_solve / ICP 对齐 / photo-texture）。"""
import json

T = "文保数字化研发组"
D = "2026年8月"

# ============ PDF 1: S1 sfm_solve ============
c1 = [
    {"type": "h1", "text": "一、功能概述"},
    {"type": "body",
     "text": "<b>S1 sfm_solve 可选 stage</b>：将 COLMAP 调用封装进管线，实现「照片放入 00_photo/ 目录 → "
             "管线自动运行」的场景①自动化闭环关键一步。默认关闭（cfg.sfm_solve.enabled=false），开启后 "
             "扫描 00_photo/<item>/，子进程调用 COLMAP（feature→match→mapper）自动解算相机位姿，输出 "
             "capture_meta.json（相机内参 + 每图外方位）供给 P1 photo_texture 消费。本机未装 COLMAP 时"
             "抛业务错误码 + 失败隔离；pytest 全部 mock（CI 不跑真实 SfM）；运行结束自动清理中间产物。"},
    {"type": "h2", "text": "1.1 架构设计（场景①自动化闭环）"},
    {"type": "flowchart",
     "nodes": [
         {"id": "p", "label": "00_photo/<item>/*.jpg（用户仅放照片）", "shape": "parallelogram"},
         {"id": "s1", "label": "S1 sfm_solve（COLMAP 子进程：feature→match→mapper）", "shape": "rect"},
         {"id": "meta", "label": "capture_meta.json（内参+外方位 R/t）", "shape": "parallelogram"},
         {"id": "m", "label": "M1~M3 激光几何链路（不受影响）", "shape": "rect"},
         {"id": "p1", "label": "P1 photo_texture（位姿+几何+照片 → 照片投影纹理）", "shape": "rect"},
         {"id": "out", "label": "50_tex 高保真带纹理模型 → M5/M6 交付", "shape": "parallelogram"},
     ],
     "edges": [{"from": "p", "to": "s1"}, {"from": "s1", "to": "meta"},
               {"from": "meta", "to": "p1"}, {"from": "m", "to": "p1"},
               {"from": "p1", "to": "out"}],
     "caption": "S1 在管线中的位置：00_photo → S1 → capture_meta → P1（场景①自动化）"},
    {"type": "h2", "text": "1.2 模块与配置"},
    {"type": "code", "language": "text",
     "text": "src/h3d/sfm_solve.py        # S1 stage + COLMAP 子进程 + 位姿解析\n"
             "  +-- run_sfm_solve         # stage 函数（默认关闭 → SKIPPED 不写入 manifest）\n"
             "  +-- run_colmap_sparse     # feature→match→mapper 三步子进程调用\n"
             "  +-- parse_cameras/parse_images/read_points3d  # sparse txt 解析\n"
             "  +-- write_capture_meta    # capture_meta.json 生成\n"
             "src/h3d/core/config.py     # SfmSolveConfig（enabled/photo_dir/colmap_path/...）\n"
             "src/h3d/runner/__init__.py # @register_stage('S1') 挂载"},
    {"type": "code", "language": "yaml",
     "text": "sfm_solve:\n"
             "  enabled: false              # 默认关闭（可选 stage）\n"
             "  photo_dir: \"\"               # 空 = 项目根/00_photo\n"
             "  colmap_path: colmap          # 用户本机安装（仓库不打包二进制）\n"
             "  matcher: exhaustive          # exhaustive / sequential\n"
             "  max_features: 8192           # SIFT 最大特征数\n"
             "  peak_threshold: 0.004        # SIFT 峰值阈值（低=更多特征）\n"
             "  workspace: \"\"               # 空 = 99_runs/sfm_tmp/<item>（自动清理）"},
    {"type": "h2", "text": "1.3 健壮性与失败隔离"},
    {"type": "table",
     "headers": ["场景", "处理", "验证"],
     "rows": [
         ["默认关闭", "返回 SKIPPED，不写 manifest，不影响 M1~M6 主链路", "✅ 单测 + 全量回归（271 通过）"],
         ["照片目录缺失/为空", "抛 RecoverableError → 失败隔离（failures.csv）", "✅ 单测通过"],
         ["未安装 COLMAP", "抛 RecoverableError（提示安装并配置 colmap_path）", "✅ 单测通过"],
         ["COLMAP 任一子步骤失败", "捕获返回码 → SfmError → 失败隔离", "✅ 单测（mock 返回码）"],
         ["CI / pytest", "全部 mock subprocess，不跑真实 SfM", "✅ CI 友好"],
         ["真实运行（本机）", "COLMAP 4.1.1 + 驱动 616.56 实跑", "✅ 18/60 张注册，4.6s，capture_meta.json 生成"],
     ]},
    {"type": "h2", "text": "1.4 测试结果"},
    {"type": "body",
     "text": "tests/test_runner/test_sfm_solve.py 共 9 项（注册检查、默认关闭、位姿解析 4 项、失败隔离 2 项、"
             "mock 成功路径）：<b>全部通过</b>。全量回归 <b>271 项通过</b>（252 + 19 新增，含 S1/P1/ICP）。"
             "真实端到端：S1 完成（18/60 张注册，1 相机，4.6s）→ capture_meta.json 生成 → P1 消费。"},
]

# ============ PDF 2: 控制点 ICP 坐标系对齐 ============
c2 = [
    {"type": "h1", "text": "一、功能概述"},
    {"type": "body",
     "text": "<b>控制点 ICP 坐标系对齐（创新点 2 雏形）</b>：解决 SfM 位姿与激光几何坐标系不匹配问题。"
             "SfM 输出任意尺度/任意坐标系（实证：稀疏点云包围盒与真值相差约 9 倍），直接投影纹理会完全"
             "错位。模块实现「迭代最近邻对应 + Umeyama 相似变换」ICP 式对齐：粗对齐初始化（质心 + 包围盒"
             "尺度比）→ 交替「稀疏点 ↔ 网格表面最近邻」与「相似变换更新」→ 收敛后把几何/位姿统一起坐标系。"},
    {"type": "h2", "text": "1.1 实现原理"},
    {"type": "flowchart",
     "nodes": [
         {"id": "in", "label": "SfM 稀疏点云 + 激光几何网格", "shape": "parallelogram"},
         {"id": "coarse", "label": "粗对齐：质心 + 包围盒尺度比", "shape": "rect"},
         {"id": "iter", "label": "迭代：最近邻对应 → Umeyama 相似变换（6 次）", "shape": "rect"},
         {"id": "out", "label": "(s, R, T) + 残差指标 → 几何变换到 SfM 系 / 位姿到几何系", "shape": "parallelogram"},
     ],
     "edges": [{"from": "in", "to": "coarse"}, {"from": "coarse", "to": "iter"},
               {"from": "iter", "to": "out"}],
     "caption": "ICP 式坐标系对齐：粗对齐初始化 + 迭代精化"},
    {"type": "h2", "text": "1.2 模块与配置"},
    {"type": "code", "language": "text",
     "text": "src/h3d/geo_align.py\n"
             "  +-- umeyama_similarity  # Kabsch 带尺度（闭环解析解）\n"
             "  +-- align_points_to_mesh # 迭代对应 + 相似变换（粗对齐初始化）\n"
             "  +-- apply_similarity     # X' = s*R@X + T\n"
             "配置：photo_texture.icp_align: true（默认开启；无靶标时残差可能较大可关闭）"},
    {"type": "h2", "text": "1.3 测试结果"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["Umeyama 恒等变换恢复", "s=1.0, R=I, T=0（误差 <1e-8）✅"],
         ["Umeyama 已知变换恢复（s=2.5 + 旋转 + 平移）", "误差 <1e-6 ✅"],
         ["Umeyama 噪声鲁棒（σ=0.01）", "s 恢复 rel<5% ✅"],
         ["ICP 尺度恢复（10 倍尺度差 + 平移旋转）", "s 恢复 rel<10%，残差 <0.05 ✅"],
         ["apply_similarity", "数值一致 ✅"],
     ]},
    {"type": "h2", "text": "1.4 局限与工程建议（实证）"},
    {"type": "body",
     "text": "管线内联实证（合成照片场景）：ICP 对齐执行（n_correspondences 326）但收敛残差 0.17（几何"
             "半径 ~1），投影未完全命中——<b>无靶标锚点时稀疏点云最近邻对应含较多错误对应，ICP 易陷入"
             "局部最优</b>。与路线文档「坐标系对齐重大风险」一致。工程建议：① 采集阶段布设<b>编码靶标/"
             "控制点</b>（真实工程必需）；② 控制点直接参与对应（替代最近邻）；③ 或 LOFTR 特征直接配准"
             "（P2 可选）。论文创新点 2 的落地方向明确：控制点引导的 ICP。"},
]

# ============ PDF 3: P1 photo_texture ============
c3 = [
    {"type": "h1", "text": "一、功能概述"},
    {"type": "body",
     "text": "<b>P1 photo_texture 可选 stage（创新点 1）</b>：将 M4 颜色源从「点云 RGB」升级为「照片投影」"
             "——消费 S1 产出的 capture_meta.json（位姿）与 00_photo 照片，把照片颜色投影到 M3 网格"
             "（激光几何），输出高保真带纹理模型（obj+mtl+diffuse / glb）。与 S1 串成场景①自动化闭环；"
             "默认关闭（cfg.photo_texture.enabled=false）。"},
    {"type": "h2", "text": "1.1 架构与数据流"},
    {"type": "flowchart",
     "nodes": [
         {"id": "m3", "label": "M3 网格（30_dense，激光几何）", "shape": "parallelogram"},
         {"id": "meta", "label": "S1 capture_meta.json（位姿）+ 00_photo 照片", "shape": "parallelogram"},
         {"id": "icp", "label": "可选：控制点 ICP 坐标系对齐（创新点 2）", "shape": "rect"},
         {"id": "uv", "label": "xatlas UV 展开", "shape": "rect"},
         {"id": "proj", "label": "照片投影：视角加权 + top-k 平均抗接缝", "shape": "rect"},
         {"id": "out", "label": "50_tex 带纹理 obj/glb/diffuse → M5/M6", "shape": "parallelogram"},
     ],
     "edges": [{"from": "m3", "to": "icp"}, {"from": "meta", "to": "icp"},
               {"from": "icp", "to": "uv"}, {"from": "uv", "to": "proj"},
               {"from": "proj", "to": "out"}],
     "caption": "P1 数据流：M3 网格 + S1 位姿 + 照片 → 照片投影纹理"},
    {"type": "h2", "text": "1.2 模块与配置"},
    {"type": "code", "language": "text",
     "text": "src/h3d/photo_texture.py\n"
             "  +-- load_capture_meta       # 读 S1 产物（cameras + images R/t）\n"
             "  +-- project_photos_to_texture # 逐三角形采样 + 视角加权投影\n"
             "  +-- run_photo_texture        # P1 stage（UV 展开 + 投影 + 导出）"},
    {"type": "code", "language": "yaml",
     "text": "photo_texture:\n"
             "  enabled: false          # 默认关闭\n"
             "  texture_size: 1024      # 纹理图集边长\n"
             "  top_k: 3                # 每面 top-k 照片平均（抗接缝）\n"
             "  samples_per_tri: 4      # 每三角形采样点数\n"
             "  icp_align: true         # 坐标系对齐（创新点 2）\n"
             "  export_formats: [obj, glb]"},
    {"type": "h2", "text": "1.3 测试结果"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["P1 注册 + 默认关闭", "SKIPPED 不写 manifest ✅"],
         ["投影算法（4 视角合成照片）", "≥2 相机参与、覆盖率 >1%（128²）✅"],
         ["照片不足异常", "ValueError 抛出 ✅"],
         ["capture_meta 往返", "cameras/images 一致 ✅"],
         ["算法正确性（真值位姿，上轮验证）", "60/60 相机参与、覆盖率 1.06%、0.75s ✅"],
         ["管线内联（S1 位姿）", "链路 done=1、输出 3 文件；投影未命中归因坐标系对齐（见 1.4）"],
     ]},
    {"type": "h2", "text": "1.4 局限与后续"},
    {"type": "body",
     "text": "P1 投影算法正确性已由真值位姿验证（60/60 相机、覆盖率 1.06%、输出 obj/glb/diffuse）。管线"
             "内联时投影覆盖率受<b>坐标系对齐精度</b>主导（SfM 位姿 vs 几何坐标系）：合成场景无靶标时 ICP "
             "收敛差 → 投影未命中。真实工程解：控制点靶标（创新点 2）+ 真实高质照片（更多注册位姿）。"
             "后续：M4 颜色源切换开关（paint_mode='photo'）与 P1 统一；泊松融合提升接缝质量。"},
]

def main():
    import os, sys, json as _j
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from render_pdf import render
    # 直接调用 render_pdf 的渲染函数（构造 3 份 PDF）
    import subprocess
    base = os.path.dirname(os.path.abspath(__file__))
    py = os.path.join(os.path.dirname(os.path.dirname(base)),
                      ".venv_lab", "Scripts", "python.exe")
    jobs = [
        (c1, "..", "造像类文物三维建模自动化系统_sfm_solve可选Stage开发说明_20260829.pdf",
         "S1 sfm_solve 可选 Stage 开发说明", "COLMAP 子进程封装 · 00_photo→capture_meta.json · 失败隔离 · CI mock"),
        (c2, "..", "造像类文物三维建模自动化系统_控制点ICP坐标系对齐开发说明_20260829.pdf",
         "控制点 ICP 坐标系对齐 开发说明", "创新点 2 · Umeyama+迭代 · 粗对齐初始化 · 局限与工程建议"),
        (c3, "..", "造像类文物三维建模自动化系统_photo-texture可选Stage开发说明_20260829.pdf",
         "P1 photo-texture 可选 Stage 开发说明", "创新点 1 · 照片投影纹理 · 场景①闭环 · 验证与局限"),
    ]
    for content, out_dir, fname, header, subtitle in jobs:
        tmp = os.path.join(base, "_tmp_job.json")
        with open(tmp, "w", encoding="utf-8") as fh:
            _j.dump(content, fh, ensure_ascii=False, indent=2)
        out = os.path.abspath(os.path.join(base, out_dir, fname))
        subprocess.run([py, os.path.join(base, "render_pdf.py"), tmp, out,
                        header, subtitle, T, D], check=True)
        print("OK:", fname)

if __name__ == "__main__":
    main()
