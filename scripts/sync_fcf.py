import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urldefrag, urlparse

from playwright.async_api import async_playwright

BASE = "https://elfutbol.co"
CLUB_URL = f"{BASE}/es/club/vilamajor-cf"
OUTPUT = Path("data/fcf.json")
MIN_TEAMS = 10


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def number(pattern, text):
    match = re.search(pattern, text, re.I | re.M)
    return int(match.group(1)) if match else None


def parse_team(name, url, title, body):
    lines = [clean(line) for line in body.splitlines() if clean(line)]
    text = "\n".join(lines)

    position = number(
        r"(\d{1,2})\s*(?:º|ª|°)\s*\n\s*POSICI[ÓO]N", text
    )
    played = number(r"(\d{1,3})\s*\n\s*PARTIDOS", text)
    points = number(r"(\d{1,3})\s*\n\s*PUNTOS", text)

    standing = {}
    if any(v is not None for v in (position, played, points)):
        standing = {
            "position": position,
            "played": played,
            "points": points,
        }

    # Only retain match information when it can be identified.
    upcoming = None
    last = None

    # Extract date/time candidates from the relevant match sections.
    for i, line in enumerate(lines):
        if re.search(r"próximo partido|proximo partido", line, re.I):
            block = " ".join(lines[i:i + 12])
            date = re.search(
                r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{4}|"
                r"\d{4}-\d{2}-\d{2})\b", block
            )
            time = re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", block)
            upcoming = {
                "date": date.group(1) if date else None,
                "time": time.group(0) if time else None,
                "home": None,
                "away": None,
                "venue": None,
                "url": url,
            }
            break

    return {
        "name": name,
        "category": title,
        "competition": title,
        "url": url,
        "next": upcoming,
        "last": last,
        "standing": standing,
        "upcoming": [upcoming] if upcoming else [],
        "results": [],
    }


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale="es-ES")

        print("Abriendo elFutbol:", CLUB_URL)
        await page.goto(
            CLUB_URL, wait_until="domcontentloaded", timeout=60000
        )
        await page.wait_for_timeout(3000)

        # Find all team pages linked from the club page.
        links = await page.locator("a").evaluate_all("""
            els => els.map(a => ({
                url: a.href,
                name: (a.innerText || a.textContent || '').trim()
            }))
        """)

        team_urls = {}
        for link in links:
            url = urldefrag(link["url"])[0]
            if (
                urlparse(url).netloc.endswith("elfutbol.co")
                and "/es/team/" in url
            ):
                team_urls[url] = clean(link["name"])

        print("Equipos encontrados:", len(team_urls))

        teams = []
        for url, link_name in team_urls.items():
            team_page = await browser.new_page(locale="es-ES")
            try:
                await team_page.goto(
                    url, wait_until="domcontentloaded", timeout=60000
                )
                await team_page.wait_for_timeout(1000)

                title = clean(await team_page.title())
                body = await team_page.locator("body").inner_text()

                heading = ""
                try:
                    heading = clean(
                        await team_page.locator("h1").first.inner_text(
                            timeout=2000
                        )
                    )
                except Exception:
                    pass

                name = heading or link_name or title.split("|")[0]
                if "vilamajor" not in (name + " " + url).lower():
                    continue

                team = parse_team(name, url, title, body)
                teams.append(team)

                print(
                    f"Leído: {name} | "
                    f"clasificación: {bool(team['standing'])} | "
                    f"próximo: {bool(team['next'])}"
                )
            except Exception as exc:
                print("Error leyendo", url, exc)
            finally:
                await team_page.close()

        await browser.close()

    # Safety: keep the existing JSON if discovery is incomplete.
    if len(teams) < MIN_TEAMS:
        raise RuntimeError(
            f"Solo se detectaron {len(teams)} equipos. "
            "No se modifica data/fcf.json."
        )

    data = {
        "source": "elFutbol",
        "source_url": CLUB_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "teams": teams,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUTPUT.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(OUTPUT)

    print(f"OK: {len(teams)} equipos guardados en {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
