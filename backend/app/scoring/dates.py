"""Turn resume dates into months and total years of experience."""
from __future__ import annotations

import re
from datetime import date

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
PRESENT = {"present", "current", "now", "today", "ongoing", "till date", "to date"}


def to_month_index(value: str | None, *, today: date | None = None, is_end: bool = False) -> int | None:
    """'2021-04' / 'Apr 2021' / '2021' / 'present' -> months since year 0. None if unknown."""
    if not value:
        return None
    v = value.strip().lower()
    today = today or date.today()
    if v in PRESENT:
        return today.year * 12 + today.month
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})", v)
    if m:
        return int(m.group(1)) * 12 + int(m.group(2))
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{4})", v)
    if m:
        return int(m.group(2)) * 12 + int(m.group(1))
    m = re.fullmatch(r"([a-z]{3})[a-z]*\.?\s+(\d{4})", v)
    if m and m.group(1) in MONTHS:
        return int(m.group(2)) * 12 + MONTHS[m.group(1)]
    m = re.fullmatch(r"(\d{4})", v)
    if m:  # year only: assume Jan for a start, Dec for an end
        return int(m.group(1)) * 12 + (12 if is_end else 1)
    return None


def total_years(spans: list[tuple[str | None, str | None]], *, today: date | None = None
                ) -> tuple[float, int]:
    """Merge overlapping jobs and return (years, number of spans that couldn't be read)."""
    intervals, unreadable = [], 0
    for start, end in spans:
        s = to_month_index(start, today=today)
        e = to_month_index(end, today=today, is_end=True) if end else None
        if s is None or e is None or e < s:
            unreadable += 1
            continue
        intervals.append((s, e + 1))  # inclusive end month
    intervals.sort()
    months, cur_s, cur_e = 0, None, None
    for s, e in intervals:
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                months += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        months += cur_e - cur_s
    return round(months / 12, 1), unreadable
