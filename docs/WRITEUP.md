# BITS Academic Course Recommender: project writeup

**Postman Round 2 · solo · Pilani campus · First Semester 2026-27 timetable**
Repository: https://github.com/deejay147/agentic-AI-based-BITS-Academic-Course-Recommender

## 1. Summary

The project is an agentic course recommender for BITS Pilani. A student builds a profile, mostly derived from the
BITS ID, and asks natural-language questions such as *"Suggest an AI-related DEL with no midsem"*. The system
computes the student's remaining graduation requirements and the set of timetable offerings they are permitted to
take under the Academic Regulations. It then answers from that eligible set only. Each recommendation states the
requirement it fills, why the student is eligible, the requested course properties (quoted from the handout or
timetable), and the source of each fact.

The central design choice is a strict separation of responsibilities. **All academic rule checking is
deterministic Python.** The language layer handles only intent, semantic matching and explanation. That layer is
an optional LLM, with a rule-based parser as the default. The LLM cannot introduce a course: every pick is
re-validated against the engine before it is shown.

| Metric | Value |
|---|---|
| Course descriptions parsed (Bulletin Part VI) | 2,015 (2,119 incl. timetable-only codes) |
| Timetable offerings / sections | 719 rows, 582 codes, 1,557 sections |
| Programmes (incl. CS/C2 variants) | 28, plus 70 composite dual-degree charts |
| Handouts | 540 files → 399 unique; 287 with a reliable evaluation table |
| Minors / HUEL pool / regulation clauses | 23 / 136 / 14 |
| Items routed to the verification queue | 108 |
| Tests | 37 (engine + agent, incl. both LLM loops with scripted clients) |

## 2. Architecture

```
PDFs ──► ingest/  (parse, normalise, validate, SQLite)
                │
profile ──► engine/  requirements ─► eligibility (clause-tagged checks) ─► schedule / planner
                │
query ──► agent/  intent (LLM or rule parser) ─► tools over the eligible set ─► validate ─► explain
                │
            app/  Streamlit dashboard
```

1. **Ingestion (`ingest/`)** runs once per data release (`python -m ingest.run_all`, ~3 min). It emits JSON,
   `academic.db`, a validation report and a verification queue. A new timetable or handout set only requires
   rerunning this step; no engine or agent code changes.
2. **Engine (`engine/`)** is pure functions over the catalog and a profile: remaining requirements, per-offering
   eligibility with clause references, clash detection, section selection and the semester planner.
3. **Agent (`agent/`)** exposes the engine as five tools (`get_requirements`, `find_courses`, `course_details`,
   `check_plan`, `submit_recommendations`). The same tools serve both the LLM loop and the rule-based path.
4. **Dashboard (`app/`)** has a profile sidebar and five tabs: Requirements, Ask, Plan semester, Eligible
   courses, Data sources.

## 3. Data pipeline

### 3.1 Parsing strategy

Early experiments with flattened text (`pdftotext -layout`) on the timetable showed column drift. Wrapped titles,
wrapped instructor names and glued day tokens were misassigned across rows. The final approach uses
**coordinate-based parsing** (pdfplumber word boxes) wherever the source is tabular:

- **Timetable.** Columns are assigned by x-position and rows by y-proximity. Wrapped fragments attach to the
  nearest anchored row. Special handling covers glued day tokens (`MW`), two-digit evening hours (`11 12`),
  cancelled sections, glued lab hours, and the equivalent-course list (Section IX), which is resolved with
  union-find into 167 groups.
- **Bulletin programme pages.** CDC and DEL lists come with OR-alternatives, tracks and compulsory DELs. The
  semester charts are parsed into (year, semester) positions for every named course. Chart totals come from rows
  whose numbers are ≥ 8, which excludes stray unit numbers that had broken year detection on the Chemical chart.
- **Bulletin Part VI.** The two-column layout is split by cropping at the measured page midline. Output includes
  titles (wrapped titles merged), units, and prerequisite text, codes and mode.
- **Minors.** These are read with table extraction; titles that sit outside the table and tables that span pages
  are handled.
