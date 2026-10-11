# Troubleshooting

## Setup (Windows PowerShell)

**`Activate.ps1 cannot be loaded because running scripts is disabled`**
Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in the same window, then activate again. It only affects that window.

**`python` is not recognised / opens the Microsoft Store**
Use the launcher (`py -3 ...`) or install Python from python.org with *Add python.exe to PATH* ticked.

**`pip install` fails for `opencv-python-headless` or `numpy`**
Use Python 3.11-3.13 and upgrade pip first (`python -m pip install --upgrade pip`). Install only `opencv-python-headless`; having `opencv-python` as well can cause import clashes.

**`rembg` is slow the first time, or the model download is blocked**
The first run downloads a ~170 MB model to `%USERPROFILE%\.u2net`. Behind a proxy, pass `--bg grabcut` (no download, a little less precise on hair). Other models: `--rembg-model u2net_human_seg`.

## Portrait

**`Portrait not found: photo\karthik_vana.jpg`**
Copy the photograph to exactly that path or pass `--input <file>`. The folder is git-ignored on purpose.

**Hair is clipped or there is a light halo**
Try the other cut-out (`--bg grabcut`, or `--rembg-model u2net_human_seg`), or adjust the crop: `--head-margin 1.4` (more room around the head), `--top-pad 0.08`, or give an explicit box `--crop LEFT,TOP,RIGHT,BOTTOM` in source pixels.

**Too bright, too dark, or too soft**
Run `python scripts\make_ascii_svg.py` with `--gamma 0.7` (darker), `--weight 1` (lighter glyphs) or `--weight 2` (heavier), `--contrast 1.1`/`1.4`, or `--cols 150` (more detail, smaller glyphs). `--preview-png build\p.png` writes a quick preview.

**The portrait looks smeared or glyphs sit in the wrong columns**
Browsers collapse runs of ordinary spaces in SVG text even with `xml:space="preserve"`, which shifts every glyph after the gap. The generator therefore writes interior blanks as no-break spaces (U+00A0). If you edit `make_ascii_svg.py`, keep that, and keep `textLength` on every row.

**The animation does not play**
The CSS animation is skipped when the viewer asks for reduced motion (Windows *Show animations*, macOS *Reduce motion*, the browser setting); they see the finished artwork, and the README also swaps in the `-static.svg` files. It runs once when the image loads; reload to replay. Some mobile apps show the finished state only.

## Heatmap and data

**`no contribution cells found` / `HTTP 200 but no calendar markup`**
GitHub changed the page or returned an interstitial. Nothing was overwritten. Use the fallback: in GitHub Actions it is automatic (the per-run `GITHUB_TOKEN`); locally set a token for the session only:

```powershell
$env:GITHUB_TOKEN = "<fine-grained token, public data only>"
python scripts\fetch_contributions.py --source graphql
Remove-Item Env:GITHUB_TOKEN
```

Never put a token in a file. If the fallback is also unavailable, update `parse_calendar_html` for the new markup; `tests/test_contributions.py` shows the expected shape.

**`HTTP 429` / `HTTP 403`** The fetcher retries with back-off and honours `Retry-After`. If it still fails, rerun later; the previous data stays in place.

**`calendar ends on ... data looks stale`** GitHub served a cached calendar. Rerun later.

**Warning: `sum of day counts (X) differs from the total GitHub reports (Y)`** The footer then omits the total rather than choosing one number. Counts come from tooltip text; if GitHub reworded it, update `COUNT_RE`.

**The heatmap says `Waiting for the first data refresh`** That is the empty placeholder. Run `fetch_contributions.py` then `render_heatmap_svg.py`, or run the workflow once.

**My private activity is missing** The public calendar shows only public contributions unless *Private contributions* is enabled in your profile settings.

**Dates look one day off** The calendar uses GitHub's own day boundaries (UTC-based as served). The scripts keep the dates as GitHub reports them and use UTC for "today". A streak that ends on a still-empty current day is not counted as broken until that day is over.

## GitHub Actions

**The workflow does not appear in the Actions tab** The `.github` folder was not pushed. The web uploader skips dot-folders; push with git and check `git ls-files .github`.

**`git push` fails with 403 in the last step** Settings > Actions > General > Workflow permissions > *Read and write permissions*, or allow `github-actions[bot]` through branch protection.

**It never runs on schedule** Schedules run only on the default branch, can be delayed by several minutes, and are paused after 60 days without repository activity (re-enable in the Actions tab). Forks have schedules disabled by default.

**It succeeds but no commit appears** Correct when nothing changed: the commit step prints `No changes - nothing to commit.` The data file is rewritten only when the day data differs, so re-running on the same day creates no diff.

**The new heatmap is not visible yet** GitHub and browsers cache images for a few minutes. Hard-refresh (Ctrl+F5).

**`validate_assets.py` fails in CI** Read the annotation: it names the file and the rule (for example text overflowing the canvas, a non-pinned action, a token-like string, or a raster image outside `photo/`). Fix it locally with `python scripts\validate_assets.py`.

## Git on Windows

**`LF will be replaced by CRLF` warnings** Harmless; `.gitattributes` keeps files LF so generated assets never produce line-ending-only diffs.
