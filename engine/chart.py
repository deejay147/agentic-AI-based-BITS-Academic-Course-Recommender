"""Semester-chart helpers: which named courses come before / in a given semester.

Used by the dashboard's 'pre-fill from my chart' button and by the test profile generator.
Single degree -> the programme's chart; dual degree -> year 1 of the first degree's chart
+ the composite dual chart for the pair (bulletin p.242-313).
"""


def _positions(cat, pids):
    """chart positions for a single degree, or year 1 of the M.Sc. chart + the composite
    dual chart (years 2-5) for a dual degree"""
    if len(pids) == 1:
        return cat.programmes[pids[0]]["chart_positions"]
    first = {c: p for c, p in cat.programmes[pids[0]]["chart_positions"].items() if p[0] == 1}
    return {**first, **cat.dual_charts[f"{pids[0]}+{pids[1]}"]["positions"]}


def named_until(cat, pids, year, sem, pick=None):
    """All named courses in semesters strictly before (year, sem). For an OR pair only the
    first option (or the one in `pick`) is taken."""
    pick = pick or {}
    out, skip = [], set()
    alts = {c: g for g in cat.gir_alternatives for c in g}
    for code, (y, s) in _positions(cat, pids).items():
        if s == 0 or code.startswith("BITS F4") or (y, s) >= (year, sem) or code in skip:
            continue
        if code in alts:
            chosen = next((c for c in alts[code] if c in pick), alts[code][0])
            skip.update(alts[code])
            if chosen not in out:
                out.append(chosen)
            continue
        out.append(code)
    return out


def named_in(cat, pids, year, sem, pick=None):
    pick = pick or {}
    alts = {c: g for g in cat.gir_alternatives for c in g}
    # CDC 'A or B' slots too, so e.g. 'MATH F212 or ME F344' only adds one of them
    for pid in pids:
        for g in cat.programmes[pid]["cdc_groups"]:
            codes = [m["code"] for m in g]
            if len(codes) > 1:
                for c in codes:
                    alts.setdefault(c, codes)
    out, skip = [], set()
    for code, (y, s) in _positions(cat, pids).items():
        if (y, s) == (year, sem) and code not in skip and not code.startswith("BITS F4"):
            if code in alts:
                skip.update(alts[code])
                code = next((c for c in alts[code] if c in pick), alts[code][0])
            out.append(code)
    return out


def due_by(cat, pids, year, sem):
    """named courses (GIR + CDC) the chart puts in this semester or earlier - what a planner
    should fill in by default. Both options of an OR slot are returned; callers keep what's eligible."""
    return [c for c, (y, s) in _positions(cat, pids).items()
            if s in (1, 2) and (y, s) <= (year, sem) and not c.startswith("BITS F4")]
