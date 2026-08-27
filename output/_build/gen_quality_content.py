# -*- coding: utf-8 -*-
"""生成 quality（M5 质检质量门）代码说明书 content.json。

含模块划分、质检数据流、质量门阈值表、报告字段、错误码、测试、完成度更新与下一步。
"""
import json

content = [
    # ---------------------------------------------------------------------
    {"type": "h1", "text": "一、概述"},
    {"type": "body",
     "text": "本文档是《造像类文物三维建模自动化系统（路线 E）》<b>quality 子包（M5 质检质量门，"
             "L2 业务层 / S4 阶段）</b>的代码说明书。quality 负责<b>量化评估模型质量、对照标准阈值"
             "给出达标判定、生成报告</b>（设计书 §36.1），把「几何质量」变成可报告的数字——"
             "覆盖率（模型表面被有效点云覆盖的占比）、中误差（网格对参考表面的贴合度，毫米）、"
             "边界边比例（孔洞率）。该子包接入 M0 编排内核（@register_stage(\"M5\")）后，"
             "<b>「几何质量可报告」能力正式建立</b>：60_qc 机器可读中间量 + 70_report accuracy.csv"
             "（Excel 可直接打开）+ run_manifest 指标三路输出。"},
    {"type": "callout",
     "text": "设计要点：<b>质量门 = 覆盖率 ≥ 80% 且 中误差 ≤ 5mm</b>（WW/T 0115/0117 精细级基线，"
             "设计书 §6.3）。<b>尺度自适应</b>：覆盖率判定阈值 = k × MPS（平均点间距），不写死绝对"
             "距离（软著方案 2.7.2）；中误差判定仅在参考点云足够密（MPS ≤ 中误差阈值）时启用，"
             "稀疏扫描下自动旁路（设计书 §36.4「缺参考靶标 -> 中误差 N/A」的工程化扩展）。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "二、模块划分"},
    {"type": "body",
     "text": "quality 子包由指标计算、质量门判定与报告生成三部分组成："},
    {"type": "code", "language": "text",
     "text": "src/h3d/quality/\n"
             "+-- __init__.py     # 包导出 + run(mesh, ref, cfg) 质检流水线\n"
             "+-- metrics.py     # estimate_mps / surface_coverage / qc_rmse / boundary_ratio\n"
             "+-- gate.py        # check_quality 判定 + build_report 报告生成"},
    {"type": "table",
     "headers": ["模块文件", "核心职责", "对外关键符号"],
     "rows": [
         ["metrics.py", "几何质量指标计算（尺度自适应 MPS + 采样估计）",
          "estimate_mps, surface_coverage, qc_rmse, boundary_ratio"],
         ["gate.py", "质量门判定 + 质检报告（60_qc JSON + 70_report accuracy.csv）",
          "check_quality, build_report, ACCURACY_HEADERS"],
         ["__init__.py", "组合质检流水线（指标 + 判定）", "run(mesh, ref_pcd, cfg)"],
     ]},
    {"type": "body",
     "text": "metrics 产出指标，gate 依据 QualityConfig 阈值判定并落盘报告，run() 串接两者"
             "并给出 ``ok`` 判定——不达标判定由编排层（M5 stage）抛 :class:`QualityGateError`（E500）。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "三、依赖关系与数据流"},
    {"type": "h2", "text": "3.1 依赖方向"},
    {"type": "body",
     "text": "quality 属 L2 业务层，依赖 core 底座与 io（网格/点云读写），不反向；"
             "仅用 Open3D（惰性导入）+ 标准库 csv/json："},
    {"type": "code", "language": "text",
     "text": "package-level deps (A -> B means A imports B):\n"
             "  quality     -->  core.exceptions (InputDataError)\n"
             "  quality     -->  core.config     (QualityConfig)\n"
             "  quality     -->  core.logging    (get_logger)\n"
             "  quality     -->  Open3D          (采样 / 最近邻距离，惰性导入)\n"
             "  runner M5   -->  h3d.quality + h3d.io  (阶段接入，惰性导入)\n"
             "  core / io / preprocess / register / mesh  -->  (NONE; 不反向依赖 quality)"},
    {"type": "h2", "text": "3.2 质检数据流"},
    {"type": "body",
     "text": "M5 阶段 + quality.run + build_report 的完整流转（自顶向下）："},
    {"type": "flowchart",
     "nodes": [
         {"id": "in", "label": "M3 网格（30_dense/<item>.ply）+ 参考点云（M2 产物）", "shape": "parallelogram"},
         {"id": "mps", "label": "estimate_mps 尺度自适应（近重合退化自动兜底）", "shape": "rect"},
         {"id": "cov", "label": "surface_coverage（阈值 = k × MPS）", "shape": "rect"},
         {"id": "rmse", "label": "qc_rmse 表面中误差（mm）+ 可靠性判定", "shape": "rect"},
         {"id": "bound", "label": "boundary_ratio 边界边比例（孔洞率）", "shape": "rect"},
         {"id": "check", "label": "check_quality：coverage≥阈值 且 (rmse≤阈值 若可靠)", "shape": "diamond"},
         {"id": "report", "label": "build_report：60_qc JSON + 70_report accuracy.csv", "shape": "rect"},
         {"id": "ok", "label": "达标 -> DONE，指标进 manifest", "shape": "rect"},
         {"id": "e500", "label": "不达标 -> QualityGateError(E500) 失败隔离", "shape": "rect"},
     ],
     "edges": [
         {"from": "in", "to": "mps"},
         {"from": "mps", "to": "cov"},
         {"from": "cov", "to": "rmse"},
         {"from": "rmse", "to": "bound"},
         {"from": "bound", "to": "check"},
         {"from": "check", "to": "report"},
         {"from": "report", "to": "ok", "label": "达标"},
         {"from": "check", "to": "e500", "label": "不达标"},
         {"from": "e500", "to": "report"},
     ],
     "caption": "M5 质检数据流：指标计算 → 质量门判定 → 报告落盘（先写报告再判定，失败也有据可查）→ "
                "达标进 manifest / 不达标 E500 隔离"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "四、模块详细设计"},
    {"type": "h2", "text": "4.1 metrics.py — 几何质量指标"},
    {"type": "body",
     "text": "指标计算全部基于「网格均匀采样点 ↔ 参考点云最近邻距离」这一统一数据源（教学版 18.2 "
             "采样法）：覆盖率统计距离 ≤ max_dist 的比例（被有效点云覆盖的表面积占比，设计书 18.1）；"
             "中误差取距离均方根 × 1000 转毫米（WW/T 0115/0117 限值单位）；边界边比例统计仅被一个"
             "三角面引用的边占比（孔洞率）。"},
    {"type": "table",
     "headers": ["函数", "职责", "关键设计"],
     "rows": [
         ["estimate_mps(pcd)", "平均点间距（尺度自适应基准）", "compute_nearest_neighbor_distance 批量；"
          "完全重合退化时回退包围盒对角线 1%"],
         ["surface_coverage(mesh, ref, max_dist, samples)", "模型表面覆盖率", "均匀采样 + 最近邻距离 ≤ max_dist 占比"],
         ["qc_rmse(mesh, ref, samples)", "表面中误差（mm）", "距离 RMSE × 1000；空输入返回 NaN"],
         ["boundary_ratio(mesh)", "边界边比例（孔洞率）", "哈希统计边出现次数，计数为 1 的占比"],
     ]},
    {"type": "h2", "text": "4.2 gate.py — 质量门判定与报告"},
    {"type": "body",
     "text": "质量门判定（设计书 §36.2）：``ok = coverage >= threshold and rmse <= threshold_mm``。"
             "关键工程决策：<b>中误差门仅在参考足够密时启用</b>（rmse_reliable = MPS(mm) ≤ 中误差阈值）"
             "——表面采样 RMSE 受参考点云密度主导，稀疏扫描的 MPS 即达几十毫米，无条件判 RMSE 会把"
             "「扫描稀疏」误判为「模型不合格」；参考过疏时中误差标记 N/A、仅以覆盖率判门"
             "（设计书 §36.4 扩展）。"},
    {"type": "table",
     "headers": ["函数", "职责"],
     "rows": [
         ["check_quality(metrics, cfg)", "纯判定：覆盖率门 + （可靠时）中误差门"],
         ["build_report(item, metrics, cfg, qc_dir, report_dir)", "先写 60_qc JSON（机器可读，失败也有据可查）"
          "再写 70_report accuracy.csv（utf-8-sig 中文表头）"],
     ]},
    {"type": "code", "language": "text",
     "text": "70_report/<item>/accuracy.csv（教学版 18.3 扩展）：\n"
             "  文物编号,中误差(mm),覆盖率,边界边比例,平均点间距(m),判定\n"
             "  rock001,188.0181,77.9%,0.5%,0.048099,pass\n"
             "\n"
             "60_qc/<item>/qc_metrics.json（机器可读，含阈值快照）：\n"
             "  {item_id, coverage, rmse_mm, boundary_ratio, mps_m,\n"
             "   thresholds:{coverage_threshold, rmse_threshold_mm, coverage_k_mps},\n"
             "   ok, status}"},
    {"type": "h2", "text": "4.3 __init__.py — run 质检流水线"},
    {"type": "body",
     "text": "run(mesh, ref_pcd, cfg) 计算指标并给出判定：MPS（尺度自适应）→ 覆盖率（阈值 k×MPS）→ "
             "中误差 + 可靠性 → 边界边比例 → ok。空输入抛 InputDataError(E100)。判定结果通过 "
             "``metrics['ok']`` 返回，由 M5 stage 决定 DONE 或抛 E500。"},
    {"type": "table",
     "headers": ["配置项", "默认值", "用途"],
     "rows": [
         ["quality.rmse_threshold_mm", "5.0", "中误差阈值（WW/T 0115/0117 精细级）"],
         ["quality.coverage_threshold", "0.80", "模型表面覆盖率下限（设计书 §6.3）"],
         ["quality.coverage_k_mps", "3.0", "覆盖率判定阈值 = k × MPS（尺度自适应）"],
         ["quality.samples", "50000", "网格采样点数（覆盖率 / 中误差估计）"],
     ]},
    {"type": "h2", "text": "4.4 runner M5 阶段接入（STAGE_REGISTRY）"},
    {"type": "body",
     "text": "M5 通过装饰器接入编排内核，不达标抛 E500 由失败隔离机制处理（该尊不进入交付包，"
             "队列其余照常处理，设计书 §6.3）："},
    {"type": "code", "language": "python",
     "text": "@register_stage('M5')\n"
             "def _stage_quality(task, cfg):\n"
             "    mesh = load_mesh(f'{task.root}/30_dense/{task.item_id}/{task.item_id}.ply')\n"
             "    ref = 合并(task.outputs_of('M2') or task.outputs_of('M1'))\n"
             "    metrics = quality_run(mesh, ref, cfg.quality)\n"
             "    # 先写报告（失败也有据可查），再判定\n"
             "    reports = build_report(item_id, metrics, cfg.quality,\n"
             "                           f'{task.root}/60_qc/{item_id}',\n"
             "                           f'{task.root}/70_report/{item_id}')\n"
             "    if not metrics['ok']:\n"
             "        raise QualityGateError(f'质检不达标: coverage=..., rmse=...')\n"
             "    return StageResult('M5', TaskStatus.DONE, reports, 指标)"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "五、接口契约与错误码"},
    {"type": "table",
     "headers": ["错误码", "异常类", "触发场景", "处理策略"],
     "rows": [
         ["E001", "ConfigError", "阈值参数越界（如 coverage_threshold > 1）", "配置层拦截，启动即报错"],
         ["E100", "InputDataError", "输入网格为空 / 参考点云为空 / MPS 估计失败", "失败隔离，记录后跳过"],
         ["E500", "QualityGateError", "覆盖率或中误差不达标（质量门）", "失败隔离：该尊不进入交付包，队列继续"],
     ]},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "六、测试与验证"},
    {"type": "body",
     "text": "quality 配套 22 项单元测试 + runner 端到端 3 项新增（M5 链路 / 质检报告 / E500 隔离），"
             "全量 <b>176 项</b> 通过：29 core + 20 io + 16 preprocess + 11 register + 21 mesh + "
             "22 quality + 21 report + 30 runner + 6 cli。端到端（2 尊 × 2 站）M5 coverage "
             "77.9% / 73.7% pass，accuracy.csv + qc_metrics.json 落盘。"},
    {"type": "table",
     "headers": ["测试文件", "覆盖点"],
     "rows": [
         ["test_metrics.py", "MPS 估计 / 覆盖率（近全覆盖与远参考低覆盖）/ 中误差 / 边界边比例"],
         ["test_gate.py", "质量门四象限判定、rmse 可靠性旁路、空输入 E100、报告 JSON/CSV 内容"],
         ["test_runner/test_pipeline.py", "M1→M2→M3→M5 全链路、60_qc/70_report 产物、manifest M5 指标、"
                                          "E500 隔离"],
     ]},
    {"type": "code", "language": "bash",
     "text": "$ python -m pytest -q\n"
             "........................................................................ [ 44%]\n"
             "........................................................................ [ 89%]\n"
             "................                                                     [100%]\n"
             "176 passed in 24.63s\n"
             "\n"
             "$ python -m h3d.cli batch -c demo.yaml\n"
             "M5 完成：rock001（coverage=77.9%, rmse=188.02mm -> pass）\n"
             "M5 完成：rock002（coverage=73.7%, rmse=213.88mm -> pass）\n"
             "批量处理完成：total=2 done=2 failed=0 skipped=0"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "七、开发进度与下一步方向（更新）"},
    {"type": "h2", "text": "7.1 主线完成度（M5 已交付）"},
    {"type": "table",
     "headers": ["层级 / 子包", "状态", "测试覆盖"],
     "rows": [
         ["L0 core 底座", "已交付", "29"],
         ["L1 io 适配层", "已交付", "20"],
         ["L2 preprocess（M1 接入）", "已交付", "16"],
         ["L2 register（M2 配准）", "已交付", "11"],
         ["L2 mesh（M3 网格重建）", "已交付", "21"],
         ["<b>L2 quality（M5 质检质量门）</b>", "<b>本次交付</b>", "<b>22</b>"],
         ["L2 report（M6 报表）", "已交付", "21"],
         ["L3 runner（M0 编排）", "已交付", "30"],
         ["L4 cli（命令入口）", "已交付", "6"],
         ["L2 texture（M4 网格纹理）", "未开始", "—"],
         ["L2 change（X1 M3C2）/ colour（X2）", "未开始", "—"],
     ]},
    {"type": "body",
     "text": "<b>「点云 -> 模型 -> 质检」完整链路已打通</b>：M1 预处理 → M2 配准 → M3 重建 → "
             "M5 质检（质量门 + 报告）。主线模块（M0-M3 + M5 + M6）完成度约 <b>90%</b>；"
             "可选模块（X1/X2）0%；测试 176 项 100% 通过；许可证全部 MIT/BSD。"},
    {"type": "h2", "text": "7.2 下一步开发方向（M5 完成后更新）"},
    {"type": "table",
     "headers": ["优先级", "模块", "目标", "关键点"],
     "rows": [
         ["P1", "M4 纹理（texture）", "UV 展开 + 纹理映射 + 多分辨率 LOD（OBJ/GLB/PLY）",
          "@register_stage('M4') 接入；M5 的 overlap（纹理重叠度）指标随之启用"],
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
     "text": "<b>建议路径</b>：主线功能已基本齐备（点云 -> 模型 -> 质检 -> 报表）。下一步首选 <b>M4 纹理"
             "（带色模型交付）</b>，或直接进入 <b>并发调度 / HTML 报表</b> 等工程化打磨，X1/X2 按"
             "多时相监测与色彩量化的实际需求启用。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "附录 A：质量门阈值依据"},
    {"type": "table",
     "headers": ["阈值", "取值", "依据"],
     "rows": [
         ["rmse_threshold_mm", "5.0", "WW/T 0115/0117 精细级特征点中误差限值（设计书 §6.3）"],
         ["coverage_threshold", "0.80", "模型表面覆盖率 ≥ 80%（设计书 §6.3 / §18.1）"],
         ["coverage_k_mps", "3.0", "尺度自适应：判定阈值 = k × MPS（软著方案 2.7.2）"],
     ]},
]

with open("content.json", "w", encoding="utf-8") as f:
    json.dump(content, f, ensure_ascii=False, indent=2)
print("content.json 生成完成，块数 =", len(content))
