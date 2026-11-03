"""交付物核验：清单 vs 磁盘 vs 报表，三处必须自洽。

不是"跑通就算过"——历史上有三次是**跑通了、报表齐全、文件对不上**：
  · 中文路径下 GLB 静默失败：outputs 记着文件、磁盘上没有（ADR-0010）
  · publish.formats 请求了 M4 没产的格式 → formats_missing 假告警
  · 图集 PNG 孤立：PLY 引不到纹理，交付包里只剩顶点色

用法：
    python scripts/verify_delivery.py D:/h3d_relic_test

退出码 0 = 全部通过；1 = 有 FAIL（WARN 不影响退出码）。
"""
import csv
import json
import os
import re
import sys

FAILS = []
WARNS = []
OKS = []


def check(cond, ok_msg, fail_msg):
    if cond:
        OKS.append(ok_msg)
    else:
        FAILS.append(fail_msg)
    return cond


def warn(cond, ok_msg, warn_msg):
    if cond:
        OKS.append(ok_msg)
    else:
        WARNS.append(warn_msg)


def load_manifest(root):
    for name in ("delivery_manifest.json", "run_manifest.json"):
        p = os.path.join(root, "90_meta", name)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                return p, json.load(fh)
    return None, None


def stages_of(man):
    st = man.get("stages") or man.get("items") or {}
    if isinstance(st, dict):
        return st
    out = {}
    for e in st:
        if isinstance(e, dict) and "stage" in e:
            out[f"{e.get('item')}/{e['stage']}"] = e
    return out


