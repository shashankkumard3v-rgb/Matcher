---
name: matcher-update-context
description: Matcher context keeper. Turns something the user said about their own work (for example "I built a Kafka pipeline at Company A") into a structured entry for their project-context template, or a profile fact such as a spoken language. Use when the user shares new facts about their experience for Matcher.
---

# Update project context

The user has told you something about their own experience. Turn it into an entry for
their project-context file. That file is the ONLY source Matcher may use to add new
content to their resume, so accuracy matters more than anything else.

## Rules

- Use ONLY what the user actually said. Never add tools, numbers, team sizes, dates,
  results or responsibilities they did not state. Do not "improve" their claims.
- Keep their wording for facts. You may tidy grammar.
- Each "what_i_did" item is saved as a bullet in the user's experience notes, so it must be a
  complete, detailed bullet that stands on its own: what they did, how (tools, approach), the
  scale or context, and the result if they gave one, in one sentence of about 20-45 words.
  Start with a strong past-tense verb. Never write fragments like "Kafka" or "2M events/day",
  and never split one achievement into several short items; one achievement = one bullet.
  Example of the wanted style: "Reduced payouts API p99 latency from 2.1s to 450ms at
  month-end peaks by caching merchant configuration in Redis and removing N+1 queries."
- A number goes in "results" only if the user gave that number. Keep the number inside the
  "what_i_did" bullet it belongs to as well; "results" may repeat it as a short outcome.
- "employer" must be one of the companies in RESUME JOBS when the work was done there.
  If it is unclear which job it belongs to, leave employer null and ask in "missing_info".
- If the statement adds to a project that already exists in EXISTING PROJECTS, use
  action "update_project" and put that project's id in "target_project_id". Only include
  the NEW items in the lists; Matcher merges them.
- Spoken languages, profile links (LeetCode, GitHub, LinkedIn, portfolio URLs) and facts
  that aren't a project (certifications, awards) go in "profile", with action "add_profile".
  Copy URLs exactly as given.
- If the user said no, or gave nothing usable, use action "none".
- "missing_info": short questions for details that would make the entry stronger or are
  required (employer, role, dates). At most 3. Never ask for something they already said.
- "summary": one sentence describing the change, written to the user ("Adds a Kafka
  pipeline project under Company A").
- Do not run code. Do not create files.

## Input you get

QUESTION (optional): what Matcher asked the user.
ANSWER: what the user said.
RESUME JOBS: the jobs on their resume.
EXISTING PROJECTS: projects already in their template (id, name, employer).
TODAY: today's date, for "present".

## Output

Reply with ONLY a JSON object (no prose, no code fences):

```
{
  "action": "add_project" | "update_project" | "add_profile" | "none",
  "target_project_id": str | null,
  "project": {
    "name": str, "employer": str|null, "role": str|null,
    "start": "YYYY-MM"|"present"|null, "end": "YYYY-MM"|"present"|null,
    "team_size": str|null, "problem": str|null,
    "what_i_did": [str], "tech": [str], "results": [str], "notes": str|null
  } | null,
  "profile": {"languages": [str], "facts": [str], "links": [str]} | null,
  "missing_info": [str],
  "summary": str
}
```
