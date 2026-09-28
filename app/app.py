"""Streamlit dashboard.   run:  streamlit run app/app.py

Sidebar  : student profile (from BITS ID, pre-filled from the semester chart, editable, saved as json)
Tabs     : Requirements | Ask | Plan semester | Eligible courses | Data sources

Nothing here computes academic rules - it only calls engine/ and agent/.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from agent.agent import Recommender, PROP_LABELS                      # noqa: E402
from agent.retrieval import handout_summary                           # noqa: E402
from engine.catalog import get_catalog                                # noqa: E402
from engine.profile import Profile, parse_id, CURRICULUM_BATCH        # noqa: E402
from ingest.timetable import HOUR_TIMES                               # noqa: E402
from engine.chart import named_in, named_until                        # noqa: E402

PROFILE_DIR = ROOT / "data" / "profiles"
TEST_PROFILES = ROOT / "tests" / "profiles"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)
GRADES = ["", "A", "A-", "B", "B-", "C", "C-", "D", "E", "NC", "W", "I", "RC"]
DAYS = ["M", "T", "W", "Th", "F", "S"]

st.set_page_config(page_title="BITS Course Recommender", page_icon="🎓", layout="wide")
cat = get_catalog()
PROG_NAMES = {pid: f"{p['name']} ({pid})" for pid, p in sorted(cat.programmes.items())}


# --------------------------------------------------------------------------- profile state
def blank_profile():
    return {"id_no": "", "name": "", "batch": CURRICULUM_BATCH, "programmes": ["A7"], "stream": "PS",
            "completed": [], "current": [], "minor": None, "interests": "", "cgpa": None}


if "profile" not in st.session_state:
    st.session_state.profile = json.loads((TEST_PROFILES / "cs_2nd_year.json").read_text())
    st.session_state.chat = []


def set_profile(d):
    st.session_state.profile = d
    st.session_state.chat = []


def prefill(d):
    """completed = named courses of earlier semesters, current = this semester's, from the chart"""
    pids = d["programmes"]
    year = 2026 - int(d["batch"]) + 1
    if len(pids) == 2 and f"{pids[0]}+{pids[1]}" not in cat.dual_charts:
        st.sidebar.warning("No composite chart for this dual degree pair in the bulletin.")
        return d
    d["completed"] = [{"code": c, "grade": ""} for c in named_until(cat, pids, year, 1)]
    d["current"] = named_in(cat, pids, year, 1)
    return d


