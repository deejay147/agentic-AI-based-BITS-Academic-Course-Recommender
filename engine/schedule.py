"""Timetable clash checks (reg 3.19: the semester programme has to be clash-free).

Two kinds of clash:
  - class clash: lecture / tutorial / practical hours overlapping
  - exam clash: same midsem date+session, or same compre date+session

For courses the student is already registered in we usually don't know which section
they're in. So a registered course only "blocks" hours when that component has exactly
one section (then there's no choice). Multi-section components are left out and we say so.

For a candidate course we try to find *some* section per component that fits -
if the student can pick L2 instead of L1 to dodge a clash, the course is still fine.
"""
from __future__ import annotations

from itertools import product

from engine.catalog import Catalog


def pick_offering(cat: Catalog, code: str, batch: int):
    """A course can appear twice in the timetable (old comcod + a >=5000 one for 2026 admits).
    2026 admits get the >=5000 row when there is one, everyone else never sees it."""
    offs = cat.offerings.get(code, [])
    if batch >= 2026:
        new = [o for o in offs if o["only_2026_admits"]]
        return new[0] if new else (offs[0] if offs else None)
    old = [o for o in offs if not o["only_2026_admits"]]
    return old[0] if old else None


def _by_type(offering):
    out = {}
    for s in offering["sections"]:
        out.setdefault(s["type"], []).append(s)
    return out


def _slot_set(section):
    return {(s["day"], s["hour"]) for s in section["slots"]}


def busy_from_registered(cat: Catalog, codes: list[str], batch: int) -> dict:
    busy, exams, unknown = {}, {"midsem": {}, "compre": {}}, []
    for code in codes:
        off = pick_offering(cat, code, batch)
        if not off:
            continue
        for kind, secs in _by_type(off).items():
            if len(secs) == 1:
                for sl in _slot_set(secs[0]):
                    busy[sl] = f"{code} {secs[0]['section']}"
            elif len(secs) > 1:
                unknown.append(f"{code} ({kind})")
        if off["midsem_date"]:
            exams["midsem"][(off["midsem_date"], off["midsem_session"])] = code
        if off["compre_date"]:
            exams["compre"][(off["compre_date"], off["compre_session"])] = code
    return {"slots": busy, "exams": exams, "unknown_sections": unknown}


def check_course(off, busy: dict, avoid_hours: set | None = None) -> dict:
    """Returns {ok, reason, sections: {type: section_id}} for one candidate offering."""
    exam_clash = []
    k = (off["midsem_date"], off["midsem_session"])
    if off["midsem_date"] and k in busy["exams"]["midsem"]:
        exam_clash.append(f"midsem {off['midsem_date']} {off['midsem_session']} clashes with {busy['exams']['midsem'][k]}")
    k = (off["compre_date"], off["compre_session"])
    if off["compre_date"] and k in busy["exams"]["compre"]:
        exam_clash.append(f"compre {off['compre_date']} {off['compre_session']} clashes with {busy['exams']['compre'][k]}")
    if exam_clash:
        return {"ok": False, "reason": "; ".join(exam_clash), "sections": {}}

    kinds = _by_type(off)
    if not kinds:
        return {"ok": True, "reason": None, "sections": {}}
    choices = []
    for kind, secs in kinds.items():
        fits = []
        for s in secs:
            ss = _slot_set(s)
            if ss & set(busy["slots"]):
                continue
            if avoid_hours and ss & avoid_hours:
                continue
            fits.append(s)
        if not fits:
            hit = {busy["slots"][x] for s in secs for x in _slot_set(s) if x in busy["slots"]}
            return {"ok": False, "reason": f"every {kind} section clashes with " + ", ".join(sorted(hit)),
                    "sections": {}}
        choices.append((kind, fits))
    # sections of the same course can't clash with each other either (lecture vs its own lab)
    for combo in product(*[f for _, f in choices]):
        taken = set()
        good = True
        for s in combo:
            ss = _slot_set(s)
            if ss & taken:
                good = False
                break
            taken |= ss
        if good:
            return {"ok": True, "reason": None,
                    "sections": {s["type"]: s["section"] for s in combo},
                    "alternatives": {k: [s["section"] for s in f] for k, f in choices}}
    return {"ok": False, "reason": "no combination of its own sections fits", "sections": {}}


def plan_sections(offerings: list, busy: dict, avoid_hours: set | None = None) -> dict:
    """Pick one section per component for *all* chosen courses together so nothing overlaps
    (plain backtracking - a semester is ~6 courses, it's tiny). Exams are checked pairwise too.

    offerings: [(code, offering_row)]. Returns {ok, sections: {code: {type: sec}}, problems: [...]}"""
    problems = []
    # exams between the chosen courses themselves + against registered ones
    seen = {"midsem": dict(busy["exams"]["midsem"]), "compre": dict(busy["exams"]["compre"])}
    for code, off in offerings:
        for kind, d, sess in (("midsem", off["midsem_date"], off["midsem_session"]),
                              ("compre", off["compre_date"], off["compre_session"])):
            if not d:
                continue
            if (d, sess) in seen[kind]:
                problems.append(f"{kind} of {code} ({d} {sess}) clashes with {seen[kind][(d, sess)]}")
            else:
                seen[kind][(d, sess)] = code
    if problems:
        return {"ok": False, "sections": {}, "problems": problems}

    # one decision per (course, component)
    slots = []
    for code, off in offerings:
        for kind, secs in _by_type(off).items():
            slots.append((code, kind, secs))
    blocked = set(busy["slots"]) | (avoid_hours or set())
    chosen = {}

    def bt(i, taken):
        if i == len(slots):
            return True
        code, kind, secs = slots[i]
        for s in secs:
            ss = _slot_set(s)
            if ss & taken or ss & blocked:
                continue
            chosen.setdefault(code, {})[kind] = s["section"]
            if bt(i + 1, taken | ss):
                return True
            chosen[code].pop(kind, None)
        return False

    if bt(0, set()):
        return {"ok": True, "sections": chosen, "problems": []}
    return {"ok": False, "sections": {}, "problems": ["no clash-free section combination exists for this set of courses"]}
