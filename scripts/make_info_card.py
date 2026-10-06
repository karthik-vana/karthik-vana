#!/usr/bin/env python3
"""Generate animated and static neofetch-style identity cards from data/profile.json."""
from __future__ import annotations
import argparse, html, json, textwrap
from pathlib import Path
from xml.etree import ElementTree as ET

DEFAULT_PROFILE=Path('data/profile.json')
DEFAULT_ANIMATED=Path('assets/info-card.svg')
DEFAULT_STATIC=Path('assets/info-card-static.svg')


def load_rows(profile: dict) -> list[tuple[str,str]]:
    """Create compact card rows from the single source-of-truth profile configuration."""
    card = profile.get('card', {})
    return [
      ('Name', profile.get('name','Karthik Vana')),
      ('Role', card.get('role','AI/ML Engineer · Data Science')),
      ('Focus', card.get('focus','GenAI · RAG · NLP · Computer Vision')),
      ('Education', card.get('education','M.Tech CSE — AI/ML (pursuing)')),
      ('Research', card.get('research','GIM SRIP-2026 · Big Data Analytics')),
      ('Stack', card.get('stack','Python · TensorFlow · LangChain · LangGraph · RAG')),
      ('Projects', card.get('projects','ShopMind AI · FoodVision AI · Telecom Retention')),
      ('Experience', card.get('experience','GIM · The Skill Union · Innomatics')),
      ('Publication', card.get('publication','PV-Based UPQC · IJSREM (Apr 2025)')),
      ('Now', card.get('mission','Building practical, deployable AI systems'))
    ]


def render_card(profile: dict, animated: bool=True) -> str:
    rows=load_rows(profile)
    width=640
    first_y=83
    line_step=16
    row_gap=11
    wrapped_rows=[(label, textwrap.wrap(value, width=57, break_long_words=False, break_on_hyphens=False) or ['']) for label,value in rows]
    height=first_y + sum(max(1,len(chunks))*line_step + row_gap for _,chunks in wrapped_rows) + 20
    parts=[
      f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
      '<title id="title">Karthik Vana — neofetch profile card</title>',
      '<desc id="desc">Terminal-style professional profile generated from CV-backed JSON configuration.</desc>',
      '<rect x="0.5" y="0.5" width="639" height="%d" rx="12" fill="#0d1117" stroke="#30363d"/>'% (height-1),
      '<path d="M12 1 H628 Q639 1 639 12 V37 H1 V12 Q1 1 12 1Z" fill="#161b22"/>',
      '<circle cx="20" cy="19" r="5" fill="#ff5f56"/><circle cx="37" cy="19" r="5" fill="#ffbd2e"/><circle cx="54" cy="19" r="5" fill="#27c93f"/>',
      '<text x="320" y="23" text-anchor="middle" font-family="Consolas,monospace" font-size="12" fill="#8b949e">karthik@github — neofetch</text>',
      '<text x="24" y="58" font-family="Consolas,monospace" font-size="12" fill="#3fb950">karthik@github ~ $ whoami</text>'
    ]
    y=first_y
    for i,(label,chunks) in enumerate(wrapped_rows):
        row_group=[f'<text x="24" y="{y}" font-family="Consolas,monospace" font-size="13" fill="#8b949e">{html.escape(label.ljust(11))}:</text>']
        for k,chunk in enumerate(chunks):
            row_group.append(f'<text x="150" y="{y+k*line_step}" font-family="Consolas,monospace" font-size="12.5" fill="#e6edf3">{html.escape(chunk)}</text>')
        group=''.join(row_group)
        if animated:
            begin=f'{0.16+i*0.14:.2f}s'
            parts.append(f'<g opacity="0.72" transform="translate(0 8)">{group}<animate attributeName="opacity" values="0.72;1" begin="{begin}" dur="0.28s" fill="freeze"/><animateTransform attributeName="transform" type="translate" values="0 8;0 0" begin="{begin}" dur="0.28s" fill="freeze"/></g>')
        else:
            parts.append(f'<g>{group}</g>')
        y += max(1,len(chunks))*line_step + row_gap
    footer_y=height-14
    parts.append(f'<text x="24" y="{footer_y}" font-family="Consolas,monospace" font-size="10.5" fill="#3fb950">[ profile: CV-backed · focus: useful AI · status: iterating ]</text>')
    parts.append('</svg>')
    svg='\n'.join(parts)+'\n'
    ET.fromstring(svg)
    return svg


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile',type=Path,default=DEFAULT_PROFILE)
    p.add_argument('--animated-output',type=Path,default=DEFAULT_ANIMATED)
    p.add_argument('--static-output',type=Path,default=DEFAULT_STATIC)
    p.add_argument('--static',action='store_true',help='Generate only a static card at --animated-output')
    args=p.parse_args()
    try:
        profile=json.loads(args.profile.read_text(encoding='utf-8'))
        if not profile.get('name') or not profile.get('experience') or not profile.get('projects'):
            raise ValueError('profile JSON is missing required name, experience, or projects')
        if args.static:
            out=render_card(profile,animated=False)
            args.animated_output.parent.mkdir(parents=True,exist_ok=True)
            args.animated_output.write_text(out,encoding='utf-8')
            print(f'Wrote static card: {args.animated_output}')
        else:
            for path,animated in ((args.animated_output,True),(args.static_output,False)):
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(render_card(profile,animated),encoding='utf-8')
                print(f'Wrote {path}')
    except (OSError,json.JSONDecodeError,ValueError) as exc:
        p.exit(2,f'error: {exc}\n')
    return 0
if __name__=='__main__': raise SystemExit(main())
