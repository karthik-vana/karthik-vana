#!/usr/bin/env python3
"""Fetch and validate the public GitHub contribution calendar without credentials."""
from __future__ import annotations
import argparse, json, re, sys, time
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

USERNAME='karthik-vana'
SOURCE_URL=f'https://github.com/users/{USERNAME}/contributions'
DATE_RE=re.compile(r'^\d{4}-\d{2}-\d{2}$')
TAG_RE=re.compile(r'<[A-Za-z][^>]*\bdata-date\s*=\s*[\"\'](?P<date>\d{4}-\d{2}-\d{2})[\"\'][^>]*>',re.I)
ATTR_RE=re.compile(r'([\w:-]+)\s*=\s*[\"\'](.*?)[\"\']',re.S)
COUNT_RE=re.compile(r'(?P<count>no\s+contributions?|[\d,]+\s+contributions?)',re.I)
MONTHS='January February March April May June July August September October November December'.split()


def parse_count(text: str, target: date) -> int | None:
    """Extract a count only when that count label names the target date."""
    month_full = target.strftime('%B')
    month_short = target.strftime('%b')
    day = target.day
    year = target.year
    # Build portable forms instead of platform-specific strftime flags such as %-d.
    date_forms = [
        f'{month_full} {day}, {year}', f'{month_full} {day:02d}, {year}',
        f'{month_short} {day}, {year}', f'{month_short} {day:02d}, {year}',
        f'{month_full} {day}', f'{month_full} {day:02d}',
        f'{month_short} {day}', f'{month_short} {day:02d}'
    ]
    for match in COUNT_RE.finditer(text):
        # GitHub tooltip strings normally read "N contributions on Month D, YYYY".
        after_raw = text[match.end():match.end()+180]
        # Stop at the next tag boundary so a neighbouring cell's date cannot be borrowed.
        after = re.split(r'<', after_raw, maxsplit=1)[0].lower()
        before_raw = text[max(0, match.start()-180):match.start()]
        before = re.split(r'>', before_raw)[-1].lower()
        if any(form.lower() in after for form in date_forms) or any(form.lower() in before for form in date_forms):
            raw = match.group('count').lower()
            if raw.startswith('no '):
                return 0
            numbers = re.search(r'[\d,]+', raw)
            return int(numbers.group(0).replace(',', '')) if numbers else None
    return None


def parse_calendar(html_text: str) -> list[dict]:
    """Extract unique contribution dates and levels from GitHub's calendar fragment."""
    candidates=[]
    for match in TAG_RE.finditer(html_text):
        attrs=dict(ATTR_RE.findall(match.group(0)))
        try:
            day=date.fromisoformat(attrs.get('data-date',''))
        except ValueError:
            continue
        if 'data-level' not in attrs and 'data-date' not in attrs:
            continue
        try: level=int(attrs.get('data-level','0'))
        except ValueError: level=0
        if not 0 <= level <= 4: continue
        candidates.append((day,level,match.start(),match.end()))
    if not candidates:
        raise ValueError('GitHub response contained no data-date contribution cells; endpoint markup may have changed.')
    # Count labels may be included in title/tool-tip text immediately before or after each day cell.
    days_by_date={}
    for i,(day,level,start,end) in enumerate(candidates):
        left=max(0,start-900)
        right=min(len(html_text),end+900)
        context=html_text[left:right]
        count=parse_count(context,day)
        if count is None:
            # Check a tighter neighbourhood too: one date per calendar cell is expected.
            count=parse_count(html_text[max(0,start-300):min(len(html_text),end+300)],day)
        if count is not None:
            level=max(0,min(4,int(level)))
        elif level == 0:
            # Level is still valid intensity data, but a count is intentionally left unknown.
            pass
        days_by_date[day.isoformat()]={"date":day.isoformat(),"count":count,"level":level}
    result=[days_by_date[k] for k in sorted(days_by_date)]
    if len(result)<250:
        raise ValueError(f'Only {len(result)} unique contribution dates were parsed; refusing to publish a partial calendar.')
    if len(result)>380:
        raise ValueError(f'Unexpectedly large contribution calendar ({len(result)} dates); refusing to publish malformed data.')
    return result


