#!/usr/bin/env python3
"""Render ``data/contributions.json`` as a GitHub-style contribution heatmap (SVG).

Output : assets/contrib-heatmap.svg

* A real calendar: up to 53 week columns x 7 weekday rows (Sunday first), rounded cells,
  GitHub's dark-mode green scale, month and weekday labels, a Less-to-More legend.
* Everything is drawn from the JSON. The footer shows only statistics the data supports
  (see ``contrib_data.compute_stats``); with zero activity the grid is still drawn in full.
* One-shot diagonal reveal (CSS inside the SVG): cells fade/scale in, staggered across weeks and
  weekdays. The base style of every cell is its final state, so renderers without animation
  support and viewers who prefer reduced motion see the complete heatmap. ``--static`` omits
  the animation entirely.
* Deterministic: the output depends only on the JSON, never on the clock.
* If anything fails, the existing SVG is left untouched (exit status 2).

Uses only the standard library.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from common import (
    ASSETS_DIR,
    DATA_DIR,
    HANDLE,
    MONO_ADVANCE,
    REDUCED_MOTION_CSS,
    THEME,
    env_flag,
    esc,
    esc_text,
    fmt,
    parse_svg,
    window_chrome,
    write_text_atomic,
)
from contrib_data import DataError, Stats, compute_stats, validate_document

WIDTH = 1000
CELL, GAP = 14, 3
PITCH = CELL + GAP
GRID_X0 = 52.0
Y_PROMPT, Y_MONTHS, GRID_Y0 = 64.0, 100.0, 108.0
GRID_BOTTOM = GRID_Y0 + 7 * PITCH - GAP
Y_STATS = GRID_BOTTOM + 30
Y_SOURCE = Y_STATS + 22
HEIGHT = int(Y_SOURCE + 20)
STATS_MAX_CHARS = 100  # keeps the footer clear of the legend (font 12, ~7.2 units per character)

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEKDAY_LABELS = {1: "Mon", 3: "Wed", 5: "Fri"}

Cell = dict[str, Any]  # {"level": int, "title": str | None}


# --------------------------------------------------------------------------- model
def build_weeks(days: list[dict[str, Any]]) -> tuple[list[list[Cell | None]], list[list[date | None]]]:
    """Group days into Sunday-first week columns. Placement uses real dates (leap years, gaps, DST-proof)."""
    first = date.fromisoformat(days[0]["date"])
    start = first - timedelta(days=first.isoweekday() % 7)  # the Sunday on/before the first day
    weeks: list[list[Cell | None]] = []
    dates: list[list[date | None]] = []
    for d in days:
        dt = date.fromisoformat(d["date"])
        col, row = (dt - start).days // 7, dt.isoweekday() % 7
        while len(weeks) <= col:
            weeks.append([None] * 7)
            dates.append([None] * 7)
        count = d["count"]
        title = None if count is None else f"{count} contribution{'' if count == 1 else 's'} on {d['date']}"
        weeks[col][row] = {"level": d["level"], "title": title}
        dates[col][row] = dt
    return weeks, dates


def month_labels(dates: list[list[date | None]]) -> list[tuple[int, str]]:
    """(column, label) wherever the month changes; a first label crowded by the second is dropped."""
    labels: list[tuple[int, str]] = []
    prev: int | None = None
    for col, week in enumerate(dates):
        first_day = next((d for d in week if d is not None), None)
        if first_day is not None and first_day.month != prev:
            labels.append((col, MONTHS[first_day.month - 1]))
            prev = first_day.month
    if len(labels) > 1 and labels[1][0] - labels[0][0] < 3:
        labels.pop(0)
    return labels


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def stats_text(s: Stats) -> str:
    """One footer line made only of facts the data supports, trimmed to fit the available width."""
    if s.total == 0:
        return "No public contributions in this period yet"
    parts: list[str] = []
    if s.total is not None:
        span = "in the last year" if s.days >= 360 else f"in the last {s.days} days"
        parts.append(f"{s.total:,} contributions {span}")
    parts.append(plural(s.active_days, "active day"))
    if s.longest_streak >= 2:
        parts.append(f"longest streak {s.longest_streak} days")
    if s.current_streak >= 2:
        parts.append(f"current streak {s.current_streak} days")
    if s.best_count and s.best_date:
        parts.append(f"best day {s.best_count} ({MONTHS[s.best_date.month - 1]} {s.best_date.day})")
    while len(" | ".join(parts)) > STATS_MAX_CHARS and len(parts) > 1:
        parts.pop()  # drop the least important (last) segment rather than overflow
    return " | ".join(parts)


# ---------------------------------------------------------------------------- SVG
def render_svg(
    weeks: list[list[Cell | None]],
    labels: list[tuple[int, str]],
    footer: str,
    coverage: str,
    desc: str,
    *,
    animated: bool,
) -> str:
    t = THEME
    cols = len(weeks)
    if GRID_X0 + cols * PITCH - GAP > WIDTH - 24:
        raise ValueError(f"{cols} week columns do not fit in the {WIDTH}-unit canvas")

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="t d">',
        f"<title id=\"t\">{esc_text('GitHub contribution heatmap for ' + HANDLE.split('@')[0])}</title>",
        f'<desc id="d">{esc_text(desc)}</desc>',
    ]
    if animated:
        ks = sorted({c + r for c, w in enumerate(weeks) for r, cell in enumerate(w) if cell})
        delays = "".join(f".k{k}{{animation-delay:{0.25 + k * 0.022:.3f}s}}" for k in ks)
        parts.append(
            "<style>.c{animation:pop .45s ease-out both;transform-box:fill-box;transform-origin:center}"
            f"@keyframes pop{{from{{opacity:0;transform:scale(.4)}}to{{opacity:1;transform:scale(1)}}}}{delays}"
            f"{REDUCED_MOTION_CSS}</style>"
        )
    parts.append(window_chrome(WIDTH, HEIGHT, f"{HANDLE}: ~/contributions"))
    parts.append(f'<g font-family="{t.mono}" xml:space="preserve">')
    parts.append(
        f'<text x="24" y="{fmt(Y_PROMPT)}" font-size="13">'
        f'<tspan fill="{t.green}" font-weight="700">{esc_text(HANDLE)}</tspan><tspan fill="{t.fg}"> </tspan>'
        f'<tspan fill="{t.blue}">~</tspan><tspan fill="{t.fg}"> $ ./contributions.sh</tspan></text>'
    )
    parts.append(f'<g font-size="11" fill="{t.muted}">')
    for col, name in labels:
        parts.append(f'<text x="{fmt(GRID_X0 + col * PITCH)}" y="{fmt(Y_MONTHS)}">{esc_text(name)}</text>')
    for row, name in WEEKDAY_LABELS.items():
        parts.append(
            f'<text x="{fmt(GRID_X0 - 10)}" y="{fmt(GRID_Y0 + row * PITCH + 11)}" text-anchor="end">{name}</text>'
        )
    parts.append("</g>")

    cells = []
    for col, week in enumerate(weeks):
        for row, cell in enumerate(week):
            if cell is None:
                continue
            cls = f' class="c k{col + row}"' if animated else ""
            title = f"<title>{esc_text(cell['title'])}</title>" if cell["title"] else ""
            cells.append(
                f'<rect{cls} x="{fmt(GRID_X0 + col * PITCH)}" y="{fmt(GRID_Y0 + row * PITCH)}" width="{CELL}" '
                f'height="{CELL}" rx="3" fill="{t.heat[cell["level"]]}">{title}</rect>'
            )
    parts.append("<g>" + "".join(cells) + "</g>")

    # Footer: verified statistics on the left, Less-to-More legend on the right.
    parts.append(f'<text x="{fmt(GRID_X0)}" y="{fmt(Y_STATS)}" font-size="12" fill="{t.fg}">{esc_text(footer)}</text>')
    parts.append(f'<text x="{fmt(GRID_X0)}" y="{fmt(Y_SOURCE)}" font-size="10.5" fill="{t.dim}">{esc_text(coverage)}</text>')
    sq0 = 844
    parts.append(f'<g font-size="11" fill="{t.muted}"><text x="{sq0 - 6}" y="{fmt(Y_STATS)}" text-anchor="end">Less</text>')
    for lv, color in enumerate(t.heat):
        parts.append(
            f'<rect x="{sq0 + lv * 16}" y="{fmt(Y_STATS - 11)}" width="12" height="12" rx="3" fill="{color}"/>'
        )
    parts.append(f'<text x="{sq0 + 5 * 16 + 2}" y="{fmt(Y_STATS)}">More</text></g>')
    parts.append("</g></svg>")
    return "\n".join(parts) + "\n"


def render_from_document(doc: dict[str, Any], *, animated: bool) -> str:
    validate_document(doc)
    stats = compute_stats(doc)
    weeks, dates = build_weeks(doc["days"])
    footer = stats_text(stats)
    coverage = (
        f"Coverage {stats.first} to {stats.last} | data: github.com/users/{doc['user']}/contributions"
        f" | refreshed {doc['as_of']}"
    )
    return render_svg(weeks, month_labels(dates), footer, coverage, f"{footer}. {coverage}.", animated=animated)


def render_placeholder(user: str, *, animated: bool) -> str:
    """Empty 53x7 grid with an honest 'waiting for data' caption (no numbers, no dates, nothing invented)."""
    weeks: list[list[Cell | None]] = [[{"level": 0, "title": None} for _ in range(7)] for _ in range(53)]
    note = "Waiting for the first data refresh"
    how = f"Run scripts/fetch_contributions.py then scripts/render_heatmap_svg.py (user: {user})"
    return render_svg(weeks, [], note, how, f"{note}. {how}.", animated=animated)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", type=Path, default=DATA_DIR / "contributions.json")
    p.add_argument("--out", type=Path, default=ASSETS_DIR / "contrib-heatmap.svg")
    p.add_argument("--static", action="store_true", help="no animation (also: STATIC=1)")
    p.add_argument("--placeholder", action="store_true", help="draw an empty 'waiting for data' grid (bootstrap only)")
    p.add_argument("--user", default="karthik-vana", help="used only by --placeholder")
    args = p.parse_args(argv)

    animated = not (args.static or env_flag("STATIC"))
    try:
        if args.placeholder:
            svg = render_placeholder(args.user, animated=animated)
        else:
            doc = json.loads(args.input.read_text(encoding="utf-8"))
            svg = render_from_document(doc, animated=animated)
        parse_svg(svg)  # refuse to replace a good file with malformed XML
        if "<script" in svg or "foreignObject" in svg:
            raise ValueError("unexpected active content in generated SVG")
    except (OSError, ValueError, DataError, KeyError) as exc:  # json.JSONDecodeError is a ValueError
        print(f"error: {exc}", file=sys.stderr)
        print(f"The existing {args.out.name} (if any) was left untouched.", file=sys.stderr)
        return 2
    write_text_atomic(args.out, svg)
    print(f"Wrote {args.out}  ({len(svg) / 1024:.1f} KiB, {'animated' if animated else 'static'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
