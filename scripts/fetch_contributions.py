#!/usr/bin/env python3
"""Fetch the public GitHub contribution calendar and store it as validated JSON.

Primary source (no token needed)
    https://github.com/users/<user>/contributions
    The same HTML fragment github.com loads into a profile page. Day cells carry ``data-date``
    and ``data-level``; the exact counts live in the matching ``<tool-tip>`` text.

Fallback (only if the page layout breaks or the request is blocked)
    GitHub GraphQL API, which returns exact counts. It needs a token, so it is used only when
    ``GITHUB_TOKEN`` / ``GH_TOKEN`` is present. In GitHub Actions the built-in, auto-created
    ``secrets.GITHUB_TOKEN`` is enough for public contribution data - no personal access token
    is required and nothing is stored in the repository.

Safety rules
    * Responses are validated (status, markup, contiguous dates, plausible length, levels).
    * On any failure the existing ``data/contributions.json`` is left exactly as it was.
    * The file is rewritten only when the day data actually changed, so re-running on the same
      day creates no diff and no commit.
    * Nothing is invented: unknown counts stay ``null`` and statistics are withheld.

Exit status: 0 = written or unchanged, 2 = fetch/validation failed (previous data preserved).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("Missing dependency: requests. Run: pip install -r requirements.txt")

from common import DATA_DIR, GITHUB_USER, write_text_atomic
from contrib_data import SCHEMA_VERSION, DataError, content_key, level_from_counts, validate_document

DEFAULT_OUT = DATA_DIR / "contributions.json"
USER_AGENT = "karthik-vana-profile-art/1.0 (+https://github.com/karthik-vana/karthik-vana)"
HTML_URL = "https://github.com/users/{user}/contributions"
GRAPHQL_URL = "https://api.github.com/graphql"

USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COUNT_RE = re.compile(r"^\s*(No|\d[\d,]*)\s+contributions?\b", re.I)
HEADING_RE = re.compile(r"([\d,]+)\s+contributions?\s+in\s+(?:the\s+last\s+year|\d{4})", re.I)
GRAPHQL_LEVELS = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}
GRAPHQL_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount contributionLevel } }
      }
    }
  }
}
"""


class FetchError(RuntimeError):
    """The calendar could not be downloaded or understood."""


