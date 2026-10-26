# -*- coding: utf-8 -*-
"""生成「GUI 增强五顶：高级面板/历史/示例/报告可视化/open3d+X1 热力图」content.json。"""
import json

content = [
    {"type": "h1", "text": "一、开发总览"},
    {"type": "body",
     "text": "本批完成 <b>GUI 增强五顶开发</b>：<b>① 高级参数折叠面板</b>（点云/网格/纹理/融合/质量门，"
             "QGroupBox 折叠，主界面集成）；<b>② 历史项目记录</b>（~/.workbuddy/h3d_history.json 最近"
             "12 项 + 欢迎页最近列表 + 快速打开/移除）；<b>③ 内置示例数据一键演示</b>（合成造像照片 12 张"
             "+ 合成激光点云，完全自包含，欢迎页一键运行）；<b>④ 质检报告可视化</b>（QTextBrowser 内嵌 "
             "report.html + 指标表达标/未达标着色 + 浏览器打开）；<b>⑤ open3d 原生交互窗口 + X1 形变"
             "热力图</b>（真 3D 旋转/着色模式 R/G/B/T，形变 jet 色映射红=形变大）。启动入口升级为"
             "欢迎页（新建向导/历史项目/示例演示）。全量 <b>355 项测试通过</b>（+7）；GUI 增强冒烟通过"
             "（offscreen：欢迎页/高级面板写回/open3d 按钮）。"},
    {"type": "callout",
     "text": "<b>结论</b>：GUI 能力覆盖 GUI.docx 全部 P1/P2/P3 需求：向导+拖拽、一键生成+进度/状态灯/"
             "实时日志、3D 预览+纹理、交付包导出、高级参数、历史项目、示例演示、报告可视化、open3d 真"
             "交互、X1 热力图。启动 python -m h3d.cli gui。"},

    {"type": "h1", "text": "二、① 高级参数折叠面板（P1）"},
    {"type": "h2", "text": "2.1 实现（src/h3d/gui/advanced.py）"},
    {"type": "table",
     "headers": ["分组", "参数"],
     "rows": [
         ["点云与网格", "降采样体素(0.005~0.5)/泊松深度(5~10)/密度过滤"],
         ["纹理与融合", "视角加权 top-k/每面采样点/泊松接缝开关/对齐模式（自动 ICP/控制点引导）/双源融合权重"],
         ["重建与质量门", "稠密质量门开关/几何质检开关"],
     ]},
    {"type": "h2", "text": "2.2 验证"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["面板写回 cfg（voxel 0.03）", "✅ 冒烟"],
         ["折叠交互", "✅ QGroupBox checkable"],
     ]},

    {"type": "h1", "text": "三、② 历史项目记录（P1）"},
    {"type": "h2", "text": "3.1 实现（src/h3d/gui/history.py + welcome.py）"},
    {"type": "code", "language": "text",
     "text": "HistoryStore：~/.workbuddy/h3d_history.json\n"
             "  add（置顶去重，上限 12）/ list / remove / find_config\n"
             "WelcomeWindow：最近项目列表（双击快速打开）+\n"
             "  新建向导 / 示例演示 / 移除选中"},
    {"type": "h2", "text": "3.2 验证"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["add 置顶去重 / remove", "✅ 单测"],
         ["find_config", "✅ 单测"],
         ["欢迎页历史列表", "✅ 冒烟"],
     ]},

    {"type": "h1", "text": "四、③ 内置示例数据一键演示（P1）"},
    {"type": "h2", "text": "4.1 实现（src/h3d/gui/demo.py）"},
    {"type": "code", "language": "text",
     "text": "create_demo_project(target)：\n"
             "  合成激光点云（球面 6000 点）+ 合成造像照片\n"
             "  （纹理球体 6 方位 × 2 高度 = 12 张 320×240）\n"
             "  场景①配置（voxel 0.05/poisson 7/纹理 1024）-> config.yaml\n"
             "  欢迎页「运行示例项目」一键生成并启动重建"},
    {"type": "h2", "text": "4.2 验证"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["demo 项目生成（照片 ≥8 + 点云 + config）", "✅ 单测"],
         ["一键演示入口", "✅ 欢迎页按钮"],
     ]},

    {"type": "h1", "text": "五、④ 质检报告可视化（P2）"},
    {"type": "h2", "text": "5.1 实现（src/h3d/gui/report_view.py）"},
    {"type": "code", "language": "text",
     "text": "ReportView：QTextBrowser 内嵌 report.html（无 WebEngine 重依赖）\n"
             "  + build_qc_table：qc_metrics.json 指标表\n"
             "  （覆盖率/中误差/边界比/纹理清晰度/纹理覆盖——✅达标/❌未达标着色）\n"
             "  + 「在浏览器打开完整报表」（webbrowser）"},
    {"type": "h2", "text": "5.2 验证"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["指标表构建（含达标判定）", "✅ 单测（覆盖率 90% → 达标）"],
         ["无质检产物容错", "✅ 单测"],
         ["HTML 内嵌", "✅ QTextBrowser"],
     ]},

    {"type": "h1", "text": "六、⑤ open3d 原生交互窗口 + X1 形变热力图（P3）"},
    {"type": "h2", "text": "6.1 实现（src/h3d/gui/viewer3d.py）"},
    {"type": "code", "language": "text",
     "text": "show_open3d_window(mesh, texture)：open3d VisualizerWithKeyCallback\n"
             "  真 3D 旋转/缩放/平移；按键 R/G/B 纯色、T 纹理\n"
             "show_x1_heatmap(mesh, root, item)：\n"
             "  读 X1 形变 -> 顶点 jet 色映射（红=形变大）-> 顶点色着色窗口\n"
             "  （无 X1 时用法线 z 分量曲率近似示意）\n"
             "主窗口「打开 open3d 交互窗口」「X1 形变热力图」按钮（完成后启用）"},
    {"type": "h2", "text": "6.2 验证"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["X1 结果读取（80_export JSON）", "✅ 单测"],
         ["无 X1 容错", "✅ 单测"],
         ["open3d 窗口启动", "独立窗口（GUI 冒烟验证组件；实际窗口需交互环境）"],
     ]},
    {"type": "body",
     "text": "如实说明：open3d 窗口为独立进程可视化（非 Qt 内嵌——Qt 内嵌需重编译 open3d，成本高）；"
             "offscreen 冒烟验证组件装配，真窗口需桌面会话。"},

    {"type": "h1", "text": "七、验证与风险"},
    {"type": "table",
     "headers": ["项", "状态"],
     "rows": [
         ["全量回归", "355 项通过（+7 GUI 增强）"],
         ["GUI 冒烟", "欢迎页/主窗口/高级面板写回/open3d 按钮 offscreen 通过"],
         ["风险（如实）", "open3d 独立窗口非内嵌；QTextBrowser 对复杂 HTML 样式支持有限（完整报表走浏览器）；demo 数据为合成（演示链路用）"],
     ]},

    {"type": "h1", "text": "八、下一步（软件收尾）"},
    {"type": "table",
     "headers": ["优先级", "方向", "内容"],
     "rows": [
         ["P1", "PyInstaller 打包", "h3d_gui.spec：排除测试、收集 h3d 包、单文件/目录 exe、图标品牌"],
         ["P1", "requirements 固化", "PySide6>=6.11 / cupy-cuda12x / open3d>=0.19 版本锁定"],
         ["P1", "GUI 实机数据工作流验证", "D:/h3d_relic_test 真实数据走 GUI（向导导入→一键生成→预览→导出）"],
         ["P2", "open3d Qt 内嵌（可选）", "重编译 open3d 或 WebRTC 内嵌——成本高，视论文演示需求"],
         ["P2", "多语言/主题", "浅色主题适配（当前深色按钮风格）"],
         ["P3", "错误报告自动收集", "运行失败一键导出日志包（排障）"],
     ]},
    {"type": "callout",
     "text": "<b>收尾建议</b>：① PyInstaller 打包（h3d_gui.spec）验证启动；② 真实数据 GUI 全流程走一遍"
             "（向导→一键→预览→导出）确认端到端；③ requirements 锁定后发布 v1.0。GUI 功能已覆盖需求"
             "文档全部 P1/P2/P3 项。"},
]

with open("content.json", "w", encoding="utf-8") as f:
    json.dump(content, f, ensure_ascii=False, indent=2)
print("content.json 生成完成，块数 =", len(content))
