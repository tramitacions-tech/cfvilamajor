#!/usr/bin/env python3
"""Actualiza calendario, resultados, clasificación y plantilla desde elFutbol.
No sustituye datos por listas vacías si una página cambia o no se puede leer.
"""
import json, os, re, sys, unicodedata
from datetime import datetime, date
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse
import requests
from bs4 import BeautifulSoup

ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH=os.path.join(ROOT,'data','club.json')
HEADERS={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36','Accept-Language':'es-ES,es;q=0.9,ca;q=0.8,en;q=0.7'}
DATE_RE=re.compile(r'(?<!\d)(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?(?!\d)')
TIME_RE=re.compile(r'(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)')
SCORE_RE=re.compile(r'(?<!\d)(\d{1,2})\s*[-–:]\s*(\d{1,2})(?!\d)')

def norm(s):
    s=unicodedata.normalize('NFKD',s or '').encode('ascii','ignore').decode().lower()
    return re.sub(r'\s+',' ',s).strip()

def clean(s): return re.sub(r'\s+',' ',s or '').strip()

def iso_date(txt, today=None):
    today=today or date.today(); m=DATE_RE.search(txt or '')
    if not m: return None
    d,mo,y=m.groups(); d=int(d); mo=int(mo)
    if y:
        y=int(y); y=y+2000 if y<100 else y
    else:
        # La temporada futbolística cruza el año natural (p. ej. 2026-27):
        # entre julio y diciembre se usa el año de inicio; enero-junio, el siguiente.
        if today.month >= 7:
            y = today.year if mo >= 7 else today.year + 1
        else:
            y = today.year - 1 if mo >= 7 else today.year
        try:
            date(y,mo,d)
        except ValueError: return None
    try: return date(y,mo,d).isoformat()
    except ValueError: return None

def page_variants(url):
    # Ask explicitly for the current season; preserve competition/group params when accepted.
    out=[url]
    try:
        p=urlparse(url); q=parse_qs(p.query)
        q.pop('season',None); q['season']=['2627']
        out.append(urlunparse((p.scheme,p.netloc,p.path,p.params,urlencode(q,doseq=True),p.fragment)))
    except Exception: pass
    return list(dict.fromkeys(out))

def get_page(session,url):
    errors=[]
    for candidate in page_variants(url):
        try:
            r=session.get(candidate,timeout=15); r.raise_for_status()
            if len(r.text)>1000 and ('elfutbol' in r.url or 'vilamajor' in r.text.lower()): return r.url,r.text
            errors.append('respuesta sin contenido útil')
        except Exception as e: errors.append(str(e))
    raise RuntimeError('; '.join(errors) or 'no se pudo descargar la página')

def row_cells(row): return [clean(x.get_text(' ',strip=True)) for x in row.find_all(['td','th'],recursive=False)]

def score_from_cells(cells, match_date=None, today=None):
    """Accept a score only when it is an isolated cell in a dated fixture row.
    This handles sources that render home, score, away in separate columns without FT.
    """
    today=today or date.today()
    if not match_date:
        return ''
    try:
        played=date.fromisoformat(match_date) < today
    except ValueError:
        return ''
    if not played:
        return ''
    for cell in cells:
        m=re.fullmatch(r'\s*(\d{1,2})\s*[-–]\s*(\d{1,2})\s*', cell)
        if m and int(m.group(1))<=20 and int(m.group(2))<=20:
            return f'{int(m.group(1))}-{int(m.group(2))}'
    return ''

def split_match_label(full, team):
    """Read elFutbol's compact single-link match labels, e.g. FT30-11 Rival A Vilamajor, C.F. A 21."""
    raw=clean(full)
    score=''
    # Only strip/decode a compact score when the source explicitly marks the match final.
    final_marked=bool(re.search(r'(?:\bFT(?=\d)|\bFT\b|\b(?:FINALIZADO|FINALITZAT)\b)',raw,re.I))
    tail=re.search(r'\s(\d{2,3})\s*$',raw) if final_marked else None
    if tail:
        digits=tail.group(1)
        if len(digits)==2: score=f'{digits[0]}-{digits[1]}'; raw=raw[:tail.start()].strip()
        elif len(digits)==3 and int(digits[:2])<=20 and int(digits[2])<=20: score=f'{digits[:2]}-{digits[2]}'; raw=raw[:tail.start()].strip()
    raw=re.sub(r'\b(?:FT|HT|FINALIZADO|FINALITZAT|APLAZADO|SUSPENDIDO|PENDIENTE)\b',' ',raw,flags=re.I)
    raw=DATE_RE.sub(' ',raw); raw=TIME_RE.sub(' ',raw)
    # Team-specific exact name, with broad fallback for the club's standard names.
    own=''
    if team:
        name=team.get('name','')
        m=re.search(r'Vilamajor,?\s*C\.?F\.?\s*[AB]?',name,re.I)
        if m: own=m.group(0)
    patterns=[]
    if own: patterns.append(re.escape(own))
    patterns += [r'Vilamajor,?\s*C\.?F\.?\s*[AB]?',r'Vilamajor\s+C\.F\.\s*[AB]?']
    m=None
    for pat in patterns:
        m=re.search(pat,raw,re.I)
        if m: break
    if not m: return None
    left=clean(raw[:m.start()].strip(' -–—|'))
    right=clean(raw[m.end():].strip(' -–—|'))
    # Drop round markers or leftover digits from the ends.
    left=re.sub(r'^(?:Jornada\s*\d+\s*)','',left,flags=re.I)
    right=re.sub(r'^(?:Jornada\s*\d+\s*)','',right,flags=re.I)
    if left and right: return (left, m.group(0), score) if False else (left, m.group(0), score, 'club-away')
    if right: return (m.group(0), right, score, 'club-home')
    return None

def related_pages(session, base_url, soup):
    """Load all sport tabs for the exact team/competition, even when tabs are JS-driven.
    elFutbol often keeps calendar/results/standings/squad as a query parameter and the
    current page only exposes the active tab in its HTML. Do not rely on visible nav links.
    """
    pages=[(base_url,soup)]
    seen={base_url}
    wanted={'partidos','partits','calendario','calendari','clasificacion','classificacio','plantilla','squad'}
    for a in soup.find_all('a',href=True):
        label=norm(a.get_text(' ',strip=True))
        if label not in wanted: continue
        url=urljoin(base_url,a['href'])
        if url in seen or urlparse(url).netloc not in ('elfutbol.co','www.elfutbol.co'): continue
        seen.add(url)
        try:
            r=session.get(url,timeout=12); r.raise_for_status()
            if len(r.text)>1000: pages.append((r.url,BeautifulSoup(r.text,'html.parser')))
        except Exception as e: print(f'AVISO subpágina {label}: {e}', flush=True)

    # Explicitly request each tab while preserving comp/group/team identifiers.
    # Try both common Spanish/Catalan labels and stable internal tab keys.
    tab_values=('calendario','calendari','partidos','partits','resultados','resultats',
                'clasificacion','classificacio','plantilla','squad')
    try:
        parsed=urlparse(base_url)
        base_q=parse_qs(parsed.query)
        for tab in tab_values:
            q={k:list(v) for k,v in base_q.items()}
            q['season']=['2627']
            q['tab']=[tab]
            candidate=urlunparse((parsed.scheme,parsed.netloc,parsed.path,parsed.params,
                                  urlencode(q,doseq=True),parsed.fragment))
            if candidate in seen: continue
            seen.add(candidate)
            try:
                r=session.get(candidate,timeout=12); r.raise_for_status()
                if len(r.text)>1000:
                    # Keep a page only if it looks like the requested team page.
                    page_soup=BeautifulSoup(r.text,'html.parser')
                    page_text=norm(page_soup.get_text(' ',strip=True))
                    if 'vilamajor' in page_text:
                        pages.append((r.url,page_soup))
            except Exception as e:
                print(f'AVISO pestanya {tab}: {e}', flush=True)
    except Exception as e:
        print(f'AVISO variants de pestanyes: {e}', flush=True)
    # A URL can be returned by redirects under a different query string; deduplicate final pages.
    unique=[]; final_seen=set()
    for url,page in pages:
        key=(urlparse(url).path,urlparse(url).query)
        if key not in final_seen:
            final_seen.add(key); unique.append((url,page))
    return unique

def explicit_match_score(text):
    """Only parse compact scores when the source explicitly marks the match FT/final.
    Never interpret a kickoff time such as 17:00 or a date such as 19-12 as a score.
    elFutbol sometimes appends a compact score (e.g. FT11-10 ... 23 = 2-3).
    """
    raw=clean(text)
    if not re.search(r'(?:\bFT(?=\d)|\bFT\b|\b(?:FINALIZADO|FINALITZAT)\b)', raw, re.I):
        return ''
    # A separated score is accepted only as the final token, never the date after FT.
    explicit=re.search(r'\s(\d{1,2})\s*[-–:]\s*(\d{1,2})\s*$', raw)
    if explicit:
        a,b=int(explicit.group(1)),int(explicit.group(2))
        if a <= 20 and b <= 20: return f'{a}-{b}'
    # Compact score suffix used by the source: 12 => 1-2; 203 => 20-3.
    tail=re.search(r'\s(\d{2,3})\s*$', raw)
    if not tail: return ''
    digits=tail.group(1)
    if len(digits)==2:
        a,b=int(digits[0]),int(digits[1])
    else:
        a,b=int(digits[:2]),int(digits[2])
    if a <= 20 and b <= 20:
        return f'{a}-{b}'
    return ''

def extract_matches(soup, source_url, team=None):
    found=[]; seen=set()
    # Prefer explicit match links; elFutbol exposes a linked label containing date, teams and sometimes score.
    for a in soup.find_all('a',href=True):
        href=a['href']; txt=clean(a.get_text(' ',strip=True))
        if not txt or not re.search(r'\d{1,2}[-/]\d{1,2}',txt): continue
        if not any(k in norm(href+' '+txt) for k in ['partid','match','jornada','fixture','calendar','result','game','/match/','/partit']):
            # Team pages frequently link the match itself without a descriptive URL; require date + two club names.
            if not ('vilamajor' in norm(txt) and len(txt)>18): continue
        iso=iso_date(txt)
        if not iso: continue
        # Parse from structured row first; fallback to the link's visible text.
        row=a.find_parent(['tr','li','article'])
        full=clean(row.get_text(' ',strip=True)) if row else txt
        names=[]
        if row:
            for link in row.find_all('a',href=True):
                label=clean(link.get_text(' ',strip=True))
                if label and label not in names and not re.search(r'\d{1,2}[-/]\d{1,2}',label) and len(label)>2:
                    if norm(label) not in {'ver','detalles','detall','resultats','resultados','calendari','calendario'}: names.append(label)
        # Remove common date/time/status fragments from link label before a conservative text fallback.
        score=explicit_match_score(full) or explicit_match_score(txt)
        compact=split_match_label(full,team)
        if len(names)<2 and compact:
            left, own, compact_score, orientation=compact
            # Match order follows the source label: opponent before club means opponent is home.
            if orientation=='club-away': names=[left,own]
            else:
                right=clean(full[full.lower().find(own.lower())+len(own):])
                right=DATE_RE.sub(' ',right); right=TIME_RE.sub(' ',right)
                right=re.sub(r'\s+\d{2,3}\s*$','',right).strip()
                names=[own,right]
            if compact_score and not score:
                score=compact_score
        if len(names)<2:
            cleaned=re.sub(r'\b(FT|HT|FINALIZADO|FINALITZAT|APLAZADO|SUSPENDIDO)\b',' ',full,flags=re.I)
            cleaned=DATE_RE.sub(' ',cleaned); cleaned=TIME_RE.sub(' ',cleaned)
            # No generic score regex here: it can mistake dates/times for goals.
            if score:
                cleaned=re.sub(r'\s+\d{2,3}\s*$', ' ', cleaned)
            parts=re.split(r'\s{2,}|\s+[–—]\s+|\s+vs\.?\s+',cleaned)
            parts=[clean(x) for x in parts if len(clean(x))>2 and not re.fullmatch(r'\d+',clean(x))]
            club=[x for x in parts if any(w in norm(x) for w in ['vilamajor','c.f.','f.c.','u.d.','c.e.','club futbol','futbol club'])]
            if len(club)>=2: names=club[:2]
        if len(names)<2: continue
        # Score is only accepted when it is visibly separated; don't guess compact strings like "21".
        # A generic N-N match over the whole label is unsafe: it catches DD-MM and HH:MM.
        score = score or explicit_match_score(full) or explicit_match_score(txt)
        time=''; tm=TIME_RE.search(full)
        if tm: time=f'{int(tm.group(1)):02d}:{tm.group(2)}'
        home,away=names[0],names[1]
        # Ignore links that are not a match involving the team of this club.
        if not ('vilamajor' in norm(home+' '+away)): continue
        key=(iso,norm(home),norm(away),score)
        if key in seen: continue
        seen.add(key)
        item={'date':date.fromisoformat(iso).strftime('%d-%m'),'iso':iso,'time':time,'home':home,'away':away,'venue':None,'source':source_url}
        if score: item.update(score=score,result=score)
        found.append(item)
    # Structured table rows are often more reliable than linked labels.
    for row in soup.find_all('tr'):
        cells=row_cells(row); text=' | '.join(cells)
        iso=iso_date(text)
        if not iso or len(cells)<3: continue
        links=[clean(a.get_text(' ',strip=True)) for a in row.find_all('a',href=True)]
        links=[x for x in links if len(x)>2 and not re.search(r'^\d{1,2}[-/]\d{1,2}$',x)]
        if len(links)<2: continue
        club=[x for x in links if 'vilamajor' in norm(x)]
        if not club: continue
        # Prefer the first two club/team links in the fixture row.
        names=[]
        for x in links:
            if x not in names: names.append(x)
        if len(names)<2: continue
        score=explicit_match_score(text) or score_from_cells(cells, iso)
        tm=TIME_RE.search(text); time=f'{int(tm.group(1)):02d}:{tm.group(2)}' if tm else ''
        item={'date':date.fromisoformat(iso).strftime('%d-%m'),'iso':iso,'time':time,'home':names[0],'away':names[1],'venue':None,'source':source_url}
        if score: item.update(score=score,result=score)
        key=(iso,norm(item['home']),norm(item['away']),score)
        if key not in seen: seen.add(key); found.append(item)
    return sorted(found,key=lambda x:(x['iso'],x.get('time','')))

def extract_standings(soup):
    """Extract a league table only when its headers identify team standings.
    Reject player-stat tables even if they contain PJ/points-like columns.
    """
    candidates=[]
    for table in soup.find_all('table'):
        header_cells=table.find_all('th')
        headers=[norm(x.get_text(' ',strip=True)) for x in header_cells]
        if not headers: continue
        # Must look like a standings table: position + team + played + points.
        pos_i=next((i for i,h in enumerate(headers) if h in {'pos','pos.','posicion','posición','class','posicio'}),None)
        team_i=next((i for i,h in enumerate(headers) if h in {'equipo','equip','club','team'}),None)
        pj_i=next((i for i,h in enumerate(headers) if h in {'pj','partidos','partits','jugados'}),None)
        pts_i=next((i for i,h in enumerate(headers) if h in {'pts','puntos','punts'}),None)
        # Compact elFutbol table has Pos, Equipo, PJ, DG, Pts.
        if pos_i is None or team_i is None or pj_i is None or pts_i is None: continue
        rows=[]
        for tr in table.find_all('tr'):
            cells=row_cells(tr)
            if tr.find('th') or len(cells)<len(headers): continue
            name=''
            if team_i < len(cells):
                links=[clean(a.get_text(' ',strip=True)) for a in tr.find_all('a',href=True)]
                name=next((x for x in links if len(x)>2),cells[team_i])
            if not name or re.fullmatch(r'[-+]?\d+',name): continue
            vals=[]
            for c in cells:
                m=re.fullmatch(r'[-+]?\d+',c)
                vals.append(int(m.group()) if m else None)
            def num_at(i):
                if i is None or i>=len(vals): return None
                return vals[i]
            # Normalize to the eight columns the site renders: PJ, PG, PE, PP, GF, GC, DG, Pts.
            indices={
                'played': next((i for i,h in enumerate(headers) if h in {'pj','partidos','partits','jugados'}),None),
                'won': next((i for i,h in enumerate(headers) if h in {'pg','ganados','victorias','guanyats'}),None),
                'drawn': next((i for i,h in enumerate(headers) if h in {'pe','empatados','empates','empatats'}),None),
                'lost': next((i for i,h in enumerate(headers) if h in {'pp','perdidos','derrotas','perduts'}),None),
                'gf': next((i for i,h in enumerate(headers) if h in {'gf','goles a favor','gols a favor'}),None),
                'ga': next((i for i,h in enumerate(headers) if h in {'gc','goles en contra','gols en contra'}),None),
                'gd': next((i for i,h in enumerate(headers) if h in {'dg','dif','diferencia','diferencia de goles'}),None),
                'points': next((i for i,h in enumerate(headers) if h in {'pts','puntos','punts'}),None),
            }
            stats=[num_at(indices[k]) for k in ('played','won','drawn','lost','gf','ga','gd','points')]
            position=num_at(pos_i)
            # Reject malformed/player-stat rows and zero-position placeholders.
            if position is None or position < 1 or num_at(pj_i) is None or num_at(pts_i) is None:
                continue
            rows.append({'team':name,'position':position,'numbers':[position]+stats,'stats':stats,'cells':cells})
        if rows:
            # A valid league table should contain several club/team rows, not individual players.
            candidates.append(rows)
    return max(candidates,key=len) if candidates else []

def extract_squad(soup):
    """Use only players with an appearance in the current group; deduplicate names.
    elFutbol's squad page includes historical/zero-appearance profiles, so the full
    profile count is not equivalent to the current active squad.
    """
    candidates=[]
    for table in soup.find_all('table'):
        headers=[norm(x.get_text(' ',strip=True)) for x in table.find_all('th')]
        blob=' '.join(headers)
        player_i=next((i for i,h in enumerate(headers) if h in {'jugador','players','jugadors','nombre','nom','player'}),None)
        pj_i=next((i for i,h in enumerate(headers) if h in {'pj','partidos','partits','apariciones'}),None)
        if player_i is None or pj_i is None: continue
        rows=[]
        for tr in table.find_all('tr'):
            if tr.find('th'): continue
            cells=row_cells(tr)
            if len(cells)<=max(player_i,pj_i): continue
            links=[clean(a.get_text(' ',strip=True)) for a in tr.find_all('a',href=True)]
            name=next((x for x in links if len(x)>2 and not re.search(r'\d{1,2}[-/]\d{1,2}',x)),cells[player_i])
            try: appearances=int(cells[pj_i])
            except (ValueError,IndexError): continue
            if appearances <= 0 or len(name)<3: continue
            if norm(name) in {'jugador','players','jugadors','nombre','nom'}: continue
            rows.append({'name':name,'appearances':appearances,'stats':cells[1:]})
        if rows: candidates.append(rows)
    if not candidates: return []
    # Prefer the largest active-player table, but keep one row per normalized name.
    source=max(candidates,key=len); dedup={}
    for row in source:
        key=norm(row['name'])
        if key not in dedup or row.get('appearances',0)>dedup[key].get('appearances',0): dedup[key]=row
    return list(dedup.values())

def extract_standing_summary(soup):
    """Read summary figures only from a tightly matched label/value pair."""
    text=clean(soup.get_text(' ',strip=True))
    out={}
    patterns=[
        ('position',r'(\d{1,2})\s*(?:°|º|ª)\s*POSICI[ÓO]N'),
        ('played',r'(\d{1,2})\s*PARTIDOS'),
        ('points',r'(\d{1,3})\s*PUNTOS'),
    ]
    for key,pat in patterns:
        m=re.search(pat,text,re.I)
        if m: out[key]=int(m.group(1))
    return out

def main():
    if not os.path.exists(DATA_PATH): raise SystemExit('Falta data/club.json')
    with open(DATA_PATH,encoding='utf-8') as f: payload=json.load(f)
    teams=payload.get('teams',[])
    if not teams: raise SystemExit('data/club.json no contiene equipos')
    session=requests.Session(); session.headers.update(HEADERS)
    today=date.today().isoformat(); success=0; failures=[]
    for team in teams:
        url=team.get('url')
        if not url: failures.append(team.get('_display') or team.get('name') or '?'); continue
        label=team.get('_display') or team.get('name') or url
        print(f'INICIO {label}: consultando fuente deportiva', flush=True)
        try:
            final_url,html=get_page(session,url); soup=BeautifulSoup(html,'html.parser')
            pages=related_pages(session,final_url,soup)
            matches=[]; standings=[]; squad=[]; summary={}
            for page_url,page_soup in pages:
                matches.extend(extract_matches(page_soup,page_url,team))
                found_standings=extract_standings(page_soup)
                if len(found_standings)>len(standings): standings=found_standings
                found_squad=extract_squad(page_soup)
                if len(found_squad)>len(squad): squad=found_squad
                summary.update(extract_standing_summary(page_soup))
            # Deduplicate fixtures collected from multiple tabs.
            unique={ (m.get('iso'),norm(m.get('home')),norm(m.get('away')),m.get('score','')):m for m in matches }
            matches=sorted(unique.values(),key=lambda x:(x.get('iso',''),x.get('time','')))
            # Save each data domain only when a real payload is found; don't erase prior values on parsing failures.
            updated=[]
            if matches:
                # Mantener el calendario completo para la pestaña CALENDARI; separar resultados solo con marcador reconocido.
                team['calendar']=matches
                future=[m for m in matches if m['iso']>=today and not m.get('score')]
                played=[m for m in matches if m.get('score') or m.get('result')]
                # When a fresh calendar was parsed, refresh these fields even when empty;
                # this removes stale false scores/fixtures from earlier faulty parser runs.
                team['upcoming']=future
                team['next']=future[0] if future else None
                team['next_match']=team['next']
                team['results']=sorted(played,key=lambda m:(m['iso'],m.get('time','')),reverse=True)
                team['last']=team['results'][0] if team['results'] else None
                team['last_match']=team['last']
                updated.append(f'partits {len(matches)}; resultats {len(played)}')
            if standings:
                team['standings']=standings
                # Derive the row for the team when present, without discarding existing fields otherwise.
                own=next((r for r in standings if 'vilamajor' in norm(r.get('team',''))),None)
                if own:
                    old=team.get('standing') or {}
                    standing={**old}
                    if own.get('position') is not None: standing['position']=own['position']
                    stats=own.get('stats') or []
                    if len(stats)>0 and stats[0] is not None: standing['played']=stats[0]
                    if len(stats)>7 and stats[7] is not None: standing['points']=stats[7]
                    team['standing']=standing
                updated.append(f'classificació {len(standings)}')
            elif summary:
                team.setdefault('standing',{}).update(summary); updated.append('resum classificació')
            if squad:
                team['squad']=squad; team['plantilla']=squad; updated.append(f'plantilla {len(squad)}')
            if not updated:
                failures.append(label); print(f'AVISO {label}: no se ha reconocido ningún dato deportivo; se conserva lo anterior', flush=True)
            else:
                success+=1; team['data_source']=final_url; team['data_updated_at']=datetime.now().astimezone().isoformat(timespec='seconds')
                print(f'OK {label}: '+', '.join(updated), flush=True)
        except Exception as e:
            failures.append(label); print(f'AVISO {label}: {e}; se conservan los datos anteriores', flush=True)
    if success==0: raise SystemExit('No se pudo extraer ningún dato deportivo. No se publica una actualización falsa.')
    payload['updated_at']=datetime.now().astimezone().isoformat(timespec='seconds')
    payload['source']='elFutbol.co (partidos, clasificación y plantilla cuando están publicados)'
    with open(DATA_PATH,'w',encoding='utf-8') as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    print(f'Actualizado data/club.json: {success}/{len(teams)} equipos con al menos un bloque de datos reconocido; {len(failures)} con avisos; fecha {payload["updated_at"]}', flush=True)
    # Exit nonzero when no team provided any recognized sports data, not merely because one field was unavailable.

if __name__=='__main__': main()
