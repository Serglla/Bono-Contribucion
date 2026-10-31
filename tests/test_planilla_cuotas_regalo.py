"""Cuotas de regalo en las planillas de cobranza.

Una boleta con menos cuotas pactadas que columnas tiene la planilla (ver
app/cuotas.py) muestra X gris en las cuotas que no se cobran, tanto en la vista
para imprimir como en la de liquidar. Son solo dibujo: en liquidar no se pueden
marcar (sin data-cuota) y no se mezclan con la X negra de la venta.
"""
import re

import _entorno
from fastapi.testclient import TestClient

from app import auth, models
from app.main import app
from app.models import CondicionBoleta


def _armar():
    db = _entorno.base_limpia()
    pata = models.Talonera(nombre="PATA 1", tipo="COMUN", valor_cuota=15000.0,
                           num_cuotas=12, multiplicador=1.0)
    cob = models.Cobrador(nombre="MABEL")
    db.add_all([pata, cob]); db.flush()
    pl = models.Planilla(cobrador_id=cob.id, numero=1, mes=8, anio=2026, comision_pct=10.0)
    db.add(pl); db.flush()
    for numero, pactadas, baja in ((101, 12, None), (102, 10, None), (103, 9, None), (104, 10, 8)):
        db.add(models.Boleta(talonera_id=pata.id, numero_principal=numero, cobrador_id=cob.id,
                             planilla_id=pl.id, cuotas_pactadas=pactadas, cuotas_anticipadas=1,
                             cuotas_pagadas=1, mes_baja=baja,
                             condicion=CondicionBoleta.EN_COBRANZA))
    db.add(models.User(username="tester", hashed_password=auth.hash_password("x" * 10), is_admin=True))
    db.commit()
    return pl.id


def _cliente():
    c = TestClient(app)
    c.cookies.update({"access_token": "Bearer " + auth.create_access_token({"sub": "tester"})})
    return c


def test_planilla_impresa_muestra_x_gris():
    html = _cliente().get(f"/cobranza/planilla/{_armar()}/ver").text
    # 0 + 2 + 3 + 2 cuotas más allá de las pactadas (12 columnas).
    assert len(re.findall(r'class="td-q cuota-regalo">X<', html)) == 7
    assert len(re.findall(r'class="td-q cuota-venta">X<', html)) == 4


def test_liquidar_muestra_x_gris_no_clickeable():
    html = _cliente().get(f"/cobranza/liquidacion/{_armar()}").text
    celdas = re.findall(r'<td class="cq cq-regalo"[^>]*>', html)
    assert len(celdas) == 7
    assert not any("data-cuota" in c for c in celdas)


def test_pdf_se_genera():
    r = _cliente().get(f"/cobranza/planilla/{_armar()}/pdf")
    assert r.status_code == 200
    assert r.content[:4] == b"%PDF"
