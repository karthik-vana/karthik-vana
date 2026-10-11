#!/usr/bin/env python3
"""Validate the generated assets and repository hygiene. Exit status 1 if any check fails.

Checks
  python     every scripts/*.py and tests/*.py compiles
  svg        every assets/*.svg is well-formed, has width/height/viewBox, contains no scripts,
             foreignObject, event handlers or external references, stays under 1 MiB, and its
             monospace text fits inside the canvas (estimate: characters x font size x 0.6)
  heatmap    cells in assets/contrib-heatmap.svg match data/contributions.json one-to-one
             (position <-> date, fill <-> level, seven weekday rows, at most 54 week columns)
  json       data/contributions.json satisfies the schema (see contrib_data.validate_document)
  readme     every local image path in README.md exists and every <img> has alt text
  workflow   triggers, least-privilege permissions, pinned actions (full parse needs PyYAML)
  hygiene    no token-like strings, no committed photographs, .gitignore covers local files

Usage: python scripts/validate_assets.py [--ci]
``--ci`` additionally prints GitHub Actions error annotations for failures.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from common import ASSETS_DIR, DATA_DIR, MONO_ADVANCE, ROOT, THEME, fmt
from contrib_data import DataError, validate_document

MAX_SVG_BYTES = 1024 * 1024
FORBIDDEN_TAGS = {"script", "foreignObject", "iframe", "object", "embed", "audio", "video"}
TOKEN_PATTERNS = {
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    "GitHub fine-grained token": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
}
SKIP_DIRS = {".git", "build", "photo", ".venv", "venv", "__pycache__", "node_modules", ".idea", ".vscode"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"}


@dataclass
class Result:
    name: str
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    skipped: str = ""

    @property
    def status(self) -> str:
        return "SKIP" if self.skipped else ("FAIL" if self.problems else "PASS")


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def iter_files() -> list[Path]:
    return [p for p in ROOT.rglob("*") if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS)]


# ---------------------------------------------------------------------------- checks
def check_python() -> Result:
    r = Result("python")
    files = sorted((ROOT / "scripts").glob("*.py")) + sorted((ROOT / "tests").glob("*.py"))
    for f in files:
        try:
            compile(f.read_text(encoding="utf-8"), str(f), "exec")  # in memory: leaves no .pyc behind
        except (SyntaxError, ValueError) as exc:
            r.problems.append(f"{f.relative_to(ROOT)}: {exc}")
    r.notes.append(f"{len(files)} files compiled")
    return r


def _ancestors(root: ET.Element) -> dict[ET.Element, ET.Element]:
    return {child: parent for parent in root.iter() for child in parent}


def _inherited(el: ET.Element, attr: str, parents: dict[ET.Element, ET.Element]) -> str | None:
    cur: ET.Element | None = el
    while cur is not None:
        if attr in cur.attrib:
            return cur.attrib[attr]
        cur = parents.get(cur)
    return None


def _translate_x(el: ET.Element, parents: dict[ET.Element, ET.Element]) -> float:
    total, cur = 0.0, el
    while cur is not None:
        m = re.match(r"\s*translate\(\s*(-?[\d.]+)", cur.attrib.get("transform", ""))
        if m:
            total += float(m.group(1))
        cur = parents.get(cur)
    return total


def check_svg_file(path: Path) -> list[str]:
    problems: list[str] = []
    rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    if path.stat().st_size > MAX_SVG_BYTES:
        problems.append(f"{rel}: {path.stat().st_size} bytes exceeds {MAX_SVG_BYTES}")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return [f"{rel}: not well-formed XML ({exc})"]
    if local(root.tag) != "svg":
        return [f"{rel}: root element is <{local(root.tag)}>"]
    try:
        _, _, vb_w, vb_h = (float(v) for v in root.attrib["viewBox"].split())
        if not root.attrib.get("width") or not root.attrib.get("height"):
            problems.append(f"{rel}: missing width/height")
    except (KeyError, ValueError):
        return problems + [f"{rel}: missing or invalid viewBox"]

    parents = _ancestors(root)
    for el in root.iter():
        tag = local(el.tag)
        if tag in FORBIDDEN_TAGS:
            problems.append(f"{rel}: forbidden element <{tag}>")
        for attr, value in el.attrib.items():
            name = local(attr)
            if name.lower().startswith("on"):
                problems.append(f"{rel}: event-handler attribute {name!r} on <{tag}>")
            if name == "href" and not value.startswith("#"):
                problems.append(f"{rel}: external reference {value[:40]!r} on <{tag}>")
        if tag == "style" and el.text and re.search(r"@import|url\(\s*['\"]?https?:", el.text):
            problems.append(f"{rel}: <style> loads an external resource")
        if tag == "text" and "textLength" not in el.attrib and el.attrib.get("text-anchor") is None and "x" in el.attrib:
            text = "".join(el.itertext())
            size = float(_inherited(el, "font-size", parents) or 16)
            right = float(el.attrib["x"]) + _translate_x(el, parents) + len(text) * size * MONO_ADVANCE
            if right > vb_w + 0.5:
                problems.append(f"{rel}: text {text[:30]!r} ends at x={right:.0f}, outside the {vb_w:.0f}-wide canvas")
            y = float(el.attrib.get("y", 0))
            if y > vb_h or y < 0:
                problems.append(f"{rel}: text {text[:30]!r} has y={y:.0f}, outside the canvas")
    return problems


def check_svgs() -> Result:
    r = Result("svg")
    files = sorted(ASSETS_DIR.glob("*.svg"))
    if not files:
        r.problems.append("no SVG files found in assets/")
    for f in files:
        r.problems += check_svg_file(f)
    r.notes.append(f"{len(files)} SVG files")
    return r


def check_json() -> tuple[Result, dict | None]:
    r = Result("json")
    path = DATA_DIR / "contributions.json"
    if not path.is_file():
        r.problems.append("data/contributions.json is missing (run scripts/fetch_contributions.py)")
        return r, None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        for warning in validate_document(doc):
            r.notes.append(f"warning: {warning}")
    except (ValueError, DataError) as exc:
        r.problems.append(str(exc))
        return r, None
    r.notes.append(f"{len(doc['days'])} days, {doc['range']['from']} to {doc['range']['to']}, source {doc['source']['type']}")
    return r, doc


def check_heatmap(doc: dict | None) -> Result:
    r = Result("heatmap")
    path = ASSETS_DIR / "contrib-heatmap.svg"
    if doc is None or not path.is_file():
        r.skipped = "needs valid data/contributions.json and assets/contrib-heatmap.svg"
        return r
    from render_heatmap_svg import CELL, GRID_X0, GRID_Y0, PITCH, build_weeks  # local import: avoids cycles

    root = ET.parse(path).getroot()
    cells = {
        (el.attrib["x"], el.attrib["y"]): el.attrib.get("fill")
        for el in root.iter()
        if local(el.tag) == "rect" and el.attrib.get("width") == str(CELL) and el.attrib.get("height") == str(CELL)
    }
    weeks, _ = build_weeks(doc["days"])
    expected: dict[tuple[str, str], str] = {}
    levels = {d["date"]: d["level"] for d in doc["days"]}
    del levels  # positions are recomputed from the weeks grid below
    for col, week in enumerate(weeks):
        for row, cell in enumerate(week):
            if cell:
                expected[(fmt(GRID_X0 + col * PITCH), fmt(GRID_Y0 + row * PITCH))] = THEME.heat[cell["level"]]
    if len(weeks) > 54:
        r.problems.append(f"{len(weeks)} week columns (expected at most 54)")
    if set(cells) != set(expected):
        r.problems.append(f"cell positions differ: {len(cells)} in SVG vs {len(expected)} from data")
    wrong = [pos for pos, fill in expected.items() if cells.get(pos) != fill]
    if wrong:
        r.problems.append(f"{len(wrong)} cells have a fill that does not match their level, e.g. {wrong[0]}")
    if len({y for _, y in cells}) != 7:
        r.problems.append(f"expected 7 weekday rows, found {len({y for _, y in cells})}")
    r.notes.append(f"{len(cells)} cells, {len(weeks)} week columns, 7 rows, fills match levels")
    return r


def check_readme() -> Result:
    r = Result("readme")
    path = ROOT / "README.md"
    if not path.is_file():
        r.problems.append("README.md is missing")
        return r
    text = path.read_text(encoding="utf-8")
    text_no_comments = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    refs = set(re.findall(r'(?:src|srcset)="([^"]+)"', text_no_comments)) | set(re.findall(r"!\[[^\]]*\]\(([^)\s]+)", text_no_comments))
    for ref in sorted(refs):
        if re.match(r"(?:https?:|mailto:|#|data:)", ref):
            continue
        if not (ROOT / ref).is_file():
            r.problems.append(f"README references {ref!r} which does not exist")
    for tag in re.findall(r"<img\b[^>]*>", text_no_comments):
        alt = re.search(r'alt="([^"]*)"', tag)
        if not alt or not alt.group(1).strip():
            r.problems.append(f"<img> without alt text: {tag[:60]}...")
    r.notes.append(f"{len(refs)} local asset references checked")
    return r


def check_workflow() -> Result:
    r = Result("workflow")
    path = ROOT / ".github" / "workflows" / "update-profile-art.yml"
    if not path.is_file():
        r.problems.append(f"{path.relative_to(ROOT)} is missing - dot-folders are skipped by the GitHub web uploader; push with git")
        return r
    text = path.read_text(encoding="utf-8")
    if "pull_request_target" in text:
        r.problems.append("uses pull_request_target (not needed and dangerous)")
    for line in re.findall(r"uses:\s*(\S+)", text):
        if "@" not in line or line.split("@", 1)[1] in {"main", "master", "latest"}:
            r.problems.append(f"action {line!r} is not pinned to a version")
    for secret in set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text)):
        if secret != "GITHUB_TOKEN":
            r.problems.append(f"references secret {secret!r}; only the built-in GITHUB_TOKEN is expected")
    try:
        import yaml  # optional
    except ImportError:
        r.notes.append("PyYAML not installed: structural checks done by text only (pip install -r requirements-dev.txt)")
        for needle in ("schedule:", "workflow_dispatch:", "permissions:", "contents: write"):
            if needle not in text:
                r.problems.append(f"expected {needle!r} in the workflow")
        return r
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        r.problems.append(f"YAML does not parse: {exc}")
        return r
    triggers = data.get("on", data.get(True)) or {}
    for needed in ("schedule", "workflow_dispatch"):
        if needed not in triggers:
            r.problems.append(f"missing trigger {needed!r}")
    if data.get("permissions") != {"contents": "read"}:
        r.problems.append(f"top-level permissions should be {{contents: read}}, got {data.get('permissions')!r}")
    for name, job in data.get("jobs", {}).items():
        perms = job.get("permissions", {})
        if perms != {"contents": "write"}:
            r.problems.append(f"job {name!r} permissions should be exactly {{contents: write}}, got {perms!r}")
    r.notes.append("YAML parsed; triggers, permissions and action pins verified")
    return r


def check_hygiene() -> Result:
    r = Result("hygiene")
    files = iter_files()
    for f in files:
        rel = f.relative_to(ROOT)
        if f.suffix.lower() in IMAGE_SUFFIXES:
            r.problems.append(f"{rel}: raster image in the repository (the source photo must stay in photo/, which is git-ignored)")
            continue
        if f.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            body = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in TOKEN_PATTERNS.items():
            if pattern.search(body):
                r.problems.append(f"{rel}: looks like a {label}")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8") if (ROOT / ".gitignore").is_file() else ""
    for needed in ("build/", "photo/*", ".env"):
        if needed not in gitignore:
            r.problems.append(f".gitignore does not cover {needed!r}")
    r.notes.append(f"{len(files)} files scanned for secrets and photographs")
    return r


# ------------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ci", action="store_true", help="also emit GitHub Actions error annotations")
    args = p.parse_args(argv)

    json_result, doc = check_json()
    results = [check_python(), check_svgs(), json_result, check_heatmap(doc), check_readme(), check_workflow(), check_hygiene()]
    failed = 0
    for res in results:
        print(f"[{res.status}] {res.name:9s} {'; '.join(res.notes) if res.notes else res.skipped}")
        for problem in res.problems:
            failed += 1
            print(f"         - {problem}")
            if args.ci:
                print(f"::error title=validate_assets ({res.name})::{problem}")
    print(f"\n{'FAILED' if failed else 'OK'}: {sum(r.status == 'PASS' for r in results)} passed, "
          f"{sum(r.status == 'SKIP' for r in results)} skipped, {failed} problem(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
