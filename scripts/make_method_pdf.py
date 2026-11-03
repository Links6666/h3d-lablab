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


def setup_fonts():
    pdfmetrics.registerFont(TTFont("MSYH", "C:/Windows/Fonts/msyh.ttc",
                                   subfontIndex=0))
    pdfmetrics.registerFont(TTFont("MSYH-B", "C:/Windows/Fonts/msyhbd.ttc",
                                   subfontIndex=0))
    return "MSYH", "MSYH-B"


def esc(t: str) -> str:
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 行内代码与粗体
    t = re.sub(r"`([^`]+)`", r'<font face="MSYH" color="#8a5a2b">\1</font>', t)
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
                               textColor=INK, spaceAfter=4),
        "quote": ParagraphStyle("quote", fontName=F, fontSize=9.2, leading=14,
                                textColor=GREY, leftIndent=10,
                                borderPadding=(0, 0, 0, 8), spaceAfter=5),
        "li": ParagraphStyle("li", fontName=F, fontSize=9.6, leading=15,
                             textColor=INK, leftIndent=12, bulletIndent=2,
                             spaceAfter=2),
        "cell": ParagraphStyle("cell", fontName=F, fontSize=8.4, leading=12,
                               textColor=INK),
        "cellh": ParagraphStyle("cellh", fontName=FB, fontSize=8.4, leading=12,
                                textColor=colors.white),
        "code": ParagraphStyle("code", fontName=F, fontSize=8.4, leading=12,
                               textColor=INK, leftIndent=8),
    }


def table_flow(rows, S, width):
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    body = [[Paragraph(esc(c), S["cellh"] if i == 0 else S["cell"])
             for c in r] for i, r in enumerate(rows)]
    t = Table(body, colWidths=[width / ncol] * ncol, repeatRows=1, hAlign="LEFT")
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


def main():
    F, FB = setup_fonts()
    S = build_styles(F, FB)
    text = open(SRC, encoding="utf-8").read()

    doc = SimpleDocTemplate(OUT, pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=16 * mm,
                            title="方法学：让自动化系统的数字可信",
                            author="H3D 1.1.16-r22")
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
            story.append(Paragraph("<br/>".join(esc(b) for b in block),
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
        if ln.startswith("### "):
            story.append(Paragraph(esc(ln[4:]), S["h3"]))
        elif ln.startswith("## "):
            story.append(KeepTogether([Spacer(1, 3),
                                       Paragraph(esc(ln[3:]), S["h2"])]))
        elif ln.startswith("# "):
            story.append(Paragraph(esc(ln[2:]), S["h1"]))
            story.append(Spacer(1, 2))
        elif ln.startswith("> "):
            story.append(Paragraph(esc(ln[2:]), S["quote"]))
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


if __name__ == "__main__":
    main()
