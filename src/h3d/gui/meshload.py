"""h3d.gui.meshload : 网格 / 点云 / 贴图读取的容错封装。

实机缺陷（2026-10-26，v1.1.10 真机报告）
--------------------------------------
用户把项目放在 ``E:\\兵马俑照片\\1.1.10``。交付的 ``relic_001.obj`` 在
``open3d.io.read_triangle_mesh`` 里直接抛::

    UnicodeDecodeError: 'utf-8' codec can't decode byte 0xb1 in position 40

同一目录下的 ``relic_001.ply`` 却读取正常（0.03 s）。根因：Open3D 0.19 对
**OBJ / GLB 走 ASSIMP**，路径按 ANSI（本机 = GBK 窄字符）传给 C++，回程再当
UTF-8 解码 → 路径里只要有中文就必炸；``0xb1`` 正是「兵」的 GBK 首字节。PLY /
点云走 Open3D 原生 reader，不受影响。

后果比"预览空白"严重得多：异常发生在 Qt 槽 ``MainWindow._on_finished`` 里，
PySide6 只会把它打到 stderr（windowed 打包版 ``sys.stderr is None`` → 彻底
静默），**槽内异常之后的代码一行都不执行**——结果条不出现、导出按钮不恢复、
X1 按钮不启用，用户看到的就是"跑完了，但预览是空的"。

策略：先直读；失败且路径含非 ASCII 时，把模型**连同伴生文件（mtl / 贴图）**
复制到 ASCII 临时目录再读一次，按「路径 + 大小 + mtime」缓存，重复预览不重复
拷贝。所有函数都不抛异常，失败返回 ``None`` / ``False``，由调用方决定提示。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile

#: 预览/质检最多显示的三角面数；超过则先做体素聚类简化（只在预览里做）
PREVIEW_MAX_TRIS = 60000
#: 简化目标：包围盒对角线 / 该值作为体素边长（实测 6 m 模型 → 约 2.8 万面）
PREVIEW_VOXEL_DIV = 150.0

_MTLLIB_RE = re.compile(r"(?im)^\s*mtllib\s+(.+?)\s*$")
_MAPKD_RE = re.compile(r"(?im)^\s*map_Kd\s+(.+?)\s*$")


def has_non_ascii(path: str) -> bool:
    """路径里是否含非 ASCII 字符（CJK、全角符号、emoji 都算）。"""
    return any(ord(ch) > 127 for ch in str(path))


def _ascii_name(name: str) -> str:
    """把文件名替换成纯 ASCII（保留扩展名）。"""
    stem, ext = os.path.splitext(name)
    keep = "".join(ch for ch in stem if ord(ch) < 128 and ch not in '<>:"/\\|?*')
    return (keep or "mesh") + ext


def _cache_root() -> str | None:
    """挑一个纯 ASCII 的临时目录；全都不 ASCII 就放弃（返回 None）。"""
    for cand in (tempfile.gettempdir(), os.environ.get("TEMP"),
                 os.environ.get("LOCALAPPDATA")):
        if not cand or has_non_ascii(cand):
            continue
        root = os.path.join(cand, "h3d_meshcache")
        try:
            os.makedirs(root, exist_ok=True)
            return root
        except OSError:
            continue
    return None


def _read_text(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return ""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _obj_sidecars(path: str) -> list[tuple[str, str]]:
    """OBJ 的伴生文件：``[(原路径, 角色)]``，角色 ∈ {``obj``/``mtl``/``tex``}。

    角色决定**能不能当文本改写**：mtl 里要改贴图名，所以得读文本；贴图是二进制，
    只能按字节复制（2026-10-26 踩过：把 PNG 当文本重写 → 首字节变成
    ``EF BF BD``，Open3D 报「Unknown file signature」）。
    """
    out: list[tuple[str, str]] = []
    src_dir = os.path.dirname(path)
    text = _read_text(path)
    for line in _MTLLIB_RE.findall(text):
        # 一行可能写多个 mtl（少见），按空白切开逐个试
        for name in line.split():
            mtl = os.path.join(src_dir, name)
            if os.path.isfile(mtl):
                out.append((mtl, "mtl"))
    for mtl, role in list(out):
        if role != "mtl":
            continue
        mtl_dir = os.path.dirname(mtl)
        for name in _MAPKD_RE.findall(_read_text(mtl)):
            tex = os.path.join(mtl_dir, name.strip().strip('"'))
            if os.path.isfile(tex):
                out.append((tex, "tex"))
    return out


def ascii_copy(path: str) -> str | None:
    """把模型（及伴生文件）复制到 ASCII 临时目录，返回副本路径。

    缓存键 = 原路径 + 大小 + mtime；同一次会话内反复预览同一个模型只会拷一次。
    引用名本身非 ASCII 时会被改写（并回写 OBJ/MTL 里的引用）。
    """
    root = _cache_root()
    if not root:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = hashlib.sha1(
        f"v2|{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}".encode(
            "utf-8", "surrogatepass")).hexdigest()[:16]
    dst_dir = os.path.join(root, key)
    base = os.path.basename(path)
    dst = os.path.join(dst_dir, _ascii_name(base))
    if os.path.isfile(dst) and os.path.getmtime(dst) >= st.st_mtime - 1:
        return dst
    try:
        os.makedirs(dst_dir, exist_ok=True)
        if not base.lower().endswith(".obj"):
            shutil.copyfile(path, dst)          # ply / glb：自包含
            return dst

        # OBJ：先搬伴生文件，拿到「原名 → ASCII 名」映射，再改写引用
        src_dir = os.path.dirname(path)
        mapping: dict[str, str] = {base: os.path.basename(dst)}
        for side, _role in _obj_sidecars(path):
            name = os.path.basename(side)
            if name in mapping:
                continue
            new = _ascii_name(name)
            if new in mapping.values():          # 撞名（如两个同名贴图）
                stem, ext = os.path.splitext(new)
                new = f"{stem}_{len(mapping)}{ext}"
            mapping[name] = new
            shutil.copyfile(side, os.path.join(dst_dir, new))
        text = _read_text(path)
        for old, new in mapping.items():
            if old != new:
                text = text.replace(old, new)
        with open(dst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        # MTL 里若引用了非 ASCII 贴图名，同样改写
        for side, role in _obj_sidecars(path):
            if role != "mtl":
                continue
            mtl_new = os.path.join(dst_dir, mapping[os.path.basename(side)])
            mtext = _read_text(mtl_new)
            for old, new in mapping.items():
                if old != new:
                    mtext = mtext.replace(old, new)
            with open(mtl_new, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(mtext)
        return dst
    except Exception:  # noqa: BLE001 - 拷贝失败等价于"读不到"，由调用方提示
        return None


def read_mesh(path: str):
    """读三角网格；失败返回 ``None``（**绝不抛异常**）。

    顺序：直读 → 失败且路径非 ASCII 时走 ASCII 副本重试。
    """
    if not path or not os.path.isfile(path):
        return None
    import open3d as o3d
    try:
        mesh = o3d.io.read_triangle_mesh(path)
        if mesh is not None and not mesh.is_empty():
            return mesh
    except Exception:  # noqa: BLE001 - 非 UTF-8 路径 / ASSIMP 失败
        pass
    if has_non_ascii(path):
        alt = ascii_copy(path)
        if alt:
            try:
                mesh = o3d.io.read_triangle_mesh(alt)
                if mesh is not None and not mesh.is_empty():
                    return mesh
            except Exception:  # noqa: BLE001
                return None
    return None


def read_point_cloud(path: str):
    """读点云；失败返回 ``None``。"""
    if not path or not os.path.isfile(path):
        return None
    import open3d as o3d
    try:
        pcd = o3d.io.read_point_cloud(path)
        if pcd is not None and len(pcd.points):
            return pcd
    except Exception:  # noqa: BLE001
        pass
    if has_non_ascii(path):
        alt = ascii_copy(path)
        if alt:
            try:
                pcd = o3d.io.read_point_cloud(alt)
                if pcd is not None and len(pcd.points):
                    return pcd
            except Exception:  # noqa: BLE001
                return None
    return None


def read_image(path: str):
    """读贴图；失败返回 ``None``。"""
    if not path or not os.path.isfile(path):
        return None
    import open3d as o3d
    try:
        img = o3d.io.read_image(path)
        return img if img is not None and not img.is_empty() else None
    except Exception:  # noqa: BLE001
        pass
    if has_non_ascii(path):
        alt = ascii_copy(path)
        if alt:
            try:
                return o3d.io.read_image(alt)
            except Exception:  # noqa: BLE001
                return None
    return None


def preview_arrays(path: str):
    """取预览用的 ``(vertices, triangles)``；无三角面返回 ``([], [])``。

    面数超过 :data:`PREVIEW_MAX_TRIS` 时做体素聚类简化——matplotlib 的
    ``plot_trisurf`` 在 11 万面上一次重绘要 0.5 s，拖拽旋转会明显卡顿；
    简化到约 2.8 万面后重绘 0.18 s，肉眼几乎看不出差别。
    """
    import numpy as np
    mesh = read_mesh(path)
    if mesh is None:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=int)
    if not mesh.has_triangles():
        verts = np.asarray(mesh.vertices)
        return verts, np.zeros((0, 3), dtype=int)
    n_tris = len(mesh.triangles)
    if n_tris > PREVIEW_MAX_TRIS:
        try:
            import open3d as o3d
            verts_all = np.asarray(mesh.vertices)
            diag = float(np.linalg.norm(verts_all.max(0) - verts_all.min(0)))
            # 剥掉 UV 再简化：simplify_vertex_clustering 不处理 triangle_uvs，
            # 带 UV 会刷一行 WARNING 且旧版会丢 uv（预览也不需要贴图）。
            plain = o3d.geometry.TriangleMesh(mesh.vertices, mesh.triangles)
            simple = plain.simplify_vertex_clustering(
                voxel_size=max(diag / PREVIEW_VOXEL_DIV, 1e-9),
                contraction=o3d.geometry.SimplificationContraction.Average)
            if simple is not None and simple.has_triangles():
                mesh = simple
        except Exception:  # noqa: BLE001 - 简化失败就用原始网格
            pass
    return np.asarray(mesh.vertices), np.asarray(mesh.triangles)


__all__ = ["has_non_ascii", "ascii_copy", "read_mesh", "read_point_cloud",
           "read_image", "preview_arrays", "PREVIEW_MAX_TRIS"]
