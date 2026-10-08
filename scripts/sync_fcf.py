import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright

BASE_URL = "https://www.fcf.cat"
CLUB_ID = "1096"
SEASON = "2026/27"
CLUB_URLS = [
    f"{BASE_URL}/ca/clubs/{CLUB_ID}",
    f"{BASE_URL}/ca/clubs/{CLUB_ID}/categories/39706",
]
OUTPUT = Path("data/fcf.json")


def clean(v):
    return re.sub(r"\s+", " ", v or "").strip()


def absolute(v):
    return urljoin(BASE_URL, v)


def date_iso(v):
    m = re.search(r"(\d{2})[./-](\d{2})[./-](\d{4})", v or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def date_display(v):
    m = re.search(r"(\d{2})[./-](\d{2})[./-](\d{4})", v or "")
    return f"{m.group(1)}.{m.group(2)}.{m.group(3)}" if m else ""


def time_value(v):
    m = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", v or "")
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def extract_urls(text):
    found = set()
    for m in re.findall(r"(?:https?://(?:www\.)?fcf\.cat)?/ca/(?:competicio/acta/\d+|[^\s\"'<>]*calendari-equip[^\s\"'<>]*)", text or "", flags=re.I):
        found.add(absolute(m))
    for m in re.findall(r"https?://(?:www\.)?fcf\.cat/ca/competicio/acta/\d+", text or "", flags=re.I):
        found.add(m)
    return {u for u in found if "fcf.cat/ca/" in u}


async def collect_links(page, url):
    discovered = set()
    responses = []

    async def on_response(resp):
        try:
            u = resp.url
            if "fcf.cat" not in u:
                return
            ct = resp.headers.get("content-type", "")
            if "text" not in ct and "json" not in ct and "javascript" not in ct:
                return
            if "/ca/" in u or "api" in u.lower() or "json" in u.lower():
                try:
                    body = await resp.text()
                    responses.append(body[:2_000_000])
                except Exception:
                    pass
        except Exception:
            pass

    page.on("response", on_response)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(9000)
        html = await page.content()
        body = await page.locator("body").inner_text(timeout=15000)
        discovered |= extract_urls(html)
        discovered |= extract_urls(body)
        for body_text in responses:
            discovered |= extract_urls(body_text)
        for a in await page.locator("a").all():
            try:
                href = await a.get_attribute("href")
                if href:
                    u = absolute(href)
                    if "/ca/competicio/acta/" in u or "calendari-equip" in u.lower():
                        discovered.add(u)
            except Exception:
                pass
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass
    return discovered


def parse_acta(text, source):
    """Parse only the official ACTA header. Never search the whole page for scores."""
    text = clean(text)
    start = text.find("Detalls de l'acta")
    if start < 0:
        start = text.find("ACTA DEL PARTIT")
    if start < 0:
        return None

    end = text.find("COMPETICIÓ /", start)
    if end < 0:
        return None
    header = clean(text[start:end])
    header = re.sub(r"^.*?Detalls de l'acta\s*", "", header, flags=re.I)

    # Closed: TEAM ACTA TANCADA 1 - 2 TEAM
    # Pending: TEAM PENDENT - - - TEAM
    m = re.match(
        r"(?P<home>.+?)\s+(?P<status>ACTA\s+TANCADA|PENDENT|ACTA\s+OBERTA)\s+"
        r"(?P<score>\d{1,2}\s*-\s*\d{1,2}|-\s*-\s*-+)\s+(?P<away>.+)$",
        header,
        flags=re.I,
    )
    if not m:
        return None

    home, away = clean(m.group("home")), clean(m.group("away"))
    status = clean(m.group("status")).upper()
    raw_score = clean(m.group("score"))
    score = "" if "- -" in raw_score or raw_score == "- - -" else re.sub(r"\s*[-–:]\s*", "-", raw_score)

    # We only accept an acta involving the club and a genuine match header.
    if "VILAMAJOR" not in (home + " " + away).upper():
        return None
    if score and not re.fullmatch(r"\d{1,2}-\d{1,2}", score):
        return None

    after = text[end:]
    comp_m = re.search(r"COMPETICIÓ\s*/\s*(.*?)\s*/\s*(.*?)\s*/\s*JORNADA\s*(\d+)", after, re.I)
    competition = clean(comp_m.group(1)) if comp_m else ""
    group = clean(comp_m.group(2)) if comp_m else ""
    round_no = comp_m.group(3) if comp_m else ""

    season_m = re.search(r"TEMPORADA\s*:\s*([0-9]{4}\s*/\s*[0-9]{2})", after, re.I)
    date_m = re.search(r"DATA\s*:\s*(\d{2}[./-]\d{2}[./-]\d{4})", after, re.I)
    time_m = re.search(r"HORA\s*:\s*([0-2]?\d[.:][0-5]\d)", after, re.I)
    stadium_m = re.search(r"ESTADI\s*:\s*(.*?)(?:\s+ENTRENADOR/A|$)", after, re.I)

    date_raw = date_m.group(1) if date_m else ""
    return {
        "home": home,
        "away": away,
        "score": score,
        "status": status,
        "date": date_display(date_raw),
        "iso": date_iso(date_raw),
        "time": time_value(time_m.group(1)) if time_m else "",
        "competition": competition,
        "group": group,
        "round": round_no,
        "season": clean(season_m.group(1)) if season_m else SEASON,
        "venue": clean(stadium_m.group(1)) if stadium_m else "",
        "source": source,
    }


async def read_acta(page, url):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2500)
        text = await page.locator("body").inner_text(timeout=15000)
        return parse_acta(text, url)
    except Exception as exc:
        print("Error acta:", url, exc)
        return None


