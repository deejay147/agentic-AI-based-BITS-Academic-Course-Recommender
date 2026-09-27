"""Timetable parser -> course-wise sections (section II) + equivalent courses (section IX).

pdftotext's layout mode kind of works on this pdf but the column alignment drifts
page to page, so instead we go by word coordinates: every word gets a column from
its x position, and words with (almost) the same y become one row.

Things the pdf does that we have to deal with:
  - titles / instructor names wrap onto the line above AND below the actual row
  - day+hour cells like 'MW 2', 'M W 5 Th 10', 'F 7 8', 'Th 11 12'
  - cancelled lab sections just say CANCLED (sic)
  - same course listed twice: old comcod + a >=5000 comcod for 2026 admits

Writes data/processed/timetable.json and equivalents.json
"""
from __future__ import annotations

import re
from collections import defaultdict

import pdfplumber

from ingest.common import TIMETABLE_PDF, dump_json, norm_code

SOURCE = "timetable.pdf"

# x ranges (pdf points) for each column. got these from the header row, it's the same
# on every course-wise page: SEC=402, INSTRUCTOR=438, ROOM=573, DAYS=629, MIDSEM=699, COMPRE=757
COLS = [
    ("comcod", 0, 75),
    ("code", 75, 135),
    ("title", 135, 300),
    ("credit", 300, 395),
    ("sec", 395, 432),
    ("instructor", 432, 578),
    ("room", 578, 625),
    ("days", 625, 697),
    ("midsem", 697, 755),
    ("compre", 755, 900),
]
CREDIT_X = [("L", 300, 322), ("P", 322, 343), ("T", 343, 358), ("S", 358, 373), ("U", 373, 395)]

# hour slot -> start time, from the legend on page 6.
# slots 11/12 do show up (evening classes) but the legend doesn't list them, so no time for those
HOUR_TIMES = {
    1: "08:00", 2: "09:00", 3: "10:00", 4: "11:00", 5: "12:00",
    6: "13:00", 7: "14:00", 8: "15:00", 9: "16:00", 10: "17:00",
}
MIDSEM_SESSIONS = {"FN1": "09:00-10:30", "FN2": "11:00-12:30", "AN1": "14:00-15:30", "AN2": "16:00-17:30"}
COMPRE_SESSIONS = {"FN": "09:00-12:00", "AN": "14:00-17:00"}


def _col(x: float, cols=COLS) -> str | None:
    for name, lo, hi in cols:
        if lo <= x < hi:
            return name
    return None


def _group_rows(words, tol=2.5):
    # words within ~2.5pt vertically are on the same visual line
    rows = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(rows[-1]["top"] - w["top"]) <= tol:
            rows[-1]["words"].append(w)
        else:
            rows.append({"top": w["top"], "words": [w]})
    return rows


_DAY_SPLIT = re.compile(r"Th|M|T|W|F|S")


def _split_day_token(tok: str) -> list[str] | None:
    # 'MW' -> ['M','W'], 'TThF' -> ['T','Th','F'], anything else -> None
    parts = _DAY_SPLIT.findall(tok)
    return parts if parts and "".join(parts) == tok else None


def _hours_from_token(tok: str) -> list[int]:
    # labs sometimes come out glued: '67' means hours 6 and 7, '12' means 1 and 2.
    # '10' is just hour 10. '11' can't be 1+1 so it has to be the evening slot 11.
    if tok in ("10", "11"):
        return [int(tok)]
    out, s = [], tok
    while s:
        if s.startswith("10"):
            out.append(10)
            s = s[2:]
        else:
            out.append(int(s[0]))
            s = s[1:]
    return [h for h in out if h >= 1]


def _parse_hours_tokens(tokens: list[str]) -> list[dict]:
    # 'M W 3 Th 9' -> M3, W3, Th9
    # days pile up until we hit digits, the digits apply to all of them,
    # and the next day letter after digits starts a fresh group
    slots, days, after_digits, prev = [], [], False, None
    for tok in tokens:
        split = _split_day_token(tok)
        if split:
            if after_digits:
                days, after_digits = [], False
            days.extend(split)
        elif tok.isdigit():
            after_digits = True
            # 'Th 11 12' is the evening block 11+12, not 11 + (1,2)
            hours = [12] if tok == "12" and prev == "11" else _hours_from_token(tok)
            for d in days:
                for h in hours:
                    slots.append({"day": d, "hour": h})
            prev = tok
    seen, out = set(), []
    for sl in slots:
        k = (sl["day"], sl["hour"])
        if k not in seen:
            seen.add(k)
            out.append(sl)
    return out


