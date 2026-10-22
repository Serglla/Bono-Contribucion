"""Tests de app/cuotas.py: cuántas cuotas se cobran según el mes de venta.

Campaña fija (ver _entorno.py): sorteo final junio 2027. Las últimas cuotas se
regalan: ago-2026 → 11, sep → 10, oct → 9...
"""
from datetime import date, datetime

import _entorno
from app.cuotas import cuotas_regaladas, cuotas_vigentes, meses_de_cobranza, monto_contado


def test_ejemplos_del_negocio():
    assert cuotas_vigentes(12, date(2026, 8, 15)) == 11
    assert cuotas_vigentes(12, date(2026, 9, 1)) == 10
    assert cuotas_vigentes(12, date(2026, 10, 1)) == 9
    assert cuotas_vigentes(12, date(2026, 11, 30)) == 8
    assert cuotas_vigentes(12, date(2026, 12, 10)) == 7


def test_nunca_supera_el_nominal():
    # Venta temprana (mayo 2026): 1 + 13 meses = 14, pero la talonera es de 12.
    assert cuotas_vigentes(12, date(2026, 5, 1)) == 12
    assert cuotas_vigentes(8, date(2026, 1, 1)) == 8


def test_piso_de_una_cuota():
    assert cuotas_vigentes(12, date(2027, 6, 5)) == 1     # el mes del sorteo
    assert cuotas_vigentes(12, date(2027, 9, 1)) == 1     # después del sorteo


def test_el_dia_del_mes_no_importa():
    assert cuotas_vigentes(12, date(2026, 8, 1)) == cuotas_vigentes(12, date(2026, 8, 31))


def test_nominal_vacio_usa_12():
    assert cuotas_vigentes(None, date(2026, 5, 1)) == 12
    assert cuotas_vigentes(0, date(2026, 5, 1)) == 12


def test_fechas_como_texto_o_datetime():
    assert cuotas_vigentes(12, "2026-10") == 9
    assert cuotas_vigentes(12, "2026-10-15T10:00") == 9
    assert cuotas_vigentes(12, datetime(2026, 10, 20, 23, 59)) == 9


def test_regaladas_y_monto_contado():
    assert cuotas_regaladas(12, date(2026, 10, 1)) == 3
    assert cuotas_regaladas(12, date(2026, 5, 1)) == 0
    # Contado de octubre: paga 9 cuotas, no 12.
    assert monto_contado(12, 15000, date(2026, 10, 1)) == 9 * 15000
    assert monto_contado(12, None, date(2026, 10, 1)) == 0


def test_meses_de_cobranza():
    assert meses_de_cobranza(date(2026, 8, 1)) == 10
    assert meses_de_cobranza(date(2027, 8, 1)) == -2


def test_si_se_corre_el_sorteo_cambian_las_cuotas():
    try:
        _entorno.fijar_campana(sorteo_final_anio=2027, sorteo_final_mes=7)
        assert cuotas_vigentes(12, date(2026, 10, 1)) == 10
    finally:
        _entorno.fijar_campana()
    assert cuotas_vigentes(12, date(2026, 10, 1)) == 9
