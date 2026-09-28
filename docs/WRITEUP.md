# BITS Academic Course Recommender: project writeup

Postman Round 2 · solo project · Pilani campus · uses the First Semester 2026-27 timetable
Repository: https://github.com/deejay147/agentic-AI-based-BITS-Academic-Course-Recommender

## 1. What it does

A BITS Pilani student enters their BITS ID and the courses they've done, then asks a question in plain English,
like *"Suggest an AI-related DEL with no midsem"*. The app:

1. works out what the student still needs to graduate,
2. finds which courses in this semester's timetable they are **allowed** to take under the Academic Regulations,
3. answers the question using only those allowed courses.

Every recommendation says which requirement it fills, why the student is allowed to take it, the course details
they asked about (quoted from the handout or timetable), and which document each fact came from.

**The main idea:** all the rule checking is ordinary Python code that gives the same answer every time. The AI
part only helps understand the question, match interests to courses, and word the answer. The AI can't slip in a
course: every course it suggests is checked again by the rules code before the student sees it. The AI is also
optional. Without an API key, a simpler built-in parser does its job, and every feature still works.

**By the numbers**

| | |
|---|---|
| Course descriptions read from the Bulletin | 2,015 |
| Timetable | 719 course rows, 1,557 sections |
| Degree programmes | 28, plus 70 combined dual-degree charts |
| Handouts | 540 files, 399 after removing duplicates |
| Handouts with a clean marks-breakdown table | 287 |
| Minors | 23 |
| Regulation rules used | 14 |
| Items flagged for a human to check | 108 |
| Automatic tests | 41 |

## 2. How it's built

The project has four parts, each in its own folder:

```
PDFs ──► 1. ingest/   read the PDFs once, save clean data
               │
profile ──► 2. engine/  what's left to graduate? what am I allowed to take? does it clash?
               │
question ──► 3. agent/  understand the question, search allowed courses, double-check, explain
               │
          4. app/      the website (Streamlit)
```

1. **Reading the PDFs (`ingest/`).** One command (`python -m ingest.run_all`, about 3 minutes) reads the
   Bulletin, Timetable, Regulations and handouts and saves clean data files and a small database. When a new
   timetable or new handouts come out, you rerun this step and nothing else changes.
2. **The rules engine (`engine/`).** Plain Python, no AI. Given a student, it works out remaining requirements,
   which courses they can take (and why not, for the rest), timetable clashes, and section choices.
3. **The assistant (`agent/`).** Turns the question into a search over allowed courses and writes the answer.
   It uses five "tools": get requirements, find courses, course details, check a plan, and submit the final
   answer.
4. **The dashboard (`app/`).** A sidebar for the profile and five tabs: Requirements, Ask, Plan semester,
   Eligible courses, Data sources.

## 3. Reading the PDFs

### Tables need positions, not just text

The first attempt copied the text out of the timetable PDF. That broke badly: when a course title or instructor
name ran onto a second line, it got attached to the wrong course. So the final version uses the **position of
every word on the page** (with the pdfplumber library). A word's left-right position says which column it belongs
to, and its height on the page says which row. The same idea is used for the Bulletin's two-column pages, which
are cut down the middle and read one column at a time.

Some timetable quirks needed their own fixes: days printed stuck together (`MW` means Monday and Wednesday),
`11 12` meaning the evening hours 11 and 12 rather than hours 1 and 2, cancelled sections, and a list of courses
that are the same course under different codes.

### Handouts

Every instructor writes their handout differently, so there's no single format to follow. The parser looks for
section headings like "Evaluation Scheme" and reads the table under it. It only trusts the table if the marks add
up to about 100%; otherwise it shows the handout's own text instead of numbers. It also pulls out the makeup
policy, attendance policy, recommended background and lecture topics. **Every fact keeps the sentence it came
from**, so the app can quote it. Duplicate handouts were removed, and the one scanned (image-only) handout was
read with OCR.

### When the data isn't clear, flag it

The task says: if something can't be read reliably, mark it for checking instead of guessing. So the pipeline
writes a **validation report** (counts of everything, to spot when something breaks) and a **verification list**
of 108 items:

