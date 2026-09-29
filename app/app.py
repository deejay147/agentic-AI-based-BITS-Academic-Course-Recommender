"""Streamlit dashboard.   run:  streamlit run app/app.py

Sidebar : the student profile in 4 steps (who you are -> courses done -> this semester -> extras),
          pre-filled from the semester chart, editable, saved as json
Tabs    : Overview | Ask | Plan semester | Eligible courses | Data sources

Nothing here computes academic rules - it only calls engine/ and agent/ and draws the results.
Colours: one per requirement category (CDC blue, GIR slate, DEL violet, HUEL amber, OPEL teal) and
green / red / amber for yes / no / could-not-verify, used the same way everywhere.
"""
from __future__ import annotations

import html
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
from ingest.timetable import COMPRE_SESSIONS, HOUR_TIMES, MIDSEM_SESSIONS  # noqa: E402
from engine.chart import due_by, named_in, named_until                # noqa: E402

PROFILE_DIR = ROOT / "data" / "profiles"
TEST_PROFILES = ROOT / "tests" / "profiles"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)
GRADES = ["", "A", "A-", "B", "B-", "C", "C-", "D", "E", "NC", "W", "I", "RC"]
DAYS = ["M", "T", "W", "Th", "F", "S"]
# hour slot -> start time; the timetable's evening slots 11 and 12 start at 18:00 and 19:00
HOUR_LABELS = {**HOUR_TIMES, 11: "18:00", 12: "19:00"}
DAY_NAMES = {"M": "Mon", "T": "Tue", "W": "Wed", "Th": "Thu", "F": "Fri", "S": "Sat"}

# (text colour, background) per requirement category - same everywhere in the app (dark "galaxy" theme)
CAT_COLORS = {"CDC": ("#9cc3ff", "rgba(59,130,246,.13)"), "GIR": ("#c3cadb", "rgba(148,163,184,.12)"),
              "DEL": ("#c4b8ff", "rgba(139,123,255,.15)"), "HUEL": ("#f5cf7a", "rgba(245,158,11,.12)"),
              "OPEL": ("#7fe0cf", "rgba(20,184,166,.12)")}
# solid versions for the table (the data grid doesn't blend transparent colours)
CAT_TABLE_BG = {"CDC": "#16244a", "GIR": "#232a3d", "DEL": "#251d4d", "HUEL": "#33290f", "OPEL": "#0f3431"}
CAT_NAMES = {"CDC": "Core (CDC)", "GIR": "General (GIR)", "DEL": "Discipline elective (DEL)",
             "HUEL": "Humanities elective (HUEL)", "OPEL": "Open elective (OPEL)"}
DONE = ("#9be8b7", "rgba(34,197,94,.10)")
NOW = ("#c4b8ff", "rgba(139,123,255,.13)")
LEFT = ("#f3adad", "rgba(239,68,68,.10)")
INFO = ("#c4b8ff", "rgba(139,123,255,.12)")
GREY = ("#b9bfd9", "rgba(148,163,184,.10)")
# one colour per course in the timetable + exam calendar: (solid, translucent fill)
_HUES = ["#3b82f6", "#a855f7", "#14b8a6", "#f59e0b", "#ec4899", "#0ea5e9", "#22c55e", "#f97316", "#84cc16",
         "#d946ef", "#06b6d4", "#f43f5e", "#eab308", "#6366f1"]


def _fill(hex_, a=.36):
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{a})"


COURSE_COLORS = [(h, _fill(h)) for h in _HUES]

QUICK_QUESTIONS = [
    ("AI-related DELs", "Suggest DELs related to AI."),
    ("OPEL, no attendance rule", "I want an OPEL with no attendance requirement."),
    ("No midsem, easy makeup", "Suggest courses with no midsem and a lenient makeup policy."),
    ("Project-based HUEL", "I need a HUEL and prefer project-based evaluation."),
    ("Finance courses", "finance"),
    ("What's left for me?", "What are my remaining requirements?"),
]

st.set_page_config(page_title="BITS Course Recommender", page_icon="🎓", layout="wide",
                   initial_sidebar_state="expanded")
cat = get_catalog()
PROG_NAMES = {pid: f"{p['name']} ({pid})" for pid, p in sorted(cat.programmes.items())}

# --------------------------------------------------------------------------- styles
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"], .stMarkdown, button, input, textarea {
  font-family: 'Inter', system-ui, sans-serif !important;}
