"""Habilitación para cobrar un premio (app/routers/sorteos.py::_habilitacion_boleta).

Regla: un ganador queda HABILITADO si se cumple al menos una:
  1) pagó alguna cuota en el mes (y año) del sorteo, o
  2) se vendió en el mismo mes del sorteo, hasta el día del sorteo inclusive, o
  3) es un contado comprado hasta el día del sorteo, o
  4) tiene cuotas adelantadas que cubren el mes del sorteo (o ya pagó todas).
En el SORTEO FINAL, en cambio, es indispensable tener todo pago (o ser contado).
Una excepción manual (HabilitacionSorteo) manda sobre el cálculo automático.
"""
import json
from datetime import date
from types import SimpleNamespace as NS

import _entorno  # noqa: F401
from app.routers.sorteos import _habilitacion_boleta

SORTEO = NS(fecha=date(2026, 10, 17), tipo="SEMANAL")
FINAL = NS(fecha=date(2027, 6, 26), tipo="FINAL")


def boleta(historial=None, venta=None, **kw):
    datos = dict(modalidad_liquidacion=None, numero_especial=None, numero_especial_2=None,
                 cuotas_pactadas=12, cuotas_anticipadas=1)
    datos.update(kw)
    return NS(historial_cuotas=json.dumps(historial) if historial is not None else None,
              fecha_venta=venta, **datos)


def test_pago_cuota_del_mes():
    ok, motivo, manual = _habilitacion_boleta(boleta({"2": "2026-09", "3": "2026-10"}), SORTEO)
    assert ok and not manual and "Pagó" in motivo


def test_pago_del_mismo_mes_de_otro_anio_no_sirve():
    ok, motivo, _ = _habilitacion_boleta(boleta({"3": "2025-10"}, venta=date(2025, 9, 1)), SORTEO)
    assert not ok and "octubre" in motivo


def test_historial_viejo_sin_anio_matchea_por_mes():
    assert _habilitacion_boleta(boleta({"3": 10}), SORTEO)[0]


def test_debe_el_mes_no_habilitado():
    ok, motivo, _ = _habilitacion_boleta(boleta({"2": "2026-09"}, venta=date(2026, 8, 1)), SORTEO)
    assert not ok and "falta de pago" in motivo


def test_vendida_en_el_mes_hasta_el_dia_del_sorteo():
    assert _habilitacion_boleta(boleta(venta=date(2026, 10, 2)), SORTEO)[0]
    assert _habilitacion_boleta(boleta(venta=date(2026, 10, 17)), SORTEO)[0]    # el mismo día
    assert not _habilitacion_boleta(boleta(venta=date(2026, 10, 18)), SORTEO)[0]


def test_historial_roto_no_explota():
    b = boleta(venta=date(2026, 8, 1))
    b.historial_cuotas = "{no es json"
    assert _habilitacion_boleta(b, SORTEO)[0] is False


def test_contado_no_debe_nada_queda_habilitado():
    # Caso real: 8266 de ENTRECOR SA, contado 12/12 comprado el 25/07.
    ok, motivo, manual = _habilitacion_boleta(
        boleta(venta=date(2026, 7, 25), cuotas_anticipadas=12), SORTEO)
    assert ok and not manual and motivo == "Pagó al contado"
    assert _habilitacion_boleta(boleta(venta=date(2026, 7, 25), modalidad_liquidacion="contado"), SORTEO)[0]
    assert _habilitacion_boleta(boleta(venta=date(2026, 7, 25), numero_especial=12), SORTEO)[0]


def test_contado_comprado_despues_del_sorteo_no_habilita():
    b = boleta(venta=date(2026, 10, 18), cuotas_anticipadas=12)
    assert not _habilitacion_boleta(b, SORTEO)[0]


def test_liquidado_en_cuotas_con_especial_viejo_sigue_debiendo():
    b = boleta(venta=date(2026, 7, 25), modalidad_liquidacion="cuotas", numero_especial=12)
    assert not _habilitacion_boleta(b, SORTEO)[0]


def _sorteo_del(anio, mes):
    return NS(fecha=date(anio, mes, 10), tipo="SEMANAL")


