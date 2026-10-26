# -*- mode: python ; coding: utf-8 -*-
# H3D 中小型文物三维建模自动化系统 —— PyInstaller 打包配置
#
# 用法：pyinstaller h3d_gui.spec
# 产出：dist/H3D/H3D.exe（onedir，含 GUI + --batch 子进程管线模式）
#
# 设计要点：
#   - 入口 launcher.py：GUI 模式 + --batch <config>（打包后子进程管线）
#   - 排除：tests / pytest / cupy / open3d（管线子进程在打包环境由
#     exe --batch 执行，cupy/open3d 随 exe 收集；GUI 自身不需要 cupy）
#   - 图标：assets/h3d_icon_v3.ico/.png（两件器物插画，2026-10-18 随
#     项目转向中小型文物更换）。运行期窗口图标同样用这份 PNG，由
#     h3d.gui.resources 在 _MEIPASS/assets 下查找。
#   - 收集 h3d 包 + 图标资源

# 2026-10-23：PE 版本资源**在打包时生成**，杜绝与 __version__ 漂移。
# 实测踩过：__version__ 与 pyproject 已是 1.1.4，命令行 --version 也输出
# 1.1.4，但 packaging/version_info.txt 手抄的还停在 1.1.3.0 → 打出的包在
# 右键属性页显示 1.1.3，与自证输出矛盾。现在这里强制重算一次。
import importlib.util as _ilu
import os as _os

_HERE = _os.path.dirname(_os.path.abspath(SPEC))
_spec_path = _os.path.join(_HERE, 'scripts', 'make_version_info.py')
_ms = _ilu.spec_from_file_location('_h3d_mvi', _spec_path)
_mvi = _ilu.module_from_spec(_ms)
_ms.loader.exec_module(_mvi)
_H3D_VER = _mvi.build()
print(f'[h3d.spec] PE 版本资源已刷新：{_H3D_VER}')

a = Analysis(
    ['src/h3d/gui/launcher.py'],
    pathex=['src'],
    binaries=[],
    datas=[('assets/h3d_icon_v3.ico', 'assets'), ('assets/h3d_icon_v3.png', 'assets')],
    hiddenimports=[
        'h3d.cli', 'h3d.gui.app', 'h3d.gui.wizard', 'h3d.gui.welcome',
        'h3d.gui.history', 'h3d.gui.config_edit', 'h3d.gui.runner',
        'h3d.gui.logparse', 'h3d.gui.preview', 'h3d.gui.export',
        'h3d.gui.advanced', 'h3d.gui.demo', 'h3d.gui.report_view',
        'h3d.gui.viewer3d', 'h3d.gui.launcher', 'h3d.gui.resources',
        'h3d.gui.items',
        'h3d.sfm_solve', 'h3d.runner', 'h3d.runner.batch_processor',
        'h3d.runner.task', 'h3d.io', 'h3d.core', 'h3d.core.config',
        'h3d.core.scale', 'h3d.core.media',
        # r6.2：数据根单实例锁。run() 里是**函数内延迟 import**
        # （from h3d.core.lockfile import acquire_run_lock），PyInstaller 静态
        # 分析抓不到；不显式声明则打包版调用时 ModuleNotFoundError，表现是
        # 「一开始跑就崩」而不是锁生效。
        'h3d.core.lockfile',
        # 2026-10-18：带纹理 GLB 必须由 trimesh 写（Open3D 会静默丢弃纹理与 UV）。
        # trimesh 由 h3d.texture.uv 内部延迟导入，PyInstaller 静态分析抓不到。
        'trimesh', 'trimesh.exchange.gltf', 'trimesh.visual.material',
        'trimesh.visual.texture',
        # r7.1：几何清理/法线朝向在 M1 里是**函数内延迟 import**
        # （from h3d.preprocess.background import remove_background），静态分析
        # 抓不到 → 打包版会在 M1 阶段 ModuleNotFoundError。此坑与
        # h3d.core.lockfile 同类，新增模块必须同步登记。
        # r7.2：启动自检。h3d.health 的 boot/project 子模块在
        # run_preflight 内部**函数级 import**（避免 import h3d.health 时就有
        # 依赖），PyInstaller 抓不到 —— 不登记则打包版一启动就少跑检查项
        # （自检"全绿"但什么都没查，比报错更危险）。
        'h3d.health', 'h3d.health._boot', 'h3d.health._project',
        'psutil',
        'h3d.preprocess', 'h3d.preprocess.background',
        'h3d.preprocess.normal_estimate', 'h3d.mesh',
        'h3d.photo_texture', 'h3d.quality', 'h3d.publish',
        'h3d.thesis_tables', 'h3d.ctrl_points', 'h3d.ctrl_auto',
        'h3d.geo_align', 'h3d.change', 'h3d.novel_calib',
        'h3d.subset_model', 'h3d.incremental_depth', 'h3d.colour',
        'h3d.texture', 'h3d.report', 'h3d.report.html_export',
        'h3d.report.plot_gen', 'matplotlib.backends.backend_qtagg',
        'matplotlib.backends.backend_agg', 'sklearn.neighbors',
        'sklearn.utils', 'yaml', 'jinja2', 'pypdf', 'PIL',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tests', 'pytest', 'test_gui', 'PySide6.QtWebEngineCore',
              'PySide6.QtWebEngineWidgets', 'PySide6.QtNetwork',
              'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtMultimedia',
              'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtXml',
              'cupy', 'cupyx', 'IPython', 'jupyter', 'py4dgeo',
              'setuptools', 'wheel'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='H3D',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # GUI 无控制台
    disable_windowed_traceback=True,   # 实机修复：异常不再弹 PyInstaller 崩溃框
                                       # （launcher 已写日志 + 友好提示）
    icon='assets/h3d_icon_v3.ico',
    # 2026-10-18：写入 PE 版本资源，属性页可看到 1.1.0（此前为空，
    # 用户无法判断手上是哪个版本）。
    version='packaging/version_info.txt',
)
