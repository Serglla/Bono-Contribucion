"""Plata de la liquidación de un vendedor: qué rinde y con qué se queda.

Movido desde routers/vendedores.py (relevamiento 21/10/2026, punto 7). Acá van
SOLO las cuentas; el router lee el formulario, busca las boletas, llama a estas
funciones y guarda. Así las cuentas se testean sin levantar la app
(tests/test_liquidacion_vendedor.py).

MODELO (lo que el vendedor rinde a la institución)
-------------------------------------------------
* Boleta en CUOTAS: el vendedor cobró la cuota 1 en el acto y se la queda
  entera (comision_cuotas = valor_cuota). No rinde nada por ella.
* Boleta al CONTADO (o CONTADO 2 VECES): vale
  `cuotas_vigentes(fecha de la liquidación) × valor_cuota` — las últimas cuotas
  se regalan (ver app/cuotas.py): un contado de agosto vale 11, de octubre 9.
  El vendedor se queda con `comision_contados_pct` % (30 por defecto) y rinde
  el resto.
* Cuotas extras (cuota 2, 3... que el vendedor cobró directo): carga manual
  cantidad × valor; se queda con `comision_cuotas_pct` % y rinde el resto.
  Hay dos renglones: PATA 1 y PATA 0.
* Cada boleta pesa según el multiplicador de su PATA (PATA 0 ×0.67, PATA 2 ×2)
  en `cuotas_equiv` / `contados_equiv` (los usa el dashboard).

    total_a_rendir = (monto_contados − comisión contados)
                   + (extras − comisión extras) + (extras P0 − comisión extras P0)
"""
from datetime import date, datetime
from typing import Iterable, Optional, Union

from ..cuotas import cuotas_vigentes
from .reglas_boleta import MODALIDADES_LIQ, modalidad_de_boleta

MODALIDADES_CONTADO = ("contado", "contado2")
PCT_CONTADO_DEFAULT = 30.0


def _mult(b) -> float:
    return float((b.talonera.multiplicador or 1.0) if b.talonera else 1.0)


def _valor_cuota(b) -> float:
    return float((b.talonera.valor_cuota or 0.0) if b.talonera else 0.0)


def recalc_totales(liq) -> None:
    """Recalcula total_a_rendir y total_comision (legacy) desde los componentes.
    Se usa al editar una liquidación (agregar/sacar números, cambiar fecha)."""
    liq.total_a_rendir = round(
        (float(liq.monto_contados or 0)         - float(liq.comision_contados or 0))
        + (float(liq.cuotas_extras_monto or 0)    - float(liq.comision_cuotas_extras or 0))
        + (float(liq.cuotas_extras_p0_monto or 0) - float(liq.comision_cuotas_extras_p0 or 0)),
        2,
    )
    liq.total_comision = round(
        float(liq.comision_cuotas or 0) + float(liq.comision_contados or 0)
        + float(liq.comision_cuotas_extras or 0) + float(liq.comision_cuotas_extras_p0 or 0),
        2,
    )


def monto_contado_boleta(b, liq, fecha: Union[date, datetime, None] = None) -> float:
    """Cuánto vale al contado la boleta `b` DENTRO de la liquidación `liq`.

    `fecha` permite simular otra fecha sin tocar `liq` (cambiar-fecha calcula la
    diferencia entre la fecha vieja y la nueva). Usa cuotas_vigentes a la FECHA
    DE LA LIQUIDACIÓN, igual que `calcular_liquidacion`.
    """
    if b is None:
        return 0.0
    num_cuotas = b.talonera.num_cuotas if b.talonera else None
    if fecha is None:
        fecha = liq.fecha if liq is not None else None
    return cuotas_vigentes(num_cuotas, fecha) * _valor_cuota(b)


def _pct_contado(liq) -> float:
    return float(liq.comision_contados_pct or 0) or PCT_CONTADO_DEFAULT