def _parse_exam(text: str, sessions: dict) -> dict | None:
    # '08/10 AN2' -> 2026-10-08, AN2
    m = re.search(r"(\d{2})/(\d{2})\s*(FN\d?|AN\d?)", text)
    if not m:
        return None
    day, month, sess = m.groups()
    return {"date": f"2026-{month}-{day}", "session": sess, "time": sessions.get(sess)}


def _is_coursewise_page(words) -> bool:
    heads = {w["text"] for w in words if w["top"] < 140}
    return {"SEC", "MIDSEM", "COMPRE"} <= heads


def _nearest(cands, top, max_dist):
    # cands = [(top, obj)], returns obj closest to `top` if within max_dist
    if not cands:
        return None
    ctop, obj = min(cands, key=lambda c: abs(c[0] - top))
    return obj if abs(ctop - top) <= max_dist else None


def parse_timetable(pdf_path=TIMETABLE_PDF) -> list[dict]:
    courses: list[dict] = []
    cur = None          # course we're currently filling
    cur_sec = None      # section we're currently filling
    sec_rows = []       # (page, top, section) - to hang wrapped instructor names on later
    course_rows = []    # (page, top, course)  - same idea for wrapped titles
    stray_instr = []    # instructor-only lines
    title_frags = []    # title-only lines

    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(x_tolerance=1.5, keep_blank_chars=False)
            if not _is_coursewise_page(words):
                continue
            body = [w for w in words if w["top"] > 138]  # skip the header block
            for row in _group_rows(body):
                cells = defaultdict(list)
                for w in row["words"]:
                    c = _col(w["x0"])
                    if c:
                        cells[c].append(w)
                text = {k: " ".join(w["text"] for w in v) for k, v in cells.items()}
                joined = " ".join(w["text"] for w in row["words"])
                if joined.startswith("Note:") or re.fullmatch(r"\d{1,3}", joined.strip()):
                    continue  # page footer / page number

                # a row with a comcod + course no starts a new course
                if "comcod" in text and re.fullmatch(r"\d{3,4}", text["comcod"]) and "code" in text:
                    code = norm_code(text["code"])
                    if not code:
                        continue
                    credit = {}
                    for w in cells.get("credit", []):
                        c = _col(w["x0"], CREDIT_X)
                        if c:
                            credit[c] = None if w["text"] == "-" else w["text"]
                    comcod = int(text["comcod"])
                    cur = {
                        "comcod": comcod,
                        "code": code,
                        "title": text.get("title", "").strip(),
                        "L": credit.get("L"), "P": credit.get("P"), "T": credit.get("T"), "S": credit.get("S"),
                        "units": credit.get("U"),
                        # footer note on every page: comcod >= 5000 is only for 2026 admits
                        "only_2026_admits": comcod >= 5000,
                        "sections": [],
                        "midsem": None,
                        "compre": None,
                        "source": {"doc": SOURCE, "page": pno},
                    }
                    courses.append(cur)
                    course_rows.append((pno, row["top"], cur))
                    cur_sec = None
                elif cur is None:
                    continue

                # 'Tutorial' / 'Practical' labels sit in the title column of the first row of that block
                label = text.get("title", "") if "comcod" not in text else ""

                if "sec" in text and re.fullmatch(r"[LTPR]\d{1,2}", text["sec"]):
                    sid = text["sec"]
                    kind = {"L": "lecture", "T": "tutorial", "P": "practical", "R": "lecture"}[sid[0]]
                    if label.lower().startswith("tutorial"):
                        kind = "tutorial"
                    elif label.lower().startswith("practical"):
                        kind = "practical"
                    if "CANCLED" in joined or "CANCELLED" in joined.upper():
                        cur_sec = None
                        continue
                    day_tokens = [w["text"] for w in cells.get("days", [])]
                    cur_sec = {
                        "section": sid,
                        "type": kind,
                        "instructors": [text["instructor"]] if text.get("instructor") else [],
                        "room": text.get("room"),
                        "slots": _parse_hours_tokens(day_tokens),
                        "raw_days": " ".join(day_tokens),
                    }
                    cur["sections"].append(cur_sec)
                    sec_rows.append((pno, row["top"], cur_sec))
                    # IC is the name in BLOCK LETTERS on a lecture row (legend, col 6)
                    if cur_sec["instructors"] and kind == "lecture" and cur_sec["instructors"][0].isupper():
                        cur.setdefault("ic", cur_sec["instructors"][0])
                    if text.get("midsem") and not cur["midsem"]:
                        cur["midsem"] = _parse_exam(text["midsem"], MIDSEM_SESSIONS)
                    if text.get("compre") and not cur["compre"]:
                        cur["compre"] = _parse_exam(text["compre"], COMPRE_SESSIONS)
                elif set(text) == {"title"}:
                    title_frags.append((pno, row["top"], text["title"]))
                elif set(text) <= {"instructor", "title"} and text.get("instructor"):
                    stray_instr.append((pno, row["top"], text["instructor"]))
                elif cur_sec is not None and "days" in text and set(text) <= {"days", "room", "instructor"}:
                    # day/hour cell wrapped onto the next line
                    cur_sec["raw_days"] += " " + " ".join(w["text"] for w in cells["days"])
                    cur_sec["slots"] = _parse_hours_tokens(cur_sec["raw_days"].split())
                elif text.get("midsem") or text.get("compre"):
                    if text.get("midsem") and not cur["midsem"]:
                        cur["midsem"] = _parse_exam(text["midsem"], MIDSEM_SESSIONS)
                    if text.get("compre") and not cur["compre"]:
                        cur["compre"] = _parse_exam(text["compre"], COMPRE_SESSIONS)

    # wrapped instructor names -> nearest section row on the same page
    secs_by_page = defaultdict(list)
    for p, top, sec in sec_rows:
        secs_by_page[p].append((top, sec))
    for p, top, name in stray_instr:
        sec = _nearest(secs_by_page.get(p), top, 12)
        if sec is not None:
            sec["instructors"].append(name)

    # wrapped title bits ('COMPUTL THINKING &' above, 'PROGRAMMING' below) -> nearest course row
    rows_by_page = defaultdict(list)
    for p, top, c in course_rows:
        rows_by_page[p].append((top, c))
    row_top = {id(c): top for _, top, c in course_rows}
    for p, top, frag in title_frags:
        if frag.strip().lower() in ("tutorial", "practical", "lecture"):
            continue
        c = _nearest(rows_by_page.get(p), top, 10)
        if c is not None:
            c.setdefault("_frags", []).append((top, frag))
    for _, _, c in course_rows:
        if c.get("_frags"):
            parts = c.pop("_frags") + [(row_top[id(c)], c["title"])]
            c["title"] = " ".join(f for _, f in sorted(parts) if f)

    for c in courses:
        c["title"] = re.sub(r"\s+", " ", c["title"]).strip()
        c["has_midsem_slot"] = c["midsem"] is not None
        c["has_compre_slot"] = c["compre"] is not None
        for sec in c["sections"]:
            sec["slots_missing"] = not sec["slots"]
        if "ic" not in c:
            lec = [s for s in c["sections"] if s["instructors"]]
            c["ic"] = lec[0]["instructors"][0] if lec else None
    return courses


