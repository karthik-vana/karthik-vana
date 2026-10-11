#!/usr/bin/env python3
"""Turn the prepared portrait into a self-contained ASCII-art SVG (animated + static twin).

Input  : build/portrait_prepped.png  (luminance + alpha, from scripts/prep_photo.py)
Output : assets/karthik-ascii.svg         typed-out reveal, one-shot, ends on the finished portrait
         assets/karthik-ascii-static.svg  identical artwork, no animation

How the picture is built
  * The photo is area-averaged onto a ``cols x rows`` grid; ``rows`` follows from the
    glyph-cell aspect ratio so the face is not stretched.
  * Brightness picks a glyph from a density ramp. The default polarity is *light-on-dark*:
    lit areas get dense glyphs, which is what makes a dark canvas read as a positive
    photograph. ``--polarity print`` gives the print-style mapping (dense = shadow) on a
    light panel.
  * Brightness also picks one of a few grey tiers, so highlights are bright and shadows
    stay dim. Everything stays monochrome; the only colour is the green typing cursor.
  * Every text row is pinned to the grid with ``textLength`` so columns line up in any
    monospace font.

Animation (CSS, inside the SVG): a panel-coloured "cover" over each row shrinks left to
right in one-column steps (typewriter), rows start in a staggered cascade top to bottom,
and a small cursor rides the leading edge. Base styles are the *finished* state, so
renderers without animation support, and viewers who prefer reduced motion, see the
complete portrait.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, replace
from pathlib import Path

try:
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:  # pragma: no cover
    sys.exit(
        f"Missing dependency: {exc.name}. "
        "Install the portrait requirements first: pip install -r requirements-portrait.txt"
    )

from common import (
    ASSETS_DIR,
    BUILD_DIR,
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

DEFAULT_RAMP = " .':;+=xo%#8M@"  # monotonic in measured ink density (DejaVu/Liberation Mono)
RAMP_PRESETS = {
    "default": DEFAULT_RAMP,
    "spec": " .`:-=+*cs#%@",  # the ramp suggested in the brief; not strictly monotonic in ink
    "classic": " .:-=+*#%@",
}
# Grey tiers, dim -> bright (light-on-dark) or light -> dark (print). The tier count picks the last N.
TIERS_DARK = ("#5c6570", "#8d96a0", "#c5ccd4", "#f3f6fa")
TIERS_PRINT = ("#8a929b", "#5a626b", "#343a42", "#12161b")
PANEL_PRINT = "#e6edf3"
SAFE_GLYPHS = {chr(c) for c in range(0x20, 0x7F)}
# Browsers collapse runs of ordinary spaces in SVG text even with xml:space="preserve" (measured in
# Chromium: 64 characters rendered as 20, shifting every glyph out of its grid cell). A no-break space
# is never collapsed and has the same advance in monospace fonts, so interior blanks use U+00A0.
NBSP = "\u00a0"


@dataclass(frozen=True)
class AsciiConfig:
    """Every tunable knob in one place (CLI flags map onto these fields)."""

    cols: int = 130  # character columns: more = finer detail, smaller glyphs
    cell_aspect: float = 0.58  # glyph cell width / height (font-aspect-ratio correction)
    ramp: str = DEFAULT_RAMP
    polarity: str = "light-on-dark"  # or "print"
    contrast: float = 1.30  # >1 pushes tones apart around mid-grey (sharper eyes, brows, smile)
    gamma: float = 0.55  # <1 lifts mid-tones: glyph ink is compressive, so this matches the photo better
    floor: int = 2  # lowest ramp index used inside the subject, keeps hair/beard visible
    p_lo: float = 1.5  # percentile mapped to black
    p_hi: float = 99.5  # percentile mapped to white
    tiers: int = 4  # grey tiers (1 = single colour)
    # Tone thresholds between the grey tiers. Set high on purpose: skin sits in the light-grey tier and
    # pure white is kept for real highlights (teeth, eyes, forehead sheen), which keeps the portrait dark.
    tier_edges: tuple[float, ...] = (0.42, 0.68, 0.90)
    # Synthetic weight: a stroke in the glyph colour thickens strokes identically in every font.
    # Values are fractions of the font size, dim -> bright tier; glyphs only cover ~20% of a cell,
    # so without this the portrait reads as dim grey haze on a dark canvas.
    stroke_em: tuple[float, ...] = (0.0, 0.0, 0.03, 0.06)
    weight: float = 1.25  # multiplier for stroke_em: 0 = plain glyphs, 1 = light, 1.25 = default, 2 = heavy
    alpha_cut: float = 0.35  # cells with less subject coverage stay empty
    # SVG geometry, in user units (the SVG is later shown at ~41% width, so units are generous).
    width: int = 1000
    height: int = 1200
    window_width: int = 992  # leaves a small gutter next to the info card
    scale: float = 2.0  # window chrome scale; 2x so it matches the 1x info card when displayed
    pad_x: float = 28.0
    pad_bottom: float = 28.0
    gap_below_bar: float = 24.0
    title: str = "karthik@github: ~/portrait"
    # animation
    total_seconds: float = 4.2
    row_seconds: float = 0.6
    start_delay: float = 0.3
    cursor: bool = True


@dataclass(frozen=True)
class Layout:
    cols: int
    rows: int
    cw: float  # cell width
    ch: float  # cell height (row pitch)
    font_size: float
    x0: float
    y0: float
    baseline: float  # offset from a row's top to its text baseline

    @property
    def art_w(self) -> float:
        return self.cw * self.cols

    @property
    def art_h(self) -> float:
        return self.ch * self.rows


# ------------------------------------------------------------------ image -> grid
def load_prepared(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Return (luminance, alpha) as float32 arrays in [0, 1]."""
    if not path.is_file():
        raise FileNotFoundError(
            f"Prepared portrait not found: {path}\n  Run: python scripts/prep_photo.py --input photo/karthik_vana.jpg"
        )
    la = np.asarray(Image.open(path).convert("LA"), dtype=np.float32) / 255.0
    return la[..., 0], la[..., 1]


