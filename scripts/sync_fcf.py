import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright


BASE_URL = "https://www.fcf.cat"
CLUB_ID = "1096"

# Página pública del C.F. Vilamajor en FCF
CLUB_URL = f"{BASE_URL}/ca/clubs/{CLUB_ID}/categories/39706"

OUTPUT = Path("data/fcf.json")


def clean(value):
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def absolute(url):
    if not url:
        return ""
    return urljoin(BASE_URL, url)


def looks_like_team_url(url):
    return (
        f"/clubs/{CLUB_ID}/" in url
        and "/categories/" in url
    )


def extract_date(text):
    patterns = [
        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
        r"\b\d{1,2}-\d{1,2}-\d{4}\b",
        r"\b\d{1,2}/\d{1,2}/\d{2}\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)

    return ""


def extract_time(text):
    match = re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", text)
    return match.group(0) if match else ""


def extract_score(text):
    patterns = [
        r"\b(\d{1,2})\s*[-–]\s*(\d{1,2})\b",
        r"\b(\d{1,2})\s*:\s*(\d{1,2})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return f"{match.group(1)}-{match.group(2)}"

    return ""


async def get_links(page):
    links = set()

    for element in await page.locator("a").all():
        try:
            href = await element.get_attribute("href")
            if href:
                url = absolute(href)
                if looks_like_team_url(url):
                    links.add(url)
        except Exception:
            pass

    return sorted(links)


async def parse_team(page, url):
    print(f"Analitzant: {url}")

    try:
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000
        )
    except Exception as exc:
        print(f"Error carregant {url}: {exc}")
        return None

    try:
        await page.wait_for_timeout(5000)
    except Exception:
        pass

    text = clean(await page.locator("body").inner_text())

    if not text:
        return None

    lines = [
        clean(line)
        for line in text.splitlines()
        if clean(line)
    ]

    # Intentem obtenir el nom de l'equip des del títol
    title = clean(await page.title())

    name = ""

    if title:
        title_match = re.search(
            r"VILAMAJOR.*?\|\s*(.*)",
            title,
            re.IGNORECASE
        )
        if title_match:
            name = clean(title_match.group(1))

    if not name:
        for line in lines[:80]:
            upper = line.upper()

            if (
                "VILAMAJOR" in upper
                and len(line) < 120
                and "CATEGOR" not in upper
                and "CONTACT" not in upper
            ):
                name = line
                break

    if not name:
        name = "C.F. Vilamajor"

    # Detectem informació bàsica que FCF mostra a la pàgina
    competition = ""
    venue = ""

    for line in lines:
        upper = line.upper()

        if (
            not competition
            and (
                "LLIGA" in upper
                or "DIVISIÓ" in upper
                or "DIVISION" in upper
                or "PREFERENT" in upper
                or "CADET" in upper
                or "JUVENIL" in upper
                or "INFANTIL" in upper
                or "ALEVÍ" in upper
                or "BENJAMÍ" in upper
                or "PREBENJAMÍ" in upper
            )
            and len(line) < 160
        ):
            competition = line

        if (
            not venue
            and (
                "CAMP" in upper
                or "ESTADI" in upper
                or "MUNICIPAL" in upper
            )
            and len(line) < 160
        ):
            venue = line

    # Busquem enllaços a actes de partits
    acta_links = []

    for element in await page.locator("a").all():
        try:
            href = await element.get_attribute("href")
            if not href:
                continue

            full = absolute(href)

            if "/competicio/acta/" in full:
                acta_links.append(full)

        except Exception:
            pass

    acta_links = list(dict.fromkeys(acta_links))

    upcoming = []
    results = []

    # Limitem el nombre inicial per no castigar FCF
    for acta_url in acta_links[:30]:

        try:
            await page.goto(
                acta_url,
                wait_until="domcontentloaded",
                timeout=60000
            )

            await page.wait_for_timeout(1500)

            acta_text = clean(
                await page.locator("body").inner_text()
            )

            if not acta_text:
                continue

            date = extract_date(acta_text)
            time = extract_time(acta_text)
            score = extract_score(acta_text)

            acta_lines = [
                clean(x)
                for x in acta_text.splitlines()
                if clean(x)
            ]

            home = ""
            away = ""

            # Intentem trobar dos equips a prop del marcador
            if score:
                score_index = None

                for i, line in enumerate(acta_lines):
                    if score in line:
                        score_index = i
                        break

                if score_index is not None:
                    before = acta_lines[
                        max(0, score_index - 8):
                        score_index
                    ]

                    after = acta_lines[
                        score_index + 1:
                        score_index + 9
                    ]

                    candidates = before + after

                    team_candidates = []

                    for candidate in candidates:
                        if (
                            len(candidate) > 2
                            and len(candidate) < 100
                            and not re.search(
                                r"\d{1,2}[:/-]\d{1,2}",
                                candidate
                            )
                        ):
                            team_candidates.append(candidate)

                    if len(team_candidates) >= 2:
                        home = team_candidates[-2]
                        away = team_candidates[-1]

            item = {
                "date": date,
                "time": time,
                "home": home,
                "away": away,
                "score": score,
                "venue": venue,
                "url": acta_url
            }

            # Si té marcador, és un resultat
            if score:
                results.append(item)
            else:
                upcoming.append(item)

        except Exception as exc:
            print(f"Error acta {acta_url}: {exc}")

    # Evitem duplicats
    def unique(items):
        output = []
        seen = set()

        for item in items:
            key = (
                item.get("date"),
                item.get("time"),
                item.get("home"),
                item.get("away"),
                item.get("score")
            )

            if key not in seen:
                seen.add(key)
                output.append(item)

        return output

    upcoming = unique(upcoming)
    results = unique(results)

    # Intentem ordenar cronològicament
    upcoming.sort(
        key=lambda x: (
            x.get("date", ""),
            x.get("time", "")
        )
    )

    results.sort(
        key=lambda x: (
            x.get("date", ""),
            x.get("time", "")
        ),
        reverse=True
    )

    # Últim resultat
    last = results[0] if results else None

    # Proper partit
    next_match = upcoming[0] if upcoming else None

    return {
        "name": name,
        "category": name,
        "competition": competition,
        "venue": venue,
        "url": url,
        "next": next_match,
        "last": last,
        "results": results[:20],
        "upcoming": upcoming[:20],
        "standing": {},
        "actas": acta_links[:30]
    }


async def main():

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    old_data = {}

    if OUTPUT.exists():
        try:
            old_data = json.loads(
                OUTPUT.read_text(
                    encoding="utf-8"
                )
            )
        except Exception:
            old_data = {}

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        page = await browser.new_page(
            viewport={
                "width": 1440,
                "height": 1000
            },
            locale="ca-ES"
        )

        print("Obrint FCF...")
        print(CLUB_URL)

        try:
            await page.goto(
                CLUB_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )
        except Exception as exc:
            print(f"Error FCF: {exc}")

        await page.wait_for_timeout(7000)

        links = await get_links(page)

        print(
            f"Equips/enllaços FCF trobats: {len(links)}"
        )

        # Si FCF no ha carregat els enllaços,
        # conservem les dades anteriors.
        if not links:

            print(
                "No s'han trobat equips. "
                "Conservem les dades anteriors."
            )

            if old_data:
                OUTPUT.write_text(
                    json.dumps(
                        old_data,
                        ensure_ascii=False,
                        indent=2
                    ),
                    encoding="utf-8"
                )

            await browser.close()
            return

        teams = []

        for team_url in links:

            try:
                team = await parse_team(
                    page,
                    team_url
                )

                if team:
                    teams.append(team)

            except Exception as exc:
                print(
                    f"Error processant equip: {exc}"
                )

        await browser.close()

    # Si el parser no ha aconseguit obtenir equips,
    # no destruïm les dades bones anteriors.
    if not teams:

        print(
            "No s'han pogut construir equips. "
            "Conservem dades anteriors."
        )

        if old_data:
            OUTPUT.write_text(
                json.dumps(
                    old_data,
                    ensure_ascii=False,
                    indent=2
                ),
                encoding="utf-8"
            )

        return

    data = {
        "updated_at": datetime.now().astimezone().isoformat(),
        "club": {
            "name": "C.F. Vilamajor",
            "fcf_id": CLUB_ID,
            "source": CLUB_URL
        },
        "teams": teams
    }

    OUTPUT.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print(
        f"OK — {len(teams)} equips guardats."
    )


if __name__ == "__main__":
    asyncio.run(main())
