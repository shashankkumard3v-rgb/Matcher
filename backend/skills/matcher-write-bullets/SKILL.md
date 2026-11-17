---
name: matcher-write-bullets
description: Matcher step 4c. Writes new resume bullets (and, if asked, the summary) for a job description from the user's own notes, in the XYZ pattern. Use when Matcher asks to write new bullets.
---

# Write new bullets

Each ITEM gives one or two lines from the user's notes. Turn them into ONE strong resume
bullet for the job description (JD). An item with type "summary" asks for a 1-2 sentence
professional summary instead.

## The one rule

Use ONLY the facts in that item. Never add a number, tool, team size, result or
responsibility that isn't there. Never upgrade a verb beyond the facts.

## How a strong bullet looks (Google's XYZ pattern)

Accomplished [X] as measured by [Y], by doing [Z]:
- start with a strong past-tense verb (Built, Reduced, Designed, Cut, Migrated, Automated).
  Never "Responsible for", "Worked on", "Helped".
- use the JD's own terms for the same thing when the facts support it, especially the
  skills in "bring_out".
- include the result or scale if the facts give one.
- one sentence, at most MAX WORDS words, no filler.

Example: facts "Built a Kafka consumer service in Python that replaced the nightly batch;
payout status delay went from 24 hours to under 1 minute"
-> "Cut payout status delay from 24 hours to under 1 minute by replacing a nightly batch
with a Kafka consumer service in Python"

Summary: who the user is (title, years only if given), their strongest JD-relevant skills
and domain, from the facts given. No first person, no buzzwords, at most 45 words.

## Context you get

JOBS lists each job these items belong to (title, company, dates) with ALL its current bullets.
Each item's "job" says which one. Use it to write for that job's scope and level, and never
repeat what another bullet of that job already says.

## Goal, must_keep and AVOID

- GOAL says what this round is trying to achieve (the gaps, most points first) and what every
  bullet must contain. Write toward it.
- Each item has "must_keep": the numbers and tools from the source. The new bullet must contain
  every one of them, exactly (you may reorder the sentence). Code checks this and rejects a
  bullet that drops one.
- AVOID lists bullets already on the resume. Never write a bullet that says the same thing as
  one of them (other than the item's own "current"). Code checks this too.

## Output

Reply with ONLY a JSON object (no prose, no code fences):
{"items": [{"id": "a1", "new": "the new bullet"}]}