def compute_layout(cfg: AsciiConfig, img_w: int, img_h: int) -> Layout:
    """Fit the character grid into the art area, keeping the photo's proportions."""
    bar = 34.0 * cfg.scale
    avail_w = cfg.window_width - 2 * cfg.pad_x
    avail_h = cfg.height - (bar + cfg.gap_below_bar) - cfg.pad_bottom
    rows = max(8, round(cfg.cols * cfg.cell_aspect * img_h / img_w))
    cw = avail_w / cfg.cols
    ch = cw / cfg.cell_aspect
    if rows * ch > avail_h:  # photo is taller than the art area: fit by height instead
        ch = avail_h / rows
        cw = ch * cfg.cell_aspect
    font_size = cw / MONO_ADVANCE
    return Layout(
        cols=cfg.cols,
        rows=rows,
        cw=cw,
        ch=ch,
        font_size=font_size,
        x0=cfg.pad_x + (avail_w - cw * cfg.cols) / 2,
        y0=bar + cfg.gap_below_bar + (avail_h - ch * rows) / 2,
        baseline=ch * 0.5 + 0.35 * font_size,
    )


def sample_grid(lum: np.ndarray, alpha: np.ndarray, cols: int, rows: int) -> tuple[np.ndarray, np.ndarray]:
    """Alpha-weighted area average, so edge cells are not dragged toward the neutral background."""
    a = cv2.resize(alpha, (cols, rows), interpolation=cv2.INTER_AREA)
    weighted = cv2.resize(lum * alpha, (cols, rows), interpolation=cv2.INTER_AREA)
    return weighted / np.maximum(a, 1e-4), a


def tone_to_glyphs(lum: np.ndarray, alpha: np.ndarray, cfg: AsciiConfig) -> tuple[np.ndarray, np.ndarray]:
    """Per-cell ramp index and grey tier. Index 0 (space) is reserved for the background."""
    ramp_n = len(cfg.ramp)
    inside = alpha >= cfg.alpha_cut
    if not inside.any():
        raise ValueError("The prepared portrait has no subject pixels; check the cut-out step.")
    lo, hi = np.percentile(lum[inside], [cfg.p_lo, cfg.p_hi])
    t = np.clip((lum - lo) / max(float(hi - lo), 1e-6), 0.0, 1.0) ** cfg.gamma
    t = np.clip(0.5 + (t - 0.5) * cfg.contrast, 0.0, 1.0)
    ink = t if cfg.polarity == "light-on-dark" else 1.0 - t  # dense glyphs = "more ink"
    idx = np.rint(cfg.floor + ink * (ramp_n - 1 - cfg.floor)).astype(int)
    soft_edge = inside & (alpha < 0.75)
    idx[soft_edge] = np.minimum(idx[soft_edge], 2)
    idx[~inside] = 0
    edges = cfg.tier_edges if cfg.tiers == len(cfg.tier_edges) + 1 else tuple(np.linspace(0, 1, cfg.tiers + 1)[1:-1])
    tier = np.digitize(ink, edges).clip(0, cfg.tiers - 1)
    return idx, tier


