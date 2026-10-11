# Installation

Everything below assumes the repository `karthik-vana/karthik-vana` (GitHub's special profile repository). Commands are written for **Windows PowerShell first**; Linux/macOS equivalents follow.

## What runs where

| Job | Where | Needs |
| --- | --- | --- |
| Portrait: `prep_photo.py`, `make_ascii_svg.py` | your machine, once (or when the photo changes) | `requirements-portrait.txt` (+ optional `requirements-optional.txt`) |
| Info card: `make_info_card.py` | your machine, when `profile_config.py` changes | standard library only |
| Heatmap: `fetch_contributions.py`, `render_heatmap_svg.py` | **GitHub Actions daily**, or your machine | `requirements.txt` (`requests` only) |
| Checks: `validate_assets.py`, `tests/` | both | standard library (PyYAML optional) |

The daily workflow installs `requirements.txt` only, so it never downloads NumPy, OpenCV or ONNX.

## 1. Prerequisites (PowerShell)

```powershell
python --version        # 3.11 or newer; the project is tested on 3.12
py -3 --version         # the Windows launcher; use it if `python` is not found
git --version
gh --version            # optional: GitHub CLI, only for triggering/inspecting workflow runs
```

If `python` opens the Microsoft Store, install Python from python.org (tick *Add python.exe to PATH*) or use `py -3` everywhere below.

## 2. Get the repository and add the files

```powershell
cd $HOME\Documents
git clone https://github.com/karthik-vana/karthik-vana.git
cd karthik-vana
```

Extract `karthik-vana-profile.zip`, then replace the old working tree with its contents (this removes files from earlier versions of the profile; `.git` is kept):

```powershell
Expand-Archive -Path "$HOME\Downloads\karthik-vana-profile.zip" -DestinationPath "$HOME\Downloads\kv-new" -Force
Get-ChildItem -Force | Where-Object Name -ne ".git" | Remove-Item -Recurse -Force
Copy-Item "$HOME\Downloads\kv-new\karthik-vana\*" -Destination . -Recurse -Force
Get-ChildItem -Force -Name        # expect README.md, scripts, assets, .github, ...
```

> **Use git to publish, not the web "Upload files" button.** The web uploader silently skips dot-folders such as `.github/`, which is where the workflow lives. After pushing, `git ls-files .github` must list `.github/workflows/update-profile-art.yml`.

## 3. Python environment

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
# If PowerShell says "running scripts is disabled on this system":
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass     (affects this window only)
python -m pip install --upgrade pip
pip install -r requirements-portrait.txt
```

Optional, recommended: neural background removal (cleaner hair edges). It adds `onnxruntime` and downloads a ~170 MB model once to `%USERPROFILE%\.u2net`. Without it the pipeline falls back to OpenCV GrabCut automatically.

```powershell
pip install -r requirements-optional.txt
```

## 4. Generate the assets

Put your photograph where the scripts expect it (this folder is git-ignored, so the photo is never committed):

```powershell
Copy-Item "$HOME\Downloads\karthik_vana.jpg" -Destination photo\karthik_vana.jpg
```

```powershell
python scripts\prep_photo.py --input photo\karthik_vana.jpg   # cut-out, crop, CLAHE  -> build\portrait_prepped.png
python scripts\make_ascii_svg.py                              # -> assets\karthik-ascii.svg (+ -static)
python scripts\make_info_card.py                              # -> assets\info-card.svg (+ -static)
python scripts\fetch_contributions.py                         # -> data\contributions.json (real data)
python scripts\render_heatmap_svg.py                          # -> assets\contrib-heatmap.svg
python scripts\validate_assets.py                             # SVG/XML, JSON, README paths, workflow, secrets
python -m unittest discover -s tests -v                       # unit tests
```

Static (non-animated) previews: set `STATIC=1` for a session, or pass `--static`:

```powershell
$env:STATIC = "1"; python scripts\make_info_card.py; Remove-Item Env:STATIC
```

Useful portrait options (all optional, see `--help`): `--cols 150` (finer), `--weight 1` (lighter glyphs) to `2` (heavier), `--gamma 0.7` (darker mid-tones), `--contrast 1.1`, `--polarity print` (dense = shadow, on a light panel), `--ramp-preset spec` (the ramp from the original brief), `--preview-png build\p.png` (quick Pillow preview).

### Linux / macOS equivalents

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-portrait.txt          # add requirements-optional.txt for rembg
cp ~/Downloads/karthik_vana.jpg photo/karthik_vana.jpg
python scripts/prep_photo.py --input photo/karthik_vana.jpg
python scripts/make_ascii_svg.py && python scripts/make_info_card.py
python scripts/fetch_contributions.py && python scripts/render_heatmap_svg.py
python scripts/validate_assets.py && python -m unittest discover -s tests -v
STATIC=1 python scripts/make_info_card.py          # static preview only
```

## 5. Preview locally

* Open an SVG in a browser to watch its one-shot animation, press F5 to replay: `Start-Process msedge assets\karthik-ascii.svg` (or `chrome`).
* VS Code's Markdown preview (`Ctrl+Shift+V`) shows the layout; animation may not play there.
* The faithful check is GitHub itself: push to a branch and open `https://github.com/karthik-vana/karthik-vana/blob/<branch>/README.md`.

## 6. Commit and push

```powershell
git status
git add .
git commit -m "feat: animated terminal-style profile README"
git push origin main
git ls-files .github              # must list .github/workflows/update-profile-art.yml
```

## 7. GitHub settings for the daily refresh

1. **Settings > Actions > General > Actions permissions:** allow actions (the default).
2. **Settings > Actions > General > Workflow permissions:** the workflow asks for `contents: write` on its single job and `contents: read` everywhere else. If a run fails at `git push` with HTTP 403, choose *Read and write permissions* here.
3. If `main` has branch protection that blocks direct pushes, allow `github-actions[bot]` to bypass it, or the commit step cannot succeed.
4. Scheduled workflows run only from the default branch. GitHub pauses them after 60 days without repository activity; the daily commit keeps this one active. If it is ever paused, press *Enable workflow* in the Actions tab.

No secrets are required. The optional GraphQL fallback uses the per-run `GITHUB_TOKEN` that GitHub creates automatically.

## 8. Trigger and inspect a run

Web: **Actions > Update profile art > Run workflow**, then open the run and expand each step.

GitHub CLI:

```powershell
gh auth login
gh workflow run update-profile-art.yml
gh run list --workflow update-profile-art.yml --limit 5
gh run watch                                  # follow the newest run
gh run view --log                             # full logs of the newest run
```

A healthy run ends with either `Wrote data\contributions.json` followed by a commit, or `Unchanged` / `No changes - nothing to commit.` On a failed fetch the job fails and nothing is committed, so the previous heatmap stays on your profile.

## 9. Everyday edits

* **Card text:** edit `scripts/profile_config.py`, then `python scripts\make_info_card.py`. A value that would not fit raises an error instead of being clipped.
* **New photo:** replace `photo\karthik_vana.jpg`, then run `prep_photo.py` and `make_ascii_svg.py`.
* **Layout widths:** the README uses `99%` for the heatmap and `49.5%` + `49.5%` for the portrait and card so they stay as wide as the heatmap at any container width. Keep the two `<picture>` tags adjacent with no whitespace between them.

## Data source and attribution

Heatmap data comes from GitHub's public contribution calendar for the profile, `https://github.com/users/karthik-vana/contributions`, fetched once a day with an identifying `User-Agent`. GitHub is the source of that data. Only public contributions appear unless *Private contributions* is enabled in your GitHub profile settings.
