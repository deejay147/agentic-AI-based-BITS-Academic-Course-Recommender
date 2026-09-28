# BITS Academic Course Recommender

Agentic course recommender for BITS Pilani students - Postman Round 2.

A student builds a profile (from their BITS ID), the app works out what they still need to graduate,
which courses in the **First Semester 2026-27** timetable they're actually allowed to take, and then answers
natural-language questions like *"Suggest DELs related to AI"* or *"I want an OPEL with no attendance requirement"*
from that eligible set only, with the reason for every pick and where each fact came from.

```
Student profile + query
        |
Academic requirement analysis        engine/requirements.py   (deterministic)
        |
Remaining GIR / CDC / DEL / HUEL / OPEL
        |
Eligible course set                  engine/eligibility.py    (every regulation check tagged with its clause)
        |
Course preference matching           agent/                   (Claude tool-calling, or rule-based without a key)
        |
BITS policy validation               agent/tools.py Session.validate  (LLM picks re-checked against the engine)
        |
Final recommendations                app/app.py               (Streamlit dashboard)
```

## Quick start

```bash
pip install -r requirements.txt
streamlit run app/app.py          # opens http://localhost:8501
```

That's it - **no API key needed**. The processed dataset is committed (`data/processed/`), so the app runs straight
away, and every feature works in the default rule-based agent mode: requirement analysis, eligibility, handout-property
filters, interest matching, explanations, planner and timetable checks.

Optional LLM mode: `cp .env.example .env` and set one key - `GEMINI_API_KEY` (free tier via Google AI Studio),
`GROQ_API_KEY` (free tier) or `ANTHROPIC_API_KEY` - or paste a key into the sidebar's *LLM (optional)* panel.
Any OpenAI-compatible server also works (`LLM_PROVIDER=openai`, `LLM_BASE_URL`, `LLM_MODEL`). The same tools and the
same policy validation are used; the LLM takes over query understanding, topic expansion / re-ranking and the wording
of explanations. If the LLM call fails (bad key, quota, network) the answer falls back to rule-based mode and says so.
The dashboard header shows which mode is active.

Try: load `test: cs_2nd_year` in the sidebar, open **Ask** and type *Suggest DELs related to AI*.

Tests: `pytest -q` (36 tests, engine + agent; both LLM loops are tested with scripted fake clients).

### Rebuilding the data from the PDFs

Put the supplied files in `data/raw/` (not committed, ~250 MB):

```
data/raw/bulletin.pdf
data/raw/timetable.pdf
data/raw/regulations.pdf          # Academic-Regulations-2023.pdf, renamed
data/raw/handouts/*.pdf
```

```bash
# needs poppler-utils (pdftotext); tesseract is optional (only for the one scanned handout)
python -m ingest.run_all          # ~3 min
```

A new timetable or a new set of handouts = drop the files in and rerun this. No engine/agent code changes.

## Dashboard

- **Sidebar - profile.** Type the BITS ID (`2025A7PS0147P` -> batch 2025, B.E. CS, PS, Pilani; `2024B3A70123P` ->
  dual degree M.Sc. Economics + B.E. CS; `..CS..` in the stream slot -> 2+2 CentraleSupelec). *Pre-fill* fills the
  named courses of the earlier semesters from the programme's semester chart; edit the list, set grades (NC / W / I
  count as not cleared), add electives already done, pick the courses registered this semester, minor, interests.
  Profiles save to `data/profiles/`. The 11 test profiles can be loaded from the same box.
- **Requirements** - remaining core / DEL / HUEL / OPEL / GIR, minor progress, graduation checklist.
- **Ask** - chat. Recommendation cards show the requirement filled, eligibility, requested properties
  (yes / no / could not be verified, with the handout or timetable quote), IC, exam slots, sections, and sources.
- **Plan semester** - pick electives; they're filed into CDC / DEL / HUEL / OPEL automatically, sections are chosen
  so nothing clashes (optionally no 8 AM / a free day), 25-unit cap checked, week view.
- **Eligible courses** - the full eligible set, and "why can't I take X?" with the clause that blocks it.
- **Data sources** - validation report, verification queue, regulation clauses in use.

## How it's built

### 1. Ingestion (`ingest/`) - PDFs -> structured records -> SQLite

| script | source | what comes out |
|---|---|---|
| `timetable.py` | timetable II, IX | 719 course rows / 582 codes, 1557 sections with day-hour slots, midsem + compre slots, IC, 2026-only flag; 167 equivalent-course groups |
| `bulletin_programmes.py` | bulletin IV | 28 programmes: CDC + DEL groups (OR-alternatives, tracks, compulsory DELs), GIR courses, semester positions of every named course, CDC/DEL totals from the charts; 70 composite dual-degree charts; HUEL pool (136); 23 minors |
| `bulletin_courses.py` | bulletin VI | 2015 course descriptions, units, stated prerequisites |
| `handouts.py` | 540 handout PDFs (399 unique) | evaluation components/weights, midsem / compre / quiz / project / lab / open-book flags, makeup + attendance policy, lecture-plan topics - each field keeps the text it came from |
| `regulations_rules.py` | Academic Regulations | the 14 clauses the engine uses, with clause numbers |
| `build_db.py` | all of the above | `academic.db`, `validation_report.md`, `verification_queue.csv` |

Every record carries its source (document + page / section / file). Things that don't check out go to the
verification queue instead of being guessed - e.g. offered courses without a handout, sections with no day/hour
in the timetable, programmes where the bulletin's CDC list and semester chart disagree (reconciled toward the chart,
reg 1.07, and logged).