- **Handouts.** These are too heterogeneous for a template. The parser uses heading detection plus two
  evaluation passes: table cells, then explicit percentages. A table counts as reliable only when its weights sum
  to 95–105%. It also extracts makeup policy (none / restricted / available, with scoped exceptions), attendance
  policy (five states), recommended background and lecture-plan topics. Every field keeps its evidence sentence.
  Exact duplicates are removed by MD5, and the single scanned handout goes through Tesseract.

### 3.2 Validation and uncertainty

The specification requires that unreliable items be marked rather than guessed. `build_db.py` writes a
**validation report** (entity counts, for regression spotting) and a **verification queue** of 108 items:

| Kind | Count |
|---|---|
| handout_missing | 53 |
| course_not_in_bulletin_descriptions | 24 |
| timetable_slots_missing | 21 |
| programme_structure | 8 |
| other (OCR, minor structure) | 2 |

At runtime a missing or unextractable property yields a tri-state `None`. The UI then shows *"No specific
information mentioned; contact the Instructor-in-Charge (name)"* and lists the property as could-not-verify. A
property is shown as satisfied only when a source sentence supports it.

### 3.3 Reconciling the bulletin with itself

For three programmes the bulletin's CDC list and its semester chart disagree. The reconciler moves toward the
chart and logs every change to the verification queue. The rules:

- add chart-only codes when the list is short
- drop pure-GIR courses and off-chart courses
- swap when counts match but units don't (ECE F331 → ECE F314)

## 4. Academic engine

### 4.1 Profile

The BITS ID determines the batch, one or two programme codes (dual degree when the second slot holds a programme
code), the stream (PS / TS / CentraleSupélec 2+2 / others) and the campus. Because the only timetable is First
Semester 2026-27, the current year is `2026 − batch + 1`. The student is therefore never asked which semester they
are planning. Grades are optional: A–E count as cleared, while NC / W / I / GA / RC do not (Reg 4.11–4.12).

*Pre-fill* populates completed and current courses from the programme's semester chart (the composite chart for
dual degrees). This turns a long data-entry task into an edit task.

### 4.2 Requirements

Requirements are counted in courses, as specified: single degree 3 HUEL / 4 DEL / 5 OPEL, and dual degree with no
OPEL requirement. A programme-specific count from its chart overrides the default (e.g. Economics 6 DEL,
Biotechnology 5). Electives are filed **DEL → HUEL → OPEL** (Reg 2.05), with two constraints:

- a HUEL cannot come from the student's own discipline
- for dual-degree students there is no separate OPEL requirement, because the DELs of one degree count as OPELs of
  the other (Reg 2.05)

The output includes a graduation checklist and minor progress. For 2+2 CSP students it adds the progression
condition from bulletin p.160.

### 4.3 Eligibility

Each timetable offering is evaluated against clause-tagged checks:

| Check | Clause |
|---|---|
| Stated prerequisites | 3.13 |
| Prior preparation for own CDCs (DCA may waive up to two) | 3.14 |
| Other programmes' CDC/DEL only after own years 1–2 (applied strictly) | 3.15(b)(i) |
| Higher-degree courses: own discipline, after 2nd-year CDCs, ≤1 per semester; CGPA cutoff not in data → noted | 3.15(b)(ii), 2.08 |
| 25-unit cap | 1.01 |
| First-year foundation courses excluded as elective hosts | 2.07 / 3.18 |
| comcod ≥ 5000 / U-codes restricted to 2026 admits | Timetable note |
| No class or exam clash, trying every section combination | 3.19 |

Each rejection carries its clause, so the dashboard can answer "Why can't I take X?" precisely.

### 4.4 Timetable intelligence (bonus)

Section selection is a joint backtracking search over (course, component) decisions. Midsem and compre slots are
checked pairwise first, since exams clash regardless of section. Preferences are expressed as blocked hours (no
8 AM, a free weekday). *Compact* chooses the clash-free assignment with the fewest idle hours, with the fewest
active days as the tie-break. If one section of a course clashes, another is tried before the course is rejected,
which is the case the specification describes. Students may optionally enter their own sections for registered
courses to make the check exact. Without them, only single-section components and exam slots block time, and the
app says which were missing. The planner tab shows a week grid.

