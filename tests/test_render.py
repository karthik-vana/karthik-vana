"""Heatmap, info card and validator tests (standard library only, synthetic data)."""
from __future__ import annotations

import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import timedelta
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (puts scripts/ on sys.path)
from helpers import START, make_document, pattern

import make_info_card as mic
import render_heatmap_svg as rh
import validate_assets as va
from common import THEME, esc_text, parse_svg
from profile_config import CARD, CardConfig, CardRow


def cells_of(svg: str) -> dict[tuple[str, str], str]:
    root = ET.fromstring(svg.encode())
    return {
        (e.attrib["x"], e.attrib["y"]): e.attrib["fill"]
        for e in root.iter()
        if e.tag.endswith("rect") and e.attrib.get("width") == "14" and e.attrib.get("height") == "14"
    }


class HeatmapTests(unittest.TestCase):
    def setUp(self):
        self.doc = make_document(pattern(367))

    def test_grid_has_seven_rows_53_weeks_and_matches_data(self):
        svg = rh.render_from_document(self.doc, animated=True)
        cells = cells_of(svg)
        self.assertEqual(len(cells), 367)
        self.assertEqual(len({y for _, y in cells}), 7)
        self.assertEqual(len({x for x, _ in cells}), 53)
        weeks, _ = rh.build_weeks(self.doc["days"])
        for col, week in enumerate(weeks):
            for row, cell in enumerate(week):
                if cell:
                    key = (f"{rh.GRID_X0 + col * rh.PITCH:g}", f"{rh.GRID_Y0 + row * rh.PITCH:g}")
                    self.assertEqual(cells[key], THEME.heat[cell["level"]])

    def test_weekday_rows_use_real_dates(self):
        weeks, dates = rh.build_weeks(self.doc["days"])
        for col, week in enumerate(dates):
            for row, d in enumerate(week):
                if d:
                    self.assertEqual(d.isoweekday() % 7, row)  # Sunday = row 0
        self.assertEqual(dates[0][0], START)  # START is a Sunday

    def test_fills_come_only_from_the_scale(self):
        self.assertTrue(set(cells_of(rh.render_from_document(self.doc, animated=False)).values()) <= set(THEME.heat))

    def test_animated_and_static_variants(self):
        animated = rh.render_from_document(self.doc, animated=True)
        static = rh.render_from_document(self.doc, animated=False)
        self.assertIn("@keyframes pop", animated)
        self.assertIn("prefers-reduced-motion", animated)
        self.assertNotIn("<style", static)
        self.assertNotIn('class="c', static)
        self.assertEqual(cells_of(animated), cells_of(static))  # same picture either way

    def test_no_active_content(self):
        svg = rh.render_from_document(self.doc, animated=True)
        for needle in ("<script", "foreignObject", "onload", "href="):
            self.assertNotIn(needle, svg)

    def test_zero_activity_still_draws_the_full_grid_with_honest_footer(self):
        svg = rh.render_from_document(make_document([0] * 367), animated=True)
        self.assertEqual(len(cells_of(svg)), 367)
        self.assertEqual(set(cells_of(svg).values()), {THEME.heat[0]})
        self.assertIn("No public contributions in this period yet", svg)
        self.assertNotIn("streak", svg)

    def test_unknown_counts_do_not_invent_a_total(self):
        doc = make_document(pattern(367))
        for d in doc["days"]:
            d["count"] = None
        doc["reported_total"] = None
        svg = rh.render_from_document(doc, animated=True)
        self.assertNotIn("contributions in the last year", svg)
        self.assertNotIn("best day", svg)

    def test_footer_fits_the_canvas(self):
        text = rh.stats_text(rh.compute_stats(self.doc))
        self.assertLessEqual(len(text), rh.STATS_MAX_CHARS)
        self.assertIn(f"{sum(d['count'] for d in self.doc['days']):,} contributions in the last year", text)

    def test_month_labels(self):
        _, dates = rh.build_weeks(self.doc["days"])
        labels = rh.month_labels(dates)
        self.assertEqual(labels[0], (0, "Oct"))
        self.assertEqual(labels[1], (4, "Nov"))
        self.assertEqual([name for _, name in labels][:3], ["Oct", "Nov", "Dec"])

    def test_output_is_deterministic(self):
        self.assertEqual(rh.render_from_document(self.doc, animated=True), rh.render_from_document(self.doc, animated=True))

    def test_user_text_is_escaped(self):
        doc = make_document(pattern(367), user="a&b")
        parse_svg(rh.render_from_document(doc, animated=True))  # would raise if '&' leaked unescaped

    def test_placeholder_contains_no_numbers_or_dates(self):
        svg = rh.render_placeholder("karthik-vana", animated=False)
        parse_svg(svg)
        self.assertIn("Waiting for the first data refresh", svg)
        self.assertNotIn("contributions in", svg)

    def test_failure_leaves_the_existing_svg_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, bad = Path(tmp) / "h.svg", Path(tmp) / "bad.json"
            out.write_text("<svg/>", encoding="utf-8")
            bad.write_text("{not json", encoding="utf-8")
            for source in (Path(tmp) / "missing.json", bad):
                self.assertEqual(rh.main(["--input", str(source), "--out", str(out)]), 2)
                self.assertEqual(out.read_text(encoding="utf-8"), "<svg/>")

    def test_schema_violation_is_rejected(self):
        doc = make_document(pattern(367))
        doc["days"][5]["level"] = 9
        with self.assertRaises(Exception):
            rh.render_from_document(doc, animated=True)


