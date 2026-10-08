import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# ============================================================
# C.F. VILAMAJOR
# Sincronizador FCF
# ============================================================

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DATA_FILE = DATA_DIR / "fcf.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/154.0 Safari/537.36"
    ),
    "Accept-Language": "ca-ES,ca;q=0.9,es;q=0.8",
}

CLUB_NAME = "VILAMAJOR, C.F."


# ------------------------------------------------------------
# Datos de respaldo
# ------------------------------------------------------------

DEFAULT_DATA = {
    "club": "C.F. Vilamajor",
    "updated_at": None,
    "source": "FCF",
    "teams": []
}


# ------------------------------------------------------------
# Utilidades
# ------------------------------------------------------------

def load_existing():
    if not DATA_FILE.exists():
        return DEFAULT_DATA.copy()

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_DATA.copy()


def save_data(data):
    data["updated_at"] = datetime.now(timezone.utc).isoformat()

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


def get(url):
    print(f"GET {url}")

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()
    return response.text


def clean_text(value):
    if not value:
        return ""

    return re.sub(
        r"\s+",
        " ",
        value
    ).strip()


# ------------------------------------------------------------
# Detectar enlaces relacionados con Vilamajor
# ------------------------------------------------------------

def find_fcf_links(html, base_url):
    soup = BeautifulSoup(html, "html.parser")

    links = []

    for a in soup.find_all("a", href=True):
        text = clean_text(a.get_text(" ", strip=True))
        href = urljoin(base_url, a["href"])

        combined = f"{text} {href}".upper()

        if "VILAMAJOR" in combined:
            links.append({
                "name": text,
                "url": href
            })

    # Eliminar duplicados
    unique = {}

    for item in links:
        unique[item["url"]] = item

    return list(unique.values())


# ------------------------------------------------------------
# Extraer posibles equipos
# ------------------------------------------------------------

def extract_team_links(html, base_url):
    links = find_fcf_links(
        html,
        base_url
    )

    teams = []

    for link in links:
        name = clean_text(link["name"])

        if not name:
            continue

        # Evitamos enlaces genéricos del club
        generic = [
            "VILAMAJOR",
            "VILAMAJOR, C.F.",
            "C.F. VILAMAJOR"
        ]

        if name.upper() in generic:
            continue

        teams.append({
            "name": name,
            "url": link["url"]
        })

    return teams


# ------------------------------------------------------------
# Buscar información de partidos
# ------------------------------------------------------------

def extract_matches(html):
    soup = BeautifulSoup(html, "html.parser")

    text = clean_text(
        soup.get_text(" ", strip=True)
    )

    matches = []

    # Detectamos bloques que contengan datos habituales FCF
    # sin depender de una clase CSS concreta.
    patterns = [
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        r"(\d{1,2}\.\d{1,2}H)",
        r"Jornada\s+(\d+)"
    ]

    dates = re.findall(
        patterns[0],
        text,
        flags=re.IGNORECASE
    )

    hours = re.findall(
        patterns[1],
        text,
        flags=re.IGNORECASE
    )

    jornadas = re.findall(
        patterns[2],
        text,
        flags=re.IGNORECASE
    )

    # Guardamos la información encontrada de forma segura.
    if dates or hours or jornadas:
        matches.append({
            "date": dates[0] if dates else "",
            "time": hours[0] if hours else "",
            "round": jornadas[0] if jornadas else ""
        })

    return matches


# ------------------------------------------------------------
# Sincronización
# ------------------------------------------------------------

def sync():
    existing = load_existing()

    # --------------------------------------------------------
    # IMPORTANTE
    #
    # La FCF puede cambiar la estructura de sus páginas.
    # Por eso NO borramos los datos existentes si una consulta
    # falla.
    # --------------------------------------------------------

    # Página pública general de FCF.
    # El resto de URLs se descubre a partir de enlaces.
    fcf_home = "https://www.fcf.cat/"

    try:
        home_html = get(fcf_home)
    except Exception as e:
        print("No se ha podido conectar con la FCF:")
        print(e)

        existing["sync_status"] = "error"
        existing["sync_error"] = str(e)

        save_data(existing)
        return

    discovered = extract_team_links(
        home_html,
        fcf_home
    )

    print(
        f"Enlaces Vilamajor encontrados: {len(discovered)}"
    )

    # --------------------------------------------------------
    # Si todavía no hemos podido descubrir los equipos,
    # conservamos los datos existentes.
    # --------------------------------------------------------

    if not discovered:
        print(
            "No se han podido descubrir equipos "
            "en esta consulta."
        )

        existing["sync_status"] = "warning"
        existing["sync_error"] = (
            "La FCF no ha devuelto enlaces de equipos."
        )

        save_data(existing)
        return

    teams = []

    for item in discovered:

        team = {
            "name": item["name"],
            "fcf_url": item["url"],
            "next_match": None,
            "last_result": None,
            "standings": None
        }

        try:
            team_html = get(item["url"])

            matches = extract_matches(
                team_html
            )

            if matches:
                team["fcf_matches_detected"] = matches

            team["status"] = "ok"

        except Exception as e:
            print(
                f"Error leyendo {item['name']}: {e}"
            )

            team["status"] = "error"

        teams.append(team)

    # --------------------------------------------------------
    # Guardar
    # --------------------------------------------------------

    result = {
        "club": "C.F. Vilamajor",
        "updated_at": None,
        "source": "FCF",
        "season": "2026/27",
        "sync_status": "ok",
        "teams": teams
    }

    save_data(result)

    print(
        f"Sincronización terminada. "
        f"{len(teams)} equipos procesados."
    )


if __name__ == "__main__":
    sync()
