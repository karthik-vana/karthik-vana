"""Schema, validation and statistics for ``data/contributions.json`` (standard library only).

Both the fetcher and the renderer import this module, so a document is judged by the same
rules before it is written and before it is drawn. Statistics are computed only from what
the data actually supports: if a day's count is unknown, totals and "best day" are withheld
rather than guessed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

SCHEMA_VERSION = 1
MIN_DAYS = 300  # a rolling "last year" calendar is 365-371 days; far fewer means a broken page
MAX_DAYS = 372  # 53 weeks x 7 = 371, plus one day of slack for time-zone edges


class DataError(ValueError):
    """The contribution data is missing, malformed or inconsistent."""


def parse_date(value: Any, what: str = "date") -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise DataError(f"{what} must be a YYYY-MM-DD string, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DataError(f"{what} is not a real calendar date: {value!r}") from exc


def level_from_counts(count: int, max_count: int) -> int:
    """Fallback intensity (0-4) when the source gives no level: quartiles of the busiest day."""
    if count <= 0 or max_count <= 0:
        return 0
    return min(4, max(1, math.ceil(4 * count / max_count)))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_document(doc: Any) -> list[str]:
    """Raise :class:`DataError` if the document is unusable; otherwise return non-fatal warnings."""
    warnings: list[str] = []
    if not isinstance(doc, dict):
        raise DataError("document is not a JSON object")
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise DataError(f"unsupported schema_version {doc.get('schema_version')!r} (expected {SCHEMA_VERSION})")
    if not isinstance(doc.get("user"), str) or not doc["user"]:
        raise DataError("'user' must be a non-empty string")
    days = doc.get("days")
    if not isinstance(days, list) or not MIN_DAYS <= len(days) <= MAX_DAYS:
        n = len(days) if isinstance(days, list) else "n/a"
        raise DataError(f"'days' must be a list of {MIN_DAYS}-{MAX_DAYS} entries, got {n}")

    prev: date | None = None
    first: date | None = None
    counts_known = True
    total = 0
    disagreements = 0
    for i, day in enumerate(days):
        if not isinstance(day, dict):
            raise DataError(f"days[{i}] is not an object")
        cur = parse_date(day.get("date"), f"days[{i}].date")
        if prev is not None and cur != prev + timedelta(days=1):
            raise DataError(f"days[{i}] ({cur}) does not directly follow {prev}: gap, duplicate or wrong order")
        first = first or cur
        count, level = day.get("count"), day.get("level")
        if count is not None and (not _is_int(count) or count < 0):
            raise DataError(f"days[{i}].count must be a non-negative integer or null, got {count!r}")
        if not _is_int(level) or level not in range(5):
            raise DataError(f"days[{i}].level must be an integer 0-4, got {level!r}")
        if count is None:
            counts_known = False
        else:
            total += count
            if (count == 0) != (level == 0):
                disagreements += 1
        prev = cur
    assert first is not None and prev is not None

    rng = doc.get("range")
    if not isinstance(rng, dict) or parse_date(rng.get("from"), "range.from") != first or parse_date(rng.get("to"), "range.to") != prev:
        raise DataError("'range' does not match the first and last entries of 'days'")
    try:
        datetime.fromisoformat(str(doc.get("updated_at")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataError(f"'updated_at' is not an ISO timestamp: {doc.get('updated_at')!r}") from exc
    parse_date(doc.get("as_of"), "as_of")
    reported = doc.get("reported_total")
    if reported is not None and (not _is_int(reported) or reported < 0):
        raise DataError(f"'reported_total' must be a non-negative integer or null, got {reported!r}")

    if disagreements:
        warnings.append(f"{disagreements} day(s) have a count/level pair that disagree (zero vs non-zero)")
    if counts_known and reported is not None and reported != total:
        warnings.append(f"sum of day counts ({total}) differs from the total GitHub reports ({reported})")
    return warnings


@dataclass(frozen=True)
class Stats:
    first: date
    last: date
    days: int
    total: int | None  # None unless every day's count is known and agrees with GitHub's own total
    active_days: int
    longest_streak: int
    current_streak: int
    best_count: int | None
    best_date: date | None


def _active(day: dict[str, Any]) -> bool:
    count = day["count"]
    return count > 0 if count is not None else day["level"] > 0


def compute_stats(doc: dict[str, Any]) -> Stats:
    """Summary statistics derived strictly from ``doc['days']`` (validate first)."""
    days = doc["days"]
    dates = [date.fromisoformat(d["date"]) for d in days]
    counts = [d["count"] for d in days]
    counts_known = all(c is not None for c in counts)
    total = sum(counts) if counts_known else None  # type: ignore[arg-type]
    reported = doc.get("reported_total")
    if total is not None and reported is not None and reported != total:
        total = None  # sources disagree: show nothing rather than pick one

    active_flags = [_active(d) for d in days]
    longest = run = 0
    for flag in active_flags:
        run = run + 1 if flag else 0
        longest = max(longest, run)

    # Current streak: consecutive active days ending at the last day. A still-running day with no
    # activity yet (the last day equals the fetch date) does not break a streak that ended yesterday.
    end = len(days) - 1
    if not active_flags[end] and dates[end] >= date.fromisoformat(doc["as_of"]):
        end -= 1
    current = 0
    while end >= 0 and active_flags[end]:
        current += 1
        end -= 1

    best_count: int | None = None
    best_date: date | None = None
    if counts_known:
        top = max(counts)  # type: ignore[type-var]
        if top and top > 0:
            idx = counts.index(top)
            best_count, best_date = top, dates[idx]  # first occurrence if tied

    return Stats(
        first=dates[0],
        last=dates[-1],
        days=len(days),
        total=total,
        active_days=sum(active_flags),
        longest_streak=longest,
        current_streak=current,
        best_count=best_count,
        best_date=best_date,
    )


def content_key(doc: dict[str, Any]) -> tuple[Any, ...]:
    """What makes two documents 'the same data' (ignores timestamps), used to avoid no-op commits."""
    return (
        doc.get("user"),
        doc.get("source", {}).get("type"),
        doc.get("reported_total"),
        tuple((d["date"], d["count"], d["level"]) for d in doc.get("days", [])),
    )
