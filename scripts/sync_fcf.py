import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urlparse

from playwright.async_api import async_playwright

BASE = "https://elfutbol.co"
CLUB_URL = f"{BASE}/es/club/vilamajor-cf"
OUTPUT = Path("data/fcf.json")
MIN_TEAMS = 10


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip(" \t\r\n·|—–-")


def first_int(pattern, text):
    m = re.search(pattern, text, re.I | re.M)
    return int(m.group(1)) if m else None


def date_iso(raw, now=None):
    if not raw:
        return None
    raw = raw.strip()
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", raw)
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    year = int(m.group(3)) if m.group(3) else (now or datetime.now()).year
    if year < 100:
        year += 2000
    # Season fixtures can cross the calendar year. Use the current year unless the
    # date is more than six months behind today, in which case treat it as next year.
    today = (now or datetime.now()).date()
    try:
        candidate = datetime(year, month, day).date()
        if not m.group(3) and candidate < today and (today - candidate).days > 180:
            year += 1
        return f"{year:04d}-{month:02d}-{day:02d}"
    except ValueError:
        return None


def get_stat(text, label_patterns):
    for pat in label_patterns:
        m = re.search(pat, text, re.I | re.M)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                pass
    return None


def extract_standing(text):
    # The profile uses stacked labels, e.g. 5° / POSICIÓN, 2 / PARTIDOS, 3 / PUNTOS.
    position = get_stat(text, [r"\b(\d{1,2})\s*(?:º|°|ª)\s*\n\s*POSICI[ÓO]N"])
    played = get_stat(text, [r"\b(\d{1,3})\s*\n\s*PARTIDOS\b"])
    points = get_stat(text, [r"\b(\d{1,3})\s*\n\s*PUNTOS\b"])
    gf = ga = None
    m = re.search(r"\b(\d{1,3})\s*:\s*(\d{1,3})\s*\n\s*GF\s*:\s*GC\b", text, re.I)
    if m:
        gf, ga = int(m.group(1)), int(m.group(2))
    if all(v is None for v in (position, played, points, gf, ga)):
        return {}
    result = {"position": position, "played": played, "points": points}
    if gf is not None: result["gf"] = gf
    if ga is not None: result["ga"] = ga
    return result


def is_team_line(line):
    line = clean(re.sub(r"^Image:\s*", "", line, flags=re.I))
    return bool(line and len(line) < 90 and re.search(r"(?:vilamajor|,\s*(?:C\.F\.|F\.C\.|U\.E\.|C\.D\.|E\.C\.)|\b(?:A|B)\s*$)", line, re.I))


def parse_upcoming(lines, team_name, url):
    idx = next((i for i, line in enumerate(lines) if re.search(r"próximo partido|proximo partido", line, re.I)), None)
    if idx is None:
        return None
    block = lines[idx:min(len(lines), idx + 18)]
    joined = " | ".join(block)
    date_raw = None
    time = None
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", joined)
    if m:
        date_raw = m.group(1)
    else:
        # Often rendered as 19:30 10-10 or 19:3010-10 in visible text.
        m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\s*(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)", joined)
        if m:
            time = f"{int(m.group(1)):02d}:{m.group(2)}"
            date_raw = m.group(3)
    if not time:
        m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", joined)
        if m:
            time = f"{int(m.group(1)):02d}:{m.group(2)}"
    # Team names commonly appear as separate text lines around the fixture header.
    candidates = []
    for line in block:
        line = clean(re.sub(r"^Image:\s*", "", line, flags=re.I))
        if (line and len(line) < 90 and not re.search(r"próximo|jornada|\bVS\b|\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}|ver más|temporada", line, re.I)
                and not re.fullmatch(r"\d+", line)
                and ("vilamajor" in line.lower() or re.search(r"(?:,\s*(?:C\.F\.|F\.C\.|U\.E\.|C\.D\.|E\.C\.)|\b(?:A|B)$)", line, re.I))):
            if line not in candidates:
                candidates.append(line)
    # The profile names the club team and opponent in home-away order.
    home = away = None
    vil_idx = next((i for i, n in enumerate(candidates) if "vilamajor" in n.lower()), None)
    if vil_idx is not None:
        if vil_idx > 0:
            home, away = candidates[vil_idx - 1], candidates[vil_idx]
        elif vil_idx + 1 < len(candidates):
            home, away = candidates[vil_idx], candidates[vil_idx + 1]
    if not date_raw and not time and not home and not away:
        return None
    return {"date": date_iso(date_raw), "iso": date_iso(date_raw), "time": time,
            "home": home, "away": away, "score": None, "venue": None, "url": url}


