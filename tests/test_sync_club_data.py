import sys
import unittest
from datetime import date
from pathlib import Path
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sync_club_data as sync

class SyncParserTests(unittest.TestCase):
    def test_time_and_date_are_not_scores(self):
        self.assertEqual(sync.explicit_match_score('17:0025-10 Vilamajor, C.F. A Sabadellenca, UE. B'), '')
        self.assertEqual(sync.explicit_match_score('19:3019-12 Les Franqueses, C.F. B Vilamajor, C.F. A'), '')

    def test_compact_final_scores(self):
        self.assertEqual(sync.explicit_match_score('FT27-09 Vilamajor, C.F. A Mollet U.E., CF. A 12'), '1-2')
        self.assertEqual(sync.explicit_match_score('FT11-10 Vilamajor, C.F. A Cardedeu, F.C. A 23'), '2-3')
        self.assertEqual(sync.explicit_match_score('FT 01-11 Vilamajor, C.F. A Rival 2-1'), '2-1')

    def test_season_year_rollover(self):
        today = date(2026, 10, 23)
        self.assertEqual(sync.iso_date('25-10', today), '2026-10-25')
        self.assertEqual(sync.iso_date('10-01', today), '2027-01-10')
        self.assertEqual(sync.iso_date('23-01', today), '2027-01-23')

    def test_standings_reject_player_table_and_accept_team_table(self):
        html = '''<table><tr><th>Jugador</th><th>PJ</th><th>Goles</th></tr><tr><td>Jugador A</td><td>2</td><td>1</td></tr></table>
        <table><tr><th>Pos</th><th>Equipo</th><th>PJ</th><th>DG</th><th>Pts</th></tr>
        <tr><td>12</td><td><a href="/team/vilamajor">Vilamajor, C.F. A</a></td><td>2</td><td>-2</td><td>0</td></tr>
        <tr><td>1</td><td><a href="/team/other">Otro C.F. A</a></td><td>3</td><td>5</td><td>7</td></tr></table>'''
        rows = sync.extract_standings(BeautifulSoup(html, 'html.parser'))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['team'], 'Vilamajor, C.F. A')
        self.assertEqual(rows[0]['position'], 12)
        self.assertEqual(rows[0]['stats'][0], 2)
        self.assertEqual(rows[0]['stats'][6], -2)
        self.assertEqual(rows[0]['stats'][7], 0)

    def test_squad_deduplicates_and_omits_zero_appearances(self):
        html = '''<table><tr><th>Jugador</th><th>PJ</th><th>Goles</th></tr>
        <tr><td><a href="/player/1">Jugador A</a></td><td>2</td><td>1</td></tr>
        <tr><td><a href="/player/2">Jugador B</a></td><td>0</td><td>0</td></tr>
        <tr><td><a href="/player/3">Jugador A</a></td><td>1</td><td>0</td></tr></table>'''
        rows = sync.extract_squad(BeautifulSoup(html, 'html.parser'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], 'Jugador A')
        self.assertEqual(rows[0]['appearances'], 2)

if __name__ == '__main__':
    unittest.main()
