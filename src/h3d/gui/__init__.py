"""h3d.gui : H3D 中小型文物三维建模自动化系统 GUI（P1 核心版）。

架构：PySide6 前端编排 + h3d.cli batch 后台子进程调度（复用核心管线，
零重复开发）。模块：
  wizard.py      新建项目三步向导（场景选择/数据拖拽导入/参数确认）
  config_edit.py 配置编辑（表单 <-> config.yaml）
  runner.py      后台运行线程（子进程 + 进度/阶段/日志信号）
  preview.py     3D 预览（matplotlib 内嵌）+ 纹理查看
  export.py      交付包一键导出
  app.py         主窗口 + 入口
"""

__all__ = []
