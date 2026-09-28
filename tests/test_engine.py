"""Engine tests on the profiles in tests/profiles/. Run: pytest -q"""
import json
from pathlib import Path

import pytest

from engine import eligibility as el
from engine import requirements as req
from engine.catalog import get_catalog
from engine.profile import Profile, parse_id

PROFILES = Path(__file__).parent / "profiles"


@pytest.fixture(scope="module")
def cat():
    return get_catalog()


def load(name):
    return Profile.from_dict(json.loads((PROFILES / f"{name}.json").read_text()))


def run(name, cat):
    p = load(name)
    return p, el.evaluate(p, cat)


def elig_codes(r):
    return {x["code"]: x for x in r["eligible"]}


def test_id_parsing(cat):
    single = parse_id("2025A7PS0147P", set(cat.programmes))
    assert (single["batch"], single["programmes"], single["stream"]) == (2025, ["A7"], "PS")
    assert parse_id("2025AACS0077P", set(cat.programmes))["stream"] == "CSP"
    dual = parse_id("2024B3A70123P", set(cat.programmes))
    assert dual["programmes"] == ["B3", "A7"]
    with pytest.raises(ValueError):
        parse_id("hello", set(cat.programmes))


def test_cs_programme_structure(cat):
    p = cat.programmes["A7"]
    assert (p["cdc_units"], p["cdc_courses"]) == (48, 14)
    assert len(p["cdc_groups"]) == 14
    assert "CS F407" in p["del_codes"] and "BITS F464" in p["del_codes"]


def test_cs_2nd_year_requirements(cat):
    p, r = run("cs_2nd_year", cat)
    st = r["state"]
    cdc = st["programmes"][0]["cdc"]
    assert set(cdc["in_progress"]) == {"CS F213", "CS F214", "CS F215", "CS F222"}
    assert len(cdc["remaining"]) == 10
    assert st["huel"]["in_progress"] == ["HSS F235"]
    # single degree: 3 HUEL, 4 DEL, 5 OPEL (counted in courses)
    assert (st["huel"]["required_courses"], st["programmes"][0]["del"]["required_courses"],
            st["opel"]["required_courses"]) == (3, 4, 5)
    assert p.curriculum_note is None      # 2025 batch = bulletin curriculum


def test_programme_specific_del_count(cat):
    # Economics chart says 6 DELs, that overrides the default 4
    _, r = run("dual_b3a7_3rd_year", cat)
    dels = {pr["id"]: pr["del"]["required_courses"] for pr in r["state"]["programmes"]}
    assert dels == {"B3": 6, "A7": 4}


def test_registered_and_cleared_courses_not_suggested(cat):
    p, r = run("cs_2nd_year", cat)
    codes = {x["code"] for x in r["eligible"] + r["ineligible"]}
    assert "CS F213" not in codes and "MATH F101" not in codes


def test_other_discipline_blocked_for_2nd_year(cat):
    # reg 3.15(b)(i): an ME CDC as OPEL needs own year 1-2 named courses done
    _, r = run("cs_2nd_year", cat)
    me = [x for x in r["ineligible"] if x["code"] == "ME F212"]
    assert me and any(c["rule"] == "other_discipline_prior_prep" for c in me[0]["checks"])


def test_other_discipline_allowed_for_3rd_year(cat):
    _, r = run("ece_3rd_year", cat)
    assert any(x["code"].startswith("ME F") for x in r["eligible"])


def test_nc_grade_is_not_cleared(cat):
    _, r = run("chem_3rd_year_nc", cat)
    assert "CHE F213" in r["state"]["not_cleared"]
    rem = [o for g in r["state"]["programmes"][0]["cdc"]["remaining"] for o in g]
    assert "CHE F213" in rem
    # it's offered this semester, so it comes back as an eligible backlog CDC
    assert elig_codes(r)["CHE F213"]["category"] == "CDC"


def test_older_batch_notice(cat):
    _, r = run("civil_4th_year_2023", cat)
    assert any("2025-26" in n for n in r["state"]["notes"])