| What | How many |
|---|---|
| Offered courses with no handout | 53 |
| Timetable courses missing from the Bulletin's descriptions | 24 |
| Timetable courses with no class days/hours listed | 21 |
| Programme structure questions | 8 |
| Other | 2 |

In the app, if a handout doesn't mention something (say, attendance), the answer is *"No specific information
mentioned; contact the Instructor-in-Charge (name)"*. The app only says a course has a property when a sentence in
a source document backs it up.

### The Bulletin sometimes disagrees with itself

For three programmes, the list of compulsory courses (CDCs) and the semester chart don't match. The app goes with
the chart and logs each difference in the verification list.

## 4. The rules engine

### Knowing the student

The BITS ID gives the batch year, the degree (or two degrees for a dual degree), the stream (PS, TS, 2+2
CentraleSupélec…) and the campus. Since the only timetable given is First Semester 2026-27, the app can tell which
year the student is in without asking. Grades are optional: A to E count as passed, and NC, W, I, GA and RC don't
(Regulations 4.11–4.12).

Typing in every course you've done is tedious, so a **Pre-fill** button fills in everything a student in that year
normally has done, from the semester chart. The student then just edits what's different.

### What's left to graduate

Single degree: 3 HUELs, 4 DELs, 5 OPELs. Dual degree: no OPEL requirement, since the DELs of one degree count as
OPELs of the other (Regulation 2.05). If a programme's chart says a different number (Economics needs 6 DELs,
Biotechnology 5), the chart's number is used. Each elective is placed in the first bucket it fits: DEL, then HUEL,
then OPEL. A HUEL can't come from your own department. The app also shows a graduation checklist and progress
towards a minor.

### What you're allowed to take

Every course in the timetable is checked against these rules. Each rule is tied to the regulation it comes from,
so the app can answer "Why can't I take X?" with the exact clause.

| Rule | Regulation |
|---|---|
| You've passed the course's prerequisites | 3.13 |
| For your own CDCs, you've done the earlier-semester ones (the DCA may allow up to two missing) | 3.14 |
| Another programme's CDCs/DELs only after finishing your own years 1–2 (applied strictly) | 3.15(b)(i) |
| Higher-degree courses: only your own discipline, after your 2nd-year CDCs, at most one per semester | 3.15(b)(ii), 2.08 |
| At most 25 units in a semester | 1.01 |
| First-year foundation courses can't be taken as electives | 2.07, 3.18 |
| Some new course codes are only for 2026 admits | Timetable note |
| No clash in classes or exams | 3.19 |

### Timetable intelligence (bonus)

If one section of a course clashes with your timetable, the app tries the other sections before saying no. It
checks lectures, tutorials, labs, midsems and compres. It can also avoid 8 AM classes, keep a weekday free, or
pick the most compact timetable (fewest free hours stuck between classes). The planner tab shows the result as a
week grid.

For exact checks, students can enter which sections they're already in. If they don't, the app only blocks the
times it knows for sure and says which ones it couldn't check.

## 5. The assistant

### It only searches courses you can take

The search tool only looks through the courses the rules engine has already allowed. Before anything is shown,
every suggested course is checked again. It's dropped if:

- the student isn't allowed to take it,
- it doesn't count as what they asked for (e.g. they asked for a DEL and it would be an OPEL),
- the handout says the opposite of what they asked for (e.g. they asked for no midsem and it has one).

### Matching interests to courses

The first version used keyword search only (BM25 plus a synonym list). Testing showed its weakness: "finance"
returned Copywriting, because both texts mention "market". The final version scores each course on three signals:

- **Keywords (BM25), weight 0.4.** How often the student's words and synonyms appear in the course's title,
  description and lecture plan, with rare words counting more.
- **A semantic model trained on the catalogue, weight 0.6.** Latent semantic analysis (LSA): the ~2,100 course texts
  become TF-IDF vectors, and an SVD keeps the 100 strongest directions. Words that appear in the same kind of course
  end up close together. The model learns from BITS's own documents that finance ≈ investors, capital, equity,
  assets. It trains in ~5 s, needs no download or GPU, and is deterministic. Instructor names are removed from its
  vocabulary.
- **The Bulletin's structure.** Courses of a minor or department whose name matches the topic get a boost and a label.

