"""
Scraper de resultados de la Tómbola Nocturna de Entre Ríos.
Fuente: argentina.resultadosorteo.net
"""
import httpx
import json
import re
from datetime import date
from bs4 import BeautifulSoup

MESES_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
}

def fecha_a_url(fecha: date) -> str:
    """Convierte una fecha en el slug de URL: '3-de-mayo-2026'"""
    return f"{fecha.day}-de-{MESES_ES[fecha.month]}-{fecha.year}"

def _extraer_numeros(soup: BeautifulSoup) -> list[str]:
    """
    Intenta extraer los 20 números ganadores de 4 dígitos del HTML.
    Prueba múltiples estrategias por si la estructura del sitio cambia.
    """
    numeros = []

    # Estrategia 1: buscar celdas/divs con exactamente 4 dígitos
    for tag in soup.find_all(["td", "span", "div", "li", "p"]):
        texto = tag.get_text(strip=True)
        if re.fullmatch(r"\d{4}", texto):
            numeros.append(texto)
        if len(numeros) == 20:
            break

    if len(numeros) >= 10:
        return numeros[:20]

    # Estrategia 2: buscar en el texto completo todos los grupos de 4 dígitos
    texto_completo = soup.get_text()
    candidatos = re.findall(r"\b(\d{4})\b", texto_completo)
    # Filtrar: los números de quiniela van de 0000 a 9999
    for c in candidatos:
        if c not in numeros:
            numeros.append(c)
        if len(numeros) == 20:
            break

    return numeros[:20]


async def buscar_resultado_tombola(fecha: date) -> dict:
    """
    Busca los 20 números ganadores de la Tómbola Nocturna de Entre Ríos
    para la fecha dada.

    Retorna:
        {"ok": True, "numeros": ["1234", ...20 items...]}
        {"ok": False, "error": "mensaje de error"}
    """
    slug = fecha_a_url(fecha)
    url = f"https://argentina.resultadosorteo.net/quiniela-entre-rios/nocturna/{slug}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "es-AR,es;q=0.9",
    }

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code == 404:
            return {"ok": False, "error": f"No hay resultados para el {fecha.strftime('%d/%m/%Y')} (404)"}
        if resp.status_code != 200:
            return {"ok": False, "error": f"Error HTTP {resp.status_code} al consultar {url}"}

        soup = BeautifulSoup(resp.text, "html.parser")
        numeros = _extraer_numeros(soup)

        if not numeros:
            return {"ok": False, "error": "Se obtuvo la página pero no se encontraron números. El sorteo puede no haberse realizado aún."}

        return {"ok": True, "numeros": numeros, "url": url}

    except httpx.ConnectError:
        return {"ok": False, "error": "No se pudo conectar al sitio de resultados. Verificá tu conexión a internet."}
    except httpx.TimeoutException:
        return {"ok": False, "error": "Tiempo de espera agotado al consultar el sitio de resultados."}
    except Exception as e:
        return {"ok": False, "error": f"Error inesperado: {str(e)}"}