# --------------------------------------------------------------------------- HTML
class _CalendarParser(HTMLParser):
    """Collects day cells (any tag with ``data-date``) and tool-tip texts keyed by their ``for`` id."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[dict[str, str | None]] = []
        self.tips: dict[str, str] = {}
        self._tip_for: str | None = None
        self._tip_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "tool-tip":
            self._tip_for, self._tip_text = a.get("for"), []
            return
        value = a.get("data-date")
        if value and DATE_RE.match(value):
            self.cells.append(a)

    def handle_endtag(self, tag: str) -> None:
        if tag == "tool-tip":
            if self._tip_for:
                self.tips[self._tip_for] = " ".join("".join(self._tip_text).split())
            self._tip_for, self._tip_text = None, []

    def handle_data(self, data: str) -> None:
        if self._tip_for is not None:
            self._tip_text.append(data)


def _cell_count(attrs: dict[str, str | None], tips: dict[str, str]) -> int | None:
    """Exact count for a cell, from (in order) ``data-count``, its tool-tip, ``aria-label``; else None."""
    raw = attrs.get("data-count")
    if raw is not None and raw.strip().isdigit():
        return int(raw)
    cell_id = attrs.get("id")
    for text in (tips.get(cell_id) if cell_id else None, attrs.get("aria-label")):
        match = COUNT_RE.match(text) if text else None
        if match:
            return 0 if match.group(1).lower() == "no" else int(match.group(1).replace(",", ""))
    return None


def parse_calendar_html(html: str) -> tuple[list[dict[str, Any]], int | None]:
    """Return ``(days, reported_total)``; ``days`` are ``{"date", "count", "level"}`` sorted by date."""
    parser = _CalendarParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # noqa: BLE001 - surface any parser problem as a typed error
        raise FetchError(f"could not parse the calendar HTML: {exc}") from exc
    if not parser.cells:
        raise FetchError("no contribution cells found - GitHub may have changed the page markup")

    by_date: dict[str, dict[str, Any]] = {}
    for attrs in parser.cells:
        day = str(attrs["data-date"])
        raw_level = (attrs.get("data-level") or "").strip()
        level = int(raw_level) if raw_level.isdigit() and int(raw_level) <= 4 else None
        entry = {"date": day, "count": _cell_count(attrs, parser.tips), "level": level}
        if day in by_date and by_date[day] != entry:
            raise FetchError(f"conflicting duplicate entries for {day}")
        by_date[day] = entry
    days = [by_date[k] for k in sorted(by_date)]

    busiest = max((d["count"] or 0) for d in days)
    for d in days:
        if d["level"] is None:
            if d["count"] is None:
                raise FetchError(f"{d['date']} has neither a level nor a count")
            d["level"] = level_from_counts(d["count"], busiest)

    heading = HEADING_RE.search(" ".join(re.sub(r"<[^>]+>", " ", html).split()))
    return days, (int(heading.group(1).replace(",", "")) if heading else None)


# ------------------------------------------------------------------------ network
def _retry_after(resp: requests.Response) -> float | None:
    raw = resp.headers.get("Retry-After", "")
    return float(min(max(int(raw), 1), 60)) if raw.isdigit() else None


def download_html(
    user: str, timeout: float, retries: int, session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """GET the contribution fragment with timeouts, retries and back-off; validates the response."""
    url = HTML_URL.format(user=user)
    sess = session or requests.Session()
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    last = "no attempt made"
    for attempt in range(1, retries + 1):
        wait: float | None = None
        try:
            resp = sess.get(url, headers=headers, timeout=(5, timeout))
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code == 200:
                if "data-date" not in resp.text:
                    raise FetchError(
                        "HTTP 200 but no calendar markup in the response (rate-limit page, login wall or layout change)"
                    )
                return resp.text
            if resp.status_code == 404:
                raise FetchError(f"user {user!r} not found (HTTP 404)")
            last = f"HTTP {resp.status_code}"
            if resp.status_code not in (403, 429) and resp.status_code < 500:
                raise FetchError(f"unexpected {last} from {url}")
            wait = _retry_after(resp)
        if attempt < retries:
            delay = wait if wait is not None else min(2**attempt, 30) + random.random()
            print(f"  attempt {attempt}/{retries} failed ({last}); retrying in {delay:.1f}s", file=sys.stderr)
            sleep(delay)
    raise FetchError(f"giving up after {retries} attempts ({last})")


def parse_graphql_payload(payload: Any) -> tuple[list[dict[str, Any]], int | None]:
    """Days and total from a GraphQL ``contributionCalendar`` response."""
    if not isinstance(payload, dict):
        raise FetchError("GraphQL response is not a JSON object")
    if payload.get("errors"):
        raise FetchError(f"GraphQL error: {str(payload['errors'])[:200]}")
    try:
        calendar = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]
        days = [
            {
                "date": d["date"],
                "count": int(d["contributionCount"]),
                "level": GRAPHQL_LEVELS[d["contributionLevel"]],
            }
            for week in calendar["weeks"]
            for d in week["contributionDays"]
        ]
        total = int(calendar["totalContributions"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FetchError(f"unexpected GraphQL response shape ({type(exc).__name__}: {exc})") from exc
    return sorted(days, key=lambda d: d["date"]), total


def download_graphql(
    user: str, token: str, now: datetime, timeout: float, retries: int,
    session: requests.Session | None = None, sleep: Callable[[float], None] = time.sleep,
) -> Any:
    sess = session or requests.Session()
    variables = {
        "login": user,
        "from": (now - timedelta(days=364)).strftime("%Y-%m-%dT00:00:00Z"),
        "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    headers = {"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"}
    last = "no attempt made"
    for attempt in range(1, retries + 1):
        try:
            resp = sess.post(GRAPHQL_URL, json={"query": GRAPHQL_QUERY, "variables": variables}, headers=headers, timeout=(5, timeout))
        except requests.RequestException as exc:
            last = type(exc).__name__
        else:
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise FetchError("GraphQL response was not valid JSON") from exc
            last = f"HTTP {resp.status_code}"
            if resp.status_code in (400, 401, 404):
                raise FetchError(f"GraphQL request rejected ({last}); check the token")
        if attempt < retries:
            sleep(min(2**attempt, 30) + random.random())
    raise FetchError(f"GraphQL: giving up after {retries} attempts ({last})")


# ---------------------------------------------------------------------- document
def build_document(
    user: str, days: list[dict[str, Any]], source_type: str, source_url: str, reported_total: int | None, now: datetime
) -> tuple[dict[str, Any], list[str]]:
    """Assemble and validate the JSON document; returns ``(document, warnings)``."""
    if not days:
        raise FetchError("the calendar contained no days")
    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "user": user,
        "source": {"type": source_type, "url": source_url},
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": now.date().isoformat(),
        "range": {"from": days[0]["date"], "to": days[-1]["date"]},
        "reported_total": reported_total,
        "days": days,
    }
    warnings = validate_document(doc)
    skew = abs((date.fromisoformat(days[-1]["date"]) - now.date()).days)
    if skew > 2:  # tolerate time-zone differences and short caching, not a stale or wrong calendar
        raise DataError(f"calendar ends on {days[-1]['date']} but today is {now.date()} (UTC): data looks stale")
    return doc, warnings


def dumps(doc: dict[str, Any]) -> str:
    """Stable JSON: pretty header, one line per day (keeps the file diff-friendly)."""
    head = json.dumps({k: v for k, v in doc.items() if k != "days"}, indent=2, ensure_ascii=False)
    rows = ",\n".join("    " + json.dumps(d, ensure_ascii=False) for d in doc["days"])
    return head[:-2] + ',\n  "days": [\n' + rows + "\n  ]\n}\n"


def write_if_changed(doc: dict[str, Any], out: Path) -> bool:
    """Write ``doc`` unless the existing file already holds the same day data. Returns True if written."""
    if out.is_file():
        try:
            old = json.loads(out.read_text(encoding="utf-8"))
            validate_document(old)
            if content_key(old) == content_key(doc):
                return False
        except (OSError, ValueError):
            pass  # unreadable or invalid previous file: replacing it is the right thing
    write_text_atomic(out, dumps(doc))
    return True


def fetch_document(user: str, source: str, timeout: float, retries: int, token: str | None, now: datetime):
    """Try the public page first; fall back to GraphQL only if allowed and a token exists."""
    problems: list[str] = []
    if source in ("auto", "html"):
        try:
            html = download_html(user, timeout, retries)
            days, total = parse_calendar_html(html)
            return build_document(user, days, "github-html", HTML_URL.format(user=user), total, now)
        except (FetchError, DataError) as exc:
            problems.append(f"html: {exc}")
            if source == "html":
                raise
    if source in ("auto", "graphql"):
        if not token:
            problems.append("graphql: skipped, no GITHUB_TOKEN/GH_TOKEN in the environment")
        else:
            try:
                days, total = parse_graphql_payload(download_graphql(user, token, now, timeout, retries))
                return build_document(user, days, "github-graphql", GRAPHQL_URL, total, now)
            except (FetchError, DataError) as exc:
                problems.append(f"graphql: {exc}")
    raise FetchError(" | ".join(problems))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--user", default=os.environ.get("GH_USER") or GITHUB_USER, help="GitHub login (default: %(default)s)")
    p.add_argument("--source", choices=["auto", "html", "graphql"], default="auto")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--timeout", type=float, default=20.0, help="read timeout in seconds")
    p.add_argument("--retries", type=int, default=4)
    p.add_argument("--from-file", type=Path, help="parse a saved calendar HTML file instead of using the network")
    p.add_argument("--dry-run", action="store_true", help="fetch and validate, but do not write")
    args = p.parse_args(argv)

    if not USER_RE.match(args.user):
        p.error(f"{args.user!r} is not a valid GitHub login")
    now = datetime.now(timezone.utc)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    try:
        if args.from_file:
            days, total = parse_calendar_html(args.from_file.read_text(encoding="utf-8"))
            doc, warnings = build_document(args.user, days, "github-html", f"file:{args.from_file.name}", total, now)
        else:
            doc, warnings = fetch_document(args.user, args.source, args.timeout, max(1, args.retries), token, now)
    except (FetchError, DataError, OSError) as exc:
        msg = f"contribution fetch failed: {exc}"
        print(f"::error title=Contribution fetch failed::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"error: {msg}", file=sys.stderr)
        print(f"The previous {args.out.name} (if any) was left untouched.", file=sys.stderr)
        return 2

    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    first, last = doc["range"]["from"], doc["range"]["to"]
    known = sum(d["count"] or 0 for d in doc["days"]) if all(d["count"] is not None for d in doc["days"]) else "unknown"
    print(f"Fetched {len(doc['days'])} days ({first} to {last}) via {doc['source']['type']}; day counts sum: {known}; GitHub reports: {doc['reported_total']}")
    if args.dry_run:
        print("Dry run: nothing written.")
        return 0
    print(f"{'Wrote' if write_if_changed(doc, args.out) else 'Unchanged:'} {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
