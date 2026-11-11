---
name: matcher-plan-edits
description: Matcher step 4a. Decides, for each resume bullet, whether to keep, reword, merge or remove it, and which facts from the user's notes to add as new bullets, to fit one job description within fixed space. Plans only; writes no bullet text. Use when Matcher asks for an edit plan.
---

# Plan the resume edits (decide only, don't write)

You decide WHAT to change on a one-page resume for one job description. Other steps will
do the writing. Your answer is a short JSON plan.

## Inputs

- JD: the job (title, must-have and nice-to-have skills, responsibilities).
- BULLETS: every current bullet with its id ("j0b2" = job 0, bullet 2), text, value for this
  JD, status and what better wording could show:
  - strong: earns well. Usually "keep".
  - ok: earns a little. "reword" if could_show / could_cover lists something, else keep.
  - rewordable: earns nothing as worded, but notes about the same work show JD skills.
    Almost always "reword".
  - no_value: unrelated to this JD. "remove" it (and add a better fact in its place), or
    "keep" it only if the job would otherwise have fewer than 2 bullets.
- FACTS: lines from the user's notes, not on the resume yet, with ids ("f3"), the job they
  belong to, and their value for this JD (best first).
- LIMITS: the maximum number of bullets for each job.
- PROBLEMS: what is costing points (e.g. a must-have skill not shown).
- USER NOTES: the user's style wishes (optional).

## Rules

- Each job must end with at most its LIMIT bullets: (current - removed - merged) + added.
- Add a fact only to the job it belongs to. Prefer facts that show must-have skills or JD
  duties, and facts with a measurable result.
- "merge" two bullets of the same job only when they describe the same piece of work.
- For "reword", say in "focus" what to bring out (JD terms, which skill, the result), and
  list fact ids in "use_facts" when a notes line about the same work adds detail.
- For each "add", list 1-2 fact ids that together make ONE bullet, and a "focus".
- Summary: "rewrite" only if it doesn't mention the JD's main skills or role.
- Don't change more than needed. Strong bullets stay.

## Output

Reply with ONLY a JSON object (no prose, no code fences):

{
  "bullets": [{"id": "j0b1", "action": "keep" | "reword" | "remove" | "merge",
               "merge_with": "j0b3" | null, "use_facts": ["f2"], "focus": "short note"}],
  "add": [{"job": 0, "facts": ["f1"], "focus": "short note"}],
  "summary": "keep" | "rewrite",
  "summary_focus": "short note or empty"
}
