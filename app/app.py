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
from ingest.timetable import HOUR_TIMES                               # noqa: E402
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
CAT_COLORS = {"CDC": ("#93c5fd", "rgba(59,130,246,.20)"), "GIR": ("#cbd5e1", "rgba(148,163,184,.18)"),
              "DEL": ("#c4b5fd", "rgba(139,92,246,.24)"), "HUEL": ("#fcd34d", "rgba(245,158,11,.18)"),
              "OPEL": ("#5eead4", "rgba(20,184,166,.18)")}
# solid versions for the table (the data grid doesn't blend transparent colours)
CAT_TABLE_BG = {"CDC": "#16244a", "GIR": "#232a3d", "DEL": "#251d4d", "HUEL": "#33290f", "OPEL": "#0f3431"}
CAT_NAMES = {"CDC": "Core (CDC)", "GIR": "General (GIR)", "DEL": "Discipline elective (DEL)",
             "HUEL": "Humanities elective (HUEL)", "OPEL": "Open elective (OPEL)"}
DONE = ("#86efac", "rgba(34,197,94,.16)")
NOW = ("#a5b4fc", "rgba(99,102,241,.22)")
LEFT = ("#fca5a5", "rgba(239,68,68,.16)")
INFO = ("#c7d2fe", "rgba(129,140,248,.20)")
GREY = ("#cbd5e1", "rgba(148,163,184,.16)")
# fills for courses in the week grid
COURSE_FILLS = ["rgba(59,130,246,.35)", "rgba(139,92,246,.38)", "rgba(20,184,166,.35)", "rgba(245,158,11,.32)",
                "rgba(236,72,153,.32)", "rgba(14,165,233,.35)", "rgba(34,197,94,.32)", "rgba(249,115,22,.32)"]
REG_FILL = "rgba(148,163,184,.22)"

QUICK_QUESTIONS = [
    ("🤖 AI-related DELs", "Suggest DELs related to AI."),
    ("🙋 OPEL, no attendance rule", "I want an OPEL with no attendance requirement."),
    ("📝 No midsem, easy makeup", "Suggest courses with no midsem and a lenient makeup policy."),
    ("📊 Project-based HUEL", "I need a HUEL and prefer project-based evaluation."),
    ("💹 Finance / economics OPEL", "I like finance and economics, any OPEL?"),
    ("🎯 What's left for me?", "What are my remaining requirements?"),
]

st.set_page_config(page_title="BITS Course Recommender", page_icon="🎓", layout="wide",
                   initial_sidebar_state="expanded")
cat = get_catalog()
PROG_NAMES = {pid: f"{p['name']} ({pid})" for pid, p in sorted(cat.programmes.items())}

