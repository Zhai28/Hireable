#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Markdown → 排版精美的 PDF（中英文混排，A4 商务报告风格）

用法:
    python3 md_to_pdf.py input.md [-o output.pdf]
        [--title "报告标题"] [--subtitle "副标题"] [--author "姓名"]
        [--kicker "JD 分析报告"] [--no-cover] [--toc] [--no-h1-break]
        [--css /path/custom.css] [--engine auto|weasyprint|chromium]
        [--keep-html]

渲染引擎:
    优先 WeasyPrint（若已安装），否则自动回退到 Chromium（Playwright）。
    两者都不可用时会报错并给出安装提示。

依赖:
    必需: python3 + markdown
    二选一: weasyprint  或  playwright(chromium)
"""

import argparse
import datetime
import html as html_mod
import os
import re
import sys

try:
    import markdown
except ImportError:
    sys.exit("缺少依赖: pip install markdown --break-system-packages")

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSS = os.path.join(HERE, "..", "assets", "business.css")

MD_EXTENSIONS = ["tables", "fenced_code", "sane_lists", "attr_list", "toc", "nl2br"]
MD_CONFIG = {"toc": {"permalink": False}}

# ── 结论框关键词 → 样式 ────────────────────────────────
CALLOUT_STYLE = [
    (r"不建议|高风险|红线|谨慎|风险提示|避免", "risk", "风险提示"),
    (r"注意|留意|需要确认|待验证|不确定", "warn", "注意"),
    (r"强烈建议|建议投递|推荐|优先|结论|建议", "good", "结论与建议"),
]
DIRECTIVE_MAP = {
    "结论": ("good", "结论与建议"), "conclusion": ("good", "结论与建议"),
    "建议": ("good", "结论与建议"), "good": ("good", "结论与建议"),
    "提示": ("", "提示"), "tip": ("", "提示"), "info": ("", "提示"),
    "注意": ("warn", "注意"), "warn": ("warn", "注意"), "warning": ("warn", "注意"),
    "风险": ("risk", "风险提示"), "risk": ("risk", "风险提示"),
    "要点": ("key", "关键要点"), "key": ("key", "关键要点"),
}

SCORE_KEYWORDS = r"匹配度|契合度|评分|得分|分值|成功概率|推荐指数|score|fit|rating"


def strip_front_matter(text):
    if text.startswith("---"):
        m = re.match(r"^---\s*\n.*?\n---\s*\n", text, re.S)
        if m:
            return text[m.end():]
    return text


def render_md(text):
    md = markdown.Markdown(extensions=MD_EXTENSIONS, extension_configs=MD_CONFIG)
    return md.convert(text), md


class Slot:
    """把特殊块先挖出来，渲染完 markdown 再填回去。"""

    def __init__(self):
        self.items = []

    def add(self, html):
        self.items.append(html)
        return "\n\nXSLOTX%dXENDX\n\n" % (len(self.items) - 1)

    def fill(self, doc_html):
        for i, frag in enumerate(self.items):
            doc_html = re.sub(
                r"<p>\s*XSLOTX%dXENDX\s*</p>" % i, lambda _m: frag, doc_html
            )
            doc_html = doc_html.replace("XSLOTX%dXENDX" % i, frag)
        return doc_html


def build_score_card(label, value, denom, note=""):
    pct = max(0.0, min(100.0, float(value) / float(denom) * 100))
    cls = "good" if pct >= 75 else ("mid" if pct >= 50 else "low")
    val_txt = ("%g" % float(value))
    note_html = '<div class="score-note">%s</div>' % note if note else ""
    return (
        '<div class="score-card">'
        '<div class="score-head"><span class="score-label">{label}</span>'
        '<span class="score-value">{val}<span class="denom">/{den}</span></span></div>'
        '<div class="score-track"><div class="score-fill {cls}" style="width:{pct:.1f}%"></div></div>'
        "{note}</div>"
    ).format(label=html_mod.escape(label), val=val_txt, den=("%g" % float(denom)),
             cls=cls, pct=pct, note=note_html)


def extract_directives(text, slot):
    """:::结论 ... :::  →  结论框"""
    pattern = re.compile(r"^:::[ \t]*([A-Za-z一-龥]+)[ \t]*\n(.*?)^:::[ \t]*$",
                         re.S | re.M)

    def repl(m):
        name, body = m.group(1).strip(), m.group(2)
        cls, title = DIRECTIVE_MAP.get(name.lower(), DIRECTIVE_MAP.get(name, ("", name)))
        inner, _ = render_md(body.strip())
        return slot.add('<div class="callout %s"><div class="callout-title">%s</div>%s</div>'
                        % (cls, html_mod.escape(title), inner))

    return pattern.sub(repl, text)


SCORE_LINE = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+|\d+\.[ \t]+)?"          # 可选列表符号
    r"(?:\*\*|__)?[ \t]*"                            # 可选加粗
    r"(?P<label>[^\n:：*]{0,24}?(?:%s)[^\n:：*]{0,12}?)"  # 标签
    r"[ \t]*(?:\*\*|__)?[ \t]*[:：][ \t]*"
    r"(?:\*\*|__)?[ \t]*"
    r"(?P<val>\d+(?:\.\d+)?)[ \t]*/[ \t]*(?P<den>\d+(?:\.\d+)?)"
    r"[ \t]*(?:\*\*|__)?[ \t]*(?P<note>[^\n]*)$" % SCORE_KEYWORDS,
    re.M | re.I,
)


def extract_scores(text, slot):
    """把「综合匹配度：8/10」这类行渲染成可视化评分条（表格内的不处理）。"""
    lines = text.split("\n")
    out = []
    in_fence = False
    for line in lines:
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence or line.lstrip().startswith("|") or line.lstrip().startswith(">"):
            out.append(line)
            continue
        m = SCORE_LINE.match(line)
        if m:
            note = re.sub(r"^[（(]|[)）]$", "", m.group("note").strip()).strip("　 -—·")
            card = build_score_card(m.group("label").strip(" *_"),
                                    m.group("val"), m.group("den"), note)
            out.append(slot.add(card))
        else:
            out.append(line)
    return "\n".join(out)


CONCLUSION_BLOCK = re.compile(
    r"(?:^|\n)(?P<block>(?:\*\*|__)[ \t]*(?:投递建议|建议|结论|总体判断|Recommendation|Verdict)"
    r"[^\n]*?(?:\*\*|__)[^\n]*(?:\n(?!\n)[^\n]+)*)",
    re.M | re.I,
)


def extract_conclusions(text, slot):
    def repl(m):
        block = m.group("block")
        cls, title = "good", "结论与建议"
        for pat, c, t in CALLOUT_STYLE:
            if re.search(pat, block):
                cls, title = c, t
                break
        inner, _ = render_md(block.strip())
        return "\n" + slot.add(
            '<div class="callout %s"><div class="callout-title">%s</div>%s</div>'
            % (cls, html_mod.escape(title), inner))

    return CONCLUSION_BLOCK.sub(repl, text)


def style_blockquotes(doc_html):
    """含结论/建议关键词的引用块升级为结论框。"""
    def repl(m):
        inner = m.group(1)
        plain = re.sub(r"<[^>]+>", "", inner)
        for pat, cls, title in CALLOUT_STYLE:
            if re.search(pat, plain):
                return ('<div class="callout %s"><div class="callout-title">%s</div>%s</div>'
                        % (cls, html_mod.escape(title), inner))
        return m.group(0)

    return re.sub(r"<blockquote>(.*?)</blockquote>", repl, doc_html, flags=re.S)


def guess_kicker(text):
    head = text[:2500]
    rules = [
        (r"JD|岗位描述|职位描述|匹配度|Hiring Manager", "JD 分析报告"),
        (r"公司调研|竞争格局|企业背景|组织架构|公司研究", "公司调研报告"),
        (r"市场调研|行业趋势|市场规模|薪酬区间|人才市场", "市场调研报告"),
        (r"简历|Resume|CV", "简历分析"),
        (r"面试|Interview|STAR", "面试准备"),
        (r"薪酬谈判|Offer|谈薪", "薪酬谈判"),
    ]
    for pat, label in rules:
        if re.search(pat, head, re.I):
            return label
    return "分析报告"


def extract_title(text):
    m = re.search(r"^#[ \t]+(.+?)[ \t]*$", text, re.M)
    if m:
        return m.group(1).strip(), text[:m.start()] + text[m.end():]
    return None, text


def build_cover(title, subtitle, kicker, author, date_str):
    meta_rows = []
    if author:
        meta_rows.append('<div><span class="k">编制</span>%s</div>' % html_mod.escape(author))
    meta_rows.append('<div><span class="k">日期</span>%s</div>' % date_str)
    sub = '<div class="cover-sub">%s</div>' % html_mod.escape(subtitle) if subtitle else ""
    return (
        '<section class="cover">'
        '<div class="cover-rule"></div>'
        '<div class="cover-kicker">%s</div>'
        '<h1 class="cover-title">%s</h1>%s'
        '<div class="cover-meta"><hr class="cover-meta-rule">%s</div>'
        "</section>"
    ) % (html_mod.escape(kicker), html_mod.escape(title), sub, "".join(meta_rows))


def convert(args):
    with open(args.input, encoding="utf-8") as f:
        raw = f.read()
    text = strip_front_matter(raw)

    doc_title, text = extract_title(text)
    title = args.title or doc_title or os.path.splitext(os.path.basename(args.input))[0]
    kicker = args.kicker or guess_kicker(raw)
    date_str = args.date or datetime.date.today().strftime("%Y 年 %m 月 %d 日")

    slot = Slot()
    text = extract_directives(text, slot)
    text = extract_conclusions(text, slot)
    text = extract_scores(text, slot)

    body, md = render_md(text)
    body = style_blockquotes(body)
    body = slot.fill(body)

    toc_html = ""
    if args.toc:
        toc_body = getattr(md, "toc", "")
        if toc_body.strip():
            toc_html = '<section class="toc"><h2>目录</h2>%s</section>' % toc_body

    cover = "" if args.no_cover else build_cover(
        title, args.subtitle, kicker, args.author, date_str)

    css_path = args.css or DEFAULT_CSS
    with open(css_path, encoding="utf-8") as f:
        css = f.read()
    header_text = (args.header if args.header is not None else title)
    if len(header_text) > 46:
        header_text = header_text[:45] + "…"
    css = css.replace("__HEADER_TEXT__",
                      header_text.replace("\\", "\\\\").replace('"', '\\"'))
    if args.no_h1_break:
        css += "\nh1 { page-break-before: auto !important; break-before: auto !important; }\n"
    if args.no_cover:
        css += "\nh1:first-of-type { page-break-before: auto !important; break-before: auto !important; }\n"

    doc = (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>%s</title><style>%s</style></head><body>%s%s%s</body></html>"
    ) % (html_mod.escape(title), css, cover, toc_html, body)

    out = args.output or os.path.splitext(args.input)[0] + ".pdf"
    html_path = os.path.splitext(out)[0] + ".html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(doc)

    engine = render(doc, html_path, out, title, args.engine)
    if not args.keep_html:
        try:
            os.remove(html_path)
        except OSError:
            pass
    return out, engine


def render(doc, html_path, out, title, engine_pref):
    errors = []
    if engine_pref in ("auto", "weasyprint"):
        try:
            from weasyprint import HTML
            HTML(string=doc, base_url=os.path.dirname(html_path) or ".").write_pdf(out)
            return "weasyprint"
        except Exception as e:  # noqa: BLE001
            errors.append("weasyprint: %s" % e)
            if engine_pref == "weasyprint":
                sys.exit("WeasyPrint 渲染失败：%s" % e)

    if engine_pref in ("auto", "chromium"):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto("file://" + os.path.abspath(html_path), wait_until="load")
                page.emulate_media(media="print")
                # Chromium 支持 CSS @page 页边距框，页眉页脚全部交给 CSS，
                # 这样封面页可以通过 @page :first 单独去掉页眉页脚。
                page.pdf(path=out, print_background=True,
                         prefer_css_page_size=True,
                         display_header_footer=False)
                browser.close()
            return "chromium"
        except Exception as e:  # noqa: BLE001
            errors.append("chromium: %s" % e)

    sys.exit("无可用渲染引擎。请安装其一：\n"
             "  pip install weasyprint --break-system-packages\n"
             "  pip install playwright --break-system-packages && playwright install chromium\n"
             "错误详情：\n  " + "\n  ".join(errors))


def main():
    ap = argparse.ArgumentParser(description="Markdown → 商务风格 PDF")
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--title")
    ap.add_argument("--subtitle")
    ap.add_argument("--author")
    ap.add_argument("--header", help="页眉文字，默认取报告标题")
    ap.add_argument("--kicker", help="封面上方的小标签，如「JD 分析报告」")
    ap.add_argument("--date")
    ap.add_argument("--css")
    ap.add_argument("--no-cover", action="store_true")
    ap.add_argument("--toc", action="store_true")
    ap.add_argument("--no-h1-break", action="store_true",
                    help="不在每个一级标题前强制分页（短报告建议加）")
    ap.add_argument("--engine", default="auto",
                    choices=["auto", "weasyprint", "chromium"])
    ap.add_argument("--keep-html", action="store_true")
    args = ap.parse_args()

    out, engine = convert(args)
    size = os.path.getsize(out) / 1024
    print("✅ 已生成 %s（%.0f KB，引擎：%s）" % (out, size, engine))


if __name__ == "__main__":
    main()
