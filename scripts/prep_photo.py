#!/usr/bin/env python3
"""Prepare a portrait for ASCII conversion; optional OpenCV CLAHE is used when installed."""
from __future__ import annotations
import argparse
from pathlib import Path
from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError


def prepare_image(source: Path, destination: Path, *, contrast: float = 1.28, sharpen: float = 1.05, use_clahe: bool = True) -> Path:
    """Convert an input photo to a clean, contrast-enhanced grayscale PNG."""
    if not source.is_file():
        raise FileNotFoundError(f"Portrait not found: {source}")
    try:
        image = Image.open(source).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"Cannot open portrait image {source}: {exc}") from exc
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    # CLAHE can preserve local facial contrast, but Pillow-only fallback always works.
    if use_clahe:
        try:
            import cv2  # type: ignore[import-not-found]
            import numpy as np  # type: ignore[import-not-found]
            arr = np.asarray(gray)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            gray = Image.fromarray(clahe.apply(arr))
        except ImportError:
            pass
    gray = ImageEnhance.Contrast(gray).enhance(contrast)
    gray = ImageEnhance.Sharpness(gray).enhance(sharpen)
    destination.parent.mkdir(parents=True, exist_ok=True)
    gray.save(destination, format="PNG", optimize=True)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Path to the original portrait")
    parser.add_argument("--output", type=Path, default=Path(".cache/karthik-prepared.png"))
    parser.add_argument("--contrast", type=float, default=1.28)
    parser.add_argument("--sharpen", type=float, default=1.05)
    parser.add_argument("--no-clahe", action="store_true", help="Skip optional OpenCV CLAHE")
    args = parser.parse_args()
    if args.contrast <= 0 or args.sharpen <= 0:
        parser.error("--contrast and --sharpen must be positive numbers")
    try:
        result = prepare_image(args.input, args.output, contrast=args.contrast, sharpen=args.sharpen, use_clahe=not args.no_clahe)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"Prepared grayscale portrait: {result}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
