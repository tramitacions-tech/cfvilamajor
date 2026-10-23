import sys
import unittest
from datetime import date
from pathlib import Path
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sync_club_data as sync

class SyncParserTests(unittest.TestCase):
    def test_current_el_futbol_match_row_extracts_final_scores_and_pending_fixtures(self):
        # Mirrors the live site's a[data-match-row] markup: team names and goals
        # are nested spans, while FT/date are in the first child block.
        html = '''
        <a data-match-row="true" href="/es/match/2627/comp/group/vilamajor-vs-mollet/j1">
          <div><span>FT</span><span>27-09-2026</span></div>
          <div><span class="truncate">Vilamajor, C.F. A</span><span class="truncate">Mollet U.E., CF. A</span></div>
          <div><span>1</span><span>2</span></div>
        </a>
        <a data-match-row="true" href="/es/match/2627/comp/group/vilamajor-vs-cardedeu/j3">
          <div><span>FT</span><span>11-10-2026</span></div>
          <div><span class="truncate">Vilamajor, C.F. A</span><span class="truncate">Cardedeu, F.C. A</span></div>
          <div><span>2</span><span>3</span></div>
        </a>
        <a data-match-row="true" href="/es/match/2627/comp/group/granollers-vs-vilamajor/j2">
          <div><span>16:00</span><span>04-10-2026</span></div>
          <div><span class="truncate">Granollers, E.C. B</span><span class="truncate">Vilamajor C.F. A</span></div>
          <div><span>chevron_right</span></div>
        </a>
        <a data-match-row="true" href="/es/match/2627/comp/group/canovelles-vs-vilamajor/j4">
          <div><span>14:00</span><span>25-10-2026</span></div>
          <div><span class="truncate">Canovelles, U.E. A</span><span class="truncate">Vilamajor C.F. A</span></div>
          <div><span>chevron_right</span></div>
        </a>
        <a data-match-row="true" href="/es/match/2627/comp/group/vilamajor-vs-sabadellenca/j5">
          <div><span>17:00</span><span>01-11-2026</span></div>
          <div><span class="truncate">Vilamajor, C.F. A</span><span class="truncate">Sabadellenca, UE. B</span></div>
          <div><span>chevron_right</span></div>
        </a>
        <a data-match-row="true" href="/es/match/2627/comp/group/rival-vs-vilamajor/j6">
          <div><span>FT</span><span>08-11-2026</span></div>
          <div><span class="truncate">Rival, C.F. A</span><span class="truncate">Vilamajor, C.F. A</span></div>
          <div><span>3</span><span>1</span></div>
        </a>'''
        rows = sync.extract_matches(BeautifulSoup(html, 'html.parser'), 'https://elfutbol.co/es/team/vilamajor-cf-a', {'name':'Vilamajor, C.F. A'})
        self.assertEqual(len(rows), 6)
        first = next(m for m in rows if m['iso'] == '2026-09-27')
        second = next(m for m in rows if m['iso'] == '2026-10-11')
        away = next(m for m in rows if m['iso'] == '2026-11-08')
        pending = [m for m in rows if not m.get('score')]
        self.assertEqual((first['home'], first['away'], first['score']), ('Vilamajor, C.F. A', 'Mollet U.E., CF. A', '1-2'))
        self.assertEqual((second['home'], second['away'], second['score']), ('Vilamajor, C.F. A', 'Cardedeu, F.C. A', '2-3'))
        self.assertEqual((away['home'], away['away'], away['score']), ('Rival, C.F. A', 'Vilamajor, C.F. A', '3-1'))
        self.assertEqual(len(pending), 3)
        self.assertTrue(any('granollers' in (m['home']+' '+m['away']).lower() for m in pending))
        self.assertTrue(any('canovelles' in (m['home']+' '+m['away']).lower() for m in pending))
        self.assertTrue(any('sabadellenca' in (m['home']+' '+m['away']).lower() for m in pending))

    def test_real_row_numeric_date_and_kickoff_never_become_score(self):
        html = '''<a data-match-row="true"><div><span>17:00</span><span>25-10-2026</span></div>
        <div><span class="truncate">Canovelles, U.E. A</span><span class="truncate">Vilamajor, C.F. A</span></div>
        <div><span class="material-symbols-outlined">chevron_right</span></div></a>'''
        rows = sync.extract_matches(BeautifulSoup(html, 'html.parser'), 'https://elfutbol.co/es/team/vilamajor-cf-a', {'name':'Vilamajor, C.F. A'})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['time'], '17:00')
        self.assertNotIn('score', rows[0])

    def test_source_final_count_detects_ft_rows_without_requiring_guessed_goals(self):
        html = '''<a data-match-row="true"><div><span>FT</span><span>27-09-2026</span></div></a>
        <a data-match-row="true"><div><span>16:00</span><span>25-10-2026</span></div></a>'''
        self.assertEqual(sync.source_final_rows(BeautifulSoup(html, 'html.parser')), 1)

    def test_time_and_date_are_not_scores(self):
        self.assertEqual(sync.explicit_match_score('17:0025-10 Vilamajor, C.F. A Sabadellenca, UE. B'), '')
        self.assertEqual(sync.explicit_match_score('19:3019-12 Les Franqueses, C.F. B Vilamajor, C.F. A'), '')

    def test_compact_final_scores(self):
        self.assertEqual(sync.explicit_match_score('FT27-09 Vilamajor, C.F. A Mollet U.E., CF. A 12'), '1-2')
        self.assertEqual(sync.explicit_match_score('FT11-10 Vilamajor, C.F. A Cardedeu, F.C. A 23'), '2-3')
        self.assertEqual(sync.explicit_match_score('FT 01-11 Vilamajor, C.F. A Rival 2-1'), '2-1')

    def test_isolated_past_score_cell_is_recognized(self):
        self.assertEqual(sync.score_from_cells(['27-09', 'Vilamajor, C.F. A', '2-1', 'Mollet U.E., CF. A'], '2026-09-27', date(2026,10,23)), '2-1')
        self.assertEqual(sync.score_from_cells(['25-10', 'Vilamajor, C.F. A', '2-1', 'Rival C.F. A'], '2026-10-25', date(2026,10,23)), '')
        self.assertEqual(sync.score_from_cells(['27-09', 'Vilamajor, C.F. A', '17:00', 'Mollet U.E., CF. A'], '2026-09-27', date(2026,10,23)), '')

    def test_season_year_rollover(self):
        today = date(2026, 10, 23)
        self.assertEqual(sync.iso_date('25-10', today), '2026-10-25')
        self.assertEqual(sync.iso_date('10-01', today), '2027-01-10')
        self.assertEqual(sync.iso_date('23-01', today), '2027-01-23')

    def test_match_row_separate_score_cell_becomes_result(self):
        html = '''<table><tr><td>27-09-2026</td><td><a href="/team/vilamajor">Vilamajor, C.F. A</a></td><td>2-1</td><td><a href="/team/mollet">Mollet U.E., CF. A</a></td></tr></table>'''
        rows = sync.extract_matches(BeautifulSoup(html, 'html.parser'), 'https://elfutbol.co/es/team/vilamajor-c-f-a', {'name':'Vilamajor, C.F. A'})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get('score'), '2-1')
        self.assertEqual(rows[0]['home'], 'Vilamajor, C.F. A')
        self.assertEqual(rows[0]['away'], 'Mollet U.E., CF. A')

    def test_match_cards_with_date_outside_team_links(self):
        html = '''<div class="match-card"><span>Última derrota</span><span>FT27-09 Vilamajor, C.F. A Mollet U.E., CF. A 12</span>
        <a href="/es/team/vilamajor-c-f-a">Vilamajor, C.F. A</a>
        <a href="/es/team/mollet-u-e-cf-a">Mollet U.E., CF. A</a></div>
        <div class="match-card"><span>Jornada 2 VS 2026-10-03 · 20:00</span>
        <a href="/es/team/granollers">Granollers, E.C. B</a>
        <a href="/es/team/vilamajor-c-f-a">Vilamajor, C.F. A</a></div>'''
        rows = sync.extract_matches(BeautifulSoup(html, 'html.parser'), 'https://elfutbol.co/es/team/vilamajor-c-f-a', {'name':'Vilamajor, C.F. A'})
        self.assertEqual(len(rows), 2)
        past = next(x for x in rows if x['iso'] == '2026-09-27')
        self.assertEqual((past['home'], past['away']), ('Vilamajor, C.F. A', 'Mollet U.E., CF. A'))
        self.assertEqual(past.get('score'), '1-2')
        upcoming = next(x for x in rows if x['iso'] == '2026-10-03')
        self.assertEqual((upcoming['home'], upcoming['away']), ('Granollers, E.C. B', 'Vilamajor, C.F. A'))
        self.assertNotIn('score', upcoming)

    def test_standings_reject_player_table_and_accept_team_table(self):
        html = '''<table><tr><th>Jugador</th><th>PJ</th><th>Goles</th></tr><tr><td>Jugador A</td><td>2</td><td>1</td></tr></table>
        <table><tr><th>Pos</th><th>Equipo</th><th>PJ</th><th>DG</th><th>Pts</th></tr>
        <tr><td>12</td><td><a href="/team/vilamajor">Vilamajor, C.F. A</a></td><td>2</td><td>0</td><td>0</td><td>2</td><td>3</td><td>5</td><td>-2</td><td>0</td></tr>
        <tr><td>1</td><td><a href="/team/other">Otro C.F. A</a></td><td>3</td><td>2</td><td>1</td><td>0</td><td>5</td><td>1</td><td>+4</td><td>7</td></tr></table>'''
        rows = sync.extract_standings(BeautifulSoup(html, 'html.parser'))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['team'], 'Vilamajor, C.F. A')
        self.assertEqual(rows[0]['position'], 12)
        self.assertEqual(rows[0]['stats'], [2, 0, 0, 2, 3, 5, -2, 0])

    def test_standings_map_all_win_draw_loss_and_goal_columns(self):
        html = '''<table><tr><th>Pos</th><th>Equipo</th><th>PJ</th><th>PG</th><th>PE</th><th>PP</th><th>GF</th><th>GC</th><th>DG</th><th>Pts</th></tr>
        <tr><td>4</td><td>Vilamajor C.F. A</td><td>4</td><td>2</td><td>1</td><td>1</td><td>8</td><td>6</td><td>+2</td><td>7</td></tr></table>'''
        rows = sync.extract_standings(BeautifulSoup(html, 'html.parser'))
        self.assertEqual(rows[0]['stats'], [4, 2, 1, 1, 8, 6, 2, 7])

    def test_merge_matches_retains_one_scored_fixture_across_tabs(self):
        base = {'iso':'2026-09-27','home':'Vilamajor C.F. A','away':'Mollet U.E. A','source':'page'}
        merged = sync.merge_matches([{**base}, {**base,'score':'1-2','result':'1-2'}])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['score'], '1-2')

    def test_empty_refresh_cannot_erase_last_valid_results(self):
        previous = [{'iso':'2026-09-27','score':'1-2'}]
        self.assertTrue(sync.result_regression(previous, [{'iso':'2026-10-25'}]))
        self.assertFalse(sync.result_regression([], [{'iso':'2026-10-25'}]))
        self.assertFalse(sync.result_regression(previous, [{'iso':'2026-10-25','score':'0-0'}]))

    def test_squad_deduplicates_and_omits_zero_appearances(self):
        html = '''<table><tr><th>Jugador</th><th>PJ</th><th>Goles</th></tr>
        <tr><td><a href="/player/1">Jugador A</a></td><td>2</td><td>1</td></tr>
        <tr><td><a href="/player/2">Jugador B</a></td><td>0</td><td>0</td></tr>
        <tr><td><a href="/player/3">Jugador A</a></td><td>1</td><td>0</td></tr></table>'''
        rows = sync.extract_squad(BeautifulSoup(html, 'html.parser'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], 'Jugador A')
        self.assertEqual(rows[0]['appearances'], 2)

    def test_related_pages_requests_all_tabs_preserving_competition(self):
        class Response:
            status_code = 200
            text = '<html><body><div>Vilamajor C.F. A</div></body></html>'
            url = 'https://elfutbol.co/es/team/vilamajor-c-f-a?comp=juvenil-primera-divisio&group=grup-4&season=2627&tab=calendario'
            def raise_for_status(self): pass
        class Session:
            def __init__(self): self.urls=[]
            def get(self, url, timeout=0):
                self.urls.append(url)
                return Response()
        session=Session()
        base='https://elfutbol.co/es/team/vilamajor-c-f-a?comp=juvenil-primera-divisio&group=grup-4'
        pages=sync.related_pages(session,base,BeautifulSoup('<html><body>Vilamajor</body></html>','html.parser'))
        self.assertTrue(any('tab=calendario' in u for u in session.urls))
        self.assertTrue(any('tab=resultados' in u for u in session.urls))
        self.assertTrue(any('tab=clasificacion' in u for u in session.urls))
        self.assertTrue(any('tab=plantilla' in u for u in session.urls))
        self.assertTrue(all('comp=juvenil-primera-divisio' in u and 'group=grup-4' in u for u in session.urls))

    def test_canonical_redirect_keeps_competition_and_group_context(self):
        url = sync.preserve_competition_context(
            'https://elfutbol.co/es/team/vilamajor-cf-a?comp=juvenil-primera-divisio&group=grup-4&season=2627',
            'https://elfutbol.co/es/team/vilamajor-cf-a')
        self.assertIn('comp=juvenil-primera-divisio', url)
        self.assertIn('group=grup-4', url)
        self.assertIn('season=2627', url)

if __name__ == '__main__':
    unittest.main()
