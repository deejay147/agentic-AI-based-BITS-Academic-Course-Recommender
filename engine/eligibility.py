"""Eligible course set for the semester being planned (First Semester 2026-27).

For every course in the timetable we decide two things:
  1. which requirement it would fill for THIS student (CDC / GIR / DEL / HUEL / OPEL)
  2. whether the student is allowed to take it, going rule by rule

Each check is recorded with the clause it comes from, so the answer can say *why*.
A course is eligible only if every check passes. Checks that can't be evaluated with the
supplied data (e.g. the CGPA cutoff for higher degree courses) are kept as notes, not failures.
"""
from __future__ import annotations

from engine import requirements as req
from engine.catalog import NON_RECOMMENDABLE_RE, PROJECT_RE, Catalog
from engine.profile import Profile
from engine.schedule import busy_from_registered, check_course, pick_offering

MAX_UNITS = 25


def _level(code: str) -> int | None:
    # 'CS F407' -> 4. bulletin VI-1 point 5: the number "generally determines the level at which
    # the course is to be normally registered" - used for prior-preparation checks
    num = code.split()[1]
    return int(num[1]) if num[1].isdigit() else None


def _position(cat: Catalog, pid: str, code: str, pids: list[str] | None = None) -> tuple[int, int]:
    """(year, semester) of a named course from the semester chart; dual degree students use the
    composite chart for their pair. Falls back to the course number level (bulletin VI-1 pt 5)."""
    pos = None
    if pids and len(pids) == 2:
        pos = cat.dual_charts.get(f"{pids[0]}+{pids[1]}", {}).get("positions", {}).get(code)
    pos = pos or cat.programmes[pid]["chart_positions"].get(code)
    if pos and pos[1] in (1, 2):
        return tuple(pos)
    return (_level(code) or 1, 1)


def _group_position(cat: Catalog, pid: str, opts: list[str], pids: list[str]) -> tuple[int, int]:
    """Position of a named slot = position of whichever of its options is on the chart
    (the chart may name EEE F211 while the student's option is INSTR F211)."""
    for o in opts:
        charts = [cat.programmes[pid]["chart_positions"]]
        if len(pids) == 2:
            charts.insert(0, cat.dual_charts.get(f"{pids[0]}+{pids[1]}", {}).get("positions", {}))
        for ch in charts:
            pos = ch.get(o)
            if pos and pos[1] in (1, 2):
                return tuple(pos)
    return _position(cat, pid, opts[0], pids)


def _missing_prior(cat: Catalog, state: dict, pids: list[str], before: tuple[int, int]) -> list[str]:
    """Named courses (GIR + CDC) prescribed in semesters before `before` = (year, sem) that
    aren't cleared yet. That's the 'prior preparation' of reg 3.14 / 3.15."""
    cleared = set(state["cleared"])
    missing = []
    for pid in pids:
        slots = req._gir_groups(cat, [pid]) + [[m["code"] for m in g] for g in cat.programmes[pid]["cdc_groups"]]
        for opts in slots:
            if _group_position(cat, pid, opts, pids) >= before:
                continue
            if not any(cat.canon(o) in cleared for o in opts) and opts[0] not in missing:
                missing.append(opts[0])
    return missing