def test_cinco_cuotas_de_entrada_cubren_hasta_la_quinta():
    # Vendida en agosto con 5 pagas: agosto, sep, oct, nov y dic.
    b = boleta(venta=date(2026, 8, 1), cuotas_anticipadas=5)
    ok, motivo, _ = _habilitacion_boleta(b, _sorteo_del(2026, 12))
    assert ok and motivo == "Tiene cuotas adelantadas"
    ok, motivo, _ = _habilitacion_boleta(b, _sorteo_del(2027, 1))
    assert not ok and "falta de pago" in motivo


def test_adelanto_pagado_en_cobranza_tambien_cubre():
    # En septiembre pagó la 2, 3 y 4: cubre hasta noviembre aunque no pague en oct.
    b = boleta({"2": "2026-09", "3": "2026-09", "4": "2026-09"}, venta=date(2026, 8, 1))
    assert _habilitacion_boleta(b, SORTEO)[0]


def test_pago_posterior_al_sorteo_no_cubre_para_atras():
    b = boleta({"2": "2026-11", "3": "2026-11", "4": "2026-11"}, venta=date(2026, 8, 1))
    assert not _habilitacion_boleta(b, SORTEO)[0]


def test_atrasado_igual_necesita_pagar_el_mes():
    # Vendida en mayo, solo pagó la 2: debe meses, y en octubre no pagó.
    b = boleta({"2": "2026-09"}, venta=date(2026, 5, 1))
    assert not _habilitacion_boleta(b, SORTEO)[0]
    # Si paga una cuota en octubre, entra aunque siga atrasado.
    b = boleta({"2": "2026-09", "3": "2026-10"}, venta=date(2026, 5, 1))
    assert _habilitacion_boleta(b, SORTEO)[0]


def test_toda_paga_en_cobranza_entra_en_los_meses_siguientes():
    # 3 cuotas pactadas, todas pagas en agosto: en octubre no debe nada.
    b = boleta({"2": "2026-08", "3": "2026-08"}, venta=date(2026, 8, 1), cuotas_pactadas=3)
    ok, motivo, _ = _habilitacion_boleta(b, SORTEO)
    assert ok and motivo == "Tiene todas las cuotas pagas"


def test_final_exige_todo_pago():
    # Pagó la cuota de junio y va adelantado para el mes, pero le falta una: no entra.
    hist = {str(n): "2027-06" for n in range(2, 12)}
    ok, motivo, _ = _habilitacion_boleta(boleta(hist, venta=date(2026, 7, 1)), FINAL)
    assert not ok and "sorteo final" in motivo and "11 de 12" in motivo
    # Vendida en el mes del final con solo la cuota 1 tampoco alcanza si debe más.
    assert not _habilitacion_boleta(boleta(venta=date(2027, 6, 1), cuotas_pactadas=2), FINAL)[0]


def test_final_con_todo_pago_o_contado_entra():
    hist = {str(n): "2027-06" for n in range(2, 13)}
    ok, motivo, _ = _habilitacion_boleta(boleta(hist, venta=date(2026, 7, 1)), FINAL)
    assert ok and motivo == "Tiene todas las cuotas pagas"
    ok, motivo, _ = _habilitacion_boleta(boleta(venta=date(2026, 7, 1), cuotas_anticipadas=12), FINAL)
    assert ok and motivo == "Pagó al contado"


def test_final_no_cuenta_lo_pagado_despues_del_sorteo():
    hist = {str(n): "2027-07" for n in range(2, 13)}
    assert not _habilitacion_boleta(boleta(hist, venta=date(2026, 7, 1)), FINAL)[0]


def test_final_la_excepcion_manual_manda():
    habilita = NS(habilitado=True, motivo=None)
    assert _habilitacion_boleta(boleta(venta=date(2026, 7, 1)), FINAL, habilita)[0]


def test_excepcion_manual_manda():
    habilita = NS(habilitado=True, motivo="Pagó en efectivo en la sede")
    ok, motivo, manual = _habilitacion_boleta(boleta(venta=date(2026, 8, 1)), SORTEO, habilita)
    assert ok and manual and motivo == "Pagó en efectivo en la sede"

    deshabilita = NS(habilitado=False, motivo=None)
    ok, motivo, manual = _habilitacion_boleta(boleta({"3": "2026-10"}), SORTEO, deshabilita)
    assert not ok and manual and motivo == "Deshabilitado manualmente"
