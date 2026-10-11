#!/usr/bin/env python3
"""Prepare the source portrait for ASCII conversion (local, one-off step).

Pipeline
  1. Load and validate the photo (type, size, EXIF orientation).
  2. Cut the subject out of the background: rembg -> GrabCut -> soft vignette.
  3. Locate the head from the cut-out and crop head-and-shoulders to a fixed aspect.
  4. Grayscale -> CLAHE -> unsharp mask, computed on the subject only.
  5. Save an "LA" PNG (luminance + alpha) to ``build/portrait_prepped.png``.

Nothing written here is committed (``build/`` is git-ignored) and the daily
GitHub Actions job never runs this script, so the heavy imaging dependencies
stay out of CI.

Examples
  python scripts/prep_photo.py --input photo/karthik_vana.jpg
  python scripts/prep_photo.py --input photo/karthik_vana.jpg --bg grabcut
  python scripts/prep_photo.py --input photo/karthik_vana.jpg --crop 400,-30,1200,900
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import cv2
    import numpy as np
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError as exc:  # pragma: no cover - friendly message for a missing dependency
    sys.exit(
        f"Missing dependency: {exc.name}. "
        "Install the portrait requirements first: pip install -r requirements-portrait.txt"
    )

from common import BUILD_DIR, PHOTO_DIR, THEME

SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MAX_BYTES = 40 * 1024 * 1024
PREFERRED_NAMES = ("karthik_vana", "karthik-vana", "portrait", "photo", "Karthik_Vana(2)")

DEFAULT_OUTPUT = BUILD_DIR / "portrait_prepped.png"
DEFAULT_PREVIEW = BUILD_DIR / "portrait_prepped_preview.png"


class PrepError(RuntimeError):
    """A user-facing problem with the input photo or the options."""


# --------------------------------------------------------------------------- input
def find_default_input() -> Path:
    """Pick a photo from ``photo/``: a preferred file name first, otherwise the first image."""
    if PHOTO_DIR.is_dir():
        images = sorted(p for p in PHOTO_DIR.iterdir() if p.suffix.lower() in SUPPORTED_SUFFIXES)
        for stem in PREFERRED_NAMES:
            for p in images:
                if p.stem.lower() == stem.lower():
                    return p
        if images:
            return images[0]
    return PHOTO_DIR / "karthik_vana.jpg"


def load_image(path: Path, max_side: int) -> Image.Image:
    """Load, validate and normalise the photo to an RGB image no larger than ``max_side``."""
    if not path.is_file():
        raise PrepError(
            f"Portrait not found: {path}\n"
            f"  Put your photo at {PHOTO_DIR / 'karthik_vana.jpg'} or pass --input <file>."
        )
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise PrepError(f"Unsupported file type {path.suffix!r}; use one of {sorted(SUPPORTED_SUFFIXES)}.")
    size = path.stat().st_size
    if size == 0 or size > MAX_BYTES:
        raise PrepError(f"Photo size {size} bytes is outside the accepted range (1 B - {MAX_BYTES // 2**20} MiB).")
    try:
        with Image.open(path) as probe:
            probe.verify()  # cheap integrity check before the real decode
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise PrepError(f"Cannot read {path}: {exc}") from exc
    w, h = img.size
    if min(w, h) < 256:
        raise PrepError(f"Photo is too small ({w}x{h}); use at least 256 px on the short side.")
    if max(w, h) > max_side:
        k = max_side / max(w, h)
        img = img.resize((round(w * k), round(h * k)), Image.Resampling.LANCZOS)
    return img


# ------------------------------------------------------------------------- analysis
def detect_face(rgb: np.ndarray) -> tuple[int, int, int, int] | None:
    """Largest frontal face as ``(x, y, w, h)`` via OpenCV's bundled Haar cascade, or ``None``."""
    cascade = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
    if cascade.empty():
        return None
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(w // 10, h // 10))
    if len(faces) == 0:
        return None
    x, y, fw, fh = max(faces, key=lambda f: int(f[2]) * int(f[3]))
    return int(x), int(y), int(fw), int(fh)


# ----------------------------------------------------------------- background removal
def mask_rembg(rgb: np.ndarray, model: str) -> np.ndarray:
    """Subject alpha in [0, 1] from the ``rembg`` neural segmenter (downloads the model once)."""
    from rembg import new_session, remove  # imported lazily: optional dependency

    session = new_session(model)
    out = remove(Image.fromarray(rgb), session=session, only_mask=True)
    return np.asarray(out.convert("L"), dtype=np.float32) / 255.0


def mask_grabcut(rgb: np.ndarray, face: tuple[int, int, int, int] | None) -> np.ndarray:
    """Subject alpha from OpenCV GrabCut seeded with a head-and-shoulders rectangle."""
    h, w = rgb.shape[:2]
    k = 640 / max(h, w) if max(h, w) > 640 else 1.0
    small = cv2.cvtColor(cv2.resize(rgb, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2BGR)
    sh, sw = small.shape[:2]
    if face:
        fx, fy, fw, fh = (v * k for v in face)
        x0, x1 = max(2, fx - 0.9 * fw), min(sw - 2, fx + 1.9 * fw)
        y0 = max(2, fy - 0.8 * fh)
    else:
        x0, x1, y0 = 0.1 * sw, 0.9 * sw, 0.03 * sh
    rect = (int(x0), int(y0), int(x1 - x0), int(sh - 2 - y0))
    gc = np.zeros((sh, sw), np.uint8)
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    cv2.grabCut(small, gc, rect, bgd, fgd, 6, cv2.GC_INIT_WITH_RECT)
    fg = np.where((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD), 1.0, 0.0).astype(np.float32)
    fg = cv2.resize(fg, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.clip(fg, 0.0, 1.0)


def mask_vignette(shape: tuple[int, ...]) -> np.ndarray:
    """Fallback when no segmentation is available: keep everything, fade the edges softly."""
    h, w = shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h * 0.45) / (h * 0.62)) ** 2)
    return np.clip(1.6 - r, 0.0, 1.0)


