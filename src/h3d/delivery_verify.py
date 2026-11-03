"""交付物核验：清单 vs 磁盘 vs 报表，三处必须自洽。

不是"跑通就算过"——历史上有三次是**跑通了、报表齐全、文件对不上**：
  · 中文路径下 GLB 静默失败：outputs 记着文件、磁盘上没有（ADR-0010）
  · publish.formats 请求了 M4 没产的格式 → formats_missing 假告警
  · 图集 PNG 孤立：PLY 引不到纹理，交付包里只剩顶点色

两个入口共用本模块（``h3d.cli verify`` 与 ``scripts/verify_delivery.py``）：
核验规则只能有一份。历史上「阶段清单」被手抄成八份、四份漏了 X3，
同一类事故不该在核验逻辑上重演。

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


def meta_dir(root):
    """阶段元数据目录：新布局在 <root>/阶段成果/90_meta，旧布局在 <root>/90_meta。

    判据是「这一层是否叫 阶段成果」（paths.py 的约定），不是「有没有 00_raw」。
    真机上两种布局都存在——relic_001 是旧布局（root 就是阶段根），batch3 是新布局
    （root 下同时有 阶段成果/ 和 交付成果/）。只认一种就会把「找不到 manifest」
    当成项目坏了。
    """
    for cand in (os.path.join(root, "90_meta"),
                 os.path.join(root, "阶段成果", "90_meta")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(root, "90_meta")


def load_manifest(root):
    md = meta_dir(root)
    for name in ("delivery_manifest.json", "run_manifest.json"):
        p = os.path.join(md, name)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                return p, json.load(fh)
    return None, None


def _field(entry, name):
    """阶段条目的一个指标：先看顶层，再看嵌套的 ``metrics``。

    两种形状在真实 manifest 里都出现过——runner 把指标拍平到条目顶层，而
    报表读的是嵌套那份。只认一种就会静默读不到（r16 那类缺陷）。
    """
    if name in entry:
        return entry.get(name)
    return (entry.get("metrics") or {}).get(name)


def atlas_expectation(m4_entries):
    """这批交付该不该有图集：``required`` / ``not_required`` / ``unknown``。

    字段优先级（每一条都来自真机 manifest 的实际形状）：

    1. ``paint_mode_effective`` / ``paint_mode`` == ``photo`` → 要
       （照片纹理一定有图集；relic_001 就靠这一条，它没有 ``uv_baked`` 键）
    2. ``uv_baked is True`` → 要
    3. ``atlas_png`` 有值 → 要（正面证据，最硬）
    4. ``paint_mode`` == ``vertex`` 或 ``uv_baked is False`` → 不要
    5. 都没有 → **unknown**，不猜（猜错两个方向都有代价）

    多件混合（一批里既有照片件又有激光件）时取「要」：只要有一件需要图集，
    交付包里就该有，否则那一件的纹理就丢了。
    """
    required = not_required = False
    for e in m4_entries:
        mode = str(_field(e, "paint_mode_effective")
                   or _field(e, "paint_mode") or "").strip().lower()
        baked = _field(e, "uv_baked")
        if mode == "photo" or baked is True or _field(e, "atlas_png"):
            required = True
        elif mode == "vertex" or baked is False:
            not_required = True
    if required:
        return "required"
    if not_required:
        return "not_required"
    return "unknown"


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
    # 每次调用清空累加器：同一个进程里连跑两次（测试、或 GUI 里重试）不该叠加。
    for bucket in (OKS, WARNS, FAILS):
        bucket.clear()
    print(f"核验项目：{root}\n")

    # ---------- 1) manifest 与磁盘 ----------
    path, man = load_manifest(root)
    if man is None:
        FAILS.append(f"找不到 manifest（{meta_dir(root)} 下没有 "
                     "delivery_manifest.json 或 run_manifest.json）")
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

    # ---------- 2b) 多件覆盖：清单说交付的件，磁盘上必须有子目录 ----------
    # 单件批看不出来这件事。多件批最容易的静默失效就是「3 件里 2 件交付了」——
    # 报表齐全、文件都在、脚本照样 PASS，但用户少了一个模型。
    # 判据取五态契约：M6 状态为 done 的件才要求交付（M6 skipped/failed 是契约，不是缺陷）。
    m6_done = sorted({k.split("/")[0] for k, e in st.items()
                      if k.endswith("/M6") and e.get("status") == "done"})
    delivered = []
    if os.path.isdir(deliver):
        delivered = sorted(d for d in os.listdir(deliver)
                           if os.path.isdir(os.path.join(deliver, d)) and d != "report")
    if m6_done:
        missing_items = [i for i in m6_done if i not in delivered]
        check(not missing_items,
              f"交付件数 {len(delivered)}，与 M6 完成件数 {len(m6_done)} 一致",
              f"交付件数不符：M6 记录 {len(m6_done)} 件已交付，磁盘只有 "
              f"{len(delivered)} 个件目录，缺：{'、'.join(missing_items[:5])}")
        extra = [i for i in delivered if i not in m6_done]
        warn(not extra, "交付目录里没有本次未交付的件",
             f"交付目录有 {len(extra)} 个本次未交付的件目录（上次运行的残留？）："
             + "、".join(extra[:5]))
    else:
        warn(False, "",
             "manifest 里没有任何 M6 状态为 done 的件 —— 交付目录为空是预期的"
             "（M5 不达标时按五态契约 M6 不执行），本次无法核验交付内容")
    # ---------- 2c) 每件的交付完整性（读 M6 自己记的账）----------
    # 「manifest 产物都在磁盘」这一层看不见**根本没产出的格式**——它不在 outputs
    # 里，所以无从比对。r16 就是这么漏的：publish.formats 要了 glb，M4 没产，
    # formats_missing 记下来了，没人读，用户拿到的交付包少一个格式而日志全绿。
    m6 = {k.split("/")[0]: e for k, e in st.items()
          if k.endswith("/M6") and e.get("status") == "done"}
    if m6:
        known = [e for e in m6.values() if "formats_missing" in e]
        if known:
            miss_fmt = [(i, e.get("formats_missing") or []) for i, e in m6.items()
                        if e.get("formats_missing")]
            check(not miss_fmt,
                  f"每件请求的格式都已交付（{len(m6)} 件）",
                  "有件缺交付格式：" + "；".join(
                      f"{i} 缺 {'、'.join(map(str, f))}" for i, f in miss_fmt[:5]))
        else:
            WARNS.append("M6 没记 formats_missing，无法确认每件格式是否齐全")

        known_o = [e for e in m6.values() if "texture_orphan" in e]
        if known_o:
            orphan = [i for i, e in m6.items() if e.get("texture_orphan")]
            check(not orphan, "没有图集孤立（图集 PNG 都有 obj/glb 承载）",
                  "有件的图集孤立（PNG 在但 obj/glb 引不到）："
                  + "、".join(orphan[:5]))
        else:
            WARNS.append("M6 没记 texture_orphan，无法确认图集是否有载体")

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
    carriers = [f for f in files if f.lower().endswith((".obj", ".glb", ".gltf"))]

    # 图集该不该有，不能一律要求：顶点色项目（paint_mode=vertex，多见于没有照片
    # 的激光件）本来就没有图集，一律要求会产出**假 FAIL**。假报警比缺检查更糟——
    # 它教会人"FAIL 可以先放过去"。
    #
    # 但也不能只用 `uv_baked` 判断：照片模式的老 manifest **没有这个键**
    # （真机 relic_001 就是），那样会把照片项目误判成顶点色，于是"交付包里没有
    # 图集"这件真缺陷被降级成"顶点色不需要图集"——假阴性比假阳性更危险。
    m4 = {k: e for k, e in st.items() if k.endswith("/M4")}
    need_atlas = atlas_expectation(list(m4.values()))

    if need_atlas == "required":
        check(bool(tex), f"交付包含图集纹理（{len(tex)} 个 PNG）",
              "交付包里没有图集 PNG —— M4 记录该批为照片纹理，但交付包引不到图集")
        if tex and not carriers:
            FAILS.append("有图集 PNG 但没有 OBJ/GLB/GLTF —— 纹理无法被引用（图集孤立）")
        elif tex:
            OKS.append(f"纹理承载格式存在（{len(carriers)} 个 obj/glb/gltf）")
    elif need_atlas == "not_required":
        OKS.append("顶点色模式（paint_mode=vertex），不要求图集 PNG"
                   + ("" if not tex else f"；实际有 {len(tex)} 个 PNG"))
        # 顶点色模式下唯一的颜色来源就是顶点色本身，没着色等于交付灰模。
        # 只在**显式** colored=False 时判失败：取不到这个字段是"没证据"，
        # 不是"证据显示没着色"——后者要报 FAIL，前者只能 WARN。
        colors = [_field(e, "colored") for e in m4.values()]
        if any(c is False for c in colors):
            FAILS.append("顶点色模式但 M4 记录 colored=False —— 交付的是灰模")
        elif any(c is None for c in colors):
            WARNS.append("M4 没记 colored，无法确认顶点色是否真的着上了")
        else:
            OKS.append("顶点色模式且模型已着色")
    else:
        warn(False, "",
             "manifest 里没有 M4，或 M4 没记 paint_mode/uv_baked —— 无法判断该不该有图集")

    # ---------- 4) 报表 ----------
    html = [f for f in files if f.lower().endswith(".html")]
    check(bool(html), f"有 HTML 报表（{len(html)} 个）", "交付包里没有 HTML 报表")
    acc = [f for f in files if os.path.basename(f) == "accuracy.csv"]
    check(bool(acc), f"有 accuracy.csv（{len(acc)} 份）", "缺少 accuracy.csv")

    if acc:
        # **每一份都要查**，不能只看 acc[0]。多件批下每件各有自己的 accuracy.csv，
        # 只看第一份等于默认「其余几件都对」——而「3 件里 1 件表坏了、报表看着正常」
        # 正是这类脚本该抓的东西。
        schemas = {}
        bad_rows = []
        for f in acc:
            with open(f, encoding="utf-8-sig") as fh:
                rows = list(csv.reader(fh))
            if not rows:
                bad_rows.append((f, "空文件"))
                continue
            ncol = len(rows[0])
            schemas.setdefault(ncol, []).append(f)
            uneven = [i for i, r in enumerate(rows) if len(r) != ncol]
            if uneven:
                bad_rows.append((f, f"{len(uneven)} 行列数不一致"))
            if len(rows) < 2:
                bad_rows.append((f, "只有表头没有数据行"))
        check(not bad_rows,
              f"每份 accuracy.csv 自身行列一致（{len(acc)} 份）",
              "accuracy.csv 有问题：" + "；".join(
                  f"{os.path.basename(os.path.dirname(f))}/{os.path.basename(f)} "
                  f"（{why}）" for f, why in bad_rows[:5]))
        if len(schemas) > 1:
            detail = "；".join(
                f"{n} 列 ← " + "、".join(os.path.basename(os.path.dirname(f))
                                        for f in fs[:3])
                for n, fs in sorted(schemas.items()))
            FAILS.append(f"各件的 accuracy.csv 表头列数不一致（{detail}）"
                         " —— 论文表格合并会错位")
        elif schemas:
            ncol = next(iter(schemas))
            head = " ".join(next(csv.reader(
                open(acc[0], encoding="utf-8-sig"))))
            OKS.append(f"accuracy.csv 表头一致（{len(acc)} 份 × {ncol} 列）")
            # X3 三列是否有值（历史：阶段清单漏 X3 → 报表没行、分母少一个）
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
