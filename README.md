# BITS Academic Course Recommender

Agentic course recommender for BITS Pilani students (Postman Round 2).
Work in progress - built phase by phase, see the status table below.

| Phase | What | Status |
|---|---|---|
| 1 | Ingestion: timetable, bulletin, handouts, regulations -> SQLite | done |
| 2 | Academic engine: remaining CDC/DEL/HUEL/OPEL, eligibility, clash check | done |
| 3 | Agent + retrieval (Claude API, with a no-key fallback) | next |
| 4 | Streamlit dashboard | |
| 5 | Timetable intelligence (bonus) | |

## Data

Put the supplied dataset in `data/raw/` (not committed, it's ~250 MB):

```
data/raw/bulletin.pdf
data/raw/timetable.pdf
data/raw/regulations.pdf          # Academic-Regulations-2023.pdf, renamed
data/raw/handouts/*.pdf
```

The processed output is committed in `data/processed/`, so the app runs without the PDFs.
To rebuild it:

```bash
pip install -r requirements.txt     # also needs poppler-utils (pdftotext), tesseract is optional
python -m ingest.run_all            # ~3 min
```

## What ingestion produces

| file | contents |
|---|---|
| `timetable.json` | 719 course rows / 582 codes, 1557 sections with day-hour slots, midsem + compre slots, IC, "2026 admits only" flag |
| `equivalents.json` | old/cross-listed code groups from timetable section IX (CS F215 ~ EEE F215 ~ INSTR F215 ...) |
| `programmes.json` | 28 first-degree programmes: CDC + DEL groups (with OR alternatives, tracks, compulsory DELs), GIR courses, unit/course totals from the semester charts |
| `huel_pool.json` | 136 Humanities electives + the own-discipline rule |
| `minors.json` | 23 minors: core/electives/pools + the general minor rules |
| `courses_bulletin.json` | 2015 course descriptions with units and stated prerequisites |
| `handouts.json` | 399 unique handouts (540 files, cross-listed duplicates merged): evaluation components, midsem/compre/quiz/project/lab flags, makeup + attendance policy - each with the text it came from |
| `rules.json` | the regulation clauses the engine uses, with clause numbers |
| `academic.db` | everything above joined in SQLite |
| `validation_report.md`, `verification_queue.csv` | things that didn't check out and need a human look |

Every record carries a `source` (document + page/section) so answers can be traced back.

## Academic engine (`engine/`)

Deterministic, no LLM involved:

- `profile.py` - profile + ID parsing (`2025A7PS0147P` -> batch 2025, B.E. CS, Pilani -> year 2, sem 1 of 2026-27;
  `2024B3A70123P` -> dual degree M.Sc. Eco + B.E. CS). Grades optional; NC / W / I / RC etc. count as not cleared (reg 4.11-4.12).
- `requirements.py` - remaining GIR, CDC, DEL, HUEL, OPEL (+ minor progress). Old/cross-listed codes are matched
  through the equivalence list. Electives are allocated DEL -> HUEL -> OPEL as in reg 2.05.
- `eligibility.py` - every course offered this semester gets a category for this student and a list of rule checks,
  each tagged with its clause: prerequisites (3.13), prior preparation (3.14), other-discipline courses (3.15(b)(i)),
  higher degree courses (3.15(b)(ii), 2.08), 25-unit cap (1.01), 2026-only courses, clash-free timetable (3.19).
- `schedule.py` - class + exam clash checks; tries every section combination before calling a course a clash.

Test profiles (built from the semester charts) are in `tests/profiles/`, tests in `tests/test_engine.py` (`pytest -q`).

## Scope decisions

- Curriculum rules come from the supplied Bulletin (2025-26). Profiles from earlier batches still get
  results, with a note that the 2025-26 curriculum was applied.
- Pilani campus only (the timetable and handouts are Pilani's).
- If a handout doesn't say something (attendance, makeup ...), the app says no specific information is
  mentioned and to contact the Instructor-in-Charge. It never guesses.
- Prerequisites: only ~70 courses state one in the bulletin. When none is listed, the app says
  "no prerequisites required" if asked, and doesn't bring it up otherwise.