# --------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Student profile")
    tests = sorted(p.stem for p in TEST_PROFILES.glob("*.json"))
    saved = sorted(p.stem for p in PROFILE_DIR.glob("*.json"))
    c1, c2 = st.columns([2, 1])
    choice = c1.selectbox("Load", ["-"] + [f"test: {t}" for t in tests] + [f"saved: {s}" for s in saved],
                          label_visibility="collapsed")
    if c2.button("Load") and choice != "-":
        kind, name = choice.split(": ")
        folder = TEST_PROFILES if kind == "test" else PROFILE_DIR
        set_profile(json.loads((folder / f"{name}.json").read_text()))
        st.rerun()
    if st.button("New empty profile"):
        set_profile(blank_profile())
        st.rerun()

    d = st.session_state.profile
    id_no = st.text_input("BITS ID", d.get("id_no") or "", placeholder="2025A7PS0147P")
    if id_no and id_no != d.get("id_no"):
        try:
            info = parse_id(id_no, set(cat.programmes))
            d.update({"id_no": info["raw"], "batch": info["batch"], "programmes": info["programmes"],
                      "stream": info["stream"]})
            if info["campus"] != "Pilani":
                st.warning("Only Pilani campus data (timetable + handouts) was supplied.")
        except ValueError as e:
            st.error(str(e))
    d["name"] = st.text_input("Name (optional)", d.get("name") or "")
    d["batch"] = st.number_input("Admission year (batch)", 2018, 2026, int(d.get("batch") or CURRICULUM_BATCH))
    d["programmes"] = st.multiselect("Degree(s) - pick 2 for a dual degree", list(PROG_NAMES),
                                     default=[p for p in d.get("programmes", []) if p in PROG_NAMES],
                                     format_func=PROG_NAMES.get, max_selections=2)
    streams = ["PS", "TS", "CSP", "RMIT", "UB", "ISU", "RPI"]
    d["stream"] = st.selectbox("Stream", streams, index=streams.index(d.get("stream") or "PS")
                               if (d.get("stream") or "PS") in streams else 0,
                               help="CSP = BITS-CentraleSupelec 2+2")
    minors = ["(none)"] + sorted(cat.minors_by_name)
    d["minor"] = st.selectbox("Minor", minors, index=minors.index(d["minor"]) if d.get("minor") in minors else 0)
    d["minor"] = None if d["minor"] == "(none)" else d["minor"]
    cg = st.number_input("CGPA (optional)", 0.0, 10.0, float(d.get("cgpa") or 0.0), 0.01)
    d["cgpa"] = cg or None
    d["interests"] = st.text_area("Academic interests", d.get("interests") or "", height=68)

    if st.button("Pre-fill courses from my semester chart", width="stretch"):
        prefill(d)
        st.rerun()

    st.caption(f"Year {2026 - int(d['batch']) + 1}, planning **First Semester 2026-27**")
    st.subheader("Completed courses")
    comp_df = pd.DataFrame(d.get("completed") or [], columns=["code", "grade"]).fillna("")
    comp_df = st.data_editor(comp_df, num_rows="dynamic", width="stretch", hide_index=True,
                             column_config={"grade": st.column_config.SelectboxColumn(options=GRADES),
                                            "code": st.column_config.TextColumn(help="e.g. CS F213")},
                             key=f"comp_{d.get('id_no')}_{len(d.get('completed') or [])}")
    d["completed"] = [{"code": r["code"], "grade": r["grade"] or None}
                      for r in comp_df.to_dict("records") if r.get("code")]
    offered = sorted(cat.offerings)
    d["current"] = st.multiselect("Registered this semester", offered,
                                  default=[c for c in d.get("current", []) if c in offered])
    if st.button("Save profile", width="stretch"):
        name = (d.get("id_no") or d.get("name") or "profile").replace(" ", "_")
        (PROFILE_DIR / f"{name}.json").write_text(json.dumps(d, indent=1))
        st.success(f"Saved as data/profiles/{name}.json")

profile = Profile.from_dict(json.loads(json.dumps(st.session_state.profile)))
if not profile.programmes:
    st.info("Pick a degree (or type your BITS ID) in the sidebar to start.")
    st.stop()
rec = Recommender(profile)
sess = rec.session
req = sess.get_requirements()

# --------------------------------------------------------------------------- header
st.title("BITS Academic Course Recommender")
mode = "Claude (" + rec.model + ")" if rec.mode == "claude" else "rule-based (no API key)"
st.caption(f"{profile.id_no or 'no ID'} · {' + '.join(PROG_NAMES.get(p, p) for p in profile.programmes)} · "
           f"{profile.semester_label} · agent mode: {mode}")
for n in req["notes"]:
    st.info(n)

tab_req, tab_ask, tab_plan, tab_elig, tab_data = st.tabs(
    ["Requirements", "Ask", "Plan semester", "Eligible courses", "Data sources"])


def course_label(code):
    return f"{code} - {cat.title(code) or ''}"


