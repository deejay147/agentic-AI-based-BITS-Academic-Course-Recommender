"""Handouts (Part II) -> evaluation pattern, makeup + attendance policy, topics.

Every IC uses their own template so there's no single table format to parse. Doing it
with rules on the pdftotext output, and every field keeps the line it came from
(`evidence`) so the app can quote the handout instead of just saying yes/no.

What we pull out:
  evaluation  components + weights (best effort), has_midsem / compre / quiz / project /
              lab / viva, open book or not
  makeup      none | restricted | available | not_mentioned
  attendance  not_required | required | expected | mentioned | not_mentioned
  topics      description + lecture plan text, for matching interests later

Cross-listed courses share one identical pdf (EEE/ECE/INSTR F211 etc) - hashed and
processed once. The one scanned handout (MATH/MAC F214) goes through tesseract.

A LLM pass would probably be more accurate on the weird templates, but this runs
without an API key and is reproducible, which matters more for now.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ingest.common import HANDOUT_DIR, dump_json, norm_code

SOURCE_DIR = "handouts/"

# ------------------------------------------------------------------ text

def pdf_text(path: Path) -> tuple[str, str]:
    """Return (text, method). Prefers poppler's layout mode (keeps table rows on one line)."""
    txt = ""
    if shutil.which("pdftotext"):
        txt = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True).stdout
        method = "pdftotext"
    else:
        import pymupdf
        with pymupdf.open(path) as doc:
            txt = "\n".join(p.get_text() for p in doc)
        method = "pymupdf"
    if len(txt.strip()) < 200:
        ocr = _ocr(path)
        if ocr:
            return ocr, "ocr"
    return txt, method


def _ocr(path: Path) -> str:
    if not shutil.which("tesseract"):
        return ""
    import pymupdf
    out = []
    with pymupdf.open(path) as doc, tempfile.TemporaryDirectory() as td:
        for i, page in enumerate(doc):
            img = Path(td) / f"p{i}.png"
            page.get_pixmap(dpi=250).save(img)
            r = subprocess.run(["tesseract", str(img), "-", "--psm", "6"], capture_output=True, text=True)
            out.append(r.stdout)
    return "\n".join(out)


# ------------------------------------------------------------------ helpers

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _sentences_with(text: str, pattern: str, limit=3) -> list[str]:
    flat = _clean(text)
    sents = re.split(r"(?<=[.;])\s+(?=[A-Z(*•])", flat)
    hits = [s for s in sents if re.search(pattern, s, re.I)]
    return [h[:400] for h in hits[:limit]]


HEADING = re.compile(r"^\s*(\d{1,2}\s*[.)]|[IVX]{1,4}\.|[A-Z][A-Za-z /&-]{2,40}:)\s*\S")


HEADING_WORDS = r"chamber|consultation|notice|grading|academic|plagiarism|attendance|course\s+(plan|description|title|no)|evaluation|make[\s-]*up|note|nc\b|important|text\s*book|reference|lecture|instructor|polic|honesty|closing|hours|objective|scope|learning|outcome|syllabus|lab\s+(plan|schedule)"


def _is_heading(line: str) -> bool:
    """A numbered heading such as '9. Make-up Policy:' (not a numbered table row like '1. Quiz 20 min 20')."""
    m = re.match(r"^\s*(\d{1,2})\s*[.)]\s*(.{2,70})$", line.strip())
    if not m:
        return False
    rest = m.group(2)
    return bool(re.match(r"^[^\d]{0,6}(" + HEADING_WORDS + ")", rest, re.I)) and not re.search(r"\d{1,3}\s*%|\bmin\b", rest, re.I)


def _section(lines: list[str], start_pat: str, max_lines=40) -> tuple[int, list[str]]:
    """Lines from the first line matching start_pat up to the next numbered heading."""
    for i, l in enumerate(lines):
        if re.search(start_pat, l, re.I):
            out = [l]
            for l2 in lines[i + 1: i + 1 + max_lines]:
                if _is_heading(l2) and not re.search(start_pat, l2, re.I):
                    break
                out.append(l2)
            return i, out
    return -1, []


