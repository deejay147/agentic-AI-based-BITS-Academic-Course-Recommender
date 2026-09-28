"""Bulletin Part IV -> programme structures (CDC / DEL lists), HUEL pool, minors.

Where stuff lives in bulletin.pdf (pdf page numbers, not the printed IV-xx ones):
  209-210  overall structure of first degrees (IV-1, IV-2)
  211-238  semester-wise chart for each single degree programme
  314-335  CDC + DEL list per programme, then the HUEL pool and 'Other Courses'
  337-349  minors (these are proper ruled tables, so extract_tables works there)

The course-list pages are two columns of plain text. We crop each column and run the
lines through a small state machine:
    programme heading -> CORE COURSES -> DISCIPLINE ELECTIVE COURSES (tracks/pools) -> next heading
An 'OR' line between two courses means either one fills that slot, so those get grouped.

Heads up: the list and the semester chart don't always agree (B.Pharm, ECE, Env Engg).
See reconcile_cdc() - whatever can't be reconciled goes to the verification queue.
"""
from __future__ import annotations

import re

import pdfplumber

from ingest.common import BULLETIN_PDF, dump_json, norm_code

SOURCE = "bulletin.pdf"
LIST_PAGES = range(314, 337)
CHART_PAGES = range(211, 239)
COL_SPLIT = 256  # x (pt) between the two text columns (page width 522)

# course-list heading -> (programme code, bit of the chart page title to find it by, degree)
# codes are the official ID-number codes from bulletin III-55 (pdf p.199-200), so a student's
# ID like 2025A7PS0147P maps straight to a programme. two exceptions:
#   RIA has no code in that table, and both General Studies streams share C2 (-CMS / -DS added)
PROGRAMMES = {
    "ARCHITECTURAL AND URBAN ENGINEERING": ("AE", "Architecture and Urban", "B.E."),
    "BIOTECHNOLOGY": ("A9", "B.E. Biotechnology Programme", "B.E."),
    "BIOTECHNOLOGY WITH SPECIALIZATION IN APPLIED MOLECULAR BIOLOGY": ("AH", "Specialization in Applied", "B.E."),
    "CHEMICAL ENGINEERING": ("A1", "B.E. Chemical Programme", "B.E."),
    "CHEMICAL ENGINEERING WITH SPECIALIZATION IN ENERGY, ENVIRONMENT, AND SUSTAINABILITY": ("AF", "Chemical with Specialization", "B.E."),
    "CIVIL ENGINEERING": ("A2", "B.E. Civil", "B.E."),
    "COMPUTER SCIENCE": ("A7", "Computer Science", "B.E."),
    "ELECTRICAL AND ELECTRONICS ENGINEERING": ("A3", "Electrical & Electronics", "B.E."),
    "ELECTRONICS AND COMMUNICATION ENGINEERING": ("AA", "Electronics & Communication", "B.E."),
    "ELECTRONICS AND COMPUTER ENGINEERING": ("AC", "Electronics & Computer", "B.E."),
    "ELECTRONICS AND INSTRUMENTATION ENGINEERING": ("A8", "Electronics and Instrumentation", "B.E."),
    "ENVIRONMENTAL AND SUSTAINABILITY ENGINEERING": ("AJ", "Environmental and Sustainability", "B.E."),
    "MANUFACTURING ENGINEERING": ("AB", "Manufacturing", "B.E."),
    "MATHEMATICS AND COMPUTING": ("AD", "Mathematics and Computing", "B.E."),
    "MECHANICAL ENGINEERING": ("A4", "B.E. Mechanical Programme", "B.E."),
    "MECHANICAL ENGINEERING WITH SPECIALIZATION IN AEROSPACE": ("AG", "Mechanical with Specialization", "B.E."),
    "PHARMACY": ("A5", "B. Pharm", "B.Pharm."),
    "BIOLOGICAL SCIENCES": ("B1", "Biological Sciences", "M.Sc."),
    "CHEMISTRY": ("B2", "M.Sc. Chemistry", "M.Sc."),
    "ECONOMICS": ("B3", "Economics", "M.Sc."),
    "MATHEMATICS": ("B4", "M.Sc. Mathematics", "M.Sc."),
    "PHYSICS": ("B5", "M. Sc. Physics Programme", "M.Sc."),
    "PHYSICS WITH SPECIALIZATION IN SPACE SCIENCE AND TECHNOLOGY": ("B6", "Physics specialization in Space", "M.Sc."),
    "ROBOTICS AND INDUSTRIAL AUTOMATION": ("RIA", "Robotics", "B.E."),
    "GENERAL STUDIES – COMMUNICATION AND MEDIA STUDIES STREAM": ("C2-CMS", "Communication and Media", "M.Sc."),
    "GENERAL STUDIES – DEVELOPMENT STUDIES STREAM": ("C2-DS", "Development Studies", "M.Sc."),
    "SEMICONDUCTOR AND NANOSCIENCE": ("B7", "Semiconductor and Nanoscience", "M.Sc."),
    # heading is an image in the PDF; recognised by its BBA-prefixed core courses
    "BACHELOR OF BUSINESS ADMINISTRATION (HONOURS)": ("C8", "Business Administration", "BBA"),
}

