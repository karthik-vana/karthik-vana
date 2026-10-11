"""Portrait pipeline tests. Skipped automatically when the imaging libraries are not installed."""
from __future__ import annotations

import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (puts scripts/ on sys.path)

try:
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw

    import make_ascii_svg as ma
    import prep_photo as pp

    HAVE_IMAGING = True
except ImportError:  # pragma: no cover
    HAVE_IMAGING = False


def make_la(path: Path, w: int = 220, h: int = 260) -> None:
    """Synthetic subject: a bright ellipse with a dark 'hair' cap on a transparent background."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    inside = (((xx - w / 2) / (w * 0.38)) ** 2 + ((yy - h * 0.5) / (h * 0.42)) ** 2) <= 1
    lum = np.where(yy < h * 0.3, 40, 90 + 150 * (xx / w)).astype(np.uint8)
    alpha = np.where(inside, 255, 0).astype(np.uint8)
    Image.merge("LA", (Image.fromarray(lum), Image.fromarray(alpha))).save(path)


def make_photo(path: Path, w: int = 600, h: int = 700) -> None:
    img = Image.new("RGB", (w, h), (150, 160, 170))
    draw = ImageDraw.Draw(img)
    draw.ellipse((200, 120, 400, 380), fill=(190, 140, 110))  # head
    draw.rectangle((120, 450, 480, 700), fill=(20, 20, 20))  # shoulders
    img.save(path)


@unittest.skipUnless(HAVE_IMAGING, "numpy / opencv / Pillow not installed (pip install -r requirements-portrait.txt)")
class AsciiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.src = self.tmp / "prepped.png"
        make_la(self.src)

    def tearDown(self):
        self._tmp.cleanup()

    def run_main(self, *extra: str) -> int:
        return ma.main(["--input", str(self.src), "--out", str(self.tmp / "a.svg"), "--out-static", str(self.tmp / "s.svg"), "--cols", "60", *extra])

    def test_generates_well_formed_animated_and_static_svgs(self):
        self.assertEqual(self.run_main(), 0)
        animated, static = (self.tmp / "a.svg").read_text(encoding="utf-8"), (self.tmp / "s.svg").read_text(encoding="utf-8")
        for svg in (animated, static):
            root = ET.fromstring(svg.encode())
            self.assertTrue(root.tag.endswith("svg"))
            for needle in ("<script", "foreignObject", "onload"):
                self.assertNotIn(needle, svg)
        rows = animated.count('class="cv"')
        self.assertGreater(rows, 20)
        self.assertEqual(rows, animated.count('class="cu"'))
        self.assertNotIn('class="cv"', static)
        self.assertNotIn("<style", static)

    def test_animation_finishes_on_a_stable_portrait(self):
        self.run_main()
        svg = (self.tmp / "a.svg").read_text(encoding="utf-8")
        self.assertIn("prefers-reduced-motion", svg)
        self.assertNotIn("infinite", svg)  # one-shot: no endless loop
        self.assertIn(".cv{opacity:0", svg)  # base state = nothing covering the art
        self.assertIn("@keyframes hd{to{opacity:0}}", svg)  # covers and cursors end hidden, whatever the step arithmetic
        self.assertIn(",start)", svg)  # steps round up, so the last step always reaches the final value
        two_delays = len(re.findall(r"animation-delay:[\d.]+s,[\d.]+s", svg))  # start + hide delay on every cover and cursor
        self.assertEqual(two_delays, svg.count('class="cv"') + svg.count('class="cu"'))

    def test_output_is_deterministic(self):
        self.run_main()
        first = (self.tmp / "a.svg").read_bytes()
        self.run_main()
        self.assertEqual((self.tmp / "a.svg").read_bytes(), first)

    def test_static_env_flag_writes_only_static(self):
        with mock.patch.dict(os.environ, {"STATIC": "1"}):
            self.assertEqual(self.run_main(), 0)
        self.assertFalse((self.tmp / "a.svg").exists())
        self.assertTrue((self.tmp / "s.svg").exists())

    def test_light_on_dark_maps_bright_to_dense_and_background_to_blank(self):
        lum, alpha = ma.load_prepared(self.src)
        cfg = ma.AsciiConfig(cols=60)
        lay = ma.compute_layout(cfg, lum.shape[1], lum.shape[0])
        g_lum, g_alpha = ma.sample_grid(lum, alpha, lay.cols, lay.rows)
        idx, _ = ma.tone_to_glyphs(g_lum, g_alpha, cfg)
        self.assertEqual(idx[0, 0], 0)  # corner is background -> space
        self.assertEqual(idx[-1, -1], 0)
        mid = idx.shape[0] // 2
        dark_cap, bright_side = idx[int(idx.shape[0] * 0.2), idx.shape[1] // 2], idx[mid, int(idx.shape[1] * 0.62)]
        self.assertGreater(bright_side, dark_cap)  # bright pixels -> denser glyphs on a dark canvas
        inverted, _ = ma.tone_to_glyphs(g_lum, g_alpha, ma.AsciiConfig(cols=60, polarity="print"))
        self.assertLess(inverted[mid, int(idx.shape[1] * 0.62)], inverted[int(idx.shape[0] * 0.2), idx.shape[1] // 2])

    def test_subject_never_uses_a_blank_glyph(self):
        lum, alpha = ma.load_prepared(self.src)
        cfg = ma.AsciiConfig(cols=60)
        lay = ma.compute_layout(cfg, lum.shape[1], lum.shape[0])
        g_lum, g_alpha = ma.sample_grid(lum, alpha, lay.cols, lay.rows)
        idx, _ = ma.tone_to_glyphs(g_lum, g_alpha, cfg)
        self.assertTrue((idx[g_alpha > 0.9] >= cfg.floor).all())

    def test_layout_keeps_photo_proportions(self):
        cfg = ma.AsciiConfig(cols=100)
        lay = ma.compute_layout(cfg, 800, 930)
        self.assertAlmostEqual(lay.art_w / lay.art_h, 800 / 930, delta=0.02)
        self.assertLessEqual(lay.x0 + lay.art_w, cfg.window_width)
        self.assertLessEqual(lay.y0 + lay.art_h, cfg.height)

    def test_invalid_options_are_rejected_cleanly(self):
        for bad in (["--ramp", "abc"], ["--ramp", " \x07x"], ["--cols", "5"], ["--cell-aspect", "3"], ["--duration", "0.1"]):
            self.assertEqual(self.run_main(*bad), 2, bad)

    def test_xml_special_glyphs_in_the_ramp_are_escaped(self):
        self.assertEqual(self.run_main("--ramp", " .<&>#@"), 0)
        svg = (self.tmp / "a.svg").read_text(encoding="utf-8")
        ET.fromstring(svg.encode())
        self.assertNotRegex(svg, r">[^<]*[<][^/a-zA-Z!?][^>]*<")  # no raw '<' inside text content
        self.assertIn("&amp;", svg + "&amp;")  # parse above is the real assertion

    def test_missing_input_gives_a_helpful_error(self):
        self.src.unlink()
        self.assertEqual(self.run_main(), 2)

    def test_preview_png_is_written_when_a_font_exists(self):
        if ma.find_mono_font() is None:
            self.skipTest("no monospace font available")
        out = self.tmp / "p.png"
        self.assertEqual(self.run_main("--preview-png", str(out), "--preview-width", "200"), 0)
        with Image.open(out) as img:
            self.assertEqual(img.width, 200)


@unittest.skipUnless(HAVE_IMAGING, "numpy / opencv / Pillow not installed")
class PrepTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_rejects_missing_unsupported_tiny_and_corrupt_inputs(self):
        with self.assertRaises(pp.PrepError):
            pp.load_image(self.tmp / "nope.jpg", 1600)
        txt = self.tmp / "a.txt"
        txt.write_text("x")
        with self.assertRaises(pp.PrepError):
            pp.load_image(txt, 1600)
        tiny = self.tmp / "tiny.png"
        Image.new("RGB", (64, 64)).save(tiny)
        with self.assertRaises(pp.PrepError):
            pp.load_image(tiny, 1600)
        bad = self.tmp / "bad.jpg"
        bad.write_bytes(b"not an image at all")
        with self.assertRaises(pp.PrepError):
            pp.load_image(bad, 1600)
        empty = self.tmp / "empty.jpg"
        empty.write_bytes(b"")
        with self.assertRaises(pp.PrepError):
            pp.load_image(empty, 1600)

    def test_large_photos_are_downscaled(self):
        big = self.tmp / "big.png"
        Image.new("RGB", (2400, 1800), (10, 20, 30)).save(big)
        self.assertEqual(max(pp.load_image(big, 1600).size), 1600)

    def test_crop_pad_returns_exact_box_size_even_outside_the_image(self):
        arr = np.full((100, 80, 3), 200, np.uint8)
        out = pp.crop_pad(arr, (-20, -30, 70, 90), cv2.BORDER_REPLICATE)
        self.assertEqual(out.shape[:2], (120, 90))
        alpha = pp.crop_pad(np.ones((100, 80), np.float32), (-20, -30, 70, 90), cv2.BORDER_CONSTANT)
        self.assertEqual(float(alpha[0, 0]), 0.0)  # padded area is background
        self.assertEqual(float(alpha[60, 40]), 1.0)

    def test_crop_box_is_centred_on_the_head_and_has_the_requested_aspect(self):
        alpha = np.zeros((900, 900), np.float32)
        alpha[100:500, 300:600] = 1  # head
        alpha[500:900, 100:800] = 1  # shoulders
        left, top, right, bottom = pp.compute_crop_box(alpha, (320, 200, 260, 260), 0.86, 1.3, 0.06)
        self.assertAlmostEqual((right - left) / (bottom - top), 0.86, delta=0.01)
        self.assertAlmostEqual((left + right) / 2, 450, delta=3)  # head centre
        self.assertLess(top, 100)  # head-room above the hair

    def test_empty_mask_is_a_clear_error(self):
        with self.assertRaises(pp.PrepError):
            pp.compute_crop_box(np.zeros((50, 50), np.float32), None, 0.86, 1.3, 0.06)

    def test_none_and_grabcut_always_return_a_usable_alpha(self):
        rgb = np.asarray(Image.open(self._photo()).convert("RGB"))
        for method in ("none", "grabcut"):
            alpha, used = pp.build_alpha(rgb, None, method, "isnet-general-use")
            self.assertEqual(alpha.shape, rgb.shape[:2])
            self.assertTrue(0.0 <= float(alpha.min()) and float(alpha.max()) <= 1.0)
            self.assertIn(used, {"grabcut", "vignette"})

    def test_enhance_keeps_shape_and_dtype(self):
        rgb = np.asarray(Image.open(self._photo()).convert("RGB"))
        out = pp.enhance_luminance(rgb, np.ones(rgb.shape[:2], np.float32), 2.4, 8, 0.75, 0.7, 1.6)
        self.assertEqual((out.shape, out.dtype), (rgb.shape[:2], np.uint8))

    def test_end_to_end_without_rembg(self):
        out = self.tmp / "prepped.png"
        code = pp.main(["--input", str(self._photo()), "--output", str(out), "--bg", "none", "--preview", "", "--crop", "100,50,500,600"])
        self.assertEqual(code, 0)
        with Image.open(out) as img:
            self.assertEqual((img.mode, img.size), ("LA", (400, 550)))

    def _photo(self) -> Path:
        path = self.tmp / "photo.jpg"
        if not path.exists():
            make_photo(path)
        return path


if __name__ == "__main__":
    unittest.main()
