---
name: matcher-profile-resume
description: Matcher library step. Reads ONE resume (and nothing else) and rates how strongly it proves each skill and each capability, on a strict 0-5 evidence scale with an exact quote for every rating. Used when a resume is saved so it can later be matched to job descriptions.
---

# Profile a resume (strict, blind)

You see ONE resume and nothing else: no job description, no target role. Rate only what the
text PROVES. A strict, low rating is always better than a generous one.

## Skills

A skill is a named technology, tool, language, framework, platform or method
(Python, Kafka, PostgreSQL, AWS, Kubernetes, React, Terraform, Scrum...). Include every skill
the resume names anywhere, each once (use its common name: "PostgreSQL", not "postgres db").

Evidence level - pick the HIGHEST level whose rule the quote fully meets:
- 1 = only listed (skills section, a list of technologies, no work shown)
- 2 = used: a bullet shows the skill being used, but not what was built with it
- 3 = built: a bullet shows something concrete was built/implemented WITH this skill
- 4 = owned/designed: the bullet shows the person designed, owned or led the work done with it
- 5 = owned/designed AND a measurable result is stated in the same bullet (a number: %, time,
      volume, money, users)
Rules:
- The skill must be named in the quote itself (for 2-5 the quote must be from a bullet or the
  summary, not the skills list).
- "Worked on", "helped", "involved in", "exposure to", "familiar with" are level 2 at most.
- "Led"/"owned"/"designed"/"architected" only count for 4-5 if those words are in the quote.
- Never infer a skill that isn't written ("microservices" does not imply Kubernetes).

## Capabilities

Capabilities are kinds of work, from the fixed list given in CAPABILITIES. Use ONLY those ids.
Rate a capability only if a bullet (or the summary) clearly shows that kind of work, with the
same 1-5 scale (1 = only claimed in the summary, 2 = took part, 3 = did it, 4 = owned/designed
it, 5 = owned it with a measurable result). Leave out capabilities with no evidence.

## Quotes

Every rating needs "quote": the shortest EXACT span copied from the resume (same words, same
spelling) that proves the level. It is checked by code; ratings whose quote isn't found in the
resume are thrown away.

## Output

Reply with ONLY a JSON object (no prose, no code fences):
{
  "skills": [{"name": "Kafka", "level": 4, "quote": "exact words from the resume"}],
  "capabilities": [{"id": "event_driven", "level": 4, "quote": "exact words from the resume"}]
}
