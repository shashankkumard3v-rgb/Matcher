"""Suggest edits in small steps, so even small models do each part well.

1. PLAN   (one AI call, or a rule-based plan if that fails): for every bullet decide
          keep / reword / merge / remove, which facts from your notes to add, and whether the
          summary needs rewriting. No writing here, only decisions.
2. WRITE  (two AI calls, run in parallel): one rewords/merges the chosen bullets, one writes
          the new bullets (and the summary). Each item comes with exactly the facts it may use.
3. Removals need no AI. Code then builds the edits with their sources, and the usual checks
   (truth check, word and bullet limits) run on them as before.
"""
from __future__ import annotations

import asyncio
import json
import logging

from app.llm.base import LLMClient
from app.loop import bullets as bullets_mod
from app.parsing.structure import _norm
from app.schemas.models import JobDescription, Resume, Round, ScoreResult

logger = logging.getLogger(__name__)

PLAN_SKILL = "matcher-plan-edits"
REWORD_SKILL = "matcher-reword"
WRITE_SKILL = "matcher-write-bullets"
ACTIONS = {"keep", "reword", "remove", "merge"}


def _bid(j: int, b: int) -> str:
    return f"j{j}b{b}"


def _jd_view(jd: JobDescription) -> dict:
    return {"title": jd.title, "must_have": jd.must_have, "nice_to_have": jd.nice_to_have,
            "responsibilities": jd.responsibilities[:10]}


# ---------- 1. plan ----------

def plan_input(jd: JobDescription, rep: bullets_mod.BulletReport, facts: dict[str, bullets_mod.Candidate],
               r: Round) -> str:
    bl = [{"id": _bid(b.job, b.bullet), "job": b.job, "text": b.text, "value": b.value, "status": b.status,
           "shows": b.shows, "could_show": b.could_show, "could_cover": b.could_cover[:2]} for b in rep.bullets]
    fl = [{"id": fid, "job": c.job, "text": c.text, "value": c.value, "shows": c.shows} for fid, c in facts.items()]
    limits = {j.job: {"job": j.job_label, "now": j.count, "max": j.cap} for j in rep.jobs}
    problems = [{"id": p.id, "label": p.label, "points_lost": p.points_lost} for p in r.problems if p.fix_by != "none"]
    return "\n\n".join([
        "JD:\n" + json.dumps(_jd_view(jd), ensure_ascii=False),
        "BULLETS:\n" + json.dumps(bl, ensure_ascii=False, indent=1),
        "FACTS:\n" + json.dumps(fl, ensure_ascii=False, indent=1),
        "LIMITS:\n" + json.dumps(limits, ensure_ascii=False),
        "PROBLEMS:\n" + json.dumps(problems, ensure_ascii=False),
        "USER NOTES:\n" + ("\n".join(f"- {n}" for n in r.notes) or "(none)"),
    ])


def rule_plan(rep: bullets_mod.BulletReport, facts: dict[str, bullets_mod.Candidate]) -> dict:
    """A sensible plan without AI: reword what wording holds back, remove what's unrelated,
    then fill free slots with the most valuable facts."""
    plan = {"bullets": [], "add": [], "summary": "keep", "summary_focus": ""}
    for b in rep.bullets:
        if b.status == "rewordable" or (b.status == "ok" and b.could_show):
            plan["bullets"].append({"id": _bid(b.job, b.bullet), "action": "reword",
                                    "focus": "bring out " + ", ".join(b.could_show or b.could_cover[:1])})
        elif b.status == "no_value" and not b.protected:
            plan["bullets"].append({"id": _bid(b.job, b.bullet), "action": "remove",
                                    "focus": "unrelated to this job"})
        elif b.redundant:
            full = next((j for j in rep.jobs if j.job == b.job), None)
            if full and full.count >= full.cap:     # only when the job needs the space
                plan["bullets"].append({"id": _bid(b.job, b.bullet), "action": "remove",
                                        "focus": "repeats what other bullets already show"})
    # new facts: most valuable first, your recent job preferred (it counts most for relevance)
    recency = lambda c: c.value * (1.0 if c.job == 0 else 0.85 if c.job == 1 else 0.7)
    for fid, c in sorted(facts.items(), key=lambda kv: -recency(kv[1])):
        if c.value >= 3:
            plan["add"].append({"job": c.job, "facts": [fid], "focus": "shows " + ", ".join(c.shows)})
    return plan


