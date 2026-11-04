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
    """造一个「完整交付」的件目录：三个模型 + 图集 + 两张表。

    ``accuracy.csv`` 这里只写一个**占位结构**（列名不是真实的），够跑结构检查。
    要测数值核对请再调 :func:`add_accuracy_numbers`——它写真实列名与一致的数值。
    """
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


#: 与 `h3d.quality.gate.ACCURACY_HEADERS` 一致的列名（顺序也一致）。
#: 夹具必须用**真列名**，否则数值核对找不到列、静默跳过——那会让测试
#: 「看起来很绿」却什么都没核到。
ACC_HEADERS = [
    "文物编号", "中误差(mm)", "中误差口径", "相对中误差(%对角线)", "覆盖率",
    "边界边比例", "平均点间距(m)", "纹理清晰度", "纹理接缝", "纹理覆盖率",
    "纹理达标", "异常面积(岛内)", "图斑数", "纹理尺度σ_L(Lab)", "残存彩绘占比",
    "彩绘区图斑数", "X1最大形变(mm)", "X1变化占比", "判定",
]


def add_accuracy_numbers(root, items, coverage=0.6148, rmse_mm=87.1479,
                         texture_coverage=0.5865, anomaly=None,
                         sharpness=79.9149, seam=1.9684):
    """给每件补 M5 指标，并写一份**与 manifest 一致**的 accuracy.csv。

    调用后 `verify` 应报 `PASS 23 / WARN 0 / FAIL 0`（若 manifest 其余部分
    也完整）。要测「数值对不上」就在返回值上改 CSV 的某个格子。
    """
    mp = os.path.join(root, "90_meta", "run_manifest.json")
    man = json.load(open(mp, encoding="utf-8"))
    anom_cell = "未筛查" if anomaly is None else f"{anomaly * 100:.2f}%"
    for item in items:
        man["stages"][f"{item}/M4"]["coverage_ratio"] = texture_coverage
        man["stages"][f"{item}/M5"] = {
            "status": "done", "outputs": [],
            "coverage": coverage, "rmse_mm": rmse_mm, "rmse_rel": 0.012845,
            "anomaly_ratio": anomaly,
            "texture_sharpness": sharpness, "texture_seam": seam}
        cells = {
            "文物编号": item,
            "中误差(mm)": rmse_mm,
            "中误差口径": "判门",
            "相对中误差(%对角线)": "1.28%",
            "覆盖率": f"{coverage * 100:.1f}%",
            "异常面积(岛内)": anom_cell,
            "图斑数": "未筛查" if anomaly is None else "7",
            "纹理覆盖率": texture_coverage,
            "纹理清晰度": sharpness,
            "纹理接缝": seam,
            "判定": "pass",
        }
        with open(os.path.join(root, "80_export", item, "accuracy.csv"),
                  "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(",".join(ACC_HEADERS) + "\n")
            fh.write(",".join(str(cells.get(h, "N/A")) for h in ACC_HEADERS)
                     + "\n")
    json.dump(man, open(mp, "w", encoding="utf-8"), ensure_ascii=False)
    return root


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
        stages[f"{item}/M6"] = {
            "status": "done", "outputs": [],
            # 真实 M6 一定写这三个键。夹具不写的话核验会 WARN
            # 「没法确认格式是否齐全」——那是**夹具失真**，不是产品缺陷。
            "formats_requested": ["ply", "obj", "glb"],
            "formats_delivered": ["ply", "obj", "glb"],
            "formats_missing": [],
            "texture_orphan": False,
        }
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