# --------------------------------------------------------------------------- requirements
with tab_req:
    cols = st.columns(5)
    cols[0].metric("Units registered", f"{req['registered_units']} / 25")
    cdc_left = sum(len(p["cdc_remaining"]) for p in req["programmes"])
    cols[1].metric("Core courses left", cdc_left)
    cols[2].metric("DELs left", sum(p["del_remaining_courses"] or 0 for p in req["programmes"]))
    cols[3].metric("HUELs left", req["huel"]["remaining_courses"])
    cols[4].metric("OPELs left", req["opel"]["remaining_courses"] if req["opel"]["required_courses"] else "n/a")

    for p in req["programmes"]:
        st.subheader(p["programme"])
        a, b = st.columns(2)
        with a:
            st.markdown("**Core (CDC) remaining**")
            st.write(", ".join(p["cdc_remaining"]) or "none")
            if p["cdc_in_progress"]:
                st.caption("In progress: " + ", ".join(p["cdc_in_progress"]))
        with b:
            st.markdown(f"**Discipline electives** - {p['del_required_courses']} needed")
            st.write(f"done: {', '.join(p['del_done']) or '-'}  ·  in progress: {', '.join(p['del_in_progress']) or '-'}")
            if p["del_note"]:
                st.caption(p["del_note"])
    a, b, c = st.columns(3)
    with a:
        st.markdown("**General institutional (GIR) left**")
        st.write(", ".join(req["gir_remaining"]) or "none")
    with b:
        h = req["huel"]
        st.markdown(f"**Humanities (HUEL)** - {h['required_courses']} needed")
        st.write(f"done: {', '.join(h['done']) or '-'}  ·  in progress: {', '.join(h['in_progress']) or '-'}")
    with c:
        o = req["opel"]
        st.markdown("**Open electives (OPEL)**" + (f" - {o['required_courses']} needed" if o["required_courses"] else ""))
        st.write(o["note"] if not o["required_courses"] else
                 f"done: {', '.join(o['done']) or '-'}  ·  in progress: {', '.join(o['in_progress']) or '-'}")
    if req["minor"]:
        m = req["minor"]
        st.subheader(m["name"])
        if m.get("error"):
            st.warning(m["error"])
        else:
            st.write(f"{m['courses_counted']} of {m['required_courses']} courses, {m['units_counted']} of "
                     f"{m['required_units']} units. Core left: "
                     + (", ".join("/".join(g) for g in m["core_remaining"]) or "none"))
            if not m["overlap_ok"]:
                st.warning("More than 2 courses / 6 units overlap with your mandatory courses (bulletin IV-129).")
            st.caption(m["rules"]["gpa"] + " · " + m["source"])
    st.subheader("Graduation checklist")
    g = req["graduation"]
    st.dataframe(pd.DataFrame([{"requirement": i["requirement"],
                                "status": "✅" if i["met"] else ("—" if i["met"] is None else "❌"),
                                "detail": i["detail"]} for i in g["items"]]),
                 hide_index=True, width="stretch")
    st.caption(g["source"])
    if req["not_cleared"]:
        st.warning("Not cleared (NC / W / I ...): " + ", ".join(req["not_cleared"]))


# --------------------------------------------------------------------------- ask
def render_cards(recs):
    for r in recs:
        with st.expander(f"**{r['code']} - {r['title']}** · {r['units']} units · fills {r['fills']}", expanded=True):
            if r.get("agent_reason"):
                st.markdown(f"_{r['agent_reason']}_")
            st.markdown(f"**Requirement:** {r['fills']} - {r['why_category']}"
                        + (f" (can count as {'/'.join(r['can_count_as'])})" if len(r["can_count_as"]) > 1 else ""))
            st.markdown("**Eligibility:** " + "; ".join(r["eligibility"]))
            for name, pr in (r.get("properties") or {}).items():
                icon = {True: "✅", False: "❌", None: "❔"}[pr["value"]]
                st.markdown(f"{icon} **{PROP_LABELS.get(name, name)}** - {pr.get('evidence') or ''}")
            bits = [f"IC {r['ic']}" if r["ic"] else None, f"midsem {r['midsem']}" if r["midsem"] else "no midsem slot",
                    f"compre {r['compre']}" if r["compre"] else None,
                    "sections " + ", ".join(r["sections"].values()) if r["sections"] else None]
            st.caption(" · ".join(b for b in bits if b))
            if r.get("match_terms"):
                st.caption("matched: " + ", ".join(t.replace("_", " ") for t in r["match_terms"][:6]))
            src = r["sources"]
            st.caption(f"sources: timetable p.{(src['timetable'] or {}).get('page')}"
                       + (f" · bulletin p.{src['bulletin']['page']}" if src.get("bulletin") else "")
                       + (f" · {src['handout']}" if src.get("handout") else " · no handout"))


