"""Fetcher, parser and statistics tests. Uses only synthetic data and mocked network calls."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (puts scripts/ on sys.path)
from helpers import START, make_document, pattern, synthetic_html

import fetch_contributions as fc
from contrib_data import DataError, compute_stats, content_key, level_from_counts, validate_document

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status: int, text: str = "", headers: dict | None = None) -> None:
        self.status_code, self.text, self.headers = status, text, headers or {}


class FakeSession:
    def __init__(self, *responses) -> None:
        self.responses, self.calls = list(responses), 0

    def get(self, url, **kwargs):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ParserTests(unittest.TestCase):
    def test_parses_days_counts_levels_and_heading(self):
        counts = pattern(367)
        days, total = fc.parse_calendar_html(synthetic_html(counts))
        self.assertEqual(len(days), 367)
        self.assertEqual([d["count"] for d in days], counts)
        self.assertEqual(total, sum(counts))
        self.assertTrue(all(0 <= d["level"] <= 4 for d in days))
        self.assertEqual(days[0]["date"], START.isoformat())

    def test_singular_plural_and_none_wording(self):
        counts = [0, 1, 2] + [0] * 5
        days, _ = fc.parse_calendar_html(synthetic_html(counts))
        self.assertEqual([d["count"] for d in days[:3]], [0, 1, 2])

    def test_thousands_separator_in_counts(self):
        html = synthetic_html([5, 0, 0]).replace("5 contributions", "1,234 contributions").replace("5 contribution ", "1,234 contributions ")
        days, _ = fc.parse_calendar_html(html)
        self.assertEqual(days[0]["count"], 1234)

    def test_missing_levels_fall_back_to_counts(self):
        days, _ = fc.parse_calendar_html(synthetic_html([0, 1, 4, 8], levels=False))
        self.assertEqual([d["level"] for d in days], [0, 1, 2, 4])

    def test_missing_tooltips_keep_counts_unknown_never_zero(self):
        days, _ = fc.parse_calendar_html(synthetic_html([0, 3, 9], tooltips=False))
        self.assertTrue(all(d["count"] is None for d in days))

    def test_missing_heading_gives_none(self):
        _, total = fc.parse_calendar_html(synthetic_html([1, 2, 3], heading=False))
        self.assertIsNone(total)

    def test_page_without_cells_is_a_typed_error(self):
        with self.assertRaises(fc.FetchError):
            fc.parse_calendar_html("<html><body>Sign in to GitHub</body></html>")

    def test_conflicting_duplicates_raise(self):
        html = synthetic_html([1, 2, 3])
        extra = '<td data-date="2025-10-05" data-level="4" id="x"></td>'
        with self.assertRaises(fc.FetchError):
            fc.parse_calendar_html(html + extra)


class DocumentTests(unittest.TestCase):
    def test_build_document_validates_and_stamps(self):
        days, total = fc.parse_calendar_html(synthetic_html(pattern(367)))
        doc, warnings = fc.build_document("karthik-vana", days, "github-html", "u", total, NOW)
        self.assertEqual(warnings, [])
        self.assertEqual(doc["as_of"], "2026-10-06")
        self.assertEqual(doc["range"]["to"], "2026-10-06")

    def test_gap_in_days_is_rejected(self):
        days, total = fc.parse_calendar_html(synthetic_html(pattern(367)))
        del days[100]
        with self.assertRaises(DataError):
            fc.build_document("karthik-vana", days, "github-html", "u", total, NOW)

    def test_too_short_calendar_is_rejected(self):
        days, total = fc.parse_calendar_html(synthetic_html(pattern(30)))
        with self.assertRaises(DataError):
            fc.build_document("karthik-vana", days, "github-html", "u", total, NOW)

    def test_stale_calendar_is_rejected(self):
        days, total = fc.parse_calendar_html(synthetic_html(pattern(367), start=START - timedelta(days=12)))
        with self.assertRaises(DataError):
            fc.build_document("karthik-vana", days, "github-html", "u", total, NOW)

    def test_total_mismatch_is_a_warning_not_a_silent_pass(self):
        days, _ = fc.parse_calendar_html(synthetic_html(pattern(367)))
        _, warnings = fc.build_document("karthik-vana", days, "github-html", "u", 99999, NOW)
        self.assertTrue(any("differs" in w for w in warnings))

    def test_leap_year_window_validates(self):
        start = date(2027, 10, 3)  # Sunday; window spans 29 Feb 2028
        doc = make_document(pattern(368), start=start)
        validate_document(doc)
        self.assertIn("2028-02-29", [d["date"] for d in doc["days"]])

    def test_dumps_round_trips(self):
        doc = make_document(pattern(367))
        self.assertEqual(json.loads(fc.dumps(doc)), doc)

    def test_write_if_changed_is_idempotent(self):
        doc = make_document(pattern(367))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "c.json"
            self.assertTrue(fc.write_if_changed(doc, out))
            before = out.read_text(encoding="utf-8")
            later = {**doc, "updated_at": "2030-01-01T00:00:00Z"}  # only the timestamp differs
            self.assertFalse(fc.write_if_changed(later, out))
            self.assertEqual(out.read_text(encoding="utf-8"), before)
            changed = make_document(pattern(367, seed=99))
            self.assertTrue(fc.write_if_changed(changed, out))
            self.assertNotEqual(content_key(json.loads(out.read_text(encoding="utf-8"))), content_key(doc))


class NetworkTests(unittest.TestCase):
    def test_retries_server_errors_then_succeeds(self):
        page = synthetic_html(pattern(367))
        session = FakeSession(FakeResponse(503), FakeResponse(429, headers={"Retry-After": "2"}), FakeResponse(200, page))
        sleeps: list[float] = []
        body = fc.download_html("karthik-vana", 5, 4, session=session, sleep=sleeps.append)
        self.assertIn("data-date", body)
        self.assertEqual(session.calls, 3)
        self.assertEqual(sleeps[1], 2.0)  # honoured Retry-After

    def test_connection_errors_are_retried_and_then_reported(self):
        import requests

        session = FakeSession(*[requests.ConnectionError("boom") for _ in range(3)])
        with self.assertRaises(fc.FetchError) as ctx:
            fc.download_html("karthik-vana", 5, 3, session=session, sleep=lambda s: None)
        self.assertIn("giving up after 3 attempts", str(ctx.exception))
        self.assertEqual(session.calls, 3)

    def test_404_is_not_retried(self):
        session = FakeSession(FakeResponse(404))
        with self.assertRaises(fc.FetchError):
            fc.download_html("nobody", 5, 4, session=session, sleep=lambda s: None)
        self.assertEqual(session.calls, 1)

    def test_200_without_calendar_markup_is_rejected(self):
        with self.assertRaises(fc.FetchError):
            fc.download_html("karthik-vana", 5, 1, session=FakeSession(FakeResponse(200, "<html>rate limited</html>")), sleep=lambda s: None)

    def test_graphql_payload_parsing(self):
        payload = {
            "data": {"user": {"contributionsCollection": {"contributionCalendar": {
                "totalContributions": 7,
                "weeks": [{"contributionDays": [
                    {"date": "2026-10-05", "contributionCount": 0, "contributionLevel": "NONE"},
                    {"date": "2026-10-04", "contributionCount": 7, "contributionLevel": "FOURTH_QUARTILE"},
                ]}],
            }}}}
        }
        days, total = fc.parse_graphql_payload(payload)
        self.assertEqual(total, 7)
        self.assertEqual([d["date"] for d in days], ["2026-10-04", "2026-10-05"])  # sorted
        self.assertEqual(days[0]["level"], 4)

    def test_graphql_errors_and_bad_shapes_are_typed(self):
        for bad in ({"errors": [{"message": "x"}]}, {"data": {"user": None}}, [], {"data": {}}):
            with self.assertRaises(fc.FetchError):
                fc.parse_graphql_payload(bad)

    def test_graphql_fallback_needs_a_token(self):
        with mock.patch.object(fc, "download_html", side_effect=fc.FetchError("page changed")):
            with self.assertRaises(fc.FetchError) as ctx:
                fc.fetch_document("karthik-vana", "auto", 5, 1, None, NOW)
        self.assertIn("graphql: skipped", str(ctx.exception))


class FailurePreservesDataTests(unittest.TestCase):
    def test_failed_fetch_leaves_previous_file_untouched(self):
        doc = make_document(pattern(367))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "contributions.json"
            out.write_text(fc.dumps(doc), encoding="utf-8")
            before = out.read_bytes()
            with mock.patch.object(fc, "download_html", side_effect=fc.FetchError("offline")):
                code = fc.main(["--out", str(out), "--source", "html", "--retries", "1"])
            self.assertEqual(code, 2)
            self.assertEqual(out.read_bytes(), before)

    def test_invalid_user_is_rejected_before_any_request(self):
        with self.assertRaises(SystemExit):
            fc.main(["--user", "bad user!", "--dry-run"])

    def test_from_file_round_trip(self):
        now = datetime.now(timezone.utc)
        html = synthetic_html(pattern(367), start=(now - timedelta(days=366)).date())
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp) / "page.html", Path(tmp) / "out.json"
            src.write_text(html, encoding="utf-8")
            self.assertEqual(fc.main(["--from-file", str(src), "--out", str(out)]), 0)
            validate_document(json.loads(out.read_text(encoding="utf-8")))


class StatsTests(unittest.TestCase):
    def doc(self, counts, as_of=None):
        return make_document(counts, as_of=as_of)

    def test_levels_from_counts(self):
        self.assertEqual([level_from_counts(c, 8) for c in (0, 1, 2, 3, 4, 6, 8)], [0, 1, 1, 2, 2, 3, 4])

    def test_streaks_total_and_best_day(self):
        counts = [0] * 340 + [1, 2, 0, 3, 4, 5, 0] + [0] * 20  # longest run 3, current 0
        s = compute_stats(self.doc(counts))
        self.assertEqual((s.total, s.active_days, s.longest_streak, s.current_streak), (15, 5, 3, 0))
        self.assertEqual((s.best_count, s.best_date), (5, START + timedelta(days=345)))

    def test_running_day_without_activity_does_not_break_the_current_streak(self):
        counts = [0] * 360 + [2, 3, 4, 5, 6, 0, 0]
        yesterday_counts = self.doc(counts, as_of=START + timedelta(days=len(counts) - 1))
        self.assertEqual(compute_stats(yesterday_counts).current_streak, 0)  # two empty days: genuinely broken
        counts = [0] * 360 + [2, 3, 4, 5, 6, 7, 0]
        d = self.doc(counts, as_of=START + timedelta(days=len(counts) - 1))
        self.assertEqual(compute_stats(d).current_streak, 6)  # today (last day) is still in progress
        d["as_of"] = (START + timedelta(days=len(counts) + 5)).isoformat()  # the empty day is long over
        self.assertEqual(compute_stats(d).current_streak, 0)

    def test_zero_activity_is_valid_and_has_no_best_day(self):
        s = compute_stats(self.doc([0] * 367))
        self.assertEqual((s.total, s.active_days, s.longest_streak, s.best_count), (0, 0, 0, None))

    def test_unknown_counts_withhold_total_and_best_day(self):
        doc = self.doc(pattern(367))
        for d in doc["days"]:
            d["count"] = None
        doc["reported_total"] = None
        s = compute_stats(doc)
        self.assertIsNone(s.total)
        self.assertIsNone(s.best_count)

    def test_disagreeing_sources_withhold_total(self):
        doc = self.doc(pattern(367))
        doc["reported_total"] += 5
        self.assertIsNone(compute_stats(doc).total)


if __name__ == "__main__":
    unittest.main()