COURSE_LINE = re.compile(
    r"^(?P<dept>[A-Z]{2,5})\s+(?P<num>[A-Z]\d{3}[A-Z]?)(?P<star>\*?)\s+(?P<rest>.*)$"
)
UNITS_TAIL = re.compile(r"\s+(?:(\d|-)\s+(\d|-)\s+(\d{1,2})|(\d{1,2})\*?)\s*$")
LONE_NUM = re.compile(r"^[A-Z]\d{3}[A-Z]?\*?$")
CAPS_LINE = re.compile(r"^[0-9 ]*[A-Z][A-Z ,&()\.–\-]{4,}$")


def _column_lines(pdf, pages):
    """Yield (page, line) for the left then right column of every page."""
    for pn in pages:
        page = pdf.pages[pn - 1]
        for x0, x1 in ((0, COL_SPLIT), (COL_SPLIT, page.width)):
            txt = page.crop((x0, 0, x1, page.height)).extract_text(x_tolerance=1.5) or ""
            lines = [l.strip() for l in txt.split("\n") if l.strip()]
            # glue split codes: 'ECOM Real Time Operating Systems 3 1 4' + 'F321'
            fixed, i = [], 0
            while i < len(lines):
                l = lines[i]
                if i + 1 < len(lines) and LONE_NUM.match(lines[i + 1]) and re.match(r"^[A-Z]{2,5}\s+(?![A-Z]\d{3})", l):
                    dept, rest = l.split(None, 1)
                    num = lines[i + 1]
                    l = f"{dept} {num} {rest}"
                    i += 1
                fixed.append(l)
                i += 1
            for l in fixed:
                yield pn, l


def _parse_course(line):
    m = COURSE_LINE.match(line)
    if not m:
        return None
    code = norm_code(f"{m['dept']} {m['num']}")
    if not code:
        return None
    rest = m["rest"]
    units = None
    um = UNITS_TAIL.search(rest)
    if um:
        units = int(um.group(3) or um.group(4))
        rest = rest[: um.start()]
    title = rest.replace("*", "").strip()
    return {"code": code, "title": title, "units": units, "starred": bool(m["star"]) or line.rstrip().endswith("*")}


