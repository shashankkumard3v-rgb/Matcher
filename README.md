# Matcher

Scores your resume against a job description, decides whether to apply, improves the
resume (using only facts from your resume and your project context) until it scores
85+, and exports an A4 Word resume.

Full plan: https://claude.ai/artifact/C7BEcbaFZK9out9R4C7YVe

## Stack

| Part | Tech | Where it runs |
| --- | --- | --- |
| Engine + API | Python, FastAPI | Your laptop now, a cheap cloud host later |
| LLM | Claude (Agent Skills), Gemini, OpenRouter or any OpenAI-compatible API (prompts), chosen by which key is in `.env` | Cloud API |
| App | React PWA (later phase) | Phone (Android, "Add to Home screen") + browser |

## Status

| Phase | What | State |
| --- | --- | --- |
| 1 | Parsers, schemas, project context, `/api/parse` | Done |
| 2 | Blind scorer, `/api/runs/{run_id}/score` | Done |
| 3 | Interactive apply gate, `/api/runs/{run_id}/gate/...` | Done |
| 4 | Interactive improve loop (text only), `/api/runs/{run_id}/loop/...` | Done |
| 5 | One-page A4 Word export, `/api/runs/{run_id}/export/...` | Done |
| 6 | Phone/desktop app: upload + score screens (`frontend/`) | In progress |

## Run it (Windows)

