"""Reglas de una boleta: ¿es contado?, ¿está toda paga?, ¿la cobra un cobrador?

ÚNICA fuente de estas reglas (relevamiento 21/10/2026, punto 6). Antes la
pregunta "¿es contado?" estaba copiada en ~18 lugares con 4 criterios distintos:
unos miraban solo los números especiales, el mapa y el dashboard ignoraban el
slot 2, otros sumaban el anticipo total… y la misma boleta salía contado en una
pantalla y en cuotas en otra. Cualquier cambio a estas reglas va ACÁ.

DOS PREGUNTAS DISTINTAS (no mezclar)
------------------------------------
* `modalidad_de_boleta(b)` → cómo la LIQUIDÓ EL VENDEDOR ('cuotas' | 'contado'
  | 'contado2'). Define la plata de la liquidación y la comisión. Si el vendedor
  la liquidó explícitamente en cuotas, es 'cuotas' aunque después el socio haya
  pagado todo de entrada.

* `es_contado(b)` → si EL SOCIO pagó todo de entrada. Define si lleva cobrador,
  la condición, los badges y los totales del dashboard. Es contado si:
    1. el vendedor la liquidó al contado ('contado' / 'contado2'), o
    2. tiene número especial en el slot 1 o 2 — salvo que la liquidación diga
       explícitamente 'cuotas' (número espurio de data vieja, ver alta de
       socio en compradores.py), o
    3. entró pagando todas las cuotas: cuotas_anticipadas >= cuotas_pactadas.
  El punto 3 no sobra: los especiales vienen en talonarios de 5 y se cargan
  cuando se cierra el talonario, así que un contado pasa semanas sin número.
  Se usa `cuotas_anticipadas` (lo cobrado en el acto), NO `cuotas_pagadas`: una
  boleta que terminó de pagarse en cobranza NO es un contado.

Cada regla tiene su versión Python (para un objeto Boleta) y SQL (para filtrar
en una query) — tienen que decir siempre lo mismo.
"""
from sqlalchemy import and_, func, not_, or_

from ..models import Boleta, CondicionBoleta

MODALIDADES_LIQ = ("cuotas", "contado", "contado2")
MODALIDADES_CONTADO = ("contado", "contado2")


def _mod(b) -> str:
    return (getattr(b, "modalidad_liquidacion", None) or "").strip().lower()


def _tiene_especial(b) -> bool:
    return (b.numero_especial is not None) or (b.numero_especial_2 is not None)


def _anticipo_total(b) -> bool:
    pactadas = int(b.cuotas_pactadas or 0)
    return pactadas > 0 and int(b.cuotas_anticipadas or 0) >= pactadas


# ── Python (objeto Boleta) ──────────────────────────────────────────────────

def modalidad_de_boleta(b) -> str:
    """Modalidad con la que la boleta entró a su liquidación de vendedor.

    Las boletas anteriores al campo `modalidad_liquidacion` lo tienen en NULL y
    se infiere con los mismos rastros que `es_contado` (especial o anticipo total).
    """
    if b is None:
        return "cuotas"
    m = _mod(b)
    if m in MODALIDADES_LIQ:
        return m
    if _tiene_especial(b) or _anticipo_total(b):
        return "contado"
    return "cuotas"


def es_contado(b) -> bool:
    """¿El socio pagó todo de entrada? (ver reglas en el docstring del módulo)."""
    if b is None:
        return False
    m = _mod(b)
    if m in MODALIDADES_CONTADO:
        return True
    if _tiene_especial(b) and m != "cuotas":
        return True
    return _anticipo_total(b)


def toda_paga(b) -> bool:
    """Ya pagó todas las cuotas pactadas (de entrada o en cobranza)."""
    return int(b.cuotas_pagadas or 0) >= int(b.cuotas_pactadas or 0)


def cuotas_pendientes(b) -> bool:
    return not toda_paga(b)


def lleva_cobrador(b) -> bool:
    """¿Tiene sentido asignarle cobrador? Solo si queda algo por cobrar."""
    return cuotas_pendientes(b) and not es_contado(b)


def en_cobranza(b) -> bool:
    """Condición EN_COBRANZA: cuotas pendientes, con cobrador y no contado.
    Todo lo demás vendido es VENDIDO."""
    return bool(b.cobrador_id) and lleva_cobrador(b)


def condicion_vendida(b) -> CondicionBoleta:
    """EN_COBRANZA o VENDIDO para una boleta ya vendida (no decide BAJA/CAJA)."""
    return CondicionBoleta.EN_COBRANZA if en_cobranza(b) else CondicionBoleta.VENDIDO


# ── SQL (para .filter() de una query sobre Boleta) ──────────────────────────

def _mod_sql():
    return func.lower(func.trim(func.coalesce(Boleta.modalidad_liquidacion, "")))


def anticipo_total_sql():
    return and_(
        func.coalesce(Boleta.cuotas_pactadas, 0) > 0,
        func.coalesce(Boleta.cuotas_anticipadas, 0) >= func.coalesce(Boleta.cuotas_pactadas, 0),
    )


def es_contado_sql():
    """Misma regla que `es_contado`, como condición SQL."""
    return or_(
        _mod_sql().in_(MODALIDADES_CONTADO),
        and_(
            or_(Boleta.numero_especial.isnot(None), Boleta.numero_especial_2.isnot(None)),
            _mod_sql() != "cuotas",
        ),
        anticipo_total_sql(),
    )


def no_es_contado_sql():
    return not_(es_contado_sql())
