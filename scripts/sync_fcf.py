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
# The club website must show 13 teams. elFutbol currently exposes this extra
# Femenino Benjamín competition in its club-link list; do not import it.
EXCLUDED_COMPETITION_SLUGS = {"femeni-benjami"}


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
      const clean = s => (s || '').replace(/\\s+/g, ' ').trim();
      const dateRe = /\\b(?:[01]?\\d|2\\d|3[01])[-/](?:0?\\d|1[0-2])\\b/;
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
    """Read goals only from explicitly score-labelled DOM nodes.

    The generic-text fallback is intentionally avoided: opponent names can contain
    years (e.g. "Palau 2024") and concatenated scores like "12" must mean 1-2,
    not be mixed with unrelated numbers from the fixture text.
    """
    score_nodes = re.findall(
        r"""<(?:span|div|b|strong)[^>]*(?:class|aria-label)=["'][^"']*(?:score|result|goal|goles|marcador)[^"']*["'][^>]*>\s*(\d{1,2})\s*</(?:span|div|b|strong)>""",
        html, re.I
    )
    if len(score_nodes) >= 2:
        return int(score_nodes[-2]), int(score_nodes[-1])
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
    """Extract completed matches from elFutbol's visible history text.

    elFutbol renders examples like:
      FT03-10 Vilamajor, C.F. A Cardedeu, F.C. B 21[Button: star]
    where 21 means 2-1 and the UI button may be appended to the same text node.
    """
    results = []
    seen = set()
    for i, line in enumerate(lines):
        if not re.search(r"\b(?:FT|FINALIZADO|FINAL|TERMINADO)\s*\d{1,2}[-/]\d{1,2}\b", line, re.I):
            continue

        # Join the current line with a few following DOM text lines because
        # the date, clubs and score can be split across siblings.
        window = []
        for candidate in lines[i:i + 10]:
            if window and re.search(
                r"\b(?:FT|FINALIZADO|FINAL|TERMINADO)\s*\d{1,2}[-/]\d{1,2}\b",
                candidate, re.I
            ):
                break
            if window and re.search(
                r"resultados recientes|pr[oó]ximo partido|clasificaci[oó]n",
                candidate, re.I
            ):
                break
            window.append(candidate)
        raw = clean(" ".join(window))
        date_match = re.search(
            r"\b(?:FT|FINALIZADO|FINAL|TERMINADO)\s*(\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?)",
            raw, re.I
        )
        if not date_match:
            continue
        date_raw = date_match.group(1)
        tail = clean(raw[date_match.end():])

        # Remove UI controls BEFORE looking for the concatenated score.
        tail = re.sub(r"\[Button:\s*star\]", " ", tail, flags=re.I)
        tail = re.sub(r"\bchevron_right\b", " ", tail, flags=re.I)
        tail = re.sub(r"\bstar\b", " ", tail, flags=re.I)
        tail = clean(tail)

        score_match = re.search(r"(?<!\d)(\d{2,3})\s*$", tail)
        if not score_match:
            continue
        token = score_match.group(1)
        # Scores are concatenated: 21 -> 2-1, 100 -> 10-0, 121 -> 12-1.
        if len(token) == 2:
            hs, aws = int(token[0]), int(token[1])
        else:
            hs, aws = int(token[:-1]), int(token[-1])
        if hs > 20 or aws > 20:
            continue

        fixture = clean(tail[:score_match.start()])
        mclub = re.search(r"Vilamajor,?\s*C\.?F\.?\s*[AB]", fixture, re.I)
        if not mclub:
            continue
        club_text = clean(mclub.group(0))
        before = clean(fixture[:mclub.start()])
        after = clean(fixture[mclub.end():])

        # If the Vilamajor name is first, it's the home side; if another
        # club precedes it, that club is home and Vilamajor is away.
        if not before and after:
            home, away = club_text, after
        elif before and not after:
            home, away = before, club_text
        elif before and after:
            # Usually any leading text is a stray icon/round label; use
            # the club's position to retain the opponent on the other side.
            home, away = before, clean(club_text + " " + after)
        else:
            continue

        home = re.sub(r"\s*(?:chevron_right|star)$", "", home, flags=re.I).strip(" -|")
        away = re.sub(r"\s*(?:chevron_right|star)$", "", away, flags=re.I).strip(" -|")
        if not home or not away or len(home) < 3 or len(away) < 3:
            continue
        key = (date_raw, home, away, hs, aws)
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "date": date_raw, "iso": date_iso(date_raw), "time": None,
            "home": home, "away": away, "home_score": hs, "away_score": aws,
            "score": f"{hs}-{aws}", "venue": None, "source": None,
        })
    results.sort(key=lambda x: x.get("iso") or "", reverse=True)
    return results