def validate_plan(raw: dict, rep: bullets_mod.BulletReport, facts: dict[str, bullets_mod.Candidate],
                  fallback: dict) -> dict:
    """Keep only valid decisions, never exceed the bullet limits, never empty a job."""
    if not isinstance(raw, dict):
        raw = fallback
    info = {_bid(b.job, b.bullet): b for b in rep.bullets}
    decided: dict[str, dict] = {}
    for d in raw.get("bullets") or []:
        if not isinstance(d, dict) or d.get("id") not in info or d.get("action") not in ACTIONS:
            continue
        if d["action"] == "keep" or d["id"] in decided:
            continue
        if d["action"] == "remove" and info[d["id"]].protected:
            continue        # the only proof of a job skill on the resume
        if d["action"] == "merge":
            other = d.get("merge_with")
            if (other not in info or other == d["id"] or info[other].job != info[d["id"]].job
                    or other in decided):
                continue
            decided[other] = {"action": "merged_into", "into": d["id"]}
        use = [f for f in (d.get("use_facts") or []) if f in facts and facts[f].job == info[d["id"]].job]
        decided[d["id"]] = {"action": d["action"], "merge_with": d.get("merge_with"),
                            "use_facts": use, "focus": str(d.get("focus") or "")}
    # each job keeps at least one bullet
    for j in rep.jobs:
        ids = [_bid(j.job, b) for b in range(j.count)]
        gone = [i for i in ids if decided.get(i, {}).get("action") in ("remove", "merged_into")]
        if ids and len(gone) == len(ids):
            for i in gone:
                if decided[i]["action"] == "remove":
                    decided.pop(i)
                    break
    # adds, within the limit of each job
    room = {}
    for j in rep.jobs:
        ids = [_bid(j.job, b) for b in range(j.count)]
        left = sum(1 for i in ids if decided.get(i, {}).get("action") not in ("remove", "merged_into"))
        room[j.job] = j.cap - left
    used = {f for d in decided.values() for f in d.get("use_facts", [])}
    # a notes line that feeds a reworded/merged bullet must not come back as a new bullet too
    feeding = {_norm(t) for bid, d in decided.items() if d["action"] in ("reword", "merge")
               for t in info[bid].related_notes}
    used |= {f for f, c in facts.items() if _norm(c.text) in feeding}
    adds = []
    for a in raw.get("add") or []:
        if not isinstance(a, dict):
            continue
        fs = [f for f in (a.get("facts") or [])[:2] if f in facts and f not in used]
        if not fs:
            continue
        job = facts[fs[0]].job
        fs = [f for f in fs if facts[f].job == job]
        if room.get(job, 0) <= 0:
            continue
        room[job] -= 1
        used.update(fs)
        adds.append({"job": job, "facts": fs, "focus": str(a.get("focus") or "")})
    return {"bullets": decided, "add": adds,
            "summary": "rewrite" if raw.get("summary") == "rewrite" else "keep",
            "summary_focus": str(raw.get("summary_focus") or "")}


# ---------- 2. write ----------

def _write_input(items: list[dict], jd: JobDescription, word_cap: int, r: Round) -> str:
    return "\n\n".join([
        "JD:\n" + json.dumps(_jd_view(jd), ensure_ascii=False),
        f"MAX WORDS: {word_cap}",
        "USER NOTES:\n" + ("\n".join(f"- {n}" for n in r.notes) or "(none)"),
        "ITEMS:\n" + json.dumps(items, ensure_ascii=False, indent=1),
    ])


async def _run_items(llm: LLMClient, skill: str, items: list[dict], jd, word_cap, r) -> dict[str, str]:
    if not items:
        return {}
    try:
        data = await llm.run_skill(skill, _write_input(items, jd, word_cap, r))
    except Exception as e:  # one failed step shouldn't lose the other step's work
        logger.warning("%s failed: %s", skill, e)
        return {}
    out = {}
    for it in (data.get("items") or []) if isinstance(data, dict) else []:
        if isinstance(it, dict) and it.get("id") and isinstance(it.get("new"), str) and it["new"].strip():
            out[it["id"]] = it["new"].strip()
    return out


