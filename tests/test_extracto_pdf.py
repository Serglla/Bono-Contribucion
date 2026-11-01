"""PDF del extracto (/sorteos/extracto/{año}/{mes}/pdf).

Cada premio ("03/10/26 1° PREMIO Nº 4266") tiene que quedar entero en una línea:
la línea solo puede cortar entre un premio y el siguiente, con cualquier cantidad
de columnas y tamaño de letra.
"""
import io
import json
import re
from datetime import date

import _entorno
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app import auth, models
from app.main import app
from app.models import TipoSorteo

PREMIOS = {date(2026, 10, 3): "4266", date(2026, 10, 10): "0564", date(2026, 10, 17): "4569",
           date(2026, 10, 24): "2065", date(2026, 10, 31): "0095"}


def _armar():
    db = _entorno.base_limpia()
    pata = models.Talonera(nombre="PATA 1", tipo="COMUN", valor_cuota=15000, num_cuotas=12)
    db.add(pata); db.flush()
    c = models.Comprador(apellido_nombre="PEREZ JUAN", direccion="MITRE 10")
    db.add(c); db.flush()
    db.add(models.Boleta(talonera_id=pata.id, numero_principal=4266, comprador_id=c.id,
                         fecha_venta=date(2026, 9, 1), cuotas_pactadas=10, cuotas_anticipadas=10, cuotas_pagadas=10))
    for f, n in PREMIOS.items():
        db.add(models.Sorteo(nombre="test", tipo=TipoSorteo.SEMANAL, cifras="4,3", fecha=f,
                             num_premios=1, resultado_json=json.dumps([n])))
    db.add(models.User(username="tester", hashed_password=auth.hash_password("x" * 10), is_admin=True))
    db.commit()


def _lineas_pdf(cols, size):
    c = TestClient(app)
    c.cookies.update({"access_token": "Bearer " + auth.create_access_token({"sub": "tester"})})
    r = c.get(f"/sorteos/extracto/2026/10/pdf?cols={cols}&filas=1&size={size}")
    assert r.status_code == 200
    texto = PdfReader(io.BytesIO(r.content)).pages[0].extract_text()
    return texto.replace("\xa0", " ").splitlines()


def test_ningun_premio_se_parte_entre_lineas():
    _armar()
    premio = re.compile(r"\d\d/\d\d/\d\d 1° PREMIO Nº \d{4}")
    for cols in (1, 2, 3):
        for size in ("sm", "md", "lg"):
            lineas = [l for l in _lineas_pdf(cols, size) if "PREMIO" in l or re.search(r"\d\d/\d\d/\d\d", l)]
            enteros = [p for l in lineas for p in premio.findall(l)]
            assert len(enteros) >= len(PREMIOS), (cols, size, lineas)
            for l in lineas:
                # Sacados los premios enteros, en la línea no tiene que quedar nada suelto.
                assert premio.sub("", l).strip() == "", (cols, size, l)
