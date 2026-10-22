"""Liquidación de una planilla de cobranza: qué cobró el cobrador y cuánto rinde.

Movido desde routers/cobranza.py (relevamiento, punto 7). Acá van las reglas y
las cuentas; el router lee el formulario, busca planilla y boletas, llama a
estas funciones y guarda. Tests: tests/test_liquidacion_cobranza.py.

REGLAS
------
* Cada cuota marcada se anota en `Boleta.historial_cuotas` como
  {"nro_cuota": "YYYY-MM"} del PERÍODO que se liquida (formato con año: fix C-1,
  antes julio 2026 y julio 2027 se pisaban).
* Volver a guardar un período REEMPLAZA lo de ese período; los demás no se tocan.
* cuotas_pagadas = anticipadas + cuotas del historial, con tope en pactadas.
* Nunca se liquida un período FUTURO (se recorta al mes actual, hora argentina).
* "Pasar números": si la boleta ENTRÓ a esta planilla desde otra, las cuotas
  que ya traía pagas (hasta `paso_cuota`) son del cobrador anterior: no se
  pisan ni suman acá.
* La baja (clic derecho) es solo un registro (`mes_baja`): la boleta sigue en
  la planilla y no pasa a BAJA.
* Plata: cuotas cobradas × valor de cuota de la tanda (con respaldo si la tanda
  quedó en $0); el cobrador se queda con `comision_pct` %, rinde el neto.
"""
import json
from typing import Iterable, Optional, Tuple

from .. import models
from ..models import CondicionBoleta
from ..tiempo import hoy_ar, match_periodo, periodo_str
from . import reglas_boleta as reglas


# ── Período ─────────────────────────────────────────────────────────────────

def resolver_periodo(mes: int = 0, anio: int = 0) -> Tuple[int, int, bool, bool]:
    """Período (año, mes) a liquidar a partir de lo pedido, con dos reglas duras:

      - Por defecto se liquida el mes calendario ACTUAL (hora argentina).
      - NUNCA un período FUTURO: si lo piden (URL manipulada, selector viejo) se
        cae al actual y se avisa con `ajustado`.

    Un período PASADO sí se permite: es corregir una cobranza ya liquidada.
    Devuelve (anio, mes, es_pasado, ajustado)."""
    hoy = hoy_ar()
    if not mes:
        mes = hoy.month
    if not anio:
        anio = hoy.year
    try:
        mes, anio = int(mes), int(anio)
    except (TypeError, ValueError):
        return hoy.year, hoy.month, False, True
    if not (1 <= mes <= 12):
        return hoy.year, hoy.month, False, True
    if (anio, mes) > (hoy.year, hoy.month):
        return hoy.year, hoy.month, False, True
    return anio, mes, (anio, mes) < (hoy.year, hoy.month), False


# ── Números pasados entre planillas ─────────────────────────────────────────

def cuota_recibida(clave, recibidas: int) -> bool:
    """True si la cuota `clave` del historial es una de las que la boleta ya
    traía pagas al entrar a la planilla (las cobró el cobrador anterior)."""
    if not recibidas:
        return False
    try:
        return int(clave) <= int(recibidas)
    except (TypeError, ValueError):
        return False


def cuota_cuenta_aca(clave, corte, recibidas) -> bool:
    """¿La cuota `clave` del historial es cobranza de ESTA planilla?

    Mismo criterio que el resumen de la planilla y la hoja de liquidación:
      - `corte` (paso_cuota) → la boleta SALIÓ de acá: solo cuentan las cuotas
        hasta el corte; las de después las cobra la planilla destino.
      - `recibidas` → la boleta ENTRÓ desde otra planilla: las cuotas que ya
        traía pagas las cobró el cobrador anterior y acá no suman.
    """
    try:
        kn = int(clave)
    except (TypeError, ValueError):
        return False
    if corte is not None and kn > corte:
        return False
    if recibidas and kn <= recibidas:
        return False
    return True


def recibidas_de(b, planilla_id=None) -> int:
    """Cuotas que la boleta YA TRAÍA PAGAS cuando entró a esta planilla.

    Cuando un número se pasa de un cobrador a otro ("Pasar números"), las cuotas
    que cobró el ANTERIOR viajan con la boleta (historial_cuotas). En la planilla
    NUEVA esas cuotas no son cobranza suya: se dibujan con X (igual que las
    anticipadas de la venta) y no suman en su resumen ni en su comisión.

    Devuelve 0 si la boleta no vino de otra planilla."""
    origen = getattr(b, "paso_origen_planilla_id", None)
    if not origen:
        return 0
    pid = planilla_id if planilla_id is not None else b.planilla_id
    if origen == pid:
        return 0
    return int(getattr(b, "paso_cuota", 0) or 0)


