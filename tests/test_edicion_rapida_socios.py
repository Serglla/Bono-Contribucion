"""Edición rápida en la lista de Socios (POST /compradores/{id}/edicion-rapida).

Solo cambia nombre, dirección y zona. No toca teléfono, cobrador, vendedor ni cuotas.
Nombre vacío o zona inexistente: no guarda nada. Hace falta permiso de edición.
"""
from datetime import date

import _entorno
from fastapi.testclient import TestClient

from app import auth, models
from app.main import app
from app.models import CondicionBoleta


def _armar():
    db = _entorno.base_limpia()
    pata = models.Talonera(nombre="PATA 1", tipo="COMUN", valor_cuota=15000.0, num_cuotas=12, multiplicador=1.0)
    ven, cob = models.Vendedor(nombre="ARIEL"), models.Cobrador(nombre="MABEL")
    db.add_all([pata, ven, cob]); db.flush()
    z1, z2 = models.Zona(nombre="107"), models.Zona(nombre="9B")
    db.add_all([z1, z2]); db.flush()
    s = models.Comprador(apellido_nombre="PEREZ JUAN", direccion="MITRE 10", telefono="442100", zona_id=z1.id)
    db.add(s); db.flush()
    b = models.Boleta(talonera_id=pata.id, numero_principal=301, comprador_id=s.id, vendedor_id=ven.id,
                      cobrador_id=cob.id, fecha_venta=date(2026, 10, 5), cuotas_pactadas=9, cuotas_anticipadas=1,
                      cuotas_pagadas=2, historial_cuotas='{"2": "2026-11"}', condicion=CondicionBoleta.EN_COBRANZA)
    db.add(b)
    db.add(models.User(username="tester", hashed_password=auth.hash_password("x" * 10), is_admin=True))
    db.add(models.User(username="mirador", hashed_password=auth.hash_password("x" * 10), is_admin=False))
    db.commit()
    return db, s, b, z2


def _c(usuario="tester"):
    c = TestClient(app)
    c.cookies.update({"access_token": "Bearer " + auth.create_access_token({"sub": usuario})})
    return c


def _editar(s, usuario="tester", **campos):
    form = {"apellido_nombre": "PEREZ JUAN", "direccion": "MITRE 10", "zona": "107"}
    form.update(campos)
    return _c(usuario).post(f"/compradores/{s.id}/edicion-rapida", data=form)


def test_cambia_nombre_direccion_y_zona_y_nada_mas():
    db, s, b, z2 = _armar()
    r = _editar(s, apellido_nombre=" pérez juana ", direccion="rocamora 5", zona="9b")
    assert r.status_code == 200
    assert r.json() == {"nombre": "PÉREZ JUANA", "direccion": "ROCAMORA 5", "zona": "9B"}
    db.expire_all()
    s, b = db.get(models.Comprador, s.id), db.get(models.Boleta, b.id)
    assert (s.apellido_nombre, s.direccion, s.zona_id) == ("PÉREZ JUANA", "ROCAMORA 5", z2.id)
    assert s.telefono == "442100"
    assert b.cobrador.nombre == "MABEL" and b.vendedor.nombre == "ARIEL"
    assert (b.cuotas_pactadas, b.cuotas_pagadas) == (9, 2) and b.fecha_venta == date(2026, 10, 5)
    assert b.historial_cuotas == '{"2": "2026-11"}'


def test_direccion_y_zona_se_pueden_vaciar():
    db, s, _, _ = _armar()
    assert _editar(s, direccion="  ", zona="").status_code == 200
    db.expire_all()
    s = db.get(models.Comprador, s.id)
    assert s.direccion is None and s.zona_id is None


def test_nombre_vacio_no_guarda():
    db, s, _, _ = _armar()
    assert _editar(s, apellido_nombre="  ", direccion="OTRA 1").status_code == 400
    db.expire_all()
    s = db.get(models.Comprador, s.id)
    assert s.apellido_nombre == "PEREZ JUAN" and s.direccion == "MITRE 10"


def test_zona_inexistente_no_guarda_ni_se_crea():
    db, s, _, _ = _armar()
    r = _editar(s, zona="999", direccion="OTRA 1")
    assert r.status_code == 400 and "999" in r.json()["error"]
    db.expire_all()
    assert db.get(models.Comprador, s.id).direccion == "MITRE 10"
    assert db.query(models.Zona).filter_by(nombre="999").first() is None


def test_sin_permiso_no_puede_y_no_ve_el_boton():
    db, s, _, _ = _armar()
    assert _editar(s, usuario="mirador", direccion="OTRA 1").status_code == 403
    db.expire_all()
    assert db.get(models.Comprador, s.id).direccion == "MITRE 10"
    assert 'id="btnEdicionRapida"' in _c().get("/compradores/").text
    assert 'id="btnEdicionRapida"' not in _c("mirador").get("/compradores/").text


def test_socio_inexistente():
    _armar()
    r = _c().post("/compradores/99999/edicion-rapida", data={"apellido_nombre": "X"})
    assert r.status_code == 404
