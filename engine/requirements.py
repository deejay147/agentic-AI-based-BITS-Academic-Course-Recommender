"""Remaining requirements for a student: GIR, CDC, DEL, HUEL, OPEL (+ minor), and a
graduation checklist.

Pure python over the catalog, no LLM anywhere in here.

Elective requirements are counted in courses:
  single degree: 3 HUEL, 4 DEL, 5 OPEL
  dual degree  : 3 HUEL, DELs of each degree, no OPEL (DELs of one degree count as OPELs of
                 the other, reg 2.05)
If a programme's semester chart gives a different DEL count (Economics 6, Biotech 5, ...),
the chart wins.

Filing (reg 2.05): a cleared course that isn't a named course (GIR/CDC) goes to DEL if it's
in the programme's DEL pool and DEL isn't full, else HUEL if it's in the humanities pool (and
not from the student's own discipline, bulletin IV-127) and HUEL isn't full, else OPEL.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.catalog import PROJECT_RE, Catalog
from engine.profile import Profile
from ingest.common import norm_code

HUEL_COURSES = 3          # humanities electives (bulletin IV-1 also says 8 units min)
HUEL_UNITS = 8
DEL_COURSES_DEFAULT = 4
OPEL_COURSES_SINGLE = 5
COURSEWORK_MIN_UNITS = 129    # bulletin IV-1
COURSEWORK_MIN_COURSES = 41


@dataclass
class Bucket:
    name: str
    required_units: int | None = None
    required_courses: int | None = None
    done: list[str] = field(default_factory=list)          # cleared
    in_progress: list[str] = field(default_factory=list)   # registered this semester
    remaining: list = field(default_factory=list)          # for named categories: groups still open
    note: str | None = None
    source: str | None = None

    def units_done(self, cat: Catalog, include_current=False) -> int:
        codes = self.done + (self.in_progress if include_current else [])
        return sum(cat.units(c) or 0 for c in codes)

    def is_full(self, cat: Catalog, include_current=True) -> bool:
        # electives are counted in courses; units are only shown for info
        if self.required_courses is None:
            return False
        n = len(self.done) + (len(self.in_progress) if include_current else 0)
        return n >= self.required_courses

    def summary(self, cat: Catalog) -> dict:
        u_done = self.units_done(cat)
        u_prog = self.units_done(cat, True) - u_done
        out = {"category": self.name, "done": self.done, "in_progress": self.in_progress,
               "units_done": u_done, "units_in_progress": u_prog,
               "required_units": self.required_units, "required_courses": self.required_courses,
               "note": self.note, "source": self.source}
        if self.required_units is not None:
            out["units_remaining"] = max(0, self.required_units - u_done - u_prog)
        if self.required_courses is not None:
            out["courses_remaining"] = max(0, self.required_courses - len(self.done) - len(self.in_progress))
        if self.remaining:
            out["remaining"] = self.remaining
        return out


def _normalise_profile(p: Profile):
    for c in p.completed:
        c.code = norm_code(c.code) or c.code.upper().strip()
    p.current = [norm_code(c) or c.upper().strip() for c in p.current]


def _gir_groups(cat: Catalog, pids: list[str]) -> list[list[str]]:
    """GIR named courses of the programme(s), with 'either/or' pairs grouped (ECON F211 / MGTS F211 ...)."""
    codes = []
    for pid in pids:
        for c in cat.programmes[pid]["gir_codes"]:
            if c not in codes:
                codes.append(c)
    groups, used = [], set()
    for alt in cat.gir_alternatives:
        present = [c for c in alt if c in codes]
        if present:
            groups.append(present)
            used.update(present)
    groups += [[c] for c in codes if c not in used]
    return groups


def compute(profile: Profile, cat: Catalog) -> dict:
    _normalise_profile(profile)
    pids = [p for p in profile.programmes if p in cat.programmes]
    unknown = [p for p in profile.programmes if p not in cat.programmes]

    cleared = {cat.canon(c.code) for c in profile.completed if c.cleared}
    not_cleared = [c.code for c in profile.completed if not c.cleared]
    registered = {cat.canon(c) for c in profile.current}

    def status(options):
        if any(cat.canon(o) in cleared for o in options):
            return "done"
        if any(cat.canon(o) in registered for o in options):
            return "in_progress"
        return "remaining"

    named = set()     # canonical codes of every GIR/CDC slot the student has used up
    result = {"programmes": [], "notes": []}
    if profile.curriculum_note:
        result["notes"].append(profile.curriculum_note)
    if unknown:
        result["notes"].append(f"No programme structure in the supplied bulletin for: {', '.join(unknown)}.")

    # ---- GIR (shared by both degrees of a dual degree, bulletin IV-2)
    gir = Bucket("GIR", source="Bulletin IV-1/IV-2 (p.209-210) + semester chart")
    for g in _gir_groups(cat, pids):
        st = status(g)
        hit = next((o for o in g if cat.canon(o) in cleared | registered), None)
        if hit:
            named.add(cat.canon(hit))
        if st == "done":
            gir.done.append(hit)
        elif st == "in_progress":
            gir.in_progress.append(hit)
        else:
            gir.remaining.append(g)

    # ---- per programme CDC
    del_buckets = {}
    for pid in pids:
        p = cat.programmes[pid]
        cdc = Bucket(f"CDC ({pid})", required_units=p["cdc_units"], required_courses=p["cdc_courses"],
                     source=f"Bulletin CDC list + semester chart p.{p['chart_page']}")
        if p["verification"]:
            cdc.note = "Bulletin list and semester chart disagree for this programme; see verification queue."
        for g in p["cdc_groups"]:
            opts = [m["code"] for m in g]
            st = status(opts)
            hit = next((o for o in opts if cat.canon(o) in cleared | registered), None)
            if hit:
                named.add(cat.canon(hit))
            if st == "done":
                cdc.done.append(hit)
            elif st == "in_progress":
                cdc.in_progress.append(hit)
            else:
                cdc.remaining.append(opts)
        n_del = p["del_courses"] or DEL_COURSES_DEFAULT
        dl = Bucket(f"DEL ({pid})", required_courses=n_del,
                    source=f"Bulletin semester chart p.{p['chart_page']}" if p["del_courses"]
                    else "default 4 DELs (no count on the chart)")
        if p["compulsory_del"]:
            dl.note = "Compulsory DELs: " + ", ".join(p["compulsory_del"])
        del_buckets[pid] = dl
        result["programmes"].append({"id": pid, "name": p["name"], "cdc": cdc})

    # ---- electives: everything cleared/registered that isn't a named course
    own_depts = set().union(*[cat.programme_depts(pid) for pid in pids]) if pids else set()
    huel = Bucket("HUEL", required_courses=HUEL_COURSES,
                  source="Bulletin IV-1 (p.209), pool IV-125..127 (p.333-335)")
    opel = Bucket("OPEL", source="Bulletin IV-1 (p.209), reg 2.05")
    if len(pids) == 2:
        opel.note = "Dual degree: no separate OPEL requirement - DELs of one degree count as OPELs of the other (reg 2.05)."
    elif pids:
        opel.required_courses = OPEL_COURSES_SINGLE

    projects = []
    for which, pool in (("done", cleared), ("in_progress", registered)):
        for code in sorted(pool - named):
            if PROJECT_RE.search(code):
                projects.append(code)
            placed = False
            for pid in pids:
                d = del_buckets[pid]
                in_pool = any(cat.same(code, c) for c in cat.programmes[pid]["del_codes"])
                # own-discipline project courses (e.g. CS F266 for CS) count towards DEL too (IV-125)
                own_project = PROJECT_RE.search(code) and code.split()[0] in cat.programme_depts(pid)
                if (in_pool or own_project) and not d.is_full(cat):
                    getattr(d, which).append(code)
                    placed = True
                    break
            if placed:
                continue
            if code in cat.huel and code.split()[0] not in own_depts and not huel.is_full(cat):
                getattr(huel, which).append(code)
                continue
            getattr(opel, which).append(code)

    result["gir"] = gir.summary(cat)
    for prog in result["programmes"]:
        prog["cdc"] = prog["cdc"].summary(cat)
        prog["del"] = del_buckets[prog["id"]].summary(cat)
    result["huel"] = huel.summary(cat)
    result["opel"] = opel.summary(cat)
    result["project_courses_used"] = projects
    result["not_cleared"] = not_cleared
    result["cleared"] = sorted(cleared)
    result["registered"] = sorted(registered)
    result["own_depts"] = sorted(own_depts)
    if profile.minor:
        result["minor"] = minor_progress(profile, cat, cleared, registered, named)
    result["graduation"] = graduation_check(profile, cat, result, cleared)
    if profile.stream == "CSP":
        result["notes"].append(
            "BITS-CentraleSupélec 2+2: years 3-4 are at CentraleSupélec Paris. To progress you need a CGPA of at "
            "least 5.0 after the first two years and no grade below D in BITS courses that count toward the CSP "
            "degree (bulletin p.160). Which BITS courses count for CSP isn't listed in the supplied data.")
    # internal handles the eligibility step needs
    result["_buckets"] = {"del": del_buckets, "huel": huel, "opel": opel, "gir": gir}
    return result


def graduation_check(profile: Profile, cat: Catalog, st: dict, cleared: set) -> dict:
    """Checklist against the first degree graduation requirements (bulletin IV-1, IV-2).
    Only coursework - PS-II / thesis is listed as a reminder, it isn't something we track."""
    items = []

    def add(name, ok, detail):
        items.append({"requirement": name, "met": ok, "detail": detail})

    gir_left = st["gir"].get("remaining", [])
    add("General institutional requirement (named courses)", not gir_left,
        f"{len(gir_left)} left" if gir_left else "all done")
    for prog in st["programmes"]:
        c, d = prog["cdc"], prog["del"]
        add(f"Core courses - {prog['id']}", not c.get("remaining"),
            f"{len(c.get('remaining', []))} left" if c.get("remaining") else "all done")
        add(f"Discipline electives - {prog['id']} ({d['required_courses']} courses)",
            d.get("courses_remaining", 0) == 0, f"{len(d['done'])} done, {d.get('courses_remaining', 0)} left")
    h = st["huel"]
    add(f"Humanities electives ({HUEL_COURSES} courses, {HUEL_UNITS}+ units)",
        h.get("courses_remaining", 0) == 0 and h["units_done"] >= HUEL_UNITS,
        f"{len(h['done'])} courses / {h['units_done']} units done")
    o = st["opel"]
    if o["required_courses"]:
        add(f"Open electives ({o['required_courses']} courses)", o.get("courses_remaining", 0) == 0,
            f"{len(o['done'])} done, {o.get('courses_remaining', 0)} left")
    units = sum(cat.units(c) or 0 for c in cleared)
    add(f"Coursework total ({COURSEWORK_MIN_UNITS}+ units, {COURSEWORK_MIN_COURSES}+ courses)",
        units >= COURSEWORK_MIN_UNITS and len(cleared) >= COURSEWORK_MIN_COURSES,
        f"{units} units, {len(cleared)} courses cleared")
    n_ps = len(st["programmes"])
    items.append({"requirement": f"PS-II or Thesis ({'one per degree' if n_ps == 2 else 'one'})", "met": None,
                  "detail": "not tracked here (bulletin IV-1/IV-2)"})
    done = all(i["met"] for i in items if i["met"] is not None)
    return {"ready": done, "items": items, "source": "Bulletin IV-1, IV-2 (p.209-210)"}


