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
    return re.sub(r"\s+", " ", value or "").strip()


def stat(pattern, text):
    m = re.search(pattern, text, re.I | re.M)
    return int(m.group(1)) if m else None


def date_iso(raw):
    if not raw:
        return None
    m = re.search(r"(\d{1,2})[-/](\d{1,2})(?:[-/](\d{2,4}))?", raw)
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    year = int(m.group(3)) if m.group(3) else datetime.now().year
    if year < 100:
        year += 2000
    today = datetime.now().date()
    try:
        candidate = datetime(year, month, day).date()
        if not m.group(3) and candidate < today and (today - candidate).days > 180:
            year += 1
        return f"{year:04d}-{month:02d}-{day:02d}"
    except ValueError:
        return None


async def extract_match_cards(page):
    """
    Extrae tarjetas de partido del DOM, no solo tablas. En elFutbol el bloque
    'Resultados recientes' suele estar maquetado con elementos/divs y los goles
    en nodos separados; por eso se leen los descendientes de cada tarjeta.
    Solo devuelve un marcador si se identifican dos goles numéricos claros.
    """
    return await page.locator("body").evaluate("""body => {
      const clean = s => (s || '').replace(/\\\\s+/g, ' ').trim();
      const dateRe = /\\\\b(?:[01]?\\\\d|2\\\\d|3[01])[-/](?:0?\\\\d|1[0-2])\\\\b/;
      const isVisible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
      const all = Array.from(body.querySelectorAll('*')).filter(el => isVisible(el));
      const candidates = [];
      for (const el of all) {
        const txt = clean(el.innerText);
        if (!txt || txt.length > 700 || txt.length < 12) continue;
        if (!dateRe.test(txt) || !/vilamajor/i.test(txt)) continue;
        // Prefer the smallest node that contains the date, team names and match state.
        const childHasSame = Array.from(el.children).some(ch => {
          const ct = clean(ch.innerText);
          return ct && ct.length >= 12 && ct.length < txt.length && dateRe.test(ct) && /vilamajor/i.test(ct);
        });
        if (childHasSame) continue;
        candidates.push({text:txt, html:el.outerHTML.slice(0,5000)});
      }
      return candidates;
    }""")


def parse_score_from_html(html, text):
    # Most reliable: elements whose class/aria-label explicitly indicates a score.
    score_nodes = re.findall(
        r"""<(?:span|div|b|strong)[^>]*(?:class|aria-label)=["'][^"']*(?:score|result|goal|goles|marcador)[^"']*["'][^>]*>\s*(\d{1,2})\s*</(?:span|div|b|strong)>""",
        html, re.I
    )
    if len(score_nodes) >= 2:
        return int(score_nodes[-2]), int(score_nodes[-1])

    # Fallback: score is commonly rendered as two separate one/two-digit nodes.
    # Only use the two final standalone numeric nodes if there are no other numeric
    # labels after the team names. Do not split a concatenated "11" into 1-1.
    tail = re.sub(r"\b(?:Jornada|J)\s*\d+\b", " ", text, flags=re.I)
    tail = re.sub(r"\b\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?\b", " ", tail)
    nums = re.findall(r"(?<![\w])(\d{1,2})(?![\w])", tail)
    if len(nums) >= 2:
        # Avoid assigning a score if more than two numbers make the row ambiguous.
        last_two = nums[-2:]
        if all(int(n) <= 20 for n in last_two):
            return int(last_two[0]), int(last_two[1])
    return None, None


def parse_match_text(text):
    text = clean(text)
    # Strip state and jornada markers, keep the date/time and team labels.
    text = re.sub(r"\b(?:FT|FINALIZADO|FINAL|TERMINADO)\b", " ", text, flags=re.I)
    text = re.sub(r"\bJornada\s*\d+\b", " ", text, flags=re.I)
    date_match = re.search(r"\b(\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?)\b", text)
    time_match = re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", text)
    return date_match.group(1) if date_match else None, time_match.group(0) if time_match else None




