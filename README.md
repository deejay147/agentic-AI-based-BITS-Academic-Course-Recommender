# BITS Academic Course Recommender

A course-picking assistant for BITS Pilani students (Postman Round 2).

You enter your BITS ID and the courses you've done. The app works out what you still need to graduate and which
courses in the **First Semester 2026-27** timetable you're allowed to take. Then you can ask things like
*"Suggest DELs related to AI"* or *"I want an OPEL with no attendance requirement"*. Every answer says which
requirement the course fills, why you're allowed to take it, and where each fact came from.

## Run it

```bash
pip install -r requirements.txt
streamlit run app/app.py          # opens http://localhost:8501
```

**No API key needed.** The data is already processed and included, so the app works straight away.

Try it: in the sidebar pick `test: cs_2nd_year`, click **Load**, open the **Ask** tab and type
*Suggest DELs related to AI*.

For a step-by-step guide (Windows / Mac / Linux, troubleshooting), see **[docs/RUNNING.md](docs/RUNNING.md)**.

## Docs

| | |
|---|---|
| [docs/RUNNING.md](docs/RUNNING.md) | How to install and run it, step by step |
| [docs/WRITEUP.md](docs/WRITEUP.md) | How it was built, key decisions, problems we fixed, extras, limitations |
| [docs/examples.md](docs/examples.md) | Real answers to the task's example questions |

## What it looks like

| | |
|---|---|
| ![requirements](docs/img/1_requirements.png) | ![AI DELs](docs/img/2_ask_ai_dels.png) |
| What you still need to graduate | "Suggest DELs related to AI" |
| ![blocked topic](docs/img/3_ask_blocked_topic.png) | ![planner](docs/img/4_plan_semester.png) |
| "Finance OPEL" for a 2nd-year: what fits, and which better matches are blocked by which rule | Semester planner with clash-free sections |

## What's in the app

- **Sidebar: your profile.** Type your BITS ID (e.g. `2025A7PS0147P`) and the app fills in your batch, degree
  and stream. It works for dual degrees (`2024B3A7...`) and 2+2 CentraleSupélec students (`2025AACS...`) too.
  **Pre-fill** adds the courses you'd normally have done by now; edit anything that's different, add grades if
  you want (NC, W and I count as not passed), and pick what you're registered for this semester.
- **Requirements.** What's left: compulsory courses, DELs, HUELs, OPELs, general courses, minor progress, and a
  graduation checklist.
- **Ask.** Chat in plain English. Each suggested course comes as a card: what it fills, why you're allowed to take
  it, the details you asked about (quoted from the handout), and sources.
- **Plan semester.** Pick the electives you want. The app sorts them into DEL / HUEL / OPEL, picks sections that
  don't clash, checks the 25-unit limit and shows your week. Options: no 8 AM, keep a day free, compact timetable.
- **Eligible courses.** Everything you can take this semester, plus "Why can't I take X?" with the exact rule.
- **Data sources.** How the data was built and what couldn't be checked.

## How it works

```
PDFs ──► ingest/   read the PDFs once, save clean data
profile ──► engine/  what's left? what am I allowed to take? any clashes?   (plain code, no AI)
question ──► agent/  understand the question, search allowed courses, double-check, explain
            app/     the website
```

**Rules are checked by plain code, not AI.** The AI part only helps understand the question and word the answer.
Every course it suggests is checked again by the rules code before you see it, so it can't recommend something you
aren't allowed to take.

**Two modes.**
- *No-key mode (default):* a built-in parser understands the question. Every feature works.
- *AI mode (optional):* add a Google Gemini or Groq key (both have free tiers) or an Anthropic key, in the sidebar
  or in a `.env` file (copy `.env.example`). If the AI call fails, the app falls back to no-key mode and says so.

**The rules it checks**, each tied to the Academic Regulations:

| Rule | Regulation |
|---|---|
| You've passed the prerequisites | 3.13 |
| For your own compulsory courses, you've done the earlier ones | 3.14 |
| Another programme's courses only after your own years 1–2 | 3.15(b)(i) |
| Higher-degree courses: own discipline, after 2nd-year courses, one per semester | 3.15(b)(ii), 2.08 |
| At most 25 units per semester | 1.01 |
| No class or exam clash | 3.19 |

**Timetable bonus:** if one section of a course clashes, the app tries the other sections before saying no. It
checks lectures, tutorials, labs, midsems and compres, and can avoid 8 AM classes, keep a day free, or keep your
timetable compact.

**If something isn't in the data, it says so.** When a handout doesn't mention (say) attendance, the answer is
*"No specific information mentioned; contact the Instructor-in-Charge"*, never a guess. Anything the PDF reader
wasn't sure about goes into `data/processed/verification_queue.csv`.

## Rebuilding the data from the PDFs

Only needed for a new timetable or new handouts. Put the files in `data/raw/`:

```
data/raw/bulletin.pdf
data/raw/timetable.pdf
data/raw/regulations.pdf          # Academic-Regulations-2023.pdf, renamed
data/raw/handouts/*.pdf
```

Then run `python -m ingest.run_all` (about 3 minutes; needs `pdftotext` from poppler). No other code changes.

## Tests

`pytest -q` runs 37 tests on the rules and the assistant. `python -m tests.run_examples` regenerates
[docs/examples.md](docs/examples.md).

## Scope and limits

- Uses the 2025-26 curriculum from the supplied Bulletin. Older batches get a note saying so and still get results.
- Pilani campus only.
- 287 of 399 handouts have a marks table the app can read cleanly. For the rest it shows the handout's own text.
- Clash checks are exact only if you enter your sections for courses that have more than one.
- The Bulletin disagrees with itself for 3 programmes; these are listed in the verification file.

## Folders

```
ingest/   PDF readers + database build        engine/  requirements, rules, clashes, planner
agent/    question understanding + search     app/     the Streamlit website
tests/    test students + tests               data/processed/  the processed data
docs/     guides, writeup, examples, screenshots
```