# --------------------------------------------------------------------------- styles
st.markdown("""
<style>
/* background: a faint nebula over deep navy */
[data-testid="stAppViewContainer"] {
  background: radial-gradient(1200px 600px at 85% -10%, rgba(139,92,246,.18), transparent 60%),
              radial-gradient(900px 500px at -10% 10%, rgba(34,211,238,.10), transparent 60%), #070b1a;}
[data-testid="stHeader"] {background: transparent;}
[data-testid="stSidebar"] {background: linear-gradient(180deg, #0c1230 0%, #080c1f 100%);
                           border-right: 1px solid #1f2850;}
.block-container {padding-top: 1.4rem; padding-bottom: 3rem;}
.hero {position: relative; overflow: hidden; color: #fff; padding: 22px 26px; border-radius: 18px; margin-bottom: 14px;
       border: 1px solid rgba(167,139,250,.35);
       background:
         radial-gradient(1.2px 1.2px at 12% 30%, #fff 60%, transparent), radial-gradient(1px 1px at 32% 70%, #fff 60%, transparent),
         radial-gradient(1.4px 1.4px at 58% 22%, #fff 60%, transparent), radial-gradient(1px 1px at 76% 64%, #fff 60%, transparent),
         radial-gradient(1.2px 1.2px at 90% 28%, #fff 60%, transparent), radial-gradient(1px 1px at 46% 84%, #dbeafe 60%, transparent),
         radial-gradient(420px 220px at 18% 0%, rgba(139,92,246,.75), transparent 70%),
         radial-gradient(380px 200px at 92% 110%, rgba(34,211,238,.45), transparent 70%),
         linear-gradient(120deg, #0d1240 0%, #1f1060 55%, #0b2a4a 100%);}
.hero h1 {color: #fff; margin: 0 0 4px 0; font-size: 1.8rem; line-height: 1.2; letter-spacing: .01em;}
.hero .sub {color: #dfe3ff; font-size: .95rem; margin-bottom: 10px;}
.chip {display: inline-block; padding: 3px 11px; border-radius: 999px; background: rgba(255,255,255,.12);
       border: 1px solid rgba(255,255,255,.18); margin: 0 6px 4px 0; font-size: .8rem; font-weight: 600; color: #fff;}
.chip.mode {background: rgba(34,211,238,.18); border-color: rgba(34,211,238,.5); color: #a5f3fc;}
.badge {display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: .76rem; font-weight: 650;
        margin: 0 4px 5px 0; white-space: nowrap; border: 1px solid rgba(255,255,255,.06);}
.badge.or {border: 1px dashed currentColor;}
.stat {border-radius: 14px; padding: 12px 14px; background: rgba(18,25,54,.85); border: 1px solid #222b55;
       border-top: 3px solid var(--c); min-height: 104px; box-shadow: 0 0 24px -12px var(--c);}
.stat .v {font-size: 1.6rem; font-weight: 750; color: #f5f6ff; line-height: 1.2;}
.stat .l {font-size: .72rem; color: #9aa3cf; text-transform: uppercase; letter-spacing: .06em; font-weight: 650;}
.stat .s {font-size: .78rem; color: #9aa3cf;}
.bar {height: 9px; background: #1b2347; border-radius: 99px; overflow: hidden; display: flex; margin: 8px 0 6px 0;}
.bar > div {height: 100%;}
.muted {color: #9aa3cf; font-size: .84rem;}
.rowlabel {color: #9aa3cf; font-size: .76rem; text-transform: uppercase; letter-spacing: .05em; font-weight: 650;
           margin: 8px 0 3px 0;}
.ctitle {font-size: 1.06rem; font-weight: 700; color: #f1f3ff; margin-bottom: 5px;}
.ctitle .code {color: #a78bfa;}
.prop {padding: 7px 11px; border-radius: 9px; margin: 5px 0; font-size: .88rem; line-height: 1.45; color: #e6e8ff;}
.prop.yes {background: rgba(34,197,94,.10); border-left: 4px solid #22c55e;}
.prop.no {background: rgba(239,68,68,.10); border-left: 4px solid #ef4444;}
.prop.unk {background: rgba(234,179,8,.10); border-left: 4px solid #eab308;}
.banner {padding: 10px 14px; border-radius: 11px; font-weight: 600; margin: 8px 0;}
.banner.ok {background: rgba(34,197,94,.12); color: #86efac; border: 1px solid rgba(34,197,94,.35);}
.banner.bad {background: rgba(239,68,68,.12); color: #fca5a5; border: 1px solid rgba(239,68,68,.35);}
.banner.info {background: rgba(129,140,248,.12); color: #c7d2fe; border: 1px solid rgba(129,140,248,.35);}
.step {font-weight: 750; color: #a78bfa; font-size: .95rem; margin: 14px 0 2px 0;}
.checkrow {padding: 8px 12px; border-radius: 9px; margin: 5px 0; font-size: .9rem; display: flex; gap: 10px; color: #e6e8ff;}
.checkrow.met {background: rgba(34,197,94,.10);} .checkrow.unmet {background: rgba(239,68,68,.08);}
.checkrow.na {background: rgba(148,163,184,.10);}
.checkrow .d {color: #9aa3cf; margin-left: auto; font-size: .82rem;}
table.week {width: 100%; border-collapse: separate; border-spacing: 3px; font-size: .78rem; color: #eef0ff;}
table.week th {background: #161e42; padding: 7px; border-radius: 7px; color: #c7cdf5;}
table.week td {padding: 6px; border-radius: 7px; vertical-align: top; background: rgba(22,30,66,.45); height: 30px;}
table.week td.time {background: none; color: #9aa3cf; white-space: nowrap; font-size: .74rem;}
table.week td.clash {background: rgba(239,68,68,.55) !important; font-weight: 700;}
.legend span {display: inline-block; padding: 3px 9px; border-radius: 7px; margin: 0 6px 5px 0; font-size: .78rem; color: #eef0ff;}
div[data-testid="stVerticalBlockBorderWrapper"] {background: rgba(16,22,50,.55); border-radius: 14px;}
div[data-testid="stSidebar"] .stButton button {border-radius: 10px;}
.stTabs [data-baseweb="tab"] {font-weight: 600;}
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
    st.markdown("## 🎓 Your profile")
    st.caption("Fill the 4 steps top to bottom. Everything updates live.")

    tests = sorted(TEST_PROFILES.glob("*.json"))
    saved = sorted(PROFILE_DIR.glob("*.json"))
    options = {"-": None}
    options.update({f"🧪 {profile_title(p)}": p for p in tests})
    options.update({f"💾 {p.stem}": p for p in saved})
    choice = st.selectbox("Quick start: load a sample or saved student", list(options))
    c1, c2 = st.columns(2)
    if c1.button("📂 Load", width="stretch", disabled=choice == "-"):
        set_profile(json.loads(options[choice].read_text()))
        st.rerun()
    if c2.button("✨ New", width="stretch", help="Start an empty profile"):
        set_profile(blank_profile())
        st.rerun()

    d = st.session_state.profile

    # ---- step 1
    st.markdown("<div class='step'>① Who are you</div>", unsafe_allow_html=True)
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
    with st.expander("✏️ Edit details"):
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
    st.markdown("<div class='step'>② Courses you've done</div>", unsafe_allow_html=True)
    if st.button("⚡ Pre-fill from my semester chart", width="stretch", type="primary",
                 help="Adds the courses a student in your year has normally done, and this semester's courses"):
        prefill(d)
        st.rerun()
    n_done = len(d.get("completed") or [])
    with st.expander(f"📋 Completed courses ({n_done})", expanded=n_done == 0):
        st.caption("Add or remove rows. Grade is optional: NC, W, I and RC count as not cleared.")
        comp_df = pd.DataFrame(d.get("completed") or [], columns=["code", "grade"]).fillna("")
        comp_df = st.data_editor(comp_df, num_rows="dynamic", width="stretch", hide_index=True,
                                 column_config={"grade": st.column_config.SelectboxColumn(options=GRADES),
                                                "code": st.column_config.TextColumn(help="e.g. CS F213")},
                                 key=f"comp_{d.get('id_no')}_{n_done}")
        d["completed"] = [{"code": r["code"], "grade": r["grade"] or None}
                          for r in comp_df.to_dict("records") if r.get("code")]

    # ---- step 3
    st.markdown("<div class='step'>③ This semester</div>", unsafe_allow_html=True)
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
        with st.expander(f"🗂️ My sections ({n_known}/{len(multi)} set, optional)"):
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
    st.markdown("<div class='step'>④ Extras (optional)</div>", unsafe_allow_html=True)
    minors = ["(none)"] + sorted(cat.minors_by_name)
    d["minor"] = st.selectbox("Minor", minors, index=minors.index(d["minor"]) if d.get("minor") in minors else 0)
    d["minor"] = None if d["minor"] == "(none)" else d["minor"]
    d["interests"] = st.text_input("Interests", d.get("interests") or "",
                                   placeholder="e.g. machine learning, finance",
                                   help="Used to rank suggestions when a question has no topic")
    cg = st.number_input("CGPA", 0.0, 10.0, float(d.get("cgpa") or 0.0), 0.01)
    d["cgpa"] = cg or None

    st.divider()
    with st.expander("🤖 AI mode (optional)"):
        st.caption("Without a key the assistant runs rule-based, and every feature works. Gemini and Groq "
                   "have free keys. The key stays in this browser session only.")
        prov = st.selectbox("Provider", ["auto (.env)", "gemini", "groq", "anthropic", "openai"],
                            help="openai = any OpenAI-compatible server; set the base URL below")
        ui_key = st.text_input("API key", type="password")
        ui_model = st.text_input("Model (blank = default)", "")
        ui_base = st.text_input("Base URL (only for 'openai')", "") if prov == "openai" else ""
    c1, c2 = st.columns(2)
    if c1.button("💾 Save", width="stretch"):
        name = (d.get("id_no") or d.get("name") or "profile").replace(" ", "_")
        (PROFILE_DIR / f"{name}.json").write_text(json.dumps(d, indent=1))
        st.toast(f"Saved as data/profiles/{name}.json", icon="💾")
    if c2.button("🧹 Clear chat", width="stretch"):
        st.session_state.chat = []
        st.rerun()

profile = Profile.from_dict(json.loads(json.dumps(st.session_state.profile)))
if not profile.programmes:
    st.markdown("<div class='hero'><h1>🎓 BITS Course Recommender</h1><div class='sub'>Type your BITS ID in the "
                "sidebar (step ①) to start, or load a sample student.</div></div>", unsafe_allow_html=True)
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
mode_chip = f"🤖 AI mode: {rec.label}" if rec.mode == "llm" else "⚙️ Rule-based mode (no API key)"
st.markdown(
    f"<div class='hero'><h1>🎓 BITS Course Recommender</h1>"
    f"<div class='sub'>{esc(profile.name or profile.id_no or 'Student')} · {esc(degrees)}</div>"
    f"<span class='chip'>{esc(profile.id_no or 'no ID')}</span>"
    f"<span class='chip'>Year {profile.year} · First Semester 2026-27</span>"
    f"<span class='chip'>{esc(profile.stream or 'PS')}</span>"
    + (f"<span class='chip'>Minor: {esc(profile.minor)}</span>" if profile.minor else "")
    + f"<span class='chip mode'>{esc(mode_chip)}</span></div>", unsafe_allow_html=True)

units = req["registered_units"]
cdc_left = sum(len(p["cdc_remaining"]) for p in req["programmes"])
del_left = sum(p["del_remaining_courses"] or 0 for p in req["programmes"])
opel_left = req["opel"]["remaining_courses"] if req["opel"]["required_courses"] else None
tiles = [
    ("Units", f"{units} / 25", f"{max(0, 25 - units)} units of room left",
     "#22c55e" if units <= 20 else ("#eab308" if units < 25 else "#ef4444")),
    ("Core left", cdc_left, "CDCs still to clear", CAT_COLORS["CDC"][0]),
    ("DELs left", del_left, "discipline electives", CAT_COLORS["DEL"][0]),
    ("HUELs left", req["huel"]["remaining_courses"], "humanities electives", CAT_COLORS["HUEL"][0]),
    ("OPELs left", "—" if opel_left is None else opel_left,
     "not required (dual degree)" if opel_left is None else "open electives", CAT_COLORS["OPEL"][0]),
    ("Open to you", len(sess.eligible), "courses open now", "#8b7bff"),
]
for col, t in zip(st.columns(len(tiles)), tiles):
    col.markdown(stat_tile(*t), unsafe_allow_html=True)
st.write("")
for n in req["notes"]:
    st.info(n, icon="ℹ️")

tab_req, tab_ask, tab_plan, tab_elig, tab_data = st.tabs(
    ["🏠 Overview", "💬 Ask", "🗓️ Plan semester", "✅ Eligible courses", "📚 Data sources"])


# --------------------------------------------------------------------------- overview
def bucket_card(title, key, summary, required, remaining=None, note=""):
    """one requirement bucket: progress bar, then Done / This semester / Still to do as separate labelled rows"""
    done, doing = summary.get("done") or [], summary.get("in_progress") or []
    color = CAT_COLORS[key][0]
    with st.container(border=True):
        parts = [cat_badge(key, title)]
        if required:
            left = max(0, required - len(done) - len(doing))
            parts.append(progress_bar(len(done), len(doing), required, color)
                         + f"<div class='muted'><b>{len(done)}</b> done · <b>{len(doing)}</b> this semester · "
                           f"<b>{left}</b> left · {required} needed</div>")

        def row(label, chips):
            return f"<div class='rowlabel'>{label}</div>" + " ".join(chips)
        if done:
            parts.append(row("✅ Done", [badge(c, *DONE) for c in done]))
        if doing:
            parts.append(row("🔄 This semester", [badge(c, *NOW) for c in doing]))
        if remaining:
            parts.append(row("⏳ Still to do", [slot_badge(g) for g in remaining]))
        elif remaining is not None and required:
            parts.append("<div class='rowlabel'>✅ Nothing left here</div>")
        if note:
            parts.append(f"<div class='muted' style='margin-top:6px'>{esc(note)}</div>")
        st.markdown("".join(parts), unsafe_allow_html=True)


with tab_req:
    with st.expander("❓ How to use this app", expanded=not st.session_state.chat):
        st.markdown(
            "1. **Set up your profile** in the sidebar: type your BITS ID, press **⚡ Pre-fill**, then fix anything "
            "that's different for you.\n"
            "2. **Overview** (this tab) shows what you still need to graduate.\n"
            "3. **💬 Ask** anything in plain English, or tap a ready-made question.\n"
            "4. **🗓️ Plan semester**: your timetable, with your core courses filled in and clash-free sections.\n"
            "5. **✅ Eligible courses**: everything you can take, and *why not* for the rest.\n\n"
            + " ".join(cat_badge(k, CAT_NAMES[k]) for k in CAT_COLORS)
            + "<br>" + badge("✅ done", *DONE) + badge("🔄 this semester", *NOW) + badge("⏳ still to do", *LEFT)
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
            st.markdown(f"#### 🎯 Minor: {m['name']}")
            if m.get("error"):
                st.warning(m["error"])
            else:
                st.markdown(progress_bar(m["courses_counted"], 0, m["required_courses"] or 1, "#f472b6")
                            + f"<div class='muted'>{m['courses_counted']} of {m['required_courses']} courses · "
                              f"{m['units_counted']} of {m['required_units']} units</div>", unsafe_allow_html=True)
                if m["core_remaining"]:
                    st.markdown("<div class='rowlabel'>⏳ Core still to do</div>"
                                + " ".join(slot_badge(g) for g in m["core_remaining"]), unsafe_allow_html=True)
                if not m["overlap_ok"]:
                    st.warning("More than 2 courses / 6 units overlap with your mandatory courses (bulletin IV-129).")
                st.caption(m["rules"]["gpa"] + " · " + m["source"])

    st.markdown("#### 🎓 Graduation checklist")
    g = req["graduation"]
    rows = []
    for i in g["items"]:
        cls, icon = ("met", "✅") if i["met"] else (("na", "➖") if i["met"] is None else ("unmet", "⏳"))
        rows.append(f"<div class='checkrow {cls}'><span>{icon}</span><span>{esc(i['requirement'])}</span>"
                    f"<span class='d'>{esc(i['detail'])}</span></div>")
    st.markdown("".join(rows), unsafe_allow_html=True)
    st.caption(g["source"])
    if req["not_cleared"]:
        st.warning("Not cleared (NC / W / I ...): " + ", ".join(req["not_cleared"]))


# --------------------------------------------------------------------------- course cards
PROP_CLASS = {True: ("yes", "✅ Yes"), False: ("no", "❌ No"), None: ("unk", "❔ Could not verify")}


def render_card(r):
    shown = r.get("shown_as") or r["fills"]
    with st.container(border=True):
        head = (f"<div class='ctitle'><span class='code'>{esc(r['code'])}</span> · {esc(r['title'])}</div>"
                + cat_badge(shown, f"Fills {shown}")
                + badge(f"{r['units']} units")
                + (badge("also counts as " + "/".join(c for c in r["can_count_as"] if c != shown), *GREY)
                   if len(r["can_count_as"]) > 1 else "")
                + (badge(f"IC: {r['ic']}", *GREY) if r.get("ic") else ""))
        st.markdown(head, unsafe_allow_html=True)
        if r.get("agent_reason"):
            st.markdown(f"💡 _{r['agent_reason']}_")
        why = r["why_category"] if shown == r["fills"] else \
            f"you asked for {shown}; it's in your {r['fills']} pool, so it can be filed as either (reg 2.05)"
        st.markdown(f"<div class='muted'>📌 <b>Why it counts:</b> {esc(why)}</div>", unsafe_allow_html=True)
        props = r.get("properties") or {}
        if props:
            st.markdown("".join(
                f"<div class='prop {PROP_CLASS[pr['value']][0]}'><b>{esc(PROP_LABELS.get(n, n))}: "
                f"{PROP_CLASS[pr['value']][1]}</b><br><span class='muted'>{esc(pr.get('evidence'))}</span></div>"
                for n, pr in props.items()), unsafe_allow_html=True)
        meta = [f"📝 Midsem {r['midsem']}" if r["midsem"] else "📝 No midsem slot",
                f"🧾 Compre {r['compre']}" if r["compre"] else None,
                "🕒 Sections " + ", ".join(r["sections"].values()) if r["sections"] else None]
        line = " &nbsp;·&nbsp; ".join(esc(m) for m in meta if m)
        if r.get("match_terms"):
            line += " &nbsp;·&nbsp; 🔎 matched " + " ".join(badge(t.replace("_", " "), *INFO)
                                                          for t in r["match_terms"][:5])
        st.markdown(f"<div class='muted'>{line}</div>", unsafe_allow_html=True)
        with st.expander("Eligibility checks & sources"):
            for e in r["eligibility"]:
                st.markdown(f"✅ {e}")
            src = r["sources"]
            st.caption(f"Timetable p.{(src['timetable'] or {}).get('page')}"
                       + (f" · Bulletin p.{src['bulletin']['page']}" if src.get("bulletin") else "")
                       + (f" · Handout: {src['handout']}" if src.get("handout") else " · no handout supplied"))


def render_cards(recs):
    for r in recs:
        render_card(r)


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
    extra = []
    if out["mode"] == "llm" and out.get("could_not_verify"):
        extra.append("Could not verify for: " + ", ".join(c["code"] for c in out["could_not_verify"]))
    if out.get("rejected_by_validation"):
        extra.append("Dropped by policy validation: " +
                     ", ".join(f"{r['code']} ({'; '.join(r['reasons'])})" for r in out["rejected_by_validation"]))
    st.session_state.chat.append({"role": "assistant", "content": text, "cards": out["recommendations"],
                                  "footer": footer, "extra": extra})


def show_turn(turn):
    with st.chat_message(turn["role"], avatar="🧑‍🎓" if turn["role"] == "user" else "🎓"):
        st.markdown(turn["content"])
        if turn.get("cards"):
            render_cards(turn["cards"])
        if turn.get("footer"):
            st.markdown(turn["footer"])
        for x in turn.get("extra") or []:
            st.caption(x)


with tab_ask:
    mode = st.segmented_control("How do you want to search?", ["💬 Chat", "🎛️ Guided search"],
                                default="💬 Chat", key="ask_mode")
    if mode == "🎛️ Guided search":
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
            go = st.button("🔍 Find courses", type="primary")
        if go:
            cats = [] if gcat in (None, "Any") else [gcat]
            res = sess.find_courses(categories=cats, topics=gtopic or None, require=gprops or [],
                                    no_8am=gno8, free_day=None if gfree == "-" else gfree, limit=8)
            if cats:
                for r in res["results"]:
                    if cats[0] in r["can_count_as"]:
                        r["shown_as"] = cats[0]
            n = len(res["results"])
            st.markdown(f"<div class='banner {'ok' if n else 'bad'}'>{n} course(s) match everything you picked"
                        + (f" · {res['total_matches']} in total" if res['total_matches'] > n else "") + "</div>",
                        unsafe_allow_html=True)
            render_cards(res["results"])
            if res["could_not_verify"]:
                with st.container(border=True):
                    st.markdown("**❔ Might fit, but the handout doesn't say**")
                    for c in res["could_not_verify"]:
                        st.markdown(f"- **{c['code']}** {c['title']}: "
                                    + "; ".join(f"{PROP_LABELS.get(k, k)}: {v}" for k, v in c["why"].items()))
            if gtopic:
                blocked = sess.blocked_matches(gtopic, cats or None)
                if blocked:
                    with st.container(border=True):
                        st.markdown("**🔒 Good matches you can't take this semester**")
                        for b in blocked:
                            st.markdown(f"- **{b['code']}** {b['title']}: " + "; ".join(b["blocked_by"]))
            if not n and gprops:
                near = sess.near_misses(cats or None, gtopic or None, gprops)
                if near:
                    st.markdown("**Closest options** (check the ❌ / ❔ lines):")
                    render_cards(near)
    else:
        st.markdown("<div class='muted'>Tap a question or type your own below.</div>", unsafe_allow_html=True)
        qcols = st.columns(3)
        clicked = None
        for i, (label, question) in enumerate(QUICK_QUESTIONS):
            if qcols[i % 3].button(label, width="stretch", key=f"qq_{i}"):
                clicked = question
        typed = st.chat_input("Ask about courses for this semester")
        q = typed or clicked
        if q:
            answer(q)
        if not st.session_state.chat:
            st.markdown("<div class='banner info'>💡 Try: <i>can I take CS F317 and GS F232 together with no gaps?</i> "
                        "or <i>prerequisites of CS F425</i></div>", unsafe_allow_html=True)
        chat = st.session_state.chat
        pairs = [chat[i:i + 2] for i in range(0, len(chat), 2)]
        for n, pair in enumerate(reversed(pairs)):
            if n == 1:
                st.markdown("<div class='muted'>Earlier questions</div>", unsafe_allow_html=True)
            for turn in pair:
                show_turn(turn)


# --------------------------------------------------------------------------- plan
def week_grid(entries, registered):
    """entries: [(code, section, slots)] -> coloured html table, days x hours"""
    colours, i = {}, 0
    for code, _, _ in entries:
        if code not in colours:
            if code in registered:
                colours[code] = REG_FILL
            else:
                colours[code] = COURSE_FILLS[i % len(COURSE_FILLS)]
                i += 1
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
                cells.append(f"<td style='background:{colours[c]}'><b>{esc(c)}</b> {esc(s)}</td>")
            else:
                cells.append("<td></td>")
        rows.append(f"<tr><td class='time'>{esc(HOUR_LABELS.get(h, f'slot {h}'))}</td>{''.join(cells)}</tr>")
    legend = "".join(f"<span style='background:{col}'>{esc(c)}{' (registered)' if c in registered else ''}</span>"
                     for c, col in colours.items())
    return (f"<div class='legend'>{legend}<span style='background:rgba(239,68,68,.55)'>clash</span></div>"
            f"<table class='week'>{''.join(rows)}</table>")


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
    autofill = t1.toggle("📌 Auto-fill my core courses (CDC)", value=True,
                         help="Adds the CDC / GIR courses your chart puts in this semester (or earlier) that you "
                              "haven't registered yet. Turn off to plan only what you pick.")
    sched_reg = t2.toggle("🗓️ Pick sections for my registered courses too", value=True,
                          help="Chooses clash-free sections for everything you're registered in, keeping any "
                               "section you gave in the sidebar. Off = only the hours we know for sure are blocked.")
    picks = st.multiselect("➕ Courses to add this semester", sorted(sess.eligible),
                           default=core_due if autofill else [],
                           format_func=lambda c: f"{course_label(c)}  [{sess.eligible[c]['category']}]",
                           placeholder="Start typing a course code or title...",
                           key=f"plan_{profile.id_no}_{autofill}")
    if autofill:
        st.caption(("📌 Auto-filled: " + ", ".join(core_due)) if core_due else
                   "📌 No core course is left to add: your CDCs for this semester are already registered "
                   "(or not open to you yet). Add electives above.")

    with st.container(border=True):
        st.markdown("**⚙️ Preferences** <span class='muted'>(optional)</span>", unsafe_allow_html=True)
        p1, p2 = st.columns([1, 3])
        p1.markdown("Free day<br><span class='muted'>keep one day clear</span>", unsafe_allow_html=True)
        free_pick = p2.pills("Free day", ["None"] + [DAY_NAMES[d] for d in DAYS], default="None",
                             key="pref_free", label_visibility="collapsed")
        free = next((d for d in DAYS if DAY_NAMES[d] == free_pick), None)
        p1, p2 = st.columns([1, 3])
        p1.markdown("Earliest class<br><span class='muted'>skip 8 AM classes</span>", unsafe_allow_html=True)
        no8 = p2.toggle("No 8 AM classes", key="pref_no8")
        p1, p2 = st.columns([1, 3])
        p1.markdown("Compact<br><span class='muted'>fewest free hours between classes</span>", unsafe_allow_html=True)
        compact = p2.toggle("Compact timetable", key="pref_compact")

    plan_codes = list(picks) + (list(profile.current) if sched_reg else [])
    # sections the student would accept, from the per-course pickers below (kept in session state)
    allowed = {}
    for code in plan_codes:
        for kind in ("lecture", "tutorial", "practical"):
            v = st.session_state.get(f"allow_{code}_{kind}")
            if v:
                allowed.setdefault(code, {})[kind] = list(v)

    if not picks and not (sched_reg and profile.current):
        st.markdown("<div class='banner info'>👆 Add one or more courses to see how they fit.</div>",
                    unsafe_allow_html=True)
    else:
        out = sess.check_plan(picks, no8, free, compact, sched_reg, allowed)
        problems = []
        if out["total_units"] > 25:
            problems.append(f"over the 25-unit cap by {out['total_units'] - 25}")
        if not out["clash_free"]:
            problems.append("; ".join(out["clash_problems"]))
        if out["rejected"]:
            problems.append(f"{len(out['rejected'])} course(s) not allowed")
        ok = not problems

        # same-day exams (different sessions - same session would already be a clash)
        exam_rows = []
        for code in [p["code"] for p in out["picks"]] + [r["code"] for r in out.get("registered", [])]:
            o = cat.offerings[code][0]
            for kind, dkey, skey in (("Midsem", "midsem_date", "midsem_session"), ("Compre", "compre_date", "compre_session")):
                if o.get(dkey):
                    exam_rows.append({"date": o[dkey], "session": o[skey], "exam": kind, "code": code})
        same_day = {}
        for r in exam_rows:
            same_day.setdefault((r["exam"], r["date"]), []).append(r["code"])
        same_day_codes = {c for v in same_day.values() if len(v) > 1 for c in v}

        n_courses = len(out["picks"]) + len(out.get("registered", []))
        sc = st.columns(4)
        sc[0].markdown(stat_tile("Courses", n_courses, f"{len(out['picks'])} added", "#8b7bff"), unsafe_allow_html=True)
        sc[1].markdown(stat_tile("Units", f"{out['total_units']} / 25", "cap per semester",
                                 "#22c55e" if out["total_units"] <= 25 else "#ef4444"), unsafe_allow_html=True)
        sc[2].markdown(stat_tile("Timetable", "Clash-free" if out["clash_free"] else "Clash",
                                 "all sections fit" if out["clash_free"] else "see below",
                                 "#22c55e" if out["clash_free"] else "#ef4444"), unsafe_allow_html=True)
        sc[3].markdown(stat_tile("Same-day exams", len([k for k, v in same_day.items() if len(v) > 1]),
                                 "days with 2+ exams", "#eab308" if same_day_codes else "#22c55e"),
                       unsafe_allow_html=True)
        msg = (f"✅ This plan works · {out['total_units']} / 25 units · clash-free" if ok else
               f"⚠️ {out['total_units']} / 25 units · " + " · ".join(problems))
        st.markdown(f"<div class='banner {'ok' if ok else 'bad'}'>{esc(msg)}</div>", unsafe_allow_html=True)

        unl = unlocks_map()

        def plan_card(col, code, title, units_, tag_html, chosen):
            o = cat.offerings[code][0]
            by = {}
            for s_ in o["sections"]:
                by.setdefault(s_["type"], []).append(s_)
            counts = " · ".join(f"{len(v)} {TYPE_SHORT.get(k, k[:1].upper())}" for k, v in by.items())
            restricted = allowed.get(code)
            pre = (cat.courses.get(code) or {}).get("prerequisite_codes") or []
            opens = unl.get(cat.canon(code), [])[:3]
            with col.container(border=True):
                st.markdown(
                    f"<div class='ctitle'><span class='code'>{esc(code)}</span></div>"
                    f"<div class='muted' style='margin-bottom:6px'>{esc(title)}</div>"
                    + tag_html + badge(f"{units_} units")
                    + (badge("⚠️ Same-day exam", "#fcd34d", "rgba(234,179,8,.18)") if code in same_day_codes else "")
                    + (badge("📄 Handout", *GREY) if cat.handout(code) else badge("no handout", *GREY))
                    + f"<div class='muted' style='margin-top:4px'>{esc(counts)} · "
                    + ("<b>your picks only</b>" if restricted else "all allowed") + "</div>"
                    + (f"<div class='muted'>needs {esc(', '.join(pre))}</div>" if pre else "")
                    + (f"<div class='muted'>unlocks {esc(', '.join(opens))}</div>" if opens else "")
                    + "<div style='margin-top:6px'>" + "".join(badge(f"{k}: {v}", *INFO) for k, v in chosen.items())
                    + "</div>", unsafe_allow_html=True)
                with st.popover("🎛️ Choose sections", width="stretch"):
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

        if out["picks"]:
            st.markdown("#### ➕ Adding")
            cols = st.columns(3)
            for i, p in enumerate(out["picks"]):
                plan_card(cols[i % 3], p["code"], p["title"], p["units"],
                          cat_badge(p["filed_as"], f"Filed as {p['filed_as']}"), p["sections"])
        if out.get("registered"):
            st.markdown("#### 📚 Already registered")
            cols = st.columns(3)
            for i, r in enumerate(out["registered"]):
                given = profile.current_sections.get(r["code"], {})
                plan_card(cols[i % 3], r["code"], r["title"], r["units"],
                          badge("your section" if given else "registered", *GREY), r["sections"])
        for r in out["rejected"]:
            st.error(f"**{r['code']}** can't be added: {'; '.join(r['reasons'])}", icon="🚫")
        for w in out["warnings"]:
            if "allowed per semester" not in w:      # the unit cap is already in the banner above
                st.warning(w, icon="⚠️")
        if out.get("gap_hours") is not None:
            st.caption(f"Compact pick: {out['gap_hours']} idle hours between classes across {out['days_used']} days.")

        # week view entries
        entries = []
        if sched_reg:
            for r in out.get("registered", []):
                o = cat.offerings[r["code"]][0]
                for s_ in o["sections"]:
                    if r["sections"].get(s_["type"]) == s_["section"]:
                        entries.append((r["code"], s_["section"], s_["slots"]))
        else:
            for code in profile.current:
                for o in cat.offerings.get(code, [])[:1]:
                    by = {}
                    for s_ in o["sections"]:
                        by.setdefault(s_["type"], []).append(s_)
                    for kind, secs in by.items():
                        mine = profile.current_sections.get(code, {}).get(kind)
                        pick = [x for x in secs if x["section"] == mine] or (secs if len(secs) == 1 else [])
                        if pick:
                            entries.append((code, pick[0]["section"], pick[0]["slots"]))
        for p in out["picks"]:
            o = cat.offerings[p["code"]][0]
            for s_ in o["sections"]:
                if p["sections"].get(s_["type"]) == s_["section"]:
                    entries.append((p["code"], s_["section"], s_["slots"]))

        w1, w2, w3 = st.tabs(["🗓️ Week", "📝 Exam calendar", "⬇️ Download"])
        with w1:
            st.markdown(week_grid(entries, set(profile.current)), unsafe_allow_html=True)
            st.caption("Grey = courses you're registered in. Colours = courses you're adding. Red = clash.")
        with w2:
            if exam_rows:
                ex = pd.DataFrame(sorted(exam_rows, key=lambda r: (r["date"], r["session"])))
                ex["title"] = ex["code"].map(lambda c: cat.title(c) or "")
                ex["note"] = [("⚠️ same day as " + ", ".join(x for x in same_day[(r.exam, r.date)] if x != r.code))
                              if len(same_day[(r.exam, r.date)]) > 1 else "" for r in ex.itertuples()]
                st.dataframe(ex[["date", "session", "exam", "code", "title", "note"]], hide_index=True,
                             width="stretch")
                st.caption("Sessions: FN1 09:00, FN2 11:00, AN1 14:00, AN2 16:00 (midsem) · FN 09:00, AN 14:00 (compre)")
            else:
                st.info("No exam slots listed for these courses.")
        with w3:
            rows_ = [{"code": c, "section": s_, "day": sl["day"], "start": HOUR_LABELS.get(sl["hour"], sl["hour"])}
                     for c, s_, slots in entries for sl in slots]
            st.download_button("⬇️ Timetable (CSV)", pd.DataFrame(rows_).to_csv(index=False),
                               file_name=f"timetable_{profile.id_no or 'plan'}.csv", mime="text/csv")
            st.download_button("⬇️ Exam calendar (CSV)", pd.DataFrame(exam_rows).to_csv(index=False),
                               file_name=f"exams_{profile.id_no or 'plan'}.csv", mime="text/csv")
            st.download_button("⬇️ Full plan (JSON)", json.dumps({k: v for k, v in out.items()
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

    st.markdown("#### 🔒 Why can't I take ...?")
    with st.container(border=True):
        pick = st.selectbox("Pick a course you can't take", ["-"] + sorted(sess.ineligible),
                            format_func=lambda c: "Choose a course..." if c == "-" else course_label(c))
        if pick != "-":
            x = sess.ineligible[pick]
            st.markdown(cat_badge(x["category"], f"Would fill {x['category']}") + badge(f"{x['units']} units"),
                        unsafe_allow_html=True)
            for c in x["checks"]:
                if not c["ok"]:
                    st.markdown(f"<div class='prop no'><b>❌ {esc(c['clause'])}</b><br>{esc(c['note'])}</div>",
                                unsafe_allow_html=True)
            passed = [c for c in x["checks"] if c["ok"]]
            if passed:
                st.markdown("".join(f"<div class='prop yes'><b>✅ {esc(c['clause'])}</b>"
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
        st.markdown("#### 🚩 Verification queue")
        kinds = sorted(vq["kind"].unique())
        kf = st.pills("Kind", ["all"] + kinds, default="all", key="vq_kind")
        st.dataframe(vq if kf in (None, "all") else vq[vq["kind"] == kf], hide_index=True, width="stretch",
                     height=280)
    st.markdown("#### ⚖️ Regulation clauses used by the engine")
    st.dataframe(pd.DataFrame([{"Clause": r["clause"], "Rule": r["text"]} for r in cat.rules.values()]),
                 hide_index=True, width="stretch")
    rep = ROOT / "data" / "processed" / "validation_report.md"
    if rep.exists():
        with st.expander("📄 Full validation report"):
            st.markdown(rep.read_text())
