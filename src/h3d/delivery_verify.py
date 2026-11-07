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


#: ``accuracy.csv`` 里**数值可核对**的列 → (阶段, 指标键, 是否百分比)。
#:
#: 只收「manifest 里有唯一来源」的列。``中误差口径`` / ``判定`` / ``纹理达标``
#: 是文字与派生结论，由口径逻辑决定，不是可独立复算的数——把它们也纳入核对，
#: 等于把同一段判断逻辑抄两遍（r16 那类「手写副本」）。
#:
#: 列名一改这里就失效，所以 ``_accuracy_drift()`` 会在核验开始时对照
#: ``ACCURACY_HEADERS`` 查一遍，名字对不上就报出来——**不静默跳过**。
ACCURACY_NUMERIC_SOURCES = {
    "中误差(mm)": ("M5", "rmse_mm", False),
    "覆盖率": ("M5", "coverage", True),
    "相对中误差(%对角线)": ("M5", "rmse_rel", True),
    "纹理覆盖率": ("M4", "coverage_ratio", False),
    "异常面积(岛内)": ("M5", "anomaly_ratio", True),
    "图斑数": ("M5", "anomaly_regions", False),
    # 纹理两列直接来自 M5 的质检记录（`_fmt_tex` 只做 round(4)）。
    # 顶点色项目这两列是 `N/A` → 读不成数 → 自动跳过，不会误报。
    "纹理清晰度": ("M5", "texture_sharpness", False),
    "纹理接缝": ("M5", "texture_seam", False),
}


def _cell_number(text, is_pct):
    """把单元格读成数。``'61.5%'`` → 61.5（百分比列保留百分数口径）。

    读不成数（``N/A`` / ``未筛查`` / ``未标定`` / 空）返回 ``None``——**那些是
    合法状态，不是对不上**。把「没数」当「数错了」会逼着人去凑数。
    """
    t = (text or "").strip()
    if t.endswith("%"):
        t = t[:-1].strip()
    t = t.replace(",", "")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _cell_decimals(text):
    """单元格自己显示了几位小数（``'61.5%'`` → 1，``'0.5865'`` → 4）。"""
    t = (text or "").strip().rstrip("%")
    return len(t.split(".")[1]) if "." in t else 0


def _cell_tolerance(text):
    """按单元格自己显示的小数位数给容差（半个最小刻度）。

    报表写的是四舍五入后的值，拿全精度去比一定对不上——容差必须跟着**显示精度**
    走，否则每一条都是假 FAIL。
    """
    return 0.5 * (10.0 ** -_cell_decimals(text)) + 1e-9