def parse_results(lines, team_name, url):
    results = []
    # Recent results are rendered after the heading "Resultados recientes". A match
    # row may look like: FT19-09 Vilamajor, C.F. A Opponent 31.
    start = next((i for i, line in enumerate(lines) if re.search(r"resultados recientes", line, re.I)), None)
    if start is None:
        return results
    block = lines[start:min(len(lines), start + 90)]
    for line in block:
        if not re.search(r"\b(?:FT|FINAL|FINALIZADO)\b", line, re.I):
            continue
        mdate = re.search(r"(?:FT|FINAL|FINALIZADO)\s*(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)", line, re.I)
        if not mdate:
            continue
        raw_date = mdate.group(1)
        rest = line[mdate.end():]
        # Score may be displayed as two adjacent digits at the end (e.g. 31 = 3-1)
        # or as a conventional 3-1 / 3:1 score. Only accept unambiguous patterns.
        score = None
        sm = re.search(r"\b(\d{1,2})\s*[-–:]\s*(\d{1,2})\s*$", rest)
        if sm:
            hs, aws = int(sm.group(1)), int(sm.group(2))
            score = f"{hs}-{aws}"
            rest = rest[:sm.start()]
        else:
            sm = re.search(r"\s(\d)(\d)\s*(?:\[Button:.*\])?\s*$", rest)
            if sm:
                hs, aws = int(sm.group(1)), int(sm.group(2))
                score = f"{hs}-{aws}"
                rest = rest[:sm.start()]
        if not score:
            continue
        rest = clean(rest)
        # Split around the club team name if possible. Keep opponent as text only when clear.
        lower = rest.lower()
        vi = lower.find("vilamajor")
        if vi < 0:
            continue
        # Name on this page is generally the team name; match row uses the same base label.
        team_match = re.search(r"Vilamajor,?\s*C\.F\.\s*[AB]?", rest, re.I)
        if not team_match:
            continue
        club = clean(team_match.group(0))
        before = clean(rest[:team_match.start()])
        after = clean(rest[team_match.end():])
        # If the club appears first, it is home; if opponent text precedes it, club is away.
        if before:
            home, away = before, club
        elif after:
            home, away = club, after
        else:
            continue
        results.append({"date": date_iso(raw_date), "iso": date_iso(raw_date), "time": None,
                        "home": home, "away": away, "score": score, "venue": None, "url": url})
    # Deduplicate and newest first.
    unique = {}
    for m in results:
        unique[(m.get("date"), m.get("home"), m.get("away"), m.get("score"))] = m
    return sorted(unique.values(), key=lambda x: x.get("iso") or "", reverse=True)


async def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale="es-ES", viewport={"width": 1440, "height": 1200})
        print("FUENTE ÚNICA: elFutbol")
        print("Abriendo club:", CLUB_URL)
        response = await page.goto(CLUB_URL, wait_until="domcontentloaded", timeout=60000)
        if not response or response.status >= 400:
            await browser.close()
            raise RuntimeError(f"No se pudo abrir el perfil del club: HTTP {response.status if response else 'sin respuesta'}")
        await page.wait_for_timeout(1800)
        links = await page.locator("a[href]").evaluate_all("""els => els.map(a => ({url:a.href, name:(a.innerText||a.textContent||'').trim()}))""")
        team_urls = {}
        for item in links:
            url = urldefrag(item.get("url", ""))[0]
            parsed = urlparse(url)
            if parsed.netloc == "elfutbol.co" and "/es/team/" in parsed.path:
                team_urls[url] = clean(item.get("name", ""))
        print("ENLACES DE EQUIPOS:", len(team_urls))
        if len(team_urls) < MIN_TEAMS:
            await browser.close()
            raise RuntimeError(f"Solo se encontraron {len(team_urls)} equipos. Se conserva el JSON anterior.")

        teams = []
        for n, (url, link_name) in enumerate(sorted(team_urls.items()), 1):
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                if not resp or resp.status >= 400:
                    print("AVISO HTTP equipo:", url)
                    continue
                await page.wait_for_timeout(700)
                title = clean(await page.title())
                try:
                    heading = clean(await page.locator("h1").first.inner_text(timeout=1500))
                except Exception:
                    heading = ""
                body = await page.locator("body").inner_text()
                lines = [clean(x) for x in body.splitlines() if clean(x)]
                name = heading or link_name or title.split("|")[0]
                if "vilamajor" not in (name + " " + url).lower():
                    continue
                standing = extract_standing(body)
                upcoming = parse_upcoming(lines, name, url)
                results = parse_results(lines, name, url)
                team = {
                    "name": name,
                    "category": title,
                    "competition": title,
                    "url": url,
                    "next": upcoming,
                    "last": results[0] if results else None,
                    "standing": standing,
                    "upcoming": [upcoming] if upcoming else [],
                    "results": results[:8],
                }
                teams.append(team)
                print(f"{n}/{len(team_urls)} {name}: próximo={'sí' if upcoming else 'no'}, resultado={'sí' if results else 'no'}, clasificación={'sí' if standing else 'no'}")
            except Exception as exc:
                print("ERROR leyendo equipo", url, repr(exc))
        await browser.close()

    if len(teams) < MIN_TEAMS:
        raise RuntimeError(f"Solo se leyeron {len(teams)} equipos. No se sobrescribe data/fcf.json.")
    data = {"source": "elFutbol", "source_url": CLUB_URL,
            "updated_at": datetime.now(timezone.utc).isoformat(), "teams": teams}
    temp = OUTPUT.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(OUTPUT)
    print("OK: equipos", len(teams), "próximos", sum(bool(t["next"]) for t in teams),
          "últimos resultados", sum(bool(t["last"]) for t in teams),
          "clasificaciones", sum(bool(t["standing"]) for t in teams))


if __name__ == "__main__":
    asyncio.run(main())