def build_runs(idx: np.ndarray, tier: np.ndarray, cfg: AsciiConfig) -> list[list[tuple[int, str] | None]]:
    """For each row and tier: (first_col, text) with interior spaces kept, or None if the tier is empty."""
    out: list[list[tuple[int, str] | None]] = []
    for r in range(idx.shape[0]):
        per_tier: list[tuple[int, str] | None] = []
        for k in range(cfg.tiers):
            cols = np.nonzero((tier[r] == k) & (idx[r] > 0))[0]
            if cols.size == 0:
                per_tier.append(None)
                continue
            a, b = int(cols[0]), int(cols[-1])
            chars = [" "] * (b - a + 1)
            for c in cols:
                chars[int(c) - a] = cfg.ramp[int(idx[r, c])]
            per_tier.append((a, "".join(chars)))
        out.append(per_tier)
    return out


# ------------------------------------------------------------------------- SVG
def tier_strokes(cfg: AsciiConfig) -> tuple[float, ...]:
    """Stroke widths (fractions of the font size) for the active tiers."""
    return tuple(w * cfg.weight for w in cfg.stroke_em[-cfg.tiers :])


def palette(cfg: AsciiConfig) -> tuple[str, tuple[str, ...]]:
    """(panel colour, tier colours) for the chosen polarity."""
    if cfg.polarity == "light-on-dark":
        return THEME.bg, TIERS_DARK[-cfg.tiers :]
    return PANEL_PRINT, TIERS_PRINT[-cfg.tiers :]


