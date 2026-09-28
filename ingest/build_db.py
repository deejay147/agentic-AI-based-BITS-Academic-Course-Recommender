"""Load all the processed JSON into one SQLite db + run validation checks.

Run order (or just `python -m ingest.run_all`):
    timetable -> bulletin_programmes -> bulletin_courses -> handouts -> regulations_rules -> build_db

Nothing here parses PDFs, it only joins what the parsers produced. That's on purpose:
a new timetable / new handouts = rerun those parsers + this, engine code stays the same.

Anything that looks off goes into verification_queue.csv instead of being silently fixed.
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import defaultdict

from ingest.common import PROCESSED, load_json

DB_PATH = PROCESSED / "academic.db"

SCHEMA = """
CREATE TABLE courses (
    code TEXT PRIMARY KEY, dept TEXT, level TEXT, title TEXT, units INTEGER,
    description TEXT, prerequisite_text TEXT, prerequisite_codes TEXT, prerequisite_mode TEXT,
    is_higher_degree INTEGER, is_project_type INTEGER, source TEXT
);
CREATE TABLE equivalents (code TEXT, canonical TEXT, source TEXT);
CREATE TABLE offerings (
    comcod INTEGER, code TEXT, title TEXT, units TEXT, L TEXT, P TEXT, T TEXT, S TEXT,
    only_2026_admits INTEGER, ic TEXT,
    midsem_date TEXT, midsem_session TEXT, compre_date TEXT, compre_session TEXT,
    source TEXT
);
CREATE TABLE sections (
    comcod INTEGER, code TEXT, section TEXT, type TEXT, instructors TEXT, room TEXT,
    slots TEXT, slots_missing INTEGER
);
CREATE TABLE handouts (
    id INTEGER PRIMARY KEY, file TEXT, title TEXT, instructor_in_charge TEXT, extraction_method TEXT,
    eval_confidence TEXT, components TEXT, components_reliable INTEGER, project_weight REAL,
    has_midsem INTEGER, has_compre INTEGER, has_quiz INTEGER, has_assignment INTEGER, has_project INTEGER,
    has_lab INTEGER, has_viva_presentation INTEGER, open_book INTEGER, eval_evidence TEXT,
    makeup_status TEXT, makeup_note TEXT, makeup_evidence TEXT,
    attendance_status TEXT, attendance_evidence TEXT,
    prerequisites_text TEXT, topics_text TEXT
);
CREATE TABLE handout_codes (handout_id INTEGER, code TEXT);
CREATE TABLE programmes (
    id TEXT PRIMARY KEY, name TEXT, degree TEXT, curriculum TEXT,
    cdc_units INTEGER, cdc_courses INTEGER, del_units INTEGER, del_courses INTEGER,
    chart_page INTEGER, chart_positions TEXT, notes TEXT, verification TEXT, source TEXT
);
CREATE TABLE programme_courses (
    programme_id TEXT, category TEXT, group_id INTEGER, code TEXT, title TEXT, units INTEGER,
    track TEXT, compulsory INTEGER, source TEXT
);
CREATE TABLE huel_pool (code TEXT, title TEXT, units INTEGER, source TEXT);
CREATE TABLE minors (id INTEGER PRIMARY KEY, name TEXT, req_courses INTEGER, req_units INTEGER, source TEXT);
CREATE TABLE minor_courses (minor_id INTEGER, category TEXT, group_id INTEGER, code TEXT, title TEXT, units INTEGER, pool TEXT);
CREATE TABLE rules (id TEXT, clause TEXT, text TEXT, params TEXT, source TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

PROJECT_RE = re.compile(r" [A-Z](266|366|367|376|377|491)$")


def _j(x):
    return json.dumps(x, ensure_ascii=False)


def canonical_map(eq_groups):
    """Union-find over the equivalence list, so chains like CS F215 ~ EEE F215 ~ INSTR F215 collapse."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for g in eq_groups:
        codes = g["equivalents"]
        for c in codes[1:]:
            ra, rb = find(codes[0]), find(c)
            if ra != rb:
                parent[rb] = ra
    groups = defaultdict(list)
    for c in list(parent):
        groups[find(c)].append(c)
    out = {}
    for members in groups.values():
        # prefer the current (F-series) code as the canonical one
        canon = sorted(members, key=lambda c: (" F" not in c, c))[0]
        for m in members:
            out[m] = canon
    return out


def build():
    tt = load_json("timetable.json")
    eq = load_json("equivalents.json")
    progs = load_json("programmes.json")
    huel = load_json("huel_pool.json")
    minors = load_json("minors.json")
    bcourses = load_json("courses_bulletin.json")
    hand = load_json("handouts.json")
    rules = load_json("rules.json")

    issues = []  # (kind, subject, detail, source)

    if DB_PATH.exists():
        DB_PATH.unlink()
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)

    # ---- courses: bulletin descriptions first, then fill gaps from programme lists / timetable
    courses = {}
    for c in bcourses:
        courses[c["code"]] = {
            "code": c["code"], "title": c["title"], "units": c["units"], "description": c["description"],
            "prerequisite_text": c["prerequisite_text"], "prerequisite_codes": c["prerequisite_codes"],
            "prerequisite_mode": c["prerequisite_mode"], "source": c["source"],
        }

    def ensure(code, title=None, units=None, source=None):
        if code not in courses:
            courses[code] = {"code": code, "title": title, "units": units, "description": None,
                             "prerequisite_text": None, "prerequisite_codes": [], "prerequisite_mode": None,
                             "source": source}
        else:
            c = courses[code]
            c["title"] = c["title"] or title
            c["units"] = c["units"] or units

    for p in progs["programmes"]:
        for cat in ("cdc", "del"):
            for g in p[cat]:
                for o in g["options"]:
                    ensure(o["code"], o["title"], o["units"], o["source"])
    for h in huel["huel_pool"] + huel["other_courses"]:
        ensure(h["code"], h["title"], h["units"], h["source"])
    for off in tt:
        u = int(off["units"]) if (off["units"] or "").isdigit() and not off["only_2026_admits"] else None
        ensure(off["code"], off["title"].title(), u, off["source"])

    for c in courses.values():
        dept, rest = c["code"].split(" ")
        db.execute("INSERT INTO courses VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            c["code"], dept, rest[0], c["title"], c["units"], c["description"], c["prerequisite_text"],
            _j(c["prerequisite_codes"]), c["prerequisite_mode"],
            int(rest[0] == "G"), int(bool(PROJECT_RE.search(c["code"]))), _j(c["source"])))

    # ---- equivalents
    canon = canonical_map(eq)
    for code, cn in canon.items():
        db.execute("INSERT INTO equivalents VALUES (?,?,?)", (code, cn, _j({"doc": "timetable.pdf", "section": "IX"})))

    # ---- timetable
    for off in tt:
        db.execute("INSERT INTO offerings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            off["comcod"], off["code"], off["title"], off["units"], off["L"], off["P"], off["T"], off["S"],
            int(off["only_2026_admits"]), off.get("ic"),
            (off["midsem"] or {}).get("date"), (off["midsem"] or {}).get("session"),
            (off["compre"] or {}).get("date"), (off["compre"] or {}).get("session"), _j(off["source"])))
        for s in off["sections"]:
            db.execute("INSERT INTO sections VALUES (?,?,?,?,?,?,?,?)", (
                off["comcod"], off["code"], s["section"], s["type"], _j(s["instructors"]), s["room"],
                _j(s["slots"]), int(s["slots_missing"])))
            if s["slots_missing"] and not PROJECT_RE.search(off["code"]) and not off["code"].endswith("T"):
                issues.append(("timetable_slots_missing", off["code"], f"section {s['section']} has no day/hour",
                               f"timetable.pdf p.{off['source']['page']}"))

    # ---- handouts
    for i, h in enumerate(hand, start=1):
        ev = h["evaluation"]
        db.execute("INSERT INTO handouts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            i, h["file"], h["title"], h["instructor_in_charge"], h["extraction_method"],
            ev["confidence"], _j(ev["components"]), int(ev["components_reliable"]), ev["project_weight"],
            int(ev["has_midsem"]), int(ev["has_compre"]), int(ev["has_quiz"]), int(ev["has_assignment"]),
            int(ev["has_project"]), int(ev["has_lab"]), int(ev["has_viva_presentation"]), int(ev["open_book"]),
            ev["evidence"], h["makeup"]["status"], h["makeup"].get("note"), h["makeup"]["evidence"],
            h["attendance"]["status"], h["attendance"]["evidence"], h["prerequisites_text"], h["topics_text"]))
        for c in h["codes"]:
            db.execute("INSERT INTO handout_codes VALUES (?,?)", (i, c))
        if h["extraction_method"] == "ocr":
            issues.append(("handout_ocr", ",".join(h["codes"]), "scanned PDF, text came from OCR", h["file"]))

    # ---- programmes
    for p in progs["programmes"]:
        ct, dt = p["cdc_total"] or {}, p["del_total"] or {}
        db.execute("INSERT INTO programmes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            p["id"], p["name"], p["degree"], p["curriculum"], ct.get("units"), ct.get("courses"),
            dt.get("units"), dt.get("courses"), p["chart_page"], _j(p.get("chart_positions", {})), _j(p["notes"]), _j(p["verification"]),
            _j(p["source"])))
        for v in p["verification"]:
            issues.append(("programme_structure", p["id"], v, f"bulletin.pdf p.{p['chart_page']}"))
        gid = 0
        for cat in ("cdc", "del"):
            for g in p[cat]:
                gid += 1
                for o in g["options"]:
                    comp = int(o["code"] in p["compulsory_del"]) if cat == "del" else 1
                    db.execute("INSERT INTO programme_courses VALUES (?,?,?,?,?,?,?,?,?)", (
                        p["id"], cat.upper(), gid, o["code"], o["title"] or courses.get(o["code"], {}).get("title"),
                        o["units"] or courses.get(o["code"], {}).get("units"), g.get("track"), comp, _j(o["source"])))
        for code in p["gir_named"]:
            gid += 1
            db.execute("INSERT INTO programme_courses VALUES (?,?,?,?,?,?,?,?,?)", (
                p["id"], "GIR", gid, code, courses.get(code, {}).get("title"), courses.get(code, {}).get("units"),
                None, 1, _j({"doc": "bulletin.pdf", "page": p["chart_page"]})))
    db.execute("INSERT INTO meta VALUES (?,?)", ("gir_structure", _j(progs["gir_structure"])))
    db.execute("INSERT INTO meta VALUES (?,?)", ("dual_charts", _j(progs.get("dual_charts", {}))))
    db.execute("INSERT INTO meta VALUES (?,?)", ("gir_alternatives", _j(progs["programmes"][0]["gir_alternatives"])))

    for h in huel["huel_pool"]:
        db.execute("INSERT INTO huel_pool VALUES (?,?,?,?)", (h["code"], h["title"], h["units"], _j(h["source"])))
    db.execute("INSERT INTO meta VALUES (?,?)", ("huel_rule", huel["rule"]))

    # ---- minors
    db.execute("INSERT INTO meta VALUES (?,?)", ("minor_rules", _j(minors["rules"])))
    for i, m in enumerate(minors["minors"], start=1):
        req = m["requirement"] or {}
        db.execute("INSERT INTO minors VALUES (?,?,?,?,?)", (i, m["name"], req.get("courses"), req.get("units"),
                                                             _j(m["source"])))
        if not m["requirement"]:
            issues.append(("minor_structure", m["name"], "course/unit requirement not extracted",
                           f"bulletin.pdf p.{m['source']['pages']}"))
        gid = 0
        for cat, entries in (("core", m["core"]), ("elective", m["electives"])):
            for e in entries:
                gid += 1
                pool = next((k for k, v in m["pools"].items() if e["options"][0] in v["codes"]), None)
                for code in e["options"]:
                    db.execute("INSERT INTO minor_courses VALUES (?,?,?,?,?,?,?)",
                               (i, cat, gid, code, e["title"], e["units"], pool))

    for r in rules:
        db.execute("INSERT INTO rules VALUES (?,?,?,?,?)", (r["id"], r["clause"], r["text"],
                                                           _j(r.get("params")), _j(r["source"])))

    # ---- validation
    known = set(courses)
    offered = {o["code"] for o in tt}
    with_handout = {c for h in hand for c in h["codes"]}
    for p in progs["programmes"]:
        for cat in ("cdc", "del"):
            for g in p[cat]:
                for o in g["options"]:
                    if o["code"] not in {c["code"] for c in bcourses}:
                        issues.append(("course_not_in_bulletin_descriptions", o["code"],
                                       f"listed in {p['id']} {cat.upper()} but has no Part VI description",
                                       f"bulletin.pdf p.{o['source']['page']}"))
    for c in bcourses:
        for pc in c["prerequisite_codes"]:
            if pc not in known:
                issues.append(("prerequisite_unknown_code", c["code"], f"prerequisite {pc} not found in any source",
                               f"bulletin.pdf p.{c['source']['page']}"))
    for code in sorted(offered - with_handout):
        if PROJECT_RE.search(code) or code.endswith("T") or " U" in code:
            continue
        issues.append(("handout_missing", code, "offered this semester but no handout supplied", "handouts/"))

    db.commit()
    stats = {
        "courses": len(courses), "offerings": len(tt), "offered_codes": len(offered),
        "sections": sum(len(o["sections"]) for o in tt), "handouts_unique": len(hand),
        "codes_with_handout": len(with_handout & offered), "programmes": len(progs["programmes"]),
        "huel_pool": len(huel["huel_pool"]), "minors": len(minors["minors"]), "rules": len(rules),
        "equivalence_codes": len(canon), "verification_items": len(set(issues)),
    }
    for k, v in stats.items():
        db.execute("INSERT INTO meta VALUES (?,?)", ("stat_" + k, str(v)))
    db.commit()
    db.close()

    # same course can appear twice (old + credit-hour comcod), no point listing it twice
    issues = list(dict.fromkeys(issues))
    with open(PROCESSED / "verification_queue.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "subject", "detail", "source"])
        w.writerows(issues)
    _write_report(stats, issues)
    return stats, issues


def _write_report(stats, issues):
    kinds = defaultdict(int)
    for k, *_ in issues:
        kinds[k] += 1
    lines = ["# Data validation report", "", "Generated by `python -m ingest.build_db`.", "", "## Counts", ""]
    lines += [f"- {k.replace('_', ' ')}: {v}" for k, v in stats.items()]
    lines += ["", "## Items marked for verification", "", "| kind | count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(kinds.items(), key=lambda x: -x[1])]
    lines += ["", "Full list: `verification_queue.csv`. These are never guessed at runtime - the app says "
                  "the property could not be verified instead."]
    (PROCESSED / "validation_report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    stats, issues = build()
    print(json.dumps(stats, indent=1))
