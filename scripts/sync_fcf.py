import requests
from bs4 import BeautifulSoup
import json
import os

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Diccionario con las URLs de tu club en la FCF
CATEGORIAS = {
    "categoria_33085": {"nombre": "Tercera División Alevín S12", "url_fcf": "https://www.fcf.cat/ca/clubs/1096/categories/33085"},
    "categoria_39706": {"nombre": "Juvenil A", "url_fcf": "https://www.fcf.cat/ca/clubs/1096/categories/39706"},
    # Añade aquí el resto de categorías con sus nombres reales...
}

datos_finales = {}

for cat_id, info in CATEGORIAS.items():
    print(f"Procesando {info['nombre']}...")
    res = requests.get(info['url_fcf'], headers=HEADERS)
    
    resultados = []
    clasificacion = []
    plantilla = []

    if res.status_code == 200:
        soup = BeautifulSoup(res.content, 'html.parser')
        
        # 1. Extraer Clasificación (ejemplo de scraping de tabla FCF)
        filas_clasif = soup.select('table.fcf-table tbody tr')
        for fila in filas_clasif:
            cols = [td.get_text(strip=True) for td in fila.select('td')]
            if len(cols) >= 8:
                clasificacion.append({
                    "posicion": cols[0],
                    "equipo": cols[1],
                    "pj": cols[2],
                    "pg": cols[3],
                    "pe": cols[4],
                    "pp": cols[5],
                    "puntos": cols[-1]
                })

        # 2. Extraer Resultados / Próximos partidos
        filas_res = soup.select('.partit-row, tr.partit')
        for fila in filas_res:
            equipo_local = fila.select_one('.local')
            equipo_visitante = fila.select_one('.visitant')
            resultado_elem = fila.select_one('.res')
            
            if equipo_local and equipo_visitante:
                resultados.append({
                    "local": equipo_local.get_text(strip=True),
                    "visitante": equipo_visitante.get_text(strip=True),
                    "resultado": resultado_elem.get_text(strip=True) if resultado_elem else "-"
                })

    datos_finales[cat_id] = {
        "nombre": info["nombre"],
        "url_fcf": info["url_fcf"],
        "resultados": resultados,
        "clasificacion": clasificacion,
        "plantilla": plantilla # Vacío si la FCF no publica la plantilla pública
    }

# Guardar en data/fcf.json
os.makedirs('data', exist_ok=True)
with open('data/fcf.json', 'w', encoding='utf-8') as f:
    json.dump(datos_finales, f, ensure_ascii=False, indent=2)

print("✅ fcf.json actualizado correctamente.")