The PDFs are parsed with coordinates (pdfplumber) rather than flattened text: the timetable by column x-positions,
the bulletin's two-column pages by cropping each column, the minors by table extraction. Handouts vary too much
between ICs for one template, so they're parsed with rules and every extracted property keeps its evidence.

### 2. Academic engine (`engine/`) - no LLM

- `profile.py` - profile + BITS ID parsing. The only timetable is First Semester 2026-27, so year = 2026 - batch + 1.
  Grades are optional; A-E count as cleared, NC / W / I / GA / RC don't (reg 4.11-4.12).
- `requirements.py` - remaining requirements. Electives are counted in courses: single degree 3 HUEL / 4 DEL / 5 OPEL,
  dual degree no OPEL (reg 2.05), and a programme's own DEL count from its chart wins (Economics 6, Biotech 5 ...).
  Old / cross-listed codes are matched through the equivalence list. Electives are filed DEL -> HUEL -> OPEL (reg 2.05;
  a HUEL can't be from the student's own discipline, bulletin IV-127). Graduation checklist per bulletin IV-1/IV-2.
- `eligibility.py` - for every offered course: which requirement it would fill for this student, and each rule:

| check | clause |
|---|---|
| prerequisites (only ~70 courses state any) | Reg 3.13 |
| prior preparation for own CDCs (named courses of earlier semesters; DCA may allow 2 missing) | Reg 3.14 |
| other degrees' CDC/DEL only after own year 1-2 named courses | Reg 3.15(b)(i) |
| higher degree courses: own discipline, after 2nd-year CDCs, one per semester, CGPA cutoff (not in data -> note) | Reg 3.15(b)(ii), 2.08 |
| 25 units per semester | Reg 1.01 |
| comcod >= 5000 / U-codes only for 2026 admits | Timetable note |
| first-year foundation courses aren't elective host regions | Reg 2.07 / 3.18 |
| no class or exam clash (tries every section combination) | Reg 3.19 |

- `schedule.py` - clash checks + joint section selection; `planner.py` - the semester planner.

**Timetable intelligence (brownie point):** class, tutorial, lab, midsem and compre clashes; if one section of a course
clashes, another section is tried before the course is rejected; preferences for no 8 AM classes, keeping a weekday
free, and a compact timetable (the section combination with the fewest idle hours, then the fewest days); week view in
the planner tab. Also reachable from chat, e.g. *can I take CS F317 and GS F232 together with no gaps?*
- Dual degree students are placed on the composite chart of their pair (bulletin p.242-313).

### 3. Agent (`agent/`)

- `tools.py` - `get_requirements`, `find_courses` (category / handout-property / time filters + topic ranking),
  `course_details`, `check_plan`, `submit_recommendations`.
- `retrieval.py` - BM25 with bigrams over title + bulletin description + handout lecture plan, and the handout
  property checks. A property is only "yes" if the handout/timetable says so; silent handouts give
  *"No specific information mentioned; contact the Instructor-in-Charge (name)"* and are listed as could-not-verify.
- `agent.py`
  - **LLM mode** (a key for Anthropic, Gemini, Groq or any OpenAI-compatible API): the model reads the request,
    calls the tools (turning "AI" into syllabus words, re-searching if thin), re-ranks, and must finish with
    `submit_recommendations`. Each pick is re-validated against the engine and the requested category / properties;
    anything invalid is dropped and reported. Two loops share everything else: Anthropic messages API and OpenAI
    chat-completions tool calling. If the API call fails, it falls back to rules mode.
  - **Rules mode** (default, no key) - `nlu.py` parses category, properties, time preferences, course codes and topic
    words; the same tools answer; explanations come from templates. When nothing matches every requested property it
    says so and shows the closest options with what they're missing. When the courses that best match a topic exist
    but are blocked (e.g. reg 3.15, an exam clash), it says so up front and lists the blocking rule for each.

## Scope decisions

- Programme rules come from the supplied Bulletin (2025-26). Earlier batches still get results, with a note that the
  2025-26 curriculum was applied. 2026 admits are treated the same way, and only they see the 2026-only (>= 5000
  comcod / U-code) timetable rows.
- Pilani campus only (the timetable and handouts are Pilani's).
- Prerequisites: when none is listed, the app says "No prerequisites required" if asked, and doesn't bring it up otherwise.
- Reg 3.15(b)(i) is applied strictly.
- 2+2 CentraleSupelec students get the progression condition from bulletin p.160 (CGPA >= 5.0, no grade below D);
  which BITS courses count for CSP isn't in the supplied data.
- PS-II / thesis appear in the graduation checklist as a reminder only.

## Known limitations

- Handout extraction is rule-based: 192 of 399 handouts give an evaluation table whose weights add up to ~100%; for the
  rest the app shows the handout's evaluation text instead of numbers. Makeup / attendance labels always come with the
  quoted sentence.
- For courses a student is already registered in, their section usually isn't known, so only single-section
  components and exam slots of those courses block time in the clash check.
- 3 programmes (ECE, Environmental & Sustainability, BBA) have CDC list vs chart differences in the bulletin itself;
  see `data/processed/verification_queue.csv`.

## Repo layout

```
ingest/     PDF parsers + db build + validation           engine/   requirements, eligibility, clashes, planner
agent/      tools, retrieval, Claude agent, rule parser   app/      Streamlit dashboard
tests/      test profiles (json) + engine/agent tests     data/processed/  structured data + academic.db
```
