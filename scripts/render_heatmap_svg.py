#!/usr/bin/env python3
"""Render a 53-week terminal-themed contribution SVG from verified JSON data only."""
from __future__ import annotations
import argparse, html, json, math
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

PALETTE=['#161b22','#0e4429','#006d32','#26a641','#39d353']


def render(data: dict, width: int=980, animated: bool=True) -> str:
    verified=data.get('status')=='verified' and data.get('source_verified') is True
    days=data.get('days',[]) if verified else []
    counts={}
    for item in days:
        try:
            day=date.fromisoformat(item['date'])
            count=item.get('count')
            level=int(item.get('level',0))
            if not 0<=level<=4: continue
            counts[day]=(count,level)
        except (ValueError,TypeError,KeyError):
            continue
    if counts:
        first=min(counts); last=max(counts)
        # Align to Sunday and include enough whole weeks to hold the real source range.
        start=first-timedelta(days=(first.weekday()+1)%7)
        end=last+timedelta(days=(5-last.weekday())%7) # Saturday
        weeks=min(53,max(1,math.ceil(((end-start).days+1)/7)))
        grid_end=start+timedelta(days=weeks*7-1)
        if last>grid_end:
            start=grid_end-timedelta(days=weeks*7-1)
    else:
        # Empty, clearly unverified layout: draw neutral cells but make no activity claims.
        last=date.today()
        start=last-timedelta(days=370)
        start=start-timedelta(days=(start.weekday()+1)%7)
        weeks=53
        grid_end=start+timedelta(days=weeks*7-1)
    cell=10.0; gap=4.0; left=26.0; top=72.0
    grid_w=53*(cell+gap)-gap
    height=212.0
    actual_width=max(width,left*2+grid_w+8)
    parts=[
      f'<svg xmlns="http://www.w3.org/2000/svg" width="{actual_width:.0f}" height="{height:.0f}" viewBox="0 0 {actual_width:.0f} {height:.0f}" role="img" aria-labelledby="title desc">',
      '<title id="title">GitHub contribution calendar for karthik-vana</title>',
      '<desc id="desc">53-week contribution heatmap. Values are shown only after the public GitHub calendar has been fetched and validated. See the labels for verification status.</desc>',
      '<rect x="0.5" y="0.5" width="99.8%" height="99%" rx="10" fill="#0d1117" stroke="#30363d"/>',
      '<circle cx="17" cy="17" r="4" fill="#ff5f56"/><circle cx="31" cy="17" r="4" fill="#ffbd2e"/><circle cx="45" cy="17" r="4" fill="#27c93f"/>',
      '<text x="64" y="21" font-family="Consolas,monospace" font-size="11" fill="#8b949e">karthik@github ~ $ ./contributions.sh</text>',
      '<text x="26" y="48" font-family="Consolas,monospace" font-size="15" font-weight="600" fill="#e6edf3">Contribution activity</text>'
    ]
    if verified:
        stats=data.get('stats') or {}
        total=stats.get('total_contributions')
        period=f"{data.get('coverage',{}).get('from','?')} → {data.get('coverage',{}).get('to','?')}"
        total_text=f'{total:,} contributions' if isinstance(total,int) else 'Contribution total unavailable'
        status=f'{total_text}  ·  {period}'
        status_color='#8b949e'
    else:
        status='LIVE DATA PENDING FIRST SUCCESSFUL REFRESH · NO TOTALS SHOWN'
        status_color='#d29922'
    parts.append(f'<text x="26" y="61" font-family="Consolas,monospace" font-size="10" fill="{status_color}">{html.escape(status)}</text>')
    # Labels are placed on the left and cells occupy exactly 53 week columns.
    weekday_labels=[('Mon',1),('Wed',3),('Fri',5)]
    for label,row in weekday_labels:
        y=top+row*(cell+gap)+8
        parts.append(f'<text x="{left-8:.1f}" y="{y:.1f}" text-anchor="end" font-family="Consolas,monospace" font-size="8.5" fill="#8b949e">{label}</text>')
    for col in range(53):
        for row in range(7):
            day=start+timedelta(days=col*7+row)
            item=counts.get(day)
            known=item is not None
            if known:
                count,level=item
                if isinstance(count,int):
                    if count==0: level=0
                    elif level==0: level=1 if count==1 else 2 if count<=4 else 3 if count<=9 else 4
                color=PALETTE[level]
                if isinstance(count,int): label=f'{day.isoformat()}: {count} contribution'+('s' if count!=1 else '')
                else: label=f'{day.isoformat()}: count unavailable; intensity level {level}'
            elif verified and data.get('coverage',{}).get('from') and data.get('coverage',{}).get('to') and data['coverage']['from']<=day.isoformat()<=data['coverage']['to']:
                color=PALETTE[0]
                label=f'{day.isoformat()}: source cell unavailable'
            else:
                color='#0d1117'
                label=f'{day.isoformat()}: outside verified coverage' if verified else f'{day.isoformat()}: no verified data loaded'
            x=left+col*(cell+gap); y=top+row*(cell+gap)
            # The outline cell is always visible, even when a client suppresses SMIL.
            parts.append(f'<rect class="contribution-cell" x="{x:.1f}" y="{y:.1f}" width="{cell:.1f}" height="{cell:.1f}" rx="2" fill="#0d1117" stroke="#30363d" stroke-width="0.55"><title>{html.escape(label)}</title></rect>')
            if animated:
                begin=(col+row)*0.016
                parts.append(f'<rect x="{x+0.6:.1f}" y="{y+0.6:.1f}" width="{cell-1.2:.1f}" height="{cell-1.2:.1f}" rx="1.5" fill="{color}" opacity="0"><animate attributeName="opacity" from="0" to="1" begin="{begin:.3f}s" dur="0.24s" fill="freeze"/></rect>')
            else:
                parts.append(f'<rect x="{x+0.6:.1f}" y="{y+0.6:.1f}" width="{cell-1.2:.1f}" height="{cell-1.2:.1f}" rx="1.5" fill="{color}"/>')
    legend_x=actual_width-152
    legend_y=177
    parts.append(f'<text x="{legend_x-37}" y="{legend_y+8}" font-family="Consolas,monospace" font-size="9" fill="#8b949e">Less</text>')
    for i,color in enumerate(PALETTE):
        parts.append(f'<rect x="{legend_x+i*14}" y="{legend_y}" width="10" height="10" rx="2" fill="{color}" stroke="#30363d" stroke-width="0.35"/>')
    parts.append(f'<text x="{legend_x+5*14+4}" y="{legend_y+8}" font-family="Consolas,monospace" font-size="9" fill="#8b949e">More</text>')
    update=data.get('updated_at_utc') if verified else None
    footer=f"Snapshot updated {update}" if update else ('Source: github.com/users/karthik-vana/contributions' if not verified else 'Source: public GitHub contribution calendar')
    parts.append(f'<text x="26" y="199" font-family="Consolas,monospace" font-size="9.5" fill="#6e7681">{html.escape(footer)}</text>')
    parts.append('</svg>')
    result='\n'.join(parts)+'\n'
    ET.fromstring(result)
    return result


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,default=Path('data/contributions.json'))
    p.add_argument('--output',type=Path,default=Path('assets/contrib-heatmap.svg'))
    p.add_argument('--static',action='store_true',help='Disable one-shot cell reveal animation')
    args=p.parse_args()
    try:
        data=json.loads(args.input.read_text(encoding='utf-8'))
        if data.get('username')!='karthik-vana': raise ValueError('Contribution data username does not match the configured profile')
        if data.get('status')=='verified' and data.get('source_verified') is True:
            if len(data.get('days',[]))<250: raise ValueError('Verified contribution data has fewer than 250 daily records')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        svg=render(data,animated=not args.static)
        temp=args.output.with_suffix(args.output.suffix+'.tmp')
        temp.write_text(svg,encoding='utf-8'); ET.fromstring(temp.read_text(encoding='utf-8')); temp.replace(args.output)
        print(f"Wrote {args.output} ({'verified' if data.get('status')=='verified' else 'unverified/sample'} input)")
    except (OSError,json.JSONDecodeError,ValueError) as exc:
        p.exit(2,f'error: {exc}\n')
    return 0
if __name__=='__main__': raise SystemExit(main())
