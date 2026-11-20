"""Chat about the finished resume (Step 3): "move Jira to DevOps", "shorten the 2nd
bullet at Gruve", "put the summary in 2 lines".

The AI only turns your message into actions; code checks every action and applies the safe
ones:
- an edit may not bring in a number or a tool that isn't in the resume, your notes, or what you
  wrote in this chat;
- a job keeps at least 2 bullets; ids must exist; skill lines must be one of the known kinds.
Every applied message makes a new version (so you can always go back). Changes that only move
skills between lines or reorder bullets keep the score; anything else is re-scored blind.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app import runs
from app.llm.base import LLMClient, LLMError
from app.parsing.structure import _norm
from app.schemas.models import ChatTurn, JobDescription, Resume

SKILL = "matcher-edit-resume"
LAYOUT_ONLY = {"set_skill_kind", "move_bullet"}
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM.findall(text or "")}


def view(resume: Resume, d: Path | None = None) -> dict:
    from app.export.builder import _kind_of
    from app.export.skillkinds import effective_now
    kinds = effective_now(resume, d)      # the lines as they are on the page now
    return {
        "summary": resume.summary,
        "skills": [{"skill": s, "kind": _kind_of(s, kinds)} for s in resume.skills],
        "experience": [{"job": j, "title": x.title, "company": x.company,
                        "bullets": [{"id": f"j{j}b{b}", "text": t} for b, t in enumerate(x.bullets)]}
                       for j, x in enumerate(resume.experience)],
        "achievements": [{"id": f"a{i}", "text": t} for i, t in enumerate(resume.achievements)],
        "education": [{"id": f"e{i}", **e.model_dump()} for i, e in enumerate(resume.education)],
    }


def _left_off(resume: Resume, jd: JobDescription) -> list[str]:
    from app.export.skillkinds import page_skills, select
    try:
        page = resume.model_copy(deep=True)
        page.skills = page_skills(page, page.skill_kinds, jd)
        return [s for s in resume.skills if s not in select(page, jd)[0]]
    except Exception:
        return []


def _input(resume: Resume, jd: JobDescription, chat: list[ChatTurn], message: str, notes: list[str],
           d: Path | None = None) -> str:
    from app.export.builder import SKILL_KINDS
    return "\n\n".join([
        "JOB: " + " at ".join(x for x in (jd.title, jd.company) if x),
        "KINDS: " + json.dumps(SKILL_KINDS),
        "RESUME:\n" + json.dumps(view(resume, d), ensure_ascii=False, indent=1),
        "LEFT OFF THE PAGE FOR THIS JOB (still saved; add_skill shows one again): " + (", ".join(_left_off(resume, jd)) or "(none)"),
        "YOUR NOTES:\n" + ("\n".join(f"- {n}" for n in notes[:80]) or "(none)"),
        "CHAT:\n" + ("\n".join(f"{t.role}: {t.text}" for t in chat[-8:]) or "(none)"),
        "MESSAGE: " + message,
    ])


def _bullet(resume: Resume, bid: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"j(\d+)b(\d+)", str(bid or ""))
    if not m:
        return None
    j, b = int(m.group(1)), int(m.group(2))
    if j < len(resume.experience) and b < len(resume.experience[j].bullets):
        return j, b
    return None


def _idx(prefix: str, ident: str, n: int) -> int | None:
    m = re.fullmatch(prefix + r"(\d+)", str(ident or ""))
    return int(m.group(1)) if m and int(m.group(1)) < n else None


def apply(resume: Resume, actions: list, said: str, notes_text: str, min_bullets: int = 2
          ) -> tuple[Resume, list[str], list[str], bool]:
    """-> (new resume, done, refused, layout_only). `said` = everything you wrote in the chat."""
    from app.export.builder import SKILL_KINDS
    from app.scoring.synonyms import count_mentions, synonym_table
    r = resume.model_copy(deep=True)
    done, refused = [], []
    layout_only = True
    known = sorted(set(synonym_table()[1].values()))

    def honest(new: str, old: str = "") -> str | None:
        support = "\n".join([old, said, notes_text, *r.skills, *(b for j in r.experience for b in j.bullets)])
        extra = _numbers(new) - _numbers(support)
        if extra:
            return f"it adds number(s) {', '.join(sorted(extra))} that aren't in your resume, notes or messages"
        tools = [k for k in known if count_mentions(k, new) and not count_mentions(k, support)]
        if tools:
            return f"it adds {', '.join(tools)}, which your resume, notes and messages don't mention"
        if len(new.split()) > 40:
            return "it's too long for one bullet"
        return None

    for a in actions if isinstance(actions, list) else []:
        if not isinstance(a, dict):
            continue
        t = a.get("type")
        new = re.sub(r"\s+", " ", str(a.get("new") or "")).strip()
        try:
            if t == "set_skill_kind":
                skill = next((s for s in r.skills if s.lower() == str(a.get("skill", "")).strip().lower()), None)
                kind = next((k for k in SKILL_KINDS if k.lower() == str(a.get("kind", "")).strip().lower()), None)
                if not skill or not kind:
                    refused.append(f"Couldn't move \"{a.get('skill')}\" to \"{a.get('kind')}\": "
                                   + ("it isn't in your Skills." if not skill else f"use one of: {', '.join(SKILL_KINDS)}."))
                    continue
                r.skill_kinds[skill] = kind
                done.append(f"Moved {skill} to {kind}.")
            elif t == "add_skill":
                skill = str(a.get("skill") or "").strip()
                have = next((s for s in r.skills if s.lower() == skill.lower()), None)
                if have:      # saved but left off the page for this job: show it anyway
                    if have.lower() not in {x.lower() for x in r.skill_keep}:
                        r.skill_keep.append(have)
                    done.append(f"{have} will show on the page.")
                    continue
                if not skill:
                    continue
                if not count_mentions(skill, "\n".join([said, notes_text, *(b for j in r.experience for b in j.bullets)])):
                    refused.append(f"Didn't add {skill}: your resume, notes and messages don't mention it. "
                                   "Tell me where you used it and I'll add it.")
                    continue
                r.skills.append(skill)
                r.skill_keep.append(skill)
                layout_only = False
                done.append(f"Added {skill} to Skills.")
            elif t == "remove_skill":
                skill = next((s for s in r.skills if s.lower() == str(a.get("skill", "")).strip().lower()), None)
                if skill:
                    r.skills.remove(skill)
                    r.skill_kinds.pop(skill, None)
                    r.skill_keep = [x for x in r.skill_keep if x.lower() != skill.lower()]
                    layout_only = False
                    done.append(f"Removed {skill} from Skills.")
            elif t == "rename_skill":
                skill = next((s for s in r.skills if s.lower() == str(a.get("skill", "")).strip().lower()), None)
                if skill and new:
                    from app.scoring.synonyms import canonical
                    if canonical(new).lower() != canonical(skill).lower() and not count_mentions(new, said):
                        refused.append(f"Didn't rename {skill} to {new}: that's a different skill.")
                        continue
                    r.skills[r.skills.index(skill)] = new
                    if skill in r.skill_kinds:
                        r.skill_kinds[new] = r.skill_kinds.pop(skill)
                    layout_only = False
                    done.append(f"Renamed {skill} to {new}.")
            elif t in ("edit_bullet", "remove_bullet", "move_bullet"):
                pos = _bullet(r, a.get("id"))
                if pos is None:
                    refused.append(f"There's no bullet {a.get('id')}.")
                    continue
                j, b = pos
                bullets = r.experience[j].bullets
                label = r.experience[j].company or r.experience[j].title or f"job {j + 1}"
                if t == "edit_bullet":
                    why = honest(new, bullets[b]) if new else "it was empty"
                    if why:
                        refused.append(f"Didn't change that bullet at {label}: {why}.")
                        continue
                    bullets[b] = new
                    layout_only = False
                    done.append(f"Rewrote a bullet at {label}.")
                elif t == "remove_bullet":
                    if len(bullets) - 1 < min_bullets:
                        refused.append(f"Didn't remove it: {label} keeps at least {min_bullets} bullets.")
                        continue
                    bullets.pop(b)
                    layout_only = False
                    done.append(f"Removed a bullet at {label}.")
                else:
                    to = max(0, min(int(a.get("position", 0)), len(bullets) - 1))
                    bullets.insert(to, bullets.pop(b))
                    done.append(f"Moved a bullet at {label} to position {to + 1}.")
            elif t == "add_bullet":
                j = a.get("job")
                if not isinstance(j, int) or not (0 <= j < len(r.experience)) or not new:
                    refused.append("Couldn't tell which job to add that bullet to.")
                    continue
                why = honest(new)
                if why:
                    refused.append(f"Didn't add that bullet: {why}.")
                    continue
                r.experience[j].bullets.append(new)
                layout_only = False
                done.append(f"Added a bullet at {r.experience[j].company or r.experience[j].title}.")
            elif t == "edit_summary":
                why = honest(new, r.summary or "") if new else "it was empty"
                if why:
                    refused.append(f"Didn't change the summary: {why}.")
                    continue
                r.summary = new
                layout_only = False
                done.append("Rewrote the summary.")
            elif t in ("edit_achievement", "remove_achievement"):
                i = _idx("a", a.get("id"), len(r.achievements))
                if i is None:
                    refused.append(f"There's no achievement {a.get('id')}.")
                    continue
                if t == "remove_achievement":
                    r.achievements.pop(i)
                    done.append("Removed an achievement.")
                else:
                    why = honest(new, r.achievements[i]) if new else "it was empty"
                    if why:
                        refused.append(f"Didn't change that achievement: {why}.")
                        continue
                    r.achievements[i] = new
                    done.append("Rewrote an achievement.")
                layout_only = False
            elif t == "edit_education":
                i = _idx("e", a.get("id"), len(r.education))
                field = a.get("field")
                if i is None or field not in ("degree", "institution", "details", "start", "end"):
                    refused.append("Couldn't tell which education detail to change.")
                    continue
                old = getattr(r.education[i], field) or ""
                why = honest(new, " ".join(str(v or "") for v in r.education[i].model_dump().values()))
                if why:
                    refused.append(f"Didn't change your education: {why}.")
                    continue
                setattr(r.education[i], field, new or None)
                layout_only = False
                done.append(f"Changed education {field}" + (f" from \"{old}\"" if old else "") + ".")
        except (TypeError, ValueError):
            refused.append(f"Couldn't do \"{t}\".")
    return r, done, refused, layout_only


def _chat_file(d: Path) -> Path:
    from app.export.export import _edir
    return _edir(d) / "chat.json"


def load_chat(d: Path) -> list[ChatTurn]:
    f = _chat_file(d)
    try:
        return [ChatTurn(**x) for x in json.loads(f.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError):
        return []


def _save_chat(d: Path, chat: list[ChatTurn]) -> None:
    _chat_file(d).write_text(json.dumps([t.model_dump() for t in chat], indent=1, ensure_ascii=False),
                             encoding="utf-8")


async def chat(llm: LLMClient, d: Path, message: str):
    """Your message -> AI actions -> code checks -> new version -> rebuilt page."""
    from app.context import notes
    from app.export import export as export_mod
    from app.loop import loop as loop_mod
    from app.loop.bullets import min_bullets
    message = (message or "").strip()
    if not message:
        raise export_mod.ExportError("empty_message", "Say what to change.")
    state = export_mod.load_state(d)
    if state is None:
        raise export_mod.ExportError("no_export", "Build the resume first.")
    ls = loop_mod.load_state(d)
    resume = loop_mod.load_version(d, state.version)
    jd = runs.load_jd(d)
    history = load_chat(d)
    note_lines = [ln for k in ("experience", "skills") for h, ln in notes.entries(notes.read_raw(k)) if h != ln]
    try:
        data = await llm.run_skill(SKILL, _input(resume, jd, history, message, note_lines, d))
    except LLMError as e:
        raise export_mod.ExportError("llm_failed", str(e))
    reply = str((data or {}).get("reply") or "").strip() if isinstance(data, dict) else ""
    said = "\n".join([t.text for t in history if t.role == "you"] + [message])
    new, done, refused, layout_only = apply(resume, (data or {}).get("actions") if isinstance(data, dict) else [],
                                            said, "\n".join(note_lines), min_bullets())
    history.append(ChatTurn(role="you", text=message))
    version = None
    if done:
        note = "Chat: " + "; ".join(done)[:200]
        score = loop_mod._load_score(d, ls, state.version) if layout_only else None
        v = await loop_mod.edit_version(llm, d, new, note, score=score)
        version = v.n
    history.append(ChatTurn(role="ai", text=reply or ("Done." if done else "I didn't change anything."),
                            done=done, refused=refused, version=version))
    _save_chat(d, history)
    if version is not None:
        out = await export_mod.build(d, history=state.history + [f"v{version}: {'; '.join(done)}"], llm=llm)
    else:
        out = state
    out.chat = history
    return out
