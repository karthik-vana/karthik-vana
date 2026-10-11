"""Shared paths, theme tokens and SVG helpers for the profile-art generators.

Everything visual (colours, fonts, terminal-window chrome) lives here so the
portrait, the info card and the heatmap stay consistent with each other.
Only the Python standard library is imported, so CI can use this module
without installing anything heavy.
"""
from __future__ import annotations

import os
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"
DATA_DIR = ROOT / "data"
BUILD_DIR = ROOT / "build"  # git-ignored scratch space (prepared portrait, previews)
PHOTO_DIR = ROOT / "photo"  # git-ignored; the source photograph lives here

GITHUB_USER = "karthik-vana"
HANDLE = "karthik@github"

# Advance width of a monospace glyph as a fraction of the font size. Real fonts
# range from ~0.55 (Consolas) to 0.60 (Menlo, DejaVu, Liberation, Courier).
# Generators that need exact columns pin them with SVG `textLength`.
MONO_ADVANCE = 0.6


@dataclass(frozen=True)
class Theme:
    """Colour and font tokens: GitHub-dark surfaces with a terminal-green accent."""

    bg: str = "#0d1117"
    title_bar: str = "#161b22"
    border: str = "#30363d"
    fg: str = "#e6edf3"
    muted: str = "#8b949e"
    dim: str = "#6e7681"
    green: str = "#3fb950"
    green_bright: str = "#56d364"
    blue: str = "#58a6ff"
    dot_red: str = "#ff5f56"
    dot_yellow: str = "#ffbd2e"
    dot_green: str = "#27c93f"
    mono: str = (
        "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
        "'Liberation Mono', 'DejaVu Sans Mono', monospace"
    )
    # GitHub dark-mode contribution scale, level 0 (none) .. 4 (most).
    heat: tuple[str, ...] = ("#161b22", "#0e4429", "#006d32", "#26a641", "#39d353")


THEME = Theme()

# Characters that are illegal in XML 1.0 (control codes, lone surrogates, U+FFFE/F).
_ILLEGAL_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff\ufffe\uffff]")


def clean_text(value: object) -> str:
    """Return ``str(value)`` without characters that XML 1.0 cannot represent."""
    return _ILLEGAL_XML.sub("", str(value))


def esc(value: object) -> str:
    """XML-escape text for element content *and* attribute values."""
    return _xml_escape(clean_text(value), {'"': "&quot;", "'": "&apos;"})


def esc_text(value: object) -> str:
    """XML-escape element content only (``&``, ``<``, ``>``); quotes stay literal to keep files small."""
    return _xml_escape(clean_text(value))


def fmt(x: float) -> str:
    """Compact, deterministic number formatting for SVG coordinates."""
    text = f"{x:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable (``1/true/yes/on`` are truthy)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def write_text_atomic(path: Path, text: str) -> None:
    """Write UTF-8 text via a temp file + ``os.replace`` so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(tmp_name, 0o644)  # mkstemp creates 0600; published assets should be world-readable
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def parse_svg(text: str) -> ET.Element:
    """Parse SVG text and return its root; raises ``ValueError`` if it is not well-formed SVG."""
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        raise ValueError(f"SVG is not well-formed XML: {exc}") from exc
    if not root.tag.endswith("}svg") and root.tag != "svg":
        raise ValueError(f"root element is <{root.tag}>, expected <svg>")
    return root


def window_chrome(
    width: float,
    height: float,
    title: str,
    *,
    scale: float = 1.0,
    bar_height: float = 34.0,
    radius: float = 12.0,
) -> str:
    """Terminal-window decoration: surface, title bar, three controls, centred title, border.

    ``scale`` multiplies every dimension so a window drawn in a 2x coordinate
    space (the portrait) looks identical to a 1x one once it is displayed.
    """
    s = scale
    bar = bar_height * s
    rad = radius * s
    inset = 0.5 * s  # half the border stroke, keeps the stroke inside the viewBox
    t = THEME
    dots = "".join(
        f'<circle cx="{fmt((18 + 18 * k) * s)}" cy="{fmt(bar / 2)}" r="{fmt(5.5 * s)}" fill="{c}"/>'
        for k, c in enumerate((t.dot_red, t.dot_yellow, t.dot_green))
    )
    return (
        f'<rect x="{fmt(inset)}" y="{fmt(inset)}" width="{fmt(width - 2 * inset)}" '
        f'height="{fmt(height - 2 * inset)}" rx="{fmt(rad)}" fill="{t.bg}"/>'
        f'<path d="M{fmt(inset)} {fmt(bar)}V{fmt(rad + inset)}Q{fmt(inset)} {fmt(inset)} {fmt(rad + inset)} {fmt(inset)}'
        f'H{fmt(width - rad - inset)}Q{fmt(width - inset)} {fmt(inset)} {fmt(width - inset)} {fmt(rad + inset)}'
        f'V{fmt(bar)}Z" fill="{t.title_bar}"/>'
        f'<path d="M{fmt(inset)} {fmt(bar)}H{fmt(width - inset)}" fill="none" stroke="{t.border}" '
        f'stroke-width="{fmt(s)}"/>'
        f"{dots}"
        f'<text x="{fmt(width / 2)}" y="{fmt(bar / 2 + 4 * s)}" text-anchor="middle" '
        f'font-family="{t.mono}" font-size="{fmt(12 * s)}" fill="{t.muted}">{esc(title)}</text>'
        f'<rect x="{fmt(inset)}" y="{fmt(inset)}" width="{fmt(width - 2 * inset)}" '
        f'height="{fmt(height - 2 * inset)}" rx="{fmt(rad)}" fill="none" stroke="{t.border}" '
        f'stroke-width="{fmt(s)}"/>'
    )


# Appended to every animated SVG: honour the viewer's "reduce motion" setting.
# Every animated element's *base* style is its final, fully visible state, so
# switching animations off simply shows the finished artwork.
REDUCED_MOTION_CSS = "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
