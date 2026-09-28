# How to run the BITS Course Recommender

This guide is for anyone who wants to use the app or check it: students, evaluators, or you on a fresh laptop.
You don't need an API key and you don't need the PDFs. Everything the app uses is already in the repo.

## 1. What you need

- **Python 3.10 or newer.** Check with `python --version` (on some Macs it's `python3 --version`).
  If it's missing, install it from [python.org](https://www.python.org/downloads/). On Windows, tick
  "Add Python to PATH" during install.
- **git** (to clone the repo), or download the repo as a ZIP from GitHub and unzip it.
- About 300 MB of free disk space for the Python packages.

## 2. Install (one time)

Open a terminal (Command Prompt / PowerShell on Windows, Terminal on Mac/Linux):

```bash
git clone https://github.com/deejay147/agentic-AI-based-BITS-Academic-Course-Recommender.git
cd agentic-AI-based-BITS-Academic-Course-Recommender

python -m venv .venv                 # a private Python environment for this project
# activate it:
#   Mac / Linux:          source .venv/bin/activate
#   Windows PowerShell:   .venv\Scripts\Activate.ps1
#   Windows cmd:          .venv\Scripts\activate.bat

pip install -r requirements.txt
```

If PowerShell refuses to run the activate script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or
use Command Prompt instead.

## 3. Start the app

```bash
streamlit run app/app.py
```

Your browser opens at **http://localhost:8501**. If it doesn't, open that address yourself. To stop the app, press
`Ctrl + C` in the terminal.

Next time you only need to `cd` into the folder, activate `.venv`, and run `streamlit run app/app.py`.

## 4. Using it

**Set up a student profile (left sidebar)**

1. Either load a ready-made one: pick `test: cs_2nd_year` (or any other test profile) and click **Load**,
   or type a BITS ID such as `2025A7PS0147P`. The ID fills in the batch, degree(s) and stream.
   - `2025A7PS...` gives a single degree (A7 = B.E. Computer Science)
   - `2024B3A7...` gives a dual degree (M.Sc. Economics + B.E. CS)
   - `2025AACS...` gives a 2+2 CentraleSupélec student
2. Click **Pre-fill courses from my semester chart**. It fills in the courses a student in that year would normally
   have finished, and the courses they're taking now, from the Bulletin's semester chart.
3. Edit anything that's different for you:
   - Remove a course, or set its grade (NC, W, I and RC count as *not cleared*).
   - Add electives you've already done.
   - Change the "Registered this semester" list.
   - Optionally, under **My sections**, pick which lecture/tutorial/lab section you're in. This makes the clash
     check exact.
4. Add a minor, CGPA and interests if you like. Click **Save profile** to keep it (saved to `data/profiles/`).

**Tabs**

| Tab | What you do there |
|---|---|
| Requirements | See what's left: core courses, DELs, HUELs, OPELs, general courses, minor progress, graduation checklist |
| Ask | Type questions in plain English, e.g. *Suggest DELs related to AI*, *I want an OPEL with no attendance requirement*, *Suggest courses with no midsem and a lenient makeup policy*, *I need a HUEL and prefer project-based evaluation*, *can I take CS F317 and GS F232 together?*, *what's left for me?*, *prerequisites of CS F425* |
| Plan semester | Your core courses are filled in for you. Add electives and each is filed as DEL / HUEL / OPEL. Clash-free sections are picked for every course; use **Choose sections** on a course to limit which sections you'd accept. It checks the 25-unit limit and shows your week, an exam calendar and downloads. Preferences: free day, no 8 AM, compact |
| Eligible courses | Every course you can take this semester, and "Why can't I take…?" for any course you can't, with the regulation clause |
| Data sources | How the data was built, what couldn't be verified, and which regulation clauses the rules use |

Every recommendation says which requirement it fills, why you're eligible, the course properties you asked about
(quoted from the handout or timetable), and where each fact came from.

## 5. Optional: turn on the LLM mode

Without a key, the app understands questions with its own rule-based parser. With a key, an LLM does the
understanding, topic matching and wording, using the same tools and the same final checks. Two providers have free
tiers:

- **Google Gemini**: create a key in Google AI Studio ([docs](https://ai.google.dev/gemini-api/docs/openai)).
- **Groq**: create a key in the Groq console ([docs](https://console.groq.com/docs/openai)).
- **Anthropic (Claude)**, paid: [get a key](https://platform.claude.com/docs/en/get-api-key).

There are two ways to use a key:

- **Quick:** in the app's sidebar, open **LLM (optional)**, choose the provider and paste the key. It's kept only for
  that browser session.
- **Permanent:** copy `.env.example` to a file named `.env` in the project folder and fill in one line, e.g.
  `GEMINI_API_KEY=your-key-here`. Restart the app. `.env` is in `.gitignore`, so it never gets committed.

The header under the title shows the active mode, e.g. `agent mode: gemini (gemini-3.8-flash)`. Model names change
over time. If the default one stops working, set `LLM_MODEL` in `.env` to a current model from the provider's
list. If the LLM call fails (wrong key, quota, no internet), the app still answers with the rule-based parser
and says so at the top of the answer.

## 6. Optional: rebuild the data from the PDFs

Only needed if you get a new timetable or new handouts, or want to check the pipeline.

1. Put the files in `data/raw/`:
   ```
   data/raw/bulletin.pdf
   data/raw/timetable.pdf
   data/raw/regulations.pdf        (Academic-Regulations-2023.pdf, renamed)
   data/raw/handouts/*.pdf
   ```
2. Install poppler, which provides the `pdftotext` tool the handout parser uses. Tesseract is optional; it's only
   used for the one scanned handout.
   - Mac: `brew install poppler tesseract`
   - Ubuntu/Debian: `sudo apt install poppler-utils tesseract-ocr`
   - Windows: `conda install -c conda-forge poppler`, or download the poppler binaries and add their `bin` folder to
     PATH. Without `pdftotext` the parser falls back to PyMuPDF, which works but reads handout tables less well.
3. Run:
   ```bash
   python -m ingest.run_all
   ```
   It takes about 3 minutes and rewrites `data/processed/`. Check `data/processed/validation_report.md` afterwards.

## 7. Run the tests

```bash
pytest -q
```

39 tests covering the engine (requirements, eligibility rules, clashes, planner, dual degrees, minors) and the agent
(query parsing, rule mode, and both LLM loops with scripted fake clients). `python -m tests.run_examples` regenerates
`docs/examples.md`.

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| `streamlit: command not found` | The virtual environment isn't active. Activate `.venv` (step 2), or run `python -m streamlit run app/app.py` |
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` inside the activated `.venv` |
| Port 8501 already in use | `streamlit run app/app.py --server.port 8502` |
| Answer starts with "LLM unavailable" | The key or model is wrong, or you're over the quota. The answer below it is still valid (rule-based) |
| "No eligible course matches" | Usually genuine: check the listed reasons (unit cap, a clash, clause 3.15). Try without some filters |
| Changes to code don't show up | Stop the app with `Ctrl + C` and start it again |
