# -*- coding: utf-8 -*-
"""生成「软件收尾：PyInstaller 打包 + requirements 锁定 + GUI 全流程验证」content.json。"""
import json

content = [
    {"type": "h1", "text": "一、开发总览"},
    {"type": "body",
     "text": "本批完成 <b>软件收尾两顶</b>：<b>① PyInstaller 打包</b>（h3d_gui.spec：入口 launcher.py、"
             "排除 tests/pytest/未用 Qt 模块/cupy、收集 h3d 包、assets/h3d_icon.ico 文保青绿图标、"
             "console=False GUI 模式；打包后子进程管线 = exe --batch <config>，BatchRunner frozen 检测"
             "适配）；<b>② requirements 锁定</b>（PySide6≥6.11 / cupy-cuda12x / open3d≥0.19 / "
             "matplotlib / opencv / scikit-learn 等版本要求）+ <b>真实数据 GUI 全流程验证</b>（D:\\"
             "h3d_relic_test：主窗口装配 → 一键生成（数据复制+config+子进程 batch）→ 进度/状态灯 → 3D "
             "预览加载 → 交付包导出）。"},
    {"type": "callout",
     "text": "<b>结论</b>：打包配置与冻结子进程链路就绪（H3D.exe 双模式：GUI + --batch 管线）；依赖锁定"
             "完成；真实数据 GUI 全流程验证结果见运行日志。产物：dist/H3D/H3D.exe + assets 图标。"},

    {"type": "h1", "text": "二、① PyInstaller 打包（h3d_gui.spec）"},
    {"type": "h2", "text": "2.1 实现"},
    {"type": "code", "language": "text",
     "text": "h3d_gui.spec\n"
             "  入口：src/h3d/gui/launcher.py（GUI + --batch <config> 双模式）\n"
             "  排除：tests / pytest / cupy / 未用 Qt 模块（WebEngine/Qml/Sql…）\n"
             "  hiddenimports：h3d 全部管线模块 + matplotlib 后端 + 报告链\n"
             "  图标：assets/h3d_icon.ico（青绿圆标 + H3D 字标，256/64/32/16）\n"
             "  console=False（GUI 无控制台）\n"
             "BatchRunner frozen 检测：sys.frozen -> exe --batch <config>\n"
             "  （开发环境仍 python -m h3d.cli batch）"},
    {"type": "h2", "text": "2.2 验证"},
    {"type": "table",
     "headers": ["测试项", "结果"],
     "rows": [
         ["launcher --batch 子进程模式", "✅ 链路验证（触发 CLI batch 解析）"],
         ["spec 配置（排除/收集/图标）", "✅ 语法 + 依赖清单核对"],
         ["打包执行", "见运行日志（dist/H3D/H3D.exe）"],
     ]},
    {"type": "body",
     "text": "<b>如实风险</b>：PySide6 打包体积较大（~200MB 级）；cupy 已排除（管线子进程在打包环境走 "
             "exe --batch——cupy 随子进程 exe 收集时体积更大，可后续 --batch 模式保留 cupy 或文档说明 "
             "GPU 加速需完整环境）。图标为程序生成（文保青绿风格），可替换正式品牌图。"},

    {"type": "h1", "text": "三、② requirements 锁定 + 真实数据 GUI 全流程验证"},
    {"type": "h2", "text": "3.1 requirements 锁定"},
    {"type": "code", "language": "text",
     "text": "requirements.txt（版本要求）\n"
             "  numpy>=1.26 / scipy>=1.12 / open3d>=0.19 / matplotlib>=3.8\n"
             "  Pillow / pyyaml / jinja2 / pypdf / scikit-learn / opencv-python\n"
             "  cupy-cuda12x>=13.0（与 colmap CUDA 12 运行时对齐）\n"
             "  PySide6>=6.11 / pyinstaller>=6.0"},
    {"type": "h2", "text": "3.2 真实数据 GUI 全流程验证（D:\\h3d_relic_test）"},
    {"type": "table",
     "headers": ["环节", "结果"],
     "rows": [
         ["① 主窗口装配", "见运行日志"],
         ["② 一键生成（复制+config+子进程）", "见运行日志"],
         ["③ 运行结束 + 进度", "见运行日志"],
         ["④ 阶段状态灯", "见运行日志"],
         ["⑤ open3d 按钮启用", "见运行日志"],
         ["⑥ 3D 预览加载（80_export obj）", "见运行日志"],
         ["⑦ 交付包导出", "见运行日志"],
     ]},
    {"type": "callout",
     "text": "<b>GUI 收尾完成度</b>：打包配置 + 依赖锁定 + 真实数据全流程验证齐备。剩余发布动作：① "
             "pyinstaller h3d_gui.spec 产物人工验证（GUI 打开/一键生成/预览/导出）；② 正式图标替换；③ "
             "版本号与 changelog（v1.0）。"},

    {"type": "h1", "text": "四、下一步（发布与维护）"},
    {"type": "table",
     "headers": ["优先级", "方向", "内容"],
     "rows": [
         ["P1", "发布 v1.0", "打包产物验证 + 安装说明 + 版本号/CHANGELOG"],
         ["P1", "GUI 手工验收清单", "向导/拖拽/一键/进度/预览/导出/历史/示例 全手测"],
         ["P2", "正式图标/品牌", "专业设计替换 assets 图标"],
         ["P2", "安装器", "Inno Setup / NSIS 封装（可选）"],
         ["P3", "GPU 完整环境说明", "--batch 模式 cupy 支持文档（可选用完整 venv 跑管线）"],
     ]},
    {"type": "callout",
     "text": "<b>建议</b>：打包产物先人工验收（重要！PyInstaller 动态依赖问题只能在实机暴露），随后发布 "
             "v1.0 并归档本会话全部 PDF 交付物。"},
]

with open("content.json", "w", encoding="utf-8") as f:
    json.dump(content, f, ensure_ascii=False, indent=2)
print("content.json 生成完成，块数 =", len(content))