:root {--bg:#0a0e1a; --surface:#0f1426; --line:#1c2340; --text:#e6e8f2; --muted:#8a92b2; --accent:#8b7bff; --cyan:#5ee0f0;}
[data-testid="stAppViewContainer"] {background: radial-gradient(900px 420px at 100% -8%, rgba(139,123,255,.10), transparent 60%), var(--bg);}
[data-testid="stHeader"] {background: transparent;}
[data-testid="stSidebar"] {background: #0b1020; border-right: 1px solid var(--line);}
.block-container {padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1280px;}
h4 {font-weight: 600 !important; letter-spacing: -.01em; margin-top: .6rem !important;}

/* top bar */
.topbar {display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; flex-wrap: wrap;
         padding: 6px 2px 14px 2px; border-bottom: 1px solid var(--line); margin-bottom: 14px;}
.brand {font-size: 1.45rem; font-weight: 700; letter-spacing: -.02em;
        background: linear-gradient(90deg, #c4b8ff, #8b7bff 45%, #5ee0f0); -webkit-background-clip: text; color: transparent;}
.brand-sub {color: var(--muted); font-size: .86rem; margin-top: 2px;}
.pill {display: inline-block; padding: 3px 10px; border-radius: 999px; border: 1px solid var(--line); color: #c9cde2;
       font-size: .76rem; font-weight: 500; margin-left: 6px; white-space: nowrap;}
.pill.live {border-color: rgba(94,224,240,.35); color: var(--cyan);}

/* metrics strip */
.metrics {display: grid; grid-template-columns: repeat(6, 1fr); border: 1px solid var(--line); border-radius: 12px;
          background: var(--surface); margin-bottom: 12px; overflow: hidden;}
.metrics > div {padding: 10px 14px; border-right: 1px solid var(--line);}
.metrics > div:last-child {border-right: none;}
.metrics .l {font-size: .68rem; text-transform: uppercase; letter-spacing: .07em; color: var(--muted); font-weight: 600;}
.metrics .v {font-size: 1.25rem; font-weight: 650; color: var(--text); margin-top: 2px;}
.metrics .v small {font-size: .78rem; color: var(--muted); font-weight: 500;}
.metrics .dot {display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; vertical-align: middle;}
@media (max-width: 900px) {.metrics {grid-template-columns: repeat(3, 1fr);} .metrics > div {border-bottom: 1px solid var(--line);}}

/* tags */
.badge {display: inline-block; padding: 1px 8px; border-radius: 6px; font-size: .72rem; font-weight: 600;
        margin: 0 4px 4px 0; white-space: nowrap; border: 1px solid rgba(255,255,255,.05);}
.badge.or {border: 1px dashed currentColor;}
.muted {color: var(--muted); font-size: .83rem;}
.rowlabel {color: var(--muted); font-size: .68rem; text-transform: uppercase; letter-spacing: .07em; font-weight: 600;
           margin: 10px 0 4px 0;}
.bar {height: 5px; background: #1a2140; border-radius: 99px; overflow: hidden; display: flex; margin: 8px 0 6px 0;}
.bar > div {height: 100%;}

/* course rows */
.crow-top {display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;}
.ccode {font-weight: 650; color: #b9acff; font-size: .95rem; letter-spacing: .01em;}
.ctitle2 {font-weight: 600; color: var(--text); font-size: .95rem;}
.crow-right {margin-left: auto; display: flex; gap: 4px; align-items: center;}
.crow-meta {color: var(--muted); font-size: .8rem; margin-top: 4px; line-height: 1.5;}
.crow-meta b {color: #c9cde2; font-weight: 600;}
.props {margin-top: 6px; display: flex; flex-wrap: wrap; gap: 6px;}
.prop2 {font-size: .76rem; padding: 2px 8px; border-radius: 6px; font-weight: 600;}
.prop2.yes {color: #86efac; background: rgba(34,197,94,.10);}
.prop2.no {color: #fca5a5; background: rgba(239,68,68,.10);}
.prop2.unk {color: #fcd34d; background: rgba(234,179,8,.10);}
.prop {padding: 6px 10px; border-radius: 8px; margin: 4px 0; font-size: .84rem; line-height: 1.45; color: var(--text);}
.prop.yes {background: rgba(34,197,94,.07); border-left: 3px solid #22c55e;}
.prop.no {background: rgba(239,68,68,.07); border-left: 3px solid #ef4444;}
.prop.unk {background: rgba(234,179,8,.07); border-left: 3px solid #eab308;}
.lockrow {display: flex; gap: 10px; align-items: baseline; padding: 7px 2px; border-bottom: 1px solid var(--line); flex-wrap: wrap;}
.lockrow:last-child {border-bottom: none;}
.lockrow .why {margin-left: auto; color: #f0a3a3; font-size: .76rem; font-weight: 500; text-align: right; max-width: 48%;}
.sect {display: flex; align-items: center; gap: 8px; font-size: .78rem; font-weight: 600; letter-spacing: .06em;
       text-transform: uppercase; color: var(--muted); margin: 14px 0 6px 0;}
.sect .n {background: #1a2140; color: #c9cde2; border-radius: 6px; padding: 0 7px; letter-spacing: 0;}
.sect.bad {color: #f0a3a3;} .sect.ok {color: #86efac;}

.banner {padding: 9px 13px; border-radius: 10px; font-weight: 500; margin: 8px 0; font-size: .88rem;}
.banner.ok {background: rgba(34,197,94,.08); color: #9be8b7; border: 1px solid rgba(34,197,94,.25);}
.banner.bad {background: rgba(239,68,68,.08); color: #f3adad; border: 1px solid rgba(239,68,68,.25);}
.banner.info {background: rgba(139,123,255,.08); color: #cbc4ff; border: 1px solid rgba(139,123,255,.25);}
.step {display: flex; align-items: center; gap: 8px; font-weight: 600; color: var(--text); font-size: .9rem; margin: 18px 0 4px 0;}
.step .num {width: 20px; height: 20px; border-radius: 6px; background: rgba(139,123,255,.18); color: #c4b8ff;
            display: inline-flex; align-items: center; justify-content: center; font-size: .72rem; font-weight: 700;}
.checkrow {padding: 7px 12px; border-radius: 8px; margin: 3px 0; font-size: .85rem; display: flex; gap: 10px; color: var(--text);
           border: 1px solid var(--line); background: var(--surface);}
.checkrow .d {color: var(--muted); margin-left: auto; font-size: .8rem;}
.checkrow .st {width: 8px; height: 8px; border-radius: 50%; margin-top: 6px; flex: none;}

/* timetable + exam calendar */
.stat {border-radius: 12px; padding: 10px 14px; background: var(--surface); border: 1px solid var(--line); min-height: 78px;}
.stat .v {font-size: 1.2rem; font-weight: 650; color: var(--text);}
.stat .l {font-size: .68rem; color: var(--muted); text-transform: uppercase; letter-spacing: .07em; font-weight: 600;}
.stat .s {font-size: .76rem; color: var(--muted);}
table.week {width: 100%; border-collapse: separate; border-spacing: 3px; font-size: .76rem; color: #eef0ff;}
table.week th {background: #121831; padding: 6px; border-radius: 6px; color: #b9bfd9; font-weight: 600;}
table.week td {padding: 5px 6px; border-radius: 6px; vertical-align: top; background: rgba(20,26,52,.6); height: 28px;}
table.week td.time {background: none; color: var(--muted); white-space: nowrap; font-size: .72rem;}
table.week td.clash {background: rgba(239,68,68,.5) !important; font-weight: 700;}
.calmonth {font-weight: 600; color: #c4b8ff; margin: 12px 0 6px 0; font-size: .92rem;}
table.excal {width: 100%; border-collapse: separate; border-spacing: 4px; table-layout: fixed;}
table.excal th {background: #121831; color: #b9bfd9; padding: 6px; border-radius: 6px; font-size: .74rem; font-weight: 600;}
table.excal td {background: rgba(20,26,52,.6); border-radius: 8px; vertical-align: top; height: 86px; padding: 5px;}
table.excal td.other {opacity: .3;}
table.excal td.busy {box-shadow: inset 0 0 0 1px rgba(139,123,255,.45);}
table.excal td.busy2 {box-shadow: inset 0 0 0 2px #eab308;}
table.excal .dnum {font-size: .74rem; color: var(--muted); font-weight: 600; margin-bottom: 3px;}
.exchip {border-radius: 5px; padding: 3px 5px; margin-bottom: 3px; font-size: .7rem; color: #eef0ff; line-height: 1.25;}
.exchip span {color: #d7dbff; font-size: .66rem;}
.legend span {display: inline-block; padding: 2px 8px; border-radius: 6px; margin: 0 5px 5px 0; font-size: .74rem; color: #eef0ff;}

div[data-testid="stVerticalBlockBorderWrapper"] {background: var(--surface); border-radius: 12px; border-color: var(--line) !important;}
.stButton button, .stDownloadButton button {border-radius: 9px; font-weight: 500; font-size: .85rem;}
.stTabs [data-baseweb="tab-list"] {gap: 6px; border-bottom: 1px solid var(--line);}
.stTabs [data-baseweb="tab"] {font-weight: 500; font-size: .9rem; padding: 8px 4px;}
[data-testid="stExpander"] details {border-color: var(--line) !important; border-radius: 10px;}
[data-testid="stExpander"] summary {padding-top: 5px !important; padding-bottom: 5px !important; font-size: .82rem;}
</style>
""", unsafe_allow_html=True)


def esc(s) -> str:
    return html.escape(str(s or ""))


def badge(text, fg=GREY[0], bg=GREY[1], cls=""):
    return f"<span class='badge {cls}' style='color:{fg};background:{bg}'>{esc(text)}</span>"


def slot_badge(options, colors=None):
    """one requirement slot; an OR slot ('ECON F211 or MGTS F211' - take either) gets a dashed outline"""
    fg, bg = colors or LEFT
    if len(options) > 1:
        return badge(" or ".join(options), fg, bg, "or")
    return badge(options[0], fg, bg)


def cat_badge(category: str, label: str | None = None):
    key = (category or "OPEL").split()[0]
    fg, bg = CAT_COLORS.get(key, GREY)
    return badge(label or category, fg, bg)


def stat_tile(label, value, sub="", color="#8b7bff"):
    return (f"<div class='stat' style='--c:{color}'><div class='l'>{esc(label)}</div>"
            f"<div class='v'>{esc(value)}</div><div class='s'>{esc(sub)}</div></div>")


def progress_bar(done, doing, total, color):
    """done = solid colour, doing (registered now) = same colour lighter"""
    if not total:
        return ""
    d, p = min(done, total) / total * 100, min(doing, max(0, total - done)) / total * 100
    return (f"<div class='bar'><div style='width:{d}%;background:{color}'></div>"
            f"<div style='width:{p}%;background:{color};opacity:.35'></div></div>")


def course_label(code):
    return f"{code} - {cat.title(code) or ''}"


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


def profile_title(path: Path) -> str:
    try:
        return json.loads(path.read_text()).get("name") or path.stem
    except Exception:
        return path.stem


# --------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("<div class='brand' style='font-size:1.15rem'>Course Recommender</div>"
                "<div class='brand-sub'>BITS Pilani · First Semester 2026-27</div>", unsafe_allow_html=True)

    tests = sorted(TEST_PROFILES.glob("*.json"))
    saved = sorted(PROFILE_DIR.glob("*.json"))
    options = {"-": None}
    options.update({f"Sample · {profile_title(p)}": p for p in tests})
    options.update({f"Saved · {p.stem}": p for p in saved})
    choice = st.selectbox("Start from a sample or saved student", list(options))
    c1, c2 = st.columns(2)
    if c1.button("Load", icon=":material/folder_open:", width="stretch", disabled=choice == "-"):
        set_profile(json.loads(options[choice].read_text()))
        st.rerun()
    if c2.button("New", icon=":material/add:", width="stretch", help="Start an empty profile"):
        set_profile(blank_profile())
        st.rerun()

    d = st.session_state.profile

    # ---- step 1
    st.markdown("<div class='step'><span class='num'>1</span>Who you are</div>", unsafe_allow_html=True)
    id_no = st.text_input("BITS ID", d.get("id_no") or "", placeholder="2025A7PS0147P",
                          help="Your batch, degree(s) and stream are read from it")
    if id_no and id_no != d.get("id_no"):
        try:
            info = parse_id(id_no, set(cat.programmes))
            d.update({"id_no": info["raw"], "batch": info["batch"], "programmes": info["programmes"],
                      "stream": info["stream"]})
            if info["campus"] != "Pilani":
                st.warning("Only Pilani campus data (timetable + handouts) was supplied.")
        except ValueError as e:
            st.error(str(e))
    if d.get("programmes"):
        yr = 2026 - int(d.get("batch") or CURRICULUM_BATCH) + 1
        st.markdown(" ".join([badge(f"Batch {d.get('batch')}", *INFO),
                              badge(f"Year {yr} · Sem 1", *INFO),
                              badge("Dual degree" if len(d["programmes"]) == 2 else "Single degree", *INFO),
                              badge(d.get("stream") or "PS", *INFO)]), unsafe_allow_html=True)
    with st.expander("Edit details", icon=":material/edit:"):
        d["name"] = st.text_input("Name (optional)", d.get("name") or "")
        d["batch"] = st.number_input("Admission year (batch)", 2018, 2026, int(d.get("batch") or CURRICULUM_BATCH))
        d["programmes"] = st.multiselect("Degree(s) - pick 2 for a dual degree", list(PROG_NAMES),
                                         default=[p for p in d.get("programmes", []) if p in PROG_NAMES],
                                         format_func=PROG_NAMES.get, max_selections=2)
        streams = ["PS", "TS", "CSP", "RMIT", "UB", "ISU", "RPI"]
        d["stream"] = st.selectbox("Stream", streams, index=streams.index(d.get("stream") or "PS")
                                   if (d.get("stream") or "PS") in streams else 0,
                                   help="CSP = BITS-CentraleSupelec 2+2")

    # ---- step 2
    st.markdown("<div class='step'><span class='num'>2</span>Courses you've done</div>", unsafe_allow_html=True)
    if st.button("Pre-fill from my semester chart", icon=":material/bolt:", width="stretch", type="primary",
                 help="Adds the courses a student in your year has normally done, and this semester's courses"):
        prefill(d)
        st.rerun()
    n_done = len(d.get("completed") or [])
    with st.expander(f"Completed courses · {n_done}", icon=":material/checklist:", expanded=n_done == 0):
        st.caption("Add or remove rows. Grade is optional: NC, W, I and RC count as not cleared.")
        comp_df = pd.DataFrame(d.get("completed") or [], columns=["code", "grade"]).fillna("")
        comp_df = st.data_editor(comp_df, num_rows="dynamic", width="stretch", hide_index=True,
                                 column_config={"grade": st.column_config.SelectboxColumn(options=GRADES),
                                                "code": st.column_config.TextColumn(help="e.g. CS F213")},
                                 key=f"comp_{d.get('id_no')}_{n_done}")
        d["completed"] = [{"code": r["code"], "grade": r["grade"] or None}
                          for r in comp_df.to_dict("records") if r.get("code")]

    # ---- step 3
    st.markdown("<div class='step'><span class='num'>3</span>This semester</div>", unsafe_allow_html=True)
    offered = sorted(cat.offerings)
    d["current"] = st.multiselect("Registered courses", offered,
                                  default=[c for c in d.get("current", []) if c in offered],
                                  format_func=course_label)
    multi = []
    for code in d["current"]:
        for o in cat.offerings.get(code, [])[:1]:
            by = {}
            for sec in o["sections"]:
                by.setdefault(sec["type"], []).append(sec["section"])
            multi += [(code, kind, secs) for kind, secs in by.items() if len(secs) > 1]
    if multi:
        known = d.get("current_sections") or {}
        n_known = sum(len(v) for v in known.values())
        with st.expander(f"My sections · {n_known}/{len(multi)} set", icon=":material/view_week:"):
            st.caption("Tell us your section to make clash checks exact.")
            new = {}
            for code, kind, secs in multi:
                cur = known.get(code, {}).get(kind, "?")
                v = st.selectbox(f"{code} {kind}", ["?"] + secs, index=(["?"] + secs).index(cur)
                                 if cur in secs else 0, key=f"sec_{code}_{kind}")
                if v != "?":
                    new.setdefault(code, {})[kind] = v
            d["current_sections"] = new

    # ---- step 4
    st.markdown("<div class='step'><span class='num'>4</span>Extras <span class='muted'>(optional)</span></div>",
                unsafe_allow_html=True)
    minors = ["(none)"] + sorted(cat.minors_by_name)
    d["minor"] = st.selectbox("Minor", minors, index=minors.index(d["minor"]) if d.get("minor") in minors else 0)
    d["minor"] = None if d["minor"] == "(none)" else d["minor"]
    d["interests"] = st.text_input("Interests", d.get("interests") or "",
                                   placeholder="e.g. machine learning, finance",
                                   help="Used to rank suggestions when a question has no topic")
    cg = st.number_input("CGPA", 0.0, 10.0, float(d.get("cgpa") or 0.0), 0.01)
    d["cgpa"] = cg or None

    st.divider()
    with st.expander("AI mode (optional)", icon=":material/auto_awesome:"):
        st.caption("Without a key the assistant runs rule-based, and every feature works. Gemini and Groq "
                   "have free keys. The key stays in this browser session only.")
        prov = st.selectbox("Provider", ["auto (.env)", "gemini", "groq", "anthropic", "openai"],
                            help="openai = any OpenAI-compatible server; set the base URL below")
        ui_key = st.text_input("API key", type="password")
        ui_model = st.text_input("Model (blank = default)", "")
        ui_base = st.text_input("Base URL (only for 'openai')", "") if prov == "openai" else ""
    c1, c2 = st.columns(2)
    if c1.button("Save", icon=":material/save:", width="stretch"):
        name = (d.get("id_no") or d.get("name") or "profile").replace(" ", "_")
        (PROFILE_DIR / f"{name}.json").write_text(json.dumps(d, indent=1))
        st.toast(f"Saved as data/profiles/{name}.json", icon=":material/save:")
    if c2.button("Clear chat", icon=":material/delete_sweep:", width="stretch"):
        st.session_state.chat = []
        st.rerun()

profile = Profile.from_dict(json.loads(json.dumps(st.session_state.profile)))
if not profile.programmes:
    st.markdown("<div class='topbar'><div><div class='brand'>BITS Course Recommender</div><div class='brand-sub'>"
                "Type your BITS ID in the sidebar to start, or load a sample student.</div></div></div>",
                unsafe_allow_html=True)
    st.stop()
llm_kw = {}
if prov != "auto (.env)" or ui_key:
    llm_kw = {"provider": None if prov == "auto (.env)" else prov, "api_key": ui_key or None,
              "model": ui_model or None, "base_url": ui_base or None}
rec = Recommender(profile, **llm_kw)
sess = rec.session
req = sess.get_requirements()
state = sess.state

# --------------------------------------------------------------------------- header
degrees = " + ".join(cat.programmes[p]["name"] if p in cat.programmes else p for p in profile.programmes)
mode_pill = f"AI · {rec.label}" if rec.mode == "llm" else "Rule-based · no API key"
st.markdown(
    "<div class='topbar'><div><div class='brand'>BITS Course Recommender</div>"
    f"<div class='brand-sub'>{esc(profile.name or profile.id_no or 'Student')} · {esc(degrees)}</div></div>"
    f"<div><span class='pill'>{esc(profile.id_no or 'no ID')}</span>"
    f"<span class='pill'>Year {profile.year} · Sem 1</span><span class='pill'>{esc(profile.stream or 'PS')}</span>"
    + (f"<span class='pill'>Minor · {esc(profile.minor.replace('Minor in ', ''))}</span>" if profile.minor else "")
    + f"<span class='pill live'>{esc(mode_pill)}</span></div></div>", unsafe_allow_html=True)

units = req["registered_units"]
cdc_left = sum(len(p["cdc_remaining"]) for p in req["programmes"])
del_left = sum(p["del_remaining_courses"] or 0 for p in req["programmes"])
opel_left = req["opel"]["remaining_courses"] if req["opel"]["required_courses"] else None
metrics = [
    ("Units", f"{units}<small> / 25</small>", "#22c55e" if units <= 20 else ("#eab308" if units < 25 else "#ef4444")),
    ("Core left", cdc_left, CAT_COLORS["CDC"][0]),
    ("DELs left", del_left, CAT_COLORS["DEL"][0]),
    ("HUELs left", req["huel"]["remaining_courses"], CAT_COLORS["HUEL"][0]),
    ("OPELs left", "—" if opel_left is None else opel_left, CAT_COLORS["OPEL"][0]),
    ("Open to you", len(sess.eligible), "#8b7bff"),
]
st.markdown("<div class='metrics'>" + "".join(
    f"<div><div class='l'><span class='dot' style='background:{c}'></span>{esc(l)}</div><div class='v'>{v}</div></div>"
    for l, v, c in metrics) + "</div>", unsafe_allow_html=True)
for n in req["notes"]:
    st.markdown(f"<div class='banner info'>{esc(n)}</div>", unsafe_allow_html=True)

tab_req, tab_ask, tab_plan, tab_elig, tab_data = st.tabs(
    [":material/space_dashboard: Overview", ":material/forum: Ask", ":material/calendar_month: Plan semester",
     ":material/task_alt: Eligible courses", ":material/database: Data sources"])


# --------------------------------------------------------------------------- overview
def bucket_card(title, key, summary, required, remaining=None, note=""):
    """one requirement bucket: title + count, a thin progress bar, then done / now / left as tag rows"""
    done, doing = summary.get("done") or [], summary.get("in_progress") or []
    color = CAT_COLORS[key][0]
    with st.container(border=True):
        count = f"{len(done) + len(doing)}<span class='muted'> / {required}</span>" if required else ""
        parts = [f"<div class='crow-top'><span class='ctitle2'>{esc(title)}</span>"
                 f"<span class='crow-right' style='font-weight:650'>{count}</span></div>"]
        if required:
            parts.append(progress_bar(len(done), len(doing), required, color))

        def row(label, chips):
            return f"<div class='rowlabel'>{label}</div>" + "".join(chips)
        if done:
            parts.append(row("Done", [badge(c, *DONE) for c in done]))
        if doing:
            parts.append(row("This semester", [badge(c, *NOW) for c in doing]))
        if remaining:
            parts.append(row("Still to do", [slot_badge(g) for g in remaining]))
        elif remaining is not None and required:
            parts.append("<div class='rowlabel' style='color:#9be8b7'>Nothing left here</div>")
        if note:
            parts.append(f"<div class='muted' style='margin-top:6px'>{esc(note)}</div>")
        st.markdown("".join(parts), unsafe_allow_html=True)


with tab_req:
    with st.expander("How to use this app", icon=":material/help:", expanded=not st.session_state.chat):
        st.markdown(
            "1. **Set up your profile** in the sidebar: type your BITS ID, press **Pre-fill**, then fix anything "
            "that's different for you.\n"
            "2. **Overview** shows what you still need to graduate.\n"
            "3. **Ask** anything in plain English, or tap a ready-made question.\n"
            "4. **Plan semester** builds your timetable: core courses filled in, clash-free sections, exam calendar.\n"
            "5. **Eligible courses** lists everything you can take, and *why not* for the rest.\n\n"
            + "".join(cat_badge(k, CAT_NAMES[k]) for k in CAT_COLORS)
            + "<br>" + badge("done", *DONE) + badge("this semester", *NOW) + badge("still to do", *LEFT)
            + badge("A or B = take either one", *LEFT, "or"), unsafe_allow_html=True)

    # per programme: core + DEL
    for prog, sp in zip(req["programmes"], state["programmes"]):
        st.markdown(f"#### {prog['programme']}")
        a, b = st.columns(2)
        with a:
            cdc = sp["cdc"]
            rem = cdc.get("remaining", [])
            bucket_card("Core courses (CDC)", "CDC", cdc, len(cdc["done"]) + len(cdc["in_progress"]) + len(rem), rem)
        with b:
            dl = sp["del"]
            bucket_card("Discipline electives (DEL)", "DEL", dl, dl["required_courses"], None, prog["del_note"] or "")

    a, b, c = st.columns(3)
    with a:
        g = state["gir"]
        rem = g.get("remaining", [])
        bucket_card("General (GIR)", "GIR", g, len(g["done"]) + len(g["in_progress"]) + len(rem), rem)
    with b:
        bucket_card("Humanities (HUEL)", "HUEL", state["huel"], state["huel"]["required_courses"])
    with c:
        o = state["opel"]
        bucket_card("Open electives (OPEL)", "OPEL", o, o["required_courses"], None, o.get("note") or "")

    if req["minor"]:
        m = req["minor"]
        with st.container(border=True):
            st.markdown(f"<div class='crow-top'><span class='ctitle2'>{esc(m['name'])}</span></div>",
                        unsafe_allow_html=True)
            if m.get("error"):
                st.warning(m["error"])
            else:
                st.markdown(progress_bar(m["courses_counted"], 0, m["required_courses"] or 1, "#f472b6")
                            + f"<div class='muted'>{m['courses_counted']} of {m['required_courses']} courses · "
                              f"{m['units_counted']} of {m['required_units']} units</div>", unsafe_allow_html=True)
                if m["core_remaining"]:
                    st.markdown("<div class='rowlabel'>Core still to do</div>"
                                + " ".join(slot_badge(g) for g in m["core_remaining"]), unsafe_allow_html=True)
                if not m["overlap_ok"]:
                    st.warning("More than 2 courses / 6 units overlap with your mandatory courses (bulletin IV-129).")
                st.caption(m["rules"]["gpa"] + " · " + m["source"])

    st.markdown("#### Graduation checklist")
    g = req["graduation"]
    rows = []
    for i in g["items"]:
        col = "#22c55e" if i["met"] else ("#6b7392" if i["met"] is None else "#eab308")
        rows.append(f"<div class='checkrow'><span class='st' style='background:{col}'></span>"
                    f"<span>{esc(i['requirement'])}</span><span class='d'>{esc(i['detail'])}</span></div>")
    st.markdown("".join(rows), unsafe_allow_html=True)
    st.caption(g["source"])
    if req["not_cleared"]:
        st.warning("Not cleared (NC / W / I ...): " + ", ".join(req["not_cleared"]))


# --------------------------------------------------------------------------- course cards
PROP_CLASS = {True: ("yes", "✓"), False: ("no", "✗"), None: ("unk", "?")}


def _short_date(d):
    """'2026-10-05' -> '5 Oct'"""
    try:
        from datetime import date
        x = date.fromisoformat(d.split()[0])
        return f"{x.day} {x.strftime('%b')}" + (f" {d.split()[1]}" if len(d.split()) > 1 else "")
    except Exception:
        return d


def render_card(r):
    """one course as a compact row: code · title · requirement · units, one line of why, then details"""
    if r.get("locked"):
        return render_locked_panel([r])
    shown = r.get("shown_as") or r["fills"]
    with st.container(border=True):
        also = f" <span class='muted'>= {esc(', '.join(r['also']))}</span>" if r.get("also") else ""
        top = (f"<div class='crow-top'><span class='ccode'>{esc(r['code'])}</span>"
               f"<span class='ctitle2'>{esc(r['title'])}</span>{also}"
               f"<span class='crow-right'>{cat_badge(shown)}{badge(str(r['units']) + ' units')}</span></div>")
        why = []
        if r.get("anchor"):
            why.append(f"<b>{esc(r['anchor'])}</b>")
        if r.get("similarity") and r["similarity"] >= 0.25:
            why.append(f"{int(round(r['similarity'] * 100))}% similar in content")
        if r.get("match_terms"):
            why.append("mentions " + ", ".join(esc(t.replace('_', ' ')) for t in r["match_terms"][:4]))
        if r.get("related"):
            why.append(f"related to '{esc(r.get('related_to'))}'")
        meta = [f"Midsem {_short_date(r['midsem'])}" if r["midsem"] else "No midsem slot",
                f"Compre {_short_date(r['compre'])}" if r["compre"] else None,
                "Sections " + ", ".join(r["sections"].values()) if r["sections"] else None,
                f"IC {r['ic']}" if r.get("ic") else None]
        lines = top
        if why:
            lines += f"<div class='crow-meta'>{' · '.join(why)}</div>"
        lines += f"<div class='crow-meta'>{esc(' · '.join(m for m in meta if m))}</div>"
        props = r.get("properties") or {}
        if props:
            lines += "<div class='props'>" + "".join(
                f"<span class='prop2 {PROP_CLASS[pr['value']][0]}'>{PROP_CLASS[pr['value']][1]} "
                f"{esc(PROP_LABELS.get(n, n))}</span>" for n, pr in props.items()) + "</div>"
        st.markdown(lines, unsafe_allow_html=True)
        if r.get("agent_reason"):
            st.caption(r["agent_reason"])
        with st.expander("Details", icon=":material/info:"):
            fills = r["why_category"] if shown == r["fills"] else \
                f"You asked for {shown}; it's in your {r['fills']} pool, so it can be filed as either (reg 2.05)."
            st.markdown(f"**Counts as {shown}:** {fills}")
            if len(r["can_count_as"]) > 1:
                st.markdown(f"**Can also count as:** {', '.join(c for c in r['can_count_as'] if c != shown)}")
            for n, pr in props.items():
                st.markdown(f"<div class='prop {PROP_CLASS[pr['value']][0]}'><b>{esc(PROP_LABELS.get(n, n))}</b>"
                            f"<br><span class='muted'>{esc(pr.get('evidence'))}</span></div>", unsafe_allow_html=True)
            st.markdown("**Eligibility:** " + "; ".join(r["eligibility"]))
            src = r["sources"]
            st.caption(f"Timetable p.{(src['timetable'] or {}).get('page')}"
                       + (f" · Bulletin p.{src['bulletin']['page']}" if src.get("bulletin") else "")
                       + (f" · Handout: {src['handout']}" if src.get("handout") else " · no handout supplied"))


def _short_reason(reasons):
    """'CDC/DEL of C8; needs all your year 1-2 named courses cleared first (...) (Reg 3.15(b)(i))' ->
    'Reg 3.15(b)(i) · other programme's course, after your year 1-2'"""
    if not reasons:
        return ""
    r = reasons[0]
    m = re.search(r"\((Reg [^()]+(?:\([^()]*\))*[^()]*|Timetable[^()]*)\)\s*$", r)
    clause = m.group(1) if m else ""
    txt = r[:m.start()].strip() if m else r
    if "year 1-2" in txt:
        txt = "other programme's course: opens after your year 1-2 courses"
    elif "higher degree" in txt:
        txt = "higher degree course"
    elif "clashes" in txt:
        txt = "exam or class clash with your courses"
    elif "units" in txt:
        txt = "over the 25-unit cap"
    return (f"{clause} · " if clause else "") + txt[:70] + (f" +{len(reasons) - 1} more" if len(reasons) > 1 else "")


def render_locked_panel(rows, title="Not open to you this semester"):
    """courses that match but can't be taken yet: one compact panel, one line each, full reasons folded"""
    if not rows:
        return
    with st.container(border=True):
        st.markdown(f"<div class='sect bad' style='margin-top:0'>{esc(title)} <span class='n'>{len(rows)}</span></div>"
                    + "".join(
                        f"<div class='lockrow'><span class='ccode'>{esc(r['code'])}</span>"
                        f"<span class='ctitle2' style='font-weight:500'>{esc(r['title'])}</span>"
                        + (f"<span class='muted'>= {esc(', '.join(r['also']))}</span>" if r.get("also") else "")
                        + cat_badge(r["fills"])
                        + f"<span class='why'>{esc(_short_reason(r.get('blocked_by')))}</span></div>" for r in rows),
                    unsafe_allow_html=True)
        with st.expander("Full reasons", icon=":material/gavel:"):
            for r in rows:
                st.markdown(f"**{r['code']}** {r['title']}: " + "; ".join(r.get("blocked_by") or []))


def render_cards(recs):
    locked = [r for r in recs if r.get("locked")]
    for r in recs:
        if not r.get("locked"):
            render_card(r)
    render_locked_panel(locked)


# --------------------------------------------------------------------------- ask
def answer(q):
    """run one question through the agent and store it in the chat history"""
    hist = [{"role": t["role"], "content": t["content"]} for t in st.session_state.chat]
    st.session_state.chat.append({"role": "user", "content": q})
    with st.spinner("Checking requirements, eligibility and handouts..."):
        out = rec.ask(q, hist)
    text, footer = out["text"], ""
    if out["mode"] == "rules" and out["recommendations"]:
        # numbered course blocks become cards; the header goes above them and the footers
        # (could-not-verify / blocked matches / closest options) below
        m = re.search(r"\n\n\*\*1\. .*?(?=\n\n\*\*(Could not verify|Matches your topic|Closest options)|\Z)", text, re.S)
        if m:
            text, footer = text[:m.start()], text[m.end():].strip()
    blocked = out.get("blocked") or []
    if blocked:
        footer = re.sub(r"\*\*Matches your topic, but not open to you this semester:\*\*.*?(?=\n\n\*\*|\Z)", "",
                        footer, flags=re.S).strip()
        text = re.sub(r"\*\*Matches your topic, but not open to you this semester:\*\*.*?(?=\n\n\*\*|\Z)", "",
                      text, flags=re.S).strip()
    extra = []
    if out["mode"] == "llm" and out.get("could_not_verify"):
        extra.append("Could not verify for: " + ", ".join(c["code"] for c in out["could_not_verify"]))
    if out.get("rejected_by_validation"):
        extra.append("Dropped by policy validation: " +
                     ", ".join(f"{r['code']} ({'; '.join(r['reasons'])})" for r in out["rejected_by_validation"]))
    st.session_state.chat.append({"role": "assistant", "content": text, "cards": out["recommendations"],
                                  "footer": footer, "extra": extra, "blocked": blocked,
                                  "blocked_first": out.get("blocked_first", False)})


def show_turn(turn):
    with st.chat_message(turn["role"], avatar=":material/person:" if turn["role"] == "user" else ":material/school:"):
        st.markdown(turn["content"])
        blocked = turn.get("blocked") or []
        if blocked and turn.get("blocked_first"):
            render_locked_panel(blocked, "Best matches · not open to you this semester")
            if turn.get("cards"):
                st.markdown("<div class='sect ok'>Closest courses you can take now</div>", unsafe_allow_html=True)
        if turn.get("cards"):
            render_cards(turn["cards"])
        if blocked and not turn.get("blocked_first"):
            render_locked_panel(blocked, "Also matches · not open to you this semester")
        if turn.get("footer"):
            st.markdown(turn["footer"])
        for x in turn.get("extra") or []:
            st.caption(x)


with tab_ask:
    mode = st.segmented_control("Search", [":material/forum: Chat", ":material/tune: Guided search"],
                                default=":material/forum: Chat", key="ask_mode", label_visibility="collapsed")
    if mode == ":material/tune: Guided search":
        with st.container(border=True):
            g1, g2 = st.columns([1, 2])
            gcat = g1.pills("Requirement", ["Any", "DEL", "HUEL", "OPEL", "CDC"], default="Any", key="g_cat")
            gprops = g2.pills("Must have", list(PROP_LABELS), selection_mode="multi",
                              format_func=PROP_LABELS.get, key="g_props")
            g3, g4, g5 = st.columns([2, 1, 1])
            gtopic = g3.text_input("Topic or interest", placeholder="e.g. machine learning, finance, robotics",
                                   key="g_topic")
            gfree = g4.selectbox("Keep a day free", ["-"] + DAYS, format_func=lambda x: DAY_NAMES.get(x, "-"),
                                 key="g_free")
            gno8 = g5.toggle("No 8 AM", key="g_no8")
            go = st.button("Find courses", icon=":material/search:", type="primary")
        if go:
            cats = [] if gcat in (None, "Any") else [gcat]
            res = sess.find_courses(categories=cats, topics=gtopic or None, require=gprops or [],
                                    no_8am=gno8, free_day=None if gfree == "-" else gfree, limit=8)
            if cats:
                for r in res["results"]:
                    if cats[0] in r["can_count_as"]:
                        r["shown_as"] = cats[0]
            n = len(res["results"])
            groups = res.get("topic_groups") or []
            st.markdown(f"<div class='sect ok'>Matches you can take <span class='n'>{n}</span></div>"
                        + (f"<div class='muted'>Topic groups in the Bulletin: {esc(' · '.join(groups))}</div>"
                           if groups else ""), unsafe_allow_html=True)
            render_cards(res["results"])
            rel = res.get("related") or {}
            if rel.get("results"):
                st.markdown(f"<div class='sect'>Related <span class='n'>{len(rel['results'])}</span></div>"
                            f"<div class='muted'>Linked to '{esc(gtopic)}' through: {esc(', '.join(rel['terms'][:6]))}"
                            "</div>", unsafe_allow_html=True)
                render_cards(rel["results"])
            if res["could_not_verify"]:
                with st.container(border=True):
                    st.markdown("<div class='sect' style='margin-top:0'>Might fit · the handout doesn't say</div>",
                                unsafe_allow_html=True)
                    for c in res["could_not_verify"]:
                        st.markdown(f"- **{c['code']}** {c['title']}: "
                                    + "; ".join(f"{PROP_LABELS.get(k, k)}: {v}" for k, v in c["why"].items()))
            if gtopic:
                blocked = sess.blocked_matches(gtopic, cats or None)
                render_locked_panel(blocked)
            if not n and gprops:
                near = sess.near_misses(cats or None, gtopic or None, gprops)
                if near:
                    st.markdown("<div class='sect'>Closest options · check the ✗ / ? tags</div>", unsafe_allow_html=True)
                    render_cards(near)
    else:
        qcols = st.columns(len(QUICK_QUESTIONS))
        clicked = None
        for i, (label, question) in enumerate(QUICK_QUESTIONS):
            if qcols[i].button(label, width="stretch", key=f"qq_{i}"):
                clicked = question
        typed = st.chat_input("Ask about courses for this semester")
        q = typed or clicked
        if q:
            answer(q)
        if not st.session_state.chat:
            st.markdown("<div class='muted' style='margin-top:6px'>Try <i>biotech courses</i>, <i>can I take CS F317 "
                        "and GS F232 together with no gaps?</i> or <i>prerequisites of CS F425</i></div>",
                        unsafe_allow_html=True)
        chat = st.session_state.chat
        pairs = [chat[i:i + 2] for i in range(0, len(chat), 2)]
        for n, pair in enumerate(reversed(pairs)):
            if n == 1:
                st.markdown("<div class='muted'>Earlier questions</div>", unsafe_allow_html=True)
            for turn in pair:
                show_turn(turn)


# --------------------------------------------------------------------------- plan
def course_colours(codes):
    """stable colour per course, in plan order"""
    out = {}
    for c in codes:
        if c not in out:
            out[c] = COURSE_COLORS[len(out) % len(COURSE_COLORS)]
    return out


def week_grid(entries, registered, colours):
    """entries: [(code, section, slots)] -> html table, days x hours; every course its own colour,
    registered courses with a dashed outline, overlaps in red"""
    grid = {}
    for code, sec, slots in entries:
        for sl in slots:
            grid.setdefault((sl["day"], sl["hour"]), []).append((code, sec))
    hours = sorted({h for _, h in grid} | set(range(1, 11)))
    rows = ["<tr><th></th>" + "".join(f"<th>{DAY_NAMES[d]}</th>" for d in DAYS) + "</tr>"]
    for h in hours:
        cells = []
        for d in DAYS:
            v = grid.get((d, h), [])
            if len(v) > 1:
                cells.append("<td class='clash'>" + "<br>".join(f"{esc(c)} {esc(s)}" for c, s in v) + "</td>")
            elif v:
                c, s = v[0]
                solid, fill = colours.get(c, COURSE_COLORS[0])
                border = f"1px dashed {solid}" if c in registered else f"1px solid {solid}"
                cells.append(f"<td style='background:{fill};border:{border}'><b>{esc(c)}</b> {esc(s)}</td>")
            else:
                cells.append("<td></td>")
        rows.append(f"<tr><td class='time'>{esc(HOUR_LABELS.get(h, f'slot {h}'))}</td>{''.join(cells)}</tr>")
    shown = [c for c in colours if any(e[0] == c for e in entries)]
    legend = "".join(f"<span style='background:{colours[c][1]};border:1px {'dashed' if c in registered else 'solid'} "
                     f"{colours[c][0]}'>{esc(c)}</span>" for c in shown)
    return (f"<div class='legend'>{legend}<span style='background:rgba(239,68,68,.5)'>clash</span>"
            f"<span class='muted' style='background:none'>dashed = already registered</span></div>"
            f"<table class='week'>{''.join(rows)}</table>")


def exam_calendar(exam_rows, colours):
    """month-style calendar(s) of the midsem and compre dates, only the weeks that have exams"""
    import calendar as _cal
    from datetime import date
    by_day = {}
    for r in exam_rows:
        by_day.setdefault(r["date"], []).append(r)
    if not by_day:
        return "<div class='muted'>No exam slots listed for these courses.</div>"
    days = sorted(date.fromisoformat(d) for d in by_day)
    html_ = []
    cal = _cal.Calendar(firstweekday=0)
    for (y, m) in sorted({(d.year, d.month) for d in days}):
        weeks = [w for w in cal.monthdatescalendar(y, m) if any(x.isoformat() in by_day and x.month == m for x in w)]
        head = "".join(f"<th>{d}</th>" for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        body = []
        for w in weeks:
            tds = []
            for x in w:
                ex = by_day.get(x.isoformat(), []) if x.month == m else []
                chips = "".join(
                    f"<div class='exchip' style='background:{colours.get(e['code'], COURSE_COLORS[0])[1]};"
                    f"border-left:3px solid {colours.get(e['code'], COURSE_COLORS[0])[0]}'>"
                    f"<b>{esc(e['code'])}</b><br><span>{esc(e['exam'])} · {esc(e['session'])} {esc(e['time'])}</span></div>"
                    for e in sorted(ex, key=lambda e: e["session"]))
                cls = "busy2" if len(ex) > 1 else ("busy" if ex else "")
                cls += " other" if x.month != m else ""
                tds.append(f"<td class='{cls}'><div class='dnum'>{x.day}</div>{chips}</td>")
            body.append("<tr>" + "".join(tds) + "</tr>")
        html_.append(f"<div class='calmonth'>{_cal.month_name[m]} {y}</div>"
                     f"<table class='excal'><tr>{head}</tr>{''.join(body)}</table>")
    return "".join(html_)


TYPE_SHORT = {"lecture": "L", "tutorial": "T", "practical": "P"}


def slot_text(slots):
    """[{day, hour}] -> 'T Th F 10:00' (days grouped by start hour)"""
    by_hour = {}
    for sl in slots:
        by_hour.setdefault(sl["hour"], []).append(sl["day"])
    parts = []
    for h, ds in sorted(by_hour.items()):
        ds = sorted(set(ds), key=DAYS.index)
        parts.append(f"{' '.join(ds)} {HOUR_LABELS.get(h, h)}")
    return "; ".join(parts) or "no time listed"


def sec_label(s_):
    who = ", ".join(i.title() for i in (s_.get("instructors") or [])[:2]) or "instructor n/a"
    return f"{s_['section']} · {who} · {slot_text(s_['slots'])}"


@st.cache_data(show_spinner=False)
def unlocks_map():
    """course -> courses whose stated prerequisites include it (bulletin Part VI)"""
    out = {}
    for code, c in cat.courses.items():
        for pre in c.get("prerequisite_codes") or []:
            out.setdefault(cat.canon(pre), []).append(code)
    return out


with tab_plan:
    st.markdown("<div class='muted'>Your timetable for First Semester 2026-27. Core courses due this semester are "
                "filled in for you; add electives, set your preferences, and every course is filed as CDC / DEL / "
                "HUEL / OPEL and given sections that don't clash.</div>", unsafe_allow_html=True)
    due = due_by(cat, profile.programmes, profile.year, 1) if all(p in cat.programmes for p in profile.programmes) else []
    # one course per requirement slot: cross-listed codes (CS F215 = EEE F215 = INSTR F215) and
    # 'A or B' slots count once - prefer the code the chart itself names
    slot_of = {}
    for pid in profile.programmes:
        for grp in (cat.programmes.get(pid) or {}).get("cdc_groups", []):
            for m in grp:
                slot_of[cat.canon(m["code"])] = tuple(sorted(cat.canon(x["code"]) for x in grp))
    for grp in cat.gir_alternatives:
        for c_ in grp:
            slot_of[cat.canon(c_)] = tuple(sorted(cat.canon(x) for x in grp))
    core_due, seen_slots = [], set()
    cands = [c for c, x in sess.eligible.items()
             if x["category"] in ("CDC", "GIR") and any(cat.same(c, d) for d in due)]
    for c_ in sorted(cands, key=lambda c: (c not in due, c)):
        slot = slot_of.get(cat.canon(c_), (cat.canon(c_),))
        if slot not in seen_slots:
            seen_slots.add(slot)
            core_due.append(c_)
    core_due.sort()

    t1, t2 = st.columns(2)
    autofill = t1.toggle("Auto-fill my core courses (CDC)", value=True,
                         help="Adds the CDC / GIR courses your chart puts in this semester (or earlier) that you "
                              "haven't registered yet. Turn off to plan only what you pick.")
    sched_reg = t2.toggle("Pick sections for my registered courses too", value=True,
                          help="Chooses clash-free sections for everything you're registered in, keeping any "
                               "section you gave in the sidebar. Off = only the hours we know for sure are blocked.")
    # any course in the timetable you haven't done / registered - ones you're not allowed to take are still
    # placed (so you can see clashes) and flagged with the rule that blocks them
    choosable = sorted(set(sess.eligible) | set(sess.ineligible))

    def pick_label(c):
        if c in sess.eligible:
            return f"{course_label(c)}  [{sess.eligible[c]['category']}]"
        return f"{course_label(c)}  [not allowed]"
    picks = st.multiselect("Add any course to your semester", choosable,
                           default=core_due if autofill else [], format_func=pick_label,
                           placeholder="Type a course code or title, e.g. CS F317 or psychology...",
                           key=f"plan_{profile.id_no}_{autofill}")
    if autofill:
        st.caption(("Auto-filled: " + ", ".join(core_due)) if core_due else
                   "No core course is left to add: your CDCs for this semester are already registered "
                   "(or not open to you yet). Add electives above.")

    with st.container(border=True):
        st.markdown("<div class='sect' style='margin-top:0'>Preferences</div>", unsafe_allow_html=True)
        p1, p2 = st.columns([1, 3])
        p1.markdown("Free day<br><span class='muted'>keep one day clear</span>", unsafe_allow_html=True)
        free_pick = p2.pills("Free day", ["None"] + [DAY_NAMES[d] for d in DAYS], default="None",
                             key="pref_free", label_visibility="collapsed")
        free = next((d for d in DAYS if DAY_NAMES[d] == free_pick), None)
        p1, p2 = st.columns([1, 3])
        p1.markdown("Earliest class<br><span class='muted'>skip 8 AM classes</span>", unsafe_allow_html=True)
        no8 = p2.toggle("No 8 AM classes", key="pref_no8")

    plan_codes = list(picks) + (list(profile.current) if sched_reg else [])
    # sections the student would accept, from the per-course pickers below (kept in session state)
    allowed = {}
    for code in plan_codes:
        for kind in ("lecture", "tutorial", "practical"):
            v = st.session_state.get(f"allow_{code}_{kind}")
            if v:
                allowed.setdefault(code, {})[kind] = list(v)

    if not picks and not (sched_reg and profile.current):
        st.markdown("<div class='banner info'>Add one or more courses to see how they fit.</div>",
                    unsafe_allow_html=True)
    else:
        cache_key = json.dumps([picks, no8, free, sched_reg, allowed, profile.current, profile.current_sections,
                                profile.id_no], sort_keys=True, default=str)
        if st.session_state.get("plan_cache_key") != cache_key:
            st.session_state.plan_cache = sess.check_plan(picks, no8, free, False, sched_reg, allowed,
                                                          include_ineligible=True, options=30)
            st.session_state.plan_cache_key = cache_key
            st.session_state.tt_opt = 1
        out = st.session_state.plan_cache

        # ---- which timetable option is shown
        opts = out.get("options") or []
        if opts:
            opt_i = min(st.session_state.get("tt_opt", 1), len(opts))
            chosen_secs = opts[opt_i - 1]["sections"]
        else:
            opt_i, chosen_secs = 0, {}

        def secs_of(code, default):
            return chosen_secs.get(code) or default

        problems = []
        if out["total_units"] > 25:
            problems.append(f"over the 25-unit cap by {out['total_units'] - 25}")
        for d_ in out["clash_details"]:
            problems.append(f"{d_['code']} clashes" + (f" with {', '.join(d_['with'])}" if d_["with"] else ""))
        n_blocked = sum(1 for p in out["picks"] if p.get("not_allowed"))
        if n_blocked:
            problems.append(f"{n_blocked} course(s) you're not allowed to take")
        for r in out["rejected"]:
            problems.append(f"{r['code']} can't be added")
        ok = not problems

        # exams + same-day check (different sessions; the same session would already be a clash)
        order = [p["code"] for p in out["picks"]] + [r["code"] for r in out.get("registered", [])]
        colours = course_colours(order)
        exam_rows = []
        for code in order:
            o = cat.offerings[code][0]
            for kind, dkey, skey, times in (("Midsem", "midsem_date", "midsem_session", MIDSEM_SESSIONS),
                                            ("Compre", "compre_date", "compre_session", COMPRE_SESSIONS)):
                if o.get(dkey):
                    exam_rows.append({"date": o[dkey], "session": o[skey], "time": times.get(o[skey], ""),
                                      "exam": kind, "code": code})
        same_day = {}
        for r in exam_rows:
            same_day.setdefault(r["date"], []).append(r["code"])
        same_day_codes = {c for v in same_day.values() if len(v) > 1 for c in v}

        sc = st.columns(4)
        sc[0].markdown(stat_tile("Courses", len(order), f"{len(out['picks'])} added", "#8b7bff"), unsafe_allow_html=True)
        sc[1].markdown(stat_tile("Units", f"{out['total_units']} / 25", "cap per semester",
                                 "#22c55e" if out["total_units"] <= 25 else "#ef4444"), unsafe_allow_html=True)
        sc[2].markdown(stat_tile("Timetable", "Clash-free" if not out["clash_details"] else "Clash",
                                 f"{len(opts)} options to browse" if opts else "no clash-free option",
                                 "#22c55e" if not out["clash_details"] else "#ef4444"), unsafe_allow_html=True)
        sc[3].markdown(stat_tile("Same-day exams", len([k for k, v in same_day.items() if len(v) > 1]),
                                 "days with 2+ exams", "#eab308" if same_day_codes else "#22c55e"),
                       unsafe_allow_html=True)
        msg = (f"This plan works · {out['total_units']} / 25 units · clash-free" if ok else
               f"{out['total_units']} / 25 units · " + " · ".join(problems))
        st.markdown(f"<div class='banner {'ok' if ok else 'bad'}'>{esc(msg)}</div>", unsafe_allow_html=True)
        for d_ in out["clash_details"]:
            st.error(f"**{d_['code']}** doesn't fit" + (f": it clashes with **{', '.join(d_['with'])}**" if d_["with"] else "")
                     + (" - " + "; ".join(d_["problems"]) if d_["problems"] else "")
                     + ". It's drawn in red below; try other sections or another course.", icon=":material/block:")
        for r in out["rejected"]:
            st.error(f"**{r['code']}** can't be added: {'; '.join(r['reasons'])}", icon=":material/block:")

        unl = unlocks_map()

        def plan_card(col, code, title, units_, tag_html, chosen, fits=True, blocked=None):
            o = cat.offerings[code][0]
            by = {}
            for s_ in o["sections"]:
                by.setdefault(s_["type"], []).append(s_)
            counts = " · ".join(f"{len(v)} {TYPE_SHORT.get(k, k[:1].upper())}" for k, v in by.items())
            restricted = allowed.get(code)
            pre = (cat.courses.get(code) or {}).get("prerequisite_codes") or []
            opens = unl.get(cat.canon(code), [])[:3]
            solid = colours.get(code, COURSE_COLORS[0])[0]
            with col.container(border=True):
                flags = (("" if fits else badge("clashes", *LEFT)) + (badge("not allowed", *LEFT) if blocked else "")
                         + (badge("same-day exam", "#f5cf7a", "rgba(234,179,8,.12)") if code in same_day_codes else ""))
                meta = [counts + (" · your picks only" if restricted else ""),
                        f"needs {', '.join(pre)}" if pre else None, f"unlocks {', '.join(opens)}" if opens else None,
                        None if cat.handout(code) else "no handout"]
                st.markdown(
                    f"<div class='crow-top'><span style='color:{solid};font-size:.8rem'>●</span>"
                    f"<span class='ccode'>{esc(code)}</span><span class='crow-right'>{tag_html}"
                    f"{badge(str(units_) + ' units')}</span></div>"
                    f"<div class='crow-meta' style='margin-top:2px'>{esc(title)}</div>"
                    + (f"<div style='margin-top:6px'>{flags}</div>" if flags else "")
                    + (f"<div class='crow-meta' style='color:#f3adad'>{esc('; '.join(blocked))}</div>" if blocked else "")
                    + f"<div class='crow-meta'>{esc(' · '.join(m for m in meta if m))}</div>"
                    + "<div style='margin-top:6px'>" + "".join(badge(f"{k} {v}", *INFO) for k, v in chosen.items())
                    + "</div>", unsafe_allow_html=True)
                with st.popover("Choose sections", icon=":material/tune:", width="stretch"):
                    st.markdown(f"**{code}** · {title}")
                    st.caption("Pick the sections you'd accept. Pick none to allow all.")
                    for kind, secs in by.items():
                        opts_ = [x["section"] for x in secs]
                        lab = {x["section"]: sec_label(x) for x in secs}
                        st.pills(kind.title(), opts_, selection_mode="multi", format_func=lambda v, lab=lab: lab[v],
                                 key=f"allow_{code}_{kind}")
                    rows_ = [("Requires", ", ".join(pre) or "None listed"),
                             ("Unlocks", ", ".join(unl.get(cat.canon(code), [])[:8]) or "Nothing in the catalogue")]
                    st.markdown("".join(f"<div class='checkrow na'><b>{a}</b><span class='d'>{esc(b)}</span></div>"
                                        for a, b in rows_), unsafe_allow_html=True)

        # ---- timetable browser + week grid (first, it's the main thing)
        st.markdown("#### Your week")
        if len(opts) > 1:
            b1, b2, b3 = st.columns([1, 4, 1])
            if b1.button("Previous", icon=":material/chevron_left:", width="stretch", disabled=opt_i <= 1):
                st.session_state.tt_opt = opt_i - 1
                st.rerun()
            b2.markdown(f"<div class='banner info' style='text-align:center;margin:0'>Timetable option "
                        f"<b>{opt_i}</b> of <b>{len(opts)}</b> · {opts[opt_i - 1]['gap_hours']} free hours between "
                        f"classes · {opts[opt_i - 1]['days_used']} days on campus"
                        + (" · fewest gaps" if opt_i == 1 else "") + "</div>", unsafe_allow_html=True)
            if b3.button("Next", icon=":material/chevron_right:", width="stretch", disabled=opt_i >= len(opts)):
                st.session_state.tt_opt = opt_i + 1
                st.rerun()
            st.caption("Options differ in sections only (same courses), best first. Fix a section with "
                       "Choose sections to narrow them down.")
        entries = []
        for r in out.get("registered", []) if sched_reg else []:
            o = cat.offerings[r["code"]][0]
            mine = secs_of(r["code"], r["sections"])
            entries += [(r["code"], s_["section"], s_["slots"]) for s_ in o["sections"]
                        if mine.get(s_["type"]) == s_["section"]]
        if not sched_reg:
            for code in profile.current:
                for o in cat.offerings.get(code, [])[:1]:
                    by = {}
                    for s_ in o["sections"]:
                        by.setdefault(s_["type"], []).append(s_)
                    for kind, secs in by.items():
                        mine = profile.current_sections.get(code, {}).get(kind)
                        pk = [x for x in secs if x["section"] == mine] or (secs if len(secs) == 1 else [])
                        if pk:
                            entries.append((code, pk[0]["section"], pk[0]["slots"]))
        for p in out["picks"]:
            o = cat.offerings[p["code"]][0]
            mine = secs_of(p["code"], p["sections"])
            entries += [(p["code"], s_["section"], s_["slots"]) for s_ in o["sections"]
                        if mine.get(s_["type"]) == s_["section"]]
        st.markdown(week_grid(entries, set(profile.current), colours), unsafe_allow_html=True)

        # ---- course cards
        if out["picks"]:
            st.markdown("#### Adding")
            cols = st.columns(3)
            for i, p in enumerate(out["picks"]):
                tag = cat_badge(p["filed_as"], f"Filed as {p['filed_as']}") if not p.get("not_allowed") else ""
                plan_card(cols[i % 3], p["code"], p["title"], p["units"], tag, secs_of(p["code"], p["sections"]),
                          p.get("fits", True), p.get("not_allowed"))
        if out.get("registered"):
            st.markdown("#### Already registered")
            cols = st.columns(3)
            for i, r in enumerate(out["registered"]):
                given = profile.current_sections.get(r["code"], {})
                plan_card(cols[i % 3], r["code"], r["title"], r["units"],
                          badge("your section" if given else "registered", *GREY), secs_of(r["code"], r["sections"]))
        for w in out["warnings"]:
            if "allowed per semester" not in w:      # the unit cap is already in the banner above
                st.warning(w, icon=":material/warning:")

        # ---- exam calendar
        st.markdown("#### Exam calendar")
        if same_day_codes:
            st.markdown(f"<div class='banner bad'>Two exams on the same day: "
                        + esc(" · ".join(f"{d} ({', '.join(v)})" for d, v in sorted(same_day.items()) if len(v) > 1))
                        + "</div>", unsafe_allow_html=True)
        st.markdown(exam_calendar(exam_rows, colours), unsafe_allow_html=True)
        st.caption("Only the weeks with exams are shown. Yellow outline = 2+ exams that day.")

        with st.expander("Download", icon=":material/download:"):
            rows_ = [{"code": c, "section": s_, "day": sl["day"], "start": HOUR_LABELS.get(sl["hour"], sl["hour"])}
                     for c, s_, slots in entries for sl in slots]
            st.download_button("Timetable (CSV)", pd.DataFrame(rows_).to_csv(index=False),
                               file_name=f"timetable_{profile.id_no or 'plan'}.csv", mime="text/csv")
            st.download_button("Exam calendar (CSV)", pd.DataFrame(exam_rows).to_csv(index=False),
                               file_name=f"exams_{profile.id_no or 'plan'}.csv", mime="text/csv")
            st.download_button("Full plan (JSON)", json.dumps({k: v for k, v in out.items()
                                                                  if k != "requirements_after"}, indent=1, default=str),
                               file_name=f"plan_{profile.id_no or 'plan'}.json", mime="application/json")


# --------------------------------------------------------------------------- eligible list
with tab_elig:
    counts = {k: sum(1 for x in sess.eligible.values() if x["category"] == k) for k in CAT_COLORS}
    labels = [f"All ({len(sess.eligible)})"] + [f"{k} ({n})" for k, n in counts.items() if n]
    e1, e2 = st.columns([3, 2])
    pickcat = e1.pills("Show", labels, default=labels[0], key="elig_cat")
    search = e2.text_input("Search", placeholder="code, title or instructor", label_visibility="collapsed")
    catf = None if not pickcat or pickcat.startswith("All") else pickcat.split()[0]
    rows = [{"Code": x["code"], "Title": x["title"], "Units": x["units"], "Fills": x["category"],
             "IC": (x["ic"] or "").title(), "Midsem": x["midsem"][0] or "-",
             "Notes": "; ".join(c["note"] for c in x["checks"] if c["note"])}
            for x in sess.eligible.values() if not catf or x["category"] == catf]
    if search:
        s = search.lower()
        rows = [r for r in rows if s in f"{r['Code']} {r['Title']} {r['IC']}".lower()]
    df = pd.DataFrame(rows)
    if len(df):
        styled = df.style.apply(lambda col: [f"color:{CAT_COLORS.get(v, GREY)[0]};"
                                             f"background-color:{CAT_TABLE_BG.get(v, '#121936')};font-weight:600"
                                             for v in col], subset=["Fills"])
        st.dataframe(styled, hide_index=True, width="stretch", height=380)
    else:
        st.info("No course matches that filter.")
    st.caption("Every course listed passed requirements, prerequisites, prior preparation, the 25-unit cap and "
               "clash checks.")

    st.markdown("#### Why can't I take …?")
    with st.container(border=True):
        pick = st.selectbox("Pick a course you can't take", ["-"] + sorted(sess.ineligible),
                            format_func=lambda c: "Choose a course..." if c == "-" else course_label(c))
        if pick != "-":
            x = sess.ineligible[pick]
            st.markdown(cat_badge(x["category"], f"Would fill {x['category']}") + badge(f"{x['units']} units"),
                        unsafe_allow_html=True)
            for c in x["checks"]:
                if not c["ok"]:
                    st.markdown(f"<div class='prop no'><b>✗ {esc(c['clause'])}</b><br>{esc(c['note'])}</div>",
                                unsafe_allow_html=True)
            passed = [c for c in x["checks"] if c["ok"]]
            if passed:
                st.markdown("".join(f"<div class='prop yes'><b>✓ {esc(c['clause'])}</b>"
                                    + (f"<br><span class='muted'>{esc(c['note'])}</span>" if c["note"] else "")
                                    + "</div>" for c in passed), unsafe_allow_html=True)
            hs = handout_summary(cat, pick)
            st.caption(hs.get("message") or f"Handout: {hs['file']}")


# --------------------------------------------------------------------------- data
with tab_data:
    st.markdown("<div class='muted'>Everything in this app is computed live from <code>data/processed/academic.db</code>, "
                "built from the supplied Bulletin, Academic Regulations, timetable and handouts by "
                "<code>python -m ingest.run_all</code>.</div>", unsafe_allow_html=True)
    st.write("")
    qpath = ROOT / "data" / "processed" / "verification_queue.csv"
    vq = pd.read_csv(qpath) if qpath.exists() else pd.DataFrame()
    dtiles = [("Courses", len(cat.courses), "from the Bulletin", "#60a5fa"),
              ("Offered now", len(cat.offerings), "course codes", "#2dd4bf"),
              ("Handouts", len({h['file'] for h in cat.handouts.values()}) if isinstance(cat.handouts, dict)
               else len(cat.handouts), "unique files", "#fbbf24"),
              ("Programmes", len(cat.programmes), f"+ {len(cat.dual_charts)} dual charts", "#a78bfa"),
              ("Flagged", len(vq), "never guessed", "#f87171")]
    for col, t in zip(st.columns(len(dtiles)), dtiles):
        col.markdown(stat_tile(*t), unsafe_allow_html=True)
    st.write("")
    if len(vq):
        st.markdown("#### Verification queue")
        kinds = sorted(vq["kind"].unique())
        kf = st.pills("Kind", ["all"] + kinds, default="all", key="vq_kind")
        st.dataframe(vq if kf in (None, "all") else vq[vq["kind"] == kf], hide_index=True, width="stretch",
                     height=280)
    st.markdown("#### Regulation clauses used by the engine")
    st.dataframe(pd.DataFrame([{"Clause": r["clause"], "Rule": r["text"]} for r in cat.rules.values()]),
                 hide_index=True, width="stretch")
    rep = ROOT / "data" / "processed" / "validation_report.md"
    if rep.exists():
        with st.expander("Full validation report", icon=":material/description:"):
            st.markdown(rep.read_text())
