---
name: matcher-reword
description: Matcher step 4b. Rewrites given resume bullets (or merges two) into strong XYZ-style bullets for a job description, using only the facts provided with each item. Use when Matcher asks to reword bullets.
---

# Reword bullets

Each ITEM is one bullet to rewrite (or two to merge into one). Write a better version of
the SAME work so it earns more for the job description (JD).

## The one rule

Use ONLY the facts in that item: "current" (and "current2"), and its "facts" lines. Never
add a number, tool, team size, result or responsibility that isn't there. Never upgrade a
verb beyond the facts ("helped with" must not become "led").

## How a strong bullet looks (Google's XYZ pattern)

Accomplished [X] as measured by [Y], by doing [Z]:
- start with a strong past-tense verb: Built, Reduced, Designed, Cut, Migrated, Automated,
  Led (only if the facts say so). Never "Responsible for", "Worked on", "Helped".
- name the JD's own terms for the same thing ("Kafka" not "a message queue") when the facts
  support it, especially the skills in "bring_out".
- include the result or scale if the facts give one (time, %, requests/day, users, money).
- one sentence, at most MAX WORDS words, no filler ("various", "successfully", "in order to").

Examples (facts -> bullet):
- "Moved cron jobs to Celery workers on AWS" + "9 jobs, automatic retries, failure alerts"
  -> "Migrated 9 cron batch jobs to Celery workers on AWS with automatic retries and failure alerts"
- "Worked on payouts API performance" + "p99 2.1s to 450ms at month-end with Redis caching"
  -> "Cut payouts API p99 latency from 2.1s to 450ms at month-end peaks by caching merchant config in Redis"
- "Responsible for code reviews" + "mentored 2 junior engineers, weekly reviews"
  -> "Mentored 2 junior engineers through weekly code reviews and pairing sessions"

## Output

Reply with ONLY a JSON object (no prose, no code fences):
{"items": [{"id": "r1", "new": "the rewritten bullet"}]}
