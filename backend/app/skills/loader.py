"""Load and validate the step instructions in backend/skills/<name>/SKILL.md.

Every step's instructions live in one SKILL.md file:
- OpenRouter mode: the SKILL.md body is sent as the system prompt.
- Claude mode: the folder is uploaded as a real Agent Skill and attached to the request.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from app import config

NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
RESERVED = ("anthropic", "claude")

TEXT_EXTS = {".md", ".txt", ".json", ".yaml", ".yml", ".csv"}
SCRIPT_EXTS = {".py", ".sh", ".js", ".ts"}

# Which pipeline step (and so which model settings) each skill belongs to.
SKILL_STEP = {
    "matcher-parse-resume": "parse",
    "matcher-parse-jd": "parse",
    "matcher-score": "score",
    "matcher-update-context": "context",
    "matcher-improve": "improve",
    "matcher-plan-edits": "improve",
    "matcher-reword": "improve",
    "matcher-write-bullets": "improve",
    "matcher-fit": "improve",
    "matcher-truth-check": "truth_check",
    "matcher-profile-resume": "score",   # strict, careful model settings (like the judge)
    "matcher-find-evidence": "improve",  # reads your notes for missing requirements
    "matcher-edit-resume": "improve",    # chat about the finished resume (Step 3)
    "matcher-sort-skills": "context",    # which Skills line an unknown skill goes on (light, fast)
}


class SkillError(ValueError):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    folder: Path
    content_hash: str

    @property
    def prompt_text(self) -> str:
        """The skill as one prompt, for models without Claude Agent Skills.

        SKILL.md's body, then every other text file in the folder (references, examples),
        so non-Claude models get the same instructions Claude would load from the folder.
        Scripts can't run in prompt mode; they're listed so the model knows not to rely on them.
        """
        parts = [self.body]
        scripts = []
        for f in sorted(p for p in self.folder.rglob("*") if p.is_file() and p.name != "SKILL.md"):
            rel = f.relative_to(self.folder).as_posix()
            if f.suffix.lower() in TEXT_EXTS:
                parts.append(f"\n\n## File: {rel}\n\n{f.read_text(encoding='utf-8').strip()}")
            elif f.suffix.lower() in SCRIPT_EXTS:
                scripts.append(rel)
        if scripts:
            parts.append("\n\n## Note\n\nThis skill includes scripts (" + ", ".join(scripts)
                         + ") that can't run here. Follow the written instructions without them.")
        return "".join(parts)

    @property
    def step(self) -> str:
        if self.name not in SKILL_STEP:
            raise SkillError(f"Skill '{self.name}' has no step in SKILL_STEP (app/skills/loader.py)")
        return SKILL_STEP[self.name]


def _split_frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if not m:
        raise SkillError("SKILL.md must start with a --- frontmatter block")
    meta = yaml.safe_load(m.group(1)) or {}
    return meta, m.group(2).strip()


def _hash_folder(folder: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in folder.rglob("*") if p.is_file()):
        h.update(f.relative_to(folder).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


def validate(meta: dict, folder: Path) -> None:
    name = meta.get("name")
    desc = meta.get("description")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise SkillError(f"{folder.name}: name must be 1-64 chars of a-z, 0-9, '-'")
    if any(r in name for r in RESERVED):
        raise SkillError(f"{name}: name may not contain {RESERVED}")
    if name != folder.name:
        raise SkillError(f"{name}: folder must be named '{name}' (is '{folder.name}')")
    if not isinstance(desc, str) or not desc.strip() or len(desc) > 1024:
        raise SkillError(f"{name}: description must be 1-1024 characters")
    if "<" in desc or "<" in name:
        raise SkillError(f"{name}: no XML tags allowed in name/description")


def load_skill(name: str) -> Skill:
    folder = config.SKILLS_DIR / name
    path = folder / "SKILL.md"
    if not path.exists():
        raise SkillError(f"Skill '{name}' not found at {path}")
    meta, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    validate(meta, folder)
    return Skill(
        name=meta["name"],
        description=meta["description"].strip(),
        body=body,
        folder=folder,
        content_hash=_hash_folder(folder),
    )


def all_skills() -> list[Skill]:
    return [load_skill(p.name) for p in sorted(config.SKILLS_DIR.iterdir())
            if (p / "SKILL.md").exists()]
