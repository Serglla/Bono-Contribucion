"""Cuotas vigentes de una talonera según la fecha — "las últimas van de regalo".

Por qué existe (03/08/2026):

La campaña cierra con el SORTEO FINAL de **junio 2027**. Una talonera de 12 cuotas
que se vende en agosto 2026 no llega a cobrar las 12 antes del sorteo, así que el
negocio decidió **regalar las últimas cuotas**: se venden menos cuotas para que la
última cobrable caiga justo en el mes del sorteo.

El esquema de pago es: la **cuota 1 la cobra el vendedor en el acto** (mes de la
venta) y las siguientes las cobra el cobrador **a partir del mes siguiente**, una
por mes. Entonces:

    cuotas cobrables = 1 (la del vendedor) + meses de cobranza hasta junio 2027

    Venta en ago-2026 → cuota 1 en ago + sep..jun = 1 + 10 = 11  (1 de regalo)
    Venta en sep-2026 → cuota 1 en sep + oct..jun = 1 +  9 = 10  (2 de regalo)
    Venta en oct-2026 → cuota 1 en oct + nov..jun = 1 +  8 =  9  (3 de regalo)
    Venta en nov-2026 → 8, dic-2026 → 7, y así.

Nunca devuelve más que `num_cuotas` (una talonera de 8 cuotas sigue siendo de 8
si se vende temprano) ni menos que 1 (siempre se cobra al menos la cuota del
vendedor, incluso si se vende después del sorteo).

DÓNDE SE USA — es la única fuente de verdad de "cuántas cuotas se cobran":
  - `compradores.py` (crear/editar): fija `Boleta.cuotas_pactadas` al dar de alta
    el socio. **De ahí cuelga todo lo demás** — cobranza, planillas, reportes y
    contabilidad leen `cuotas_pactadas` por boleta, no `talonera.num_cuotas`.
  - `vendedores.py` (liquidar): el monto al contado es
    `cuotas_vigentes × valor_cuota`, no `num_cuotas × valor_cuota`.
  - `contabilidad.py`: el ingreso proyectado de una boleta al contado.
  - `vendedor_detalle.html`: réplica en JS de `cuotas_vigentes()` para el preview
    del modal. **Si cambiás la fórmula acá, cambiala también allá.**

Las fechas de la campaña NO están fijas en el código: se configuran desde
Contabilidad → Egresos → "Campaña del bono" (tabla ConfigBono) y se leen con
`campana()`. Si se corre la fecha del sorteo, se cambia ahí y listo.
"""
import time
from datetime import date, datetime
from typing import Optional, Union

from .tiempo import hoy_ar

# ── Configuración de la campaña ─────────────────────────────────────────
# Valores por defecto = campaña 2026-2027. Los reales se guardan en ConfigBono
# (clave → valor_float) y se editan desde Contabilidad. Cambian bono a bono.
CAMPANA_DEFAULTS = {
    "campana_inicio_anio":        2026,  # mes en que arranca la venta
    "campana_inicio_mes":         5,
    "sorteo_final_anio":          2027,  # mes del SORTEO FINAL: última cuota cobrable
    "sorteo_final_mes":           6,
    "cobranza_meses_post_sorteo": 1,     # meses de cobranza de atrasados después del sorteo
    "venta_min_cuotas":           6,     # se proyecta vender mientras queden >= N cuotas
}
CAMPANA_CLAVES = tuple(CAMPANA_DEFAULTS)

# Cache corto: la app corre con varios workers (WEB_CONCURRENCY), así que no
# alcanza con invalidar en memoria al guardar — cada worker relee cada 30 s.
_CAMPANA_TTL = 30.0
_campana_cache = {"t": 0.0, "v": None}

