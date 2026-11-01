"""Orden de la columna ESTADO de Socios (app/estado_socio.py::orden_estado).

De menor a mayor: bajas, después de quien más debe a quien menos (DEBE 4, 3, 2, 1)
sin importar el color del badge, y al final al día y contado.
"""
import json
from datetime import date

import _entorno
from fastapi.testclient import TestClient

from app import auth, models
from app.estado_socio import estados_por_socio
from app.main import app
from app.models import CondicionBoleta


def armar():
    """Un socio por estado, cargados por N° de boleta (el orden con que llega la lista)."""
    db = _entorno.base_limpia()
    pata = models.Talonera(nombre="PATA 1", tipo="COMUN", valor_cuota=15000.0,
                           num_cuotas=12, multiplicador=1.0)
    cob = models.Cobrador(nombre="MABEL")
    db.add_all([pata, cob]); db.flush()
    casos = [
        # N°, venta, anticipadas, pagadas, historial, condicion
        (8001, date(2026, 5, 1), 1, 1, {}, CondicionBoleta.BAJA),
        (8002, date(2026, 8, 1), 1, 1, {}, CondicionBoleta.EN_COBRANZA),            # DEBE 2 rojo
        (8003, date(2026, 6, 1), 1, 1, {}, CondicionBoleta.EN_COBRANZA),            # DEBE 4 rojo
        (8004, date(2026, 9, 1), 1, 1, {}, CondicionBoleta.EN_COBRANZA),            # DEBE 1
        (8005, date(2026, 7, 1), 1, 1, {}, CondicionBoleta.EN_COBRANZA),            # DEBE 3 rojo
        (8006, date(2026, 5, 1), 1, 3, {"2": "2026-06", "3": "2026-10"},
         CondicionBoleta.EN_COBRANZA),                                              # DEBE 3 amarillo
        (8007, date(2026, 10, 1), 1, 1, {}, CondicionBoleta.EN_COBRANZA),           # AL DÍA
        (8008, date(2026, 5, 1), 12, 12, {}, CondicionBoleta.VENDIDO),              # CONTADO
    ]
    for numero, venta, ant, pagadas, hist, cond in casos:
        socio = models.Comprador(apellido_nombre=f"SOCIO {numero}")
        db.add(socio); db.flush()
        db.add(models.Boleta(talonera_id=pata.id, numero_principal=numero, comprador_id=socio.id,
                             cobrador_id=cob.id, fecha_venta=venta, cuotas_pactadas=12,
                             cuotas_anticipadas=ant, cuotas_pagadas=pagadas,
                             historial_cuotas=json.dumps(hist) if hist else None, condicion=cond))
    db.add(models.User(username="tester", hashed_password=auth.hash_password("x" * 10), is_admin=True))
    db.commit()
    return db


def test_bajas_y_despues_de_mas_deuda_a_menos():
    db = armar()
    socios = db.query(models.Comprador).all()
    estados, _ = estados_por_socio(db, models, socios)
    textos = [e["texto"] for e in sorted(estados.values(), key=lambda e: e["orden"])]
    assert textos == ["BAJA", "DEBE 4", "DEBE 3", "DEBE 3", "DEBE 2", "DEBE 1", "AL DÍA", "CONTADO"]


def test_a_igual_deuda_primero_el_que_no_pago_el_mes():
    db = armar()
    socios = db.query(models.Comprador).all()
    estados, _ = estados_por_socio(db, models, socios)
    debe_3 = sorted((e for e in estados.values() if e["texto"] == "DEBE 3"), key=lambda e: e["orden"])
    assert [e["clase"] for e in debe_3] == ["moroso", "atrasado"]


def test_la_lista_usa_el_orden_en_la_celda():
    armar()
    c = TestClient(app)
    c.cookies.update({"access_token": "Bearer " + auth.create_access_token({"sub": "tester"})})
    html = c.get("/compradores/").text
    assert 'data-val="0" style="white-space:nowrap;"' in html       # BAJA
    assert 'data-val="961" style="white-space:nowrap;"' in html     # DEBE 4 rojo
