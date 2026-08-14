"""Estado de cobranza de un socio — columna ESTADO de la lista de socios.

QUÉ RESPONDE
------------
"¿Este bono está al día?" mirando el último mes con cobranza cargada.

REGLAS (en orden de prioridad, la primera que matchea gana)

  1. BAJA         rojo    — se dio de baja durante la cobranza. Además la fila
                            entera se tiñe de rojo en la tabla.
  2. CONTADO      azul    — pagó todo de una. Sale del circuito de cuotas.
     CONTADO X2   azul    — pagó en 2 pagos.
  3. AL DÍA       verde   — no debe nada al mes de referencia. Incluye:
                            · terminó de pagar las cuotas pactadas
                            · se vendió este mes (solo debe la cuota del vendedor,
                              que ya cobró el vendedor en el acto)
                            · viene pagando puntual
  4. DEBE N       amarillo — pagó el mes de referencia PERO tiene huecos de meses
                            anteriores. Para entrar al sorteo final va a tener que
                            pagar cuotas adelantadas en algún momento.
  5. DEBE 1       rosado  — no pagó el mes de referencia y es lo único que debe:
                            atraso recién empezado.
  6. DEBE N       rojo    — no pagó el mes de referencia y además arrastra meses
                            anteriores: atraso serio.

MES DE REFERENCIA
-----------------
NO es el mes calendario anterior, sino el **último período con cobranza cargada**
en el sistema (el máximo `historial_cuotas` de todas las boletas). Si se usara el
mes calendario, el día 1 de cada mes —con la cobranza del mes anterior todavía sin
cerrar— toda la lista se pondría en rojo de golpe.

CUÁNTAS CUOTAS DEBERÍA TENER PAGAS
----------------------------------
La cuota 1 la cobra el vendedor en el acto, el mes de la venta. Las siguientes las
cobra el cobrador, una por mes, a partir del mes siguiente. Entonces:

    esperado = 1 + (meses entre el mes de venta y el mes de referencia)

con tope en `cuotas_pactadas` (ver app/cuotas.py: las últimas cuotas van de regalo
si la boleta se vendió tarde). `debe = esperado - cuotas_pagadas`.

SOCIOS CON VARIAS BOLETAS
-------------------------
Se muestra el estado MÁS URGENTE de todas sus boletas (menor `rank`): si tiene una
al día y otra atrasada, importa la atrasada.
"""
import json

from .tiempo import hoy_ar, parse_periodo

# rank: para ordenar la columna. Menor = más urgente, así al ordenar ascendente
# los problemas quedan arriba.
RANK_BAJA, RANK_ROJO, RANK_ROSA, RANK_AMARILLO, RANK_ALDIA, RANK_CONTADO = 0, 1, 2, 3, 4, 5

# clase = sufijo CSS definido en compradores.html (.est-baja, .est-rojo, ...)
_SIN_DATOS = {"texto": "—", "clase": "vacio", "rank": 9, "tip": "Sin fecha de compra cargada"}


def _hist_periodos(boleta):
    """Set de períodos (anio, mes) en que esta boleta pagó alguna cuota.

    `historial_cuotas` es un JSON {"nro_cuota": "2026-07"}. Los valores legacy
    vienen sin año (solo el mes) — parse_periodo los devuelve como (None, mes) y
    los tratamos como comodín de año (ver _pago_en).
    """
    try:
        h = json.loads(boleta.historial_cuotas) if boleta.historial_cuotas else {}
    except (ValueError, TypeError):
        return set()
    out = set()
    for v in h.values():
        p = parse_periodo(v)
        if p:
            out.add(p)
    return out


def _pago_en(periodos, anio, mes):
    """¿Pagó en (anio, mes)? Los legacy sin año matchean solo por mes."""
    return (anio, mes) in periodos or (None, mes) in periodos


def periodo_referencia(db, models):
    """Último período (anio, mes) con cobranza cargada en todo el sistema.

    Se resuelve en UNA query que trae solo la columna `historial_cuotas`. Si
    todavía no hay ningún pago registrado, cae al mes calendario actual.
    """
    mejor = None
    filas = (db.query(models.Boleta.historial_cuotas)
               .filter(models.Boleta.historial_cuotas.isnot(None))
               .filter(models.Boleta.historial_cuotas.notin_(("", "{}")))
               .all())
    for (h,) in filas:
        try:
            data = json.loads(h) if h else {}
        except (ValueError, TypeError):
            continue
        for v in data.values():
            p = parse_periodo(v)
            if not p:
                continue
            anio, mes = p
            if not anio:            # legacy sin año: no sirve para elegir el máximo
                continue
            if mejor is None or (anio, mes) > mejor:
                mejor = (anio, mes)
    if mejor is None:
        hoy = hoy_ar()
        return (hoy.year, hoy.month)
    return mejor


def _meses_entre(desde, hasta):
    """Cantidad de meses de (anio, mes) `desde` a `hasta`. Puede ser negativo."""
    return (hasta[0] - desde[0]) * 12 + (hasta[1] - desde[1])