def parse_course_lists(pdf):
    programmes, cur, section, track = [], None, None, None
    pending_or = False
    heading_buf = []
    huel, other, mode = [], [], "programmes"
    last_entry = None

    def flush_heading():
        nonlocal cur, section, track, heading_buf
        if not heading_buf:
            return
        name = re.sub(r"^\d+\s+", "", " ".join(heading_buf)).strip()
        name = re.sub(r"\s+", " ", name)
        heading_buf = []
        cur = {"heading": name, "core": [], "del": [], "notes": [], "pages": set()}
        programmes.append(cur)
        section, track = None, None

    for pn, line in _column_lines(pdf, LIST_PAGES):
        if re.fullmatch(r"IV-\d+", line):
            continue
        if line.startswith("Project Type Courses"):
            mode = "skip"
            continue
        if line.startswith("Pool of Humanities courses"):
            mode = "huel"
            continue
        if mode == "huel" and line.startswith("It may be noted"):
            mode = "skip"
            continue
        if line.startswith("Other Courses"):
            mode = "other"
            continue
        if mode == "other" and line.startswith("List of Audit Type Courses"):
            break
        if mode in ("huel", "other"):
            c = _parse_course(line)
            tgt = huel if mode == "huel" else other
            if c:
                c["source"] = {"doc": SOURCE, "page": pn}
                tgt.append(c)
                last_entry = c
            elif last_entry is not None and not line.startswith(("Course", "No.", "*[")) and tgt:
                tgt[-1]["title"] += " " + line
            continue
        if mode == "skip":
            continue

        if CAPS_LINE.match(line) and not line.startswith(("CORE COURSES", "DISCIPLINE ELECTIVE")) \
                and not re.fullmatch(r"(OR|or)(\s+(OR|or))*", line) and not COURSE_LINE.match(line):
            heading_buf.append(re.sub(r"^\d+\s+", "", line))
            continue
        flush_heading()

        if line.startswith("CORE COURSES"):
            if cur is None or cur["core"]:
                # a new programme whose heading is not in the text layer
                cur = {"heading": None, "core": [], "del": [], "notes": [], "pages": set()}
                programmes.append(cur)
            section, track = "core", None
            continue
        if line.startswith("DISCIPLINE ELECTIVE"):
            section, track = "del", None
            continue
        if cur is None or section is None:
            continue
        cur["pages"].add(pn)
        if re.match(r"^(Track|Pool)\s*[-–]?\s*\S+", line):
            track = line
            continue
        if re.fullmatch(r"(OR|or)(\s+(OR|or))*.*", line) and not COURSE_LINE.match(line):
            pending_or = True
            continue
        if line.startswith("*"):
            cur["notes"].append(line)
            last_entry = None
            continue
        c = _parse_course(line)
        if c:
            c["source"] = {"doc": SOURCE, "page": pn}
            bucket = cur[section]
            if pending_or and bucket:
                bucket[-1]["options"].append(c)
            else:
                bucket.append({"options": [c], "track": track})
            pending_or = False
            last_entry = c
            continue
        # continuation line: title wrap, or a note continuing a '*' note
        if line.lower().startswith(("or ", "or")) and len(line) < 30:
            pending_or = True
            rest = line[2:].strip()
            if last_entry is not None and rest:
                last_entry["title"] += " " + rest
            continue
        if cur["notes"] and last_entry is None:
            cur["notes"][-1] += " " + line
        elif last_entry is not None:
            # title continuation (may carry trailing units, e.g. 'or 3 1 4')
            last_entry["title"] = (last_entry["title"] + " " + UNITS_TAIL.sub("", " " + line).strip()).strip()

    # name the heading-less programme(s)
    for p in programmes:
        if p["heading"] is None and p["core"] and p["core"][0]["options"][0]["code"].startswith("BBA"):
            p["heading"] = "BACHELOR OF BUSINESS ADMINISTRATION (HONOURS)"
            p["notes"].append("Heading not present in PDF text layer; identified from BBA-prefixed core courses.")
    return programmes, huel, other


_TOTAL_TOK = re.compile(r"^\d{1,2}(\*|\(min\)|/\d{1,2}|to\d+)?$|^\(min\)$")


