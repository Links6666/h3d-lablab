"""把《方法学》Markdown 排成 A4 PDF（reportlab + 微软雅黑）。

只支持本文用到的子集：# / ## / ### 标题、段落、有序与无序列表、表格、代码块、
引用块。够用即可，不写成通用渲染器。
"""
import os
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

SRC = r"E:\h3d_lablab\docs\方法学-让自动化系统的数字可信.md"
OUT = r"E:\h3d_lablab\docs\方法学-让自动化系统的数字可信.pdf"

INK = colors.HexColor("#1d1d1f")
GREY = colors.HexColor("#6b6b70")
ACCENT = colors.HexColor("#8a5a2b")
RULE = colors.HexColor("#d8d4cc")


def _version() -> str:
    """作者字段里的版本号取自 h3d.__version__（别再手抄一遍——它漂过）。"""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    import h3d
    return h3d.__version__


def setup_fonts():
    pdfmetrics.registerFont(TTFont("MSYH", "C:/Windows/Fonts/msyh.ttc",
                                   subfontIndex=0))
    pdfmetrics.registerFont(TTFont("MSYH-B", "C:/Windows/Fonts/msyhbd.ttc",
                                   subfontIndex=0))
    return "MSYH", "MSYH-B"


def esc(t: str) -> str:
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 行内代码与粗体
    t = re.sub(r"`([^`]+)`",
               r'<font face="MSYH" size="7.8" color="#8a5a2b">\1</font>', t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    return t


def build_styles(F, FB):
    return {
        "h1": ParagraphStyle("h1", fontName=FB, fontSize=19, leading=26,
                             textColor=INK, spaceAfter=6),
        "h2": ParagraphStyle("h2", fontName=FB, fontSize=13.5, leading=19,
                             textColor=ACCENT, spaceBefore=12, spaceAfter=4),
        "h3": ParagraphStyle("h3", fontName=FB, fontSize=11, leading=16,
                             textColor=INK, spaceBefore=8, spaceAfter=3),
        "body": ParagraphStyle("body", fontName=F, fontSize=9.6, leading=15,
                               textColor=INK, spaceAfter=4, wordWrap="CJK"),
        "quote": ParagraphStyle("quote", fontName=F, fontSize=9.2, leading=14,
                                textColor=GREY, leftIndent=10,
                                borderPadding=(0, 0, 0, 8), spaceAfter=5,
                                wordWrap="CJK"),
        "li": ParagraphStyle("li", fontName=F, fontSize=9.6, leading=15,
                             textColor=INK, leftIndent=12, bulletIndent=2,
                             spaceAfter=2, wordWrap="CJK"),
        "cell": ParagraphStyle("cell", fontName=F, fontSize=8.4, leading=12,
                               textColor=INK, wordWrap="CJK"),
        "cellh": ParagraphStyle("cellh", fontName=FB, fontSize=8.4, leading=12,
                                textColor=colors.white, wordWrap="CJK"),
        "code": ParagraphStyle("code", fontName=F, fontSize=8.4, leading=12,
                               textColor=INK, leftIndent=8),
    }


def join_wrapped(parts):
    """把被硬换行切开的同一段文字拼回去。

    为什么需要：本文的 Markdown 是**硬换行**的（每行 ~40 个汉字），而
    reportlab 的 Paragraph 会自己排版。按行各建一个 Paragraph 的后果是
    ①每行都成了独立段落（左边缘参差）；②跨行的 ``**粗体**`` 配不上对，
    星号被原样印出来（第一版 PDF 第 6 页就是这样）。

    拼接规则：中文之间直接相连，ASCII 字母数字之间补一个空格——否则
    ``sample_points_uniformly`` 这种单词会在换行处被粘成一坨。
    """
    out = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if out and out[-1].isascii() and out[-1].isalnum() \
                and part[0].isascii() and part[0].isalnum():
            out += " "
        out += part
    return out


def _code_line(text: str) -> str:
    """代码块的一行：转义 + 把行首空格换成不断行空格（否则缩进会被折掉）。"""
    import re as _re

    out = esc(text.rstrip())
    m = _re.match(r"^( +)", out)
    if m:
        out = "&nbsp;" * len(m.group(1)) + out[len(m.group(1)):]
    return out


def _is_plain(ln: str) -> bool:
    """这一行是不是「可以跟上下行拼成一段」的普通正文。"""
    if not ln.strip():
        return False
    if ln.startswith(("```", "|", "### ", "## ", "# ", "> ")):
        return False
    if re.match(r"^\d+\.\s", ln) or ln.startswith("- "):
        return False
    return True


def _dw(text: str) -> int:
    """显示宽度：中日韩全角字算 2，其余算 1。列宽分配要用它，不能用 len()。"""
    import unicodedata

    text = re.sub(r"[`*]", "", text)
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def _col_widths(rows, ncol, width):
    """按内容长度分配列宽。

    等宽列在「三列、且一列只有术语」的表上会把术语挤断成两行，
    又把空话那一列留出一大片白。这里用「表头与该列 p75 内容的显示宽度」
    做权重，再夹在 10%~52% 之间——上限是为了防止某一列把整行吃掉。
    """
    if ncol <= 1:
        return [width] * ncol
    w = []
    for c in range(ncol):
        vals = sorted(_dw(r[c]) for r in rows[1:]) or [4]
        typical = vals[min(len(vals) - 1, int(len(vals) * 0.75))]
        w.append(float(max(4, _dw(rows[0][c]), typical)))
    # 下限 0.21：三列表（术语/解释/作用）的第一列只有几个字，按内容比例算会被挤到
    # 10%，英文标识符被硬切成 "norma / l_agreement"。四列以上的表内容通常比较均衡，
    # 用 0.12 就够，抬太高反而浪费。
    lo = 0.21 if ncol <= 3 else 0.12
    total = sum(w)
    frac = [x / total for x in w]
    for _ in range(4):                      # 反复归一，直到每个都在 [lo, 0.52]
        frac = [min(0.52, max(lo, f)) for f in frac]
        s = sum(frac)
        frac = [f / s for f in frac]
    return [width * f for f in frac]


def table_flow(rows, S, width):
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    body = [[Paragraph(esc(c), S["cellh"] if i == 0 else S["cell"])
             for c in r] for i, r in enumerate(rows)]
    t = Table(body, colWidths=_col_widths(rows, ncol, width),
              repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#faf8f5")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _title_from(src: str) -> str:
    """取 markdown 第一个 `# ` 当 PDF 元数据标题（不再手抄一遍）。"""
    try:
        with open(src, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return os.path.splitext(os.path.basename(src))[0]


def main(argv=None):
    global SRC, OUT
    import argparse

    ap = argparse.ArgumentParser(description="把 docs 下的 markdown 排成 A4 PDF")
    ap.add_argument("src", nargs="?", default=SRC, help="源 .md（默认：方法学）")
    ap.add_argument("out", nargs="?", default=None, help="输出 .pdf（默认同名）")
    args = ap.parse_args(argv)
    SRC = os.path.abspath(args.src)
    OUT = os.path.abspath(args.out or os.path.splitext(SRC)[0] + ".pdf")
    if not os.path.isfile(SRC):
        print(f"[E100] 源文件不存在：{SRC}")
        return 2
    F, FB = setup_fonts()
    S = build_styles(F, FB)
    text = open(SRC, encoding="utf-8").read()

    doc = SimpleDocTemplate(OUT, pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=16 * mm,
                            title=_title_from(SRC),
                            author=f"H3D {_version()}")
    width = A4[0] - 40 * mm
    story = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        if not ln.strip():
            i += 1
            continue
        if ln.startswith("```"):
            j = i + 1
            block = []
            while j < len(lines) and not lines[j].startswith("```"):
                block.append(lines[j])
                j += 1
            # 代码块必须保留**行首缩进**：JSON 层次与目录树全靠它读。
            # reportlab 的 Paragraph 会把连续空格折成一个，所以换成 &nbsp;。
            story.append(Paragraph("<br/>".join(_code_line(b) for b in block),
                                   S["code"]))
            story.append(Spacer(1, 4))
            i = j + 1
            continue
        if ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                raw = lines[i].strip().strip("|")
                cells = [c.strip() for c in raw.split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            story.append(table_flow(rows, S, width))
            story.append(Spacer(1, 7))
            continue
        # 普通正文 / 列表项：把硬换行拼回一段（见 join_wrapped 的说明）
        m_item = re.match(r"^(\d+)\.\s+(.*)$", ln)
        if m_item or ln.startswith("- "):
            head = m_item.group(2) if m_item else ln[2:]
            bullet = (m_item.group(1) + ".") if m_item else "\u2022"
            chunk = [head]
            j = i + 1
            while j < len(lines) and lines[j].strip() \
                    and lines[j].startswith(("  ", "\t")) \
                    and _is_plain(lines[j]):
                chunk.append(lines[j])
                j += 1
            story.append(Paragraph(esc(join_wrapped(chunk)), S["li"],
                                   bulletText=bullet))
            i = j
            continue
        if _is_plain(ln):
            chunk = [ln]
            j = i + 1
            while j < len(lines) and _is_plain(lines[j]) \
                    and not lines[j].startswith(("  ", "\t")):
                chunk.append(lines[j])
                j += 1
            story.append(Paragraph(esc(join_wrapped(chunk)), S["body"]))
            i = j
            continue
        if ln.startswith("### "):
            story.append(Paragraph(esc(ln[4:]), S["h3"]))
        elif ln.startswith("## "):
            story.append(KeepTogether([Spacer(1, 3),
                                       Paragraph(esc(ln[3:]), S["h2"])]))
        elif ln.startswith("# "):
            story.append(Paragraph(esc(ln[2:]), S["h1"]))
            story.append(Spacer(1, 2))
        elif ln.startswith("> "):
            # 引用块也要按块聚合：逐行建 Paragraph 会让每行变成一个独立段落，
            # 行距被拉开一倍，读起来像「一首诗」而不是一句话。
            chunk = [ln[2:]]
            j = i + 1
            while j < len(lines) and lines[j].startswith("> "):
                chunk.append(lines[j][2:])
                j += 1
            story.append(Paragraph(esc(join_wrapped(chunk)), S["quote"]))
            i = j
            continue
        elif re.match(r"^\d+\.\s", ln):
            story.append(Paragraph(esc(re.sub(r"^\d+\.\s*", "", ln)),
                                   S["li"], bulletText=ln.split(".")[0] + "."))
        elif ln.startswith("- "):
            story.append(Paragraph(esc(ln[2:]), S["li"], bulletText="•"))
        else:
            story.append(Paragraph(esc(ln), S["body"]))
        i += 1

    doc.build(story)
    print(f"已生成 {OUT}（{os.path.getsize(OUT) / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    main()