def aporte_boleta(liq, b, modalidad):
    """(mult, valor_cuota, monto_contado, comision_contado, pct) de una boleta."""
    pct = _pct_contado(liq)
    monto = monto_contado_boleta(b, liq) if modalidad in MODALIDADES_CONTADO else 0.0
    return _mult(b), _valor_cuota(b), monto, round(monto * pct / 100, 2), pct


def sumar_boleta(liq, b, modalidad) -> None:
    """Suma a la liquidación lo que aporta `b` con `modalidad`. Espejo exacto
    de `restar_boleta`: si cambian los números, cambian en los dos."""
    if liq is None or b is None:
        return
    if modalidad not in MODALIDADES_LIQ:
        modalidad = "cuotas"
    mult, valor_cuota, monto, com, pct = aporte_boleta(liq, b, modalidad)
    if modalidad in MODALIDADES_CONTADO:
        liq.contados_vendidos = int(liq.contados_vendidos or 0) + 1
        liq.contados_equiv    = float(liq.contados_equiv or 0) + mult
        liq.monto_contados    = round(float(liq.monto_contados or 0) + monto, 2)
        liq.comision_contados = round(float(liq.comision_contados or 0) + com, 2)
        if not liq.comision_contados_pct:
            liq.comision_contados_pct = pct
    else:
        liq.cuotas_vendidas = int(liq.cuotas_vendidas or 0) + 1
        liq.cuotas_equiv    = float(liq.cuotas_equiv or 0) + mult
        liq.cuota_1_total   = round(float(liq.cuota_1_total or 0) + valor_cuota, 2)
        liq.monto_cuotas    = round(float(liq.monto_cuotas or 0) + valor_cuota, 2)
        liq.comision_cuotas = round(float(liq.comision_cuotas or 0) + valor_cuota, 2)
    recalc_totales(liq)


def restar_boleta(liq, b, modalidad=None) -> None:
    """Descuenta de la liquidación TODO lo que aportaba `b` y recalcula totales.

    Se usa cuando una boleta SALE de la liquidación ("sacar número", "liberar
    boleta" desde Socios) y como primer paso al CORREGIR la modalidad. Mantenerlo
    en un solo lugar evita el "drift" de los snapshots (el dashboard los usa).

    `modalidad` permite forzar con cuál se había sumado; por defecto la deduce
    de la boleta con `modalidad_de_boleta`.
    """
    if liq is None or b is None:
        return
    if modalidad not in MODALIDADES_LIQ:
        modalidad = modalidad_de_boleta(b)
    mult, valor_cuota, monto, com, _pct = aporte_boleta(liq, b, modalidad)
    if modalidad in MODALIDADES_CONTADO:
        liq.contados_vendidos = max(0, int(liq.contados_vendidos or 0) - 1)
        liq.contados_equiv    = max(0.0, float(liq.contados_equiv or 0) - mult)
        liq.monto_contados    = max(0.0, round(float(liq.monto_contados or 0) - monto, 2))
        liq.comision_contados = max(0.0, round(float(liq.comision_contados or 0) - com, 2))
    else:
        liq.cuotas_vendidas = max(0, int(liq.cuotas_vendidas or 0) - 1)
        liq.cuotas_equiv    = max(0.0, float(liq.cuotas_equiv or 0) - mult)
        liq.cuota_1_total   = max(0.0, round(float(liq.cuota_1_total or 0) - valor_cuota, 2))
        liq.monto_cuotas    = max(0.0, round(float(liq.monto_cuotas or 0) - valor_cuota, 2))
        liq.comision_cuotas = max(0.0, round(float(liq.comision_cuotas or 0) - valor_cuota, 2))
    recalc_totales(liq)


