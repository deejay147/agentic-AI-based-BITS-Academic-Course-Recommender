"""Parse course descriptions from Bulletin Part VI (on-campus), PDF pages 610-752.

Each course starts with a header line  'CS F407 Artificial Intelligence   303'
(L P U digits run together, or '3*' / '4*' for courses given only in units),
followed by a free-text description and sometimes 'Pre-requisite(s): ...' and
'Equivalent: ...' lines. Pages are two-column; each column is read separately.

Output: data/processed/courses_bulletin.json
"""
from __future__ import annotations

import re

import pdfplumber

from ingest.common import BULLETIN_PDF, dump_json, norm_code

SOURCE = "bulletin.pdf"
PAGES = range(611, 753)
COL_SPLIT = 296  # pages are A4 (595pt); right column starts at x~300

HEADER = re.compile(
    r"^(?P<dept>[A-Z]{2,5})\s+(?P<num>[A-Z]\d{3}[A-Z]?)\s+(?P<title>.+?)\s+"
    r"(?P<lpu>\d{3}|\d{1,2}\*|\d\s+\d\s+\d{1,2}|\d{1,2})$"
)
CODE_ONLY_START = re.compile(r"^(?P<dept>[A-Z]{2,5})\s+(?P<num>[A-Z]\d{3}[A-Z]?)\s+(?P<title>\S.*)$")
LPU_TAIL = re.compile(r"^(?P<rest>.*?)\s*(?P<lpu>\d{3}|\d{1,2}\*|\d\s+\d\s+\d{1,2})$")
PREREQ = re.compile(r"Pre[\s-]*[Rr]equisites?\s*[:\-–]?\s*(?P<txt>.*)", re.S)
CODE_IN_TEXT = re.compile(r"\b([A-Z]{2,5})\s*([A-Z]\s?\d{3}[A-Z]?)\b")


def _lpu(s: str):
    s = s.strip()
    if s.endswith("*"):
        return {"L": None, "P": None, "units": int(s[:-1])}
    parts = s.split()
    if len(parts) == 3:
        return {"L": int(parts[0]), "P": int(parts[1]), "units": int(parts[2])}
    if len(s) == 3:
        return {"L": int(s[0]), "P": int(s[1]), "units": int(s[2])}
    return {"L": None, "P": None, "units": int(s)}


def _lines(pdf):
    for pn in PAGES:
        page = pdf.pages[pn - 1]
        for x0, x1 in ((0, COL_SPLIT), (COL_SPLIT, page.width)):
            txt = page.crop((x0, 0, x1, page.height)).extract_text(x_tolerance=1.5) or ""
            for line in txt.split("\n"):
                line = line.strip()
                if line and not re.fullmatch(r"VI-\d+", line):
                    yield pn, line


def parse(pdf):
    courses, cur = {}, None
    lines = list(_lines(pdf))
    i = 0
    while i < len(lines):
        pn, line = lines[i]
        m = HEADER.match(line)
        # header whose title wraps: 'BITS F232 Foundations of Data Structures and Algo- 3 1 4' is caught above;
        # 'XX F123 Long title that' + 'continues here 303'
        if not m:
            m2 = CODE_ONLY_START.match(line)
            if m2 and i + 1 < len(lines):
                nxt = lines[i + 1][1]
                t = LPU_TAIL.match(nxt)
                if t and len(nxt) < 45 and not CODE_ONLY_START.match(nxt):
                    line = f"{line} {nxt}"
                    m = HEADER.match(line)
                    if m:
                        i += 1
        if m:
            code = norm_code(f"{m['dept']} {m['num']}")
            if code:
                cur = {"code": code, "title": m["title"].strip(), **_lpu(m["lpu"]),
                       "description": "", "source": {"doc": SOURCE, "page": pn, "section": "Part VI"}}
                if code in courses:  # same code listed twice (e.g. under two departments) -> keep the richer one later
                    cur["_dup"] = True
                courses.setdefault(code, cur) if not cur.get("_dup") else None
                if cur.get("_dup"):
                    courses[code].setdefault("alt_descriptions", []).append(cur)
                i += 1
                continue
        if cur is not None:
            # a line that is just a department heading ('Aeronautics', 'Computer Science') has no period and
            # is short and Title Case; skip those
            if len(line) < 45 and re.fullmatch(r"[A-Z][A-Za-z&,\- ]+", line) and not line.endswith(".") \
                    and sum(w[0].isupper() for w in line.split() if w not in ("and", "of", "&")) == \
                    len([w for w in line.split() if w not in ("and", "of", "&")]) and len(line.split()) <= 5:
                i += 1
                continue  # department heading
            cur["description"] += (" " if cur["description"] else "") + line
        i += 1

    out = []
    for c in courses.values():
        c.pop("_dup", None)
        desc = re.sub(r"\s+", " ", c["description"]).replace("- ", "")
        eq = None
        em = re.search(r"Equivalent\s*:?\s*(.*)$", desc)
        if em:
            eq = em.group(1).strip()
            desc = desc[: em.start()].strip()
        pre_txt = None
        pm = PREREQ.search(desc)
        if pm:
            pre_txt = pm.group("txt").strip()
            desc = desc[: pm.start()].strip()
        codes = []
        if pre_txt:
            for d, n in CODE_IN_TEXT.findall(pre_txt):
                cc = norm_code(f"{d} {n.replace(' ', '')}")
                if cc and cc != c["code"] and cc not in codes:
                    codes.append(cc)
        c["description"] = desc
        c["prerequisite_text"] = pre_txt
        c["prerequisite_codes"] = codes
        # 'A OR B' -> any one; otherwise treat listed codes as all required (conservative)
        c["prerequisite_mode"] = ("any" if pre_txt and re.search(r"\bOR\b|\bor\b", pre_txt) else "all") if codes else None
        c["equivalent_text"] = eq
        c.pop("alt_descriptions", None)
        out.append(c)
    return out


def build():
    with pdfplumber.open(BULLETIN_PDF) as pdf:
        courses = parse(pdf)
    dump_json(courses, "courses_bulletin.json")
    return courses


if __name__ == "__main__":
    cs = build()
    print("courses:", len(cs), " with prerequisite text:", sum(1 for c in cs if c["prerequisite_text"]),
          " with prerequisite codes:", sum(1 for c in cs if c["prerequisite_codes"]))