with tab_ask:
    st.caption("e.g. *Suggest DELs related to AI* · *I want an OPEL with no attendance requirement* · "
               "*Suggest courses with no midsem and a lenient makeup policy* · *I need a HUEL and prefer "
               "project-based evaluation* · *can I take CS F407 and BITS F464 together?* · *what's left for me?*")
    for turn in st.session_state.chat:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])
            if turn.get("cards"):
                render_cards(turn["cards"])
    q = st.chat_input("Ask about courses for this semester")
    if q:
        st.session_state.chat.append({"role": "user", "content": q})
        with st.chat_message("user"):
            st.markdown(q)
        with st.chat_message("assistant"):
            with st.spinner("Checking requirements and eligibility..."):
                hist = [{"role": t["role"], "content": t["content"]} for t in st.session_state.chat[:-1]]
                out = rec.ask(q, hist)
            text = out["text"]
            if out["mode"] == "rules" and out["recommendations"]:
                # the numbered course blocks are shown as cards below - keep the header and the footers
                # (could-not-verify / blocked matches / closest options), drop the duplicated middle
                m = re.search(r"\n\n\*\*1\. .*?(?=\n\n\*\*(Could not verify|Matches your topic|Closest options)|\Z)",
                              text, re.S)
                if m:
                    text = text[:m.start()] + text[m.end():]
            st.markdown(text)
            render_cards(out["recommendations"])
            if out["mode"] == "claude" and out.get("could_not_verify"):
                st.caption("Could not verify for: " + ", ".join(c["code"] for c in out["could_not_verify"]))
            if out.get("rejected_by_validation"):
                st.caption("Dropped by policy validation: " +
                           ", ".join(f"{r['code']} ({'; '.join(r['reasons'])})" for r in out["rejected_by_validation"]))
        st.session_state.chat.append({"role": "assistant", "content": text, "cards": out["recommendations"]})


# --------------------------------------------------------------------------- plan
def week_grid(entries):
    """entries: [(code, section, slots)] -> html table days x hours"""
    grid = {}
    for code, sec, slots in entries:
        for sl in slots:
            grid.setdefault((sl["day"], sl["hour"]), []).append(f"{code} {sec}")
    hours = sorted({h for _, h in grid} | set(range(1, 11)))
    rows = ["<tr><th></th>" + "".join(f"<th>{d}</th>" for d in DAYS) + "</tr>"]
    for h in hours:
        cells = []
        for d in DAYS:
            v = grid.get((d, h), [])
            style = "background:#fde2e2;" if len(v) > 1 else ("background:#e3f2fd;" if v else "")
            cells.append(f"<td style='{style}font-size:12px'>{'<br>'.join(v)}</td>")
        rows.append(f"<tr><td style='font-size:12px'>{HOUR_TIMES.get(h, f'slot {h}')}</td>{''.join(cells)}</tr>")
    return "<table style='width:100%;border-collapse:collapse' border=1>" + "".join(rows) + "</table>"