def chart_positions(page, year_offset: int = 0) -> dict:
    """code -> [year, semester] read off the semester-wise chart.

    Layout: first semester in the left half, second in the right half. Each year block
    ends with a row that only has the unit totals ('18  19', '20(min) 21(min)'), so the
    year of a course = 1 + number of total-rows above it. Summer (PS-I) sits in the middle
    and gets semester 0."""
    ws = page.extract_words(x_tolerance=1.5)
    rows = []
    for w in sorted(ws, key=lambda w: (round(w["top"]), w["x0"])):
        if rows and abs(rows[-1][0] - w["top"]) < 3:
            rows[-1][1].append(w)
        else:
            rows.append([w["top"], [w]])
    # a year ends with its semester totals - those are always >= 8 units. rows that only hold
    # per-course units (3, 4, 1*) show up too when the unit sits on its own line, so skip those
    ends = [top for top, r in rows if len(r) <= 3 and all(_TOTAL_TOK.match(w["text"]) for w in r)
            and all(int(re.match(r"\d+", w["text"]).group()) >= 8 for w in r if re.match(r"\d+", w["text"]))]
    mid = page.width / 2
    pos = {}
    for i, w in enumerate(ws[:-1]):
        n = ws[i + 1]
        if re.fullmatch(r"[A-Z]{2,5}", w["text"]) and re.fullmatch(r"[A-Z]\d{3}[A-Z]?", n["text"]) \
                and abs(w["top"] - n["top"]) < 3:
            code = norm_code(f"{w['text']} {n['text']}")
            year = 1 + year_offset + sum(1 for e in ends if e < w["top"])
            sem = 0 if mid * 0.55 < w["x0"] < mid * 0.9 else (1 if w["x0"] < mid else 2)
            if code and code not in pos:
                pos[code] = [year, sem]
    return pos


def parse_chart(pdf, title_fragment):
    """Find the semester-wise chart page for a programme and pull: all course codes on it,
    the 'Discipline Core - N Units (M Courses)' / 'Discipline Electives - N Units (M Courses)' footer."""
    for pn in CHART_PAGES:
        txt = pdf.pages[pn - 1].extract_text() or ""
        head = " ".join(txt.split("\n")[:3])
        if title_fragment.lower() not in re.sub(r"\s+", " ", head).lower():
            continue
        codes = []
        for dept, num in re.findall(r"\b([A-Z]{2,5})\s+([A-Z]\d{3}[A-Z]?)\b", txt):
            c = norm_code(f"{dept} {num}")
            if c and c not in codes:
                codes.append(c)
        # 'or F425T' style continuation of BITS thesis codes is ignored (PS/thesis handled separately)
        def footer(label):
            # 'Discipline Core -48 Units (16 Courses)', '-47 or 48 Units (14 Courses)',
            # 'Discipline Electives - 15 Units (min)-(4 Courses (min))'
            m = re.search(label + r"\s*[-–]?\s*(\d+)(?:\s*or\s*(\d+))?\s*Units?[^0-9]{0,12}(\d+)\s*Courses?", txt, re.I | re.S)
            if not m:
                return None
            lo = int(m.group(1))
            return {"units": int(m.group(2) or lo), "units_min": lo, "courses": int(m.group(3))}
        return {
            "page": pn,
            "codes": codes,
            "positions": chart_positions(pdf.pages[pn - 1]),
            "cdc_total": footer(r"Discipline\s+Core"),
            "del_total": footer(r"Discipline\s+Electives?"),
            "text": txt,
        }
    return None


# ---------------------------------------------------------------------------
# Composite dual degree charts (pdf p.242-313), one page per M.Sc. + B.E. pair.
# Year I on these pages is just 'Same as First degree Programme', so the first
# totals row closes year II -> year_offset=1.
DUAL_PAGES = range(242, 314)
MSC_NAMES = {"Biological Sciences": "B1", "Chemistry": "B2", "Economics": "B3", "Mathematics": "B4",
             "Physics": "B5", "Semiconductor and Nanoscience": "B7"}