def team_key(name, competition, group):
    return (clean(name).upper(), clean(competition).upper(), clean(group).upper())


def build_teams(matches):
    grouped = {}
    for m in matches:
        for side in ("home", "away"):
            name = m[side]
            if "VILAMAJOR" not in name.upper():
                continue
            key = team_key(name, m["competition"], m["group"])
            grouped.setdefault(key, []).append(m)

    teams = []
    for _, ms in grouped.items():
        ms.sort(key=lambda x: x.get("iso", ""))
        name = next(x["home"] if "VILAMAJOR" in x["home"].upper() else x["away"] for x in ms)
        results = [m for m in ms if m["score"] and m["status"] == "ACTA TANCADA"]
        upcoming = [m for m in ms if not m["score"]]
        results.sort(key=lambda x: x.get("iso", ""), reverse=True)
        upcoming.sort(key=lambda x: x.get("iso", ""))
        sample = ms[0]
        def item(m):
            return {
                "home": m["home"], "away": m["away"], "date": m["date"], "iso": m["iso"],
                "time": m["time"], "score": m["score"], "venue": m["venue"],
                "competition": m["competition"], "round": m["round"], "source": m["source"],
            }
        teams.append({
            "name": name,
            "category": sample["competition"] or "FCF",
            "competition": sample["competition"],
            "group": sample["group"],
            "venue": sample["venue"],
            "url": sample["source"],
            "next": item(upcoming[0]) if upcoming else None,
            "last": item(results[0]) if results else None,
            "upcoming": [item(x) for x in upcoming],
            "results": [item(x) for x in results],
            "standing": {},
        })
    teams.sort(key=lambda x: x["name"])
    return teams


async def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    old = None
    if OUTPUT.exists():
        try:
            old = json.loads(OUTPUT.read_text(encoding="utf-8"))
        except Exception:
            old = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        discover = await browser.new_page(viewport={"width": 1440, "height": 1200}, locale="ca-ES")
        urls = set()
        for club_url in CLUB_URLS:
            print("DESCUBRIR:", club_url)
            try:
                urls |= await collect_links(discover, club_url)
            except Exception as exc:
                print("Error club:", exc)
        await discover.close()

        # Always include actas already known in the existing JSON.
        if old:
            for t in old.get("teams", []):
                for bucket in ("upcoming", "results"):
                    for x in t.get(bucket, []):
                        if x.get("source"):
                            urls.add(x["source"])
                for x in (t.get("next"), t.get("last")):
                    if isinstance(x, dict) and x.get("source"):
                        urls.add(x["source"])

        urls = {u for u in urls if re.search(r"/competicio/acta/\d+", u)}
        print("ACTAS DESCUBIERTAS:", len(urls))

        page = await browser.new_page(viewport={"width": 1440, "height": 1200}, locale="ca-ES")
        matches = []
        for i, url in enumerate(sorted(urls), 1):
            print(f"ACTA {i}/{len(urls)}: {url}")
            m = await read_acta(page, url)
            if m:
                matches.append(m)
        await browser.close()

    unique = {}
    for m in matches:
        key = (m["home"], m["away"], m["date"], m["time"], m["score"], m["source"])
        unique[key] = m
    matches = list(unique.values())
    teams = build_teams(matches)

    # Hard validation: no footer/address can ever become a score or a team.
    bad = [m for m in matches if m["score"] and not re.fullmatch(r"\d{1,2}-\d{1,2}", m["score"])]
    if bad:
        raise RuntimeError("Validación FCF fallida: marcador inválido detectado.")
    if not teams:
        raise RuntimeError("FCF no ha devuelto ningún acta válida de C.F. Vilamajor. No se modifica data/fcf.json.")

    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "club": {
            "name": "C.F. Vilamajor",
            "fcf_id": CLUB_ID,
            "source": f"{BASE_URL}/ca/clubs/{CLUB_ID}",
        },
        "teams": teams,
    }

    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("OK: equipos", len(teams), "partidos", len(matches))


if __name__ == "__main__":
    asyncio.run(main())