def evaluate(profile: Profile, cat: Catalog, state: dict | None = None) -> dict:
    state = state or req.compute(profile, cat)
    pids = [p for p in profile.programmes if p in cat.programmes]
    cleared, registered = set(state["cleared"]), set(state["registered"])
    own_depts = set(state["own_depts"])
    b = state["_buckets"]

    # what's still open
    open_cdc = {}
    for prog in state["programmes"]:
        for opts in prog["cdc"].get("remaining", []):
            for o in opts:
                open_cdc[cat.canon(o)] = prog["id"]
    open_gir = {cat.canon(o) for g in state["gir"].get("remaining", []) for o in g}
    own_del = {}
    for pid in pids:
        for c in cat.programmes[pid]["del_codes"]:
            own_del.setdefault(cat.canon(c), pid)

    # reg 3.15(b)(i): other degrees' CDC/DEL only after prior prep of own 3rd year 1st sem,
    # i.e. every named course of years 1-2 (course level 1 or 2) cleared
    missing_3_1 = _missing_prior(cat, state, pids, (3, 1))
    # reg 3.15(b)(ii): own-discipline higher degree courses after the first set of CDCs (the 2nd-year ones)
    missing_first_cdcs = []
    for pid in pids:
        for g in cat.programmes[pid]["cdc_groups"]:
            opts = [m["code"] for m in g]
            if _level(opts[0]) == 2 and not any(cat.canon(o) in cleared for o in opts):
                missing_first_cdcs.append(opts[0])

    busy = busy_from_registered(cat, profile.current, profile.batch, profile.current_sections)
    reg_units = sum(cat.units(c) or 0 for c in profile.current)
    hd_registered = [c for c in profile.current if _level(c) and c.split()[1][0] == "G"]

    eligible, ineligible = [], []
    for code in sorted(cat.offerings):
        off = pick_offering(cat, code, profile.batch)
        if off is None:
            continue   # only offered to 2026 admits
        cn = cat.canon(code)
        if cn in cleared or cn in registered:
            continue
        if NON_RECOMMENDABLE_RE.search(code):
            continue   # PS / thesis / dissertation
        checks = []

        def check(rule, ok, note, clause):
            checks.append({"rule": rule, "ok": ok, "note": note, "clause": clause})

        lvl_char = code.split()[1][0]
        level = _level(code)
        is_project = bool(PROJECT_RE.search(code))

        # ---------- category
        if cn in open_cdc:
            category = "CDC"
            why = f"remaining core course of {open_cdc[cn]}"
        elif cn in open_gir:
            category = "GIR"
            why = "remaining general institutional requirement"
        elif cn in own_del or (is_project and code.split()[0] in own_depts):
            pid = own_del.get(cn, pids[0] if pids else None)
            full = b["del"][pid].is_full(cat) if pid in b["del"] else True
            category = "OPEL" if full else "DEL"
            why = (f"in the {pid} discipline elective pool" if not full
                   else f"in the {pid} DEL pool, but DEL requirement is already met so it counts as an OPEL (reg 2.05)")
        elif code in cat.huel and code.split()[0] not in own_depts:
            full = b["huel"].is_full(cat)
            category = "OPEL" if full else "HUEL"
            why = "in the humanities elective pool" if not full else \
                "in the HUEL pool, but HUEL is already met so it counts as an OPEL (reg 2.05)"
        else:
            category = "OPEL"
            why = "counts as an open elective (reg 2.05)"

        # ---------- rules
        # 2026-only timetable rows are already filtered by pick_offering; U-codes are the new curriculum
        if lvl_char == "U" and profile.batch < 2026:
            check("new_curriculum_only", False, "new credit-hour curriculum course (2026 admits)", "Timetable note")

        # first-year foundation courses of other programmes aren't elective host regions
        if level == 1 and category not in ("GIR", "CDC"):
            check("first_year_course", False, "first-year foundation course, not open as an elective",
                  "Reg 2.07 / 3.18")

        # other degrees' CDC / DEL -> reg 3.15(b)(i)
        owners = cat.discipline_owner.get(code, set()) | cat.discipline_owner.get(cn, set())
        other_disc = category == "OPEL" and owners and not (owners & set(pids)) and code not in cat.huel \
            and lvl_char != "G"
        if other_disc:
            ok = not missing_3_1
            check("other_discipline_prior_prep", ok,
                  None if ok else f"CDC/DEL of {', '.join(sorted(owners))}; needs all your year 1-2 named courses "
                                  f"cleared first (missing: {', '.join(missing_3_1[:6])}"
                                  f"{'...' if len(missing_3_1) > 6 else ''})", "Reg 3.15(b)(i)")

        # other programmes' project courses: allowed as OPEL (max 3), flag it
        if is_project and code.split()[0] not in own_depts:
            check("project_course", True, "project-type course of another discipline; needs a supervisor and "
                  "counts toward the 3 project courses allowed as OPEL", "Bulletin IV-125")

        # higher degree courses
        if lvl_char == "G":
            same_disc = code.split()[0] in own_depts
            check("hd_own_discipline", same_disc,
                  None if same_disc else "higher degree course of another discipline",
                  "Timetable V(A) / Reg 3.15(b)(ii)")
            if same_disc:
                ok = not missing_first_cdcs
                check("hd_first_cdcs", ok, None if ok else
                      "needs your 2nd-year CDCs cleared first (missing: " + ", ".join(missing_first_cdcs[:5]) + ")",
                      "Reg 3.15(b)(ii)")
                check("hd_one_per_sem", not hd_registered,
                      None if not hd_registered else f"already registered in {hd_registered[0]}", "Reg 2.08")
                check("hd_cgpa", True, "also needs CGPA above the AGC threshold, which isn't in the supplied data",
                      "Reg 2.08")

        # own CDCs: prior preparation (3.14 iii) = named courses of the semesters before it.
        # DCA can allow up to 2 missing (with no bearing on core), more than that is a no
        if category == "CDC":
            grp = next(([m["code"] for m in g] for g in cat.programmes[open_cdc[cn]]["cdc_groups"]
                        if any(cat.same(m["code"], code) for m in g)), [code])
            miss = _missing_prior(cat, state, pids, _group_position(cat, open_cdc[cn], grp, pids))
            if len(miss) > 2:
                check("cdc_prior_prep", False, f"{len(miss)} earlier named courses not cleared "
                      f"({', '.join(miss[:5])}...)", "Reg 3.14")
            elif miss:
                check("cdc_prior_prep", True, "needs DCA approval: not cleared " + ", ".join(miss), "Reg 3.14")

        # prerequisites (3.13) - only ~70 courses state any
        c = cat.courses.get(code) or {}
        pre, mode = c.get("prerequisite_codes") or [], c.get("prerequisite_mode")
        if pre:
            have = [p for p in pre if cat.canon(p) in cleared]
            ok = bool(have) if mode == "any" else len(have) == len(pre)
            check("prerequisites", ok, None if ok else
                  ("needs one of " if mode == "any" else "needs ") + ", ".join(pre), "Reg 3.13")
        elif c.get("prerequisite_text"):
            check("prerequisites", True, "stated prerequisite (check manually): " + c["prerequisite_text"][:150],
                  "Reg 3.13")

        # DEL/OPEL from a later year than the student is in: allowed, but worth saying
        if category in ("DEL", "OPEL", "HUEL") and level and lvl_char in "FU" and level > profile.year:
            check("course_level", True, f"level-{level} course; normally taken around year {level} "
                  "(bulletin VI-1, pt 5)", "Bulletin VI-1")

        # units cap
        u = cat.units(code) or 0
        if reg_units + u > MAX_UNITS:
            check("max_units", False, f"{reg_units} registered + {u} = {reg_units + u} > {MAX_UNITS} units",
                  "Reg 1.01")

        # timetable
        clash = check_course(off, busy)
        check("timetable", clash["ok"], clash["reason"], "Reg 3.19")

        ok = all(ch["ok"] for ch in checks)
        rec = {
            "code": code, "title": cat.title(code), "units": u, "category": category, "why": why,
            "eligible": ok, "checks": checks,
            "sections": clash.get("sections", {}), "section_options": clash.get("alternatives", {}),
            "ic": off.get("ic"), "midsem": (off["midsem_date"], off["midsem_session"]),
            "compre": (off["compre_date"], off["compre_session"]),
            "is_project": is_project, "is_higher_degree": lvl_char == "G",
            "timetable_source": off["source"],
        }
        (eligible if ok else ineligible).append(rec)

    return {"eligible": eligible, "ineligible": ineligible, "state": state,
            "registered_units": reg_units, "unknown_sections": busy["unknown_sections"]}
