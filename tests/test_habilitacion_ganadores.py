"""Habilitación para cobrar un premio (app/routers/sorteos.py::_habilitacion_boleta).

Regla: un ganador queda HABILITADO si se cumple al menos una:
  1) pagó alguna cuota en el mes (y año) del sorteo, o
  2) se vendió en el mismo mes del sorteo, hasta el día del sorteo inclusive.
Una excepción manual (HabilitacionSorteo) manda sobre el cálculo automático.
"""
import json
from datetime import date
from types import SimpleNamespace as NS

import _entorno  # noqa: F401
from app.routers.sorteos import _habilitacion_boleta

SORTEO = NS(fecha=date(2026, 10, 17))


def boleta(historial=None, venta=None):
    return NS(historial_cuotas=json.dumps(historial) if historial is not None else None,
              fecha_venta=venta)


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
    b = NS(historial_cuotas="{no es json", fecha_venta=date(2026, 8, 1))
    assert _habilitacion_boleta(b, SORTEO)[0] is False


def test_excepcion_manual_manda():
    habilita = NS(habilitado=True, motivo="Pagó en efectivo en la sede")
    ok, motivo, manual = _habilitacion_boleta(boleta(venta=date(2026, 8, 1)), SORTEO, habilita)
    assert ok and manual and motivo == "Pagó en efectivo en la sede"

    deshabilita = NS(habilitado=False, motivo=None)
    ok, motivo, manual = _habilitacion_boleta(boleta({"3": "2026-10"}), SORTEO, deshabilita)
    assert not ok and manual and motivo == "Deshabilitado manualmente"