def mapa_recibidas(boletas, planilla_id) -> dict:
    """{boleta_id: cuotas_pagas_al_recibirla} para las boletas que ENTRARON a
    esta planilla viniendo de otra (ver `recibidas_de`)."""
    out = {}
    for b in boletas:
        if b.planilla_id != planilla_id:
            continue
        n = recibidas_de(b, planilla_id)
        if n > 0:
            out[b.id] = n
    return out


# ── Plata ───────────────────────────────────────────────────────────────────

def valor_cuota(b) -> float:
    """Valor de UNA cuota de la boleta para sumar plata cobrada.

    Usa el valor_cuota de su tanda; si esa tanda quedó en $0 (pasó con la
    9024→9073 de PATA 0: la hoja de Mabel daba $50.000 de menos en la P8),
    toma el de otra tanda de la misma PATA y, si ninguna tiene, el de PATA 1 ×
    multiplicador (misma regla que la planilla). El respaldo se cachea en la
    talonera para no consultar la base por cada boleta."""
    t = b.talonera
    if not t:
        return 0.0
    if t.valor_cuota and float(t.valor_cuota) > 0:
        return float(t.valor_cuota)
    cached = getattr(t, "_vc_fallback", None)
    if cached is not None:
        return cached
    from sqlalchemy.orm import object_session
    vc = 0.0
    ses = object_session(t)
    if ses is not None:
        hermana = (ses.query(models.Talonera)
                   .filter(models.Talonera.nombre == t.nombre,
                           models.Talonera.tipo == (t.tipo or "COMUN"),
                           models.Talonera.valor_cuota > 0)
                   .first())
        if hermana:
            vc = float(hermana.valor_cuota)
        else:
            base = (ses.query(models.Talonera)
                    .filter(models.Talonera.tipo == "COMUN",
                            models.Talonera.multiplicador == 1.0,
                            models.Talonera.valor_cuota > 0)
                    .first())
            if base:
                vc = round(float(base.valor_cuota) * float(t.multiplicador or 1.0))
    try:
        t._vc_fallback = vc
    except Exception:
        pass
    return vc


def totales(monto_total: float, comision_pct: Optional[float]) -> Tuple[float, float, float]:
    """(monto, comisión del cobrador, neto que rinde), redondeados a centavos."""
    comision = round(monto_total * (float(comision_pct or 0.0) / 100.0), 2)
    return round(monto_total, 2), comision, round(monto_total - comision, 2)


# ── Una boleta ──────────────────────────────────────────────────────────────

def aplicar_cobro(boleta, cuotas_marcadas: Iterable, anio: int, mes: int,
                  planilla_id: int, mes_baja: Optional[int] = None) -> int:
    """Anota en `boleta` las cuotas cobradas en el período (anio, mes) y
    recalcula cuotas pagas, baja y condición. Devuelve cuántas cuotas cobró
    ESTE cobrador en el período (las recibidas de otro no cuentan).

    `cuotas_marcadas` es la selección completa del período: lo que había antes
    de ese período se reemplaza (desmarcar = borrar)."""
    try:
        historial = json.loads(boleta.historial_cuotas) if boleta.historial_cuotas else {}
    except (ValueError, TypeError):
        historial = {}
    # Las cuotas que la boleta ya traía pagas de OTRO cobrador no se tocan:
    # aunque se hayan cobrado en este mismo período, son de la liquidación de
    # aquel cobrador (no viajan en el form: se dibujan con X).
    recibidas = recibidas_de(boleta, planilla_id)
    historial = {k: v for k, v in historial.items()
                 if not match_periodo(v, anio, mes) or cuota_recibida(k, recibidas)}
    nuevas = [c for c in cuotas_marcadas if not cuota_recibida(c, recibidas)]
    per = periodo_str(anio, mes)
    for c in nuevas:
        historial[str(c)] = per
    boleta.historial_cuotas = json.dumps(historial)

    pagas = (boleta.cuotas_anticipadas or 0) + len(historial)
    boleta.cuotas_pagadas = min(pagas, boleta.cuotas_pactadas or pagas)

    # Baja: solo registro visual; la boleta sigue en la planilla (no pasa a BAJA).
    boleta.mes_baja = mes_baja if mes_baja else None

    # Condición según las cuotas. No se tocan BAJA / SIN_VENDER / CAJA.
    if boleta.condicion not in (CondicionBoleta.BAJA, CondicionBoleta.SIN_VENDER,
                                CondicionBoleta.CAJA):
        boleta.condicion = reglas.condicion_vendida(boleta)
    return len(nuevas)