def test_dual_degree_no_opel(cat):
    _, r = run("dual_b4a4_4th_year", cat)
    st = r["state"]
    assert st["opel"]["required_courses"] is None and "Dual degree" in st["opel"]["note"]
    assert [pr["id"] for pr in st["programmes"]] == ["B4", "A4"]
    assert not any("Open electives" in i["requirement"] for i in st["graduation"]["items"])


def test_dual_uses_composite_chart(cat):
    # B.E. EEE core of a 2nd-year Physics+EEE dual student sits in year 3 of the composite chart,
    # so it needs prior preparation - none of it should be eligible yet
    _, r = run("dual_b5a3_2nd_year", cat)
    assert not [x for x in r["eligible"] if x["category"] == "CDC"]


def test_unit_cap(cat):
    # composite 3-1 for Eco+CS is 23 units, so nothing else fits under 25
    _, r = run("dual_b3a7_3rd_year", cat)
    assert r["registered_units"] == 23 and not r["eligible"]


def test_csp_note(cat):
    p, r = run("csp_ece_2nd_year", cat)
    assert p.stream == "CSP" and any("CentraleSup" in n for n in r["state"]["notes"])


def test_pharm_general_maths_sequence(cat):
    _, r = run("pharm_2nd_year", cat)
    st = r["state"]
    assert not st["gir"].get("remaining")
    assert "BITS F218" in st["gir"]["in_progress"]


def test_minor_progress(cat):
    _, r = run("eee_3rd_year_ds_minor", cat)
    m = r["state"]["minor"]
    assert m["core_done"] == ["CS F320"]
    assert ["BITS F464"] in m["core_remaining"]


def test_graduation_checklist(cat):
    _, r = run("civil_4th_year_2023", cat)
    g = r["state"]["graduation"]
    assert not g["ready"]
    names = [i["requirement"] for i in g["items"]]
    assert any(n.startswith("Open electives") for n in names)


def test_planner_files_electives(cat):
    from engine import planner
    p = load("cs_2nd_year")
    out = planner.plan(p, cat, ["CS F317", "GS F232", "ME F212"])
    filed = {x["code"]: x["filed_as"] for x in out["picks"]}
    assert filed == {"CS F317": "DEL (A7)", "GS F232": "HUEL"}
    assert out["rejected"][0]["code"] == "ME F212"
    assert out["clash_free"]


def test_every_eligible_course_passes_all_checks(cat):
    for f in PROFILES.glob("*.json"):
        _, r = run(f.stem, cat)
        for x in r["eligible"]:
            assert all(c["ok"] for c in x["checks"]), (f.stem, x["code"])
        assert not any(" U" in x["code"] for x in r["eligible"])


def test_timetable_clash_detected(cat):
    # ECON F211's midsem+compre collide with CS F213 (both 10/10 AN1, 16/12 FN)
    _, r = run("cs_2nd_year", cat)
    econ = [x for x in r["ineligible"] if x["code"] == "ECON F211"][0]
    assert any(c["rule"] == "timetable" and not c["ok"] for c in econ["checks"])


def test_compact_timetable_never_worse(cat):
    # bonus: compact mode picks the section combination with the fewest idle hours
    from engine import planner
    from engine.schedule import busy_from_registered, gap_hours
    p = load("ece_3rd_year")
    picks = ["EEE F411", "BITS F364"]

    def gaps(out):
        slots = set(busy_from_registered(cat, p.current, p.batch)["slots"])
        for x in out["picks"]:
            off = cat.offerings[x["code"]][0]
            for s in off["sections"]:
                if s["section"] in x["sections"].values():
                    slots |= {(sl["day"], sl["hour"]) for sl in s["slots"]}
        return gap_hours(slots)

    plain = planner.plan(p, cat, picks)
    compact = planner.plan(p, cat, picks, compact=True)
    assert compact["clash_free"] and gaps(compact) <= gaps(plain)
    assert compact["gap_hours"] == gaps(compact)
