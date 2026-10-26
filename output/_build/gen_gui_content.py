# -*- coding: utf-8 -*-
"""生成「GUI P1 核心版开发」content.json。"""
import json

content = [
    {"type": "h1", "text": "一、开发总览"},
    {"type": "body",
     "text": "本批完成 <b>GUI P1 核心版三项开发</b>（依据 GUI.docx 需求规格：欢迎页/三步向导/主界面/一键"
             "生成/进度监控/结果预览/一键导出）。<b>① GUI 主界面</b>：PySide6 + 新建项目三步向导（场景选择"
             "→ 数据拖拽导入 → 参数确认）+ 功能配置区（几何/纹理/质检/高级折叠预留）+ 一键生成按钮；"
             "<b>② 进度可视化</b>：总进度条 + 阶段状态灯（S1/M1~M6/X1/X2 九灯，等待/运行/完成/失败四态）"
             "+ 实时日志滚动（后台子进程解析）；<b>③ 结果浏览</b>：3D 模型预览（matplotlib 内嵌，网格/"
             "点云）+ 纹理查看（diffuse 贴图）+ 一键导出交付包（zip 含模型/报告/论文表/manifest 摘要）。"
             "技术路线：PySide6 6.11.2 前端 + h3d.cli batch 后台子进程（复用核心管线零重复开发）+ "
             "logparse 纯 Python 日志解析。全量 <b>348 项测试通过</b>（+11 GUI 逻辑层）；GUI 冒烟通过"
             "（offscreen：向导/主窗口/状态灯/日志/预览）。"},
    {"type": "callout",
     "text": "<b>结论</b>：GUI P1 核心版落地。全程零命令行（向导+拖拽+按钮）；一键生成走真实管线（S1→M6"
             "+X1/X2）；进度/状态/日志全可视化；交付包一键导出。启动：<b>python -m h3d.cli gui</b>。"},

    {"type": "h1", "text": "二、① GUI 主界面（PySide6 + 向导 + 配置编辑）"},
    {"type": "h2", "text": "2.1 模块架构（src/h3d/gui/）"},
    {"type": "table",
     "headers": ["模块", "职责"],
     "rows": [
         ["app.py", "主窗口（配置区/预览区/进度区）+ run_gui 入口"],
         ["wizard.py", "三步向导：场景选择（①激光+照片/②纯照片）→ 拖拽导入（激光/照片/控制点）→ 参数确认"],
         ["config_edit.py", "ProjectConfig：目录布局构建（00_raw/00_photo/00_ctrl）+ config.yaml 生成（场景①/②模板）"],
         ["runner.py", "BatchRunner QThread：子进程调度 + 进度/阶段/日志信号 + 取消"],
         ["logparse.py", "CLI 输出解析（纯 Python）：阶段完成/失败/总量正则"],
         ["preview.py", "MeshPreview（matplotlib 3D 内嵌）+ TextureView（diffuse 显示）"],
         ["export.py", "交付包 zip 导出（模型/报告/论文表/manifest 摘要）"],
     ]},
    {"type": "h2", "text": "2.2 交互流程（需求全闭环）"},
    {"type": "body",
     "text": "启动 python -m h3d.cli gui → 向导（场景二选一默认①；拖拽 00_raw/00_photo/00_ctrl；极简参数"
             "：纹理分辨率 1024/2048/4096、输出 obj/glb/ply）→「创建并开始重建」→ 主界面（左侧配置折叠"
             "预留、右侧 3D 预览+纹理）→ 一键生成 → 进度监控 → 结果预览 → 一键导出交付包。全部无命令行。"},

    {"type": "h1", "text": "三、② 进度可视化（进度条 + 状态灯 + 实时日志）"},
    {"type": "h2", "text": "3.1 实现"},
    {"type": "code", "language": "text",
     "text": "BatchRunner（QThread）：subprocess 运行 h3d.cli batch\n"
             "  stdout 逐行 -> log_line 信号（实时日志滚动）\n"
             "  logparse 解析：阶段完成 -> stage_updated('M3','done') 灯变绿\n"
             "    失败隔离 -> 'failed' 灯变红\n"
             "  总进度 = 阶段完成数 / 9（S1~X2），完成 100%\n"
             "  cancel()：terminate 子进程（暂停/取消按钮）"},
    {"type": "h2", "text": "3.2 验证"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["阶段完成解析（M3 完成 → done）", "✅ logparse 单测"],
         ["失败隔离解析（M5 → failed）", "✅ logparse 单测"],
         ["总量解析（done=1 failed=0 → 成功）", "✅ logparse 单测"],
         ["GUI 冒烟（9 灯 + 进度 + 日志）", "✅ offscreen 启动"],
     ]},

    {"type": "h1", "text": "四、③ 结果浏览（3D 预览 + 纹理 + 导出）"},
    {"type": "h2", "text": "4.1 实现"},
    {"type": "code", "language": "text",
     "text": "MeshPreview：matplotlib FigureCanvasQTAgg 3D\n"
             "  load_mesh 支持 PLY/OBJ（open3d 读取，网格/点云自适应）\n"
             "  plot_trisurf / scatter；可旋转缩放平移\n"
             "TextureView：diffuse PNG 显示（KeepAspectRatio 缩放）\n"
             "export_delivery_package：80_export/<item> + accuracy.csv\n"
             "  + thesis 表 + delivery_manifest + manifest 指标摘要 -> zip"},
    {"type": "h2", "text": "4.2 验证"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["交付包导出（含模型文件）", "✅ 单测（n_files>=1, zip 落盘）"],
         ["空交付物返回空", "✅ 单测"],
         ["GUI 冒烟（预览组件实例化）", "✅ offscreen"],
     ]},

    {"type": "h1", "text": "五、验证与风险"},
    {"type": "table",
     "headers": ["项", "状态"],
     "rows": [
         ["全量回归", "348 项通过（+11 GUI 逻辑层）"],
         ["GUI 冒烟", "向导/主窗口/9 灯/进度/日志/预览 offscreen 通过"],
         ["PySide6 依赖", "6.11.2（pyproject 需固化）"],
         ["风险（如实）", "matplotlib 3D 非真交互渲染（GUI 阶段后可选 open3d 可视化窗口）；大纹理显示内存；子进程输出解析依赖日志格式稳定"],
     ]},

    {"type": "h1", "text": "六、下一步（GUI 增强与软件收尾）"},
    {"type": "table",
     "headers": ["优先级", "方向", "内容"],
     "rows": [
         ["P1", "高级参数折叠面板", "降采样/网格精度/融合权重/泊松开关/CUDA 选择（主界面已预留）"],
         ["P1", "历史项目记录与快速打开", "项目文件 .h3d 序列化 + 最近列表"],
         ["P1", "内置示例数据一键演示", "打包 demo 数据（photo_e2e 素材）"],
         ["P2", "质检报告可视化", "Jinja2 HTML 报表内嵌预览（浏览器控件）"],
         ["P2", "软件收尾", "PyInstaller 打包 + 图标/品牌 + requirements 固化（PySide6/cupy-cuda12x）"],
         ["P3", "open3d 原生交互窗口", "真 3D 交互（旋转/着色模式/视角切换）替换 matplotlib"],
         ["P3", "X1 形变热力图预览", "多时相可视化（P3 特色）"],
     ]},
    {"type": "callout",
     "text": "<b>启动方式</b>：python -m h3d.cli gui（或打包后 exe）。<b>收尾建议</b>：requirements 固化 "
             "PySide6>=6.11 与 cupy-cuda12x；PyInstaller 打包时排除 PySide6 测试文件、收集 h3d 包；示例"
             "数据打包后即可一键演示全流程。"},
]

with open("content.json", "w", encoding="utf-8") as f:
    json.dump(content, f, ensure_ascii=False, indent=2)
print("content.json 生成完成，块数 =", len(content))
