"""Tests de la plata de la liquidación del vendedor (app/routers/vendedores.py).

Reglas:
  * Boleta en CUOTAS: el vendedor se queda con la cuota 1 (comision_cuotas =
    valor_cuota). No rinde nada por ella.
  * Boleta al CONTADO: vale cuotas_vigentes(fecha de la liquidación) × valor_cuota;
    el vendedor se queda con el % (30 por defecto) y rinde el resto.
  * Cada boleta pesa según el multiplicador de su PATA (PATA 0 ×0.67, PATA 2 ×2).
  * Sumar y sacar una boleta tienen que ser espejo exacto (si no, la liquidación
    se descuadra al editarla desde el historial).
"""
from datetime import date
from types import SimpleNamespace as NS

import _entorno  # noqa: F401
from app.routers.vendedores import (
    _monto_contado_boleta,
    _restar_boleta_de_liq,
    _sumar_boleta_a_liq,
)

OCT = date(2026, 10, 5)   # contado de octubre = 9 cuotas

PATA0 = NS(valor_cuota=10000.0, num_cuotas=12, multiplicador=2 / 3)
PATA1 = NS(valor_cuota=15000.0, num_cuotas=12, multiplicador=1.0)
PATA2 = NS(valor_cuota=30000.0, num_cuotas=12, multiplicador=2.0)

CAMPOS = ("contados_vendidos", "contados_equiv", "monto_contados", "comision_contados",
          "cuotas_vendidas", "cuotas_equiv", "cuota_1_total", "monto_cuotas",
          "comision_cuotas", "total_a_rendir", "total_comision")


def liq_vacia(pct=30.0, fecha=OCT):
    base = {c: 0 for c in CAMPOS}
    base.update(fecha=fecha, comision_contados_pct=pct,
                cuotas_extras_monto=0, comision_cuotas_extras=0,
                cuotas_extras_p0_monto=0, comision_cuotas_extras_p0=0)
    return NS(**base)


def boleta(pata, modalidad=None):
    return NS(talonera=pata, modalidad_liquidacion=modalidad, numero_especial=None,
              numero_especial_2=None, cuotas_pactadas=9, cuotas_anticipadas=1)


def test_monto_contado_usa_las_cuotas_de_la_fecha_de_liquidacion():
    liq = liq_vacia()
    assert _monto_contado_boleta(boleta(PATA1), liq) == 9 * 15000
    # Misma boleta, liquidación de agosto → 11 cuotas.
    assert _monto_contado_boleta(boleta(PATA1), liq, fecha=date(2026, 8, 1)) == 11 * 15000


def test_contado_pata1():
    liq = liq_vacia()
    _sumar_boleta_a_liq(liq, boleta(PATA1), "contado")
    assert liq.contados_vendidos == 1
    assert liq.contados_equiv == 1.0
    assert liq.monto_contados == 135000
    assert liq.comision_contados == 40500          # 30 %
    assert liq.total_a_rendir == 94500             # rinde el 70 %
    assert liq.total_comision == 40500


def test_contado2_vale_lo_mismo_que_contado():
    a, b = liq_vacia(), liq_vacia()
    _sumar_boleta_a_liq(a, boleta(PATA1), "contado")
    _sumar_boleta_a_liq(b, boleta(PATA1), "contado2")
    assert (a.monto_contados, a.comision_contados) == (b.monto_contados, b.comision_contados)


def test_cuotas_el_vendedor_se_queda_la_cuota_1():
    liq = liq_vacia()
    _sumar_boleta_a_liq(liq, boleta(PATA2), "cuotas")
    assert liq.cuotas_vendidas == 1
    assert liq.cuotas_equiv == 2.0                 # PATA 2 pesa doble
    assert liq.comision_cuotas == 30000
    assert liq.total_a_rendir == 0                 # por cuotas no rinde nada
    assert liq.total_comision == 30000


def test_multiplicador_pata0():
    liq = liq_vacia()
    _sumar_boleta_a_liq(liq, boleta(PATA0), "contado")
    assert abs(liq.contados_equiv - 2 / 3) < 1e-9
    assert liq.monto_contados == 9 * 10000


def test_porcentaje_vacio_usa_30():
    liq = liq_vacia(pct=0)
    _sumar_boleta_a_liq(liq, boleta(PATA1), "contado")
    assert liq.comision_contados == 40500
    assert liq.comision_contados_pct == 30.0


def test_varias_boletas_suman():
    liq = liq_vacia()
    for pata, mod in ((PATA1, "contado"), (PATA0, "contado"), (PATA1, "cuotas"), (PATA2, "cuotas")):
        _sumar_boleta_a_liq(liq, boleta(pata), mod)
    assert liq.contados_vendidos == 2 and liq.cuotas_vendidas == 2
    assert liq.monto_contados == 135000 + 90000
    assert liq.comision_contados == 40500 + 27000
    assert liq.total_a_rendir == (135000 + 90000) - (40500 + 27000)
    assert liq.comision_cuotas == 15000 + 30000


def test_sumar_y_sacar_son_espejo():
    liq = liq_vacia()
    boletas = [(boleta(PATA1), "contado"), (boleta(PATA0), "contado2"),
               (boleta(PATA2), "cuotas"), (boleta(PATA1), "cuotas")]
    for b, mod in boletas:
        _sumar_boleta_a_liq(liq, b, mod)
    for b, mod in boletas:
        _restar_boleta_de_liq(liq, b, mod)
    for campo in CAMPOS:
        assert abs(float(getattr(liq, campo))) < 1e-6, (campo, getattr(liq, campo))


def test_sacar_sin_modalidad_la_deduce_de_la_boleta():
    liq = liq_vacia()
    b = boleta(PATA1, modalidad="contado")
    _sumar_boleta_a_liq(liq, b, "contado")
    _restar_boleta_de_liq(liq, b)              # sin pasar modalidad
    assert liq.monto_contados == 0 and liq.contados_vendidos == 0


def test_modalidad_invalida_cuenta_como_cuotas():
    liq = liq_vacia()
    _sumar_boleta_a_liq(liq, boleta(PATA1), "cualquier-cosa")
    assert liq.cuotas_vendidas == 1 and liq.contados_vendidos == 0
