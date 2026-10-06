#!/usr/bin/env python3
"""Validate the generated SVG/XML, JSON, Python syntax, README asset paths, and workflow shape."""
from __future__ import annotations
import argparse, ast, json, re, sys
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
SVG_EXPECTED=['assets/karthik-ascii.svg','assets/karthik-ascii-static.svg','assets/info-card.svg','assets/info-card-static.svg','assets/contrib-heatmap.svg','assets/contrib-heatmap-static.svg']


def validate(root: Path, extra_svgs: list[Path] | None=None) -> list[str]:
    errors=[]
    for rel in SVG_EXPECTED:
        path=root/rel
        if not path.is_file(): errors.append(f'Missing required SVG: {rel}'); continue
        try:
            ET.parse(path)
        except (ET.ParseError,OSError) as exc: errors.append(f'Invalid SVG/XML {rel}: {exc}')
    for path in extra_svgs or []:
        try: ET.parse(path)
        except (ET.ParseError,OSError) as exc: errors.append(f'Invalid extra SVG/XML {path}: {exc}')
    for path in sorted((root/'scripts').glob('*.py')):
        try: ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
        except (SyntaxError,OSError) as exc: errors.append(f'Python syntax error {path.name}: {exc}')
    try:
        profile=json.loads((root/'data/profile.json').read_text(encoding='utf-8'))
        if profile.get('username')!='karthik-vana': errors.append('profile.json username mismatch')
    except (OSError,json.JSONDecodeError) as exc: errors.append(f'Invalid profile JSON: {exc}')
    try:
        data=json.loads((root/'data/contributions.json').read_text(encoding='utf-8'))
        if data.get('username')!='karthik-vana': errors.append('contributions.json username mismatch')
        if data.get('status')=='verified' and data.get('source_verified') is not True: errors.append('verified data must set source_verified=true')
        if data.get('status')!='verified' and data.get('stats') not in (None,{}): errors.append('unverified sample data must not advertise statistics')
    except (OSError,json.JSONDecodeError) as exc: errors.append(f'Invalid contribution JSON: {exc}')
    readme=root/'README.md'
    if readme.is_file():
        content=readme.read_text(encoding='utf-8')
        for path in re.findall(r'<img\b[^>]*?src=["\']([^"\']+)["\']',content,re.I):
            if path.startswith(('http://','https://','data:','#')): continue
            clean=path.split('#',1)[0].split('?',1)[0]
            if not (root/clean).is_file(): errors.append(f'README image path does not exist: {path}')
    workflow=root/'.github/workflows/update-profile-art.yml'
    if not workflow.is_file(): errors.append('Missing GitHub Actions workflow')
    else:
        text=workflow.read_text(encoding='utf-8')
        for required in ['workflow_dispatch','schedule:','contents: write','fetch_contributions.py','render_heatmap_svg.py','git diff --quiet']:
            if required not in text: errors.append(f'Workflow missing expected configuration: {required}')
    return errors


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=ROOT)
    p.add_argument('--extra-svg',type=Path,action='append',default=[])
    args=p.parse_args()
    errors=validate(args.root,args.extra_svg)
    if errors:
        print('VALIDATION FAILED')
        for error in errors: print(f' - {error}')
        return 1
    print('PASS: required SVG files parse as XML')
    print('PASS: Python scripts parse successfully')
    print('PASS: profile and contribution JSON schemas are readable')
    print('PASS: local README image paths resolve')
    print('PASS: workflow contains required scheduling, permission, fetch/render and diff checks')
    return 0
if __name__=='__main__': raise SystemExit(main())
