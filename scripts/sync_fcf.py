import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright


BASE_URL = "https://www.fcf.cat"
CLUB_ID = "1096"

CLUB_URLS = [
    f"{BASE_URL}/ca/clubs/{CLUB_ID}",
    f"{BASE_URL}/ca/clubs/{CLUB_ID}/categories/39706",
]

OUTPUT = Path("data/fcf.json")


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def absolute(url):
    return urljoin(BASE_URL, url)


def valid_fcf(url):
    return url.startswith(BASE_URL)


def valid_score(value):
    """
    SOLO acepta marcadores de 0-0 a 30-30.
    Esto elimina basura como teléfonos/direcciones.
    """
    if not value:
        return False

    m = re.fullmatch(r"\s*(\d{1,2})\s*[-–:]\s*(\d{1,2})\s*", value)

    if not m:
        return False

    a = int(m.group(1))
    b = int(m.group(2))

    return 0 <= a <= 30 and 0 <= b <= 30


def score(value):
    if valid_score(value):
        return re.sub(r"\s+", "", value).replace(":", "-").replace("–", "-")
    return ""


def date_value(value):
    m = re.search(
        r"\b(\d{2})[./-](\d{2})[./-](\d{4})\b",
        value or ""
    )

    if not m:
        return ""

    return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"


def time_value(value):
    m = re.search(
        r"\b([01]\d|2[0-3])[:.]([0-5]\d)\b",
        value or ""
    )

    if not m:
        return ""

    return f"{m.group(1)}:{m.group(2)}"


def team_name(value):
    value = clean(value)

    if not value:
        return False

    upper = value.upper()

    if "VILAMAJOR" in upper:
        return True

    return False


async def get_links(page):
    result = set()

    for a in await page.locator("a").all():
        try:
            href = await a.get_attribute("href")

            if not href:
                continue

            url = absolute(href)

            if not valid_fcf(url):
                continue

            low = url.lower()

            if (
                "calendari-equip" in low
                or "/competicio/acta/" in low
            ):
                result.add(url)

        except Exception:
            pass

    return result


async def extract_real_match_rows(page):

    matches = []

    # -------------------------------------------------
    # BUSCAMOS TABLAS REALES
    # -------------------------------------------------

    tables = await page.locator("table").all()

    for table in tables:

        rows = await table.locator("tr").all()

        for row in rows:

            cells = await row.locator(
                "th,td"
            ).all()

            values = []

            for cell in cells:

                try:
                    value = clean(
                        await cell.inner_text()
                    )

                    if value:
                        values.append(value)

                except Exception:
                    pass

            if len(values) < 2:
                continue

            row_text = " | ".join(values)

            # -----------------------------------------
            # SOLO FILAS QUE CONTIENEN VILAMAJOR
            # -----------------------------------------

            if not team_name(row_text):
                continue

            # -----------------------------------------
            # RESULTADO:
            # SOLO CELDAS INDIVIDUALES
            # -----------------------------------------

            found_score = ""

            for value in values:

                s = score(value)

                if s:
                    found_score = s
                    break

            found_date = ""

            for value in values:

                d = date_value(value)

                if d:
                    found_date = d
                    break

            found_time = ""

            for value in values:

                t = time_value(value)

                if t:
                    found_time = t
                    break

            # -----------------------------------------
            # EQUIPOS
            # -----------------------------------------

            teams = [
                v for v in values
                if team_name(v)
            ]

            # Necesitamos al menos Vilamajor.
            # Si no hay segundo equipo, no inventamos.
            if not teams:
                continue

            matches.append({
                "teams": teams,
                "date": found_date,
                "time": found_time,
                "score": found_score,
                "raw": values
            })

    return matches


async def extract_page_data(page, url):

    print("FCF:", url)

    try:

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=90000
        )

    except Exception as exc:

        print("Error:", exc)
        return []

    await page.wait_for_timeout(5000)

    return await extract_real_match_rows(page)


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

        print("")
        print("===================================")
        print("FCF VILAMAJOR 2026/27")
        print("===================================")
        print("")

        # -------------------------------------------------
        # 1. ENTRAMOS EN LA PÁGINA OFICIAL DEL CLUB
        # -------------------------------------------------

        calendar_links = set()

        for club_url in CLUB_URLS:

            try:

                await page.goto(
                    club_url,
                    wait_until="domcontentloaded",
                    timeout=90000
                )

                await page.wait_for_timeout(8000)

                found = await get_links(page)

                calendar_links.update(found)

            except Exception as exc:

                print(
                    "Error club:",
                    club_url,
                    exc
                )

        print(
            "Calendarios/actas encontrados:",
            len(calendar_links)
        )

        # -------------------------------------------------
        # 2. LEER SOLO PÁGINAS FCF
        # -------------------------------------------------

        all_matches = []

        for url in sorted(calendar_links):

            rows = await extract_page_data(
                page,
                url
            )

            for row in rows:

                row["source"] = url

                all_matches.append(row)

        # -------------------------------------------------
        # 3. DEDUPLICAR
        # -------------------------------------------------

        unique = {}

        for match in all_matches:

            key = (
                tuple(match["teams"]),
                match["date"],
                match["time"],
                match["score"]
            )

            unique[key] = match

        all_matches = list(
            unique.values()
        )

        print(
            "Partidos reales encontrados:",
            len(all_matches)
        )

        # -------------------------------------------------
        # 4. CONSTRUIR UN ÚNICO EQUIPO SOLO SI EXISTE
        # -------------------------------------------------

        team = {
            "name": "C.F. Vilamajor",
            "category": "",
            "competition": "",
            "venue": "",
            "url": CLUB_URLS[0],
            "next": None,
            "last": None,
            "upcoming": [],
            "results": [],
            "standing": {}
        }

        for match in all_matches:

            item = {
                "teams": match["teams"],
                "date": match["date"],
                "time": match["time"],
                "score": match["score"],
                "source": match["source"]
            }

            # IMPORTANTÍSIMO:
            #
            # SIN MARCADOR = PRÓXIMO
            # CON MARCADOR = RESULTADO
            #
            # Nunca intentamos deducirlo de otro texto.

            if match["score"]:

                team["results"].append(item)

            else:

                team["upcoming"].append(item)

        if team["results"]:

            team["last"] = team["results"][-1]

        if team["upcoming"]:

            team["next"] = team["upcoming"][0]

        # -------------------------------------------------
        # 5. SI NO HAY DATOS REALES, NO PISAR JSON
        # -------------------------------------------------

        if not all_matches:

            print("")
            print("NO SE HAN ENCONTRADO PARTIDOS REALES.")
            print("NO SE MODIFICA data/fcf.json.")
            print("")

            await browser.close()

            raise RuntimeError(
                "FCF no ha devuelto filas de partidos."
            )

        # -------------------------------------------------
        # 6. JSON
        # -------------------------------------------------

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
                    f"{BASE_URL}/ca"
            },

            "teams": [
                team
            ]
        }

        OUTPUT.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        print("")
        print("===================================")
        print("OK")
        print(
            "Partidos:",
            len(all_matches)
        )
        print(
            "Resultados:",
            len(team["results"])
        )
        print(
            "Próximos:",
            len(team["upcoming"])
        )
        print("===================================")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
