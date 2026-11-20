---
name: matcher-sort-skills
description: Matcher helper. Puts each skill Matcher doesn't recognise into one of a fixed set of resume Skills lines (Languages, Frontend, Backend, Databases, Cloud, DevOps, Engineering, AI Tools, Soft Skills). Use when Matcher asks which line a skill belongs on.
---

# Sort skills into resume lines

You get KINDS (the only allowed lines) and SKILLS (names Matcher didn't recognise). For each
skill, pick the ONE kind a recruiter would expect it under:

- Languages: programming/query languages (Go, Golang, Rust, PHP, SQL, Bash).
- Backend: server frameworks, API styles, messaging, architecture (Gin, Fastify, NestJS, gRPC, Kafka, System Design).
- Frontend: UI frameworks, styling and browser tooling (Svelte, Tailwind, Vite).
- Databases: databases, data stores, ORMs (Firestore, Supabase, Prisma).
- Cloud: cloud platforms and their services (AWS, GCP, Cloud Run, Lambda, Vercel).
- DevOps: containers, CI/CD, build and infra tools, monitoring (Docker, Kubernetes, Bazel, Helm, Terraform).
- Engineering: testing tools, practices and other dev tools (Testify, Playwright, Git, Jira).
- AI Tools: AI assistants, models, LLM frameworks and AI engineering (Cursor, Copilot, ChatGPT, Claude, LangChain, MCP, RAG).
- Soft Skills: people skills (mentoring, communication).

Use the skill's real meaning ("Go (Golang)" is a language). Never rename or drop a skill.

## Output

Reply with ONLY a JSON object (no prose, no code fences):

{"kinds": [{"skill": "<exactly as given>", "kind": "<one of KINDS>"}]}
