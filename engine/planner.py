"""Semester planner: the student picks the electives they want this semester, we file them.

Flow:
  1. each picked course must be in the eligible set (otherwise we say which rule blocks it)
  2. filing into CDC / GIR / DEL / HUEL / OPEL is done by re-running the requirement
     calculation with the picks added as 'registered' - same filing logic as everywhere else,
     so there's exactly one place that decides categories
  3. sections for all picks are chosen together so nothing clashes (reg 3.19)
  4. 25 unit cap (reg 1.01) and the 'max 4 electives above requirement' rule (reg 2.08)

schedule_registered=True: the courses the student is already registered in are timetabled too -
we pick their sections together with the new picks (keeping any section the student told us),
so the week comes out complete instead of only blocking the hours we know for sure.
"""
from __future__ import annotations

import copy

from engine import eligibility as el
from engine import requirements as req
from engine.catalog import Catalog
from engine.profile import Profile
from engine.schedule import busy_from_registered, pick_offering, plan_sections
from ingest.common import norm_code

MAX_UNITS = 25


def _restrict(off: dict, allowed: dict | None) -> dict:
    """keep only the sections the student said they'd accept, per component ({"lecture": ["L1", "L2"]});
    an empty / missing list means any section is fine"""
    if not allowed:
        return off
    return {**off, "sections": [s for s in off["sections"]
                                if not allowed.get(s["type"]) or s["section"] in allowed[s["type"]]]}


def plan(profile: Profile, cat: Catalog, picks: list[str], avoid_hours: set | None = None,
         compact: bool = False, schedule_registered: bool = False, allowed: dict | None = None) -> dict:
    """allowed = {code: {component: [sections the student would accept]}} (optional)"""
    allowed = allowed or {}
    picks = [norm_code(p) or p.upper().strip() for p in picks]
    ev = el.evaluate(profile, cat)
    elig = {x["code"]: x for x in ev["eligible"]}
    inel = {x["code"]: x for x in ev["ineligible"]}

    rejected, ok_picks = [], []
    for code in picks:
        if code in elig:
            ok_picks.append(code)
        elif code in inel:
            why = [f"{c['note']} ({c['clause']})" for c in inel[code]["checks"] if not c["ok"]]
            rejected.append({"code": code, "reasons": why})
        else:
            rejected.append({"code": code, "reasons": ["not offered this semester, or already done / registered"]})

    # file the picks: pretend they're registered and recompute
    hypo = copy.deepcopy(profile)
    hypo.current = list(profile.current) + ok_picks
    after = req.compute(hypo, cat)
    filed = {}
    buckets = [("GIR", after["gir"])] + [(f"CDC ({p['id']})", p["cdc"]) for p in after["programmes"]] + \
              [(f"DEL ({p['id']})", p["del"]) for p in after["programmes"]] + \
              [("HUEL", after["huel"]), ("OPEL", after["opel"])]
    for code in ok_picks:
        for name, b in buckets:
            if any(cat.same(code, c) for c in b["in_progress"]):
                filed[code] = name
                break
        filed.setdefault(code, "OPEL")

    offs = [(c, _restrict(pick_offering(cat, c, profile.batch), allowed.get(c))) for c in ok_picks]
    reg_offs = []
    if schedule_registered:
        known = profile.current_sections or {}
        for code in profile.current:
            off = pick_offering(cat, code, profile.batch)
            if not off:
                continue
            mine = known.get(code, {})
            if mine:   # keep only the section the student said they're in, for that component
                off = {**off, "sections": [s for s in off["sections"] if mine.get(s["type"]) in (None, s["section"])]}
            reg_offs.append((code, _restrict(off, allowed.get(code))))
        busy = busy_from_registered(cat, [], profile.batch)      # nothing fixed, everything is chosen together
    else:
        busy = busy_from_registered(cat, profile.current, profile.batch, profile.current_sections)
    sched = plan_sections(reg_offs + offs, busy, avoid_hours, compact)

    units = ev["registered_units"] + sum(cat.units(c) or 0 for c in ok_picks)
    warnings = []
    if units > MAX_UNITS:
        warnings.append(f"{units} units in total, more than the {MAX_UNITS} allowed per semester (reg 1.01)")
    # electives beyond what the programme needs (reg 2.08 allows up to 4 extra)
    extra = 0
    for name, b in buckets:
        if name.startswith(("DEL", "HUEL", "OPEL")):
            have = len(b["done"]) + len(b["in_progress"])
            extra += max(0, have - (b.get("required_courses") or 0))
    if extra > 4:
        warnings.append(f"{extra} electives above requirement; at most 4 extra are allowed (reg 2.08)")
    if ev["unknown_sections"] and not schedule_registered:
        warnings.append("Sections not given for: " + ", ".join(ev["unknown_sections"][:6]) +
                        ("..." if len(ev["unknown_sections"]) > 6 else "") +
                        ". Only exam slots and single-section components of those were clash-checked - "
                        "add your sections in the profile for a full check.")

    return {
        "picks": [{"code": c, "title": cat.title(c), "units": cat.units(c), "filed_as": filed[c],
                   "sections": sched["sections"].get(c, {})} for c in ok_picks],
        "registered": [{"code": c, "title": cat.title(c), "units": cat.units(c),
                        "sections": sched["sections"].get(c, {})} for c, _ in reg_offs],
        "rejected": rejected,
        "clash_free": sched["ok"], "clash_problems": sched["problems"],
        "gap_hours": sched.get("gap_hours"), "days_used": sched.get("days_used"),
        "total_units": units, "warnings": warnings,
        "requirements_after": after,
    }