## 5. Agent layer

### 5.1 Tools and validation

`find_courses` searches only the eligible set. It filters by category, handout properties and time preferences,
then ranks by topic. `submit_recommendations` is the only way to finish an LLM turn. Every submitted code passes
through `Session.validate`, which rejects a pick when:

- it is not eligible for this student (the engine's reason is returned)
- it cannot count as the requested category
- the handout contradicts a requested property

Invalid picks are dropped and reported, never shown as recommendations.

### 5.2 Retrieval

Interest matching uses **BM25** over a per-course document: title weighted ×3, bulletin description, and handout
lecture plan. It adds bigrams, a stopword list tuned to handout boilerplate, a light stemmer that keeps a surface
form for display, and a synonym map (e.g. `ai` → artificial intelligence, machine learning, neural networks,
reinforcement learning…). A relevance threshold (≥ max(1.5, 0.3 × top score)) prevents padding the list with weak
matches. Results are deduplicated by handout file and canonical code.

BM25 was chosen over embeddings on purpose:

- it is deterministic and explainable (matched terms are shown on each card)
- it needs no model download or network
- the vocabulary gap it leaves is closed by the synonym map in rules mode and by the LLM's query expansion in LLM
  mode

### 5.3 Two modes

- **Rules mode (default, no key).** `nlu.py` extracts intent (recommend / requirements / details / plan),
  categories, required properties, time preferences, course codes and topic terms. Explanations are templated.
  When no course satisfies every property, it presents near misses with what each lacks. When the best topical
  matches are blocked, it says so up front and names the blocking clause for each.
- **LLM mode (optional).** A tool-calling loop in two implementations: the Anthropic Messages API, and the OpenAI
  chat-completions format, which covers Gemini's and Groq's free tiers and any compatible server. Keys go in the
  sidebar (kept for the session only) or in `.env` (gitignored). On any API failure the answer falls back to rules
  mode with a visible notice.

Rules mode was promoted to the primary path mid-project. The submission must work for an evaluator without a paid
key, and the specification asks for deterministic rule checking anyway, so the LLM adds fluency, not correctness.

## 6. Engineering decisions

| Decision | Rationale |
|---|---|
| Coordinate parsing over text extraction for tabular PDFs | Text extraction mis-assigned wrapped cells; coordinates are stable |
| Bulletin chart wins over CDC list, with logging | The chart encodes placement; discrepancies are surfaced, not hidden |
| Tri-state property checks with evidence | Enforces "could not be verified" instead of silent defaults |
| Engine is LLM-free; LLM output re-validated | Hallucinated or ineligible courses cannot reach the user |
| BM25 + synonyms over embeddings | Deterministic, offline, explainable, adequate for ~580 courses |
| Rules mode as primary; LLM optional with fallback | Works for any evaluator; no single point of failure |
| OpenAI-compatible client in addition to Anthropic | Enables free providers without code changes |
| Year inferred from batch and the single supplied timetable | Removes a user input that could be entered inconsistently |
| Elective counts in courses; chart-specific counts override defaults | Matches the stated requirements and programme exceptions |
| Reg 3.15(b)(i) applied strictly | Conservative; avoids recommending something a student can't register for |
| Processed data committed, raw PDFs not | App runs immediately from a clone; repo stays small |
| Streamlit | Fastest path to a usable dashboard in pure Python |

Scope decisions agreed during the project:

- The 2025-26 curriculum applies to every batch. Earlier batches get a notice and still see results.
- Pilani only.
- "No prerequisites required" appears only when asked.
- Missing handout information directs the student to the Instructor-in-Charge.

## 7. Problems encountered and resolutions

| Problem | Cause | Resolution |
|---|---|---|
| Only 546 course descriptions found | Column split at x=522 on a 595-wide page | Measured the page; split at 296 → 2,015 courses |
| Timetable rows with empty titles (e.g. CS U111) | Titles wrapped onto unanchored lines | Attach fragments to the nearest anchored row |
| `MW`, `11 12` misread | Glued days; evening hours vs hours 1, 2 | Day tokeniser; two-digit hour rule |
| Chemical chart years wrong | Stray unit numbers taken as totals | Total rows require values ≥ 8 |
| BBA programme not detected | Heading is an image | Identify by course prefixes |
| CS F111 dropped from GIR | Own-department filter | Keep the full GIR list |
| Dual-degree CDCs shown eligible too early | Single-degree chart positions used | Composite dual charts + group positions |
| Handout sections cut short | Numbered table rows looked like headings | Stricter heading detection |
| "No makeup" over-applied | Sentence scoped to one component | Scoped-exception detection |
| `NC` matched "announced" | Case-insensitive regex | Case-sensitive `\bNC\b` |
| Component names like "1" | Serial-number column | Skip numeric first columns; second percent-based pass (192 → 287 reliable tables) |
| "machine learning" matched every course | "learning" in handout boilerplate | Bigrams + boilerplate stopwords; stopwords applied after stemming |
| Cards said "fills HUEL" for an OPEL query | Category shown was the default filing | Show the requested category (`shown_as`) |
| Rules-mode footer lost in UI | Text truncation | Split and render footer below cards |
| LLM client detection raised on a broken client | `hasattr` triggered the error | Guarded detection with try/except |

## 8. Additions beyond the specification

- A no-key agent that supports every feature, and free LLM providers (Gemini, Groq) alongside Anthropic.
- Blocked-topic explanations and near-miss suggestions.
- A semester planner with automatic DEL/HUEL/OPEL filing, section selection, unit and extra-elective warnings
  (Reg 2.08), and a week view.
- "Why can't I take X?" with the governing clause.
- A graduation checklist, minor progress (23 minors), dual-degree composite charts and CentraleSupélec 2+2
  handling.
- Optional section entry for registered courses.
- Recommended background from handouts, shown as advice rather than enforced.
- Chart-based profile pre-fill.
- A validation report, a verification queue and a Data sources tab.
- Reproducible example outputs (`docs/examples.md`) and README screenshots, both generated from code.
- 11 test profiles, chosen for coverage:
  - single degrees across five disciplines
  - an NC grade
  - a minor
  - a 2023 batch
  - CentraleSupélec 2+2
  - three dual degrees in years 2–4, including one at 23 units where almost nothing fits

## 9. Mapping to the evaluation requirements

| Requirement | Where it is met |
|---|---|
| Profile create/update | Sidebar: ID parsing, pre-fill, editable completed/registered lists, save/load |
| Live computation, nothing hardcoded | Engine computes from `academic.db` on every query; examples generated by script |
| Pre-processing with schema and source metadata | `ingest/`, JSON + SQLite, per-record source references |
| Mark rather than guess | Verification queue; tri-state properties; "could not be verified" |
| Deterministic rules; LLM for intent/matching/explanation | `engine/` has no LLM; `Session.validate` gates the agent |
| Handout-based preferences | Evaluation, midsem/compre, quizzes, project, lab, open book, makeup, attendance, topics, IC |
| Concise recommendation format | Card: requirement, eligibility, properties with quotes, match reason, sources |
| New semester without logic changes | Rerun `ingest.run_all` only |
| Timetable intelligence (bonus) | Class/tutorial/lab/exam clashes, alternative sections, no 8 AM, free day, compact |
| Clean repo with setup instructions | README, `docs/RUNNING.md`, tests |

## 10. Limitations and future work

- **Handout coverage.** 112 of 399 handouts have no reliable numeric evaluation table; their text is shown
  instead. An LLM-assisted extraction pass, with human review via the verification queue, would close most of
  this gap.
- **Section-level clash precision** depends on the student entering sections for registered multi-section
  courses.
- **Source inconsistencies.** Three programmes have bulletin inconsistencies, and 21 offerings lack slots in the
  timetable. These are surfaced, not resolved.
- **Data not supplied.** The CGPA cutoffs for higher-degree courses (Reg 2.08) and the CSP course mapping are not
  in the dataset; the app notes this.
- **Retrieval.** Synonyms are hand-curated. Embedding-based retrieval, with BM25 as a fallback, would widen
  topic coverage in no-key mode.
- **Scope.** Pilani only, one semester. Other campuses would need their timetables and handouts run through the
  same pipeline.