async def suggest(llm: LLMClient, resume: Resume, jd: JobDescription, score: ScoreResult | None,
                  r: Round, cfg: dict, found: list | None = None) -> tuple[list[dict], dict]:
    """Returns raw edits (same shape as matcher-improve's "changes") plus the plan used.
    found: requirements your notes show in other words; their notes lines come first as facts."""
    rep = bullets_mod.analyze(resume, jd, score, cfg, found=found)
    facts = {f"f{i}": c for i, c in enumerate(rep.candidates)}
    fallback = rule_plan(rep, facts)
    try:
        raw = await llm.run_skill(PLAN_SKILL, plan_input(jd, rep, facts, r))
    except Exception as e:
        logger.warning("plan step failed, using the rule-based plan: %s", e)
        raw = fallback
    plan = validate_plan(raw, rep, facts, fallback)
    if not plan["bullets"] and not plan["add"] and plan["summary"] == "keep" and raw is not fallback:
        plan = validate_plan(fallback, rep, facts, fallback)   # the AI changed nothing: use the rules

    info = {_bid(b.job, b.bullet): b for b in rep.bullets}
    word_cap = rep.word_cap
    reword, write, edits = [], [], []
    for bid, d in plan["bullets"].items():
        b = info[bid]
        if d["action"] == "remove":
            edits.append({"kind": "remove_bullet", "job": b.job, "bullet": b.bullet, "old": b.text,
                          "why": d["focus"] or "Earns nothing for this job.", "fixes": ["bullets:no_value"],
                          "sources": []})
        elif d["action"] in ("reword", "merge"):
            item = {"id": f"r{len(reword) + 1}", "current": b.text,
                    "facts": list(dict.fromkeys(b.related_notes + [facts[f].text for f in d["use_facts"]])),
                    "bring_out": list(dict.fromkeys(b.could_show + b.shows)), "focus": d["focus"]}
            if d["action"] == "merge":
                o = info[d["merge_with"]]
                item["current2"] = o.text
                item["facts"] = list(dict.fromkeys(item["facts"] + o.related_notes))
                item["bring_out"] = list(dict.fromkeys(item["bring_out"] + o.could_show + o.shows))
            reword.append((item, b, d))
    for a in plan["add"]:
        item = {"id": f"a{len(write) + 1}", "facts": [facts[f].text for f in a["facts"]],
                "bring_out": list(dict.fromkeys(s for f in a["facts"] for s in facts[f].shows)),
                "focus": a["focus"]}
        write.append((item, a))
    if plan["summary"] == "rewrite":
        strong = [b.text for b in sorted(rep.bullets, key=lambda x: -x.value)[:4]]
        write.append(({"id": "s1", "type": "summary", "current": resume.summary or "",
                       "facts": [x for x in [resume.summary or ""] + strong if x],
                       "bring_out": jd.must_have[:6], "focus": plan["summary_focus"]}, None))

    new_r, new_w = await asyncio.gather(
        _run_items(llm, REWORD_SKILL, [i for i, _, _ in reword], jd, word_cap, r),
        _run_items(llm, WRITE_SKILL, [i for i, _ in write], jd, word_cap, r))

    def notes_src(texts):
        return [{"type": "notes", "project_id": "experience", "quote": t} for t in texts]

    for item, b, d in reword:
        text = new_r.get(item["id"])
        if not text or _norm(text) == _norm(b.text):
            continue
        src = [{"type": "resume", "quote": b.text}] + notes_src(item["facts"])
        e = {"kind": "replace_bullet", "job": b.job, "bullet": b.bullet, "old": b.text, "new": text,
             "why": d["focus"] or "Clearer for this job.", "fixes": ["bullets:reword"], "sources": src}
        if d["action"] == "merge":
            o = info[d["merge_with"]]
            e.update(kind="merge_bullets", bullet2=o.bullet, why=d["focus"] or "Combines two bullets about the same work.")
            e["sources"].insert(1, {"type": "resume", "quote": o.text})
        edits.append(e)
    for item, a in write:
        text = new_w.get(item["id"])
        if not text:
            continue
        if a is None:
            edits.append({"kind": "set_summary", "new": text, "why": plan["summary_focus"] or "Leads with this job's skills.",
                          "fixes": ["relevance"], "sources": [{"type": "resume", "quote": q} for q in item["facts"]]})
        else:
            edits.append({"kind": "add_bullet", "job": a["job"], "new": text,
                          "why": a["focus"] or "A relevant fact from your notes.", "fixes": ["bullets:add"],
                          "sources": notes_src(item["facts"])})
    seen, unique = set(), []
    for e in edits:  # the same new text twice is one edit
        key = _norm(e.get("new") or "") or f"{e['kind']}:{e.get('job')}:{e.get('bullet')}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(e)
    return unique, {"bullets": plan["bullets"], "add": plan["add"], "summary": plan["summary"],
                   "used_rules": raw is fallback}
