"""Mapa de socios: datos para los filtros y ubicaciones guardadas.

Reglas que se prueban:
  * Cada punto trae el mes de compra, la precisión y si se le puede asignar cobrador.
  * Se ofrecen todos los cobradores, aunque todavía no tengan socios.
  * Lo automático solo se acepta dentro de la ciudad; lo que pone una persona, en
    toda la zona.
  * Un punto manual o GPS no lo pisa el geocoding, ni lo borra "Recalcular", ni el
    cambio de dirección de un socio. Un punto inválido no borra el anterior.
"""
from datetime import date

import _entorno
from fastapi.testclient import TestClient

from app import auth, models
from app.main import app
from app.models import CondicionBoleta

CENTRO = (-32.4847, -58.2347)
RURAL = (-32.30, -58.40)     # dentro de la zona, fuera de la ciudad


def _armar():
    db = _entorno.base_limpia()
    pata = models.Talonera(nombre="PATA 1", tipo="COMUN", valor_cuota=15000.0, num_cuotas=12, multiplicador=1.0)
    cob = models.Cobrador(nombre="MABEL")
    db.add_all([pata, cob, models.Cobrador(nombre="NUEVO")]); db.flush()
    casos = [
        (101, "MITRE 100", date(2026, 10, 5), dict(cuotas_pactadas=9, cuotas_pagadas=1, condicion=CondicionBoleta.EN_COBRANZA)),
        (102, "MITRE 200", date(2026, 9, 5), dict(cuotas_pactadas=9, cuotas_anticipadas=9, cuotas_pagadas=9, condicion=CondicionBoleta.VENDIDO)),
        (103, "MITRE 300", None, dict(cuotas_pactadas=9, cuotas_pagadas=1, condicion=CondicionBoleta.BAJA)),
    ]
    for num, dire, fecha, kw in casos:
        c = models.Comprador(apellido_nombre=f"SOCIO {num}", direccion=dire)
        db.add(c); db.flush()
        db.add(models.Boleta(talonera_id=pata.id, numero_principal=num, comprador_id=c.id, fecha_venta=fecha, **kw))
    db.add(models.User(username="tester", hashed_password=auth.hash_password("x" * 10), is_admin=True))
    db.commit()
    return db


def _c():
    c = TestClient(app)
    c.cookies.update({"access_token": "Bearer " + auth.create_access_token({"sub": "tester"})})
    return c


def _guardar(direccion, lat, lng, precision=None):
    return _c().post("/compradores/mapa-geocode-save",
                     json={"direccion": direccion, "lat": lat, "lng": lng, "precision": precision})


def _cache(db, direccion):
    db.expire_all()
    return db.get(models.GeocodeCache, direccion)


def test_la_pantalla_trae_los_filtros_y_asignar_en_grupo():
    _armar()
    html = _c().get("/compradores/mapa").text
    for pieza in ("filtroMes", "filtroPrecision", "colorModo", "filtroSinCobrador", "btnAsignarGrupo"):
        assert pieza in html


def test_datos_para_los_filtros():
    _armar()
    _guardar("MITRE 100", *CENTRO, "casa")
    d = _c().get("/compradores/mapa-data").json()
    p = {x["numero_int"]: x for x in d["puntos"]}
    assert p[101]["mes"] == "2026-10" and p[102]["mes"] == "2026-09" and p[103]["mes"] == ""
    assert p[101]["precision"] == "casa" and p[102]["precision"] is None
    assert p[101]["asignable"] is True
    assert p[102]["asignable"] is False      # contado
    assert p[103]["asignable"] is False      # baja
    assert {c["nombre"] for c in d["cobradores"]} == {"MABEL", "NUEVO"}


def test_automatico_fuera_de_la_ciudad_queda_sin_ubicar():
    db = _armar()
    _guardar("MITRE 100", *RURAL, "calle")
    e = _cache(db, "MITRE 100")
    assert e.lat is None and e.precision is None


