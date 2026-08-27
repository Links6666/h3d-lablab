# -*- coding: utf-8 -*-
"""生成 L1 适配层（io 子包）代码说明书 content.json。"""
import json

content = [
    # ---------------------------------------------------------------------
    {"type": "h1", "text": "一、概述"},
    {"type": "body",
     "text": "本文档是《造像类文物三维建模自动化系统（路线 E）》<b>L1 适配层（io 子包）</b>的代码说明书。"
             "L1 适配层对应工程目录 <b>src/h3d/io</b>，职责是「把外部点云 / 网格格式读入为统一数据模型，"
             "并把统一数据模型写回外部格式」，同时完成格式与完整性校验。它是整条管线中<b>唯一</b>直接依赖"
             "Open3D / laspy 的位置，使业务层（preprocess / register / ...）完全不感知具体读写实现。"
             "本说明书严格遵循《软件开发项目设计书》第 10 章（五层架构 L1）、《软著方案》2.3.1（适配层"
             "抽象原则）、《管线骨架构建分析》第 4~5 章（模块划分与依赖约束）及《开发指南（教学版）》"
             "第 14 章的接口约定。"},
    {"type": "callout",
     "text": "设计铁律（《软著方案》2.3.1）：业务代码永远不直接 import open3d / laspy，只调用 io 层"
             "提供的抽象接口。这一「适配器模式」带来许可证隔离、可替换性、独创性论证与可测试性四重收益。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "二、模块划分"},
    {"type": "body", "text": "L1 适配层由 3 个文件构成，目录结构与职责如下："},
    {"type": "code", "language": "text",
     "text": "src/h3d/io/\n"
             "+-- __init__.py          # 包导出：统一暴露读写接口（__all__）\n"
             "+-- pointcloud_io.py    # 点云读写：ply / pcd / xyz / pts / las / laz\n"
             "+-- mesh_io.py          # 网格读写：obj / ply / stl / glb / gltf"},
    {"type": "table",
     "headers": ["模块文件", "核心职责", "对外关键符号"],
     "rows": [
         ["pointcloud_io.py", "点云读写与格式适配（Open3D + laspy）", "load_point_cloud, load_all, save_point_cloud, POINTCLOUD_READ_EXT, POINTCLOUD_WRITE_EXT"],
         ["mesh_io.py", "网格读写与格式适配（Open3D）", "load_mesh, save_mesh, MESH_READ_EXT, MESH_WRITE_EXT"],
         ["__init__.py", "包导出：统一暴露读写接口", "（聚合导出上述符号，供业务层按需导入）"],
     ]},
    {"type": "body",
     "text": "两个读写模块各司其职：<b>pointcloud_io</b> 只管点云（输入为统一 Open3D PointCloud），"
             "<b>mesh_io</b> 只管网格（输入为统一 Open3D TriangleMesh）。二者通过显式导入共享"
             "<b>h3d.core</b> 的异常与日志，互不交叉。支持格式按扩展名路由，见第四章。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "三、依赖关系与数据流"},
    {"type": "h2", "text": "3.1 依赖方向"},
    {"type": "body", "text": "依据《管线骨架构建分析》5.2，io 属 L1 适配层，仅依赖 core 底座（exceptions "
             "与 logging），且不反向（core 不依赖 io），业务层只依赖 io 不直接依赖第三方库："},
    {"type": "code", "language": "text",
     "text": "package-level deps (A -> B means A imports B):\n"
             "  preprocess / register / change / colour / runner / report  -->  io\n"
             "  io  -->  core.exceptions (InputDataError, ExportError)\n"
             "  io  -->  core.logging   (get_logger)\n"
             "  io  -->  Open3D / laspy (仅在读写函数内部惰性导入，适配层独占)\n"
             "  core  -->  (NONE; does not depend on io)"},
    {"type": "callout",
     "text": "Open3D / laspy 采用<b>函数内惰性导入</b>（_o3d() / _read_las 内 import），使 import h3d.io"
             "本身不触发重型三维库加载，业务层单元测试可对 io 层整体 mock，契合《软著方案》"
             "「适配层可 mock，业务逻辑单元测试不依赖重型三维库」的可测试性原则。"},
    {"type": "h2", "text": "3.2 点云读取数据流"},
    {"type": "body", "text": "单文件点云读取（load_point_cloud）的完整流转（自顶向下，非法取值经 "
             "InputDataError E100 分支中断）："},
    {"type": "flowchart",
     "nodes": [
         {"id": "path", "label": "输入路径 path", "shape": "parallelogram"},
         {"id": "exist", "label": "文件存在性检查", "shape": "rect"},
         {"id": "route", "label": "扩展名路由（ply/pcd/xyz/las/laz）", "shape": "rect"},
         {"id": "read", "label": "Open3D / laspy / XYZ 解析", "shape": "rect"},
         {"id": "check", "label": "空点云校验（0 点）", "shape": "rect"},
         {"id": "ret", "label": "返回统一 PointCloud", "shape": "parallelogram"},
     ],
     "edges": [
         {"from": "path", "to": "exist"},
         {"from": "exist", "to": "route"},
         {"from": "route", "to": "read"},
         {"from": "read", "to": "check"},
         {"from": "check", "to": "ret"},
     ],
     "caption": "load_point_cloud 数据流：缺失 / 不支持格式 / 空点云均抛 InputDataError(E100)"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "四、模块详细设计"},
    {"type": "h2", "text": "4.1 pointcloud_io.py — 点云读写"},
    {"type": "body",
     "text": "点云模块以<b>扩展名路由</b>为核心：读取时按后缀分发到 Open3D（ply / pcd）、文本解析（xyz / "
             "pts）或 laspy（las / laz），统一归一为 Open3D PointCloud；写出时仅允许 ply / pcd / xyz。"
             "所有错误统一转为带错误码的管线异常，不向业务层泄漏第三方库原生异常。"},
    {"type": "table",
     "headers": ["函数", "签名", "职责"],
     "rows": [
         ["load_point_cloud", "load_point_cloud(path) -> PointCloud", "读取单文件；缺失/不支持/空点云 → InputDataError(E100)"],
         ["load_all", "load_all(pattern) -> [(path, PointCloud), ...]", "glob 批量读取；无匹配 → InputDataError(E100)"],
         ["save_point_cloud", "save_point_cloud(pcd, path) -> None", "写回 ply/pcd/xyz；不支持/写失败 → ExportError(E601)"],
         ["_read_open3d / _read_xyz / _read_las", "内部", "按格式分发的底层读取实现"],
     ]},
    {"type": "code", "language": "python",
     "text": "def load_point_cloud(path: str):\n"
             "    path = str(path)\n"
             "    if not os.path.exists(path):\n"
             "        raise InputDataError(f\"点云文件不存在: {path}\")\n"
             "    ext = _ext(path)\n"
             "    if ext not in POINTCLOUD_READ_EXT:\n"
             "        raise InputDataError(f\"不支持的点云格式 {ext!r}: {path}\")\n"
             "    if ext in _LAS_EXT:\n"
             "        pcd = _read_las(path)      # laspy(BSD-2)\n"
             "    elif ext in _TEXT_XYZ_EXT:\n"
             "        pcd = _read_xyz(path)\n"
             "    else:\n"
             "        pcd = _read_open3d(path)  # Open3D(MIT)\n"
             "    if pcd is None or not pcd.has_points():\n"
             "        raise InputDataError(f\"空点云（0 点）: {path}\")\n"
             "    return pcd"},
    {"type": "table",
     "headers": ["操作", "支持扩展名", "引擎 / 许可证"],
     "rows": [
         ["点云读取", "ply, pcd, xyz, pts, las, laz", "Open3D(MIT) / laspy(BSD-2)"],
         ["点云写出", "ply, pcd, xyz", "Open3D(MIT) / NumPy(BSD-3)"],
     ]},
    {"type": "h2", "text": "4.2 mesh_io.py — 网格读写"},
    {"type": "body",
     "text": "网格模块封装 Open3D 的 TriangleMesh 读写，支持 obj / ply / stl / glb / gltf 读取与 "
             "obj / ply / stl / glb 写出（对齐《设计书》M6 标准格式导出）。写 STL 时若网格缺顶点法线，"
             "在副本上自动补算，避免污染调用方对象——这是适配层「格式适配」职责的体现。"},
    {"type": "table",
     "headers": ["函数", "签名", "职责"],
     "rows": [
         ["load_mesh", "load_mesh(path) -> TriangleMesh", "读取单文件；缺失/不支持/空网格 → InputDataError(E100)"],
         ["save_mesh", "save_mesh(mesh, path) -> None", "写回 obj/ply/stl/glb；STL 自动补法线；写失败 → ExportError(E601)"],
     ]},
    {"type": "code", "language": "python",
     "text": "def save_mesh(mesh, path: str) -> None:\n"
             "    ext = _ext(path)\n"
             "    if ext not in MESH_WRITE_EXT:\n"
             "        raise ExportError(f\"不支持的网格写出格式 {ext!r}: {path}\")\n"
             "    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)\n"
             "    # STL 格式要求顶点法线；缺失时在副本上补算，避免污染调用方\n"
             "    if ext == \".stl\" and not mesh.has_vertex_normals():\n"
             "        mesh = o3d.geometry.TriangleMesh(mesh)\n"
             "        mesh.compute_vertex_normals()\n"
             "    if not o3d.io.write_triangle_mesh(path, mesh):\n"
             "        raise ExportError(f\"网格写盘失败: {path}\")"},
    {"type": "h2", "text": "4.3 __init__.py — 包导出"},
    {"type": "body",
     "text": "io/__init__.py 聚合导出两个读写模块的公共符号，业务方只需 "
             "<b>from h3d.io import load_point_cloud, save_point_cloud, load_mesh, save_mesh</b> "
             "即可，无需关心底层引擎。这是适配器模式的入口契约。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "五、接口契约与错误码"},
    {"type": "body",
     "text": "io 层对外暴露的稳定接口遵循设计书 13.2 的「函数 + 路径 + 统一数据模型」约定，错误码与设计书 "
             "13.3 严格对齐。io 层只抛两类异常：读入类错误用 InputDataError(E100)，写出类错误用 ExportError(E601)。"},
    {"type": "table",
     "headers": ["错误码", "异常类", "触发场景", "处理策略"],
     "rows": [
         ["E100", "InputDataError", "文件缺失 / 扩展名不支持 / 空点云 / 空网格 / 解析失败", "标记跳过，记入 manifest（可恢复）"],
         ["E601", "ExportError", "写出格式不支持 / 写盘失败", "跳过该格式，记入 manifest（可恢复）"],
     ]},
    {"type": "body",
     "text": "两类异常均继承 <b>RecoverableError</b>，供 M0 编排器以 <b>except RecoverableError</b> "
             "实现失败隔离——单尊数据坏、单格式不可写，只跳过该尊 / 该格式，不中断队列。"},

    # ---------------------------------------------------------------------
    {"type": "h1", "text": "六、测试与验证"},
    {"type": "body",
     "text": "L1 适配层配套单元测试位于 <b>tests/test_io/</b>，用真实 Open3D / NumPy 生成合成点云与网格，"
             "覆盖格式路由、错误路径（E100 / E601）与往返读写，共 20 项，全部通过。"},
    {"type": "table",
     "headers": ["测试文件", "覆盖点"],
     "rows": [
         ["test_pointcloud_io.py", "ply/pcd/xyz 往返、load_all glob、缺失/空/不支持格式 → E100、写出不支持 → E601、父目录自动创建"],
         ["test_mesh_io.py", "obj/ply/stl 往返、缺失/空/不支持格式 → E100、写出不支持 → E601、STL 自动补法线"],
     ]},
    {"type": "code", "language": "bash",
     "text": "$ python -m pytest tests/test_io -q\n"
             "....................            [100%]\n"
             "20 passed in 2.72s\n"
             "\n"
             "$ python -m pytest -q   # 全量（core + io）\n"
             ".................................................   [100%]\n"
             "49 passed in 2.55s"},
    {"type": "callout",
     "text": "运行方式：在 .venv_lab 虚拟环境执行 pytest；或 python -c \"from h3d.io import load_point_cloud, "
             "save_point_cloud\" 冒烟验证。核心依赖全部 MIT/BSD（Open3D、NumPy、laspy），GPL 类库一律不进入 io 层。"},
]

with open("content.json", "w", encoding="utf-8") as f:
    json.dump(content, f, ensure_ascii=False, indent=2)
print("content.json 生成完成，块数 =", len(content))
