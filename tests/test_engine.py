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
    assert single == {"batch": 2025, "programmes": ["A7"], "campus": "Pilani", "raw": "2025A7PS0147P"}
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
    assert st["opel"]["required_units"] == 16
    assert p.curriculum_note is None      # 2025 batch = bulletin curriculum


def test_registered_and_cleared_courses_not_suggested(cat):
    p, r = run("cs_2nd_year", cat)
    codes = {x["code"] for x in r["eligible"] + r["ineligible"]}
    assert "CS F213" not in codes and "MATH F101" not in codes


def test_other_discipline_blocked_for_2nd_year(cat):
    # reg 3.15(b)(i): ME CDC as OPEL needs own year 1-2 named courses done
    _, r = run("cs_2nd_year", cat)
    me = [x for x in r["ineligible"] if x["code"] == "ME F212"]
    assert me and any(c["rule"] == "other_discipline_prior_prep" for c in me[0]["checks"])


def test_other_discipline_allowed_for_3rd_year(cat):
    _, r = run("cs_3rd_year", cat)
    assert any(x["code"].startswith("ME F") for x in r["eligible"])


def test_nc_grade_is_not_cleared(cat):
    _, r = run("cs_3rd_year_nc", cat)
    rem = [o for g in r["state"]["programmes"][0]["cdc"]["remaining"] for o in g]
    assert "CS F211" in rem
    assert "CS F211" in r["state"]["not_cleared"]


def test_del_goes_to_opel_once_full(cat):
    # 4th year with 2 DELs done still needs 6 DEL units -> CS DELs are DEL, not OPEL
    _, r = run("cs_4th_year_old_batch", cat)
    e = elig_codes(r)
    dels = [x for x in e.values() if x["category"] == "DEL"]
    assert dels and all(x["code"] != "CS F407" for x in dels)
    assert r["state"]["notes"]   # older batch notice


def test_dual_degree_opel_note(cat):
    _, r = run("dual_eco_cs", cat)
    assert "Dual degree" in r["state"]["opel"]["note"]
    assert [pr["id"] for pr in r["state"]["programmes"]] == ["B3", "A7"]


def test_pharm_general_maths_sequence(cat):
    _, r = run("pharm_2nd_year", cat)
    st = r["state"]
    # BITS F113/F114 fill the MATH F101/F102 slots, BITS F218 (registered) fills MATH F211
    assert not st["gir"].get("remaining")
    assert "BITS F218" in st["gir"]["in_progress"]


def test_minor_progress(cat):
    _, r = run("cs_minor_ds", cat)
    m = r["state"]["minor"]
    assert m["core_done"] == ["CS F320"]
    assert ["BITS F464"] in m["core_remaining"]


def test_every_eligible_course_passes_all_checks(cat):
    for f in PROFILES.glob("*.json"):
        _, r = run(f.stem, cat)
        for x in r["eligible"]:
            assert all(c["ok"] for c in x["checks"]), (f.stem, x["code"])
        # no 2026-only / new-curriculum course for pre-2026 batches
        assert not any(" U" in x["code"] for x in r["eligible"])


def test_timetable_clash_detected(cat):
    # ECON F211's midsem+compre collide with CS F213 (both 10/10 AN1, 16/12 FN)
    _, r = run("cs_2nd_year", cat)
    econ = [x for x in r["ineligible"] if x["code"] == "ECON F211"][0]
    assert any(c["rule"] == "timetable" and not c["ok"] for c in econ["checks"])