def clean_mask(alpha: np.ndarray) -> np.ndarray:
    """Drop specks, fill interior holes and soften the edge a touch."""
    binary = (alpha > 0.5).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if n <= 1:
        return alpha
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = np.zeros_like(binary)
    for i, area in enumerate(areas, start=1):
        if area >= 0.05 * areas.max():
            keep[labels == i] = 1
    padded = np.pad(keep, 1)
    flood = padded.copy()
    cv2.floodFill(flood, np.zeros((flood.shape[0] + 2, flood.shape[1] + 2), np.uint8), (0, 0), 1)
    holes = (flood == 0)[1:-1, 1:-1]
    keep[holes] = 1
    grown = cv2.dilate(keep, np.ones((5, 5), np.uint8))
    out = np.where(grown > 0, alpha, 0.0).astype(np.float32)
    out[keep > 0] = np.maximum(out[keep > 0], 0.95)
    return cv2.GaussianBlur(out, (0, 0), 0.8)


def build_alpha(
    rgb: np.ndarray, face: tuple[int, int, int, int] | None, method: str, rembg_model: str
) -> tuple[np.ndarray, str]:
    """Return ``(alpha, method_used)``; falls back gracefully when a method is unavailable."""
    order = {"auto": ["rembg", "grabcut"], "rembg": ["rembg", "grabcut"], "grabcut": ["grabcut"], "none": []}[method]
    for name in order:
        try:
            alpha = mask_rembg(rgb, rembg_model) if name == "rembg" else mask_grabcut(rgb, face)
        except Exception as exc:  # noqa: BLE001 - any failure must degrade, never abort
            print(f"  [warn] {name} unavailable ({type(exc).__name__}: {str(exc)[:90]}); trying next option")
            continue
        coverage = float((alpha > 0.5).mean())
        if 0.12 <= coverage <= 0.90:
            return clean_mask(alpha), name
        print(f"  [warn] {name} mask covers {coverage:.0%} of the frame - rejected as implausible")
    return mask_vignette(rgb.shape), "vignette"


# ---------------------------------------------------------------------------- crop
def compute_crop_box(
    alpha: np.ndarray,
    face: tuple[int, int, int, int] | None,
    aspect: float,
    head_margin: float,
    top_pad: float,
) -> tuple[int, int, int, int]:
    """Head-and-shoulders crop ``(left, top, right, bottom)`` in source pixels (may exceed the photo)."""
    ys, xs = np.nonzero(alpha > 0.5)
    if ys.size == 0:
        raise PrepError("The subject mask is empty; try --bg grabcut or pass --crop explicitly.")
    top, bottom = int(ys.min()), int(ys.max())
    chin = face[1] + face[3] if face else top + int(0.55 * (bottom - top))
    head_h = max(chin - top, 32)
    band = alpha[top : top + int(0.8 * head_h)] > 0.5  # hair + ears, above the shoulders
    cols = np.nonzero(band.any(axis=0))[0]
    x0, x1 = int(cols.min()), int(cols.max())
    crop_w = max(x1 - x0, 32) * head_margin
    crop_h = crop_w / aspect
    left = round((x0 + x1) / 2 - crop_w / 2)
    t = round(top - top_pad * crop_h)
    return left, t, left + round(crop_w), t + round(crop_h)


def crop_pad(arr: np.ndarray, box: tuple[int, int, int, int], border: int) -> np.ndarray:
    """Crop ``arr`` to ``box``; areas outside the image are padded (edge-replicated or zero)."""
    left, top, right, bottom = box
    h, w = arr.shape[:2]
    pl, pt, pr, pb = max(0, -left), max(0, -top), max(0, right - w), max(0, bottom - h)
    if pl or pt or pr or pb:
        arr = cv2.copyMakeBorder(arr, pt, pb, pl, pr, border, value=0)
    return arr[top + pt : bottom + pt, left + pl : right + pl]