with tab_plan:
    st.markdown("Pick the electives (or backlog courses) you want this semester. They get filed into "
                "CDC / DEL / HUEL / OPEL automatically, and sections are chosen so nothing clashes.")
    opts = sorted(sess.eligible)
    picks = st.multiselect("Courses you want to add", opts, format_func=lambda c: f"{course_label(c)} "
                           f"[{sess.eligible[c]['category']}]")
    a, b, c = st.columns(3)
    no8 = a.checkbox("No 8 AM classes")
    compact = c.checkbox("Compact timetable (fewest gaps)")
    free = b.selectbox("Keep a day free", ["-"] + DAYS)
    if picks:
        out = sess.check_plan(picks, no8, None if free == "-" else free, compact)
        st.dataframe(pd.DataFrame([{"course": course_label(p["code"]), "units": p["units"], "filed as": p["filed_as"],
                                    "sections": ", ".join(f"{k}: {v}" for k, v in p["sections"].items())}
                                   for p in out["picks"]]), hide_index=True, width="stretch")
        for r in out["rejected"]:
            st.error(f"{r['code']}: {'; '.join(r['reasons'])}")
        (st.success if out["clash_free"] else st.error)(
            f"Total {out['total_units']} units · " + ("clash-free" if out["clash_free"] else "; ".join(out["clash_problems"])))
        for w in out["warnings"]:
            st.warning(w)
        if out.get("gap_hours") is not None:
            st.caption(f"Compact pick: {out['gap_hours']} idle hours between classes across {out['days_used']} days "
                       "(counted with your registered single-section courses).")
        # timetable view: registered single-section components + chosen sections
        entries = []
        for code in profile.current:
            for o in cat.offerings.get(code, [])[:1]:
                by = {}
                for s in o["sections"]:
                    by.setdefault(s["type"], []).append(s)
                for secs in by.values():
                    if len(secs) == 1:
                        entries.append((code, secs[0]["section"], secs[0]["slots"]))
        for p in out["picks"]:
            o = cat.offerings[p["code"]][0]
            for s in o["sections"]:
                if s["section"] in p["sections"].values():
                    entries.append((p["code"], s["section"], s["slots"]))
        st.markdown("**Week view** (registered courses with a single section + your picks)")
        st.markdown(week_grid(entries), unsafe_allow_html=True)


# --------------------------------------------------------------------------- eligible list
with tab_elig:
    st.markdown(f"**{len(sess.eligible)}** courses in the First Semester 2026-27 timetable are open to you. "
                "Every one passed requirements, prerequisites, prior preparation, unit cap and clash checks.")
    catf = st.multiselect("Category", ["CDC", "GIR", "DEL", "HUEL", "OPEL"])
    rows = [{"code": x["code"], "title": x["title"], "units": x["units"], "fills": x["category"],
             "IC": (x["ic"] or "").title(), "midsem": x["midsem"][0] or "-",
             "notes": "; ".join(c["note"] for c in x["checks"] if c["note"])}
            for x in sess.eligible.values() if not catf or x["category"] in catf]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=380)
    st.markdown("**Why can't I take ...?**")
    pick = st.selectbox("Course", ["-"] + sorted(sess.ineligible), format_func=lambda c: c if c == "-" else course_label(c))
    if pick != "-":
        for c in sess.ineligible[pick]["checks"]:
            if not c["ok"]:
                st.error(f"{c['note']} - {c['clause']}")
        hs = handout_summary(cat, pick)
        st.caption(hs.get("message") or f"handout: {hs['file']}")


# --------------------------------------------------------------------------- data
with tab_data:
    st.markdown("Everything above is computed live from `data/processed/academic.db`, built from the supplied "
                "Bulletin, Academic Regulations, timetable and handouts by `python -m ingest.run_all`.")
    rep = (ROOT / "data" / "processed" / "validation_report.md")
    if rep.exists():
        st.markdown(rep.read_text())
    q = ROOT / "data" / "processed" / "verification_queue.csv"
    if q.exists():
        st.markdown("**Verification queue** (never guessed at runtime)")
        st.dataframe(pd.read_csv(q), hide_index=True, width="stretch", height=300)
    st.markdown("**Regulation clauses used by the engine**")
    st.dataframe(pd.DataFrame([{"clause": r["clause"], "rule": r["text"]} for r in cat.rules.values()]),
                 hide_index=True, width="stretch")