def minor_progress(profile: Profile, cat: Catalog, cleared: set, registered: set, named: set) -> dict:
    m = cat.minors_by_name.get(profile.minor)
    if not m:
        return {"name": profile.minor, "error": "minor not found in the supplied bulletin"}
    rules = cat.minor_rules
    core_left, core_done, elec_done = [], [], []
    for g in m["core"].values():
        opts = [r["code"] for r in g]
        hit = next((o for o in opts if cat.canon(o) in cleared | registered), None)
        (core_done.append(hit) if hit else core_left.append(opts))
    elec_codes = {r["code"] for g in m["electives"].values() for r in g}
    for c in sorted(cleared | registered):
        if any(cat.same(c, e) for e in elec_codes):
            elec_done.append(c)
    counted = core_done + elec_done
    overlap = [c for c in counted if cat.canon(c) in named]
    units = sum(cat.units(c) or 0 for c in counted)
    grades = {cat.canon(c.code): c.grade for c in profile.completed if c.grade}
    return {
        "name": m["name"],
        "required_courses": m["req_courses"], "required_units": m["req_units"],
        "core_done": core_done, "core_remaining": core_left, "electives_done": elec_done,
        "courses_counted": len(counted), "units_counted": units,
        "overlap_with_mandatory": overlap,
        "overlap_ok": len(overlap) <= 2 and sum(cat.units(c) or 0 for c in overlap) <= 6,
        "grades_available": bool(grades),
        "rules": rules,
        "source": f"Bulletin minors p.{m['source']['pages']}",
    }
