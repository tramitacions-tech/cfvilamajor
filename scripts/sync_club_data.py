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
        y=today.year
        # season turns over around July; Oct is current year, dates in Jan-Jun remain current year
        try:
            candidate=date(y,mo,d)
            if candidate < today.replace(year=max(1,today.year-1)) and mo>=7: y+=1
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
            r=session.get(candidate,timeout=35); r.raise_for_status()
            if len(r.text)>1000 and ('elfutbol' in r.url or 'vilamajor' in r.text.lower()): return r.url,r.text
            errors.append('respuesta sin contenido útil')
        except Exception as e: errors.append(str(e))
    raise RuntimeError('; '.join(errors) or 'no se pudo descargar la página')

def row_cells(row): return [clean(x.get_text(' ',strip=True)) for x in row.find_all(['td','th'],recursive=False)]

def split_match_label(full, team):
    """Read elFutbol's compact single-link match labels, e.g. FT30-11 Rival A Vilamajor, C.F. A 21."""
    raw=clean(full)
    score=''
    # In match links elFutbol sometimes concatenates scores (2-1 -> 21).
    tail=re.search(r'\s(\d{2,3})\s*$',raw)
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
            r=session.get(url,timeout=30); r.raise_for_status()
            if len(r.text)>1000: pages.append((r.url,BeautifulSoup(r.text,'html.parser')))
        except Exception as e: print(f'AVISO subpágina {label}: {e}')
    return pages

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
        score=''
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
            if compact_score and not SCORE_RE.search(full):
                score=compact_score
        if len(names)<2:
            cleaned=re.sub(r'\b(FT|HT|FINALIZADO|FINALITZAT|APLAZADO|SUSPENDIDO)\b',' ',full,flags=re.I)
            cleaned=DATE_RE.sub(' ',cleaned); cleaned=TIME_RE.sub(' ',cleaned)
            sm=SCORE_RE.search(cleaned)
            if sm: cleaned=cleaned[:sm.start()]+' '+cleaned[sm.end():]
            parts=re.split(r'\s{2,}|\s+[–—]\s+|\s+vs\.?\s+',cleaned)
            parts=[clean(x) for x in parts if len(clean(x))>2 and not re.fullmatch(r'\d+',clean(x))]
            club=[x for x in parts if any(w in norm(x) for w in ['vilamajor','c.f.','f.c.','u.d.','c.e.','club futbol','futbol club'])]
            if len(club)>=2: names=club[:2]
        if len(names)<2: continue
        # Score is only accepted when it is visibly separated; don't guess compact strings like "21".
        sm=SCORE_RE.search(full)
        if sm: score=f'{sm.group(1)}-{sm.group(2)}'
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
        sm=SCORE_RE.search(text); score=f'{sm.group(1)}-{sm.group(2)}' if sm else ''
        tm=TIME_RE.search(text); time=f'{int(tm.group(1)):02d}:{tm.group(2)}' if tm else ''
        item={'date':date.fromisoformat(iso).strftime('%d-%m'),'iso':iso,'time':time,'home':names[0],'away':names[1],'venue':None,'source':source_url}
        if score: item.update(score=score,result=score)
        key=(iso,norm(item['home']),norm(item['away']),score)
        if key not in seen: seen.add(key); found.append(item)
    return sorted(found,key=lambda x:(x['iso'],x.get('time','')))

def extract_standings(soup):
    best=[]
    for table in soup.find_all('table'):
        headers=[norm(x.get_text(' ',strip=True)) for x in table.find_all('th')]
        if not headers: continue
        blob=' '.join(headers)
        if not any(x in blob for x in ['pts','punts','puntos','equip','club','pj','partits','partidos']): continue
        rows=[]
        for tr in table.find_all('tr'):
            cells=row_cells(tr)
            if len(cells)<3 or tr.find('th'): continue
            links=[clean(a.get_text(' ',strip=True)) for a in tr.find_all('a',href=True)]
            name=next((x for x in links if len(x)>2),cells[0])
            nums=[]
            for c in cells:
                m=re.fullmatch(r'[-+]?\d+',c)
                nums.append(int(m.group()) if m else None)
            rows.append({'team':name,'cells':cells,'numbers':nums})
        if len(rows)>len(best): best=rows
    return best