def main(root):
    print(f"核验项目：{root}\n")

    # ---------- 1) manifest 与磁盘 ----------
    path, man = load_manifest(root)
    if man is None:
        FAILS.append("找不到 manifest（90_meta/delivery_manifest.json 或 run_manifest.json）")
        report()
        return 1
    OKS.append(f"manifest: {os.path.basename(path)}")
    st = stages_of(man)
    check(len(st) > 0, f"manifest 有 {len(st)} 条阶段记录", "manifest 里没有任何阶段记录")

    missing = []
    n_out = 0
    for key, e in st.items():
        for p in (e.get("outputs") or []):
            n_out += 1
            if not os.path.exists(p):
                missing.append((key, p))
    check(not missing,
          f"manifest 记录的 {n_out} 个产物全部在磁盘上",
          f"manifest 有 {len(missing)} 个产物不在磁盘上（断点续跑会永远失效）："
          + "；".join(f"{k}→{os.path.basename(p)}" for k, p in missing[:5]))

    # ---------- 2) 交付目录（两种布局都要认）----------
    # 新布局：<root>/阶段成果/ + <root>/交付成果/
    # 旧布局：<root>/80_export/（根目录不叫「阶段成果」时 paths.py 走这条路）
    # 判据是「这一层是否叫 阶段成果」，不是「有没有 00_raw」——两者都可能存在。
    if os.path.isdir(os.path.join(root, "交付成果")):
        deliver, layout = os.path.join(root, "交付成果"), "新（交付成果/）"
    elif os.path.isdir(os.path.join(root, "80_export")):
        deliver, layout = os.path.join(root, "80_export"), "旧（80_export/）"
    else:
        deliver, layout = os.path.join(root, "交付成果"), "缺失"
    check(layout != "缺失", f"交付目录：{layout}", "既没有 交付成果/ 也没有 80_export/")
    print(f"  目录布局：{layout}")
    # 报表目录候选：新布局 交付成果/report；旧布局 <root>/report（paths.report_dir
    # 的定义）；再兜一个 70_report（历史阶段目录）。存在的都算，全部纳入清单。
    cand = [os.path.join(deliver, "report"), os.path.join(root, "report"),
            os.path.join(root, "70_report"), os.path.join(root, "交付成果", "report")]
    report_dirs = [d for d in dict.fromkeys(cand) if os.path.isdir(d)]
    check(bool(report_dirs),
          "报表目录：" + "、".join(os.path.basename(d) for d in report_dirs),
          "找不到任何报表目录（交付成果/report、report/、70_report 都没有）")
    report_dir = report_dirs[0] if report_dirs else os.path.join(root, "report")

    files = []
    seen = set()
    for base in [deliver] + report_dirs:
        for dp, _dn, fn in os.walk(base):
            for f in fn:
                fp = os.path.join(dp, f)
                if fp not in seen:
                    seen.add(fp)
                    files.append(fp)
    check(len(files) > 0, f"交付成果里有 {len(files)} 个文件", "交付成果是空的")

    exts = {}
    for f in files:
        exts[os.path.splitext(f)[1].lower()] = exts.get(
            os.path.splitext(f)[1].lower(), 0) + 1
    print("  交付文件类型：" + "  ".join(f"{k or '(无扩展名)'}×{v}"
                                    for k, v in sorted(exts.items())))

    # 空文件 / 0 字节
    empties = [f for f in files if os.path.getsize(f) == 0]
    check(not empties, "交付文件均非空",
          f"有 {len(empties)} 个 0 字节文件：" + "；".join(os.path.basename(f) for f in empties[:5]))

    # ---------- 3) 图集与纹理承载 ----------
    # 交付侧的图集叫 <件>_diffuse.png（无 LOD 标记），阶段目录里是
    # <件>_L1.0_diffuse.png —— 两种都要认，否则会把"有纹理"误报成"没有"。
    tex = [f for f in files
           if re.search(r"_(L\d\.\d_)?(diffuse|mask)\.png$", f)]
    check(bool(tex), f"交付包含图集纹理（{len(tex)} 个 PNG）", "交付包里没有图集 PNG")
    carriers = [f for f in files if f.lower().endswith((".obj", ".glb", ".gltf"))]
    if tex and not carriers:
        FAILS.append("有图集 PNG 但没有 OBJ/GLB/GLTF —— 纹理无法被引用（图集孤立）")
    elif tex:
        OKS.append(f"纹理承载格式存在（{len(carriers)} 个 obj/glb/gltf）")

    # ---------- 4) 报表 ----------
    html = [f for f in files if f.lower().endswith(".html")]
    check(bool(html), f"有 HTML 报表（{len(html)} 个）", "交付包里没有 HTML 报表")
    acc = [f for f in files if os.path.basename(f) == "accuracy.csv"]
    check(bool(acc), f"有 accuracy.csv（{len(acc)} 份）", "缺少 accuracy.csv")

    if acc:
        with open(acc[0], encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        if rows:
            ncol = len(rows[0])
            uneven = [i for i, r in enumerate(rows) if len(r) != ncol]
            check(not uneven, f"accuracy.csv 列数一致（{ncol} 列 × {len(rows)} 行）",
                  f"accuracy.csv 有 {len(uneven)} 行列数不一致")
            # X3 三列是否有值（历史：阶段清单漏 X3 → 报表没行、分母少一个）
            head = " ".join(rows[0])
            has_x3 = "anomaly" in head or "异常" in head
            warn(has_x3, "accuracy.csv 含 X3 异常列",
                 "accuracy.csv 里没有 X3 异常列 —— 检查阶段清单是否漏了 X3")

    if html:
        txt = open(html[0], encoding="utf-8", errors="ignore").read()
        m = re.search(r"(\d+)\s*/\s*(\d+)|完成率[^\d]{0,8}(\d+)", txt)
        warn(m is not None, "HTML 里有完成率/分母",
             "HTML 里找不到完成率/分母，无法核对阶段数")
        for stage in ("X3", "X1", "X2"):
            warn(stage in txt, f"HTML 覆盖 {stage}", f"HTML 里没有 {stage} 行")

    # ---------- 5) 报表数字能否从 manifest 复算 ----------
    cov = None
    for key, e in st.items():
        if key.endswith("/M4"):
            cov = (e.get("metrics") or {}).get("coverage_ratio")
    if cov is not None and html:
        shown = re.search(r"(\d+\.\d+)\s*%", open(html[0], encoding="utf-8",
                                                  errors="ignore").read())
        warn(shown is not None, f"M4 coverage={cov} 可对照报表", "报表里没找到百分比数字")

    report()
    return 0 if not FAILS else 1


def report():
    print()
    for m in OKS:
        print(f"  [PASS] {m}")
    for m in WARNS:
        print(f"  [WARN] {m}")
    for m in FAILS:
        print(f"  [FAIL] {m}")
    print(f"\n合计：PASS {len(OKS)} / WARN {len(WARNS)} / FAIL {len(FAILS)}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "output/_build/batch3"))
