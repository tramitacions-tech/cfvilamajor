import json
import os
import urllib.request
from bs4 import BeautifulSoup

JSON_PATH = "data/fcf.json"

# Les 14 categories del C.F. Vilamajor amb les seves URLs exactes de la FCF
CATEGORIAS = [
    {"id": "categoria_33085", "nombre": "Categoria 33085", "url": "https://www.fcf.cat/ca/clubs/1096/categories/33085"},
    {"id": "categoria_39706", "nombre": "Categoria 39706", "url": "https://www.fcf.cat/ca/clubs/1096/categories/39706"},
    {"id": "categoria_18499958", "nombre": "Categoria 18499958", "url": "https://www.fcf.cat/ca/clubs/1096/categories/18499958"},
    {"id": "categoria_58515341", "nombre": "Categoria 58515341", "url": "https://www.fcf.cat/ca/clubs/1096/categories/58515341"},
    {"id": "categoria_58730723", "nombre": "Categoria 58730723", "url": "https://www.fcf.cat/ca/clubs/1096/categories/58730723"},
    {"id": "categoria_51348999", "nombre": "Categoria 51348999", "url": "https://www.fcf.cat/ca/clubs/1096/categories/51348999"},
    {"id": "categoria_51349033", "nombre": "Categoria 51349033", "url": "https://www.fcf.cat/ca/clubs/1096/categories/51349033"},
    {"id": "categoria_47860945", "nombre": "Categoria 47860945", "url": "https://www.fcf.cat/ca/clubs/1096/categories/47860945"},
    {"id": "categoria_51436287", "nombre": "Categoria 51436287", "url": "https://www.fcf.cat/ca/clubs/1096/categories/51436287"},
    {"id": "categoria_47860913", "nombre": "Categoria 47860913", "url": "https://www.fcf.cat/ca/clubs/1096/categories/47860913"},
    {"id": "categoria_47860604", "nombre": "Categoria 47860604", "url": "https://www.fcf.cat/ca/clubs/1096/categories/47860604"},
    {"id": "categoria_47860528", "nombre": "Categoria 47860528", "url": "https://www.fcf.cat/ca/clubs/1096/categories/47860528"},
    {"id": "categoria_58663444", "nombre": "Categoria 58663444", "url": "https://www.fcf.cat/ca/clubs/1096/categories/58663444"},
    {"id": "categoria_58663290", "nombre": "Categoria 58663290", "url": "https://www.fcf.cat/ca/clubs/1096/categories/58663290"}
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def fetch_html(url):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.read().decode('utf-8')
    except Exception as e:
        print(f"[ERROR] No s'ha pogut obtenir la URL {url}: {e}")
        return None

def parse_data(soup):
    resultados = []
    clasificacion = []
    plantilla = []

    # 1. Extreure taula de resultats / partits
    tabla_res = soup.find('table', class_='acta-table') or soup.find('table', class_='t-resultados') or soup.find('table', class_='table-striped')
    if tabla_res:
        for fila in tabla_res.find_all('tr'):
            cols = [c.get_text(strip=True) for c in fila.find_all(['td', 'th'])]
            if cols:
                resultados.append(" ".join(cols))

    # 2. Extreure taula de classificació
    tabla_clas = soup.find('table', class_='t-clasificacio') or soup.find('table', class_='table-clasificacio')
    if tabla_clas:
        for fila in tabla_clas.find_all('tr')[1:]:
            cols = fila.find_all('td')
            if len(cols) >= 3:
                clasificacion.append({
                    "posicion": cols[0].get_text(strip=True),
                    "equipo": cols[1].get_text(strip=True),
                    "puntos": cols[2].get_text(strip=True)
                })

    # 3. Extreure plantilla / jugadors
    for el in soup.select('.jugador-card, .plantilla-jugador, .jugador, .fitxa-jugador'):
        nom = el.get_text(strip=True)
        if nom:
            plantilla.append({"nombre": nom})

    return resultados, clasificacion, plantilla

def main():
    datos_previos = {}
    if os.path.exists(JSON_PATH):
        try:
            with open(JSON_PATH, 'r', encoding='utf-8') as f:
                datos_previos = json.load(f)
        except Exception:
            pass

    nuevos_datos = {}

    for cat in CATEGORIAS:
        cat_key = cat["id"]
        print(f"Sincronitzant {cat['nombre']} ({cat_key})...")
        html = fetch_html(cat["url"])

        if not html:
            # Conservem dades anteriors en cas d'error de connexió
            if cat_key in datos_previos:
                nuevos_datos[cat_key] = datos_previos[cat_key]
            continue

        soup = BeautifulSoup(html, 'html.parser')
        res, clas, plan = parse_data(soup)

        # Si l'extracció no retorna res, preservem les dades existents per evitar JSONs buits
        nuevos_datos[cat_key] = {
            "nombre": cat["nombre"],
            "url_fcf": cat["url"],
            "resultados": res if res else datos_previos.get(cat_key, {}).get("resultados", []),
            "clasificacion": clas if clas else datos_previos.get(cat_key, {}).get("clasificacion", []),
            "plantilla": plan if plan else datos_previos.get(cat_key, {}).get("plantilla", [])
        }

    os.makedirs(os.path.dirname(JSON_PATH), exist_ok=True)
    with open(JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(nuevos_datos, f, ensure_ascii=False, indent=2)

    print("Sincronització finalitzada correctament.")

if __name__ == "__main__":
    main()
