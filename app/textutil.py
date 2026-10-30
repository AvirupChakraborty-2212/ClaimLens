"""Small parsing helpers: money, dates, stemming."""
from __future__ import annotations

import re
from datetime import date

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

NUM_RE = re.compile(r"(?<!\w)(?<!\d\.)(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d])")

DATE_PATTERNS = [
    re.compile(r"(?P<d>\d{1,2})[/\-.](?P<m>\d{1,2})[/\-.](?P<y>\d{4})"),
    re.compile(r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})"),
    re.compile(r"(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+(?P<m>[A-Za-z]{3,9})\.?,?\s+(?P<y>\d{4})"),
]


def money(s: str) -> float:
    return float(s.replace(",", ""))


def parse_date(s: str) -> date | None:
    """Parse the first date in `s`. Numeric dates are read day-first (Indian convention)."""
    for pat in DATE_PATTERNS:
        m = pat.search(s)
        if not m:
            continue
        d, mo, y = m.group("d"), m.group("m"), m.group("y")
        try:
            mo_i = int(mo) if mo.isdigit() else _MONTHS[mo[:3].lower()]
            return date(int(y), mo_i, int(d))
        except (ValueError, KeyError):
            continue
    return None


def stem(w: str) -> str:
    w = w.lower()
    return w[:-1] if len(w) > 3 and w.endswith("s") else w


def words(s: str) -> list[str]:
    return [stem(w) for w in re.findall(r"[A-Za-z][A-Za-z\-]+", s)]


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def months_between(a: date, b: date) -> int:
    """Whole months elapsed from a to b."""
    m = (b.year - a.year) * 12 + (b.month - a.month)
    if b.day < a.day:
        m -= 1
    return m
