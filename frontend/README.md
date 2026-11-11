# Matcher app (frontend)

A React web app, already built into `dist/`. The Python server serves it, so you don't
need Node.js to use it:

1. Run `backend\run.bat`.
2. Open http://localhost:8000 (exactly this address) and **Sign in with Google**
   (one-time setup in the main README). Only this laptop can open it for now; phone access
   returns with HTTPS when the server moves to the cloud.

## What's in the app now

- **New**: paste the job description, "Find my best resume": your saved resumes ranked for this job,
  with the must-haves each one proves (dots = evidence level 1-5) or misses. "Use this resume" starts
  the run. The first time (no saved resumes) it asks you to add one right there.
- **Run page**:
  - Score (with a breakdown you can tap open), warnings, what Matcher read from the job and resume.
  - **Step 1 · Is it worth applying?** "Check my chances" shows the score now and the best
    possible score using My info. Answer its questions (answers can be saved to your notes), ignore a
    deal breaker if you know better, then pick a target: **80+, 85+, 90+ or 95+**.
  - **Step 2 · Improve**: each round shows what's costing points and asks up to 5 questions, then
    "Suggest improvements". Accept, reject, edit or ask the AI to revise each suggestion, then apply
    and re-score blind. Change the target any time. **Download this version** works at any moment
    (e.g. you're happy at 80); every version is listed and can be downloaded or continued from.
  - **Step 3 · Your one-page resume**: "View resume" shows the page before you download it;
    an **ATS check** lists what applicant tracking systems care about (one column, contact in the
    body, standard headings, readable dates and bullets, fonts...); **"Check the score again
    (fresh)"** reads the exported file from scratch like an ATS and has the blind judge score it,
    with no earlier scores or edits involved. Word (.docx), PDF preview and report. If it's too long, pick
    bullets to cut; if it's too short or long, "Let AI fit it to one page". "Keep improving" goes back
    to Step 2.
- **Runs**: every run, newest first, with its score and stage.
- **My info**: **Resumes** (upload, see each one's skill levels, rename, notes, download, rebuild,
  delete) and your experience and skills notes. Step 3 has **Save to my resumes**.
- **Auto-refresh**: when the app files change (a rebuild, or an update copied in), any open page
  reloads itself within a few seconds. If you're typing, it shows a "Reload now" banner instead.

## Changing the app (only if you edit the UI)

Needs Node.js 20+ (https://nodejs.org):

```
cd frontend
npm install
npm run dev      # live-reload at http://localhost:5173 (API calls go to :8000)
npm run build    # writes dist/ - the Python server picks it up on refresh
```

Files: `src/App.jsx` (layout, navigation, provider badge), `src/Upload.jsx` (New: JD first), `src/MyResumes.jsx`, `src/Improve.jsx`
(gate, improve loop, export), `src/MyInfo.jsx`,
`src/RunView.jsx` (score + parsed results), `src/RunsList.jsx`, `src/api.js` (talks to the
server), `src/styles.css` (light and dark mode).
