# Project Context

<!--
HOW TO USE THIS FILE
- This is your experience bank. Matcher writes new resume bullets ONLY from what is here
  and in your resume. If it isn't written here, it will never appear on your resume.
- Add one "## Project:" block per project. Copy the example block below.
- Keep the field names exactly as shown (Employer, Role, Start, End, ...).
- Lists (What I did, Tech, Results) use "- " bullets on the lines below the field name.
- Only write numbers you can defend in an interview. Leave a field empty if you don't know.
- Dates: YYYY-MM (e.g. 2024-03). Use "present" for ongoing work.
- Employer must match a company on your resume, so the bullet lands under that job.
- Lines inside these comment markers are ignored.
-->

## Profile
- Languages:
- Other facts:
- Links:

<!-- Profile = facts about you that aren't a project. Examples:
- Languages:
  - English (fluent)
  - Hindi (native)
- Other facts:
  - AWS Certified Developer, 2024
- Links:
  - https://leetcode.com/u/your-handle
  - https://github.com/your-handle
-->

## Project: Example - Order event pipeline
- Employer: Company A
- Role: Software Engineer
- Start: 2023-04
- End: 2023-11
- Team size: 4
- Problem: Order updates were processed by a nightly batch job, so customers saw stale status for hours.
- What I did:
  - Designed and built a Kafka-based event pipeline to replace the nightly batch job
  - Wrote the consumer services in Python with retry and dead-letter handling
  - Added Grafana dashboards and alerts for consumer lag
- Tech:
  - Python
  - Kafka
  - PostgreSQL
  - Docker
  - Grafana
- Results:
  - Order status latency went from ~6 hours to under 5 seconds
  - Removed a nightly job that failed about twice a month
- Notes: Anything else useful - who used it, scale (requests/day, users), what you owned end to end.
