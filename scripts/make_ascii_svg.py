#!/usr/bin/env python3
"""Build self-contained animated and static ASCII portrait SVGs from a real photo."""
from __future__ import annotations
import argparse
import html
from pathlib import Path
from xml.etree import ElementTree as ET
from PIL import Image, ImageOps, ImageEnhance, UnidentifiedImageError

RAMP = " .':-=+*cs#%@"  # sparse highlights -> dense shadows on a light canvas
DEFAULT_INPUT = Path(".cache/karthik-prepared.png")
DEFAULT_ANIMATED = Path("assets/karthik-ascii.svg")
DEFAULT_STATIC = Path("assets/karthik-ascii-static.svg")


def ascii_lines(image_path: Path, columns: int, rows: int, contrast: float = 1.0) -> list[str]:
    """Downsample the actual portrait to a character grid with font aspect correction."""
    if not image_path.is_file():
        raise FileNotFoundError(f"Image not found: {image_path}. Run scripts/prep_photo.py first or pass --input.")
    try:
        img = Image.open(image_path).convert("L")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"Cannot read image {image_path}: {exc}") from exc
    img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Contrast(img).enhance(contrast)
    # Character cells are about 0.6 as wide as they are tall; compensate at grid sizing time.
    src_w, src_h = img.size
    cell_ratio = 0.60
    target_w_over_h = columns * cell_ratio / rows
    source_ratio = src_w / src_h
    if source_ratio > target_w_over_h:
        crop_w = int(src_h * target_w_over_h / cell_ratio * cell_ratio)  # kept for deterministic crop below
        # Use center-crop to the visual aspect ratio of the text grid.
        desired = target_w_over_h / cell_ratio
        new_w = max(1, min(src_w, int(src_h * desired)))
        left = (src_w - new_w) // 2
        img = img.crop((left, 0, left + new_w, src_h))
    else:
        desired = target_w_over_h / cell_ratio
        new_h = max(1, min(src_h, int(src_w / desired)))
        top = (src_h - new_h) // 2
        img = img.crop((0, top, src_w, top + new_h))
    img = img.resize((columns, rows), Image.Resampling.LANCZOS)
    pixels = img.load()
    output: list[str] = []
    for r in range(rows):
        chars = []
        for c in range(columns):
            brightness = pixels[c, r]
            darkness = 255 - brightness
            idx = round(darkness / 255 * (len(RAMP) - 1))
            chars.append(RAMP[idx])
        output.append("".join(chars).rstrip())
    return output


def build_svg(lines: list[str], *, animated: bool, columns: int, font_size: float, line_height: float, title: str) -> str:
    """Render line-based SVG; every row receives a finite one-shot horizontal reveal."""
    char_width = font_size * 0.60
    pad_x, top, header_h, pad_bottom = 18.0, 17.0, 32.0, 16.0
    body_w = columns * char_width + pad_x * 2
    body_h = len(lines) * line_height + 18.0
    width = body_w
    height = top + header_h + body_h + pad_bottom
    svg: list[str] = [
      f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.1f}" height="{height:.1f}" viewBox="0 0 {width:.1f} {height:.1f}" role="img" aria-labelledby="title desc">',
      f'<title id="title">{html.escape(title)}</title>',
      '<desc id="desc">Monochrome ASCII portrait generated from Karthik Vana’s supplied photograph. The animated variant reveals rows once from top to bottom.</desc>',
      '<defs><clipPath id="frame"><rect x="0" y="0" width="100%" height="100%" rx="9"/></clipPath></defs>',
      '<rect x="0.5" y="0.5" width="99%" height="99%" rx="10" fill="#0d1117" stroke="#30363d"/>',
      f'<path d="M10 1 H{width-10:.1f} Q{width-0.5:.1f} 1 {width-0.5:.1f} 11 V{top+header_h:.1f} H0.5 V11 Q0.5 1 10 1Z" fill="#161b22"/>',
      '<circle cx="17" cy="17" r="4" fill="#ff5f56"/><circle cx="31" cy="17" r="4" fill="#ffbd2e"/><circle cx="45" cy="17" r="4" fill="#27c93f"/>',
      f'<text x="{width/2:.1f}" y="21" font-family="Consolas,Monaco,monospace" font-size="10" text-anchor="middle" fill="#8b949e">{html.escape(title)}</text>',
      f'<rect x="8" y="{top+header_h:.1f}" width="{width-16:.1f}" height="{body_h-5:.1f}" rx="4" fill="#e6edf3"/>'
    ]
    clip_defs=[]
    text_parts=[]
    for row, line in enumerate(lines):
        y = top + header_h + 13 + (row + 1) * line_height
        clip_id=f"row-{row}"
        if animated:
            clip_defs.append(f'<clipPath id="{clip_id}"><rect x="{pad_x:.1f}" y="{y-font_size:.1f}" width="0" height="{line_height+1:.1f}"><animate attributeName="width" from="0" to="{columns*char_width:.1f}" dur="0.32s" begin="{row*0.032:.3f}s" fill="freeze"/></rect></clipPath>')
            text_parts.append(f'<text x="{pad_x:.1f}" y="{y:.1f}" font-family="Consolas,Monaco,\'Liberation Mono\',monospace" font-size="{font_size:.2f}" xml:space="preserve" fill="#20262e" clip-path="url(#{clip_id})">{html.escape(line)}</text>')
        else:
            text_parts.append(f'<text x="{pad_x:.1f}" y="{y:.1f}" font-family="Consolas,Monaco,\'Liberation Mono\',monospace" font-size="{font_size:.2f}" xml:space="preserve" fill="#20262e">{html.escape(line)}</text>')
    if clip_defs:
        svg.insert(3, '<defs>' + ''.join(clip_defs) + '</defs>')
    svg.extend(text_parts)
    if animated:
        duration = max(0.32, (len(lines)-1)*0.032 + 0.32)
        svg.append(f'<text x="{width-19:.1f}" y="{height-10:.1f}" font-family="monospace" font-size="9" fill="#238636">$<animate attributeName="opacity" values="1;0;1" dur="0.75s" begin="{duration:.2f}s" repeatCount="1"/></text>')
    svg.append('</svg>')
    return '\n'.join(svg) + '\n'


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, default=DEFAULT_INPUT, help='Prepared or original portrait path')
    p.add_argument('--animated-output', type=Path, default=DEFAULT_ANIMATED)
    p.add_argument('--static-output', type=Path, default=DEFAULT_STATIC)
    p.add_argument('--columns', type=int, default=160)
    p.add_argument('--rows', type=int, default=100)
    p.add_argument('--font-size', type=float, default=8.4)
    p.add_argument('--line-height', type=float, default=8.3)
    p.add_argument('--contrast', type=float, default=1.0)
    p.add_argument('--title', default='karthik@github: ./portrait --ascii')
    args=p.parse_args()
    if args.columns < 30 or args.rows < 20 or args.font_size <= 0 or args.line_height <= 0 or args.contrast <= 0:
        p.error('columns >= 30, rows >= 20, and font-size/line-height/contrast must be positive')
    try:
        lines=ascii_lines(args.input,args.columns,args.rows,args.contrast)
        for path, animated in [(args.animated_output,True),(args.static_output,False)]:
            svg=build_svg(lines,animated=animated,columns=args.columns,font_size=args.font_size,line_height=args.line_height,title=args.title)
            ET.fromstring(svg)
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(svg,encoding='utf-8')
            print(f'Wrote {path} ({len(lines)} rows; {args.columns} columns)')
    except (OSError, ValueError) as exc:
        p.exit(2,f'error: {exc}\n')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