def test_una_persona_puede_ubicar_fuera_de_la_ciudad():
    db = _armar()
    assert _guardar("MITRE 100", *RURAL, "gps").json()["ok"] is True
    e = _cache(db, "MITRE 100")
    assert e.lat == RURAL[0] and e.precision == "gps"


def test_punto_manual_invalido_no_borra_el_anterior():
    db = _armar()
    _guardar("MITRE 100", *CENTRO, "manual")
    assert _guardar("MITRE 100", 10.0, 10.0, "manual").status_code == 400
    assert _guardar("MITRE 100", None, None, "gps").status_code == 400
    assert _cache(db, "MITRE 100").lat == CENTRO[0]


def test_lo_automatico_no_pisa_lo_manual():
    db = _armar()
    _guardar("MITRE 100", *CENTRO, "manual")
    r = _guardar("MITRE 100", -32.49, -58.24, "casa").json()
    assert r["protegida"] is True and r["lat"] == CENTRO[0]
    _guardar("MITRE 100", None, None)
    e = _cache(db, "MITRE 100")
    assert e.lat == CENTRO[0] and e.precision == "manual"
    # Una persona sí puede corregir otra vez.
    _guardar("MITRE 100", -32.49, -58.24, "gps")
    assert _cache(db, "MITRE 100").precision == "gps"


def test_recalcular_conserva_lo_manual():
    db = _armar()
    _guardar("MITRE 100", *CENTRO, "manual")
    _guardar("MITRE 200", *CENTRO, "gps")
    _guardar("MITRE 300", *CENTRO, "calle")
    db.add(models.GeocodeCache(direccion="VIEJA 1", lat=CENTRO[0], lng=CENTRO[1]))   # sin clasificar
    db.commit()
    j = _c().post("/compradores/mapa-geocode-reset").json()
    assert j["borradas"] == 2 and j["conservadas"] == 2
    assert _cache(db, "MITRE 100") is not None and _cache(db, "MITRE 200") is not None
    assert _cache(db, "MITRE 300") is None and _cache(db, "VIEJA 1") is None


def test_cambiar_direccion_conserva_lo_manual():
    db = _armar()
    c = db.query(models.Comprador).filter_by(direccion="MITRE 100").one()
    _guardar("MITRE 100", *CENTRO, "manual")
    _guardar("MITRE 999", *CENTRO, "gps")
    assert _c().post(f"/compradores/{c.id}/actualizar-direccion-mapa", json={"direccion": "mitre 999"}).json()["ok"]
    assert _cache(db, "MITRE 100").precision == "manual"
    assert _cache(db, "MITRE 999").precision == "gps"


def test_cambiar_direccion_borra_lo_automatico():
    db = _armar()
    c = db.query(models.Comprador).filter_by(direccion="MITRE 100").one()
    _guardar("MITRE 100", *CENTRO, "calle")
    _guardar("MITRE 999", *CENTRO, "calle")
    _c().post(f"/compradores/{c.id}/actualizar-direccion-mapa", json={"direccion": "mitre 999"})
    assert _cache(db, "MITRE 100") is None and _cache(db, "MITRE 999") is None


def test_la_migracion_agrega_la_columna_en_una_base_vieja():
    import sqlite3
    import tempfile, os
    from sqlalchemy import create_engine, inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import importlib.util
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location("rev0002", os.path.join(raiz, "alembic", "versions", "0002_geocode_precision.py"))
    rev = importlib.util.module_from_spec(spec); spec.loader.exec_module(rev)
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE geocode_cache (direccion TEXT PRIMARY KEY, lat REAL, lng REAL)")
        con.commit(); con.close()
        eng = create_engine("sqlite:///" + path.replace("\\", "/"))
        with eng.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                rev.upgrade()
                rev.upgrade()    # dos veces: la base nueva ya la trae y no debe fallar
            assert "precision" in {c["name"] for c in inspect(conn).get_columns("geocode_cache")}
        eng.dispose()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