def parse_history_full_text(body, team_name, source=None):
    """Fallback parser for pages where Playwright splits FT/date/teams/scores
    into separate DOM text lines. Parse each completed-match block from the full
    visible page text, bounded by the next FT marker.
    """
    text = clean(body)
    marker = re.compile(
        r"\b(?:FT|FINALIZADO|FINAL|TERMINADO)\s*(\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?)",
        re.I,
    )
    matches = list(marker.finditer(text))
    # Upcoming fixtures have a clock followed by a date (often with no space,
    # e.g. "18:3010-10"). Stop the final completed result before that next card.
    upcoming_marker = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\s*\d{1,2}[-/]\d{1,2}\b")
    results = []
    seen = set()
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        upcoming = upcoming_marker.search(text, match.end(), end)
        if upcoming:
            end = upcoming.start()
        raw = text[match.end():end]
        raw = re.sub(r"\[Button:\s*star\]", " ", raw, flags=re.I)
        raw = re.sub(r"\b(?:chevron_right|star)\b", " ", raw, flags=re.I)
        raw = clean(raw)

        # Accept explicit scores (2-1 / 2:1) and elFutbol's compact token (21 / 100).
        hs = aws = None
        score = re.search(r"(?:^|\s)(\d{1,2})\s*[-:]\s*(\d{1,2})\s*$", raw)
        if score:
            hs, aws = int(score.group(1)), int(score.group(2))
            fixture = clean(raw[:score.start()])
        else:
            score = re.search(r"(?<!\d)(\d{2,3})\s*$", raw)
            if not score:
                continue
            token = score.group(1)
            if len(token) == 2:
                hs, aws = int(token[0]), int(token[1])
            else:
                hs, aws = int(token[:-1]), int(token[-1])
            if hs > 20 or aws > 20:
                continue
            fixture = clean(raw[:score.start()])

        fixture = clean(re.sub(r"\b(?:Jornada|J)\s*\d+\b", " ", fixture, flags=re.I))
        club_match = re.search(r"Vilamajor,?\s*C\.?F\.?\s*[AB]", fixture, re.I)
        if not club_match:
            continue
        club = clean(club_match.group(0))
        before = clean(fixture[:club_match.start()]).strip(" -|·")
        after = clean(fixture[club_match.end():]).strip(" -|·")
        if not before and after:
            home, away = club, after
        elif before and not after:
            home, away = before, club
        else:
            # Ambiguous team labels: don't invent a home/away assignment.
            continue
        if len(home) < 3 or len(away) < 3:
            continue
        date_raw = match.group(1)
        key = (date_raw, home, away, hs, aws)
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "date": date_raw, "iso": date_iso(date_raw), "time": None,
            "home": home, "away": away, "home_score": hs, "away_score": aws,
            "score": f"{hs}-{aws}", "venue": None, "source": source,
        })
    results.sort(key=lambda x: x.get("iso") or "", reverse=True)
    return results


