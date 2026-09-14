#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Convert report_draft.md (Pandoc HTML) -> PDF with embedded CJK font (MS YaHei)."""
import re, html as htmlmod
from pathlib import Path
from fpdf import FPDF

DOCS = Path(r"D:\python_work\work2026-4\RAG Knowledge Base\docs")
FONT = r"C:\Windows\Fonts\msyh.ttc"
SRC = DOCS / "report_draft.html"
OUT = DOCS / "report_draft.pdf"

raw = SRC.read_text(encoding="utf-8")

# 1) Drop <style>...</style> and <head> blocks
raw = re.sub(r"<style[\s\S]*?</style>", "", raw, flags=re.I)
raw = re.sub(r"<head[\s\S]*?</head>", "", raw, flags=re.I)
# 2) Strip class/id attributes (fpdf2 ignores them but keeps noise)
raw = re.sub(r'\s(class|id)="[^"]*"', "", raw, flags=re.I)
# 3) Replace mermaid <pre> block with a readable plain-text flow box
def mermaid_repl(m):
    code = htmlmod.unescape(re.sub(r"<[^>]+>", "", m.group(1)))
    lines = [ln.rstrip() for ln in code.splitlines() if ln.strip()]
    return "<pre>" + htmlmod.escape("\n".join(lines)) + "</pre>"
raw = re.sub(r'<pre[^>]*class="[^"]*mermaid[^"]*"[^>]*>([\s\S]*?)</pre>',
             mermaid_repl, raw, flags=re.I)
# Any remaining <pre> (e.g. code fences) -> <p> to avoid courier (no CJK)
raw = re.sub(r"<pre[^>]*>", "<p>", raw, flags=re.I)
raw = raw.replace("</pre>", "</p>")
# 3.5) Drop inline tags that fpdf2 can't nest inside <td> (keep inner text)
for tag in ("code", "strong", "b", "em", "i"):
    raw = re.sub(rf"<{tag}[^>]*>", "", raw, flags=re.I)
    raw = re.sub(rf"</{tag}>", "", raw, flags=re.I)
# 4) FPDF write_html only supports a subset; convert some tags it handles:
#    <ul><ol><li>, <table>, <h1-6>, <p>, <b>/<strong>, <i>/<em>, <blockquote>, <hr>, <pre>, <br>
#    Remove <hr> (use spacing), <blockquote> -> <p>
raw = raw.replace("<hr />", "<br><br>").replace("<hr/>", "<br><br>")
raw = re.sub(r"<blockquote>([\s\S]*?)</blockquote>", r"<p>\1</p>", raw, flags=re.I)

class PDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("msyh", "", 8)
        self.set_text_color(150)
        self.cell(0, 6, "政策法规问答助手 · 项目报告", align="R")
        self.ln(8)
    def footer(self):
        self.set_y(-12)
        self.set_font("msyh", "", 8)
        self.set_text_color(150)
        self.cell(0, 8, f"第 {self.page_no()} 页", align="C")

pdf = PDF(format="A4")
pdf.set_auto_page_break(auto=True, margin=15)
pdf.add_font("msyh", "", FONT)
pdf.add_font("msyh", "B", FONT)  # same file; bold simulated via fpdf is limited, fallback ok
pdf.set_margins(18, 16, 18)

pdf.add_page()
pdf.set_font("msyh", "", 10.5)
pdf.write_html(raw)
pdf.output(str(OUT))
print("written", OUT, OUT.stat().st_size, "bytes")
