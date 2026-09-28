"""Builds the test profiles in tests/profiles/*.json from the semester charts.

Run once (`python -m tests.make_profiles`); the json files are what get committed and
reviewed. Completed courses are the named courses of the semesters before the one the
student is in now (from the ID's batch), current courses are that semester's named courses.
Grades are made up but plausible - they only matter for NC handling and the minor GPA.
"""
import json
import random
from pathlib import Path

from engine.catalog import get_catalog

OUT = Path(__file__).parent / "profiles"
random.seed(7)
GRADES = ["A", "A-", "B", "B", "B-", "C", "C", "C-", "D"]


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


def graded(codes, overrides=None):
    overrides = overrides or {}
    return [{"code": c, "grade": overrides.get(c, random.choice(GRADES))} for c in codes]


def prof(id_no, name, pids, completed, current, interests, cgpa, minor=None, stream=None):
    batch = int(id_no[:4])
    return {"id_no": id_no, "name": name, "batch": batch, "programmes": pids, "stream": stream,
            "completed": completed, "current": current, "minor": minor, "interests": interests, "cgpa": cgpa}


def main():
    cat = get_catalog()
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*.json"):
        f.unlink()
    P = {}

    # --- single degree
    P["cs_2nd_year"] = prof("2025A7PS0147P", "B.E. CS, 2-1", ["A7"],
                            graded(named_until(cat, ["A7"], 2, 1)),
                            named_in(cat, ["A7"], 2, 1) + ["HSS F235"],
                            "artificial intelligence, machine learning", 8.4)
    P["mech_2nd_year"] = prof("2025A4PS0044P", "B.E. Mechanical, 2-1", ["A4"],
                              graded(named_until(cat, ["A4"], 2, 1)), named_in(cat, ["A4"], 2, 1),
                              "robotics, manufacturing, design", 7.2)
    ph = named_until(cat, ["A5"], 2, 1, pick={"BITS F113", "BITS F114", "PHY F102"})
    P["pharm_2nd_year"] = prof("2025A5PS0055P", "B.Pharm, 2-1 (General Maths sequence)", ["A5"],
                               graded(ph), named_in(cat, ["A5"], 2, 1, pick={"BITS F218"}),
                               "drug discovery, computational biology", 8.8)
    chem = graded(named_until(cat, ["A1"], 3, 1) + ["GS F232", "HSS F222"], {"CHE F213": "NC"})
    P["chem_3rd_year_nc"] = prof("2024A1PS0031P", "B.E. Chemical, 3-1, NC in Thermodynamics", ["A1"],
                                 chem, named_in(cat, ["A1"], 3, 1),
                                 "energy, process simulation, sustainability", 6.9)
    P["ece_3rd_year"] = prof("2024AAPS0033P", "B.E. ECE, 3-1", ["AA"],
                             graded(named_until(cat, ["AA"], 3, 1) + ["GS F211", "HSS F235"]),
                             named_in(cat, ["AA"], 3, 1), "signal processing, embedded systems, IoT", 8.1)
    P["eee_3rd_year_ds_minor"] = prof("2024A3PS0088P", "B.E. EEE, 3-1, Data Science minor", ["A3"],
                                      graded(named_until(cat, ["A3"], 3, 1) + ["GS F232", "HSS F235", "CS F320"]),
                                      named_in(cat, ["A3"], 3, 1), "data science, machine learning, power systems",
                                      8.6, minor="Minor in Data Science")
    P["civil_4th_year_2023"] = prof("2023A2PS0099P", "B.E. Civil, 4-1 (2023 batch)", ["A2"],
                                    graded(named_until(cat, ["A2"], 4, 1) + ["GS F232", "HSS F235", "HSS F222",
                                                                           "CE F423", "CE F434"]),
                                    [], "structures, GIS, sustainability", 7.0)
    # 2+2 CentraleSupelec: two years at BITS, then Paris
    P["csp_ece_2nd_year"] = prof("2025AACS0077P", "B.E. ECE, 2+2 CentraleSupelec, 2-1", ["AA"],
                                 graded(named_until(cat, ["AA"], 2, 1)), named_in(cat, ["AA"], 2, 1),
                                 "electronics, communication", 7.8, stream="CSP")
    # --- dual degree (composite charts, p.242-313)
    P["dual_b5a3_2nd_year"] = prof("2025B5A30011P", "M.Sc. Physics + B.E. EEE, 2-1", ["B5", "A3"],
                                   graded(named_until(cat, ["B5", "A3"], 2, 1)),
                                   named_in(cat, ["B5", "A3"], 2, 1), "quantum computing, electronics", 8.2)
    P["dual_b3a7_3rd_year"] = prof("2024B3A70123P", "M.Sc. Economics + B.E. CS, 3-1", ["B3", "A7"],
                                   graded(named_until(cat, ["B3", "A7"], 3, 1) + ["GS F232", "HSS F235"]),
                                   named_in(cat, ["B3", "A7"], 3, 1), "finance, econometrics, machine learning", 8.0)
    P["dual_b4a4_4th_year"] = prof("2023B4A40066P", "M.Sc. Mathematics + B.E. Mechanical, 4-1", ["B4", "A4"],
                                   graded(named_until(cat, ["B4", "A4"], 4, 1) +
                                          ["GS F232", "HSS F235", "HSS F222", "MATH F424"]),
                                   named_in(cat, ["B4", "A4"], 4, 1), "optimisation, CFD, numerical methods", 7.5)
    for name, p in P.items():
        (OUT / f"{name}.json").write_text(json.dumps(p, indent=1))
    print("wrote", len(P), "profiles to", OUT)


if __name__ == "__main__":
    main()