def parse_history_cards(cards, team_name):
    """Parse completed fixtures from DOM cards, even when body.inner_text splits them across lines."""
    results = []
    seen = set()
    club_match = re.search(r"Vilamajor,\s*C\.F\.\s*[AB]", team_name, re.I)
    club = club_match.group(0) if club_match else "Vilamajor, C.F. A"

    for card in cards or []:
        raw = clean(card.get("text", ""))
        if not raw or not re.search(r"\b(?:FT|FINALIZADO|FINAL|TERMINADO)\s*\d{1,2}[-/]\d{1,2}\b", raw, re.I):
            continue
        date_match = re.search(r"\b(?:FT|FINALIZADO|FINAL|TERMINADO)\s*(\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?)", raw, re.I)
        if not date_match:
            continue
        date_raw = date_match.group(1)
        tail = clean(raw[date_match.end():])
        tail = re.sub(r"\[Button:\s*star\]", " ", tail, flags=re.I)
        tail = re.sub(r"\bchevron_right\b|\bstar\b", " ", tail, flags=re.I)
        tail = clean(tail)

        hs, aws = parse_score_from_html(card.get("html", ""), raw)
        score_match = re.search(r"(?:^|\s)(\d{2,3})\s*$", tail)
        if hs is None and score_match:
            token = score_match.group(1)
            # elFutbol joins home and away goals in one token: 31 => 3-1, 100 => 10-0.
            if len(token) == 2:
                hs, aws = int(token[0]), int(token[1])
            elif len(token) == 3:
                hs, aws = int(token[:-1]), int(token[-1])
            if hs is not None and (hs > 20 or aws > 20):
                hs = aws = None
            if hs is not None:
                tail = clean(tail[:score_match.start()])
        if hs is None or aws is None:
            continue

        # If goals were separate DOM nodes, remove only a trailing numeric score token if present.
        tail = re.sub(r"\s+\d{2,3}\s*$", "", tail).strip()
        fixture_text = clean(re.sub(r"\b(?:Jornada|J)\s*\d+\b", "", tail, flags=re.I))
        idx = fixture_text.lower().find(club.lower())
        if idx < 0:
            # Try the short club label without relying on exact punctuation/spacing.
            mclub = re.search(r"Vilamajor,?\s*C\.?F\.?\s*[AB]", fixture_text, re.I)
            if not mclub:
                continue
            idx, end_idx = mclub.start(), mclub.end()
        else:
            end_idx = idx + len(club)
        before = clean(fixture_text[:idx])
        after = clean(fixture_text[end_idx:])
        if before:
            home, away = before, clean(fixture_text[idx:end_idx])
        elif after:
            home, away = clean(fixture_text[idx:end_idx]), after
        else:
            continue
        # Remove stray UI labels from card text.
        home = re.sub(r"\s*(?:chevron_right|star)$", "", home, flags=re.I).strip()
        away = re.sub(r"\s*(?:chevron_right|star)$", "", away, flags=re.I).strip()
        key = (date_raw, home, away, hs, aws)
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "date": date_raw, "iso": date_iso(date_raw), "time": None,
            "home": home, "away": away, "home_score": hs, "away_score": aws,
            "score": f"{hs}-{aws}", "venue": None, "source": None,
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

    # IMPORTANT: elFutbol opens team pages on the "Detalles" tab. The history
    # of completed matches is rendered in the separate "Partidos" tab, so
    # parsing the initial DOM can find standings/upcoming matches but zero
    # results. Activate that tab before scraping history.
    results_body = body
    results_lines = lines
    try:
        partidos_tab = page.get_by_text("Partidos", exact=True).first
        if await partidos_tab.count():
            await partidos_tab.click(timeout=4000)
            await page.wait_for_timeout(350)
            results_body = await page.locator("body").inner_text()
            results_lines = [clean(x) for x in results_body.splitlines() if clean(x)]
    except Exception as exc:
        print(f"AVISO: no se pudo abrir la pestaña Partidos en {url}: {exc}")

    # Results: read the visible match cards, then text fallbacks from the
    # activated Partidos tab (not the initial Details tab).
    cards = await extract_match_cards(page)
    results = parse_history_cards(cards, name)
    if not results:
        results = parse_history_lines(results_lines, name)
    if not results:
        results = parse_history_full_text(results_body, name, url)
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
        print("FUENTE: elFutbol (pestaña Partidos + tarjetas DOM + texto de respaldo)")
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
                # Ignore the one extra competition link not included in the
                # club's 13-team website roster.
                query = urlparse(url).query.lower()
                if any(f"comp={slug}" in query for slug in EXCLUDED_COMPETITION_SLUGS):
                    continue
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
    result_count = sum(bool(t.get("last")) for t in teams)
    if result_count == 0:
        raise RuntimeError(
            "No se ha podido extraer ningún último resultado. "
            "Se conserva data/fcf.json anterior; no se publica una sincronización vacía."
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
