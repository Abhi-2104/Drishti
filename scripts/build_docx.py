#!/usr/bin/env python3
"""Regenerate Drishti.docx from README.md.

Usage:  python3 -m venv .venv && .venv/bin/pip install python-docx
        .venv/bin/python scripts/build_docx.py
Plain, professional Word styling (no colour beyond a light table header).
"""
import re
from pathlib import Path
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "README.md"
OUT = ROOT / "Drishti.docx"

text = SRC.read_text(encoding="utf-8")

doc = Document()
normal = doc.styles["Normal"]
normal.font.name = "Calibri"; normal.font.size = Pt(11)
for i in range(1, 4):
    h = doc.styles[f"Heading {i}"]
    h.font.name = "Calibri"; h.font.bold = True
    h.font.size = Pt({1: 16, 2: 14, 3: 12}[i])

# join soft-wrapped lines within a block
raw_lines = text.split("\n")
block_start = re.compile(r"^\s*(#{1,4}\s|[-*]\s|\d+\.\s|\||>|```)")
joined, in_fence = [], False
for ln in raw_lines:
    if ln.strip().startswith("```"):
        in_fence = not in_fence; joined.append(ln); continue
    cont = (not in_fence and joined and ln.strip() and not block_start.match(ln)
            and joined[-1].strip() and joined[-1].strip() != "---"
            and not re.match(r"^\s*#{1,4}\s", joined[-1]))
    if cont:
        joined[-1] = joined[-1].rstrip() + " " + ln.strip()
    else:
        joined.append(ln)
lines = joined


def add_inline(p, seg):
    for tok in re.split(r"(\*\*.*?\*\*|\*[^*]+?\*|`.*?`)", seg):
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**"):
            r = p.add_run(tok[2:-2]); r.bold = True
        elif tok.startswith("`") and tok.endswith("`"):
            r = p.add_run(tok[1:-1]); r.font.name = "Consolas"
        elif tok.startswith("*") and tok.endswith("*") and len(tok) > 1:
            r = p.add_run(tok[1:-1]); r.italic = True
        else:
            p.add_run(tok)


def shade(cell, hexc="F2F2F2"):
    tcPr = cell._tc.get_or_add_tcPr(); shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear"); shd.set(qn("w:fill"), hexc); tcPr.append(shd)


i, in_code, buf = 0, False, []


def flush_code():
    global buf
    if not buf:
        return
    p = doc.add_paragraph(); p.paragraph_format.left_indent = Inches(0.3)
    for idx, cl in enumerate(buf):
        if idx:
            p.add_run().add_break()
        r = p.add_run(cl); r.font.name = "Consolas"; r.font.size = Pt(10)
    buf = []


while i < len(lines):
    line = lines[i]; s = line.strip()
    if s.startswith("```"):
        if in_code:
            flush_code(); in_code = False
        else:
            in_code = True
        i += 1; continue
    if in_code:
        buf.append(line); i += 1; continue
    if s == "" or s == "---":
        i += 1; continue
    if s.startswith("|"):
        tl = []
        while i < len(lines) and lines[i].strip().startswith("|"):
            tl.append(lines[i].strip()); i += 1
        rows = [[c.strip() for c in r.strip("|").split("|")] for r in tl
                if not ("---" in r and set(r.replace("|", "").replace("-", "").replace(":", "").strip()) == set())]
        header, data = rows[0], rows[1:]
        t = doc.add_table(rows=1 + len(data), cols=len(header)); t.style = "Table Grid"
        for c, htext in enumerate(header):
            cell = t.cell(0, c); cell.text = ""; rr = cell.paragraphs[0].add_run(htext); rr.bold = True; shade(cell)
        for ri, row in enumerate(data, 1):
            for c, v in enumerate(row):
                if c >= len(header):
                    continue
                cell = t.cell(ri, c); cell.text = ""; add_inline(cell.paragraphs[0], v)
        doc.add_paragraph(); continue
    m = re.match(r"^(#{1,4})\s+(.*)", line)
    if m:
        lvl, htext = len(m.group(1)), m.group(2).strip()
        if lvl == 1:
            p = doc.add_heading(level=0); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = p.add_run(htext); r.font.size = Pt(20); r.bold = True
        else:
            doc.add_heading(re.sub(r"[`*]", "", htext), level=min(lvl, 3))
        i += 1; continue
    if s.startswith(">"):
        p = doc.add_paragraph(); p.paragraph_format.left_indent = Inches(0.3)
        p.add_run(s.lstrip(">").strip()).italic = True; i += 1; continue
    m = re.match(r"^(\d+)\.\s+(.*)", s)
    if m:
        add_inline(doc.add_paragraph(style="List Number"), m.group(2)); i += 1; continue
    m = re.match(r"^[-*]\s+(.*)", s)
    if m:
        add_inline(doc.add_paragraph(style="List Bullet"), m.group(1)); i += 1; continue
    if s.startswith("*") and s.endswith("*") and not s.startswith("**"):
        p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run(s.strip("*")).italic = True; i += 1; continue
    add_inline(doc.add_paragraph(), s); i += 1

doc.save(OUT)
print("Saved:", OUT)