1. Install Python 3.11+ from python.org (tick "Add Python to PATH").
2. Double-click `backend\run.bat`. The first run sets everything up and creates `backend\.env`.
3. Open `backend\.env` and paste at least one key, then run `run.bat` again.
   Matcher uses the first one it finds, in this order (change it in `config\models.yaml`):
   - `ANTHROPIC_API_KEY` (https://platform.claude.com) -> Claude, steps run as **Agent Skills**
   - `GEMINI_API_KEY` (https://aistudio.google.com) -> Gemini, steps run as **prompts**
   - `OPENROUTER_API_KEY` (https://openrouter.ai/keys) -> many models, steps run as **prompts**
   - `OPENAI_API_KEY` (+ `OPENAI_BASE_URL` for Groq, Ollama, ...) -> steps run as **prompts**
     (set the model names under `openai:` in `config\models.yaml` first)
   Use a paid key for your real resume: free tiers may use what you send to train models.
4. For the Word export, Microsoft Word (installed with Office) is used to measure the page.
   No Word? Install LibreOffice (free) instead.
5. Set up **Sign in with Google** and the database once (next section), then open
   **http://localhost:8000** (exactly this address) and sign in. See `frontend/README.md`.
6. The full API (gate, loop, export) is at http://localhost:8000/docs
   - `POST /api/parse` with your resume PDF and a JD -> copy the `run_id`
   - `POST /api/runs/{run_id}/score` -> the score, broken down by dimension
   - `POST /api/runs/{run_id}/gate` -> apply / don't apply, questions, explanation
   - `POST /api/runs/{run_id}/gate/decide` with `{"decision": "proceed"}` -> then the loop below

Only this laptop can open Matcher (the server listens on 127.0.0.1). Phone access comes back
with HTTPS when the server moves to the cloud: Google sign-in doesn't work on http://<IP> addresses.

## Sign in with Google + MongoDB (one-time setup)

Everyone signs in with Google; each account sees only its own data. Stored in MongoDB Atlas
(database `MONGODB_DB`, default `matcher`): users and sign-in sessions, your My info notes
(experience, skills, project template, with the last 10 versions kept), and your saved resumes
(with their original files). Runs are still files in `backend\users\<your user id>\runs\`.
Your old notes files are copied into the database the first time you sign in.

1. **MongoDB Atlas** (free): https://cloud.mongodb.com -> create a free cluster ->
   *Database Access*: add a user with a password -> *Network Access*: add your current IP ->
   *Connect* -> *Drivers*: copy the `mongodb+srv://...` string and put your password in it.
   In `backend\.env`: `MONGODB_URI=mongodb+srv://user:password@cluster0.xxxxx.mongodb.net/`
2. **Google sign-in**: https://console.cloud.google.com -> create a project ->
   *APIs & Services* -> *OAuth consent screen*: External, app name "Matcher", your email ->
   *Credentials* -> *Create credentials* -> *OAuth client ID* -> *Web application*.
   *Authorized JavaScript origins*: `http://localhost:8000` **and** `http://localhost`, plus
   `http://localhost:5173` if you use the live-reload dev server (`npm run dev`).
   Copy the *Client ID* into `backend\.env`: `GOOGLE_CLIENT_ID=1234-abc.apps.googleusercontent.com`
   (While the consent screen is in "Testing", add the Google accounts that may sign in as test users.)
3. Double-click `backend\check_setup.bat`: it checks every setting and connects to MongoDB,
   and tells you exactly what's wrong (it never shows passwords or keys).
4. Close the server window, double-click `run.bat` (it installs new packages by itself) and open
   http://localhost:8000. The sign-in screen also shows if the database can't be reached.

Tests: double-click `backend\run_tests.bat`.

**Auto-restart (like nodemon).** The server restarts by itself when files in `backend\app`,
`backend\config` or `backend\skills` change. Saved runs and your notes don't trigger a restart.
Because the project is in OneDrive (which doesn't always announce file changes), it checks the
files by polling. Manual command with the same behaviour, from the `backend` folder:

```
.venv\Scripts\python.exe serve.py
```

If the server ever misses an update anyway, the app shows **"Restart the server"** at the top:
close the server window and start `run.bat` again.

The web app (`frontend\dist`) needs no restart at all: refresh the browser (Ctrl+F5).

## Step instructions = skills (work with every provider)

Every step's instructions live in `backend\skills\<name>\SKILL.md` (Claude skill format).
Edit those files to change how a step behaves - no code changes needed. The same files are
used whichever AI you run:

| Provider | How a skill runs |
| --- | --- |
| Claude | Uploaded to your Anthropic account as a real Agent Skill (new version whenever you edit it). If your account doesn't have Skills/code execution enabled, Matcher automatically sends the SKILL.md as the prompt instead (`claude.agent_skills: auto / on / off`) |
| Gemini, OpenRouter, OpenAI-compatible | `SKILL.md` + every other text file in the skill's folder (references, examples) is sent as the prompt. Scripts in a skill folder can only run on Claude |

Everything else - the loops, blind scoring, truth checks, gate, Word export - is Matcher's
own code and works identically on every provider.

- `GET /api/skills` shows the active provider, which providers are available, and how skills run.
- `POST /api/skills/sync` (Claude only) uploads changed skills right away.
- Models per step and per provider: `backend\config\models.yaml`.
- A run locks the provider and model on its first score. Switching provider mid-run is
  refused (HTTP 409): different AIs judge differently, so start a new run instead.

## How the improve loop works (design rule)

The improve step is an agentic loop: rewrite -> score -> check -> rewrite ... until the
criteria are met (score >= 85 and the truth check passes) or a stop rule fires.
Each phase in that loop is independent:

- The **scorer is a blind judge**. Every pass is a fresh call with the same skill version,
  same model and same rubric. It only sees the JD and the resume as it stands now. It never
  sees that the resume was rewritten, the pass number, earlier scores, the diff or the
  improver's notes. Revision 4 is scored exactly like the original upload.
- The **improver never grades itself**. Its own opinion of its work is ignored.
- **Code, not the AI, decides** whether to continue or stop, using the scorer's number and
  the stop rules in `config/weights.yaml`.
- Skill versions and models are **pinned for the whole run**, so editing a SKILL.md
  mid-run can't change how that run is judged.

## How scoring works (100 points)

| Dimension | Points | How |
| --- | --- | --- |
| Must-have skills | 25 | Code. Full credit only if a job/project bullet shows it used; only listed (skills or summary) = half; >4 mentions = stuffing penalty |
| Nice-to-have skills | 10 | Same rules (full points if the JD lists none) |
| Title alignment | 8 | Code: words of your 2 latest titles vs the JD title (seniority words ignored) |
| Years of experience | 7 | Code: total years from your job dates (overlaps merged) vs the JD minimum |
| ATS format | 5 | Code: 5 checks (contact, readable dates, skills section, one column, no stray text) |
| Experience relevance | 20 | AI (`matcher-score` skill), level 0/25/50/75/100 |
| Impact and numbers | 10 | AI |
| Seniority and scope | 10 | AI |
| Domain fit | 5 | AI |

The AI must quote the resume word for word for any level above 0; quotes that aren't in the
resume are thrown out, and a dimension with no valid quote scores 0.
Synonyms (k8s = Kubernetes ...) are in `config/skills_synonyms.yaml`; weights in `config/weights.yaml`.

Each run saves `pins.json` the first time it's scored (skill version, model, weights, synonyms).
Later scores in that run must use exactly the same settings, or scoring refuses (HTTP 409).

## The apply gate (interactive)

`POST /api/runs/{run_id}/gate` scores your resume as uploaded AND a "best possible"
version (your resume + every fact in your template, added word for word) with the same
blind scorer. The verdict uses the best possible score:

| Best possible score | Verdict |
| --- | --- |
| 85+ | APPLY |
| 65-84 | APPLY_WITH_RISK |
| under 65, or any active knockout | DONT_APPLY (with a full explanation) |

Knockouts: more than 1 required skill missing everywhere; years under 70% of the
requirement; a required spoken language not on your resume.

It asks you about: missing required skills, skills that are only listed, years short of
the requirement, and required languages ("This is required and your resume doesn't
mention it. Do you want to add German?"). Location and work permit are shown as notes only.

| Endpoint | What it does |
| --- | --- |
| `POST .../gate/answer` | Answer a question -> a proposed template change (shown before/after) |
| `POST .../gate/context` | Tell it anything else about your work -> a proposed change |
| `POST .../gate/proposals/{id}` | Accept (writes your template, keeps a .bak) or reject |
| `POST .../gate/skip/{question_id}` | Skip a question |
| `POST .../gate` | Recompute after accepting changes |
| `POST .../gate/override` | Switch off a knockout or the whole verdict (with a reason) |
| `POST .../gate/decide` | `proceed` or `stop`; proceeding past DON'T APPLY needs a reason |

Proposed changes keep only what you said: any item with a number you didn't say is
removed. Everything you answer, accept or override is saved in the run's `gate.json`.

## The improve loop (interactive, text only)

No documents are made here - it's all JD vs resume text. Every version is kept
(`runs/<id>/versions/resume_vN.txt` + its score), so you can roll back any time.

Each round:

1. **Diagnose** - problems ranked by points lost, tagged `ai` (the facts exist, a rewrite
   can fix it), `you` (facts are missing) or `none` (titles, real years: can't be rewritten).
2. **Ask** - up to 5 questions on the biggest `you` problems. Answer, skip, or add any
   other context; answers become template proposals you accept or reject.
   Add style notes ("keep bullets short") with `/loop/notes`; they're never used as facts.
3. **Suggest** - the AI proposes edits. Each shows old -> new, which problem it fixes,
   and its sources (resume / template quotes).
4. **Truth check** - code blocks any edit whose quotes don't exist or that adds a number
   or skill its sources don't have. The AI checker flags overstatement ("helped" -> "led")
   as a warning you can see.
5. **Review each edit** - `accept`, `reject`, `edit` (your own words) or `revise`
   (the AI redoes just that edit using your feedback).
6. **Apply** - accepted edits make a new version; the blind scorer re-scores it.
   You get advice flags (target reached, score dropped, plateau, max rounds) and choose:
   next round, roll back, or finish.

| Endpoint (under `/api/runs/{run_id}/loop`) | What it does |
| --- | --- |
| `POST /start` | Begin (needs the gate decision `proceed`); opens round 1 |
| `GET /` , `GET /versions/{n}` | Current state; a version's text + score |
| `POST /answer`, `/skip/{qid}`, `/context` | Questions and your own context |
| `POST /proposals/{id}` | Accept/reject a template change |
| `POST /notes` | Style input for this round |
| `POST /suggest` | Get suggestions |
| `POST /changes/{id}` | `{"action": "accept" / "reject" / "edit" / "revise", "text", "feedback"}` |
| `POST /apply` | New version + blind re-score |
| `POST /next`, `/rollback`, `/finish` | Your call after each round |

## The Word file (one full A4 page)

After `POST .../loop/finish`, `POST /api/runs/{run_id}/export` builds the resume:
Summary, Skills, Experience, Achievements (achievements + certifications), contact at the
top. One column, no tables or icons, real Word bullets, dates right-aligned.

**One full page, every time.** Matcher renders the file, measures how full the page is,
and adjusts font size (10-11.5pt), spacing and margins (1.27-2.0 cm) until page 1 is
93-99% full. It measures with Microsoft Word if installed (exact), else LibreOffice.

| Result | What happens |
| --- | --- |
| `fitted` | Done. `GET .../export/file/docx` downloads it |
| `overflow` | It lists bullets to cut, least useful first (pre-selected); `POST .../export/trim` with your picks -> new version, blind re-score, rebuild |
| `underfilled` | It says how many lines are free and which template facts aren't used; `POST .../export/reopen-loop` goes back to improving with that note |

**Fit agent (agentic, interactive).** For `overflow` or `underfilled`, `POST .../export/fit/start`
opens a fit round. The agent looks at the real page layout (lines per bullet from the actual
font widths), finds cheap wins (a bullet that puts 1-3 words on its own line: trim a few
characters, save a whole line, lose no facts), and asks you for more content when the page
is short (up to 5 questions, e.g. more work at a job, or achievements). Then use the normal
loop endpoints: `/loop/answer`, `/loop/context`, `/loop/notes`, `/loop/suggest`,
`/loop/changes/{id}` (each edit shows how many lines it adds or saves, and goes through the
same truth checks). `POST .../export/fit/apply` makes a new version, blind re-scores it,
rebuilds the Word file and measures again. Repeat until it fits.

**Links.** Profile links show as short clickable labels: LeetCode, LinkedIn, GitHub, ...
They come from your resume and from `- Links:` in the Profile block of your template.

Also: `GET .../export/file/pdf` (preview) and `GET .../export/file/report` (every change,
its source, and the score of each version). Look settings: `config/format_spec.yaml`.

## My resumes + quick match (JD first)

1. **My info -> Resumes**: upload a resume PDF (name + optional notes). The first time, the New
   page asks for one too. After improving a resume, **Step 3 -> Save to my resumes** keeps that version.
2. Saving builds the resume's **description** (skill `matcher-profile-resume`, AI, blind: it sees only
   the resume). Every skill gets an evidence level with an exact quote from the resume:
   1 listed, 2 used, 3 built with it, 4 owned/designed, 5 owned with a measured result. Code makes it
   strict: a quote not in the resume is dropped; skills-list only = 1; summary only = max 2; no
   number = max 4; no "led/owned/designed" = max 3. Last used and years come from the job dates.
   Kinds of work ("capabilities") come from the fixed list in `backend/config/capabilities.yaml`.
3. **New**: paste the job description -> every saved resume is ranked (code only, same answer every
   time): must-have skills 55, nice-to-haves 10, kind of work 20, years 8, title 7; skills not used
   lately count less. Tap "Why this score", then **Use this resume**: the run starts from that saved
   resume (no re-upload) and everything after (gate, improve, export) works as before.
4. Rename, add notes, download, rebuild the description or delete any saved resume at any time
   (runs that used it are kept).

## Your info: notes + project template

Matcher only ever uses facts from your resume and these three notes (in the app under **My info**;
stored per account in MongoDB once you're signed in; the files in `backend\context\` are the
starting point and are used by the tests):

| File | What it's for |
| --- | --- |
| `experience.txt` | Plain-text notes on your work, in as much detail as you like. Start each job with a `Company: <name>` line; everything under it belongs to that job. Unfilled `[placeholders]` are ignored. |
| `skills.txt` | One skill per line: skill \| where you used it \| what you did with it \| level. `##` headings to group them are optional. |
| `project_context.md` | The structured version (one `## Project:` block per project). The AI adds entries here when you answer its questions. |

Lines starting with `#` in the `.txt` files are comments (each file explains itself at the top).
In the app you can also upload a `.txt` file into either notes file (add to the end, or replace).
All three count as your facts: the rewrite may use them, the truth check accepts quotes from them,
and the gate counts them toward your best possible score.

## Your project context

Edit `backend\context\project_context.md` (created from the template on first run).
One `## Project:` block per project; see the comments at the top of the file.
The `Example` project is ignored. `Employer` must match a company on your resume.

## Folders

```
backend/
  app/
    main.py              FastAPI app
    api/routes.py        HTTP endpoints
    llm/factory.py       picks the provider (auto order: claude, gemini, openrouter, openai)
    llm/claude.py        Claude: Agent Skills (upload/version), falls back to prompts
    llm/openai_compat.py Gemini, OpenRouter, any OpenAI-compatible API: SKILL.md as prompt
    skills/loader.py     reads + validates SKILL.md files
    parsing/
      extract.py         PDF/DOCX text (no LLM), scanned + two-column checks
      projects.py        project_context.md parser + checks (no LLM)
      structure.py       LLM -> JSON, then verifies every bullet exists in the PDF
      pipeline.py        Step 1: writes a run folder
    scoring/
      synonyms.py        whole-word skill matching + synonym map
      deterministic.py   the 55 code-scored points
      llm_rubric.py      the 45 AI points, evidence quotes verified
      render.py          one canonical text view of a resume (same for every version)
      scorer.py          Step 2 blind judge + run pins
    context/updater.py   your answers -> proposed template changes (accept to save)
    gate/ceiling.py      resume + template = best possible resume
    gate/gate.py         Step 3: verdict, knockouts, questions, overrides, decision
    loop/diagnose.py     score -> problems (ai / you / none) + up to 5 questions
    loop/truth.py        code + AI truth checks on every suggested edit
    loop/loop.py         Step 4: rounds, review, apply, blind re-score, rollback, finish
    export/builder.py    ATS-safe A4 .docx from a resume version
    export/render.py     .docx -> PDF with Word or LibreOffice, then measures the page
    export/fit.py        searches font/spacing/margins to fill exactly one page
    export/layout.py     lines per bullet from real font widths (orphan words, lines to save)
    export/export.py     Step 5: fitted / overflow (trim) / underfilled (reopen loop)
    export/report.py     report.md
    runs.py              read/write files in a run folder
    schemas/models.py    resume / JD / project shapes
  skills/                one folder per step: SKILL.md instructions
  config/                models.yaml (provider + models), weights.yaml
  context/               YOUR project_context.md
  templates/             project context template
  runs/                  one folder per JD + resume run
  tests/
frontend/                phone app (later)
```