def build_svg(runs: list[list[tuple[int, str] | None]], cfg: AsciiConfig, lay: Layout, *, animated: bool) -> str:
    panel, colors = palette(cfg)
    xr = lay.x0 + lay.art_w
    stagger = max(0.0, (cfg.total_seconds - cfg.row_seconds - cfg.start_delay) / max(lay.rows - 1, 1))
    desc = (
        "Monochrome ASCII-art portrait generated from a photograph. "
        + ("It is typed out row by row once and then stays still." if animated else "Static version.")
    )
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{cfg.width}" height="{cfg.height}" '
        f'viewBox="0 0 {cfg.width} {cfg.height}" role="img" aria-labelledby="t d">',
        f"<title id=\"t\">{esc_text('ASCII portrait of Karthik Vana')}</title>",
        f'<desc id="d">{esc_text(desc)}</desc>',
    ]
    if animated:
        keyframes = (
            "@keyframes rv{from{opacity:1;transform:scaleX(1)}to{opacity:1;transform:scaleX(0)}}"
            f"@keyframes mv{{from{{opacity:1;transform:translateX(0)}}to{{opacity:1;transform:translateX({fmt(lay.art_w)}px)}}}}"
            "@keyframes hd{to{opacity:0}}"
        )
        # steps(n, start) rounds *up*, so a progress value that is a hair below 1.0 (floating-point at the
        # end of the interval, seen in Chromium with steps(n, end)) still lands on the final step. On top of
        # that, each cover is explicitly hidden once its row is done, so no sliver can ever stay behind.
        rules = (
            f".cv{{opacity:0;transform-origin:{fmt(xr + 1)}px 0;animation:rv {fmt(cfg.row_seconds)}s steps({lay.cols},start) both,hd .01s linear forwards}}"
            f".cu{{opacity:0;animation:mv {fmt(cfg.row_seconds)}s steps({lay.cols},start) forwards,hd .01s linear forwards}}"
        )
        parts.append(f"<style>{keyframes}{rules}{REDUCED_MOTION_CSS}</style>")
    parts.append(window_chrome(cfg.window_width, cfg.height, cfg.title, scale=cfg.scale))
    if cfg.polarity != "light-on-dark":  # print polarity: a light "paper" panel inside the window
        px, py = lay.x0 - 14, lay.y0 - 14
        parts.append(
            f'<rect x="{fmt(px)}" y="{fmt(py)}" width="{fmt(lay.art_w + 28)}" height="{fmt(lay.art_h + 28)}" '
            f'rx="8" fill="{panel}"/>'
        )

    parts.append(
        f'<g font-family="{THEME.mono}" font-size="{fmt(lay.font_size)}" xml:space="preserve" style="white-space:pre">'
    )
    for k, color in enumerate(colors):
        texts = []
        for r, per_tier in enumerate(runs):
            item = per_tier[k]
            if item is None:
                continue
            a, text = item
            texts.append(
                f'<text x="{fmt(lay.x0 + a * lay.cw)}" y="{fmt(lay.y0 + r * lay.ch + lay.baseline)}" '
                f'textLength="{fmt(len(text) * lay.cw)}" lengthAdjust="spacing">{esc_text(text.replace(" ", NBSP))}</text>'
            )
        if texts:
            sw = tier_strokes(cfg)[k] * lay.font_size
            weight = f' stroke="{color}" stroke-width="{fmt(sw)}" stroke-linejoin="round"' if sw > 0.05 else ""
            parts.append(f'<g fill="{color}"{weight}>{"".join(texts)}</g>')
    parts.append("</g>")

    if animated:
        covers, cursors = [], []
        for r in range(lay.rows):
            delay = cfg.start_delay + r * stagger
            top = lay.y0 + r * lay.ch
            covers.append(
                f'<rect class="cv" x="{fmt(lay.x0 - 1)}" y="{fmt(top - 0.3)}" width="{fmt(lay.art_w + 2)}" '
                f'height="{fmt(lay.ch + 0.6)}" style="animation-delay:{delay:.3f}s,{delay + cfg.row_seconds:.3f}s"/>'
            )
            if cfg.cursor:
                cursors.append(
                    f'<rect class="cu" x="{fmt(lay.x0)}" y="{fmt(top + lay.ch * 0.08)}" width="{fmt(lay.cw)}" '
                    f'height="{fmt(lay.ch * 0.84)}" style="animation-delay:{delay:.3f}s,{delay + cfg.row_seconds:.3f}s"/>'
                )
        parts.append(f'<g fill="{panel}">{"".join(covers)}</g>')
        if cursors:
            parts.append(f'<g fill="{THEME.green}" fill-opacity=".9">{"".join(cursors)}</g>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------- PNG preview
def find_mono_font() -> Path | None:
    for cand in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/cour.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
    ):
        if Path(cand).is_file():
            return Path(cand)
    return None


