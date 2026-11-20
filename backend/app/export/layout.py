"""Where do lines go? Per-bullet line counts from the real font widths.

The fit agent uses this to find cheap wins, e.g. a bullet that wraps only 2 words onto a
new line: trimming ~15 characters saves a whole line without losing any fact.
Uses Calibri (Windows) or Carlito (same widths) via Pillow; falls back to an estimate.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from functools import lru_cache

from app.export.builder import A4_W_CM, Look, achievements_of, format_spec
from app.schemas.models import Resume

BULLET_INDENT_PT = 18.0  # Word's "List Bullet" text indent (0.25 in)
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\calibri.ttf",
    "/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf",
    *glob.glob("/usr/share/fonts/**/Carlito-Regular.ttf", recursive=True),
    "/Library/Fonts/Calibri.ttf",
]


@lru_cache
def _font_path() -> str | None:
    return next((p for p in FONT_CANDIDATES if p and os.path.exists(p)), None)


@lru_cache(maxsize=8)
def _font(size_pt: float):
    path = _font_path()
    if not path:
        return None
    from PIL import ImageFont
    return ImageFont.truetype(path, size=int(round(size_pt * 20)))  # 20x for precision


def text_width_pt(text: str, size_pt: float) -> float:
    f = _font(size_pt)
    if f is None:
        return len(text) * size_pt * 0.5
    return f.getlength(text) / 20.0


def wrap(text: str, width_pt: float, size_pt: float) -> list[str]:
    """Greedy word wrap, like Word does for plain left-aligned text."""
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}" if cur else word
        if cur and text_width_pt(trial, size_pt) > width_pt:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines or [""]


@dataclass
class BulletLayout:
    id: str            # "j<job>b<bullet>" or "a<i>" for achievements
    job: int | None
    bullet: int | None
    text: str
    lines: int
    last_line_words: int
    last_line_fill: float      # 0..1 of the line width
    chars_to_save_line: int    # trim about this many characters to lose the last line


@dataclass
class Layout:
    width_pt: float
    bullets: list[BulletLayout] = field(default_factory=list)
    other_lines: int = 0       # name, contact, headings, summary, skills, job headers

    @property
    def bullet_lines(self) -> int:
        return sum(b.lines for b in self.bullets)

    @property
    def total_lines(self) -> int:
        return self.other_lines + self.bullet_lines

    def orphans(self, max_words: int = 3) -> list[BulletLayout]:
        """Bullets whose last line holds only a few words: cheapest lines to win back."""
        return sorted((b for b in self.bullets if b.lines > 1 and b.last_line_words <= max_words),
                      key=lambda b: b.chars_to_save_line)


def _bullet(bid: str, job, bullet, text: str, width: float, size: float) -> BulletLayout:
    lines = wrap(text, width, size)
    last = lines[-1]
    return BulletLayout(id=bid, job=job, bullet=bullet, text=text, lines=len(lines),
                        last_line_words=len(last.split()),
                        last_line_fill=round(text_width_pt(last, size) / width, 2),
                        chars_to_save_line=len(last) + 1 if len(lines) > 1 else 0)


def page_skill_lines(resume: Resume, width_pt: float, size_pt: float
                     ) -> tuple[list[list[tuple[str, list[str]]]], list[str]]:
    """The Skills lines exactly as printed: each fits on ONE printed line. A line that would wrap
    drops its least relevant skill until it fits (resume.skill_rank, see skillkinds.rank; without
    it: skills the job names, then skills your bullets show). Tools and Soft Skills keep at least
    2 places (which other skills appear at all is decided before, by skillkinds.select). -> (lines, left off)."""
    from app.export.builder import _kind_of, skill_groups, skills_line_text
    from app.export.skillkinds import ALWAYS_TWO
    from app.scoring.render import evidence_text
    from app.scoring.synonyms import count_mentions
    focus = "\n".join(resume.skill_focus)
    bullets = evidence_text(resume)
    ranks = resume.skill_rank

    def worth(sk: str) -> float:
        if sk in ranks:
            return ranks[sk]
        return 3 if focus and count_mentions(sk, focus) else 1 if count_mentions(sk, bullets) else 0

    kind = {sk: _kind_of(sk, resume.skill_kinds) for sk in resume.skills}
    lines, dropped = [], []
    for parts in skill_groups(resume.skills, overrides=resume.skill_kinds):
        label, items = parts[0][0], list(parts[0][1])
        # a little room to spare: the bold label is wider than plain text
        while len(items) > 1 and len(wrap(skills_line_text([(label, items)]), width_pt * 0.95, size_pt)) > 1:
            count = {}
            for x in items:
                count[kind.get(x)] = count.get(kind.get(x), 0) + 1
            choices = [i for i in range(len(items)) if kind.get(items[i]) not in ALWAYS_TWO
                       or count[kind.get(items[i])] > 2]
            if not choices:
                break
            weakest = min(choices, key=lambda i: (worth(items[i]), -i))
            dropped.append(items.pop(weakest))
        lines.append([(label, items)])
    return lines, dropped


def analyze(resume: Resume, look: Look, spec: dict | None = None) -> Layout:
    spec = spec or format_spec()
    usable = (A4_W_CM - 2 * look.margin_cm) / 2.54 * 72
    bw = usable - BULLET_INDENT_PT
    lay = Layout(width_pt=round(usable, 1))
    lay.other_lines = 2  # name + contact
    for section in spec.get("sections", []):
        if section == "summary" and resume.summary:
            lay.other_lines += 1 + len(wrap(resume.summary, usable, look.body_pt))
        elif section == "skills" and resume.skills:
            from app.export.builder import skills_line_text
            lines, _ = page_skill_lines(resume, usable, look.body_pt)
            lay.other_lines += 1 + sum(len(wrap(skills_line_text(parts), usable, look.body_pt)) for parts in lines)
        elif section == "experience" and resume.experience:
            lay.other_lines += 1
            for j, job in enumerate(resume.experience):
                lay.other_lines += 1
                for b, text in enumerate(job.bullets):
                    lay.bullets.append(_bullet(f"j{j}b{b}", j, b, text, bw, look.body_pt))
        elif section == "achievements":
            items = achievements_of(resume, spec)
            if items:
                lay.other_lines += 1
                for i, text in enumerate(items):
                    lay.bullets.append(_bullet(f"a{i}", None, None, text, bw, look.body_pt))
        elif section == "education" and resume.education:
            lay.other_lines += 1                         # heading
            for e in resume.education:
                lay.other_lines += 1                     # degree ........ dates
                second = " | ".join(x for x in ((e.institution if e.degree else None), e.details) if x)
                if second:
                    lay.other_lines += len(wrap(second, usable, look.body_pt))
    return lay
