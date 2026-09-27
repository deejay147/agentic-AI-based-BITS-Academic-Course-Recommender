# BITS Academic Course Recommender

Agentic course recommender for BITS Pilani students (Postman Round 2).
Work in progress - built phase by phase, see the status table below.

| Phase | What | Status |
|---|---|---|
| 1 | Ingestion: timetable, bulletin, handouts, regulations -> SQLite | done |
| 2 | Academic engine: remaining CDC/DEL/HUEL/OPEL, eligibility, clash check | next |
| 3 | Agent + retrieval (Claude API, with a no-key fallback) | |
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

## Scope decisions

- Curriculum rules come from the supplied Bulletin (2025-26). Profiles from earlier batches still get
  results, with a note that the 2025-26 curriculum was applied.
- Pilani campus only (the timetable and handouts are Pilani's).
- If a handout doesn't say something (attendance, makeup ...), the app says no specific information is
  mentioned and to contact the Instructor-in-Charge. It never guesses.
- Prerequisites: only ~70 courses state one in the bulletin. When none is listed, the app says
  "no prerequisites required" if asked, and doesn't bring it up otherwise.