def accuracy_value_mismatches(acc_files, stages):
    """逐件逐列核对 ``accuracy.csv`` 的数值与 manifest。

    返回 ``(mismatches, checked)``：``mismatches`` 是
    ``[(件, 列名, 单元格原文, 期望值), ...]``，``checked`` 是实际比过的格子数。
    """
    mism, checked = [], 0
    for f in acc_files:
        item = os.path.basename(os.path.dirname(f))
        with open(f, encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        if len(rows) < 2:
            continue
        head = rows[0]
        # 优先挑「第一列就是本件」的那行；没有就退回第一条数据行
        row = next((r for r in rows[1:] if r and r[0] == item), rows[1])
        if len(row) < len(head):
            row = row + [""] * (len(head) - len(row))
        for col, (stage, key, is_pct) in ACCURACY_NUMERIC_SOURCES.items():
            if col not in head:
                continue
            val = _field(stages.get(f"{item}/{stage}") or {}, key)
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                continue
            idx = head.index(col)
            got = _cell_number(row[idx], is_pct)
            if got is None:
                continue
            expect = float(val) * (100.0 if is_pct else 1.0)
            checked += 1
            if abs(got - expect) > _cell_tolerance(row[idx]):
                # 期望值按**单元格自己的精度**印出来，否则提示里写 62.11 而表里是
                # 62.1%，读的人会以为两个数不一样、去找一个不存在的偏差。
                mism.append((item, col, row[idx],
                             f"{expect:.{_cell_decimals(row[idx])}f}"))
    return mism, checked


def accuracy_column_drift():
    """核验脚本认识的列名，是否都还在 ``ACCURACY_HEADERS`` 里。

    列名一改，数值核对就会**静默跳过**——那是最难发现的一类失效（r16 的根因
    就是「改了产出的键、忘了改消费方」）。这里主动对一遍。
    """
    try:
        from h3d.quality.gate import ACCURACY_HEADERS
    except Exception:  # noqa: BLE001 - 取不到就不判（不因此让整次核验失败）
        return []
    known = {h for _k, h in ACCURACY_HEADERS}
    return [c for c in ACCURACY_NUMERIC_SOURCES if c not in known]


#: HTML 里内嵌的图表是 data URI（base64）。**在原始 HTML 里搜关键字会被它骗过**：
#: 随机 base64 串里出现 "X3" 的概率不低（batch3 的报表里就有 34 处），于是
#: 「HTML 覆盖 X3」这项检查哪怕阶段行真的丢了也会 PASS。判据必须先把图片剥掉。
_DATA_URI = re.compile(r"data:[-\w.]+/[\w.+-]+;base64,[A-Za-z0-9+/=\s]+")


def html_text(path):
    """读 HTML 并剥掉内嵌图片，用于关键字核对（不是给人看的文本）。"""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            raw = fh.read()
    except OSError:
        return ""
    return _DATA_URI.sub(" [img] ", raw)


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


def _ledger_score(man):
    """账本「有多了解这个项目」：阶段记录数、其中产物还在磁盘的个数。"""
    st = man.get("stages") or man.get("items") or {}
    if not isinstance(st, dict):
        return (0, 0)
    n = len(st)
    alive = sum(1 for e in st.values()
                for p in ((e or {}).get("outputs") or [])
                if isinstance(p, str) and os.path.exists(p))
    return (n, alive)


def _ledger_candidates(root):
    """阶段账本可能落在哪：``90_meta`` 与 ``output`` 两处都要看。

    只找 ``90_meta`` 会把**正常项目判成坏的**（2026-11-04 实测）：runner 的默认
    账本位置是 ``<阶段根>/output/run_manifest.json``（``RunnerConfig.manifest``
    的默认值就是 ``output/run_manifest.json``），GUI 一键生成也写这里；而
    ``90_meta`` 只放 M6 的交付摘要与元数据快照。于是一个刚跑完的项目
    ``h3d verify`` 报「找不到 manifest」——验收工具自己先不可靠了。
    """
    cands = [os.path.join(meta_dir(root), n) for n in
             ("run_manifest.json", "delivery_manifest.json")]
    for base in (os.path.join(root, "output"),
                 os.path.join(root, "阶段成果", "output")):
        cands.append(os.path.join(base, "run_manifest.json"))
    seen, out = set(), []
    for p in cands:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def load_manifest(root):
    """读**阶段账本**：在候选里挑「最完整」的那份，而不是按文件名排队。

    两个坑都真踩过：
      · `90_meta/delivery_manifest.json` 是 M6 的**交付摘要**（键 `deliveries`），
        原来优先读它 → 报告「manifest 里没有任何阶段记录」，把正常项目判成坏的；
      · 反过来盲信 `run_manifest.json` 也不行：探针脚本会各自写一份残缺账本
        （真机 relic_001 的 `90_meta/run_manifest.json` 只有 6 条、M4 覆盖率还是
        0.0010 的陈年值），照着它核验会把正确产物判成对不上。

    判据取「阶段记录数 + 产物还在磁盘的个数」，谁更了解这个项目用谁。
    """
    best, best_score = (None, None), (0, 0)
    for p in _ledger_candidates(root):
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                man = json.load(fh)
        except (OSError, ValueError):
            continue
        score = _ledger_score(man)
        if score > best_score:
            best, best_score = (p, man), score
    return best if best[0] else (None, None)


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


def dense_backend_gaps(root, man) -> list[str]:
    """有稠密产物、但账本里查不到 ``backend_id`` 的件号。

    为什么这条要存在（P0-3 / ADR-0028）：跨期比对的前提是「同一条对比链内重建
    后端版本一致」。不同后端（或同后端不同版本）在弱纹理区「补点」的方式不同，
    偏差是系统性偏置——LOD 吃不掉，整片区域会被判成「变化」。

    判据取「有 ``fused_dense.ply`` **且** 有 S1 记录」：只跑稀疏的项目不该被要求
    标后端，纯激光件项目（无 S1）也不该。

    旧账本会命中：那是**真的缺**，不是误报——修法是重跑 S1，或者接受它在报表上
    一直显示（这一项是 WARN 不是 FAIL）。
    """
    st = stages_of(man or {})
    gaps: list[str] = []
    try:
        from h3d.core import paths as _paths

        dense_root = os.path.join(_paths.stage_root(root),
                                  _paths.STAGE_DIRS["dense"])
    except Exception:  # noqa: BLE001 - 算不出路径就没有判据，别让核验崩
        return gaps
    for key in sorted(st):
        if not str(key).endswith("/S1"):
            continue
        entry = st[key] or {}
        inner = entry["metrics"] if isinstance(entry.get("metrics"), dict) else entry
        if inner.get("backend_id"):
            continue
        item = str(key).rsplit("/", 1)[0]
        if os.path.isfile(os.path.join(dense_root, item, "fused_dense.ply")):
            gaps.append(item)
    return gaps


def stages_of(man):
    st = man.get("stages") or man.get("items") or {}
    if isinstance(st, dict):
        return st
    out = {}
    for e in st:
        if isinstance(e, dict) and "stage" in e:
            out[f"{e.get('item')}/{e['stage']}"] = e
    return out


def main(root, quiet: bool = False):
    # 每次调用清空累加器：同一个进程里连跑两次（测试、或 GUI 里重试）不该叠加。
    for bucket in (OKS, WARNS, FAILS):
        bucket.clear()
    global _QUIET
    _QUIET = bool(quiet)
    _say(f"核验项目：{root}\n")

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
    _say(f"  目录布局：{layout}")

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
    _say("  交付文件类型：" + "  ".join(f"{k or '(无扩展名)'}×{v}"
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

        # ---- 数值核对：报表的数 vs manifest 的数（「三处自洽」的第三处）----
        # 结构对得上不代表数对得上：这张表是论文直接引用的，手改一个数、或者
        # 某次重算后 CSV 没重新生成，结构检查全都看不见。
        drift = accuracy_column_drift()
        if drift:
            WARNS.append("核验脚本认识的列名已不在 ACCURACY_HEADERS 里"
                         "（这些列的数值核对会被跳过）：" + "、".join(drift))
        mism, checked = accuracy_value_mismatches(acc, st)
        check(not mism,
              f"accuracy.csv 的 {checked} 个数值与 manifest 一致",
              "accuracy.csv 数值与 manifest 对不上：" + "；".join(
                  f"{i} 的「{c}」写 {cell}，manifest 是 {exp}"
                  for i, c, cell, exp in mism[:5]))
        if not checked and not mism:
            WARNS.append("没有任何数值可核对（CSV 全是 N/A/未筛查，"
                         "或 manifest 缺对应指标）")

    if html:
        txt = html_text(html[0])
        m = re.search(r"(\d+)\s*/\s*(\d+)|完成率[^\d]{0,8}(\d+)", txt)
        warn(m is not None, "HTML 里有完成率/分母",
             "HTML 里找不到完成率/分母，无法核对阶段数")
        # 阶段号的**对外名称**（2026-11-06，ADR-0027）：报表里 X3 写作
        # 「X2 异常检测」、X2 写作「色差评估」。核验要按对外名找，
        # 同时保留裸 id 以兼容旧报表（旧版直接印 stage id）。
        from h3d.report.html_export import public_stage

        for stage in ("X3", "X1", "X2"):
            label = public_stage(stage)
            hit = label in txt or stage in txt
            warn(hit, f"HTML 覆盖 {stage}",
                 f"HTML 里没有 {stage} 行（对外名「{label}」也没找到）")

    # ---------- 4b) 稠密产物的后端标识（P0-3 / ADR-0028）----------
    gaps = dense_backend_gaps(root, man)
    warn(not gaps, "稠密产物均已标注重建后端",
         f"{len(gaps)} 件的稠密产物缺 backend 标识（跨期无法判断是否同后端）："
         f"{'、'.join(gaps[:3])}")

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


#: 静默开关。GUI 要的是**结构**不是 stdout——打包版是 windowed 应用，
#: stderr/stdout 可能是 None，打印出去没人看得见（还会污染日志）。
#: 用模块级开关而不是层层传参：本模块的累加器（OKS/WARNS/FAILS）本来就是
#: 模块级的，函数内的 print 藏在好几处，传参会漏。
_QUIET = False


def _say(msg):
    if not _QUIET:
        print(msg)


def format_report(oks, warns, fails) -> str:
    """把三段结果拼成可直接展示的文本（GUI 对话框 / CLI 输出共用）。"""
    lines = [f"[PASS] {m}" for m in oks]
    lines += [f"[WARN] {m}" for m in warns]
    lines += [f"[FAIL] {m}" for m in fails]
    lines.append("")
    lines.append(f"合计：PASS {len(oks)} / WARN {len(warns)} / FAIL {len(fails)}")
    return "\n".join(lines)


def verify_report(root) -> dict:
    """核验并返回**结构**：``ok`` / 三段清单 / 计数 / 可展示文本。

    GUI 走这里，CLI 走 :func:`main`（两者共用同一份规则，只是出口不同）。
    """
    code = main(root, quiet=True)
    return {
        "root": root,
        "ok": code == 0,
        "pass": list(OKS),
        "warn": list(WARNS),
        "fail": list(FAILS),
        "n_pass": len(OKS),
        "n_warn": len(WARNS),
        "n_fail": len(FAILS),
        "text": format_report(OKS, WARNS, FAILS),
    }


def report():
    _say("")
    for m in OKS:
        _say(f"  [PASS] {m}")
    for m in WARNS:
        _say(f"  [WARN] {m}")
    for m in FAILS:
        _say(f"  [FAIL] {m}")
    _say(f"\n合计：PASS {len(OKS)} / WARN {len(WARNS)} / FAIL {len(FAILS)}")