EVAL_START = r"evaluation\s*(scheme|components?|plan|pattern|policy|criteria)|components?\s+of\s+evaluation|^\s*(sl\.?\s*no\.?\s*)?(evaluation\s+)?components?\s{2,}.*(weight|marks|%)|weightage"
EVAL_END = r"chamber|consultation|notices?\b|make[\s-]*up|academic\s+honesty|plagiarism|grading\s+(policy|procedure)|nc\s+policy"

MIDSEM = r"mid[\s-]*sem|mid[\s-]*term|midterm|mid\s+semester|\bmst\b|mid[\s-]*semester"
COMPRE = r"compre|comprehensive|end[\s-]*sem(ester)?\s*(exam|test)?|final\s+exam"
QUIZ = r"quiz|quizzes|class\s+tests?|surprise\s+tests?|\bCTs?\b"
ASSIGN = r"assignment|home\s*work|take[\s-]*home"
PROJECT = r"project|term\s+paper"
LAB = r"\blab\b|laboratory|practical|experiment"
VIVA = r"viva|presentation|seminar"
OPENBOOK = r"open[\s-]*book|\bOB\b"


def parse_evaluation(lines: list[str]) -> dict:
    i, sec = _section(lines, EVAL_START, max_lines=45)
    found = i >= 0
    if found:
        # stop at the first 'end' heading after the start
        cut = []
        for l in sec:
            if cut and re.search(EVAL_END, l, re.I) and (_is_heading(l) or re.match(r"^\s*[A-Z][A-Za-z /&-]{2,40}:", l)):
                break
            cut.append(l)
        sec = cut
    region = "\n".join(sec) if found else ""
    comps = []
    for l in sec:
        s = l.strip()
        if not s or re.search(r"weightage|component\s", s, re.I) and not re.search(r"\d", s):
            continue
        # treat the line as table cells (2+ spaces = new cell). first cell is the component name,
        # weight = first later cell that is just a number (optionally with % / marks)
        cells = re.split(r"\s{2,}", s)
        name = re.sub(r"^[^\w(]+", "", cells[0])  # bullets like \uf0a7, •, -
        name = re.sub(r"^(\d{1,2}[.)]?|[a-z][.)])\s*", "", name).strip(" :-–")
        weight = None
        for cell in cells[1:]:
            m = re.fullmatch(r"(\d{1,3}(?:\.\d+)?)\s*(%|marks|M)?(\s*\(.*\))?", cell.strip())
            if m and 1 <= float(m.group(1)) <= 100:
                weight = float(m.group(1))
                break
        # table rows have column gaps; prose lines that happen to contain a number don't
        looks_like_row = re.search(r"\S\s{2,}\S", s) and len(name) <= 50 and not name[:1].islower()
        if name and weight is not None and len(name) > 2 and looks_like_row \
                and not re.fullmatch(r"(total|sl\.? ?no\.?|s\.? ?no\.?)", name, re.I):
            comps.append({"name": _clean(name)[:80], "weight": weight})
    text_for_flags = region if found else "\n".join(lines)
    flags = {
        "has_midsem": bool(re.search(MIDSEM, text_for_flags, re.I)),
        "has_compre": bool(re.search(COMPRE, text_for_flags, re.I)),
        "has_quiz": bool(re.search(QUIZ, text_for_flags, re.I)),
        "has_assignment": bool(re.search(ASSIGN, text_for_flags, re.I)),
        "has_project": bool(re.search(PROJECT, text_for_flags, re.I)),
        "has_lab": bool(re.search(LAB, text_for_flags, re.I)),
        "has_viva_presentation": bool(re.search(VIVA, text_for_flags, re.I)),
        "open_book": bool(re.search(OPENBOOK, text_for_flags)),
    }
    project_weight = sum(c["weight"] for c in comps if re.search(PROJECT, c["name"], re.I)) or None
    # if the weights add up to ~100 the table parse is probably right; otherwise the UI
    # should show the raw evaluation text instead of our component list
    total = sum(c["weight"] for c in comps)
    comps_ok = 95 <= total <= 105
    return {
        "table_found": found,
        "confidence": "evaluation_section" if found else "whole_document",
        "components": comps,
        "components_reliable": comps_ok,
        "project_weight": project_weight if comps_ok else None,
        "evidence": _clean(region)[:1200] if found else None,
        **flags,
    }


