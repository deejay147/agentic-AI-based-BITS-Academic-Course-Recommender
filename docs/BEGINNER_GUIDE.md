# How this project was built: a beginner's guide

This guide explains the whole project, start to finish, for someone who has never built anything like it.
If you know a bit of Python, you can follow all of it. Where a new idea comes up, it follows the same pattern:
a small example, the intuition behind it, how it works, and where it goes wrong.

If you just want to run the app, read [RUNNING.md](RUNNING.md) instead.

---

## Part 0: The problem in one paragraph

Every semester, BITS students pick courses. To do that properly you have to read four things: the **Bulletin**
(which courses your degree needs), the **Academic Regulations** (rules like "you can't take more than 25 units"),
the **Timetable** (when each course meets and when its exams are), and **course handouts** (how each course is
graded: is there a midsem, is attendance compulsory, and so on). That's hundreds of pages of PDFs. The task was to
build an app where a student says *"Suggest a DEL related to AI with no midsem"* and gets a correct, explained
answer.

The key word is **correct**. A chatbot that makes up a course, or recommends one you aren't allowed to take, is
worse than no chatbot. So the whole design comes from one idea:

> **Rules are checked by normal code. The AI only helps with language.**

---

## Part 1: The big picture

Think of it as a factory with four stations:

```
   PDFs  ──►  [1. Ingestion]  ──►  clean data (JSON + a database)
                                        │
   student profile  ──►  [2. Engine]  ◄─┘   "what does this student still need,
                              │               and which courses can they legally take?"
                              ▼
   question  ──►  [3. Agent]  ──►  picks + explanations, then re-checked by the engine
                              │
                              ▼
                     [4. Dashboard]  ──►  what the student sees in the browser
```

| Station | Folder | Uses AI? | In one line |
|---|---|---|---|
| Ingestion | `ingest/` | No | Read the PDFs once, turn them into tidy records |
| Engine | `engine/` | No | Apply the rules to one student |
| Agent | `agent/` | Optional | Understand the question, find matching courses, explain |
| Dashboard | `app/` | No | The website |

Why split it like this? Each station can be tested on its own, and if the timetable changes next semester, only
station 1 has to run again.

---

## Part 2: The tools (tech stack) and why each one

| Tool | What it is | Why we used it |
|---|---|---|
| **Python** | The programming language | Best libraries for PDFs, data and AI |
| **pdfplumber** | Reads PDFs and tells you *where* every word is (x, y position) | The timetable and bulletin are tables; position is what tells you which column a word belongs to |
| **pdftotext** (poppler) | Turns a PDF into plain text while keeping the layout | Handouts read better this way |
| **PyMuPDF** | Another PDF reader | Backup when pdftotext isn't installed |
| **Tesseract** | OCR: reads text out of an image | One handout was a scanned image |
| **JSON** | A text format for data (`{"code": "CS F213", "units": 4}`) | Easy to read and to check by eye |
| **SQLite** | A database stored in one file | Fast lookups; no server to install |
| **Streamlit** | Turns a Python script into a website | Build a dashboard without writing HTML/JavaScript |
| **BM25** | A classic search-ranking formula | Finds courses that match "AI" without any AI |
| **LLM APIs** (Gemini, Groq, Claude) | Large language models you call over the internet | Optional: better understanding of free-form questions |
| **pytest** | Test runner | 37 automatic checks that things still work |
| **Playwright** | Controls a browser from code | Took the screenshots in the README |
| **git / GitHub** | Version control | Every step is a commit you can look back at |

---

## Part 3: Station 1, ingestion (reading the PDFs)

### 3.1 Why PDFs are hard

A PDF doesn't store "a table with rows and columns". It stores "draw the letters `CS F213` at position
x=40, y=312". Humans see a table. The computer sees scattered words.

**Example.** A timetable row looks like this to you:

```
COM COD   COURSE NO   TITLE                  SEC   INSTRUCTOR      DAYS/HOURS
1234      CS F213     OBJECT ORIENTED PROG   L1    SMITH J         M W F 2
```

If you just "copy all text", titles that wrap onto two lines mix into the next row, and a day like `MW`
(Monday + Wednesday, printed glued together) looks like one word.