def render_preview(
    runs: list[list[tuple[int, str] | None]], cfg: AsciiConfig, lay: Layout, out: Path, display_width: int
) -> bool:
    """Rasterise the glyph grid with Pillow (an approximation of how a browser draws the SVG)."""
    font_path = find_mono_font()
    if font_path is None:
        print("  [skip] preview PNG: no monospace TrueType font found")
        return False
    panel, colors = palette(cfg)
    strokes = tier_strokes(cfg)
    ss, margin = 2, 16  # draw at 2x, then downscale: closer to a browser's anti-aliasing than hinted tiny text
    img = Image.new("RGB", (round((lay.art_w + 2 * margin) * ss), round((lay.art_h + 2 * margin) * ss)), panel)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(font_path), lay.font_size * ss)
    for r, per_tier in enumerate(runs):
        for k, item in enumerate(per_tier):
            if item is None:
                continue
            a, text = item
            width = round(strokes[k] * lay.font_size * ss / 2)  # PIL strokes grow both ways
            for j, glyph in enumerate(text):
                if glyph != " ":
                    draw.text(
                        ((margin + (a + j) * lay.cw) * ss, (margin + r * lay.ch + lay.baseline) * ss),
                        glyph,
                        font=font,
                        fill=colors[k],
                        anchor="ls",
                        stroke_width=width,
                        stroke_fill=colors[k],
                    )
    target = display_width or round(img.width / ss)
    img = img.resize((target, round(img.height * target / img.width)), Image.Resampling.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return True


# ------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    d = AsciiConfig()
    p.add_argument("--input", type=Path, default=BUILD_DIR / "portrait_prepped.png")
    p.add_argument("--out", type=Path, default=ASSETS_DIR / "karthik-ascii.svg", help="animated SVG")
    p.add_argument("--out-static", type=Path, default=ASSETS_DIR / "karthik-ascii-static.svg", help="static SVG")
    p.add_argument("--static", action="store_true", help="write only the static SVG (also: STATIC=1)")
    p.add_argument("--cols", type=int, default=d.cols, help="character columns (default %(default)s)")
    p.add_argument("--cell-aspect", type=float, default=d.cell_aspect, help="glyph cell width/height")
    p.add_argument("--ramp", default=None, help="custom density ramp, sparse -> dense, starting with a space")
    p.add_argument("--ramp-preset", choices=sorted(RAMP_PRESETS), default="default")
    p.add_argument("--polarity", choices=["light-on-dark", "print"], default=d.polarity)
    p.add_argument("--contrast", type=float, default=d.contrast)
    p.add_argument("--gamma", type=float, default=d.gamma)
    p.add_argument("--floor", type=int, default=d.floor, help="lowest ramp index inside the subject")
    p.add_argument("--tiers", type=int, default=d.tiers, choices=[1, 2, 3, 4], help="grey tiers")
    p.add_argument("--weight", type=float, default=d.weight, help="synthetic boldness: 0 plain, 1 default, 2 heavy")
    p.add_argument("--duration", type=float, default=d.total_seconds, help="total reveal time in seconds")
    p.add_argument("--row-duration", type=float, default=d.row_seconds, help="time to type one row")
    p.add_argument("--no-cursor", action="store_true", help="omit the typing cursor")
    p.add_argument("--title", default=d.title, help="window title text")
    p.add_argument("--preview-png", type=Path, default=None, help="also write a Pillow-rendered PNG preview")
    p.add_argument("--preview-width", type=int, default=415, help="preview display width (0 = full size)")
    return p


def config_from_args(args: argparse.Namespace) -> AsciiConfig:
    ramp = args.ramp if args.ramp is not None else RAMP_PRESETS[args.ramp_preset]
    if len(ramp) < 3 or ramp[0] != " " or not set(ramp) <= SAFE_GLYPHS:
        raise ValueError("--ramp must be 3+ printable ASCII characters and start with a space")
    if not 30 <= args.cols <= 400:
        raise ValueError("--cols must be between 30 and 400")
    if not 0.3 <= args.cell_aspect <= 1.0:
        raise ValueError("--cell-aspect must be between 0.3 and 1.0")
    if not 0 <= args.floor < len(ramp) - 1:
        raise ValueError("--floor must be >= 0 and smaller than the ramp length - 1")
    if not 0 <= args.weight <= 4:
        raise ValueError("--weight must be between 0 and 4")
    if args.contrast <= 0 or args.gamma <= 0 or args.duration <= args.row_duration or args.row_duration <= 0:
        raise ValueError("contrast/gamma/row-duration must be positive and --duration must exceed --row-duration")
    return replace(
        AsciiConfig(),
        cols=args.cols,
        cell_aspect=args.cell_aspect,
        ramp=ramp,
        polarity=args.polarity,
        contrast=args.contrast,
        gamma=args.gamma,
        floor=args.floor,
        tiers=args.tiers,
        weight=args.weight,
        total_seconds=args.duration,
        row_seconds=args.row_duration,
        cursor=not args.no_cursor,
        title=args.title,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = config_from_args(args)
        lum, alpha = load_prepared(args.input)
        lay = compute_layout(cfg, lum.shape[1], lum.shape[0])
        g_lum, g_alpha = sample_grid(lum, alpha, lay.cols, lay.rows)
        idx, tier = tone_to_glyphs(g_lum, g_alpha, cfg)
        runs = build_runs(idx, tier, cfg)
    except (OSError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    static_only = args.static or env_flag("STATIC")
    targets = [(args.out_static, False)] if static_only else [(args.out, True), (args.out_static, False)]
    for path, animated in targets:
        svg = build_svg(runs, cfg, lay, animated=animated)
        parse_svg(svg)  # refuse to write anything that is not well-formed
        if "<script" in svg or "foreignObject" in svg:
            raise RuntimeError("unexpected active content in generated SVG")
        write_text_atomic(path, svg)
        print(f"Wrote {path}  ({len(svg) / 1024:.1f} KiB, {lay.cols}x{lay.rows} glyphs, {'animated' if animated else 'static'})")
    if args.preview_png and render_preview(runs, cfg, lay, args.preview_png, args.preview_width):
        print(f"Preview PNG: {args.preview_png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