def parse_equivalents(pdf_path=TIMETABLE_PDF) -> list[dict]:
    # section IX rows look like: 'CS F215  DIGITAL DESIGN  EEE F215  INSTR F215  CS F215'
    # any line with 2+ course codes = one equivalence group
    groups = []
    code_re = re.compile(r"\b([A-Z]{2,5})\s+([A-Z]\d{3}[A-Z]?)\b")
    with pdfplumber.open(pdf_path) as pdf:
        in_section = False
        for pno, page in enumerate(pdf.pages, start=1):
            txt = page.extract_text() or ""
            if "LIST OF EQUIVALENT COURSES" in txt:
                in_section = True
            elif in_section and "LIBRARY AND BITS COOP" in txt:
                break
            if not in_section:
                continue
            for line in txt.split("\n"):
                codes = [c for c in (norm_code(f"{a} {b}") for a, b in code_re.findall(line)) if c]
                if len(codes) >= 2:
                    groups.append({"course": codes[0], "equivalents": sorted(set(codes)),
                                   "source": {"doc": SOURCE, "page": pno, "section": "IX"}})
    return groups


def main():
    courses = parse_timetable()
    dump_json(courses, "timetable.json")
    eq = parse_equivalents()
    dump_json(eq, "equivalents.json")
    n_sec = sum(len(c["sections"]) for c in courses)
    print(f"timetable: {len(courses)} course rows, {len({c['code'] for c in courses})} codes, {n_sec} sections")
    print(f"equivalents: {len(eq)} groups")


if __name__ == "__main__":
    main()
