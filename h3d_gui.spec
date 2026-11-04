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

# 2026-11-04：`h3d.*` 改为**自动收集**，不再手写清单。
# 手写清单已漂过两次：先是 h3d.core.lockfile / h3d.health（函数内延迟 import
# 抓不到），这次是当天新增的 h3d.delivery_verify、以及整批 h3d.anomaly.*
# （X3 阶段）和 h3d.archive.*——打包版跑到 X3 就会 ModuleNotFoundError。
# 第三方包没法按包名枚举，仍手写（见下面的清单）。
import sys as _sys
_sys.path.insert(0, _os.path.join(_HERE, 'src'))
from PyInstaller.utils.hooks import collect_submodules  # noqa: E402
_H3D_MODULES = collect_submodules('h3d')
print(f'[h3d.spec] h3d 子模块自动收集：{len(_H3D_MODULES)} 个')

a = Analysis(
    ['src/h3d/gui/launcher.py'],
    pathex=['src'],
    binaries=[],
    datas=[('assets/h3d_icon_v3.ico', 'assets'), ('assets/h3d_icon_v3.png', 'assets')],
    hiddenimports=[
        # 本项目模块由 collect_submodules('h3d') 自动收集（见文件头）
        *_H3D_MODULES,
        # 下面只剩**第三方**：没法按包名枚举，必须手写。
        # 这些都是「函数内延迟 import / 运行时才用到」的包，PyInstaller 的静态
        # 分析抓不到，漏了就是打包版报 ModuleNotFoundError。
        #
        # 历史上真正漏过的（现在已由 collect_submodules 覆盖，留作教训）：
        #   h3d.core.lockfile  数据根单实例锁 → 「一开始跑就崩」
        #   h3d.health.*       启动自检 → 少跑检查项，「自检全绿但什么都没查」
        #   h3d.preprocess.background  M1 几何清理 → M1 阶段崩
        #   h3d.anomaly.*      X3 阶段 → 打包版跑到 X3 才崩（2026-11-04 发现）
        #   h3d.delivery_verify  核验按钮 → 「点了没反应」
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
        'psutil',
        'matplotlib.backends.backend_qtagg',
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
