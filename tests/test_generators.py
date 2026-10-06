"""Small standard-library checks for generated SVG and contribution safety."""
from __future__ import annotations
import json, tempfile, unittest
from datetime import date, timedelta
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from render_heatmap_svg import render
from fetch_contributions import parse_calendar
from make_info_card import render_card

class GeneratorTests(unittest.TestCase):
    def test_sample_heatmap_does_not_claim_totals(self):
        sample=json.loads((ROOT/'data/contributions.json').read_text(encoding='utf-8'))
        svg=render(sample)
        self.assertIn('LIVE DATA PENDING FIRST SUCCESSFUL REFRESH',svg)
        self.assertNotIn('0 contributions',svg)

    def test_verified_heatmap_has_7_by_53_cells(self):
        start=date(2025,10,5) # Sunday
        days=[]
        for i in range(371):
            d=start+timedelta(days=i)
            days.append({'date':d.isoformat(),'count':0,'level':0})
        payload={'username':'karthik-vana','status':'verified','source_verified':True,'coverage':{'from':days[0]['date'],'to':days[-1]['date']},'days':days,'stats':{'total_contributions':0,'active_days':0},'updated_at_utc':'2025-10-05T00:00:00Z'}
        svg=render(payload)
        self.assertEqual(svg.count('class="contribution-cell"'),53*7) # exactly 53 week columns × 7 weekdays
        self.assertIn('0 contributions',svg)

    def test_info_card_is_valid_svg(self):
        data=json.loads((ROOT/'data/profile.json').read_text(encoding='utf-8'))
        import xml.etree.ElementTree as ET
        ET.fromstring(render_card(data,animated=True))
        ET.fromstring(render_card(data,animated=False))

    def test_parser_rejects_empty_or_incomplete_markup(self):
        with self.assertRaises(ValueError): parse_calendar('<html><body>not a contributions calendar</body></html>')

    def test_parser_matches_tooltip_counts_to_the_correct_date(self):
        import html
        start=date(2025,10,1)
        cells=[]
        for i in range(260):
            day=start+timedelta(days=i)
            count=i % 6
            label=('No contributions' if count == 0 else f'{count} contribution' + ('s' if count != 1 else '')) + f' on {day.strftime("%B")} {day.day}, {day.year}'
            cells.append(f'<g><title>{html.escape(label)}</title><rect data-date="{day.isoformat()}" data-level="{min(4,count)}" /></g>')
        parsed=parse_calendar('<svg>' + ''.join(cells) + '</svg>')
        self.assertEqual(len(parsed),260)
        self.assertEqual(parsed[0]['count'],0)
        self.assertEqual(parsed[1]['count'],1)
        self.assertEqual(parsed[2]['count'],2)

if __name__=='__main__': unittest.main(verbosity=2)
