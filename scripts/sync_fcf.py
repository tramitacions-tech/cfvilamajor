import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright


BASE_URL = "https://www.fcf.cat"
CLUB_ID = "1096"
CLUB_URL = f"{BASE_URL}/ca/clubs/{CLUB_ID}/categories/39706"

OUTPUT = Path("data/fcf.json")
DEBUG = Path("data/fcf-debug.json")


def clean(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def absolute(url):
    return urljoin(BASE_URL, url)


def is_fcf_url(url):
    return isinstance(url, str) and (
        "fcf.cat" in url
        or url.startswith("/")
    )


def find_strings(obj, results=None):
    if results is None:
        results = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str):
                results.append((str(key), clean(value)))
            else:
                find_strings(value, results)

    elif isinstance(obj, list):
        for item in obj:
            find_strings(item, results)

    return results


def find_urls(obj, urls=None):
    if urls is None:
        urls = set()

    if isinstance(obj, dict):
        for key, value in obj.items():

            if isinstance(value, str):
                if (
                    "fcf.cat" in value
                    or "/ca/" in value
                    or "/clubs/" in value
                    or "/competicio/" in value
                    or "/acta/" in value
                ):
                    urls.add(absolute(value))

            else:
                find_urls(value, urls)

    elif isinstance(obj, list):
        for item in obj:
            find_urls(item, urls)

    return urls


def possible_team_name(text):
    text = clean(text)

    if not text:
        return False

    upper = text.upper()

    if len(text) > 120:
        return False

    forbidden = [
        "FEDERACIÓ",
        "FEDERACION",
        "CONTACTE",
        "CONTACT",
        "INICI",
        "CERCAR",
        "CERCA",
        "COOKIE",
        "PRIVACITAT",
        "POLÍTICA",
        "VILAMAJOR, C.F. (VILAMAJOR, C.F.)",
    ]

    if any(x in upper for x in forbidden):
        return False

    football_words = [
        "VILAMAJOR",
        "JUVENIL",
        "CADET",
        "INFANTIL",
        "ALEVÍ",
        "BENJAMÍ",
        "PREBENJAMÍ",
        "AMATEUR",
        "FEMENÍ",
        "FEMENI",
        "SENIOR",
        "SÈNIOR",
        "FUTBOL",
        "FÚTBOL",
    ]

    return any(word in upper for word in football_words)


def extract_date(text):
    patterns = [
        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
        r"\b\d{1,2}-\d{1,2}-\d{4}\b",
        r"\b\d{1,2}\.\d{1,2}\.\d{4}\b",
    ]

    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(0)

    return ""


