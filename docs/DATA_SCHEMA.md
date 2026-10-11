# `data/contributions.json`

Written by `scripts/fetch_contributions.py`, read by `scripts/render_heatmap_svg.py`. Machine-readable schema: [`data/contributions.schema.json`](../data/contributions.schema.json). The rules actually enforced (before writing and before drawing) live in `scripts/contrib_data.py`.

| Field | Meaning |
| --- | --- |
| `schema_version` | Always `1`. A different value is rejected. |
| `user` | GitHub login the calendar belongs to. |
| `source` | `type` is `github-html` (public page, no token) or `github-graphql` (fallback) plus the `url`. |
| `updated_at` | UTC time the **day data last changed**. Repeated runs with identical data do not touch the file, so it never creates an empty commit. |
| `as_of` | UTC date of the fetch. Lets the renderer decide whether an empty final day is "still in progress" without reading the clock, which keeps the SVG deterministic. |
| `range` | First and last date; must equal the first and last entries of `days`. |
| `reported_total` | The "N contributions in the last year" figure GitHub prints on the page, or `null`. Used only as a cross-check. |
| `days[]` | `{date, count, level}` per day, ascending and contiguous. 300-372 entries (53 weeks = 371 days; leap years are handled by real dates). |
| `days[].count` | Exact contributions that day, or `null` when the source gave none (never guessed as 0). |
| `days[].level` | GitHub's own intensity 0-4. If a source omits it, it is derived from counts as quartiles of the busiest day. |

## Rules applied to every document

* Dates are real calendar dates, strictly consecutive, no duplicates.
* `level` is an integer 0-4; `count` is a non-negative integer or `null`.
* The calendar must end within two days of the fetch date (time-zone slack), otherwise it is rejected as stale.
* A mismatch between the sum of `count` and `reported_total` produces a warning, and the heatmap footer then shows no total.

## Statistics shown in the heatmap footer

Computed only from `days`, and only when supported:

* **Total**: shown when every day has a count and it agrees with `reported_total` (or none is reported).
* **Active days**: days with `count > 0` (or `level > 0` when counts are unknown).
* **Longest streak**: longest run of consecutive active days; shown from 2 days.
* **Current streak**: the run ending on the last day. If the last day is the fetch date and still empty, the streak that ended yesterday is not broken yet.
* **Best day**: highest count (first occurrence on a tie); needs all counts.
* With zero activity the footer says so and shows no streaks; the grid is still drawn in full.

## Example structure

> **SAMPLE ONLY: illustrative numbers.** The committed `data/contributions.json` contains the real calendar fetched from GitHub. A real file has 300-372 day entries; three are shown.

```json
{
  "schema_version": 1,
  "user": "octocat",
  "source": { "type": "github-html", "url": "https://github.com/users/octocat/contributions" },
  "updated_at": "2026-01-15T01:17:09Z",
  "as_of": "2026-01-15",
  "range": { "from": "2025-01-12", "to": "2026-01-15" },
  "reported_total": 412,
  "days": [
    {"date": "2025-01-12", "count": 0, "level": 0},
    {"date": "2025-01-13", "count": 3, "level": 2},
    {"date": "2025-01-14", "count": 11, "level": 4}
  ]
}
```

## Exit codes

`fetch_contributions.py`: `0` written or unchanged, `2` fetch or validation failed (previous file untouched). `render_heatmap_svg.py`: `0` written, `2` failed (previous SVG untouched). `validate_assets.py`: `0` all checks pass, `1` at least one failed.
