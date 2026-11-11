from app.config import PROJECT_CONTEXT_TEMPLATE
from app.parsing.projects import parse_project_context

GOOD = """
# Project Context
<!-- ## Project: Hidden in a comment
- Employer: Nobody -->

## Project: Order event pipeline
- Employer: Company A
- Role: Software Engineer
- Start: 2023-04
- End: present
- Team size: 4
- Problem: Batch job made order status stale.
- What I did:
  - Built a Kafka-based event pipeline
  - Wrote consumers with retry: dead-letter handling
- Tech: Python, Kafka
- Results:
  - Latency from ~6 hours to under 5 seconds

## Project: Internal CLI
- Role: Developer
- Start: March 2022
"""


def test_template_example_is_ignored():
    ctx = parse_project_context(PROJECT_CONTEXT_TEMPLATE.read_text(encoding="utf-8"))
    assert ctx.projects == []


def test_fields_and_lists_parsed():
    ctx = parse_project_context(GOOD)
    names = [p.name for p in ctx.projects]
    assert names == ["Order event pipeline", "Internal CLI"]
    p = ctx.projects[0]
    assert p.id == "order-event-pipeline"
    assert p.employer == "Company A" and p.end == "present" and p.team_size == "4"
    assert p.what_i_did == [
        "Built a Kafka-based event pipeline",
        "Wrote consumers with retry: dead-letter handling",
    ]
    assert p.tech == ["Python", "Kafka"]  # comma-separated on one line
    assert p.results == ["Latency from ~6 hours to under 5 seconds"]


def test_issues_reported():
    ctx = parse_project_context(GOOD)
    first = [i for i in ctx.issues if i.project_id == "order-event-pipeline"]
    assert first == []
    cli = {(i.field) for i in ctx.issues if i.project_id == "internal-cli"}
    assert {"employer", "end", "start", "what_i_did"} <= cli  # start has bad format