**Intuition.** Use the *position* of each word. Everything whose x-position is between 60 and 110 is the
course number, everything between 110 and 300 is the title, and so on. Everything whose y-position is close to
the same line is the same row.

**How it's done.** `pdfplumber` gives every word with its coordinates:

```python
for w in page.extract_words():
    # w = {"text": "CS", "x0": 61.2, "top": 312.4, ...}
    column = which_column(w["x0"])     # decide the column from the x-position
```

**Edge cases we actually hit** (each one was a bug first):

- **Wrapped titles.** A long title continues on the next line, where the course code is empty. Fix: attach a
  title fragment to the nearest row above it.
- **Glued days.** `MW` means Monday and Wednesday. Fix: split day tokens into known day letters.
- **"Th 11 12".** Normally `1 2` means hours 1 and 2, but in some rows `11 12` means the evening hours
  11 and 12, not hours 1,1,1,2. Fix: a special rule for two-digit hours.
- **Cancelled sections.** Some rows say `CANCLED` (typo in the source). Fix: skip them.
- **Missing information.** A few courses in the timetable have no days/hours at all. Fix: **don't guess**; put
  them in a "verification queue" file so a human can check them.

### 3.2 The bulletin

The bulletin has two kinds of pages we need:

1. **Programme pages**: for each degree (e.g. B.E. Computer Science = code `A7`), the list of compulsory
   courses (CDCs), the elective list (DELs), and a **semester chart** showing which course is done in which
   year and semester.
2. **Course descriptions** (Part VI): 2000+ courses with title, units, description and prerequisites.

The course description pages are **two columns**. Reading straight across mixes the left and right columns.
Fix: cut each page vertically in half (`crop`) and read each half separately.

> **A real bug:** the first version cut the page at x=522, thinking the page was wider than it is. It found only
> 546 courses. Measuring the page showed it's 595 wide, so the middle is about 296. After the fix: 2015 courses.
> Lesson: when a number looks low, check your assumptions about the input.

Other bulletin surprises:

- The **BBA heading is an image**, not text, so the parser couldn't "see" it. Fix: recognise BBA by its course
  prefixes instead.
- Sometimes the **CDC list and the semester chart disagree** (e.g. B.Pharm lists a course the chart replaces).
  Fix: trust the chart (the bulletin itself says the chart governs), and log every disagreement.
- **Dual degrees** (e.g. `B3A7` = M.Sc. Economics + B.E. CS) have their own combined charts at the back
  (pages 242–313). 70 of them were parsed so a dual student is placed on the correct combined timeline.

### 3.3 The handouts

There are 540 handout PDFs (399 unique after removing exact duplicates by comparing file fingerprints, called
MD5 hashes). Each instructor writes theirs differently, so there's no single template.

For each handout we pull out:

| Property | Example of what we look for |
|---|---|
| Evaluation table | rows like `Mid-Semester Test   90 min   30%   Closed Book` |
| Midsem / compre / quizzes / project / lab | a component with that name |
| Makeup policy | a sentence containing "make-up" / "makeup" |
| Attendance policy | a sentence containing "attendance" |
| Recommended background | "students are expected to know…" |
| Topics | the lecture plan text |

**Intuition.** Find the section heading ("Evaluation Scheme"), then read the rows under it. If the percentages
add up to about 100%, trust the table. If not, don't pretend: show the handout's own words instead.

**The most important rule:** every extracted fact keeps the **sentence it came from**. So when the app says
"No makeup", it can show you the exact line from the handout.

> **A real bug:** searching for "NC" (Not Cleared) with a case-insensitive search also matched the letters "nc"
> inside the word "a**nn**ou**nc**ed". Fix: make that search case-sensitive.
>
> **Another:** some tables have a serial-number column (1, 2, 3…). The parser took "1" as the name of the
> component. Fix: skip a first column that's just numbers.
>
> **Another:** "No makeup for quizzes" was read as "no makeup at all". Fix: detect when a "no makeup" sentence is
> limited to one component.

After two parsing passes, 287 of 399 handouts give a clean table. The rest still give their text; they just
don't get numbers.

### 3.4 Storing it

Everything goes into JSON files and then into one **SQLite** database (`data/processed/academic.db`).
`build_db.py` also writes:

- `validation_report.md`: counts of everything, so you can see at a glance if a parser broke.
- `verification_queue.csv`: 108 items the parser wasn't sure about. The app never guesses these; it says
  "could not be verified".

You run the whole pipeline with one command: `python -m ingest.run_all`.

---

## Part 4: Station 2, the engine (the rules)

This is plain Python, no AI. Given a student, it answers two questions:

1. **What do I still need to graduate?** (`engine/requirements.py`)
2. **Which courses in this timetable am I allowed to take right now?** (`engine/eligibility.py`)

### 4.1 Knowing who the student is

**Example.** BITS ID `2025A7PS0147P`:

```
2025   A7    PS    0147   P
batch  B.E.  Practice    Pilani
       CS    School
```

And `2024B3A70123P`: the slot where `PS` would be holds `A7`, a second degree code, so it's a **dual degree**
(B3 = M.Sc. Economics, A7 = B.E. CS).

```python
batch, p1, p2, _, campus = ID_RE.match(s).groups()
progs = [p1]
if p2 is a programme code:
    progs.append(p2)        # dual degree
```

The only timetable supplied is First Semester 2026-27, so a 2025 batch student is in **year 2, semester 1**.
That's how the app knows "what semester you're planning" without asking.

### 4.2 Remaining requirements

A BITS degree needs:

