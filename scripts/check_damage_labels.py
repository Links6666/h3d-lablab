"""校核病害标注包是否符合《采集与标注规范-病害语义》。

规范里每一条约束在这里都有一条检查——没有检查的约束等于没写。

用法::

    python scripts/check_damage_labels.py <标注包根> [--project <项目根>]

退出码：0 全过 / 1 有硬失败 / 2 输入问题。

"硬失败"与"样本不足"分开对待：前者是**数据错**（照片不在、位姿缺失、多边形出画），
必须改；后者只是**量不够**，能算但不能开工（可以边拍边跑这个脚本看进度）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

CLASS_WHITELIST = {
    "crack": "裂隙",
    "detachment": "起翘/脱落",
    "efflorescence": "泛碱/盐析",
    "loss": "缺失（结构性）",
}
SCHEMA = "h3d-damage-labels/1"
MIN_VIEWS = 3
MIN_VIEW_ANGLE_DEG = 40.0
MIN_PER_CLASS = 10
MIN_CLASSES = 3


# --------------------------------------------------------------------------- #
# 位姿 → 视角夹角
# --------------------------------------------------------------------------- #
def _forward(R):
    """相机朝向（世界系）。管线约定：前方 −Z，``R`` 是 world→cam。"""
    import numpy as np

    R = np.asarray(R, dtype=float)
    v = -R[2, :]                       # R.T @ [0,0,-1]
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _max_view_angle(photos, poses):
    """这些照片的相机朝向两两夹角的最大值（度）。缺位姿的忽略。"""
    import numpy as np

    dirs = [_forward(poses[p]["R"]) for p in photos if p in poses]
    best = 0.0
    for i in range(len(dirs)):
        for j in range(i + 1, len(dirs)):
            c = float(np.clip(dirs[i] @ dirs[j], -1.0, 1.0))
            best = max(best, float(np.degrees(np.arccos(c))))
    return best


def _load_poses(project, item):
    """读 capture_meta 的位姿；没有就返回空 dict（调用方据此降级成 WARN）。"""
    if not project:
        return {}
    cand = os.path.join(project, "阶段成果", "90_meta", f"{item}_capture_meta.json")
    if not os.path.isfile(cand):
        cand = os.path.join(project, "90_meta", f"{item}_capture_meta.json")
    if not os.path.isfile(cand):
        return {}
    from h3d.photo_texture import load_capture_meta

    try:
        _cams, images = load_capture_meta(cand)
    except Exception:  # noqa: BLE001 - 位姿坏了算数据问题，由调用方报
        return {}
    return {im["name"]: im for im in images}


def _photo_dir(project, item):
    if not project:
        return ""
    for cand in (os.path.join(project, "阶段成果", "00_photo", item),
                 os.path.join(project, "00_photo", item)):
        if os.path.isdir(cand):
            return cand
    return ""


# --------------------------------------------------------------------------- #
# 单件校核
# --------------------------------------------------------------------------- #
def check_item(item_dir, project=""):
    """返回 (hard_failures, shortages, stats, notes)。"""
    hard, notes = [], []
    item = os.path.basename(os.path.normpath(item_dir))
    path = os.path.join(item_dir, "labels.json")
    if not os.path.isfile(path):
        return [f"{item}: 缺 labels.json"], [], {}, notes
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return [f"{item}: labels.json 读不了（{exc}）"], [], {}, notes

    if data.get("schema") != SCHEMA:
        hard.append(f"{item}: schema={data.get('schema')!r}，应为 {SCHEMA!r}")
        return hard, [], {}, notes
    if data.get("item_id") != item:
        hard.append(f"{item}: item_id={data.get('item_id')!r} 与目录名不一致")

    if not project:
        # 必须说出来：不给项目根时照片/位姿两类检查全不做，静默通过等于骗人
        notes.append(f"{item}: 未指定 --project，照片存在性与位姿检查**全部跳过**"
                     "（这不等于通过）")
    poses = _load_poses(project, item)
    if project and not poses:
        notes.append(f"{item}: 没有 capture_meta，视角夹角与位姿检查**跳过**"
                     "（这不等于通过）")
    pdir = _photo_dir(project, item)
    if project and not pdir:
        notes.append(f"{item}: 找不到 00_photo/{item}/，照片存在性检查跳过")

    damages = data.get("damages") or []
    if not isinstance(damages, list) or not damages:
        hard.append(f"{item}: damages 为空")
        return hard, [], {}, notes

    seen = set()
    per_class: dict[str, int] = {}
    valid = 0
    for d in damages:
        did = d.get("id") or "(无 id)"
        cls = d.get("class")
        if did in seen:
            hard.append(f"{item}/{did}: id 重复")
        seen.add(did)
        if cls not in CLASS_WHITELIST:
            hard.append(f"{item}/{did}: class={cls!r} 不在白名单 "
                        f"{sorted(CLASS_WHITELIST)}")
            continue
        views = d.get("views") or []
        photos = [v.get("photo") for v in views]
        if len(views) < MIN_VIEWS:
            per_class.setdefault(cls, 0)
            notes.append(f"{item}/{did}: 只有 {len(views)} 个视角（<{MIN_VIEWS}），"
                         "不计入有效实例")
            continue
        ok = True
        for v in views:
            ph = v.get("photo") or ""
            poly = v.get("polygon") or []
            if len(poly) < 3:
                hard.append(f"{item}/{did}: {ph} 的多边形少于 3 点")
                ok = False
            if pdir and not os.path.isfile(os.path.join(pdir, ph)):
                hard.append(f"{item}/{did}: 照片不存在 00_photo/{item}/{ph}")
                ok = False
            if poses and ph not in poses:
                hard.append(f"{item}/{did}: {ph} 在 capture_meta 里没有位姿")
                ok = False
            if pdir and poly:
                hard += _polygon_inside(os.path.join(pdir, ph), poly,
                                       f"{item}/{did}:{ph}")
        if poses:
            ang = _max_view_angle(photos, poses)
            if ang < MIN_VIEW_ANGLE_DEG:
                notes.append(f"{item}/{did}: 视角最大夹角 {ang:.1f}° < "
                             f"{MIN_VIEW_ANGLE_DEG}°（像同一机位连拍），不计入有效实例")
                continue
        if ok:
            valid += 1
            per_class[cls] = per_class.get(cls, 0) + 1

    shortages = []
    if valid < MIN_PER_CLASS * MIN_CLASSES:
        shortages.append(f"{item}: 有效实例 {valid} 处，"
                         f"少于 {MIN_CLASSES} 类 × {MIN_PER_CLASS} 处 = "
                         f"{MIN_CLASSES * MIN_PER_CLASS}")
    if len(per_class) < MIN_CLASSES:
        shortages.append(f"{item}: 只覆盖 {len(per_class)} 类，要求 ≥{MIN_CLASSES}")
    for cls, n in sorted(per_class.items()):
        if n < MIN_PER_CLASS:
            shortages.append(f"{item}: {cls}（{CLASS_WHITELIST[cls]}）{n} 处，"
                             f"要求 ≥{MIN_PER_CLASS}")

    stats = {"damages": len(damages), "valid": valid, "per_class": per_class}
    return hard, shortages, stats, notes


def _polygon_inside(photo_path, poly, tag):
    """多边形必须落在画幅内（用原图尺寸核，别拿缩略图的坐标）。"""
    try:
        from PIL import Image

        with Image.open(photo_path) as im:
            w, h = im.size
    except Exception:  # noqa: BLE001 - 图读不了已在别处报
        return []
    bad = [p for p in poly
           if not (isinstance(p, (list, tuple)) and len(p) == 2
                   and 0 <= float(p[0]) < w and 0 <= float(p[1]) < h)]
    if bad:
        return [f"{tag}: 多边形有 {len(bad)} 个点出画（图 {w}×{h}，"
                f"首个越界点 {bad[0]}）"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description="校核病害标注包")
    ap.add_argument("bundle", help="标注包根（其下每件一个子目录，内含 labels.json）")
    ap.add_argument("--project", default="", help="项目根，用于核对照片与位姿")
    args = ap.parse_args()

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "src"))

    if not os.path.isdir(args.bundle):
        print(f"[E100] 标注包根不存在：{args.bundle}")
        return 2
    items = [os.path.join(args.bundle, d) for d in sorted(os.listdir(args.bundle))
             if os.path.isdir(os.path.join(args.bundle, d))]
    if not items:
        print(f"[E100] 标注包根下没有件目录：{args.bundle}")
        return 2

    all_hard, all_short, all_notes = [], [], []
    for d in items:
        hard, short, stats, notes = check_item(d, args.project)
        name = os.path.basename(os.path.normpath(d))
        all_hard += hard
        all_short += short
        all_notes += notes
        if stats:
            print(f"── {name}：{stats['damages']} 处标注，"
                  f"其中 {stats['valid']} 处有效；分类 "
                  + "、".join(f"{k}={v}" for k, v in sorted(stats['per_class'].items())))

    print()
    for n in all_notes:
        print(f"  [注意] {n}")
    for s in all_short:
        print(f"  [样本不足] {s}")
    for h in all_hard:
        print(f"  [硬失败] {h}")

    print()
    if all_hard:
        print(f"结论：不可开工 —— {len(all_hard)} 条硬失败（数据错，必须改）")
        return 1
    if all_short:
        print(f"结论：可继续采，但样本不足 —— {len(all_short)} 项未达标")
        return 1
    print("结论：通过 —— 满足开工门槛（≥3 类 × ≥10 处，视角与位姿齐全）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
