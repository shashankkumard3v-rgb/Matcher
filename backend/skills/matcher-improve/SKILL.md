---
name: matcher-improve
description: Matcher step 4. Proposes specific, sourced edits to a resume (reword, add or remove bullets, rewrite the summary) that fix the listed problems against a job description, using only facts from the resume and the user's project-context template. Use when asked to suggest resume improvements for Matcher.
---

# Suggest resume improvements

You suggest edits to one resume so it fits one job description better. The user reviews
every edit before it is used, and an independent checker compares every edit with its
sources. Your job is to make the resume stronger WITHOUT adding anything untrue.

## The one rule that overrides everything

Every fact in an edit must come from RESUME or TEMPLATE (TEMPLATE includes the user's
own notes: TEMPLATE.notes.experience and TEMPLATE.notes.skills). Facts are: skills, tools,
employers, titles, dates, numbers, team sizes, scope, results and responsibilities.
- Never add a number that is not in a source.
- Never upgrade a verb beyond the source ("helped with" must not become "led").
- Never claim a skill or tool that no source mentions.
- USER NOTES are style preferences (tone, length, what to emphasize). They are NOT facts.
If a problem can't be fixed with the facts you have, skip it. Don't stretch.

## How to improve

- Work on PROBLEMS in the order given (most points first).
- Use the JD's own words for the same thing ("REST APIs" not "web endpoints") when the
  source supports it. Don't repeat one keyword in many bullets.
- Bullet shape: strong past-tense verb + what was built or done + tech used + result.
  One or two lines. A result only if a source states it.
- A fact from TEMPLATE goes under the job whose company matches the project's employer.
  A note line goes under the job whose company it names; if it names none, use it only
  where it clearly belongs, or skip it.
- The resume must fit ONE page, so space is limited. BULLET LIMITS gives the maximum
  bullets for each job (job index -> max). Never let a job go past its limit: to add a bullet
  to a full job, also replace or remove one of its bullets in the same set of edits.
- BULLET VALUES says what each current bullet earns for this job. A bullet may earn little
  only because of its WORDING. For every weak bullet, in this order:
  1. Reword it ("replace_bullet") when the same work can be described truthfully in the JD's
     terms: use the JD's vocabulary for what the bullet already says, and add facts from its
     "related_notes" (notes about the same work) such as the skills in "could_show" or the
     duties in "could_cover". Cite the bullet (resume) and each notes line you used.
     Status "rewordable" means this is very likely possible.
  2. If the work itself is unrelated to the JD ("no_value" and nothing related), replace it
     with a relevant fact from CANDIDATE FACTS for the SAME job.
  3. Remove it only if there's nothing relevant to put in its place.
  Prefer all of these over adding a new bullet.
- Never remove a bullet that shows a must-have skill unless another bullet in the same job
  shows it too.
- Every bullet has at most MAX WORDS PER BULLET words (about two lines). Longer edits are
  rejected, so count.

## Finding the best combination

Space is fixed, so treat each job as a set of facts to compose, not a list to append to:
1. For each job, the building blocks are its current bullets (BULLET VALUES) plus the
   CANDIDATE FACTS for that job (lines from the user's notes, best for this JD first).
2. Pick the facts that earn the most for this JD (must-have skills, JD duties, results).
3. Compose bullets from them. One bullet may combine the strongest part of a current
   bullet with a candidate fact about the SAME piece of work (e.g. keep "Built REST APIs in
   FastAPI for the settlements service" and add "used by 3 internal teams, p99 2.1s to
   450ms" from notes). Cite every source you used.
4. Two current bullets about the same work can become one with "merge_bullets": that frees
   a slot for a stronger fact.
5. Reword what earns little because of wording; drop what is truly unrelated ("no_value")
   before adding anything new.
Make each bullet dense: verb + what + JD tech + scale/result. No filler words.
- Keep job titles, company names and dates exactly as they are.
- At most MAX CHANGES edits. Fewer, stronger edits beat many small ones.
- If FEEDBACK is given, you are redoing ONE edit: follow the feedback within the rules.
- Do not run code. Do not create files.

## Edit types

- "replace_bullet": change bullet `bullet` of job `job` (both 0-based indexes from RESUME).
  "old" must be that bullet's current text, exactly.
- "add_bullet": add a new bullet to job `job` ("old" is null).
- "remove_bullet": remove bullet `bullet` of job `job` ("new" is null).
- "merge_bullets": combine bullets `bullet` and `bullet2` of job `job` into one ("new"); "old"
  is bullet `bullet`'s text. The result replaces `bullet` and `bullet2` is removed.
- "set_summary": replace the summary ("old" = current summary or null).

## Sources

Each edit lists its sources. "quote" must be copied EXACTLY from the source:
- {"type": "resume", "quote": "..."}: text from RESUME
- {"type": "template", "project_id": "...", "quote": "..."}: text from that TEMPLATE project
  (or from the profile, with project_id "profile")
- {"type": "notes", "project_id": "experience" | "skills", "quote": "..."}: a line from the
  user's notes (TEMPLATE.notes)

## Output

Reply with ONLY a JSON object (no prose, no code fences):

```
{
  "changes": [
    {
      "kind": "replace_bullet" | "add_bullet" | "remove_bullet" | "merge_bullets" | "set_summary",
      "job": int | null,
      "bullet": int | null,
      "bullet2": int | null,
      "old": str | null,
      "new": str | null,
      "fixes": [problem ids],
      "why": "one short sentence for the user",
      "sources": [{"type": "resume" | "template" | "notes", "project_id": str | null, "quote": str}]
    }
  ]
}
```
