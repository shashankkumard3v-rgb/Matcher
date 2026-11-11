"""report.md: what changed, why, where each fact came from, and how the score moved."""
from __future__ import annotations

from pathlib import Path

from app import runs
from app.gate import gate as gate_mod
from app.loop import loop as loop_mod


def write_report(d: Path, export_state) -> Path:
    jd = runs.load_jd(d)
    ls = loop_mod.load_state(d)
    gs = gate_mod.load_state(d)
    lines = [f"# Matcher report: {jd.title or 'Role'} at {jd.company or 'company'}", ""]

    if gs:
        lines += ["## Apply gate", "",
                  f"- Resume as uploaded: **{gs.current_score}/100**; best possible with your My info notes: "
                  f"**{gs.ceiling_score}/100**",
                  f"- Verdict: **{gs.verdict}**" + (f" (decision: {gs.decision})" if gs.decision else "")]
        for o in gs.overrides:
            lines.append(f"- Override of `{o.target}` on {o.at}" + (f": {o.reason}" if o.reason else ""))
        lines.append("")

    if ls:
        lines += ["## Score by version (blind scorer)", "", "| Version | Score | Note |", "| --- | --- | --- |"]
        for v in ls.versions:
            mark = " (final)" if v.n == ls.final_version else ""
            lines.append(f"| v{v.n}{mark} | {v.score} | {v.note} |")
        lines.append("")
        for r in ls.rounds:
            accepted = [c for c in r.changes if c.status == "accepted"]
            lines += [f"## Round {r.n} (from v{r.base_version}: {r.score_before}"
                      + (f" -> {r.score_after})" if r.score_after is not None else ")"), ""]
            answered = [q for q in r.questions if q.status == "answered"]
            if answered:
                lines.append("Your answers:")
                lines += [f"- {q.text}\n  - **You:** {q.answer}" for q in answered]
                lines.append("")
            if accepted:
                lines.append("Edits made:")
                for c in accepted:
                    where = c.job_label or "Summary"
                    lines.append(f"- **{c.kind.replace('_', ' ')}** ({where})")
                    if c.old:
                        lines.append(f"  - Before: {c.old}")
                    if c.new:
                        lines.append(f"  - After: {c.new}")
                    srcs = "; ".join(
                        f"{s.type}{':' + s.project_id if s.project_id else ''} \"{s.quote[:90]}\""
                        for s in c.sources if s.verified)
                    if srcs:
                        lines.append(f"  - Source: {srcs}")
                    if c.truth_issues:
                        lines.append(f"  - Notes: {'; '.join(c.truth_issues)}")
                lines.append("")

    e = export_state
    lines += ["## Word file", "",
              f"- Status: **{e.status}**, {e.pages} page(s), page 1 is {e.fill:.0%} full",
              f"- Look: body {e.look['body_pt']}pt, name {e.look['name_pt']}pt, margins "
              f"{e.look['margin_cm']:.2f} cm, line spacing {e.look['line_spacing']:.2f}",
              f"- Measured with: {e.renderer}"]
    lines += [f"- {h}" for h in e.history]
    path = d / "export" / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
