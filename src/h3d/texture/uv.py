"""h3d.texture.uv : UV 图集纹理（M4 增强路径，paint_mode='uv'）。

设计依据：
    《设计书》§35 —— UV 展开并映射纹理，输出带纹理模型至 50_tex；
    《软著方案》2.4 —— texture.uv_method=xatlas，纹理图集分辨率 texture_res；
    M5 质检的 overlap（纹理重叠度）指标随 UV 路径启用（设计书 §17.3）。

实现要点：
    1. UV 展开用 xatlas（MIT）：``parametrize`` 返回 (vmap, indices, uvs)，
       按 vmap 重排顶点，indices 重建三角形，uvs 为每顶点 UV（[0,1]）。
    2. 纹理烘焙（bake）：把参考点云颜色映射到 UV 图集——每三角形在 3D 侧
       重心采样若干点，scipy.cKDTree 取最近邻点云颜色；UV 侧光栅化到纹理
       像素（重心坐标判断），实现「点云颜色 -> 纹理图」。
    3. 导出：Open3D 设置 triangle_uvs + textures 后写 OBJ（含 MTL + PNG）与
       GLB（内嵌纹理）；PLY 不支持纹理，回退顶点色。
    4. 健壮性：输入为空 / xatlas 失败（如退化网格）时抛 InputDataError /
       降级顶点色并告警（软著方案 2.6「单项缺失 -> 注明」）。
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from h3d.core import get_logger

log = get_logger("texture.uv")


def rebuild_with_uv(mesh, vmap, indices, uvs):
    """按 ``vmap`` 重建展开后的网格（UV 缓存命中时用，与 unwrap_mesh 逐位同构）。

    ``vmap`` 语义：新顶点下标 -> 原顶点下标（xatlas ``parametrize`` 的约定）。
    顶点色/法线必须一起按 ``vmap`` 重排，否则缓存命中的网格会丢掉属性
    （顶点色 PLY 兜底会变成全黑、法线缺失会让采样判据失效）。
    """
    o3d = _o3d()
    vmap = np.asarray(vmap, dtype=np.int64)
    verts = np.asarray(mesh.vertices, dtype=np.float32)
    new_verts = np.asarray(verts[vmap], dtype=np.float64)
    new_mesh = o3d.geometry.TriangleMesh()
    new_mesh.vertices = o3d.utility.Vector3dVector(new_verts)
    new_mesh.triangles = o3d.utility.Vector3iVector(
        np.asarray(indices, dtype=np.int32))
    if mesh.has_vertex_colors():
        new_mesh.vertex_colors = o3d.utility.Vector3dVector(
            np.asarray(mesh.vertex_colors, dtype=np.float64)[vmap])
    if mesh.has_vertex_normals():
        new_mesh.vertex_normals = o3d.utility.Vector3dVector(
            np.asarray(mesh.vertex_normals, dtype=np.float64)[vmap])
    return new_mesh, np.asarray(uvs, dtype=np.float32), \
        np.asarray(indices, dtype=np.int64)


def unwrap_mesh(mesh, max_vertices: int = 200_000, return_vmap: bool = False):
    """xatlas UV 展开，返回 (new_mesh, uv_coords, uv_triangles[, vmap])。

    参数
    ----
    mesh:        TriangleMesh（M3 产物）。
    max_vertices: 顶点数上限（xatlas 展开耗时随顶点数增长，超限降级告警）。
    return_vmap: 追加返回 ``vmap``（新顶点 -> 原顶点）。缓存复用需要它把
                 「几何指纹 + vmap」重建成同一张展开网格，否则只能整段重跑。

    返回
    ----
    (new_mesh, uv, faces)：重建网格（含顶点色/法线，按 vmap 重排）、
    每顶点 UV（float32, n×2）、三角形索引。
    """
    import xatlas

    from h3d.core import InputDataError

    verts = np.asarray(mesh.vertices, dtype=np.float32)
    faces = np.asarray(mesh.triangles, dtype=np.uint32)
    if len(verts) == 0 or len(faces) == 0:
        raise InputDataError("UV 展开输入网格为空")
    if len(verts) > max_vertices:
        log.warning("顶点数 %d 超上限 %d，UV 展开可能较慢", len(verts), max_vertices)

    try:
        vmap, indices, uvs = xatlas.parametrize(verts, faces)
    except Exception as exc:  # 退化网格等
        raise InputDataError(f"xatlas UV 展开失败: {exc}") from exc

    vmap = np.asarray(vmap, dtype=np.int64)
    new_mesh, uv, faces_idx = rebuild_with_uv(mesh, vmap, indices, uvs)
    log.info("UV 展开完成：%d 顶点 -> %d 顶点 / %d 面", len(verts),
             len(new_mesh.vertices), len(faces_idx))
    if return_vmap:
        return new_mesh, uv, faces_idx, vmap
    return new_mesh, uv, faces_idx


def bake_texture(mesh, uv, ref_pcd, size: int = 1024,
                 samples_per_tri: int = 8) -> np.ndarray:
    """把参考点云颜色烘焙到 UV 纹理图（重心采样 + 光栅化）。

    参数
    ----
    mesh:           展开后的网格（顶点与 uv 一一对应）。
    uv:             每顶点 UV（n×2，[0,1]）。
    ref_pcd:        带色参考点云（Open3D PointCloud）。
    size:           纹理图边长（默认 1024，texture_res 概念）。
    samples_per_tri:每三角形 3D 重心采样点数（颜色插值密度）。

    返回
    ----
    (size, size, 3) uint8 纹理图（黑底填充缺失区）。
    """
    import open3d as o3d
    from scipy.spatial import cKDTree

    if not ref_pcd.has_colors() or not ref_pcd.has_points():
        raise ValueError("纹理烘焙需要带色参考点云")
    pts = np.asarray(ref_pcd.points, dtype=np.float64)
    colors = np.asarray(ref_pcd.colors, dtype=np.float64)  # 0~1
    tree = cKDTree(pts)

    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.triangles, dtype=np.int64)
    uv_f = np.asarray(uv, dtype=np.float64)
    image = np.zeros((size, size, 3), dtype=np.float32)

    # 重心采样权重（每三角形 samples_per_tri 个点）
    rng = np.random.default_rng(0)
    w1 = rng.random((samples_per_tri,))
    w2 = rng.random((samples_per_tri,))
    mask = w1 + w2 > 1.0
    w1[mask] = 1.0 - w1[mask]
    w2[mask] = 1.0 - w2[mask]
    w0 = 1.0 - w1 - w2

    for tri_idx, tri in enumerate(faces):
        v0, v1, v2 = verts[tri]
        # 3D 采样点
        pts3d = (w0[:, None] * v0 + w1[:, None] * v1 + w2[:, None] * v2)
        # 最近邻颜色
        _d, idx = tree.query(pts3d, k=1)
        tri_colors = colors[idx]  # (S, 3)
        # UV 采样点
        t0, t1, t2 = uv_f[tri]
        uv_pts = (w0[:, None] * t0 + w1[:, None] * t1 + w2[:, None] * t2)
        # 光栅化到像素（uv_pts 为 (S,2)：u=v[:,0], v=v[:,1]）
        px = np.clip((uv_pts[:, 0] * size).astype(np.int32), 0, size - 1)
        py = np.clip(((1.0 - uv_pts[:, 1]) * size).astype(np.int32), 0, size - 1)
        image[py, px, :] = tri_colors
    log.info("纹理烘焙完成：%d 面 -> %dx%d 图集（%d 采样点/面）",
             len(faces), size, size, samples_per_tri)
    return np.clip(image * 255.0, 0, 255).astype(np.uint8)


def attach_texture(mesh, uv, faces, image: np.ndarray):
    """把 UV 与纹理图附着到网格（triangle_uvs + textures），供带纹理导出。"""
    o3d = _o3d()
    uv_f = np.asarray(uv, dtype=np.float64)[np.asarray(faces, dtype=np.int64)]
    mesh.triangle_uvs = o3d.utility.Vector2dVector(uv_f.reshape(-1, 2))
    mesh.textures = [o3d.geometry.Image(image)]
    mesh.triangle_material_ids = o3d.utility.IntVector(
        np.zeros(len(faces), dtype=np.int32))
    return mesh


def _write_obj_textured(path: str, mesh, uv, image: np.ndarray) -> bool:
    """手写带纹理 OBJ + MTL + 贴图（Open3D 写 OBJ 实测 5.3 s/37k 顶点）。

    为什么自己写：``o3d.io.write_triangle_mesh`` 写 obj 时按**逐角**重复写 UV
    （3F 条 ``vt``）并且逐行格式化，37k 顶点/74k 面实测 5.34 s，占满一件 M4 的
    导出耗时；同一份数据用 numpy 一次成型约 0.1 s。复用图集后导出成了唯一
    大头（单件 6.2 s 里 6.05 s 是导出），所以这一项必须拿下。

    与 Open3D 逐值等价（已核对）：``vt`` 直接写 uv 原值**不做 V 翻转**，
    ``vn`` 写顶点法线，``f`` 用 ``v/vt/vn`` 同号（xatlas 已按 vmap 拆开接缝，
    每顶点只有一个 UV，所以逐顶点 vt 与逐角 vt 语义相同）。

    返回 False 表示无法安全快速写出（例如拿不到逐顶点 UV），由调用方回退。
    """
    import os

    base = os.path.splitext(os.path.basename(path))[0]
    parent = os.path.dirname(os.path.abspath(path))
    mtl_name = f"{base}.mtl"
    png_name = f"{base}_diffuse.png"
    uv_pv = _per_vertex_uv(mesh, uv)
    if uv_pv is None:
        return False
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.triangles, dtype=np.int64)
    if len(verts) == 0 or len(faces) == 0 or uv_pv.shape[0] != len(verts):
        return False
    has_normals = mesh.has_vertex_normals()
    normals = (np.asarray(mesh.vertex_normals, dtype=np.float64)
               if has_normals else None)

    def _block(fmt: str, arr: np.ndarray) -> str:
        # tolist() 得到的是嵌套 list，"%.6f" % [x,y,z] 会抛 TypeError，必须转 tuple
        return "\n".join(map(fmt.__mod__, map(tuple, arr.tolist())))

    fi = faces + 1
    if has_normals:
        face_txt = "\n".join(
            f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}" for a, b, c in fi.tolist())
    else:
        face_txt = "\n".join(f"f {a}/{a} {b}/{b} {c}/{c}" for a, b, c in fi.tolist())

    lines = [f"# H3D textured mesh ({base})", f"mtllib {mtl_name}",
             f"o {base}", _block("v %.6f %.6f %.6f", verts),
             _block("vt %.6f %.6f", uv_pv)]
    if has_normals:
        lines.append(_block("vn %.6f %.6f %.6f", normals))
    lines += [f"usemtl {base}", "s off", face_txt]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")

    mtl = "\n".join([
        "# H3D material", f"newmtl {base}",
        "Ka 1.000 1.000 1.000", "Kd 1.000 1.000 1.000",
        "Ks 0.000 0.000 0.000", "Ns 0.0", "d 1.0", "illum 2",
        f"map_Kd {png_name}", ""])
    with open(os.path.join(parent, mtl_name), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(mtl)
    from PIL import Image as _Image
    _Image.fromarray(np.asarray(image, dtype=np.uint8)).save(
        os.path.join(parent, png_name))
    return True


def export_textured(mesh, out_dir: str, item_id: str, lod_tag: str = "L1.0",
                    formats: Optional[list[str]] = None,
                    uv: Optional[np.ndarray] = None,
                    image: Optional[np.ndarray] = None) -> list[str]:
    """带纹理模型导出（OBJ+MTL+PNG / GLB；PLY 不支持纹理时回退顶点色）。

    返回导出文件路径列表。

    实机问题（2026-10-18）：Open3D 0.19 的 ``write_triangle_mesh`` 写 GLB 时
    **静默丢弃纹理与 UV**（实测产物 glTF JSON 里 ``images/textures`` 为空、
    图元无 ``TEXCOORD_0``）——交付的 glb 在任意查看器里都是一片灰白，且没有
    任何报错。因此 GLB 改用 trimesh（MIT，项目已作为可选依赖）手工写带纹理
    glTF；trimesh 不可用时回退 Open3D 并**显式告警**，不再假装成功。

    OBJ 侧同名问题：Open3D 固定把纹理写成 ``<base>_0.png``，而质检/交付按
    ``<base>_diffuse.png`` 找图，于是出现「贴图文件名对不上」与「同一张图存
    两份」。此处统一收敛为 ``<base>_diffuse.png`` 并改写 MTL 的 ``map_Kd``。
    """
    import os

    from h3d.core import ExportError

    formats = formats or ["obj", "glb"]
    os.makedirs(out_dir, exist_ok=True)
    # 自愈：调用方给了 uv+image 却没挂到网格上时，Open3D 会写出「无 mtl/无贴图」
    # 的 obj（只留一句 console 警告）——正是本函数要根除的静默失败。
    if image is not None and uv is not None:
        try:
            if not mesh.has_triangle_uvs() or not mesh.textures:
                attach_texture(mesh, uv, np.asarray(mesh.triangles), image)
        except Exception as exc:  # noqa: BLE001 - 挂载失败交由下方告警
            log.warning("纹理挂载失败（导出将不含贴图）：%s", exc)
    outputs: list[str] = []
    for fmt in formats:
        fmt = fmt.lstrip(".").lower()
        if fmt in ("ply",):
            log.warning("PLY 不支持纹理图，回退顶点色导出")
            continue
        if fmt not in ("obj", "glb", "gltf"):
            log.warning("跳过不支持的带纹理格式 %r", fmt)
            continue
        path = os.path.join(out_dir, f"{item_id}_{lod_tag}.{fmt}")
        if fmt == "obj" and image is not None:
            # 快速路径：手写 obj+mtl+png（等价于 Open3D 产物但快 ~50 倍）。
            # 失败（拿不到逐顶点 UV 等）才回退 Open3D + _normalize_obj_texture。
            try:
                if _write_obj_textured(path, mesh, uv, image):
                    outputs.append(os.path.abspath(path))
                    continue
                log.warning("逐顶点 UV 不可用，回退 Open3D 写 OBJ（较慢）：%s",
                            os.path.basename(path))
            except (OSError, ValueError) as exc:
                log.warning("快速 OBJ 写出失败（回退 Open3D）：%s", exc)
        if fmt in ("glb", "gltf"):
            if image is not None and _write_glb_textured(path, mesh, uv, image):
                outputs.append(os.path.abspath(path))
                continue
            if image is not None:
                log.warning(
                    "%s 未能写入纹理（trimesh 不可用或失败），GLB 仅含几何；"
                    "带贴图请用同名 .obj + .mtl + _diffuse.png",
                    os.path.basename(path))
        ok = _o3d().io.write_triangle_mesh(path, mesh)
        if not ok:
            raise ExportError(f"带纹理模型导出失败: {path}")
        if fmt == "obj" and image is not None:
            _normalize_obj_texture(path, image)
        outputs.append(os.path.abspath(path))
    log.info("带纹理模型导出：%s_%s（%s）", item_id, lod_tag, outputs)
    return outputs


def _normalize_obj_texture(obj_path: str, image: np.ndarray) -> None:
    """把 Open3D 写出的 ``<base>_0.png`` 收敛为 ``<base>_diffuse.png``。

    Open3D 写 OBJ 时自动生成 ``<base>.mtl`` 与 ``<base>_0.png``；本函数把纹理
    另存为 ``<base>_diffuse.png``、改写 MTL 的 ``map_Kd`` 行并删除旧 ``_0.png``，
    使「obj / mtl / png」三件套命名统一，交付复制时只认一套规则。
    """
    import os

    from PIL import Image

    base = os.path.splitext(obj_path)[0]
    diffuse = f"{base}_diffuse.png"
    Image.fromarray(image).save(diffuse)
    mtl = f"{base}.mtl"
    if os.path.exists(mtl):
        with open(mtl, encoding="utf-8") as fh:
            lines = fh.readlines()
        out = []
        for line in lines:
            if line.strip().lower().startswith("map_kd"):
                indent = line[:len(line) - len(line.lstrip())]
                out.append(f"{indent}map_Kd {os.path.basename(diffuse)}\n")
            else:
                out.append(line)
        with open(mtl, "w", encoding="utf-8") as fh:
            fh.writelines(out)
    stale = f"{base}_0.png"
    if os.path.exists(stale) and os.path.abspath(stale) != os.path.abspath(diffuse):
        try:
            os.remove(stale)
        except Exception:  # noqa: BLE001 - 清理残留属尽力而为，失败不得中断导出
            log.debug("残留贴图清理失败（忽略）：%s", stale)


def _per_vertex_uv(mesh, uv: Optional[np.ndarray]) -> Optional[np.ndarray]:
    """取得逐顶点 UV（trimesh 需要 N×2，而 Open3D 存的是逐角 3F×2）。"""
    n_v = len(mesh.vertices)
    if uv is not None:
        arr = np.asarray(uv, dtype=np.float64)
        if arr.ndim == 2 and arr.shape[0] == n_v:
            return arr
    if not mesh.has_triangle_uvs():
        return None
    tri_uv = np.asarray(mesh.triangle_uvs, dtype=np.float64)
    faces = np.asarray(mesh.triangles, dtype=np.int64)
    if tri_uv.shape[0] != faces.size:
        return None
    # xatlas 展开后同一顶点只对应一个 UV；逐角回填（后写覆盖，值相同）
    per = np.zeros((n_v, 2), dtype=np.float64)
    per[faces.reshape(-1)] = tri_uv
    return per


def _write_glb_textured(path: str, mesh, uv: Optional[np.ndarray],
                        image: np.ndarray) -> bool:
    """用 trimesh 写**带纹理** GLB（内嵌 PNG + TEXCOORD_0 + baseColorTexture）。

    返回 True 表示成功。任何失败都返回 False，由调用方决定回退/告警——
    绝不静默产出无纹理 glb。
    """
    try:
        import trimesh
        from PIL import Image as _Image
        from trimesh.visual.material import PBRMaterial
    except Exception as exc:  # noqa: BLE001 - trimesh 为可选依赖
        log.warning("trimesh 不可用，无法写带纹理 GLB：%s", exc)
        return False
    uv_pv = _per_vertex_uv(mesh, uv)
    if uv_pv is None:
        log.warning("网格缺少逐顶点 UV，跳过带纹理 GLB")
        return False
    try:
        img = _Image.fromarray(np.asarray(image, dtype=np.uint8))
        tri = trimesh.Trimesh(vertices=np.asarray(mesh.vertices, dtype=np.float64),
                              faces=np.asarray(mesh.triangles, dtype=np.int64),
                              process=False)
        # 关键：trimesh 默认 baseColorFactor 为 0.4 灰，会把贴图整体压暗 60%，
        # 必须显式设为纯白；doubleSided 保证薄壁/开孔处不出现黑洞。
        mat = PBRMaterial(baseColorTexture=img,
                          baseColorFactor=[255, 255, 255, 255],
                          metallicFactor=0.0, roughnessFactor=1.0,
                          doubleSided=True)
        tri.visual = trimesh.visual.TextureVisuals(uv=uv_pv, material=mat)
        blob = tri.export(file_type="glb")
        if not blob:
            return False
        with open(path, "wb") as fh:
            fh.write(blob)
        return True
    except Exception as exc:  # noqa: BLE001 - 任何失败都回退，不阻断导出
        log.warning("带纹理 GLB 写入失败（%s）", exc)
        return False


def _o3d():
    import open3d

    return open3d


__all__ = ["unwrap_mesh", "rebuild_with_uv", "bake_texture", "attach_texture",
           "export_textured"]
