"""Bulletin Part VI -> one record per course (title, units, description, prereqs).

Each course starts with a header like  'CS F407 Artificial Intelligence   303'
where 303 = L P U squashed together (or '3*' / '4*' when only units are given).
After that it's free text, sometimes with 'Pre-requisite: ...' and 'Equivalent: ...'.

Only ~70 of ~2000 courses actually state a prerequisite here. Whatever is stated
gets kept as text + any course codes we can pull out of it. Missing != none, the
engine treats it as "no prerequisite listed in the supplied data".
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
        # sometimes the L P U ends up on the next line with the rest of the title
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
                # a few codes are described twice (listed under two departments) - keep all, pick later
                courses.setdefault(code, []).append(cur)
                # title wrapped onto the next line: 'Foundations of ... Algo-' + 'rithms', '... Biology &' + 'Immunology'
                if re.search(r"(-|&|,|\b(and|of|in|for|to|with|the))$", cur["title"]) and i + 1 < len(lines):
                    nxt = lines[i + 1][1]
                    first_sentence = re.split(r"(?<=[a-z])\s+(?=[A-Z])", nxt, maxsplit=1)[0] if len(nxt) > 40 else nxt
                    if not HEADER.match(nxt) and len(first_sentence) <= 40:
                        joiner = "" if cur["title"].endswith("-") else " "
                        cur["title"] = (cur["title"].rstrip("-") + joiner + first_sentence).strip()
                        rest = nxt[len(first_sentence):].strip()
                        if rest:
                            cur["description"] = rest
                        i += 1
                i += 1
                continue
        if cur is not None:
            # lines like 'Aeronautics' / 'Computer Science' are department headings, not description
            if len(line) < 45 and re.fullmatch(r"[A-Z][A-Za-z&,\- ]+", line) and not line.endswith(".") \
                    and sum(w[0].isupper() for w in line.split() if w not in ("and", "of", "&")) == \
                    len([w for w in line.split() if w not in ("and", "of", "&")]) and len(line.split()) <= 5:
                i += 1
                continue  # department heading
            cur["description"] += (" " if cur["description"] else "") + line
        i += 1

    out = []
    for versions in courses.values():
        c = max(versions, key=lambda v: len(v["description"]))  # the fuller description wins
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
        # 'A OR B' -> any one is enough, otherwise assume all listed codes are needed
        c["prerequisite_mode"] = ("any" if pre_txt and re.search(r"\bOR\b|\bor\b", pre_txt) else "all") if codes else None
        c["equivalent_text"] = eq
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