def make_payload(days: list[dict], existing_path: Path | None) -> dict:
    total=None
    active_days=None
    if days and all(isinstance(d.get('count'),int) for d in days):
        total=sum(int(d['count']) for d in days)
        active_days=sum(1 for d in days if int(d['count'])>0)
    start,end=days[0]['date'],days[-1]['date']
    updated=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
    if existing_path and existing_path.is_file():
        try:
            old=json.loads(existing_path.read_text(encoding='utf-8'))
            old_days=old.get('days')
            if old.get('status')=='verified' and old.get('source_verified') is True and old_days==days and old.get('coverage')=={"from":start,"to":end}:
                updated=old.get('updated_at_utc') or updated
        except (OSError,json.JSONDecodeError):
            pass
    return {
      "schema_version":1,
      "username":USERNAME,
      "source_url":SOURCE_URL,
      "status":"verified",
      "source_verified":True,
      "updated_at_utc":updated,
      "coverage":{"from":start,"to":end},
      "days":days,
      "stats":{"total_contributions":total,"active_days":active_days,"count_coverage":"complete" if total is not None else "partial_or_unavailable"},
      "note":"Counts are sourced from the public GitHub contribution calendar when exposed by its tooltip markup; null means GitHub did not expose a parseable count for that date."
    }


def fetch(url: str, timeout: int=20, retries: int=3) -> str:
    headers={'User-Agent':'karthik-vana-terminal-profile/1.0','Accept':'text/html,application/xhtml+xml'}
    last_error=None
    for attempt in range(retries):
        try:
            req=Request(url,headers=headers)
            with urlopen(req,timeout=timeout) as response:
                status=getattr(response,'status',200)
                if status != 200: raise RuntimeError(f'HTTP status {status}')
                content_type=response.headers.get('Content-Type','').lower()
                body=response.read(3_000_000)
                if not body: raise ValueError('GitHub returned an empty response')
                if len(body)>=3_000_000: raise ValueError('GitHub response exceeded the 3 MB safety limit')
                text=body.decode('utf-8',errors='replace')
                if 'text/html' not in content_type and '<svg' not in text[:5000].lower() and '<rect' not in text[:5000].lower():
                    raise ValueError(f'Unexpected content type: {content_type or "unknown"}')
                if 'sign in' in text[:1500].lower() and 'data-date' not in text:
                    raise ValueError('GitHub returned a sign-in page instead of contribution data')
                return text
        except (HTTPError,URLError,TimeoutError,RuntimeError,ValueError) as exc:
            last_error=exc
            if attempt+1<retries: time.sleep(1.5*(attempt+1))
    raise RuntimeError(f'Unable to retrieve contribution calendar after {retries} attempts: {last_error}')


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=Path('data/contributions.json'))
    p.add_argument('--existing',type=Path,default=Path('data/contributions.json'),help='Existing snapshot used to avoid timestamp-only changes')
    p.add_argument('--url',default=SOURCE_URL,help='Override source endpoint for controlled testing')
    p.add_argument('--timeout',type=int,default=20)
    p.add_argument('--retries',type=int,default=3)
    p.add_argument('--input-html',type=Path,help='Parse a saved HTML fixture instead of making a network request')
    args=p.parse_args()
    try:
        if args.timeout<1 or args.retries<1: raise ValueError('timeout and retries must be positive')
        response=args.input_html.read_text(encoding='utf-8') if args.input_html else fetch(args.url,args.timeout,args.retries)
        days=parse_calendar(response)
        payload=make_payload(days,args.existing)
        # Atomic write: never truncate the prior valid file if serialization/write fails.
        args.output.parent.mkdir(parents=True,exist_ok=True)
        temp=args.output.with_suffix(args.output.suffix+'.tmp')
        temp.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        temp.replace(args.output)
        print(f"Validated {len(days)} dates: {payload['coverage']['from']} to {payload['coverage']['to']}")
        if payload['stats']['total_contributions'] is None:
            print('Contribution counts were not fully exposed; total remains null (not estimated).')
        else:
            print(f"Count total: {payload['stats']['total_contributions']} (from public calendar data)")
        print(f'Wrote verified snapshot: {args.output}')
    except (OSError,ValueError,RuntimeError) as exc:
        p.exit(2,f'error: {exc}\nExisting data is untouched.\n')
    return 0
if __name__=='__main__': raise SystemExit(main())