BE_NAMES = {"Chemical": "A1", "Civil": "A2", "Computer Science": "A7", "Electrical & Electronics": "A3",
            "Electronics & Computer Engineering": "AC", "Electronics & Communication": "AA",
            "Electronics & Instrumentation": "A8", "Environmental and Sustainability Engineering": "AJ",
            "Manufacturing": "AB", "Mathematic and Computing": "AD", "Mechanical": "A4",
            "Robotics and Industrial Automation": "RIA"}


def parse_dual_charts(pdf) -> dict:
    out = {}
    for pn in DUAL_PAGES:
        page = pdf.pages[pn - 1]
        head = re.sub(r"\s+", " ", " ".join((page.extract_text() or "").split("\n")[:4]))
        m = re.search(r"\(M\.\s?Sc\.\s*(.+?) with (?:B\.E\.\s*)?(.+?)(?: Programme| Engineering)?\)", head)
        if not m:
            continue
        msc = next((v for k, v in MSC_NAMES.items() if m.group(1).strip().startswith(k)), None)
        be = next((v for k, v in BE_NAMES.items() if (m.group(2).strip() + " Engineering").startswith(k)
                   or m.group(2).strip().startswith(k)), None)
        if msc and be:
            out[f"{msc}+{be}"] = {"page": pn, "positions": chart_positions(page, year_offset=1)}
    return out


# General Institutional Requirement (bulletin IV-1/IV-2, p.209-210). The named GIR
# courses are taken from each programme's chart (codes that are not CDCs).
GIR_STRUCTURE = {
    "humanities_electives": {"units": 8, "courses": 3},
    "coursework_min_units": 129,
    "coursework_min_courses": 41,
    "total_min_units": 144,
    "open_electives_range_units": [15, 27],
    "source": {"doc": SOURCE, "page": 209, "section": "IV-1"},
}
NON_COURSEWORK = {"BITS F221", "BITS F412", "BITS F421T", "BITS F424T", "BITS F425T", "BITS F231", "BITS F241"}


# GIR courses named in IV-2 (p.210) and the alternatives visible in the charts
GIR_ALL = {
    "BIO F101", "CHEM F101", "PHY F101", "PHY F102",            # science foundation
    "EEE F111", "BITS F111", "BITS F219",                        # engineering foundation
    "CS F111", "BITS F103", "BITS F112",                         # technical arts
    "BITS F101", "BITS F102", "BITS K101", "ECON F211", "MGTS F211", "BITS F225",  # general awareness
    "MATH F101", "MATH F102", "MATH F113", "MATH F211",          # mathematics foundation
    "BITS F113", "BITS F114", "BITS F218",                       # general mathematics sequence (B.Pharm)
}
GIR_ALTERNATIVES = [["ECON F211", "MGTS F211"], ["PHY F101", "PHY F102"],
                    ["MATH F101", "BITS F113"], ["MATH F102", "BITS F114"], ["MATH F211", "BITS F218"]]


def _units(groups):
    return sum((g["options"][0]["units"] or 0) for g in groups)