def calcular_liquidacion(
    cuotas: Iterable,
    contados: Iterable,
    fecha_liq: Union[date, datetime, None],
    comision_cuotas_pct: float = 5.0,
    comision_contados_pct: float = PCT_CONTADO_DEFAULT,
    cuotas_extras_cantidad: int = 0,
    cuotas_extras_valor: float = 0.0,
    cuotas_extras_p0_cantidad: int = 0,
    cuotas_extras_p0_valor: float = 0.0,
) -> dict:
    """Totales de una liquidación nueva: {campo de LiquidacionVendedor: valor}.

    `cuotas` / `contados` = boletas liquidadas en cada modalidad (contado incluye
    contado 2 veces). El router asigna el resultado tal cual a la liquidación.
    """
    cuotas, contados = list(cuotas), list(contados)

    # Cuota 1: el vendedor YA la cobró del socio y se la queda. No entra al rinde.
    cuota_1_total = sum(_valor_cuota(b) for b in cuotas)
    com_cuotas    = round(cuota_1_total, 2)

    # Contado: cuotas vigentes a la fecha de liquidación × valor de cuota de su PATA.
    monto_contados = sum(monto_contado_boleta(b, None, fecha_liq) for b in contados)
    com_contados   = round(monto_contados * comision_contados_pct / 100, 2)

    # Cuotas extras (PATA 1 y PATA 0): carga manual cantidad × valor.
    extras_monto    = round(cuotas_extras_cantidad * cuotas_extras_valor, 2)
    com_extras      = round(extras_monto * comision_cuotas_pct / 100, 2)
    extras_p0_monto = round(cuotas_extras_p0_cantidad * cuotas_extras_p0_valor, 2)
    com_extras_p0   = round(extras_p0_monto * comision_cuotas_pct / 100, 2)

    return {
        "cuotas_vendidas":           len(cuotas),
        "cuotas_equiv":              sum(_mult(b) for b in cuotas),
        "cuota_1_total":             cuota_1_total,
        "monto_cuotas":              cuota_1_total,   # 1 cuota por boleta: coincide
        "comision_cuotas_pct":       comision_cuotas_pct,
        "comision_cuotas":           com_cuotas,
        "contados_vendidos":         len(contados),
        "contados_equiv":            sum(_mult(b) for b in contados),
        "monto_contados":            monto_contados,
        "comision_contados_pct":     comision_contados_pct,
        "comision_contados":         com_contados,
        "cuotas_extras_cantidad":    cuotas_extras_cantidad,
        "cuotas_extras_valor":       cuotas_extras_valor,
        "cuotas_extras_monto":       extras_monto,
        "comision_cuotas_extras":    com_extras,
        "cuotas_extras_p0_cantidad": cuotas_extras_p0_cantidad,
        "cuotas_extras_p0_valor":    cuotas_extras_p0_valor,
        "cuotas_extras_p0_monto":    extras_p0_monto,
        "comision_cuotas_extras_p0": com_extras_p0,
        "total_comision": round(com_cuotas + com_contados + com_extras + com_extras_p0, 2),
        "total_a_rendir": round(
            (monto_contados - com_contados)
            + (extras_monto - com_extras)
            + (extras_p0_monto - com_extras_p0),
            2,
        ),
    }


def cambiar_fecha(liq, boletas: Iterable, nueva_fecha: Union[date, datetime]) -> float:
    """Re-precia los contados de `liq` a `nueva_fecha` (cambia cuántas cuotas
    vale un contado) y recalcula comisión y totales. NO toca `liq.fecha` ni las
    boletas: eso lo hace el router. Devuelve la diferencia de monto."""
    vieja = liq.fecha
    delta = 0.0
    for b in boletas:
        if modalidad_de_boleta(b) in MODALIDADES_CONTADO:
            delta += monto_contado_boleta(b, liq, nueva_fecha) - monto_contado_boleta(b, liq, vieja)
    if delta:
        liq.monto_contados = max(0.0, round(float(liq.monto_contados or 0) + delta, 2))
        liq.comision_contados = round(float(liq.monto_contados or 0) * _pct_contado(liq) / 100, 2)
    recalc_totales(liq)
    return delta