def parse_history_lines(lines, team_name):
    """Parse visible elFutbol fixture lines such as:
       FT27-09 Vilamajor, C.F. A Mollet U.E., CF. A 12
       FT14-12 Vilassar ... Vilamajor, C.F. A 01
       Scores are rendered as concatenated digits by the site: 12 means 1-2,
       100 means 10-0, 131 means 13-1.
    """
    results = []
    seen = set()
    # Match the club's actual short label rather than the title containing its league.
    club_match = re.search(r"Vilamajor,\s*C\.F\.\s*[AB]", team_name, re.I)
    club = club_match.group(0) if club_match else "Vilamajor, C.F. A"
    for line in lines:
        raw = clean(line)
        if not raw or not re.search(r"\bFT\s*\d{1,2}[-/]\d{1,2}\b", raw, re.I):
            continue
        date_match = re.search(r"\bFT\s*(\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?)", raw, re.I)
        if not date_match:
            continue
        date_raw = date_match.group(1)
        tail = raw[date_match.end():].strip()
        # Last token is the site-rendered score, e.g. 12, 100 or 131.
        score_match = re.search(r"(\d{2,3})\s*$", tail)
        if not score_match:
            continue
        score_token = score_match.group(1)
        if len(score_token) == 2:
            home_score, away_score = int(score_token[0]), int(score_token[1])
        else:
            # Three digits means a double-digit score on the home side (10-20) and
            # a single-digit away score, as in 100 = 10-0 or 131 = 13-1.
            home_score, away_score = int(score_token[:-1]), int(score_token[-1])
            if home_score > 20 or away_score > 20:
                continue
        if home_score > 20 or away_score > 20:
            continue

        fixture_text = clean(tail[:score_match.start()])
        # Drop any leading jornada/time leftovers.
        fixture_text = re.sub(r"^(?:Jornada\s*\d+\s*)", "", fixture_text, flags=re.I).strip()
        idx = fixture_text.lower().find(club.lower())
        if idx < 0:
            # Some team pages abbreviate spacing; allow normalized whitespace comparison.
            norm_fixture = re.sub(r"\s+", " ", fixture_text).lower()
            norm_club = re.sub(r"\s+", " ", club).lower()
            idx = norm_fixture.find(norm_club)
            if idx < 0:
                continue
        before = clean(fixture_text[:idx])
        after = clean(fixture_text[idx + len(club):])
        if before:
            home, away = before, club
        elif after:
            home, away = club, after
        else:
            continue

        key = (date_raw, home, away, home_score, away_score)
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "date": date_raw,
            "iso": date_iso(date_raw),
            "time": None,
            "home": home,
            "away": away,
            "home_score": home_score,
            "away_score": away_score,
            "score": f"{home_score}-{away_score}",
            "venue": None,
            "source": None,
        })
    results.sort(key=lambda x: x.get("iso") or "", reverse=True)
    return results


