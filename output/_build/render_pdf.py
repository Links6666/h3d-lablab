# -*- coding: utf-8 -*-
"""reportlab 通用渲染器：content.json（结构化块）→ PDF 代码说明书。

块类型（与 gen_*.py 约定一致）：
    h1 / h2          章节标题（h1 分页 + 目录；h2 子标题 + 目录）
    body             段落（支持 <b> 加粗）
    code             代码块（language: python/text/bash，pygments 高亮，CJK 字体）
    table            表格（headers + rows）
    callout          高亮提示框（深蓝左边线 + 浅底）
    flowchart        自顶向下流程图（nodes + edges + caption）

用法：
    python render_pdf.py content.json output.pdf "页眉标题" "页脚单位"

字体：微软雅黑（CJK），代码块同样用 CJK 字体以避免中文 / 树形字符豆腐块。
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table,
    TableStyle, XPreformatted, HRFlowable, PageBreak, Flowable,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus.tableofcontents import TableOfContents

import pygments
from pygments.lexers import get_lexer_by_name
from pygments.token import Token

# ---------------------------------------------------------------------------
# 字体与配色
# ---------------------------------------------------------------------------
FONT_REG = r"C:/Windows/Fonts/msyh.ttc"
FONT_BOLD = r"C:/Windows/Fonts/msyhbd.ttc"
pdfmetrics.registerFont(TTFont("CJK", FONT_REG, subfontIndex=0))
pdfmetrics.registerFont(TTFont("CJK-Bold", FONT_BOLD, subfontIndex=0))
pdfmetrics.registerFontFamily("CJK", normal="CJK", bold="CJK-Bold",
                              italic="CJK", boldItalic="CJK-Bold")

NAVY = colors.HexColor("#1f3a5f")
STEEL = colors.HexColor("#2a4d7a")
GREY = colors.HexColor("#555555")
LIGHT = colors.HexColor("#f4f5f7")
BORDER = colors.HexColor("#cccccc")
CODEBG = colors.HexColor("#f6f8fa")
CALLOUT_BG = colors.HexColor("#eef3fa")

TOKEN_COLORS = {
    Token.Keyword: "#0033b3", Token.Keyword.Constant: "#0033b3",
    Token.Keyword.Type: "#0033b3", Token.Name: "#000000",
    Token.Name.Builtin: "#6f42c1", Token.Name.Function: "#6f42c1",
    Token.Name.Class: "#6f42c1", Token.Name.Decorator: "#6f42c1",
    Token.String: "#0a7d2c", Token.String.Doc: "#0a7d2c",
    Token.Number: "#1750eb", Token.Comment: "#8a8a8a",
    Token.Operator.Word: "#0033b3", Token.Text: "#000000",
}
DEFAULT_TOKEN_COLOR = "#000000"

STYLES = {
    "body": ParagraphStyle("body", fontName="CJK", fontSize=10, leading=16,
                           alignment=TA_JUSTIFY, spaceAfter=6, wordWrap="CJK"),
    "h1": ParagraphStyle("h1", fontName="CJK-Bold", fontSize=17, leading=23,
                         textColor=NAVY, spaceBefore=4, spaceAfter=10,
                         wordWrap="CJK"),
    "h2": ParagraphStyle("h2", fontName="CJK-Bold", fontSize=13, leading=18,
                         textColor=NAVY, spaceBefore=12, spaceAfter=6,
                         wordWrap="CJK"),
    "code": ParagraphStyle("code", fontName="CJK", fontSize=8.2, leading=11.5,
                           textColor=colors.black, leftIndent=4,
                           backColor=CODEBG, borderPadding=6, spaceAfter=8,
                           splitLongWords=True, wordWrap=None),
    "cell": ParagraphStyle("cell", fontName="CJK", fontSize=8.5, leading=12,
                           wordWrap="CJK"),
    "cellh": ParagraphStyle("cellh", fontName="CJK-Bold", fontSize=8.5,
                            leading=12, textColor=colors.white, wordWrap="CJK"),
    "toc1": ParagraphStyle("toc1", fontName="CJK-Bold", fontSize=12,
                           leading=20, textColor=NAVY),
    "toc2": ParagraphStyle("toc2", fontName="CJK", fontSize=10.5,
                           leading=17, leftIndent=16, textColor=colors.black),
    "caption": ParagraphStyle("caption", fontName="CJK", fontSize=8.5,
                              leading=12, textColor=GREY, alignment=TA_CENTER,
                              spaceBefore=2, spaceAfter=8),
}


def _iter_strings(obj):
    """递归取出 content 里的所有字符串（用于缺字预检）。"""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _iter_strings(v)


_CMAP_CACHE: set[int] | None = None


def missing_glyphs(content) -> list[str]:
    """返回正文字体（微软雅黑）里没有的字符——它们会被渲染成方框。

    2026-10-18：审计报告里用了「↔」（U+2194）和「▶」（U+25B6），msyh.ttc
    两者都没有，PDF 里直接出现方框豆腐块，而按「\\u25a1 计数」的常规校验
    查不出来（那是另一个码位）。故在渲染前做一次真实 cmap 覆盖检查。
    """
    global _CMAP_CACHE
    if _CMAP_CACHE is None:
        try:
            from fontTools.ttLib import TTCollection
            _CMAP_CACHE = set(TTCollection(FONT_REG).fonts[0].getBestCmap())
        except Exception:  # noqa: BLE001 - 预检失败不影响出 PDF
            _CMAP_CACHE = set()
    if not _CMAP_CACHE:
        return []
    chars = {c for s in _iter_strings(content) for c in s}
    miss = sorted({c for c in chars
                   if ord(c) > 127 and ord(c) not in _CMAP_CACHE})
    return miss


def _esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(t: str) -> str:
    """行内标记：保留 <b> / <code>，其余 HTML 实体转义。

    2026-10-18：新增 <code> 支持。此前 <code> 会被当作普通文本转义，
    PDF 里直接印出「<code>xxx</code>」字面量——技术文档里非常刺眼。
    这里不做等宽字体替换：全文用微软雅黑（CJK），换成 Courier 会让
    含中文的代码片段（如「造像 → 文物」）出现豆腐块。
    改以「深蓝字 + 浅灰底」区分行内代码，中英文都安全。
    """
    parts = re.split(r"(<b>|</b>|<code>|</code>)", t)
    out = []
    for p in parts:
        if p in ("<b>", "</b>"):
            out.append(p)
        elif p == "<code>":
            out.append('<font color="#1f3a5f" backColor="#eef1f5">')
        elif p == "</code>":
            out.append("</font>")
        else:
            out.append(_esc(p))
    return "".join(out)


def _highlight(code: str, lang: str) -> str:
    code = code.replace("\t", "    ")
    try:
        lexer = get_lexer_by_name(lang, stripall=False)
    except Exception:
        lexer = None
    if lexer is None:
        return _esc(code)
    out = []
    for tok_type, tok_text in pygments.lex(code, lexer):
        color = TOKEN_COLORS.get(tok_type, DEFAULT_TOKEN_COLOR)
        if color == DEFAULT_TOKEN_COLOR:
            for parent in tok_type.split():
                c = TOKEN_COLORS.get(parent)
                if c:
                    color = c
                    break
        out.append('<font color="%s">%s</font>' % (color, _esc(tok_text)))
    return "".join(out)


# ---------------------------------------------------------------------------
# 流程图 Flowable（自顶向下）
# ---------------------------------------------------------------------------
class Flowchart(Flowable):
    """把节点列表渲染为自顶向下的流程图（线性链 + 向下箭头）。"""

    def __init__(self, nodes: list[dict], edges: list[dict], caption: str):
        super().__init__()
        self.nodes = nodes
        self.edges = edges
        self.caption = caption
        self.box_w = 300
        self.box_h = 30
        self.gap = 24

    def _order(self) -> list[dict]:
        """按 edges 计算线性顺序（首节点 → 链尾），兼容显式拓扑。"""
        ids = [n["id"] for n in self.nodes]
        by_id = {n["id"]: n for n in self.nodes}
        indeg = {i: 0 for i in ids}
        adj: dict[str, list[str]] = {i: [] for i in ids}
        for e in self.edges:
            if e["from"] in adj and e["to"] in adj:
                adj[e["from"]].append(e["to"])
                indeg[e["to"]] += 1
        # 拓扑排序（保证确定性）
        order, frontier = [], sorted([i for i in ids if indeg[i] == 0])
        while frontier:
            cur = frontier.pop(0)
            order.append(cur)
            for nxt in adj[cur]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    frontier.append(nxt)
                    frontier.sort()
        if len(order) != len(ids):  # 有环或孤立：回退为原始顺序
            order = ids
        return [by_id[i] for i in order]

    def wrap(self, availWidth, availHeight):
        self.box_w = min(availWidth * 0.78, 340)
        self.height = len(self.nodes) * self.box_h \
            + (len(self.nodes) - 1) * self.gap
        self.width = self.box_w
        return self.box_w, self.height

    def draw(self):
        canvas = self.canv
        ordered = self._order()
        x0 = (self.width - self.box_w) / 2.0
        y = self.height  # 自顶向下：从最高处开始
        centers = []
        for node in ordered:
            top = y - self.box_h
            shape = node.get("shape", "rect")
            self._draw_box(canvas, x0, top, self.box_w, self.box_h,
                           node.get("label", ""), shape)
            centers.append((x0 + self.box_w / 2.0, top + self.box_h / 2.0))
            y = top - self.gap
        # 箭头
        canvas.setStrokeColor(NAVY)
        canvas.setLineWidth(1.1)
        for i in range(len(centers) - 1):
            (x1, y1), (x2, y2) = centers[i], centers[i + 1]
            # 2026-10-18：原实现从**框心**连到**框心**，竖线穿过节点文字，
            # 看上去像划掉了每一行标签。改为「上框下沿 → 下框上沿」，
            # 箭头 tip 落在下框上沿（_arrow 的 ang 由 tail→tip 计算）。
            top_edge = y1 - self.box_h / 2.0
            bot_edge = y2 + self.box_h / 2.0
            canvas.line(x1, top_edge, x1, bot_edge)
            self._arrow(canvas, x1, bot_edge, x1, top_edge)

    def _draw_box(self, canvas, x, y, w, h, label, shape):
        canvas.saveState()
        if shape == "parallelogram":
            skew = 14
            p = canvas.beginPath()
            p.moveTo(x + skew, y + h)
            p.lineTo(x + w, y + h)
            p.lineTo(x + w - skew, y)
            p.lineTo(x, y)
            p.close()
            canvas.setFillColor(LIGHT)
            canvas.setStrokeColor(STEEL)
            canvas.drawPath(p, stroke=1, fill=1)
        else:
            canvas.setFillColor(LIGHT)
            canvas.setStrokeColor(STEEL)
            canvas.setLineWidth(1.0)
            canvas.roundRect(x, y, w, h, 6, stroke=1, fill=1)
        canvas.setFillColor(colors.black)
        canvas.setFont("CJK", 8.5)
        canvas.drawCentredString(x + w / 2.0, y + h / 2.0 - 3, label)
        canvas.restoreState()

    @staticmethod
    def _arrow(canvas, x2, y2, x1, y1):
        import math
        ang = math.atan2(y2 - y1, x2 - x1)
        ah = 6
        canvas.saveState()
        canvas.setFillColor(NAVY)
        p = canvas.beginPath()
        p.moveTo(x2, y2)
        p.lineTo(x2 - ah * math.cos(ang - 0.5), y2 - ah * math.sin(ang - 0.5))
        p.lineTo(x2 - ah * math.cos(ang + 0.5), y2 - ah * math.sin(ang + 0.5))
        p.close()
        canvas.drawPath(p, stroke=0, fill=1)
        canvas.restoreState()


# ---------------------------------------------------------------------------
# 文档模板
# ---------------------------------------------------------------------------
class _Doc(BaseDocTemplate):
    def __init__(self, filename, header, footer, **kw):
        super().__init__(filename, **kw)
        self.header = header
        self.footer = footer
        self._cur_heading = ""

    def afterFlowable(self, flowable):
        level = getattr(flowable, "_toc_level", None)
        text = getattr(flowable, "_toc_text", None)
        if level is not None and text is not None:
            self.notify("TOCEntry", (level, text, self.page, None))
            self._cur_heading = text


def build_doc(filename, header, footer, side=None):
    doc = _Doc(filename, header, footer, pagesize=A4,
               leftMargin=2.0 * cm, rightMargin=2.0 * cm,
               topMargin=2.4 * cm, bottomMargin=2.0 * cm,
               title=header, author="文保数字化研发组")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  id="main")
    side_text = side or ""

    def on_page(canvas, d):
        canvas.saveState()
        canvas.setFont("CJK", 8)
        canvas.setFillColor(GREY)
        canvas.drawString(2.0 * cm, A4[1] - 1.4 * cm, d.header)
        # 右侧固定显示日期：原先用 d._cur_heading（当前小节标题），但 multiBuild
        # 会渲染两遍，第二遍开始时该变量还停留在**上一遍最后一个小节**，
        # 导致每页页眉都显示「8.3 后续方向」这类错位文本。
        canvas.drawRightString(A4[0] - 2.0 * cm, A4[1] - 1.4 * cm,
                               side_text)
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(0.4)
        canvas.line(2.0 * cm, A4[1] - 1.6 * cm, A4[0] - 2.0 * cm, A4[1] - 1.6 * cm)
        canvas.setFont("CJK", 8)
        canvas.setFillColor(GREY)
        canvas.drawString(2.0 * cm, 1.1 * cm, d.footer)
        canvas.drawRightString(A4[0] - 2.0 * cm, 1.1 * cm, "第 %d 页" % d.page)
        canvas.line(2.0 * cm, 1.4 * cm, A4[0] - 2.0 * cm, 1.4 * cm)
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="main", frames=[frame],
                                       onPage=on_page)])
    return doc


# ---------------------------------------------------------------------------
# 封面
# ---------------------------------------------------------------------------
def cover(header: str, subtitle: str, org: str, date: str):
    st_kicker = ParagraphStyle("k", fontName="CJK", fontSize=10, leading=14,
                               textColor=STEEL, alignment=TA_CENTER)
    st_title = ParagraphStyle("t", fontName="CJK-Bold", fontSize=24, leading=34,
                              textColor=NAVY, alignment=TA_CENTER)
    st_sub = ParagraphStyle("s", fontName="CJK", fontSize=12, leading=18,
                            textColor=GREY, alignment=TA_CENTER)
    return [
        Spacer(1, 3.2 * cm),
        Paragraph("REPORT &nbsp;·&nbsp; " + date, st_kicker),
        Spacer(1, 1.2 * cm),
        Paragraph(header, st_title),
        Spacer(1, 0.5 * cm),
        Paragraph(subtitle, st_sub),
        Spacer(1, 3.6 * cm),
        HRFlowable(width="55%", thickness=1.0, color=NAVY, hAlign="CENTER"),
        Spacer(1, 1.0 * cm),
        Paragraph(org, st_sub),
        Paragraph(date, st_sub),
        PageBreak(),
    ]


# ---------------------------------------------------------------------------
# 内容渲染
# ---------------------------------------------------------------------------
def _table(block: dict):
    headers = block.get("headers", [])
    rows = block.get("rows", [])
    ncol = len(headers)
    data = [[Paragraph(_inline(h), STYLES["cellh"]) for h in headers]]
    for r in rows:
        r = (r + [""] * ncol)[:ncol]
        data.append([Paragraph(_inline(c), STYLES["cell"]) for c in r])
    avail = A4[0] - 4.0 * cm
    colw = [avail / ncol] * ncol
    t = Table(data, colWidths=colw, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _callout(block: dict):
    text = block.get("text", "")
    st = ParagraphStyle("callout", parent=STYLES["body"], fontName="CJK",
                        fontSize=9.5, leading=14.5, textColor=colors.black,
                        backColor=CALLOUT_BG, borderColor=STEEL, borderWidth=1,
                        borderPadding=8, leftIndent=0, spaceAfter=8)
    t = Table([[Paragraph(_inline(text), st)]], colWidths=[A4[0] - 4.0 * cm])
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, -1), CALLOUT_BG),
        ("LINEBEFORE", (0, 0), (0, -1), 3, NAVY),
    ]))
    return t


def render(content: list[dict]) -> list:
    flow = []
    first_h1 = True
    for block in content:
        typ = block.get("type")
        if typ == "h1":
            if not first_h1:
                flow.append(PageBreak())
            first_h1 = False
            p = Paragraph(_inline(block["text"]), STYLES["h1"])
            p._toc_level, p._toc_text = 0, block["text"]
            flow.append(p)
        elif typ == "h2":
            p = Paragraph(_inline(block["text"]), STYLES["h2"])
            p._toc_level, p._toc_text = 1, block["text"]
            flow.append(p)
        elif typ == "body":
            flow.append(Paragraph(_inline(block["text"]), STYLES["body"]))
        elif typ == "code":
            marked = _highlight(block.get("text", ""), block.get("language", "text"))
            flow.append(XPreformatted(marked, STYLES["code"]))
        elif typ == "table":
            flow.append(_table(block))
            flow.append(Spacer(1, 4))
        elif typ == "callout":
            flow.append(_callout(block))
        elif typ == "flowchart":
            nodes = block.get("nodes") or []
            if not nodes:
                # 2026-10-18：曾出现内容脚本误用 {"type":"flowchart","text":...}
                # 的情况——Flowchart 收到空 nodes，wrap() 算出负高度，
                # 渲染出来是一行与前后正文重叠的乱码。缺 nodes 直接跳过，
                # 并在 stderr 留痕，避免"看起来像渲染 bug"。
                sys.stderr.write("[render] 跳过无 nodes 的 flowchart 块\n")
                continue
            fc = Flowchart(nodes, block.get("edges", []),
                           block.get("caption", ""))
            flow.append(Spacer(1, 2))
            flow.append(fc)
            if block.get("caption"):
                flow.append(Paragraph(_inline(block["caption"]), STYLES["caption"]))
    return flow


def main():
    if len(sys.argv) < 3:
        print("Usage: python render_pdf.py <content.json> <output.pdf> "
              "[header] [subtitle] [org] [date]")
        sys.exit(1)
    src, out = sys.argv[1], sys.argv[2]
    header = sys.argv[3] if len(sys.argv) > 3 else "代码说明书"
    subtitle = sys.argv[4] if len(sys.argv) > 4 else "代码说明书"
    footer = sys.argv[5] if len(sys.argv) > 5 else "文保数字化研发组"
    date = sys.argv[6] if len(sys.argv) > 6 else "2026年8月"
    with open(src, "r", encoding="utf-8") as f:
        content = json.load(f)

    miss = missing_glyphs(content)
    if miss:
        sys.stderr.write(
            "[render] 警告：字体缺字 " + " ".join(f"{c}(U+{ord(c):04X})"
                                                 for c in miss)
            + " —— 这些字符会渲染成方框，请换用同义字或 cmap 内符号\n")

    flow = render(content)
    toc = TableOfContents()
    toc.levelStyles = [STYLES["toc1"], STYLES["toc2"]]
    toc_flow = [
        Spacer(1, 2.0 * cm),
        Paragraph("目 录", ParagraphStyle("tocTitle", fontName="CJK-Bold",
                   fontSize=22, leading=28, textColor=NAVY, alignment=TA_CENTER)),
        Spacer(1, 0.6 * cm),
        toc,
        PageBreak(),
    ]
    doc = build_doc(out, header, footer, date)
    doc.multiBuild(cover(header, subtitle, footer, date) + toc_flow + flow)
    import os
    print("OK: %s (%d bytes)" % (out, os.path.getsize(out)))


if __name__ == "__main__":
    main()