- **GIR**: general courses everyone does (Maths, Physics, etc.)
- **CDC**: your degree's compulsory courses
- **DEL**: electives from your own discipline (usually 4)
- **HUEL**: humanities electives (3)
- **OPEL**: open electives, anything else (5; dual degree students don't need these)

**Intuition.** Take the list of courses the student has cleared. Tick off GIRs and CDCs. Everything else is an
elective, filed into the first bucket it fits: **DEL first, then HUEL, then OPEL** (the regulations say this).

**Edge cases:**

- **Grades.** A, B, C, D, E count as cleared. NC, W, I, GA and RC don't (Regulations 4.11–4.12). Grades are
  optional; no grade means "cleared".
- **Renamed courses.** A course can have an old code and a new code. The timetable lists "equivalent courses";
  we group them with a small algorithm called **union-find** (if A = B and B = C, then A = C).
- **Different numbers per degree.** Economics needs 6 DELs, not 4. The chart's number wins over the default.
- **A HUEL can't be from your own department.**

### 4.3 Eligibility: can I take this course now?

For every course in the timetable, the engine runs a list of checks. Each check is tagged with the regulation
clause it comes from, so the app can tell you *why*:

| Check | Clause | Plain English |
|---|---|---|
| Prerequisites | 3.13 | Did you clear the courses it requires? |
| Prior preparation | 3.14 | For your own CDCs, did you do the earlier-semester ones? |
| Other degrees' courses | 3.15(b)(i) | You can only take other programmes' CDC/DEL after your own years 1–2 |
| Higher degree courses | 3.15(b)(ii), 2.08 | M.E./M.Sc. courses only in your discipline, after 2nd-year CDCs, one per semester |
| Unit limit | 1.01 | No more than 25 units in a semester |
| Clash | 3.19 | No class or exam overlap with what you're already taking |

**Example.** A 2nd-year CS student asks for a finance OPEL. The best matching courses are Economics DELs, but
rule 3.15(b)(i) says a 2nd-year can't take another programme's DELs yet. The app shows what does fit, and says
"better matches exist but are blocked by 3.15(b)(i)", listing them.

### 4.4 Timetable intelligence (the bonus)

**Example** (made-up hours, to show the idea). You want a course whose lecture section L1 meets on Monday hour 2,
when your CDC also meets. Section L2 meets on Tuesday hour 5, which is free. A naive checker would say "clash, rejected". Ours picks L2.

**Intuition.** Treat it like a puzzle. Each course has components (lecture, tutorial, lab). Each component has
a few section options. Try options one by one; if you hit a clash, go back and try the next option. This is
called **backtracking**. A semester has ~6 courses with a few sections each, so it's fast.

```python
def bt(i, taken):
    if i == len(slots):           # every component placed without clash
        record the solution
        return
    code, kind, secs = slots[i]
    for sec in secs:
        if sec's hours overlap taken or blocked hours:
            continue              # try next section
        bt(i + 1, taken | sec's hours)
```

Preferences are just extra rules on top:

- **No 8 AM**: block hour 1 (8–9 AM) on every day.
- **Keep a day free**: block every hour on that day.
- **Compact**: among all clash-free solutions, pick the one with the fewest idle hours in between classes:

```python
def gap_hours(taken):
    # per day: (last hour - first hour + 1) - hours actually used
    return sum(max(hs) - min(hs) + 1 - len(set(hs)) for hs in by_day.values())
```

Exams are checked separately: two courses with a midsem in the same date + session clash no matter which
section you're in.

---

## Part 5: Station 3, the agent (understanding the question)

### 5.1 What "agentic" means here

A plain chatbot just writes a reply. An **agent** can use **tools**: it decides "I should look up the
requirements", calls a function, reads the result, and decides the next step. Our tools are the engine:

| Tool | What it does |
|---|---|
| `get_requirements` | What's left for this student |
| `find_courses` | Search the **eligible** courses by category, property ("no midsem") and topic ("AI") |
| `course_details` | Everything about one course, including why you can or can't take it |
| `check_plan` | Do these courses fit together in the timetable? |
| `submit_recommendations` | The final answer, which gets checked before it's shown |

The AI never sees courses you can't take as options, because `find_courses` only searches the eligible set.

### 5.2 The safety net: validation

Even so, an LLM might make something up. So every course it submits goes through `Session.validate`:

```python
if code not in self.eligible:          -> rejected, with the engine's reason
if it can't count as the requested category (e.g. asked for DEL, it's an OPEL)  -> rejected
if the handout contradicts a requested property (asked "no midsem", handout has one) -> rejected
```

Rejected picks are dropped and the user is told. **The AI can suggest; only the engine can approve.**

### 5.3 Finding "AI-related" courses without AI: BM25

**Example.** The query is "AI". A course titled *Neural Networks and Fuzzy Logic* doesn't contain "AI", but it
is AI-related.

**Step 1, synonyms.** A small hand-made table expands `ai` into words like `artificial intelligence`,
`machine learning`, `deep learning`, `neural networks`, `reinforcement learning`.

**Step 2, scoring with BM25.** For each course we build a "document": title (counted three times, because the
title matters most), bulletin description, and handout lecture plan. BM25 gives a score based on:

- **how often** a query word appears in the course's document (more is better, but with diminishing returns),
- **how rare** the word is across all courses (a rare word like "neural" matters more than a common one like
  "system"),
- **how long** the document is (long documents mention everything, so they're penalised a little).

```python
s += idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / avgdl))
#    rarity   frequency, with saturation and length normalisation
```

**Edge cases we fixed:**

- The word "learning" appears in every handout ("learning outcomes"), so "machine learning" matched everything.
  Fix: treat common filler words as stopwords and use **bigrams** (pairs of words like `machine learning`).
- **Stemming** (cutting words to their root: "networks" → "network") helps matching but showed ugly words like
  "optim" to users. Fix: remember the original word for display.
- Weak matches were shown just to fill the list. Fix: a **relevance threshold**: a result must score at least
  30% of the best result.

### 5.4 Two modes

- **No-key mode (default).** A rule-based parser (`agent/nlu.py`) reads the question: which category (DEL,
  HUEL, OPEL), which properties ("no midsem", "no attendance", "project-based"), time preferences ("no 8 AM"),
  course codes, and topic words. Then it calls the same tools and fills in explanation templates. It needs no
  internet and gives the same answer every time.
- **LLM mode (optional).** If you give an API key (Gemini and Groq have free tiers), the LLM does the reading and
  the wording. It calls the same tools in a loop. If the call fails (bad key, no internet), the app falls back to
  no-key mode and says so.

We made no-key mode the main path because the task needed a working submission without anyone paying for an
API, and because rule checks shouldn't depend on an AI anyway.

### 5.5 "Could not be verified"

When a student asks for "no attendance requirement" and the handout doesn't mention attendance, the app does
**not** say yes or no. Each property check returns one of three values:

```
True   → the handout says so (quote shown)
False  → the handout says the opposite (quote shown)
None   → not mentioned → "No specific information mentioned; contact the Instructor-in-Charge (name)"
```

---

## Part 6: Station 4, the dashboard

Streamlit reruns your Python script top to bottom every time the user clicks something, and draws whatever
`st.` calls you make:

```python
import streamlit as st
id_no = st.sidebar.text_input("BITS ID")
if st.button("Pre-fill courses"):
    ...
st.tabs(["Requirements", "Ask", "Plan semester", "Eligible courses", "Data sources"])
```

To keep data between reruns (the profile, the chat history), it uses `st.session_state`, a dictionary that
survives the reruns.

A helpful extra: **Pre-fill**. Typing every course you've done is tedious, so the app reads your semester chart
and fills in everything a student in your year normally has done. You then edit what's different.

---

## Part 7: Testing

- **Test profiles.** 11 made-up students in `tests/profiles/`, deliberately different: CS, Mechanical, Pharmacy,
  Chemical (with an NC grade), ECE, EEE with a Data Science minor, a 4th-year Civil from the 2023 batch, a 2+2
  CentraleSupélec student, and three dual-degree students in years 2, 3 and 4. One of them (B3A7, 3rd year) is
  already at 23 units, so almost nothing fits; the app must explain that rather than recommend anyway.
- **Automatic tests.** `pytest -q` runs 37 tests. The LLM loops are tested with **fake clients**: pretend AI
  objects that return a scripted answer, including a deliberately invalid pick, to prove validation drops it.
- **Examples.** `python -m tests.run_examples` runs real questions and writes the answers to
  [examples.md](examples.md), so anyone can see real output without running anything.
- **Screenshots.** Playwright opened the app in a headless browser, typed questions, and saved images.

---

## Part 8: What went wrong, and what we learned

| Problem | Lesson |
|---|---|
| Plain text extraction scrambled tables | Use positions (coordinates), not just text |
| Found 546 courses instead of ~2000 | Sanity-check counts; measure the input instead of assuming |
| "NC" matched inside "announced" | Be careful with case-insensitive search on short words |
| Bulletin contradicts itself | Pick a rule (chart wins), apply it consistently, and log every case |
| "learning" matched every course | Common words carry no meaning; remove them |
| LLM could invent courses | Never trust it for rules; validate every output against the engine |
| A command to stop the server also killed the terminal running it | `pkill -f pattern` matches your own command too; be specific |
| The web app kept showing old behaviour | Streamlit caches imported modules; restart after code changes |

---

## Part 9: Things we added beyond the task

- A **free** LLM option (Gemini / Groq) and a fully working **no-key** mode.
- **Blocked-topic explanations**: "better matches exist, here's the rule that blocks each".
- **Near misses**: if nothing matches every property, the closest options with what they're missing.
- **Semester planner**: pick electives, they're filed into DEL/HUEL/OPEL automatically, sections are chosen,
  and you see a week grid.
- **"Why can't I take X?"** with the regulation clause.
- **Graduation checklist** and **minor progress** (23 minors parsed).
- **Dual-degree composite charts** and **2+2 CentraleSupélec** support.
- **Your own sections** for registered courses, so clash checks are exact.
- **Recommended background** from handouts, shown as advice (not enforced as a prerequisite).
- A **validation report** and **verification queue** for the data.

---

## Glossary

| Term | Meaning |
|---|---|
| API | A way for one program to ask another for something over the internet |
| API key | A password that identifies you to an API |
| Backtracking | Try an option; if it leads to a dead end, undo and try the next |
| BM25 | A formula that scores how well a document matches search words |
| Bigram | Two words taken together, e.g. "machine learning" |
| CDC / DEL / HUEL / OPEL / GIR | Compulsory / discipline elective / humanities elective / open elective / general institute requirement |
| Deterministic | Same input → same output, every time |
| Ingestion | Reading raw data and turning it into structured data |
| LLM | Large language model (Gemini, Claude, Llama…) |
| OCR | Reading text from an image |
| Stemming | Cutting a word to its root: "learning" → "learn" |
| Tool calling | An LLM asking your code to run a function and give back the result |
| Union-find | A quick way to group things that are "the same as" each other |
| Virtual environment (venv) | A private folder of Python packages for one project |