NO_MAKEUP = r"(make[\s-]*up\s+\w+\s+(will|shall)\s+not\s+be\s+\w+[^.]*|no\s+make[\s-]*ups?\b[^.]*|make[\s-]*ups?\s+(will|shall)\s+not\s+be\s+(given|granted|allowed|entertained|conducted)[^.]*|not\s+be\s+given\s+under\s+any[^.]*)"
SCOPED = r"\b(for|in|of|on)\s+(the\s+|any\s+)?(class\s+|surprise\s+|announced\s+|unannounced\s+|online\s+|in-class\s+)?(lab|labs|laboratory|quiz|quizzes|assignment|assignments|class\s+test|surprise|tutorials?|practical|project|presentation|continuous|seminar|viva)|make[\s-]*up\s+(quiz|lab|assignment)|without|except|unless|other\s+than"
RESTRICT = r"genuin|geneu|polic|medical|hospital|prior\s+permission|emergenc|serious|illness|proof|documentary|certificate|exceptional|rules|guidelines|norms|procedure|permission|justifiable|valid\s+reason|discretion"


def parse_makeup(text: str, lines: list[str]) -> dict:
    i, sec = _section(lines, r"make[\s-]*up", max_lines=10)
    block = _clean(" ".join(sec))[:700] if sec else ""
    # the heading alone ('9. Make-up Policy:') carries no policy
    if not block or len(re.sub(r"(?i)^\W*\d*\W*make[\s-]*up\s*(policy|policies)?\s*:?", "", block).strip()) < 8:
        return {"status": "not_mentioned", "evidence": None}
    low = block.lower()
    nos = [m.group(0) for m in re.finditer(NO_MAKEUP, low)]
    if nos and all(re.search(SCOPED, n) for n in nos):
        status = "restricted"          # no make-up only for some components
        note = "no make-up for some components"
    elif nos:
        status = "none"
        note = None
    elif re.search(RESTRICT, low):
        status, note = "restricted", None
    else:
        status, note = "available", None
    return {"status": status, "note": note, "evidence": block}


ATT_NOT_REQUIRED = r"attendance\s+(is|will)\s+not\s+(be\s+)?(mandatory|compulsory|required|counted|taken|recorded|considered)|attendance\s+is\s+not\s+compulsory|no\s+attendance\s+(requirement|marks|will)|attendance\s+is\s+optional|attendance[^.]{0,30}not\s+mandatory|policy:\s*not\s+mandatory"
ATT_REQUIRED = r"attendance\s+(is\s+)?(mandatory|compulsory)|compulsory\s+attendance|mandatory\s+attendance|required\s+to\s+attend|must\s+attend|minimum\s+(of\s+)?\d{2}\s*%|\d{2}\s*%\s+(of\s+)?(the\s+)?attendance|attendance\s+(is\s+)?below\s+\d{2}|marks?\s+for\s+attendance|attendance\s+(marks|carries|will\s+carry|component)|linked\s+to\s+attendance|attendance.{0,40}\d+\s*%|debar"
ATT_EXPECTED = r"expected\s+to\s+(attend|be\s+regular)|regular(ity)?\s+(in|to)\s+(attend|class|theory|lecture)|attendance\s+will\s+be\s+(taken|recorded|counted|considered|monitored)"


def parse_attendance(text: str) -> dict:
    """Status: not_required | required | expected | mentioned | not_mentioned."""
    sents = _sentences_with(text, r"attend", limit=5)
    if not sents:
        return {"status": "not_mentioned", "evidence": None}
    joined = " ".join(sents)
    low = joined.lower()
    if re.search(ATT_NOT_REQUIRED, low):
        status = "not_required"
    elif re.search(ATT_REQUIRED, low):
        status = "required"
    elif re.search(ATT_EXPECTED, low):
        status = "expected"
    else:
        status = "mentioned"
    return {"status": status, "evidence": joined[:600]}