def reconcile_cdc(core, chart):
    """The CDC list (IV-106..) and the semester chart occasionally disagree. Rules, in order:
    1. start from the CDC list;
    2. add non-GIR courses that are named on the chart but missing from the list;
    3. if the list exceeds the chart footer, drop groups that are purely GIR courses
       (e.g. BITS F219 Process Engineering is Engineering Foundation for B.Pharm.);
    4. if it still exceeds, drop groups not named on the chart (e.g. PHA F243, replaced by PHA F215);
    5. any remaining mismatch with the footer goes to the verification queue.
    Returns (cdc_groups, gir_named, issues)."""
    issues = []
    groups = [dict(g, source_kind="cdc_list") for g in core]
    if not chart:
        issues.append("No semester chart found; CDC list used as-is.")
        return groups, [], issues
    on_chart = set(chart["codes"])
    listed = {o["code"] for g in groups for o in g["options"]}
    extra = [c for c in chart["codes"] if c not in listed and c not in GIR_ALL and c not in NON_COURSEWORK
             and not c.startswith("BITS F42") and not c.endswith("T")]
    tot = chart["cdc_total"]
    if tot and len(groups) >= tot["courses"]:
        extra = []  # the list already has enough courses; chart-only codes are GIR/other named courses
    for c in extra:
        groups.append({"options": [{"code": c, "title": None, "units": None, "starred": False,
                                     "source": {"doc": SOURCE, "page": chart["page"]}}],
                       "track": None, "source_kind": "chart_only"})
    if extra:
        issues.append(f"Named on semester chart p.{chart['page']} but not in CDC list: {', '.join(extra)} (added as CDC).")
    if tot and _units(groups) > tot["units"]:
        pure_gir = [g for g in groups if all(o["code"] in GIR_ALL for o in g["options"])]
        if pure_gir:
            groups = [g for g in groups if g not in pure_gir]
            issues.append("Dropped GIR course(s) from CDC count: " + ", ".join(g["options"][0]["code"] for g in pure_gir))
    if tot and len(groups) > tot["courses"]:
        off = [g for g in groups if g["source_kind"] == "cdc_list" and not any(o["code"] in on_chart for o in g["options"])]
        if off:
            groups = [g for g in groups if g not in off]
            issues.append("Dropped CDC-list course(s) not named on the chart: " + ", ".join(g["options"][0]["code"] for g in off))
    # same number of courses but the units are off, and exactly one list course is missing from the
    # chart while exactly one own-department chart course is missing from the list -> the chart is the
    # prescribed pattern (reg 1.07), so swap them. (ECE: list has ECE F331, chart has ECE F314.)
    if tot and len(groups) == tot["courses"] and _units(groups) != tot["units"]:
        listed_now = {o["code"] for g in groups for o in g["options"]}
        depts = {g["options"][0]["code"].split()[0] for g in groups}
        off = [g for g in groups if not any(o["code"] in on_chart for o in g["options"])]
        only_chart = [c for c in chart["codes"] if c not in listed_now and c.split()[0] in depts
                      and c not in GIR_ALL and not c.endswith("T")]
        if len(off) == 1 and len(only_chart) == 1:
            groups = [g for g in groups if g is not off[0]] + [{
                "options": [{"code": only_chart[0], "title": None, "units": None, "starred": False,
                             "source": {"doc": SOURCE, "page": chart["page"]}}],
                "track": None, "source_kind": "chart_only"}]
            issues.append(f"CDC list has {off[0]['options'][0]['code']} but the semester chart p.{chart['page']} "
                          f"prescribes {only_chart[0]} instead; using the chart.")
    if tot and (len(groups) != tot["courses"] or not (tot.get("units_min", tot["units"]) <= _units(groups) <= tot["units"])):
        issues.append(f"CDC count {len(groups)} courses/{_units(groups)} units vs chart footer {tot['courses']} courses/{tot['units']} units.")
    cdc_codes = {o["code"] for g in groups for o in g["options"]}
    # the programme's own departments (e.g. ECE F314 on the ECE chart is a discipline course
    # that just isn't in the CDC list, not a GIR course)
    dept_count = {}
    for g in groups:
        d = g["options"][0]["code"].split()[0]
        dept_count[d] = dept_count.get(d, 0) + 1
    own = {d for d, n in dept_count.items() if n >= 2}
    gir = [c for c in chart["codes"] if c not in cdc_codes and c not in NON_COURSEWORK
           and not c.startswith("BITS F42") and not c.endswith("T")
           and (c in GIR_ALL or c.split()[0] not in own)]
    return groups, gir, issues