When few allowed courses match, the nearest ones by the model are added as "related", with the words it links to the
topic. When the best matches are courses the student can't take yet, they're shown first as locked cards with the
blocking rule. For example, finance courses for a CS 2nd-year are blocked by Reg 3.15(b)(i).

**Measured, not guessed.** `tests/eval_retrieval.py` uses the Bulletin's 21 minors (with enough offered courses) as
ground truth: the minor's name is the query, and its courses are the answers. Over 582 offered courses, keywords
alone reach precision@5 0.29, recall@10 0.37 and MRR 0.50. With the trained model they reach 0.34, 0.44 and 0.54. The
minors' lists are narrow, so the absolute numbers understate quality. The model size and weights were tuned on this
set, which is small (21 queries), so the gain is indicative, not precise. A re-ranking step that averages the top
results (Rocchio) was tried and dropped because it didn't help consistently.

### Two modes

- **No-key mode (default).** A built-in parser reads the question: which kind of elective, which properties (no
  midsem, no attendance, project-based), time preferences, course codes and topics. Answers use templates. If
  nothing matches everything asked for, it shows the closest options and what each is missing. If the best
  matches exist but the student isn't allowed to take them, it says so and names the rule.
- **AI mode (optional).** With an API key (Google Gemini and Groq have free tiers; Anthropic's Claude is paid),
  a language model reads the question, calls the same tools and writes the answer. If the AI call fails (wrong
  key, no internet, out of quota), the app quietly falls back to no-key mode and says so.

No-key mode became the main mode partway through. The evaluators shouldn't need a paid key to run it, and the
rules are supposed to be checked by code anyway. The AI makes answers read better; it doesn't make them more
correct.

## 6. Key decisions and why

| Decision | Why |
|---|---|
| Read PDF tables by word position | Plain text extraction put wrapped text in the wrong rows |
| When the Bulletin disagrees with itself, follow the semester chart and log it | The chart shows where each course actually sits; nothing is hidden |
| Every course property is yes / no / not mentioned, with a quote | So the app never quietly assumes |
| Rules in plain code; AI answers double-checked | The AI can't recommend a course you can't take |
| Keyword + a small LSA model trained on the catalogue, not a downloaded embedding model | Learns BITS's own vocabulary; offline, deterministic, ~5 s to train; measured against the minors |
| No-key mode first, AI optional | Anyone can run it; if the AI fails, the app still works |
| Support Gemini and Groq as well as Claude | Free options for people without a paid key |
| Work out the student's year from their ID | One less thing to type in, and one less thing to get wrong |
| Use each programme's own elective counts | Some degrees need more DELs than the default |
| Apply Regulation 3.15(b)(i) strictly | Better to be cautious than suggest something you can't register for |
| Commit the processed data, not the PDFs | The app runs straight after cloning, and the repo stays small |
| Streamlit for the website | Quickest way to build a working dashboard in Python |

Other scope choices:

- The 2025-26 curriculum is used for everyone. Older batches see a note saying so, and still get results.
- Pilani campus only.
- "No prerequisites required" is only said when asked.
- When a handout doesn't mention something, the student is pointed to the Instructor-in-Charge.

## 7. Problems we hit and how we fixed them

| Problem | Cause | Fix |
|---|---|---|
| Only 546 course descriptions found instead of ~2,000 | The page was cut in the wrong place when splitting the two columns | Measured the page and cut at the real middle: 2,015 found |
| Some timetable rows had no title | Long titles wrapped onto the next line | Attach the wrapped text to the nearest row above |
| `MW` and `11 12` read wrongly | Days printed stuck together; evening hours look like hours 1 and 2 | Split day letters; special rule for two-digit hours |
| Wrong years for the Chemical Engineering chart | Stray unit numbers were read as semester totals | Only treat large numbers (8+) as totals |
| BBA programme not found | Its heading is an image, not text | Recognise it by its course codes instead |
| CS F111 missing from the general courses | A filter wrongly removed it | Keep the full list of general courses |
| Dual-degree students shown courses too early | The single-degree chart was being used | Use the combined dual-degree charts |
| Handout sections cut short | Numbered table rows looked like headings | Stricter heading detection |
| "No makeup" applied to the whole course | The sentence was only about quizzes | Detect when "no makeup" is about one part only |
| Searching for "NC" matched the word "announced" | The search ignored upper/lower case | Made that search case-sensitive |
| Marks components named "1", "2"… | A serial-number column was read as the name | Skip number-only columns; add a second reading pass. Clean tables went from 192 to 287 |
| "Machine learning" matched almost every course | Every handout mentions "learning outcomes" | Search word pairs, and ignore common filler words |
| Answers said "fills HUEL" when the student asked for an OPEL | The app showed the default bucket | Show the bucket the student asked for |
| Part of the answer went missing in the UI | Text was being cut short | Show that part below the course cards |
| A broken AI connection crashed the check that detects it | The check itself triggered the error | Wrapped it in error handling |

## 8. What we added beyond the task

- A no-key mode where every feature works, plus free AI options (Gemini, Groq) next to Claude.
- When the best matches are blocked, the app says so and names the rule. When nothing matches everything, it shows
  the closest options.
- A semester planner: core courses are auto-filled, and any course can be added. If a course doesn't fit, it says
  which course it clashes with, and courses the student isn't allowed to take are flagged with the rule. The student
  can flip through up to 30 clash-free timetable options (fewest gaps first) and limit which sections they'd accept.
  There's a colour-coded week grid, a month-style exam calendar flagging same-day exams, and CSV/JSON export. Some
  feature ideas came from the DVM timetable tool students already use.
- "Related" answers when few allowed courses mention a topic: pseudo-relevance feedback learns the words typical of
  the topic's best-matching courses anywhere in the timetable, and searches the student's allowed courses with them.
- A dark "galaxy" dashboard theme with one colour per requirement type, used everywhere, and a guided-search mode next
  to chat.
- "Why can't I take X?" with the exact regulation.
- A graduation checklist, progress towards a minor (23 minors), dual-degree charts, and 2+2 CentraleSupélec
  students.
- Students can enter their own sections for exact clash checks.
- "Recommended background" from handouts, shown as advice, not as a hard requirement.
- Pre-fill from the semester chart.
- A validation report, a verification list, and a Data sources tab in the app.
- Real example answers (`docs/examples.md`) and screenshots, both generated by scripts.
- 11 test students covering five single degrees, a failed (NC) course, a minor, an older batch, a 2+2 student,
  and three dual-degree students in years 2–4. One of them is already at 23 units, so almost nothing fits; the app
  has to explain that instead of recommending anyway.

## 9. How it meets the task requirements

| Requirement | How |
|---|---|
| Create and update a profile | Sidebar: BITS ID, pre-fill, edit courses and grades, save and load |
| Computed live, nothing hardcoded | Every answer is computed from the processed data when asked |
| Pre-process documents with sources kept | `ingest/` saves every record with the document and page it came from |
| Flag instead of guessing | Verification list; "could not be verified" in answers |
| Rules checked by code; AI only for understanding and wording | The rules engine has no AI; every AI suggestion is re-checked |
| Use handout details | Marks breakdown, midsem, compre, quizzes, projects, labs, open book, makeup, attendance, topics, instructor |
| Short, clear recommendations | Each card: requirement filled, why you're allowed, requested details with quotes, why it matches, sources |
| New semester without code changes | Rerun one command on the new PDFs |
| Timetable intelligence (bonus) | All clash types, other sections tried, no 8 AM, free day, compact timetable |
| Clean repo with instructions | README, `docs/RUNNING.md`, tests |

## 10. Limitations and what could come next

- 112 of 399 handouts don't have a marks table the parser can trust, so their text is shown instead of numbers. An
  AI-assisted reading pass, checked by a person, could fix most of these.
- Clash checks are only exact if the student enters their sections for courses with more than one section.
- The source documents themselves have gaps: three programmes where the Bulletin disagrees with itself, and 21
  timetable courses with no class times. The app shows these; it doesn't guess.
- Some information isn't in the dataset at all, like the CGPA cutoff for higher-degree courses and which courses
  count for 2+2 students. The app says so.
- Interest matching is measured on only 21 topics, and the minors' lists are a strict stand-in for relevance. A
  labelled set of real student queries would tune it better. A pretrained sentence-embedding model could be tried
  next, keeping the trained LSA model as the offline fallback.
- Pilani and one semester only. Other campuses would need their own timetables and handouts run through the same
  pipeline.
