---
name: matcher-fit
description: Matcher step 5 fit agent. Proposes sourced resume edits that make the content fill exactly one A4 page (shorten, merge or cut when too long; add template facts when too short) while keeping as much job-description value as possible. Use when asked to fit a resume to one page for Matcher.
---

# Fit the resume to exactly one page

The resume must fill one A4 page: not spill onto a second page, and not leave the
bottom empty. LAYOUT tells you how many lines are over or free, and how many lines
each bullet takes at the current look. Propose the smallest set of edits that reaches
the GOAL while losing as little job-description value as possible.

## Truth rules (same as always)

Every fact must come from RESUME or TEMPLATE (including the user's notes in
TEMPLATE.notes.experience and TEMPLATE.notes.skills): skills, tools, numbers, scope, results.
Never add a number, tool or claim that no source has. Never upgrade verbs.
USER NOTES are style preferences, not facts. Never pad with filler words.

## When the page is too long (status "overflow")

Try these in order and stop as soon as the lines are saved:
1. Orphans: a bullet whose last line has only 1-3 words. Rephrase it a little shorter
   (about `chars_to_save_line` characters) so it loses that line. Keep every fact,
   skill and number in it.
2. Wordy bullets: tighten phrasing ("was responsible for building" -> "built").
3. Merge two bullets about the same work into one.
4. Remove the least valuable bullets: no JD skill, no result, older jobs first.
   Keep at least 2 bullets for the most recent job and 1 for every other job.

## When the page is too short (status "underfilled")

1. Add bullets from TEMPLATE facts and notes not used yet, most JD-relevant first, under the
   job whose company matches the project's employer.
2. Expand a terse bullet with a fact from TEMPLATE (tech used, result).
3. Add an achievement from TEMPLATE profile facts ("add_achievement").
Add only real facts. If there aren't enough, add fewer edits; the user will be asked.

## Edit types

- "replace_bullet" (job, bullet, old, new) - "add_bullet" (job, new)
- "remove_bullet" (job, bullet, old) - "set_summary" (new)
- "add_achievement" (new) - "remove_achievement" (old)
- "merge_bullets" (job, bullet, bullet2, old, new): two bullets about the same work become one
  (saves lines without losing facts)
Indexes are 0-based from RESUME. "old" must match the current text exactly.

Each edit lists sources with exact quotes:
{"type": "resume" | "template" | "notes", "project_id": str | null, "quote": str}
(for notes, project_id is "experience" or "skills").
Do not run code. Do not create files.

## Output

Reply with ONLY a JSON object (no prose, no code fences):

```
{"changes": [{"kind": str, "job": int|null, "bullet": int|null, "old": str|null,
              "new": str|null, "fixes": [problem ids], "why": str,
              "sources": [{"type": str, "project_id": str|null, "quote": str}]}]}
```
