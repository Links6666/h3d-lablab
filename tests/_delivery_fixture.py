"""构造「交付包」测试项目的共享夹具（下划线前缀，pytest 不收集）。

为什么单独一个文件：核验逻辑要对**两个入口**测（`h3d.cli verify` 与
`scripts/verify_delivery.py`），两边都要造项目。夹具写两份必然漂移，
而这类测试一旦漂移，就会出现「A 入口绿、B 入口红」却查不出谁对。
"""
import json
import os


def write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "w" if isinstance(data, str) else "wb"
    with open(path, mode) as fh:
        fh.write(data)


def item_dir(root, item, atlas=True, extra_files=()):
    """造一个「完整交付」的件目录：三个模型 + 图集 + 两张表。"""
    d = os.path.join(root, "80_export", item)
    for ext in ("ply", "obj", "glb"):
        write(os.path.join(d, f"{item}.{ext}"), b"model")
    # 图集：交付侧无 LOD 标记。顶点色模式本来就没有图集，用 atlas=False 造。
    if atlas:
        write(os.path.join(d, f"{item}_diffuse.png"), b"png")
    write(os.path.join(d, "accuracy.csv"),
          "item,stage,coverage_ratio,anomaly_ratio\n"
          f"{item},M4,0.58,0.003\n")
    write(os.path.join(d, "thesis_table.csv"), "col\nval\n")
    for name in extra_files:
        write(os.path.join(d, name), b"x")
    return d


def build_project(tmp_path, m6_done, delivered, extra_items=(),
                  atlas=True, uv_baked=True, colored=True):
    """造一个多件项目：00_raw 有 m6_done 件，交付目录只有 delivered 件。"""
    root = str(tmp_path)
    for item in m6_done:
        write(os.path.join(root, "00_raw", item, "scan.ply"), b"cloud")
    stages = {}
    for item in m6_done:
        stages[f"{item}/M1"] = {"status": "done", "outputs": []}
        stages[f"{item}/M4"] = {"status": "done", "outputs": [],
                                "uv_baked": uv_baked,
                                "colored": colored,
                                "paint_mode": "photo" if uv_baked else "vertex"}
        stages[f"{item}/M6"] = {"status": "done", "outputs": []}
    # 交付根即使一件都没交也要存在（现实中 M6 全跳时它就不存在，那是另一种情形；
    # 这里要测的是「交付根在、但少了几件」）
    os.makedirs(os.path.join(root, "80_export"), exist_ok=True)
    for item in delivered:
        item_dir(root, item, atlas=atlas)
    for item in extra_items:
        item_dir(root, item, atlas=atlas)
    write(os.path.join(root, "90_meta", "run_manifest.json"),
          json.dumps({"stages": stages}, ensure_ascii=False))
    # 报表目录
    write(os.path.join(root, "report", "report.html"),
          "<html>完成率 3/3 X3 X1 X2</html>")
    write(os.path.join(root, "report", "metrics_summary.csv"), "k,v\n")
    return root