def extract_squad(soup):
    best=[]
    for table in soup.find_all('table'):
        headers=[norm(x.get_text(' ',strip=True)) for x in table.find_all('th')]
        blob=' '.join(headers)
        if not headers or not any(x in blob for x in ['jugador','players','jugadors','nombre','nom']): continue
        rows=[]
        for tr in table.find_all('tr'):
            if tr.find('th'): continue
            cells=row_cells(tr)
            if not cells: continue
            links=[clean(a.get_text(' ',strip=True)) for a in tr.find_all('a',href=True)]
            name=next((x for x in links if len(x)>2 and not re.search(r'\d{1,2}[-/]\d{1,2}',x)),cells[0])
            if len(name)>2 and not any(x in norm(name) for x in ['plantilla','jugador','nombre']):
                rows.append({'name':name,'stats':cells[1:]})
        if len(rows)>len(best): best=rows
    # fallback: player links in section following Plantilla heading
    if not best:
        for a in soup.find_all('a',href=True):
            label=clean(a.get_text(' ',strip=True)); href=norm(a['href'])
            if label and len(label)>4 and ('player' in href or 'jugador' in href or 'persona' in href):
                if not any(norm(x.get('name',''))==norm(label) for x in best): best.append({'name':label,'stats':[]})
    return best

def extract_standing_summary(soup):
    text=clean(soup.get_text(' ',strip=True))
    out={}
    patterns=[('position',r'(\d{1,2})\s*(?:°|º)\s*POSICI[ÓO]N'),('played',r'(\d{1,2})\s*PARTIDOS'),('points',r'(\d{1,3})\s*PUNTOS')]
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
                team['upcoming']=future; team['next']=future[0] if future else None; team['next_match']=team['next']
                if played:
                    team['results']=sorted(played,key=lambda m:(m['iso'],m.get('time','')),reverse=True)
                    team['last']=team['results'][0]; team['last_match']=team['last']
                updated.append(f'partits {len(matches)}; resultats {len(played)}')
            if standings:
                team['standings']=standings
                # Derive the row for the team when present, without discarding existing fields otherwise.
                own=next((r for r in standings if 'vilamajor' in norm(r.get('team',''))),None)
                if own:
                    nums=[n for n in own.get('numbers',[]) if n is not None]
                    old=team.get('standing') or {}
                    if nums:
                        team['standing']={**old,'position':nums[0] if len(nums)>0 else old.get('position')}
                    for key in ('played','points'):
                        if key in summary: team.setdefault('standing',{})[key]=summary[key]
                updated.append(f'classificació {len(standings)}')
            elif summary:
                team.setdefault('standing',{}).update(summary); updated.append('resum classificació')
            if squad:
                team['squad']=squad; team['plantilla']=squad; updated.append(f'plantilla {len(squad)}')
            if not updated:
                failures.append(label); print(f'AVISO {label}: no se ha reconocido ningún dato deportivo; se conserva lo anterior')
            else:
                success+=1; team['data_source']=final_url; team['data_updated_at']=datetime.now().astimezone().isoformat(timespec='seconds')
                print(f'OK {label}: '+', '.join(updated))
        except Exception as e:
            failures.append(label); print(f'AVISO {label}: {e}; se conservan los datos anteriores')
    if success==0: raise SystemExit('No se pudo extraer ningún dato deportivo. No se publica una actualización falsa.')
    payload['updated_at']=datetime.now().astimezone().isoformat(timespec='seconds')
    payload['source']='elFutbol.co (partidos, clasificación y plantilla cuando están publicados)'
    with open(DATA_PATH,'w',encoding='utf-8') as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    print(f'Actualizado data/club.json: {success}/{len(teams)} equipos con al menos un bloque de datos reconocido; {len(failures)} con avisos; fecha {payload["updated_at"]}')
    # Exit nonzero when no team provided any recognized sports data, not merely because one field was unavailable.

if __name__=='__main__': main()