class InfoCardTests(unittest.TestCase):
    def test_builds_both_variants_and_every_text_fits(self):
        with tempfile.TemporaryDirectory() as tmp:
            for animated in (True, False):
                path = Path(tmp) / f"card-{animated}.svg"
                path.write_text(mic.build_svg(CARD, animated=animated), encoding="utf-8")
                self.assertEqual(va.check_svg_file(path), [])

    def test_static_variant_has_no_animation(self):
        static = mic.build_svg(CARD, animated=False)
        self.assertNotIn("<style", static)
        self.assertNotIn('class="ln"', static)
        self.assertIn("@keyframes ln", mic.build_svg(CARD, animated=True))

    def test_card_only_states_cv_backed_facts(self):
        svg = mic.build_svg(CARD, animated=False)
        for expected in ("Karthik Vana", "AI/ML Engineer", "SRIP-2026", "ShopMind AI", "FoodVision AI"):
            self.assertIn(expected, svg)
        self.assertNotIn("currently", svg.lower())  # ended roles must not be presented as current

    def test_too_long_value_is_an_error_not_clipping(self):
        card = CardConfig(CARD.title, CARD.handle, CARD.command, (CardRow("Name", "x" * 80),), CARD.statement)
        with self.assertRaises(mic.CardError):
            mic.build_svg(card, animated=False)

    def test_too_many_rows_is_an_error(self):
        card = CardConfig(CARD.title, CARD.handle, CARD.command, tuple(CardRow(f"R{i}", "v") for i in range(30)), CARD.statement)
        with self.assertRaises(mic.CardError):
            mic.build_svg(card, animated=False)

    def test_special_characters_are_escaped(self):
        card = CardConfig(CARD.title, CARD.handle, CARD.command, (CardRow("Name", "A&B <b>\"q\" 'x'"),), CARD.statement)
        svg = mic.build_svg(card, animated=True)
        parse_svg(svg)
        self.assertNotIn("<b>", svg)

    def test_static_env_flag_writes_only_the_static_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            animated, static = Path(tmp) / "a.svg", Path(tmp) / "s.svg"
            with mock.patch.dict(os.environ, {"STATIC": "1"}):
                self.assertEqual(mic.main(["--out", str(animated), "--out-static", str(static)]), 0)
            self.assertFalse(animated.exists())
            self.assertTrue(static.exists())

    def test_default_run_writes_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            animated, static = Path(tmp) / "a.svg", Path(tmp) / "s.svg"
            with mock.patch.dict(os.environ, {"STATIC": ""}):
                self.assertEqual(mic.main(["--out", str(animated), "--out-static", str(static)]), 0)
            self.assertTrue(animated.exists() and static.exists())

    def test_escape_helper_drops_illegal_control_characters(self):
        self.assertEqual(esc_text("a\x00b\x0bc&"), "abc&amp;")


class ValidatorTests(unittest.TestCase):
    def check(self, body: str, width: int = 100) -> list[str]:
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="50" viewBox="0 0 {width} 50">{body}</svg>'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.svg"
            path.write_text(svg, encoding="utf-8")
            return va.check_svg_file(path)

    def test_clean_svg_passes(self):
        self.assertEqual(self.check('<text x="2" y="20" font-size="10">ok</text>'), [])

    def test_script_event_handler_and_external_reference_are_flagged(self):
        problems = " ".join(self.check('<script>alert(1)</script><rect onclick="x()" width="1" height="1"/><image href="https://evil.example/x.png"/>'))
        for needle in ("script", "onclick", "external reference"):
            self.assertIn(needle, problems)

    def test_overflowing_text_is_flagged(self):
        self.assertTrue(any("outside" in p for p in self.check('<text x="2" y="20" font-size="10">' + "w" * 60 + "</text>")))

    def test_missing_viewbox_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.svg"
            path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>', encoding="utf-8")
            self.assertTrue(va.check_svg_file(path))

    def test_malformed_xml_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.svg"
            path.write_text("<svg><g></svg>", encoding="utf-8")
            self.assertTrue(any("well-formed" in p for p in va.check_svg_file(path)))


if __name__ == "__main__":
    unittest.main()
