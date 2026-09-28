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
from engine.schedule import busy_from_registered, pick_offering, plan_sections, section_options
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
         compact: bool = False, schedule_registered: bool = False, allowed: dict | None = None,
         include_ineligible: bool = False, options: int = 0) -> dict:
    """allowed = {code: {component: [sections the student would accept]}} (optional)
    include_ineligible: courses the student isn't allowed to take are still timetabled (so they can see
    clashes), and come back under 'not_allowed' with the rules that block them."""
    allowed = allowed or {}
    picks = [norm_code(p) or p.upper().strip() for p in picks]
    ev = el.evaluate(profile, cat)
    elig = {x["code"]: x for x in ev["eligible"]}
    inel = {x["code"]: x for x in ev["ineligible"]}

    rejected, ok_picks, not_allowed = [], [], {}
    for code in picks:
        if code in elig:
            ok_picks.append(code)
        elif code in inel:
            why = [f"{c['note']} ({c['clause']})" for c in inel[code]["checks"] if not c["ok"]]
            if include_ineligible:
                ok_picks.append(code)
                not_allowed[code] = why
            else:
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

    # doesn't fit together: add the picks one at a time (in the order given - core courses first) and keep
    # the ones that fit, so the rest of the week still gets a timetable; then say exactly what each
    # leftover course collides with
    clash_details, unplaced = [], []
    if not sched["ok"]:
        fit = []
        for code, off in offs:
            if plan_sections(reg_offs + fit + [(code, off)], busy, avoid_hours)["ok"]:
                fit.append((code, off))
                continue
            partners, notes = [], []
            alone = plan_sections([(code, off)], busy, avoid_hours)
            if not alone["ok"]:
                notes += alone["problems"] if schedule_registered or alone["problems"][0].startswith(("midsem", "compre")) \
                    else ["clashes with hours your registered courses already use"]
            empty = busy_from_registered(cat, [], profile.batch)
            for other, o_off in reg_offs + fit:
                pair = plan_sections([(code, off), (other, o_off)], empty, avoid_hours)
                if not pair["ok"]:
                    partners.append(other)
                    notes += [p for p in pair["problems"] if p.startswith(("midsem", "compre"))]
            if not partners and not notes:
                notes.append("fits with each course on its own, but not with all of them together")
            if avoid_hours and plan_sections([(code, off)], empty, None)["ok"] and \
                    not plan_sections([(code, off)], empty, avoid_hours)["ok"]:
                notes.append("no section of it fits your time preferences (no 8 AM / free day)")
            clash_details.append({"code": code, "with": partners, "problems": list(dict.fromkeys(notes))})
            first = {}
            for s_ in off["sections"]:
                first.setdefault(s_["type"], s_["section"])
            unplaced.append({"code": code, "sections": first})
        sched_fit = plan_sections(reg_offs + fit, busy, avoid_hours, compact)
        if sched_fit["ok"]:
            sched = {**sched_fit, "ok": False, "problems": sched["problems"]}

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

    placed = {u["code"]: u["sections"] for u in unplaced}
    # other timetables to flip through (same courses, different sections), best first
    opts = []
    if options:
        unfit = set(placed)
        opts = section_options(reg_offs + [(c, o) for c, o in offs if c not in unfit], busy, avoid_hours, n=options)
    return {
        "picks": [{"code": c, "title": cat.title(c), "units": cat.units(c), "filed_as": filed[c],
                   "sections": sched["sections"].get(c) or placed.get(c, {}), "fits": c not in placed,
                   "not_allowed": not_allowed.get(c)} for c in ok_picks],
        "clash_details": clash_details,
        "options": opts,
        "registered": [{"code": c, "title": cat.title(c), "units": cat.units(c),
                        "sections": sched["sections"].get(c, {})} for c, _ in reg_offs],
        "rejected": rejected,
        "clash_free": sched["ok"], "clash_problems": sched["problems"],
        "gap_hours": sched.get("gap_hours"), "days_used": sched.get("days_used"),
        "total_units": units, "warnings": warnings,
        "requirements_after": after,
    }