# ------------------------------------------------------------------- enhancement
def enhance_luminance(
    rgb: np.ndarray, alpha: np.ndarray, clip: float, tiles: int, mix: float, sharpen: float, sigma: float
) -> np.ndarray:
    """Grayscale + CLAHE + unsharp mask, with the background neutralised so it cannot bias the tiles."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    fg = alpha > 0.5
    fill = int(np.median(gray[fg])) if fg.any() else 128
    work = np.where(fg, gray, fill).astype(np.uint8)
    eq = cv2.createCLAHE(clipLimit=clip, tileGridSize=(tiles, tiles)).apply(work)
    out = cv2.addWeighted(eq, mix, work, 1.0 - mix, 0)
    if sharpen > 0:
        blur = cv2.GaussianBlur(out, (0, 0), sigma)
        out = cv2.addWeighted(out, 1.0 + sharpen, blur, -sharpen, 0)
    return out


# --------------------------------------------------------------------------- main
def parse_box(text: str) -> tuple[int, int, int, int]:
    try:
        left, top, right, bottom = (int(float(v)) for v in text.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--crop expects LEFT,TOP,RIGHT,BOTTOM in pixels") from exc
    if right - left < 64 or bottom - top < 64:
        raise argparse.ArgumentTypeError("--crop box is too small")
    return left, top, right, bottom


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", type=Path, default=None, help="source photo (default: first image in photo/)")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="prepared LA PNG (default: %(default)s)")
    p.add_argument("--preview", default=str(DEFAULT_PREVIEW), help="preview PNG on a dark canvas (pass '' to skip)")
    p.add_argument("--bg", choices=["auto", "rembg", "grabcut", "none"], default="auto", help="background removal")
    p.add_argument("--rembg-model", default="isnet-general-use", help="rembg model name (default: %(default)s)")
    p.add_argument("--aspect", type=float, default=0.86, help="crop width/height, matches the SVG art area")
    p.add_argument("--head-margin", type=float, default=1.32, help="crop width as a multiple of head width")
    p.add_argument("--top-pad", type=float, default=0.06, help="head-room above the hair, fraction of crop height")
    p.add_argument("--crop", type=parse_box, default=None, help="manual crop LEFT,TOP,RIGHT,BOTTOM (overrides auto)")
    p.add_argument("--clahe-clip", type=float, default=2.4, help="CLAHE clip limit")
    p.add_argument("--clahe-tiles", type=int, default=8, help="CLAHE tile grid size")
    p.add_argument("--clahe-mix", type=float, default=0.75, help="blend of CLAHE vs original luminance (0-1)")
    p.add_argument("--sharpen", type=float, default=0.7, help="unsharp-mask amount (0 disables)")
    p.add_argument("--sharpen-sigma", type=float, default=1.6, help="unsharp-mask radius")
    p.add_argument("--max-side", type=int, default=1600, help="downscale larger photos to this long side")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (0.4 <= args.aspect <= 1.5 and 0 <= args.clahe_mix <= 1 and args.clahe_tiles >= 2 and args.max_side >= 256):
        build_parser().error("check --aspect (0.4-1.5), --clahe-mix (0-1), --clahe-tiles (>=2), --max-side (>=256)")
    try:
        source = args.input or find_default_input()
        img = load_image(source, args.max_side)
        rgb = np.asarray(img)
        face = detect_face(rgb)
        print(f"Input     : {source} ({img.width}x{img.height})")
        print(f"Face box  : {face if face else 'not detected (using mask geometry only)'}")

        alpha, used = build_alpha(rgb, face, args.bg, args.rembg_model)
        print(f"Cut-out   : {used} (subject covers {float((alpha > 0.5).mean()):.0%} of the frame)")

        box = args.crop or compute_crop_box(alpha, face, args.aspect, args.head_margin, args.top_pad)
        print(f"Crop box  : left={box[0]} top={box[1]} right={box[2]} bottom={box[3]} (source pixels)")
        rgb_c = crop_pad(rgb, box, cv2.BORDER_REPLICATE)
        alpha_c = crop_pad(alpha, box, cv2.BORDER_CONSTANT)

        gray = enhance_luminance(
            rgb_c, alpha_c, args.clahe_clip, args.clahe_tiles, args.clahe_mix, args.sharpen, args.sharpen_sigma
        )
        alpha8 = np.clip(alpha_c * 255.0, 0, 255).astype(np.uint8)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        Image.merge("LA", (Image.fromarray(gray), Image.fromarray(alpha8))).save(args.output, optimize=True)
        print(f"Prepared  : {args.output} ({gray.shape[1]}x{gray.shape[0]}, luminance + alpha)")

        if args.preview:  # an empty string means "skip the preview"
            preview = Path(args.preview)
            canvas = Image.new("RGB", (gray.shape[1], gray.shape[0]), THEME.bg)
            canvas = Image.composite(Image.fromarray(gray).convert("RGB"), canvas, Image.fromarray(alpha8))
            preview.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(preview)
            print(f"Preview   : {preview}")
    except PrepError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
