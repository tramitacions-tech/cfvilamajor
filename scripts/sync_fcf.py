import json
import os
import urllib.request
from bs4 import BeautifulSoup

JSON_PATH = "data/fcf.json"

# Las 14 categorías del C.F. Vilamajor con sus enlaces oficiales de la FCF
CATEGORIAS = [
    {"id": "primer_equipo", "nombre": "Primer Equip", "url": "https://www.fcf.cat/equip/2425/11/cf-vilamajor-a"},
    {"id": "juvenil_a", "nombre": "Juvenil A", "url": "https://www.fcf.cat/equip/2425/11/cf-vilamajor-a-juvenil"},
    # Comprobar e incluir aquí las URLs correspondientes a las 14 categorías
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
        print(f"[ERROR] No se pudo obtener la URL {url}: {e}")
        return None

def parse_data(soup):
    resultados = []
    clasificacion = []
    plantilla = []

    # Extraer tabla de resultados
    tabla_res = soup.find('table', class_='acta-table') or soup.find('table', class_='t-resultados')
    if tabla_res:
        for fila in tabla_res.find_all('tr'):
            cols = [c.get_text(strip=True) for c in fila.find_all(['td', 'th'])]
            if cols:
                resultados.append(" ".join(cols))

    # Extraer tabla de clasificación
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

    # Extraer plantilla
    for el in soup.select('.jugador-card, .plantilla-jugador, .jugador'):
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
        print(f"Sincronizando {cat['nombre']}...")
        html = fetch_html(cat["url"])

        if not html:
            # Conservar datos previos si falla la consulta
            if cat_key in datos_previos:
                nuevos_datos[cat_key] = datos_previos[cat_key]
            continue

        soup = BeautifulSoup(html, 'html.parser')
        res, clas, plan = parse_data(soup)

        # Si el raspado devuelve listas vacías, conservar datos anteriores válidos
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

    print("Sincronización completada.")

if __name__ == "__main__":
    main()
