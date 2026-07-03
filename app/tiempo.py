"""Fecha/hora en zona horaria ARGENTINA + helpers de "período" (año-mes).

Por qué existe este módulo (auditoría 03/07/2026, hallazgos A-2 y C-1):

A-2 — El servidor de Railway corre en UTC. `date.today()` / `datetime.utcnow()`
      devuelven la fecha UTC: entre las 21:00 y las 00:00 hora argentina "hoy"
      ya es mañana. En fin de mes eso corría cuotas al mes siguiente en el
      historial y en el consolidado. TODO código que necesite "hoy" o "el mes
      actual" para cobranza debe usar hoy_ar() / periodo_actual() de acá.

C-1 — `Boleta.historial_cuotas` guardaba {"cuota": mes} con mes 1-12 SIN AÑO.
      Con la campaña 2026-2027 eso colisionaba (julio 2026 vs julio 2027):
      al liquidar se borraban cuotas del mismo mes de OTRO año, y el
      consolidado mezclaba años. El formato nuevo es {"cuota": "YYYY-MM"}
      (ej: {"3": "2026-07"}). Los helpers de abajo parsean AMBOS formatos:
      el valor legacy (int 1-12) se trata como "solo mes, año desconocido"
      y matchea por mes — igual que el comportamiento viejo — hasta que la
      migración de main.py (startup) lo convierta.

Argentina no usa horario de verano desde 2009, así que el fallback de offset
fijo -3 es seguro si zoneinfo no tiene la base de datos de zonas (Windows sin
el paquete tzdata).
"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

try:
    from zoneinfo import ZoneInfo
    TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")
except Exception:                     # Windows sin tzdata instalado
    TZ_AR = timezone(timedelta(hours=-3), name="AR")


def ahora_ar() -> datetime:
    """Datetime actual en hora argentina (naive, para comparar con la DB)."""
    return datetime.now(TZ_AR).replace(tzinfo=None)


def hoy_ar() -> date:
    """Fecha de HOY en Argentina. Reemplaza a date.today() en cobranza."""
    return datetime.now(TZ_AR).date()


def periodo_str(anio: int, mes: int) -> str:
    """(2026, 7) -> "2026-07"."""
    return f"{anio:04d}-{mes:02d}"


def periodo_actual() -> str:
    """Período (año-mes) actual en Argentina, ej "2026-07"."""
    h = hoy_ar()
    return periodo_str(h.year, h.month)


def parse_periodo(v) -> Optional[Tuple[Optional[int], int]]:
    """Parsea un valor de historial_cuotas a (anio, mes).

    - "2026-07"  -> (2026, 7)      formato nuevo
    - 7 / "7"    -> (None, 7)      formato legacy sin año (pre-migración)
    - basura     -> None
    """
    if v is None:
        return None
    if isinstance(v, int):
        return (None, v) if 1 <= v <= 12 else None
    s = str(v).strip()
    if "-" in s:
        try:
            a, m = s.split("-", 1)
            anio, mes = int(a), int(m)
            if 1 <= mes <= 12:
                return (anio, mes)
        except (ValueError, TypeError):
            pass
        return None
    try:
        mes = int(s)
    except (ValueError, TypeError):
        return None
    return (None, mes) if 1 <= mes <= 12 else None


def mes_de(v) -> int:
    """Mes (1-12) de un valor de historial, o 0 si no parsea. Para mostrar."""
    p = parse_periodo(v)
    return p[1] if p else 0


def match_periodo(v, anio: int, mes: int) -> bool:
    """¿El valor de historial `v` corresponde al período (anio, mes)?

    Valores legacy (sin año) matchean solo por mes — mismo comportamiento
    que el código viejo, para no romper datos aún no migrados.
    """
    p = parse_periodo(v)
    if p is None:
        return False
    a, m = p
    if m != mes:
        return False
    return a is None or a == anio
