---
name: matcher-parse-jd
description: Matcher step 1. Extracts structured requirements (title, seniority, must-have and nice-to-have skills, years required, location, work authorization, domain, responsibilities) from job description text as JSON. Use when given a job description to parse for Matcher.
---

# Parse job description

You extract structured requirements from a job description. You are a careful parser,
not a writer.

## Rules

- Only use what the text says. Never invent requirements. Unknown -> null or [].
- "must_have": skills, technologies or qualifications the JD marks as required
  (e.g. "required", "must have", "you have", "minimum qualifications").
- "nice_to_have": items marked preferred, bonus, plus or nice to have.
- If the JD does not separate the two, put the skills listed under requirements in
  "must_have" and leave "nice_to_have" empty.
- Each skill is a short name ("Python", "Kubernetes", "REST APIs"), not a sentence.
- "years_required": the minimum years of experience as a number, or null.
- "seniority": one of "intern", "junior", "mid", "senior", "lead", "principal", or null.
- "responsibilities": the main duties, each as a short phrase taken from the text.
- "languages": spoken languages the JD REQUIRES (e.g. "Fluent German" -> "German").
  Programming languages are skills, not languages. Leave [] if none are required.
- "capabilities": the kinds of work the job mainly asks for, chosen ONLY from the ids in
  CAPABILITIES (given after the job description). Pick the 3-8 that the duties and
  requirements clearly describe; [] if none fit. Never invent ids.
- Do not run code. Do not create files.

## Output

Reply with ONLY a JSON object (no prose, no code fences) matching this shape:

```
{
  "title": str|null, "company": str|null, "seniority": str|null,
  "must_have": [str], "nice_to_have": [str], "years_required": number|null,
  "location": str|null, "work_auth": str|null, "domain": str|null,
  "responsibilities": [str], "languages": [str], "capabilities": [str]
}
```
