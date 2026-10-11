"""Shared test helpers. All numbers below are SYNTHETIC test data, not anyone's real activity."""
from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from contrib_data import SCHEMA_VERSION, level_from_counts  # noqa: E402

START = date(2025, 10, 5)  # a Sunday


def pattern(n: int = 367, seed: int = 7, busy: float = 0.2) -> list[int]:
    """Deterministic pseudo-activity: mostly zeros with some bursts."""
    rng = random.Random(seed)
    return [rng.choice([1, 1, 2, 3, 5, 8, 13]) if rng.random() < busy else 0 for _ in range(n)]


def make_days(counts: list[int | None], start: date = START) -> list[dict]:
    top = max([c or 0 for c in counts] + [0])
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "count": c, "level": level_from_counts(c or 0, top)}
        for i, c in enumerate(counts)
    ]


def make_document(counts: list[int | None], start: date = START, as_of: date | None = None, user: str = "karthik-vana") -> dict:
    days = make_days(counts, start)
    last = start + timedelta(days=len(counts) - 1)
    known = all(c is not None for c in counts)
    return {
        "schema_version": SCHEMA_VERSION,
        "user": user,
        "source": {"type": "github-html", "url": f"https://github.com/users/{user}/contributions"},
        "updated_at": f"{last.isoformat()}T01:00:00Z",
        "as_of": (as_of or last).isoformat(),
        "range": {"from": days[0]["date"], "to": days[-1]["date"]},
        "reported_total": sum(counts) if known else None,  # type: ignore[arg-type]
        "days": days,
    }


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def synthetic_html(
    counts: list[int], start: date = START, *, levels: bool = True, heading: bool = True, tooltips: bool = True
) -> str:
    """HTML shaped like github.com/users/<login>/contributions (same attributes, tool-tips and heading)."""
    top = max(counts)
    first_sunday = start - timedelta(days=start.isoweekday() % 7)
    rows: list[list[str]] = [[] for _ in range(7)]
    tips: list[str] = []
    for i, c in enumerate(counts):
        d = start + timedelta(days=i)
        col, row = (d - first_sunday).days // 7, d.isoweekday() % 7
        cid = f"contribution-day-component-{row}-{col}"
        attrs = f'tabindex="-1" data-ix="{col}" aria-selected="false" style="width: 10px" data-date="{d.isoformat()}" id="{cid}"'
        if levels:
            attrs += f' data-level="{level_from_counts(c, top)}"'
        rows[row].append(f'<td {attrs} role="gridcell" class="ContributionCalendar-day"></td>')
        label = "No contributions" if c == 0 else f"{c} contribution{'' if c == 1 else 's'}"
        tips.append(
            f'<tool-tip id="tooltip-{i}" for="{cid}" popover="manual" class="sr-only position-absolute">'
            f"{label} on {d.strftime('%B')} {ordinal(d.day)}.</tool-tip>"
        )
    head = (
        f'<h2 class="f4 text-normal mb-2">\n      {sum(counts)}\n      contributions\n        in the last year\n    </h2>'
        if heading
        else ""
    )
    body = "".join(f"<tr>{''.join(r)}</tr>" for r in rows)
    return f'<div class="js-yearly-contributions">{head}<table><tbody>{body}</tbody></table>{"".join(tips) if tooltips else ""}</div>'