async def read_team(page, url, link_name):
    response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if not response or response.status >= 400:
        print("AVISO: no se pudo abrir", url)
        return None
    await page.wait_for_timeout(800)
    title = clean(await page.title())
    body = await page.locator("body").inner_text()
    lines = [clean(x) for x in body.splitlines() if clean(x)]

    heading = ""
    try:
        heading = clean(await page.locator("h1").first.inner_text(timeout=1500))
    except Exception:
        pass
    name = heading or link_name or title.split("|")[0]
    if "vilamajor" not in (name + " " + url).lower():
        return None

    position = stat(r"\b(\d{1,2})\s*(?:º|°|ª)\s*\n\s*POSICI[ÓO]N", body)
    played = stat(r"\b(\d{1,3})\s*\n\s*PARTIDOS", body)
    points = stat(r"\b(\d{1,3})\s*\n\s*PUNTOS", body)
    standing = {}
    if any(v is not None for v in (position, played, points)):
        standing = {"position": position, "played": played, "points": points}
    gf_gc = re.search(r"\bGF\s*:\s*GC\s*\n\s*(\d+)\s*:\s*(\d+)", body, re.I)
    if gf_gc:
        standing["gf"], standing["ga"] = int(gf_gc.group(1)), int(gf_gc.group(2))

    # Upcoming match: use the explicit 'Próximo partido' block and nearby text.
    upcoming = []
    for i, line in enumerate(lines):
        if re.search(r"pr[oó]ximo partido", line, re.I):
            block = " ".join(lines[i:i + 18])
            date_match = re.search(r"\b(\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?)\b", block)
            time_match = re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", block)
            # Locate the two team names in the block without inventing them.
            names = []
            for candidate in lines[i + 1:i + 14]:
                if "vilamajor" in candidate.lower() or (
                    len(candidate) > 5 and not re.search(r"jornada|ver m[aá]s|pr[oó]ximo|partido", candidate, re.I)
                    and not re.search(r"\d{1,2}[-/]\d{1,2}", candidate)
                ):
                    if candidate not in names and not re.fullmatch(r"\d+", candidate):
                        names.append(candidate)
                if len(names) >= 2:
                    break
            upcoming.append({
                "date": date_match.group(1) if date_match else None,
                "iso": date_iso(date_match.group(1)) if date_match else None,
                "time": time_match.group(0) if time_match else None,
                "home": names[0] if len(names) > 0 else None,
                "away": names[1] if len(names) > 1 else None,
                "venue": None,
                "source": url,
            })
            break

    # Results: elFutbol renders completed fixtures as visible text lines, e.g.
    # "FT27-09 Vilamajor, C.F. A Mollet U.E., CF. A 12". The final score is
    # concatenated (12 = 1-2, 100 = 10-0); parse these lines directly.
    results = parse_history_lines(lines, name)
    for result in results:
        result["source"] = url

    results.sort(key=lambda x: x.get("iso") or "", reverse=True)
    upcoming.sort(key=lambda x: x.get("iso") or "")
    category = title
    return {
        "name": name,
        "category": category,
        "competition": category,
        "url": url,
        "next": upcoming[0] if upcoming else None,
        "last": results[0] if results else None,
        # Compatibility with the existing index.html card renderers:
        "next_match": upcoming[0] if upcoming else None,
        "last_match": results[0] if results else None,
        "standing": standing,
        "upcoming": upcoming,
        "results": results,
    }


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale="es-ES")
        print("FUENTE ÚNICA: elFutbol")
        print("Abriendo club:", CLUB_URL)
        response = await page.goto(CLUB_URL, wait_until="domcontentloaded", timeout=60000)
        if not response or response.status >= 400:
            raise RuntimeError("No se puede abrir la ficha del club en elFutbol.")
        await page.wait_for_timeout(1200)

        links = await page.locator("a[href]").evaluate_all("""els => els.map(a => ({
          url: a.href, name: (a.innerText || a.textContent || '').trim()
        }))""")
        team_urls = {}
        for link in links:
            url = urldefrag(link["url"])[0]
            if urlparse(url).netloc.endswith("elfutbol.co") and "/es/team/" in url:
                team_urls[url] = clean(link["name"])
        print("ENLACES DE EQUIPOS:", len(team_urls))
        teams = []
        for i, (url, link_name) in enumerate(sorted(team_urls.items()), 1):
            try:
                item = await read_team(page, url, link_name)
                if item:
                    teams.append(item)
                    print(
                        f"{i}/{len(team_urls)} {item['name']}: "
                        f"próximo={'sí' if item['next'] else 'no'}, "
                        f"resultado={'sí' if item['last'] else 'no'}, "
                        f"clasificación={'sí' if item['standing'] else 'no'}"
                    )
            except Exception as exc:
                print("ERROR leyendo", url, repr(exc))
        await browser.close()

    if len(teams) < MIN_TEAMS:
        raise RuntimeError(
            f"Solo se han leído {len(teams)} equipos. "
            "No se sobrescribe data/fcf.json."
        )

    data = {
        "source": "elFutbol",
        "source_url": CLUB_URL,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "club": {"name": "C.F. Vilamajor", "source": CLUB_URL},
        "teams": teams,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUTPUT)
    print(
        f"OK: equipos {len(teams)} próximos {sum(bool(t['next']) for t in teams)} "
        f"últimos resultados {sum(bool(t['last']) for t in teams)} "
        f"clasificaciones {sum(bool(t['standing']) for t in teams)}"
    )


if __name__ == "__main__":
    asyncio.run(main())
