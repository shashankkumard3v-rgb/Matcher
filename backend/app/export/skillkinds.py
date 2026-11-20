"""Which Skills line each skill goes on, in this order:

1. your own choice in the resume chat ("move Jira to DevOps")  -> resume.skill_kinds
2. what YOUR skills notes say: a line like "Backend: Gin, Fastify" or "Database: Firestore"
   puts those skills on that line (the label is matched to a kind: Backend, Database(s),
   Cloud -> Cloud, Build Tools / CI/CD -> DevOps, Testing / Architecture -> Engineering ...)
3. Matcher's own lists (builder.known_kind)
4. a skill none of these knows is checked ONCE by the AI and remembered for this run
5. still unknown -> Engineering
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app.schemas.models import Resume

SKILL = "matcher-sort-skills"
log = logging.getLogger("matcher.skills")

# notes label -> kind; first match wins (checked in this order)
_LABEL_RULES = [
    ("Soft Skills", ("soft", "leadership", "communication", "people")),
    ("Languages", ("language",)),
    ("Frontend", ("frontend", "front-end", "front end", "ui")),
    ("Databases", ("database", "db", "storage", "data store")),
    ("Cloud", ("cloud",)),
    ("DevOps", ("infrastructure", "devops", "ci/cd", "deployment", "build tool", "container")),
    ("Backend", ("backend", "back-end", "back end", "api", "server", "framework")),
    ("AI Tools", ("ai", "llm", "llms", "genai", "agent", "agents", "ml", "model", "models", "copilot", "prompt")),
    ("Engineering", ("test", "architecture", "engineering", "concurrency", "caching", "performance",
                     "integration", "validation", "workflow", "scheduling", "resilience", "nlp",
                     "tool", "monitoring", "security", "secret", "image", "ml", "model")),
]
_LINE = re.compile(r"^\s*(?:[-*•]\s*)?([A-Za-z][A-Za-z &/+().-]{1,40}?)\s*:\s*(.+)$")


def kind_for_label(label: str) -> str | None:
    words = set(re.split(r"[^a-z/+-]+", label.lower()))
    low = label.lower()
    for kind, keys in _LABEL_RULES:
        for k in keys:
            if (" " in k or "/" in k or "-" in k) and k in low:
                return kind
            if k in words or any(w.startswith(k) and len(k) >= 4 for w in words):
                return kind
    return None


def notes_kinds(text: str) -> dict[str, str]:
    """{skill (lower case): kind} from labelled lines in your notes. A skill listed under
    several labels takes the label used most."""
    votes: dict[str, dict[str, int]] = {}
    for line in (text or "").splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        kind = kind_for_label(m.group(1))
        if not kind:
            continue
        for item in re.split(r",|;", m.group(2)):
            item = item.strip().strip(".")
            if not item or len(item) > 40:
                continue
            for name in {item, re.sub(r"\s*\(.*?\)\s*", " ", item).strip()}:
                v = votes.setdefault(name.lower(), {})
                v[kind] = v.get(kind, 0) + 1
    return {k: max(v, key=v.get) for k, v in votes.items()}


def _cache(d: Path) -> Path:
    return d / "export" / "skill_kinds_ai.json"


def _load_cache(d: Path | None) -> dict[str, str]:
    if d is None:
        return {}
    try:
        return json.loads(_cache(d).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def from_notes(resume: Resume) -> dict[str, str]:
    from app.context import notes
    from app.export.builder import _names
    text = notes.read_raw("skills") + "\n" + notes.read_raw("experience")
    nk = notes_kinds(text)
    out = {}
    for sk in resume.skills:
        for n in _names(sk):
            if n in nk:
                out[sk] = nk[n]
                break
    return out


async def effective(resume: Resume, d: Path | None = None, llm=None) -> dict[str, str]:
    """{skill: kind} to use on the page (see the module docstring for the order)."""
    from app.export.builder import SKILL_KINDS, known_kind
    notes_part = from_notes(resume)
    cache = _load_cache(d)
    unknown = [s for s in resume.skills if s not in notes_part and s not in resume.skill_kinds
               and known_kind(s) is None and s.lower() not in cache]
    if unknown and llm is not None and d is not None:
        try:
            data = await llm.run_skill(SKILL, "KINDS: " + json.dumps(SKILL_KINDS) + "\nSKILLS: "
                                       + json.dumps(unknown, ensure_ascii=False))
            got = (data or {}).get("kinds") if isinstance(data, dict) else None
            for item in got or []:
                if isinstance(item, dict) and item.get("kind") in SKILL_KINDS and item.get("skill") in unknown:
                    cache[item["skill"].lower()] = item["kind"]
            _cache(d).parent.mkdir(parents=True, exist_ok=True)
            _cache(d).write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
        except Exception as e:      # sorting skills must never stop the page from being built
            log.warning("skill sorting by AI failed: %s", e)
    ai_part = {s: cache[s.lower()] for s in resume.skills if s.lower() in cache and known_kind(s) is None}
    return {**ai_part, **notes_part, **resume.skill_kinds}


def effective_now(resume: Resume, d: Path | None = None) -> dict[str, str]:
    """Same without asking the AI (uses what it already said for this run)."""
    from app.export.builder import known_kind
    cache = _load_cache(d)
    ai_part = {s: cache[s.lower()] for s in resume.skills if s.lower() in cache and known_kind(s) is None}
    return {**ai_part, **from_notes(resume), **resume.skill_kinds}


_AI_ASK = re.compile(r"\b(ai|a\.i\.|genai|gen ai|generative ai|artificial intelligence|llms?|machine learning|"
                     r"ai[- ]assisted|ai[- ]powered|ai tools?|copilot|chatgpt|claude|cursor|prompt engineering|"
                     r"agents?|agentic|rag|mcp|langchain|openai|anthropic|gemini|vertex ai|hugging ?face)\b",
                     re.IGNORECASE)


def jd_wants_ai(jd) -> bool:
    """Does the job ad ask for AI tools or AI work at all? (title, requirements, duties, domain)"""
    text = " ".join([jd.title or "", jd.domain or "", *jd.must_have, *jd.nice_to_have, *jd.responsibilities])
    return bool(_AI_ASK.search(text))


_SOFT_ASK = re.compile(r"\b(communicat\w*|leadership|lead(ing)? (a )?team|mentor\w*|stakeholder\w*|"
                       r"collaborat\w*|teamwork|interpersonal|soft skills|ownership)\b", re.IGNORECASE)


def jd_wants_soft_skills(jd) -> bool:
    text = " ".join([jd.title or "", *jd.must_have, *jd.nice_to_have, *jd.responsibilities])
    return bool(_SOFT_ASK.search(text))


def page_skills(resume: Resume, kinds: dict[str, str], jd) -> list[str]:
    """The skills shown on the page (they stay saved either way): AI Tools only when the job asks
    for AI. Soft Skills are always shown (the Skills lines keep at least 2 of them)."""
    from app.export.builder import _kind_of
    hide = set()
    if jd is None or not jd_wants_ai(jd):
        hide.add("AI Tools")
    keep = {x.lower() for x in resume.skill_keep}
    return [s for s in resume.skills if _kind_of(s, kinds) not in hide or s.lower() in keep]


# ---------- which skills matter most for THIS job (when a Skills line is too long) ----------

_ROLE_KINDS = [
    (re.compile(r"\bfull[- ]?stack\b", re.I), {"Frontend", "Backend", "Databases", "Cloud"}),
    (re.compile(r"\b(front[- ]?end|ui engineer|react developer|web developer)\b", re.I), {"Frontend"}),
    (re.compile(r"\b(devops|sre|site reliability|infrastructure|cloud engineer)\b", re.I), {"Cloud", "DevOps"}),
    (re.compile(r"\b(data engineer|data platform|analytics)\b", re.I), {"Databases", "Cloud", "Backend"}),
    (re.compile(r"\b(ml|machine learning|ai engineer|llm)\b", re.I), {"AI Tools", "Backend"}),
    (re.compile(r"\b(back[- ]?end|server|api|platform|sde\d*|software (development )?engineer|swe)\b", re.I),
     {"Backend", "Databases", "Cloud", "DevOps"}),
]


def _signals(resume: Resume, jd, jd_text: str = "") -> dict[str, dict]:
    """Per skill: named (the job ad names it), related (near a skill it names), job (index of the
    latest job whose bullets show it, or None), fits (its kind fits the role), kind."""
    from app.export.builder import _kind_of
    from app.scoring.synonyms import alternatives, close_to, count_mentions, family, shown_by
    ad = "\n".join([jd.title or "", *jd.must_have, *jd.nice_to_have, *jd.responsibilities, jd_text or ""])
    asked = [a for x in [*jd.must_have, *jd.nice_to_have] for a in alternatives(x)]
    asked_fams = {family(a) for a in asked} - {None, "language"}
    fit_kinds = role_kinds(jd)
    asked_rel = "\n".join(r for a in asked for r in shown_by(a) + close_to(a))
    out = {}
    for sk in resume.skills:
        named = bool(count_mentions(sk, ad))
        related = not named and (
            family(sk) in asked_fams
            or any(count_mentions(r, ad) for r in shown_by(sk) + close_to(sk))
            or bool(asked_rel and count_mentions(sk, asked_rel)))
        job = next((j for j, e in enumerate(resume.experience) if count_mentions(sk, "\n".join(e.bullets))), None)
        kind = _kind_of(sk, resume.skill_kinds)
        # programming languages count for any engineering role
        fits = kind in fit_kinds or (kind == "Languages" and bool(fit_kinds))
        out[sk] = {"named": named, "related": related, "job": job, "kind": kind, "fits": fits}
    return out


def role_kinds(jd) -> set[str]:
    return next((kinds for rx, kinds in _ROLE_KINDS if rx.search(jd.title or "")), set())


def rank(resume: Resume, jd, jd_text: str = "", notes_text: str = "") -> dict[str, float]:
    """A relevance score for every skill on the resume:
      +100  the job ad names it (requirements, duties or anywhere in its text)
      + 60  it's related to a skill the job names (same family: MySQL when the ad says PostgreSQL,
            GCP when it says AWS; or one implies the other: TypeScript/JavaScript, Django/Python)
      + 30/20/10  your bullets show it at your latest / previous / older job
      + 15  its kind fits the role (a backend role favours Backend, Databases, Cloud, DevOps)
      + 0-10  how often your notes mention it
    So a job ad that lists only 5 skills still decides the rest sensibly."""
    from app.scoring.synonyms import count_mentions
    out = {}
    for sk, g in _signals(resume, jd, jd_text).items():
        score = 100.0 if g["named"] else 60.0 if g["related"] else 0.0
        if g["job"] is not None:
            score += (30, 20)[g["job"]] if g["job"] < 2 else 10
        if g["fits"]:
            score += 15
        score += min(count_mentions(sk, notes_text), 5) * 2 if notes_text else 0
        out[sk] = score
    return out


# ---------- which skills to show at all (not just which to cut when a line is too long) ----------

TARGET_MAX = 24     # a Skills section a recruiter can skim; more reads as a keyword dump
TARGET_MIN = 14     # fewer looks thin: refill from the best of what was left off
ALWAYS_TWO = ("Engineering", "Soft Skills")   # Tools and Soft Skills always keep 2 places
OFF_ROLE_MAX = 2    # a kind the role doesn't use (Frontend on a backend job) keeps at most 2
UNPROVEN_PER_ROLE_KIND = 2   # a kind the role uses also keeps its 2 best that nothing proves


def select(resume: Resume, jd, jd_text: str = "", notes_text: str = "") -> tuple[list[str], list[str]]:
    """Which skills the page shows for THIS job, and which are left off on purpose. -> (shown, left off)

    Keep:  1. skills the job ad names
           2. skills related to what it names (same family, implied by / close to it)
           3. skills of a kind the role uses (backend role: Backend, Databases, Cloud, DevOps)
              that your last two jobs' bullets show, plus that kind's 2 best others
           4. languages your bullets or notes show (or imply: TypeScript -> JavaScript)
           (a kind the role uses also counts when your notes mention it at least twice)
           5. 2 Tools and 2 Soft Skills (best ranked)
           6. for a kind the role doesn't use (Frontend on a backend job): at most the 2 best,
              and only ones your bullets or notes show
           7. anything you asked for in the resume chat (resume.skill_keep)
    Leave off everything else, even when there's room. Then at most TARGET_MAX (what nothing
    proves goes first, then the lowest ranked), and at least TARGET_MIN (refill from the best left off)."""
    if jd is None or not resume.skills:
        return list(resume.skills), []
    from app.scoring.synonyms import count_mentions, shown_by
    sig = _signals(resume, jd, jd_text)
    score = rank(resume, jd, jd_text, notes_text)
    page_text = "\n".join(b for e in resume.experience for b in e.bullets)

    def proof(sk: str) -> bool:
        """Your bullets or notes show it (or a skill that implies it: TypeScript -> JavaScript)."""
        return (sig[sk]["job"] is not None or (bool(notes_text) and count_mentions(sk, notes_text) >= 2)
                or any(count_mentions(x, page_text) for x in shown_by(sk)))
    pinned = {s.lower() for s in resume.skill_keep}
    has_role = bool(role_kinds(jd))
    keep: set[str] = set()
    for sk, g in sig.items():
        recent = g["job"] is not None and g["job"] < 2
        if (sk.lower() in pinned or g["named"] or g["related"]
                or (g["kind"] == "Languages" and proof(sk))
                or (recent and (g["fits"] or not has_role))
                or (g["fits"] and bool(notes_text) and count_mentions(sk, notes_text) >= 2)):
            keep.add(sk)
    by_kind: dict[str, list[str]] = {}
    for sk in resume.skills:
        by_kind.setdefault(sig[sk]["kind"], []).append(sk)
    for kind, items in by_kind.items():
        items = sorted(items, key=lambda s: -score[s])
        if kind in ALWAYS_TWO:
            for sk in items[:2]:
                keep.add(sk)
        elif kind in role_kinds(jd):
            # a kind the role uses: its 2 best that nothing proves yet still support the role
            for sk in [s for s in items if s not in keep][:UNPROVEN_PER_ROLE_KIND]:
                keep.add(sk)
        elif has_role and kind not in role_kinds(jd) and kind != "Languages":
            # off-role kind: the 2 best your bullets show; the rest only if the job names them
            shown = [s for s in items if proof(s) and s not in keep]
            room = OFF_ROLE_MAX - sum(1 for s in items if s in keep)
            for sk in shown[:max(room, 0)]:
                keep.add(sk)

    def protected(sk: str) -> bool:
        return sig[sk]["named"] or sk.lower() in pinned

    # too many: drop what nothing proves first, then the lowest ranked (never what the job names or
    # you pinned; Tools/Soft Skills keep 2)
    while len(keep) > TARGET_MAX:
        cands = [s for s in keep if not protected(s)
                 and not (sig[s]["kind"] in ALWAYS_TWO and sum(1 for x in keep if sig[x]["kind"] == sig[s]["kind"]) <= 2)]
        if not cands:
            break
        keep.discard(min(cands, key=lambda s: (proof(s), score[s], -resume.skills.index(s))))
    # too few: refill from the best left off
    rest = sorted((s for s in resume.skills if s not in keep), key=lambda s: -score[s])
    while len(keep) < TARGET_MIN and rest:
        keep.add(rest.pop(0))
    shown = [s for s in resume.skills if s in keep]
    return shown, [s for s in resume.skills if s not in keep]
