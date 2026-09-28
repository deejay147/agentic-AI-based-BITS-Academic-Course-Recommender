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


def named_until(cat, pid, year, sem, pick=None):
    """All named courses of `pid` in semesters strictly before (year, sem). For an OR pair
    only the first option (or the one in `pick`) is taken."""
    pick = pick or {}
    pos = cat.programmes[pid]["chart_positions"]
    out = []
    alts = {c: g for g in cat.gir_alternatives for c in g}
    skip = set()
    for code, (y, s) in pos.items():
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


def named_in(cat, pid, year, sem):
    pos = cat.programmes[pid]["chart_positions"]
    alts = {c: g for g in cat.gir_alternatives for c in g}
    out, skip = [], set()
    for code, (y, s) in pos.items():
        if (y, s) == (year, sem) and code not in skip:
            if code in alts:
                skip.update(alts[code])
            out.append(code)
    return out


def graded(codes, overrides=None):
    overrides = overrides or {}
    return [{"code": c, "grade": overrides.get(c, random.choice(GRADES))} for c in codes]


def main():
    cat = get_catalog()
    OUT.mkdir(parents=True, exist_ok=True)
    profiles = {}

    # 1. CS, 2nd year (batch 2025 = exactly the bulletin's curriculum)
    profiles["cs_2nd_year"] = {
        "id_no": "2025A7PS0147P", "name": "CS 2-1", "batch": 2025, "programmes": ["A7"],
        "completed": graded(named_until(cat, "A7", 2, 1)),
        "current": named_in(cat, "A7", 2, 1) + ["HSS F235"],
        "minor": None, "interests": "artificial intelligence, machine learning", "cgpa": 8.4,
    }
    # 2. CS, 3rd year: years 1-2 done + two humanities electives
    y12 = named_until(cat, "A7", 3, 1)
    profiles["cs_3rd_year"] = {
        "id_no": "2024A7PS0021P", "name": "CS 3-1", "batch": 2024, "programmes": ["A7"],
        "completed": graded(y12 + ["GS F232", "HSS F222"]),
        "current": named_in(cat, "A7", 3, 1),
        "minor": None, "interests": "AI, data science, systems", "cgpa": 7.9,
    }
    # 3. same as 2 but NC in Data Structures -> CS F211 must come back as a remaining CDC
    profiles["cs_3rd_year_nc"] = json.loads(json.dumps(profiles["cs_3rd_year"]))
    profiles["cs_3rd_year_nc"].update({"id_no": "2024A7PS0022P", "name": "CS 3-1 with an NC"})
    for c in profiles["cs_3rd_year_nc"]["completed"]:
        if c["code"] == "CS F211":
            c["grade"] = "NC"
    # 4. ECE, 3rd year
    profiles["ece_3rd_year"] = {
        "id_no": "2024AAPS0033P", "name": "ECE 3-1", "batch": 2024, "programmes": ["AA"],
        "completed": graded(named_until(cat, "AA", 3, 1) + ["GS F211", "HSS F235"]),
        "current": named_in(cat, "AA", 3, 1),
        "minor": None, "interests": "signal processing, embedded systems, IoT", "cgpa": 8.1,
    }
    # 5. Mechanical, 2nd year
    profiles["mech_2nd_year"] = {
        "id_no": "2025A4PS0044P", "name": "Mech 2-1", "batch": 2025, "programmes": ["A4"],
        "completed": graded(named_until(cat, "A4", 2, 1)),
        "current": named_in(cat, "A4", 2, 1),
        "minor": None, "interests": "robotics, manufacturing, design", "cgpa": 7.2,
    }
    # 6. B.Pharm, 2nd year, took the General Mathematics sequence + Intro to Oscillations & Waves
    ph = named_until(cat, "A5", 2, 1, pick={"BITS F113", "BITS F114", "PHY F102"})
    profiles["pharm_2nd_year"] = {
        "id_no": "2025A5PS0055P", "name": "B.Pharm 2-1", "batch": 2025, "programmes": ["A5"],
        "completed": graded(ph),
        "current": [c for c in named_in(cat, "A5", 2, 1) if c != "MATH F211"],
        "minor": None, "interests": "drug discovery, computational biology", "cgpa": 8.8,
    }
    # 7. dual degree M.Sc. Economics + B.E. CS, 4th year
    dual = named_until(cat, "B3", 4, 1) + [c for c in named_until(cat, "A7", 3, 1) if c not in named_until(cat, "B3", 4, 1)]
    profiles["dual_eco_cs"] = {
        "id_no": "2023B3A70066P", "name": "M.Sc. Eco + B.E. CS, 4-1", "batch": 2023, "programmes": ["B3", "A7"],
        "completed": graded(dual + ["ECON F354", "GS F232", "HSS F235", "GS F211"]),
        "current": ["CS F351", "CS F372", "CS F301", "CS F342"],
        "minor": None, "interests": "finance, econometrics, machine learning", "cgpa": 8.0,
    }
    # 8. CS 3rd year pursuing the Data Science minor, one core done already
    profiles["cs_minor_ds"] = {
        "id_no": "2024A7PS0088P", "name": "CS 3-1 + DS minor", "batch": 2024, "programmes": ["A7"],
        "completed": graded(y12 + ["GS F232", "HSS F235", "CS F320"]),
        "current": named_in(cat, "A7", 3, 1),
        "minor": "Minor in Data Science", "interests": "data science, deep learning", "cgpa": 8.6,
    }
    # 9. older batch, 4th year CS: should get the "2025-26 curriculum" notice
    profiles["cs_4th_year_old_batch"] = {
        "id_no": "2023A7PS0099P", "name": "CS 4-1 (2023 batch)", "batch": 2023, "programmes": ["A7"],
        "completed": graded(named_until(cat, "A7", 4, 1) + ["GS F232", "HSS F235", "HSS F222",
                                                            "CS F407", "BITS F464"]),
        "current": [],
        "minor": None, "interests": "compilers, security", "cgpa": 7.0,
    }
    for name, p in profiles.items():
        (OUT / f"{name}.json").write_text(json.dumps(p, indent=1))
    print("wrote", len(profiles), "profiles to", OUT)


if __name__ == "__main__":
    main()