def estado_boleta(b, ref):
    """Estado de UNA boleta. `ref` = período de referencia (anio, mes).

    Devuelve dict {texto, clase, rank, tip}.
    """
    cond = b.condicion.value if b.condicion is not None else ""

    # 1. Baja
    if cond == "BAJA" or b.mes_baja:
        tip = "Dado de baja durante la cobranza"
        if b.mes_baja:
            tip += f" (mes {b.mes_baja})"
        return {"texto": "BAJA", "clase": "baja", "rank": RANK_BAJA, "tip": tip}

    pactadas = int(b.cuotas_pactadas or 0)
    pagadas = int(b.cuotas_pagadas or 0)
    anticipadas = int(b.cuotas_anticipadas or 0)
    modalidad = (getattr(b, "modalidad_liquidacion", None) or "").lower()

    # 2. Contado — pagó todo de entrada, no entra en el circuito de cuotas.
    #    Slot 1 + slot 2 asignados = pago en 1 sola vez ("CONTADO").
    #    Solo slot 2 = pago en 2 veces ("CONTADO X2").
    esp1 = b.numero_especial is not None
    esp2 = getattr(b, "numero_especial_2", None) is not None
    if modalidad == "contado2" or (esp2 and not esp1):
        return {"texto": "CONTADO X2", "clase": "contado", "rank": RANK_CONTADO,
                "tip": "Pagó en 2 pagos (sorteo CONTADO 2 VECES)"}
    if modalidad == "contado" or esp1 or (pactadas and anticipadas >= pactadas):
        return {"texto": "CONTADO", "clase": "contado", "rank": RANK_CONTADO,
                "tip": "Pagó al contado (bono completo de una)"}

    # 3. Terminó de pagar todas las cuotas pactadas.
    if pactadas and pagadas >= pactadas:
        return {"texto": "AL DÍA", "clase": "aldia", "rank": RANK_ALDIA,
                "tip": f"Bono completo: {pagadas}/{pactadas} cuotas pagas"}

    if not b.fecha_venta:
        return dict(_SIN_DATOS)

    venta = (b.fecha_venta.year, b.fecha_venta.month)

    # Cuántas cuotas debería tener pagas al mes de referencia.
    # La cuota 1 la cobra el vendedor el mes de la venta; después una por mes.
    esperado = 1 + _meses_entre(venta, ref)
    esperado = max(1, esperado)
    if pactadas:
        esperado = min(esperado, pactadas)

    debe = esperado - pagadas
    ref_txt = f"{ref[1]:02d}/{ref[0]}"

    if debe <= 0:
        return {"texto": "AL DÍA", "clase": "aldia", "rank": RANK_ALDIA,
                "tip": f"Al día a {ref_txt} — {pagadas}/{pactadas} cuotas pagas"}

    periodos = _hist_periodos(b)
    pago_ref = _pago_en(periodos, ref[0], ref[1])

    plural = "cuota" if debe == 1 else "cuotas"

    if pago_ref:
        # 4. Pagó el último mes pero le faltan meses anteriores: para entrar al
        #    sorteo final va a tener que ponerse al día con cuotas adelantadas.
        return {"texto": f"DEBE {debe}", "clase": "atrasado", "rank": RANK_AMARILLO,
                "tip": (f"Pagó {ref_txt} pero arrastra {debe} {plural} de meses "
                        f"anteriores — {pagadas}/{pactadas} pagas. "
                        f"Debería adelantarlas para entrar al sorteo final.")}

    if debe == 1:
        # 5. No pagó el mes de referencia y es lo único que debe.
        return {"texto": "DEBE 1", "clase": "moroso1", "rank": RANK_ROSA,
                "tip": f"No pagó {ref_txt} — es la única cuota que debe "
                       f"({pagadas}/{pactadas} pagas)"}

    # 6. No pagó el mes de referencia y además arrastra meses anteriores.
    return {"texto": f"DEBE {debe}", "clase": "moroso", "rank": RANK_ROJO,
            "tip": f"No pagó {ref_txt} y debe {debe} {plural} en total "
                   f"({pagadas}/{pactadas} pagas)"}


def estado_socio(comprador, ref):
    """Estado de un socio = el más urgente (menor rank) entre todas sus boletas."""
    if not comprador.boletas:
        return dict(_SIN_DATOS)
    estados = [estado_boleta(b, ref) for b in comprador.boletas]
    peor = min(estados, key=lambda e: e["rank"])
    if len(estados) > 1:
        otros = [e["texto"] for e in estados if e is not peor]
        if otros:
            peor = dict(peor)
            peor["tip"] += " · Otras boletas del socio: " + ", ".join(otros)
    return peor


def estados_por_socio(db, models, compradores):
    """{comprador_id: estado} para toda la lista. Una sola query extra."""
    ref = periodo_referencia(db, models)
    return {c.id: estado_socio(c, ref) for c in compradores}, ref