_MESES_NOMBRE = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                 "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _armar_campana(vals: dict) -> dict:
    ia, im = int(vals["campana_inicio_anio"]), int(vals["campana_inicio_mes"])
    sa, sm = int(vals["sorteo_final_anio"]), int(vals["sorteo_final_mes"])
    post = max(0, int(vals["cobranza_meses_post_sorteo"]))
    li = sa * 12 + sm - 1 + post
    return {
        "inicio":       (ia, im),
        "sorteo":       (sa, sm),
        "limite":       (li // 12, li % 12 + 1),   # último mes con cobranza
        "post_sorteo":  post,
        "min_cuotas":   max(1, int(vals["venta_min_cuotas"])),
        "inicio_txt":   f"{_MESES_NOMBRE[im - 1]} {ia}",
        "sorteo_txt":   f"{_MESES_NOMBRE[sm - 1]} {sa}",
        "limite_txt":   f"{_MESES_NOMBRE[li % 12]} {li // 12}",
        "valores":      {k: int(vals[k]) for k in CAMPANA_CLAVES},
    }


def campana() -> dict:
    """Configuración vigente de la campaña (ver CAMPANA_DEFAULTS).

    Devuelve {"inicio": (anio, mes), "sorteo": (anio, mes), "limite": (anio, mes),
    "post_sorteo", "min_cuotas", "inicio_txt", "sorteo_txt", "limite_txt",
    "valores"}. Si la DB no responde, usa los valores por defecto."""
    now = time.monotonic()
    if _campana_cache["v"] is not None and now - _campana_cache["t"] < _CAMPANA_TTL:
        return _campana_cache["v"]
    vals = dict(CAMPANA_DEFAULTS)
    try:
        from .database import SessionLocal
        from . import models
        s = SessionLocal()
        try:
            for row in (s.query(models.ConfigBono)
                        .filter(models.ConfigBono.clave.in_(CAMPANA_CLAVES)).all()):
                if row.valor_float is not None:
                    vals[row.clave] = int(row.valor_float)
        finally:
            s.close()
    except Exception:
        pass
    try:
        c = _armar_campana(vals)
    except Exception:
        c = _armar_campana(CAMPANA_DEFAULTS)
    _campana_cache.update(t=now, v=c)
    return c


def invalidar_campana() -> None:
    """Fuerza a releer la configuración en el próximo `campana()` (este worker)."""
    _campana_cache["v"] = None

# Fallback cuando la talonera no tiene num_cuotas cargado.
NUM_CUOTAS_DEFAULT = 12


def _a_fecha(f: Union[date, datetime, str, None]) -> date:
    """Normaliza a `date`. None/basura → hoy en Argentina (nunca UTC, ver tiempo.py)."""
    if f is None:
        return hoy_ar()
    if isinstance(f, datetime):
        return f.date()
    if isinstance(f, date):
        return f
    s = str(f).strip()
    if not s:
        return hoy_ar()
    # "2026-08-03" o "2026-08-03T10:00" o "2026-08"
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        pass
    try:
        anio, mes = s[:7].split("-")
        return date(int(anio), int(mes), 1)
    except (ValueError, TypeError):
        return hoy_ar()


def meses_de_cobranza(fecha: Union[date, datetime, str, None] = None) -> int:
    """Meses de cobranza que quedan DESPUÉS del mes de `fecha`, hasta el sorteo final.

    Es la distancia en meses calendario; el día del mes no importa (una venta el
    1 y otra el 31 de agosto cobran las mismas cuotas). Puede ser negativo si la
    fecha ya pasó el sorteo — `cuotas_vigentes` se encarga del piso.
    """
    f = _a_fecha(fecha)
    sa, sm = campana()["sorteo"]
    return 12 * (sa - f.year) + (sm - f.month)


def cuotas_vigentes(
    num_cuotas: Optional[int],
    fecha: Union[date, datetime, str, None] = None,
) -> int:
    """Cuántas cuotas se cobran realmente por una boleta vendida en `fecha`.

    `num_cuotas` es el nominal de la talonera (12 en las PATA actuales). El
    resultado nunca lo supera: las cuotas se REGALAN, no se agregan.

        >>> cuotas_vigentes(12, date(2026, 8, 15))
        11
        >>> cuotas_vigentes(12, date(2026, 10, 1))
        9
        >>> cuotas_vigentes(8, date(2026, 1, 1))   # talonera corta, venta temprana
        8
    """
    nominal = int(num_cuotas or NUM_CUOTAS_DEFAULT)
    if nominal < 1:
        nominal = NUM_CUOTAS_DEFAULT
    return max(1, min(nominal, 1 + meses_de_cobranza(fecha)))


def cuotas_regaladas(
    num_cuotas: Optional[int],
    fecha: Union[date, datetime, str, None] = None,
) -> int:
    """Cuántas cuotas del nominal quedan sin cobrar. Para mostrar en la UI."""
    nominal = int(num_cuotas or NUM_CUOTAS_DEFAULT)
    if nominal < 1:
        nominal = NUM_CUOTAS_DEFAULT
    return max(0, nominal - cuotas_vigentes(nominal, fecha))


def monto_contado(
    num_cuotas: Optional[int],
    valor_cuota: Optional[float],
    fecha: Union[date, datetime, str, None] = None,
) -> float:
    """Precio total de una boleta al contado vendida en `fecha`.

    Antes era `num_cuotas × valor_cuota` (siempre 12). Ahora descuenta las
    cuotas regaladas: el que paga al contado en octubre paga 9 cuotas, no 12.
    """
    return cuotas_vigentes(num_cuotas, fecha) * float(valor_cuota or 0.0)