def build():
    with pdfplumber.open(BULLETIN_PDF) as pdf:
        progs, huel, other = parse_course_lists(pdf)
        out = []
        for p in progs:
            name = p["heading"]
            meta = PROGRAMMES.get(name)
            if not meta:
                print("WARN: unmapped programme heading:", name)
                continue
            pid, frag, degree = meta
            chart = parse_chart(pdf, frag) if frag else None
            cdc, gir, issues = reconcile_cdc(p["core"], chart)
            compulsory_del = [o["code"] for g in p["del"] for o in g["options"] if o["starred"]]
            out.append({
                "id": pid,
                "name": name.title().replace("And", "and").replace("With", "with").replace("In ", "in "),
                "degree": degree,
                "curriculum": "Bulletin 2025-26",
                "cdc": cdc,
                "cdc_list_raw": p["core"],
                "verification": issues,
                "del": p["del"],
                "compulsory_del": compulsory_del if any("Compulsory" in n for n in p["notes"]) else [],
                "notes": p["notes"],
                "gir_named": gir,
                "gir_alternatives": GIR_ALTERNATIVES,
                "cdc_total": chart["cdc_total"] if chart else None,
                "del_total": chart["del_total"] if chart else None,
                "chart_page": chart["page"] if chart else None,
                "chart_positions": chart["positions"] if chart else {},
                "source": {"doc": SOURCE, "pages": sorted(p["pages"]),
                           "chart_page": chart["page"] if chart else None},
            })
        dual = parse_dual_charts(pdf)
    dump_json({"gir_structure": GIR_STRUCTURE, "programmes": out, "dual_charts": dual}, "programmes.json")
    dump_json({"huel_pool": huel, "other_courses": other,
               "rule": "A student cannot count a course (or its equivalent) of his/her own discipline(s) as a humanities elective even if it is listed in this pool.",
               "source": {"doc": SOURCE, "pages": [333, 334, 335]}}, "huel_pool.json")
    return out, huel, other


# ---------------------------------------------------------------------------
# Minor programmes (IV-129 .. IV-141, PDF pages 337-349). These pages are ruled
# tables, so pdfplumber's table extraction recovers the merged category cells
# ('Core Courses', 'Electives (Science Pool) 01 (min)') on the first row of each block.
MINOR_PAGES = range(337, 350)