def parse_header(text: str, fname_code: str | None) -> dict:
    head = text[:3000]
    codes = []
    m = re.search(r"Course\s*(No|Number|Code)\.?\s*[:\-]?\s*(.+)", head, re.I)
    if m:
        for d, n in re.findall(r"\b([A-Z]{2,5})\s*[-_ ]?\s*([A-Z]\s?\d{3}[A-Z]?)\b", m.group(2)):
            c = norm_code(f"{d} {n.replace(' ', '')}")
            if c and c not in codes:
                codes.append(c)
    if fname_code and fname_code not in codes:
        codes.insert(0, fname_code)
    t = re.search(r"Course\s*Title\s*[:\-]?\s*(.+)", head, re.I)
    ic = re.search(r"Instructor[\s-]*in[\s-]*charge\s*[:\-]?\s*(.+)", head, re.I)
    return {
        "codes": codes,
        "title": _clean(t.group(1))[:120] if t else None,
        "instructor_in_charge": _clean(re.split(r"\(|\s{3,}", ic.group(1))[0])[:80] if ic else None,
    }


def parse_topics(text: str) -> str:
    """Course description + lecture plan, without the evaluation/policy tail. Used for interest matching."""
    m = re.search(EVAL_START, text, re.I | re.M)
    body = text[: m.start()] if m else text
    body = re.sub(r"(?i)birla institute of technology.*|pilani\s*\|.*|academic.{0,5}undergraduate.*|please do not print.*", " ", body)
    return _clean(body)[:6000]


# ------------------------------------------------------------------ main

def process_file(path: Path) -> dict:
    stem = path.stem
    fname_code = norm_code(re.sub(r"^\d+_", "", stem).replace("-1", "").replace("_", " "))
    text, method = pdf_text(path)
    lines = text.split("\n")
    rec = {
        "file": SOURCE_DIR + path.name,
        "extraction_method": method,
        "text_chars": len(text),
        **parse_header(text, fname_code),
        "evaluation": parse_evaluation(lines),
        "makeup": parse_makeup(text, lines),
        "attendance": parse_attendance(text),
        "prerequisites_text": (_sentences_with(text, r"pre[\s-]*requisite", 1) or [None])[0],
        "topics_text": parse_topics(text),
    }
    return rec


def build(handout_dir: Path = HANDOUT_DIR) -> list[dict]:
    by_hash, records = {}, []
    files = sorted(p for p in Path(handout_dir).glob("*.pdf"))
    for p in files:
        h = hashlib.md5(p.read_bytes()).hexdigest()
        fname_code = norm_code(re.sub(r"^\d+_", "", p.stem).replace("-1", "").replace("_", " "))
        if h in by_hash:
            rec = by_hash[h]
            if fname_code and fname_code not in rec["codes"]:
                rec["codes"].append(fname_code)
            rec["duplicate_files"].append(SOURCE_DIR + p.name)
            continue
        rec = process_file(p)
        rec["md5"] = h
        rec["duplicate_files"] = []
        by_hash[h] = rec
        records.append(rec)
    dump_json(records, "handouts.json")
    return records


if __name__ == "__main__":
    import collections
    recs = build()
    print("unique handouts:", len(recs), " codes covered:", len({c for r in recs for c in r["codes"]}))
    print("methods:", collections.Counter(r["extraction_method"] for r in recs))
    print("eval table found:", sum(r["evaluation"]["table_found"] for r in recs),
          " components sum to ~100:", sum(r["evaluation"]["components_reliable"] for r in recs))
    for k in ["makeup", "attendance"]:
        print(k, collections.Counter(r[k]["status"] for r in recs))
    for f in ["has_midsem", "has_compre", "has_quiz", "has_project", "open_book"]:
        print(f, sum(r["evaluation"][f] for r in recs))
