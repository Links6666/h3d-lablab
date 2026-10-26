# -*- mode: python ; coding: utf-8 -*-
"""make_icon.py : 由插画源图生成 H3D 应用图标（PNG + 多尺寸 ICO）。

背景（2026-10-18）：项目定位由「造像类文物」转向「中小型文物」，图标同步
换成两件器物（褐釉双耳盖壶 + 白釉开光玉壶春）的插画。

源图是一张 1442x1462 的 JPG：圆角卡片铺满画面，上方留白约 10 px，底部带
一层投影。直接缩放成 ICO 会有两个问题：
  1. 卡片外的那圈白会变成不透明方块（在深色任务栏上很脏）；
  2. 投影被一起缩进去，小尺寸（16/32）下糊成一团灰边。

处理策略：
  * 裁成正方形（去掉底部投影），
  * 用半径略大于卡片自身圆角的圆角矩形做 alpha 掩膜，把卡片外的白与投影
    整片切掉（掩膜半径偏大是刻意的：宁可多切进一点米色卡面，也不留白牙），
  * 2x 超采样做抗锯齿的边缘过渡，再降采样到目标尺寸，
  * 输出 PNG（512，GUI 内 logo / windowIcon）+ ICO（16~256，exe 图标）。

用法：
    python scripts/make_icon.py [源图] [--out-dir assets]
"""

from __future__ import annotations

import argparse
import os
import sys

from PIL import Image, ImageDraw

#: ICO 内嵌尺寸。16/24/32 用于任务栏与列表，48/64 用于桌面，128/256 用于大图标视图。
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]

#: 圆角半径占边长的比例。源图卡片实测约 0.18，这里取 0.20 略作内切。
CORNER_RATIO = 0.20

#: 超采样倍率（圆角边缘抗锯齿）。
SS = 4


def _square_crop(im: Image.Image, top_trim: int = 10) -> Image.Image:
    """裁成以卡片为主体的正方形。

    源图宽 1442、高 1462：顶部 10 px 留白，底部约 10 px 投影。取宽为边长，
    纵向从 ``top_trim`` 起裁 —— 正好把底部投影挤出去。
    """
    w, h = im.size
    side = min(w, h - top_trim)
    left = (w - side) // 2
    top = top_trim if h - top_trim >= side else max(0, (h - side) // 2)
    return im.crop((left, top, left + side, top + side))


def _rounded_mask(side: int) -> Image.Image:
    """生成圆角矩形 alpha 掩膜（灰度图，超采样后降采样抗锯齿）。"""
    big = side * SS
    mask = Image.new("L", (big, big), 0)
    radius = int(round(big * CORNER_RATIO))
    # 留 1 px 安全边，避免源图边缘的压缩噪点被算进图标
    inset = SS
    ImageDraw.Draw(mask).rounded_rectangle(
        (inset, inset, big - inset, big - inset), radius=radius, fill=255)
    return mask.resize((side, side), Image.LANCZOS)


def build(src: str, out_dir: str) -> tuple[str, str]:
    im = Image.open(src).convert("RGB")
    im = _square_crop(im)

    side = im.size[0]
    rgba = im.convert("RGBA")
    rgba.putalpha(_rounded_mask(side))

    os.makedirs(out_dir, exist_ok=True)
    png_path = os.path.join(out_dir, "h3d_icon_v3.png")
    ico_path = os.path.join(out_dir, "h3d_icon_v3.ico")

    # PNG：GUI 内按需缩放，给 512 足够（256 在小尺寸下偏软）
    rgba.resize((512, 512), Image.LANCZOS).save(png_path, "PNG", optimize=True)

    # ICO：以 256 为基准逐级 LANCZOS 降采样（PIL 对 sizes= 会自行缩放，
    # 但让它在 RGBA 大图上做，质量比逐级更好）
    rgba.save(ico_path, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    return png_path, ico_path


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 H3D 应用图标")
    ap.add_argument("src", nargs="?",
                    default=os.path.join(os.path.expanduser("~"), "Desktop",
                                         "99e59bea4e3e47838990bc87fac7dc0b.jpg"),
                    help="源插画（JPG/PNG）")
    ap.add_argument("--out-dir", default="assets")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.src):
        print(f"[图标] 源图不存在：{args.src}", file=sys.stderr)
        return 2
    png, ico = build(args.src, args.out_dir)
    print(f"[图标] PNG -> {png} ({os.path.getsize(png)} B)")
    print(f"[图标] ICO -> {ico} ({os.path.getsize(ico)} B)"
          f" 尺寸 {ICO_SIZES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
