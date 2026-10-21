#!/usr/bin/env python3
"""Sync C.F. Vilamajor team fixtures from the elFutbol team pages.
Only writes dates/matches when they can be parsed from the source HTML.
Existing team metadata is retained; past fixtures are never exposed as upcoming.
"""
import json, os, re, sys
from datetime import datetime, date
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(ROOT, "data", "club.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CFVilamajorSync/1.0)"}
DATE_PATTERNS = [
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b"),
    re.compile(r"\b(\d{1,2})[/-](\d{1,2})\b"),
]
SCORE_RE = re.compile(r"\b(\d{1,2})\s*[-:]\s*(\d{1,2})\b")

def parse_date(text):
    m = DATE_PATTERNS[0].search(text)
    if m:
        try: return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError: return None
    m = DATE_PATTERNS[1].search(text)
    if m:
        a,b,y = map(int,m.groups())
        if y < 100: y += 2000
        # elFutbol pages normally show day/month/year
        try: return date(y,b,a).isoformat()
        except ValueError: return None
    m = DATE_PATTERNS[2].search(text)
    if m:
        day,month=map(int,m.groups())
        year=date.today().year
        if month < date.today().month-5: year += 1
        try: return date(year,month,day).isoformat()
        except ValueError: return None
    return None

def parse_rows(page, team):
    soup=BeautifulSoup(page,"html.parser")
    found=[]
    # Use rows/cards as units so the home/away pair and date are from the same fixture.
    candidates=soup.select("tr, article, li, .match, .fixture, .partido, .game, .match-row, .fixture-row")
    seen=set()
    for node in candidates:
        text=" ".join(node.stripped_strings)
        if len(text)<8: continue
        iso=parse_date(text)
        if not iso: continue
        # Extract two team names from links when possible; avoid using arbitrary page navigation links.
        names=[]
        for el in node.select("a"):
            label=" ".join(el.stripped_strings).strip()
            if label and len(label)>2 and label.lower() not in {"ver","detalles","resultados","calendario"}:
                if label not in names: names.append(label)
        if len(names)<2: continue
        home,away=names[0],names[1]
        # Score is optional; only accept if row contains a clear numeric score.
        score_match=SCORE_RE.search(text)
        score=score_match.group(0) if score_match else ""
        # Avoid extracting tables with two arbitrary linked teams: at least one team must resemble the club/team page.
        club_words=("vilamajor","vilamajor, c.f.","c.f. vilamajor")
        if not any(w in (home+" "+away).lower() for w in club_words):
            # Team name can differ in some competitions; only accept explicit match-like rows.
            if not any(k in text.lower() for k in ("jornada","camp","hora","finalitzat","finalizado")):
                continue
        key=(iso,home,away,score)
        if key in seen: continue
        seen.add(key)
        dt=datetime.fromisoformat(iso)
        item={"date":dt.strftime("%d-%m"),"iso":iso,"time":"","home":home,"away":away,"venue":None,"source":team.get("url")}
        if score:
            item["score"]=score
            item["result"]=score
        found.append(item)
    return sorted(found,key=lambda x:(x["iso"],x.get("time","")))

def main():
    if not os.path.exists(DATA_PATH):
        raise SystemExit("Falta data/club.json")
    with open(DATA_PATH,encoding="utf-8") as f: payload=json.load(f)
    teams=payload.get("teams",[])
    if not teams: raise SystemExit("data/club.json no contiene equipos")
    session=requests.Session(); session.headers.update(HEADERS)
    today=date.today().isoformat()
    successes=0
    for team in teams:
        url=team.get("url")
        if not url: continue
        try:
            r=session.get(url,timeout=25)
            r.raise_for_status()
            matches=parse_rows(r.text,team)
            if matches:
                future=[m for m in matches if m["iso"]>=today and not m.get("score")]
                played=[m for m in matches if m["iso"]<today or m.get("score")]
                # Replace only when a real match list was recognized; otherwise keep prior fields.
                team["upcoming"]=future
                team["next"]=future[0] if future else None
                team["next_match"]=future[0] if future else None
                if played:
                    team["results"]=sorted(played,key=lambda m:m["iso"],reverse=True)
                    team["last"]=team["results"][0]
                    team["last_match"]=team["results"][0]
                successes += 1
                print(f"OK {team.get('_display') or team.get('name')}: {len(future)} futuros, {len(played)} jugados")
            else:
                print(f"AVISO {team.get('_display') or team.get('name')}: no se reconocieron partidos; se conservan los datos existentes")
        except Exception as e:
            print(f"AVISO {team.get('_display') or team.get('name')}: {e}")
    if successes == 0:
        raise SystemExit("No se pudo extraer ningún calendario. No se publica una actualización falsa.")
    payload["updated_at"]=datetime.now().astimezone().isoformat(timespec="seconds")
    payload["source"]="elFutbol.co"
    with open(DATA_PATH,"w",encoding="utf-8") as f: json.dump(payload,f,ensure_ascii=False,indent=2)
    print(f"Actualizado data/club.json: {successes}/{len(teams)} equipos; fecha {payload['updated_at']}")

if __name__=="__main__":
    main()
