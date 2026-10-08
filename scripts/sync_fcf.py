import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright


BASE_URL = "https://www.fcf.cat"
CLUB_ID = "1096"
CLUB_URL = f"{BASE_URL}/ca/clubs/{CLUB_ID}/categories/39706"

OUTPUT = Path("data/fcf.json")


TEAM_WORDS = [
    "VILAMAJOR",
    "JUVENIL",
    "CADET",
    "INFANTIL",
    "ALEV",
    "BENJ",
    "PREBENJ",
    "FEMEN",
]


def clean(value):
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def absolute(url):
    return urljoin(BASE_URL, url)


def is_fcf(url):
    return "fcf.cat" in url


def looks_like_team(text):
    text = clean(text).upper()

    if not text:
        return False

    if len(text) > 120:
        return False

    return any(word in text for word in TEAM_WORDS)


def score_from_text(text):
    patterns = [
        r"\b(\d{1,2})\s*[-–:]\s*(\d{1,2})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if match:
            return f"{match.group(1)}-{match.group(2)}"

    return ""


def date_from_text(text):
    patterns = [
        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
        r"\b\d{1,2}-\d{1,2}-\d{4}\b",
        r"\b\d{1,2}\.\d{1,2}\.\d{4}\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if match:
            return match.group(0)

    return ""


def time_from_text(text):
    match = re.search(
        r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b",
        text
    )

    return match.group(0) if match else ""


def competition_from_text(text):
    text = clean(text)

    match = re.search(
        r"COMPETICI[ÓO][N/:\s]+(.+?)(?:/Jornada|\s+Jornada)",
        text,
        re.IGNORECASE
    )

    if match:
        return clean(match.group(1))

    return ""


async def get_page(page, url):

    try:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=90000
        )

        await page.wait_for_timeout(5000)

        return response

    except Exception as exc:

        print("ERROR:", url)
        print(exc)

        return None


async def extract_calendar_links(page):

    links = set()

    for a in await page.locator("a").all():

        try:

            href = await a.get_attribute("href")

            if not href:
                continue

            url = absolute(href)

            if not is_fcf(url):
                continue

            low = url.lower()

            if (
                "calendari-equip" in low
                or "calendario-equipo" in low
                or "/competicio/" in low
            ):
                links.add(url)

        except Exception:
            pass

    return links


async def extract_team_links(page):

    links = set()

    for a in await page.locator("a").all():

        try:

            href = await a.get_attribute("href")
            text = clean(await a.inner_text())

            if not href:
                continue

            url = absolute(href)

            if not is_fcf(url):
                continue

            if looks_like_team(text):

                links.add(url)

        except Exception:
            pass

    return links


async def parse_calendar(page, url):

    print("")
    print("CALENDARIO:")
    print(url)

    await get_page(page, url)

    text = clean(
        await page.locator("body").inner_text()
    )

    title = clean(await page.title())

    lines = [
        clean(x)
        for x in text.splitlines()
        if clean(x)
    ]

    matches = []

    current = {}

    for i, line in enumerate(lines):

        upper = line.upper()

        if "JORNADA" in upper:

            if current:
                matches.append(current)

            current = {
                "round": line,
                "raw": []
            }

        if current:
            current["raw"].append(line)

        if "VILAMAJOR" in upper:

            window = " ".join(
                lines[
                    max(0, i - 3):
                    min(len(lines), i + 5)
                ]
            )

            current.setdefault(
                "teams_text",
                window
            )

            current.setdefault(
                "date",
                date_from_text(window)
            )

            current.setdefault(
                "time",
                time_from_text(window)
            )

            current.setdefault(
                "score",
                score_from_text(window)
            )

    if current:
        matches.append(current)

    clean_matches = []

    for match in matches:

        raw = clean(
            " ".join(match.get("raw", []))
        )

        if "VILAMAJOR" not in raw.upper():
            continue

        clean_matches.append({
            "round": match.get("round", ""),
            "date": match.get("date", ""),
            "time": match.get("time", ""),
            "score": match.get("score", ""),
            "text": match.get("teams_text", ""),
            "raw": raw[:500]
        })

    competition = ""

    for line in lines:

        if any(
            word in line.upper()
            for word in [
                "DIVISIÓ",
                "PREFERENT",
                "TERCERA",
                "SEGONA",
                "PRIMERA",
                "CADET",
                "INFANTIL",
                "ALEVÍ",
                "BENJAMÍ",
                "JUVENIL",
                "FEMENÍ"
            ]
        ):

            if len(line) < 150:
                competition = line
                break

    return {
        "url": url,
        "title": title,
        "competition": competition,
        "matches": clean_matches,
    }


async def main():

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        page = await browser.new_page(
            viewport={
                "width": 1440,
                "height": 1200
            },
            locale="ca-ES"
        )

        print("====================================")
        print("FCF VILAMAJOR")
        print("TEMPORADA 2026/27")
        print("====================================")
        print("")

        # ------------------------------------------------
        # 1. ENTRAR A LA PÀGINA DEL CLUB
        # ------------------------------------------------

        await get_page(
            page,
            CLUB_URL
        )

        # ------------------------------------------------
        # 2. BUSCAR ENLACES DIRECTOS
        # ------------------------------------------------

        team_links = await extract_team_links(
            page
        )

        calendar_links = await extract_calendar_links(
            page
        )

        print(
            f"Enllaços d'equip trobats: {len(team_links)}"
        )

        print(
            f"Enllaços de calendari trobats: {len(calendar_links)}"
        )

        # ------------------------------------------------
        # 3. EXPLORAR TODAS LAS PÁGINAS FCF ENCONTRADAS
        # ------------------------------------------------

        discovered = set()

        for url in team_links:

            if url not in discovered:

                discovered.add(url)

                try:

                    await get_page(
                        page,
                        url
                    )

                    found = await extract_calendar_links(
                        page
                    )

                    calendar_links.update(
                        found
                    )

                except Exception:
                    pass

        print(
            f"Calendarios finales: {len(calendar_links)}"
        )

        # ------------------------------------------------
        # 4. LEER CALENDARIOS
        # ------------------------------------------------

        calendars = []

        for url in sorted(calendar_links):

            result = await parse_calendar(
                page,
                url
            )

            if result["matches"]:

                calendars.append(
                    result
                )

        print(
            f"Calendarios con partidos: {len(calendars)}"
        )

        # ------------------------------------------------
        # 5. CONSTRUIR EQUIPOS
        # ------------------------------------------------

        teams = []

        for calendar in calendars:

            text = (
                calendar["title"]
                + " "
                + calendar["competition"]
                + " "
                + " ".join(
                    m["text"]
                    for m in calendar["matches"]
                )
            )

            name = "VILAMAJOR, C.F."

            upper = text.upper()

            if "JUVENIL" in upper:
                name = "Juvenil"

            elif "CADET" in upper:
                name = "Cadet"

            elif "INFANTIL" in upper:
                name = "Infantil"

            elif "ALEV" in upper:
                name = "Aleví"

            elif "BENJ" in upper:
                name = "Benjamí"

            elif "FEMEN" in upper:
                name = "Femení"

            elif "TERCERA CATALANA" in upper:
                name = "Primer Equip"

            team = {
                "name": name,
                "category": name,
                "competition": calendar[
                    "competition"
                ],
                "venue": "",
                "url": calendar["url"],
                "next": None,
                "last": None,
                "upcoming": [],
                "results": [],
                "standing": {}
            }

            for match in calendar["matches"]:

                item = {
                    "round": match["round"],
                    "date": match["date"],
                    "time": match["time"],
                    "score": match["score"],
                    "text": match["text"],
                    "source": calendar["url"]
                }

                if match["score"]:

                    team["results"].append(
                        item
                    )

                else:

                    team["upcoming"].append(
                        item
                    )

            if team["upcoming"]:

                team["next"] = team[
                    "upcoming"
                ][0]

            if team["results"]:

                team["last"] = team[
                    "results"
                ][-1]

            teams.append(team)

        # ------------------------------------------------
        # 6. DEDUPLICAR EQUIPOS
        # ------------------------------------------------

        unique = {}

        for team in teams:

            key = (
                team["name"]
                + "|"
                + team["competition"]
            )

            if key not in unique:

                unique[key] = team

            else:

                old = unique[key]

                old["upcoming"].extend(
                    team["upcoming"]
                )

                old["results"].extend(
                    team["results"]
                )

                if not old["next"]:
                    old["next"] = team["next"]

                if team["last"]:
                    old["last"] = team["last"]

        teams = list(
            unique.values()
        )

        # ------------------------------------------------
        # 7. RESULTADO
        # ------------------------------------------------

        data = {
            "updated_at":
                datetime.now(
                    timezone.utc
                ).isoformat(),

            "club": {
                "name":
                    "C.F. Vilamajor",

                "fcf_id":
                    CLUB_ID,

                "source":
                    CLUB_URL
            },

            "teams":
                teams
        }

        # ------------------------------------------------
        # 8. NO SOBREESCRIBIR CON DATOS VACÍOS
        # ------------------------------------------------

        if not teams:

            print("")
            print(
                "ERROR: FCF NO HA DEVUELTO EQUIPOS."
            )
            print(
                "NO SE MODIFICA fcf.json."
            )

            await browser.close()

            raise RuntimeError(
                "No se han encontrado equipos/partidos FCF."
            )

        # ------------------------------------------------
        # 9. GUARDAR
        # ------------------------------------------------

        OUTPUT.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        print("")
        print("====================================")
        print(
            f"OK - {len(teams)} EQUIPS"
        )
        print("====================================")

        for team in teams:

            print(
                f"- {team['name']} | "
                f"{team['competition']} | "
                f"{len(team['upcoming'])} propers | "
                f"{len(team['results'])} resultats"
            )

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