def parse_minors(pdf):
    minors, cur, category = [], None, None
    # titles in reading order; a few title rows sit outside the ruled table (e.g. 'Minor in Film and Media')
    titles = []
    for pn in MINOR_PAGES[1:]:  # p.337 is the general intro listing all minors in one sentence
        for t in re.findall(r"(?m)^\s*(Minor in [A-Z][^\n.]{3,60})\s*$", pdf.pages[pn - 1].extract_text() or ""):
            t = re.sub(r"\s+", " ", t).strip()
            if t not in titles and not t.lower().startswith("minor in entrepreneurship aims"):
                titles.append(t)
    for pn in MINOR_PAGES:
        for table in pdf.pages[pn - 1].extract_tables():
            for row in table:
                cells = [(c or "").strip() for c in row]
                first = cells[0]
                if first.startswith("Minor in"):
                    name = re.sub(r"\s+", " ", first)
                    prev = next((m for m in minors if m["name"] == name), None)
                    if prev is not None:  # table continued on the next page with a repeated title row
                        cur = prev
                        if pn not in cur["source"]["pages"]:
                            cur["source"]["pages"].append(pn)
                        continue
                    cur = {"name": name, "requirement": None, "core": [], "electives": [],
                           "pools": {}, "source": {"doc": SOURCE, "pages": [pn]}}
                    minors.append(cur)
                    category = None
                    continue
                if cur is None:
                    continue
                if pn not in cur["source"]["pages"]:
                    cur["source"]["pages"].append(pn)
                joined = " ".join(c for c in cells if c)
                if first.startswith("Courses") or "courses (min)" in joined.lower():
                    m = re.search(r"(\d+)\s*courses?\s*\(min\)\s*,?\s*(\d+)\s*units?", joined, re.I)
                    if m:
                        cur["requirement"] = {"courses": int(m.group(1)), "units": int(m.group(2))}
                    continue
                if first.startswith("Description"):
                    if cur.get("requirement") is not None:
                        # a new minor whose title row is outside the table
                        used = {m["name"] for m in minors}
                        after = titles[titles.index(cur["name"]) + 1:] if cur["name"] in titles else titles
                        nxt = next((t for t in after if t not in used), None)
                        if nxt:
                            cur = {"name": nxt, "requirement": None, "core": [], "electives": [],
                                   "pools": {}, "source": {"doc": SOURCE, "pages": [pn]}}
                            minors.append(cur)
                            category = None
                    continue
                if first:
                    category = re.sub(r"\s+", " ", first)
                code_cell = next((c for c in cells[1:3] if re.match(r"^[A-Z]{2,5}(/[A-Z]{2,5})?\s*[A-Z]\d{3}", c)), None)
                if not code_cell or category is None:
                    continue
                # 'EEE/INSTR F432' -> two alternative codes; 'CHE F243 / ME F213' -> alternatives
                opts = []
                for part in re.split(r"\s*/\s*(?=[A-Z]{2,5}\s*[A-Z]\d{3})|\n", code_cell):
                    m = re.match(r"^([A-Z]{2,5})(?:/([A-Z]{2,5}))?\s*([A-Z]\d{3}[A-Z]?)", part.strip())
                    if m:
                        for dept in filter(None, (m.group(1), m.group(2))):
                            c = norm_code(f"{dept} {m.group(3)}")
                            if c:
                                opts.append(c)
                if not opts:
                    continue
                units = next((int(x) for x in reversed(cells) if re.fullmatch(r"\d{1,2}", x or "")), None)
                title_idx = cells.index(code_cell) + 1
                entry = {"options": opts, "title": re.sub(r"\s+", " ", cells[title_idx]) if title_idx < len(cells) else None,
                         "units": units, "source": {"doc": SOURCE, "page": pn}}
                if category.lower().startswith("core"):
                    cur["core"].append(entry)
                else:
                    cur["electives"].append(entry)
                    pool = re.search(r"\(([^)]*Pool[^)]*)\)\s*(\d+)?", category)
                    if pool:
                        cur["pools"].setdefault(pool.group(1), {"min_courses": int(pool.group(2)) if pool.group(2) else None,
                                                                "codes": []})["codes"].extend(opts)
    return minors


MINOR_RULES = {
    "core_max": {"courses": 4, "units": 12},
    "electives_min": {"courses": 2, "units": 6},
    "total_min": {"courses": 5, "units": 15},
    "overlap": "At most 2 courses (and at most 6 units) of the minor requirement may be met by mandatory courses of the "
               "student's degree(s) (GIR excluding Humanities, or discipline core). No course may count toward two minors, "
               "or toward two majors and a minor.",
    "project_limit": "At most one project/seminar type course may be used for a minor.",
    "gpa": "Cumulative GPA of 4.5 or above (out of 10) in the courses applied to the minor.",
    "declaration": "Declared at the end of the 2nd year.",
    "source": {"doc": SOURCE, "page": 337, "section": "IV-129"},
}


def build_minors():
    with pdfplumber.open(BULLETIN_PDF) as pdf:
        minors = parse_minors(pdf)
    dump_json({"rules": MINOR_RULES, "minors": minors}, "minors.json")
    return minors


if __name__ == "__main__":
    progs, huel, other = build()
    for p in progs:
        for v in p["verification"]:
            print("   VERIFY", p["id"], v)
        n_cdc = len(p["cdc"])
        u_cdc = sum((g["options"][0]["units"] or 0) for g in p["cdc"])
        print(f"{p['id']:9s} {p['name'][:45]:45s} CDC {n_cdc:2d} ({u_cdc:2d}u) vs chart {p['cdc_total']}  DEL {len(p['del']):3d} {p['del_total']}  GIR {len(p['gir_named'])} p{p['chart_page']}")
    print("HUEL pool:", len(huel), " other courses:", len(other))
    minors = build_minors()
    print("minors:", len(minors))