def extract_time(text):
    m = re.search(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", text)
    return m.group(0) if m else ""


def extract_score(text):
    patterns = [
        r"\b(\d{1,2})\s*[-–]\s*(\d{1,2})\b",
        r"\b(\d{1,2})\s*:\s*(\d{1,2})\b",
    ]

    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return f"{m.group(1)}-{m.group(2)}"

    return ""


async def main():

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    network_json = []
    network_urls = set()

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

        async def capture_response(response):

            url = response.url

            if "fcf.cat" not in url:
                return

            network_urls.add(url)

            content_type = response.headers.get(
                "content-type",
                ""
            ).lower()

            if (
                "json" in content_type
                or "javascript" in content_type
                or "text" in content_type
            ):
                try:
                    body = await response.text()

                    if len(body) < 5000000:
                        network_json.append({
                            "url": url,
                            "content_type": content_type,
                            "body": body
                        })

                except Exception:
                    pass

        page.on(
            "response",
            lambda response: asyncio.create_task(
                capture_response(response)
            )
        )

        print("====================================")
        print("FCF VILAMAJOR - SINCRONITZACIÓ")
        print("====================================")
        print(CLUB_URL)

        try:
            await page.goto(
                CLUB_URL,
                wait_until="domcontentloaded",
                timeout=90000
            )
        except Exception as exc:
            print("Error inicial:", exc)

        # FCF necessita temps per carregar les dades
        await page.wait_for_timeout(12000)

        # Intentem forçar scroll per activar càrrega lazy
        await page.evaluate(
            """
            async () => {
                for (let i = 0; i < 8; i++) {
                    window.scrollTo(
                        0,
                        document.body.scrollHeight
                    );
                    await new Promise(
                        r => setTimeout(r, 800)
                    );
                }
                window.scrollTo(0, 0);
            }
            """
        )

        await page.wait_for_timeout(5000)

        html = await page.content()
        visible_text = clean(
            await page.locator("body").inner_text()
        )

        # Tots els links que el navegador veu
        anchors = []

        for a in await page.locator("a").all():

            try:

                href = await a.get_attribute("href")
                txt = clean(
                    await a.inner_text()
                )

                if href:
                    anchors.append({
                        "text": txt,
                        "url": absolute(href)
                    })

            except Exception:
                pass

        # Guardem informació de diagnòstic
        debug = {
            "generated_at": datetime.now().astimezone().isoformat(),
            "club_url": CLUB_URL,
            "visible_text": visible_text,
            "anchors": anchors,
            "network_urls": sorted(network_urls),
            "network_responses": network_json
        }

        DEBUG.write_text(
            json.dumps(
                debug,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        print(
            f"Respostes FCF capturades: {len(network_json)}"
        )

        print(
            f"URLs FCF capturades: {len(network_urls)}"
        )

        print(
            f"Enllaços visibles: {len(anchors)}"
        )

        # --------------------------------
        # BUSQUEM EQUIPS
        # --------------------------------

        team_candidates = []

        # 1. Enllaços visibles
        for item in anchors:

            txt = clean(item["text"])
            url = item["url"]

            if (
                possible_team_name(txt)
                and (
                    "/clubs/" in url
                    or "/categories/" in url
                )
            ):

                team_candidates.append({
                    "name": txt,
                    "url": url
                })

        # 2. Text visible
        for line in visible_text.splitlines():

            line = clean(line)

            if possible_team_name(line):

                team_candidates.append({
                    "name": line,
                    "url": ""
                })

        # 3. Respostes JSON / text
        for response in network_json:

            body = response["body"]

            # noms que continguin Vilamajor o categories
            for match in re.finditer(
                r".{0,100}(?:VILAMAJOR|JUVENIL|CADET|INFANTIL|ALEVÍ|BENJAMÍ|PREBENJAMÍ|FEMENÍ|SENIOR|SÈNIOR).{0,150}",
                body,
                re.IGNORECASE
            ):

                snippet = clean(match.group(0))

                if possible_team_name(snippet):

                    team_candidates.append({
                        "name": snippet,
                        "url": response["url"]
                    })

        # --------------------------------
        # DEDUPLICAR
        # --------------------------------

        teams = []
        seen = set()

        for candidate in team_candidates:

            name = clean(candidate["name"])

            # Simplifiquem snippets massa llargs
            if len(name) > 100:

                parts = re.split(
                    r"[|{}[\],\"]+",
                    name
                )

                useful = []

                for part in parts:

                    part = clean(part)

                    if possible_team_name(part):
                        useful.append(part)

                if useful:
                    name = max(
                        useful,
                        key=len
                    )

            key = name.upper()

            if (
                key
                and key not in seen
                and len(name) > 2
            ):

                seen.add(key)

                teams.append({
                    "name": name,
                    "category": name,
                    "competition": "",
                    "venue": "",
                    "url": candidate.get("url", ""),
                    "next": None,
                    "last": None,
                    "upcoming": [],
                    "results": [],
                    "standing": {}
                })

        # --------------------------------
        # SI NO HAY EQUIPS:
        # NO BORRAR DATOS Y DEJAR DEBUG
        # --------------------------------

        if not teams:

            print("")
            print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            print("NO S'HAN TROBAT EQUIPS")
            print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            print("")
            print(
                "El fitxer data/fcf-debug.json "
                "conté la informació capturada."
            )

            # Mantener estructura válida
            data = {
                "updated_at": "",
                "club": {
                    "name": "C.F. Vilamajor",
                    "fcf_id": CLUB_ID,
                    "source": CLUB_URL
                },
                "teams": []
            }

            OUTPUT.write_text(
                json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2
                ),
                encoding="utf-8"
            )

            await browser.close()
            return

        # --------------------------------
        # DATOS ENCONTRADOS
        # --------------------------------

        data = {
            "updated_at":
                datetime.now().astimezone().isoformat(),

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

        print("")
        print(
            f"OK: {len(teams)} equips trobats."
        )

        for team in teams:
            print(
                " -",
                team["name"]
            )

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
