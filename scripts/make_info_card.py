#!/usr/bin/env python3
"""Generate the neofetch-style terminal info card.

Output : assets/info-card.svg         rows print in one after another (opacity + 5 px rise), once
         assets/info-card-static.svg  identical card, no animation

Content lives in ``scripts/profile_config.py``. Set ``STATIC=1`` (or pass ``--static``) to write
only the non-animated card. Uses only the standard library, so it also runs in CI.

Animation is CSS inside the SVG. The base style of every row is its final, visible state, so
renderers without animation support and viewers who prefer reduced motion see the finished card.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import (
    ASSETS_DIR,
    MONO_ADVANCE,
    REDUCED_MOTION_CSS,
    THEME,
    env_flag,
    esc_text,
    fmt,
    parse_svg,
    window_chrome,
    write_text_atomic,
)
from profile_config import CARD, CardConfig

WIDTH, HEIGHT = 500, 600  # user units; displayed at ~49.5% of the README width
GUTTER = 4  # transparent strip on the left so neighbouring windows do not touch
WINDOW_W = WIDTH - GUTTER
FONT_SIZE = 13.5
CW = FONT_SIZE * MONO_ADVANCE  # nominal advance width of one character
PAD_X = 24.0
LABEL_COLS = 12  # value column, in characters from the left padding
RIGHT_LIMIT = WINDOW_W - 16.0  # no text may extend beyond this x

Y_PROMPT, Y_HANDLE, Y_RULE, Y_ROW0, ROW_STEP = 66.0, 98.0, 120.0, 148.0, 24.0

PALETTE = ("#0e4429", "#006d32", "#26a641", "#39d353", "#8b949e", "#c9d1d9", "#58a6ff", "#e6edf3")


class CardError(ValueError):
    """The card content does not fit or is malformed."""


def _check_fit(text: str, x: float, what: str) -> None:
    right = x + len(text) * CW
    if right > RIGHT_LIMIT:
        raise CardError(
            f"{what} {text!r} would end at x={right:.0f}, beyond the card edge ({RIGHT_LIMIT:.0f}); shorten it"
        )


def _text(x: float, y: float, content: str, fill: str, weight: str = "") -> str:
    w = f' font-weight="{weight}"' if weight else ""
    return f'<text x="{fmt(x)}" y="{fmt(y)}" fill="{fill}"{w}>{esc_text(content)}</text>'


def _prompt_line(y: float, handle: str, command: str, cursor: bool) -> str:
    """``karthik@github ~ $ whoami`` assembled from spans so the colours stay in one text run."""
    t = THEME
    spans = (
        f'<tspan fill="{t.green}" font-weight="700">{esc_text(handle)}</tspan>'
        f'<tspan fill="{t.fg}"> </tspan><tspan fill="{t.blue}">~</tspan>'
        f'<tspan fill="{t.fg}"> $ {esc_text(command)}</tspan>'
    )
    out = f'<text x="{fmt(PAD_X)}" y="{fmt(y)}" xml:space="preserve">{spans}</text>'
    if cursor:  # static block cursor after the prompt (no blinking: the card never loops)
        chars = len(handle) + len(f" ~ $ {command}")
        out += (
            f'<rect x="{fmt(PAD_X + chars * CW + 2)}" y="{fmt(y - FONT_SIZE + 2)}" width="{fmt(CW)}" '
            f'height="{fmt(FONT_SIZE)}" fill="{t.green}" fill-opacity=".85"/>'
        )
    return out


def build_lines(card: CardConfig) -> list[str]:
    """Each element is the SVG for one logical terminal line (animated as a unit)."""
    t = THEME
    for row in card.rows:
        _check_fit(row.value, PAD_X + LABEL_COLS * CW, f"value of {row.label or 'continuation row'}")
        if len(row.label) > LABEL_COLS - 2:
            raise CardError(f"label {row.label!r} is too long for the label column")
    _check_fit(card.statement, PAD_X + 2 * CW, "statement")
    _check_fit(f"{card.handle} ~ $ {card.command}", PAD_X, "prompt")

    lines = [
        _prompt_line(Y_PROMPT, card.handle, card.command, cursor=False),
        _text(PAD_X, Y_HANDLE, card.handle, t.green, "700"),
        _text(PAD_X, Y_RULE, "-" * 30, t.dim),
    ]
    for i, row in enumerate(card.rows):
        y = Y_ROW0 + i * ROW_STEP
        parts = []
        if row.label:
            parts.append(_text(PAD_X, y, row.label, t.green, "700"))
            parts.append(_text(PAD_X + (LABEL_COLS - 2) * CW, y, ":", t.muted))
        parts.append(_text(PAD_X + LABEL_COLS * CW, y, row.value, t.fg))
        lines.append("".join(parts))

    y_pal = Y_ROW0 + len(card.rows) * ROW_STEP - 6
    swatches = "".join(
        f'<rect x="{fmt(PAD_X + k * 26)}" y="{fmt(y_pal)}" width="22" height="12" rx="2" fill="{c}"/>'
        for k, c in enumerate(PALETTE)
    )
    lines.append(swatches)
    lines.append(_text(PAD_X, y_pal + 36, f"# {card.statement}", t.dim))
    lines.append(_prompt_line(y_pal + 62, card.handle, "", cursor=True))
    bottom = y_pal + 62 + 8
    if bottom > HEIGHT - 12:
        raise CardError(f"card content ends at y={bottom:.0f}; the card is only {HEIGHT} tall - remove a row")
    return lines


def build_svg(card: CardConfig, *, animated: bool) -> str:
    lines = build_lines(card)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="t d">',
        f"<title id=\"t\">{esc_text('Terminal info card: ' + card.rows[0].value)}</title>",
        f'<desc id="d">{esc_text("; ".join(f"{r.label}: {r.value}" for r in card.rows if r.label))}</desc>',
    ]
    if animated:
        parts.append(
            "<style>.ln{animation:ln .5s ease-out both}"
            "@keyframes ln{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:translateY(0)}}"
            f"{REDUCED_MOTION_CSS}</style>"
        )
    parts.append(f'<g transform="translate({GUTTER} 0)">')
    parts.append(window_chrome(WINDOW_W, HEIGHT, card.title))
    parts.append(f'<g font-family="{THEME.mono}" font-size="{fmt(FONT_SIZE)}" xml:space="preserve">')
    for i, line in enumerate(lines):
        if animated:
            parts.append(f'<g class="ln" style="animation-delay:{0.25 + i * 0.11:.2f}s">{line}</g>')
        else:
            parts.append(f"<g>{line}</g>")
    parts.append("</g></g></svg>")
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=ASSETS_DIR / "info-card.svg", help="animated SVG")
    p.add_argument("--out-static", type=Path, default=ASSETS_DIR / "info-card-static.svg", help="static SVG")
    p.add_argument("--static", action="store_true", help="write only the static SVG (also: STATIC=1)")
    args = p.parse_args(argv)

    static_only = args.static or env_flag("STATIC")
    targets = [(args.out_static, False)] if static_only else [(args.out, True), (args.out_static, False)]
    try:
        for path, animated in targets:
            svg = build_svg(CARD, animated=animated)
            parse_svg(svg)  # never write malformed XML
            write_text_atomic(path, svg)
            print(f"Wrote {path}  ({len(svg) / 1024:.1f} KiB, {'animated' if animated else 'static'})")
    except (CardError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
