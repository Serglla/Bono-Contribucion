"""Alta de socio (POST /compradores/crear), de punta a punta.

Es el modal "Nuevo Socio": se busca la boleta, se cargan los datos y se guarda.
Reglas que se prueban:
  * El socio queda en MAYÚSCULAS; la boleta queda con socio, fecha y vendedor.
  * Cuotas que se cobran = cuotas_vigentes(fecha): la del modal, si no la de la
    liquidación del vendedor. Nunca más que eso (oct-2026 → 9).
  * CONTADO (liquidada contado/contado2, o número especial en data vieja):
    pactadas = anticipadas = pagas = todas, sin cobrador y VENDIDO.
  * Liquidada explícitamente EN CUOTAS: manda eso; un número especial colgado
    se limpia.
  * Cobrador: solo si queda algo por cobrar. El elegido a mano pisa al de la zona.
  * Zona: se puede crear por nombre; una zona tiene un solo vendedor.
  * Reintentar el mismo alta NO duplica el socio (idempotente).
  * Números especiales: se asignan solo si están libres.
"""
from datetime import date, datetime

import _entorno
from fastapi.testclient import TestClient

from app import auth, models
from app.main import app
from app.models import CondicionBoleta

OCT = "2026-10-05"     # venta de octubre → 9 cuotas


def _armar():
    db = _entorno.base_limpia()
    pata = models.Talonera(nombre="PATA 1", tipo="COMUN", valor_cuota=15000.0, num_cuotas=12)
    pool = models.Talonera(nombre="CONTADO", tipo="CONTADO")
    ven, ven2 = models.Vendedor(nombre="ARIEL"), models.Vendedor(nombre="LUCAS")
    cob, cob2 = models.Cobrador(nombre="MABEL"), models.Cobrador(nombre="CARO")
    db.add_all([pata, pool, ven, ven2, cob, cob2]); db.flush()
    zona = models.Zona(nombre="6", vendedor_id=ven.id)
    db.add(zona); db.flush()
    db.add(models.ZonaCobrador(zona_id=zona.id, cobrador_id=cob.id))
    db.add(models.User(username="tester", hashed_password=auth.hash_password("x" * 10), is_admin=True))
    db.commit()
    return db, NS(pata=pata, pool=pool, ven=ven, ven2=ven2, cob=cob, cob2=cob2, zona=zona)


class NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _boleta(db, x, numero=4783, **kw):
    datos = dict(talonera_id=x.pata.id, numero_principal=numero, vendedor_id=x.ven.id,
                 condicion=CondicionBoleta.VENDIDO)
    datos.update(kw)
    b = models.Boleta(**datos)
    db.add(b); db.commit()
    return b


def _alta(boleta_id, usuario="tester", **campos):
    form = {"apellido_nombre": "pérez juan", "direccion": "san martín 123",
            "telefono": " 3442 555 ", "fecha_compra": OCT, "cuotas_pactadas": "12",
            "cuotas_anticipadas": "1"}
    if boleta_id is not None:
        form["boleta_id"] = str(boleta_id)
    form.update({k: str(v) for k, v in campos.items()})
    c = TestClient(app)
    c.cookies.update({"access_token": "Bearer " + auth.create_access_token({"sub": usuario})})
    return c.post("/compradores/crear", data=form,
                  headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
                  follow_redirects=False)


def _b(db, b):
    db.expire_all()
    return db.get(models.Boleta, b.id)


def test_alta_en_cuotas():
    db, x = _armar()
    b = _boleta(db, x)
    r = _alta(b.id, zona_id=x.zona.id)
    assert r.status_code == 200, r.text[:300]
    j = r.json()
    b = _b(db, b)
    socio = db.get(models.Comprador, j["comprador_id"])
    assert socio.apellido_nombre == "PÉREZ JUAN"
    assert socio.direccion == "SAN MARTÍN 123" and socio.telefono == "3442 555"
    assert socio.zona_id == x.zona.id
    assert b.comprador_id == socio.id and b.fecha_venta == date(2026, 10, 5)
    assert (b.cuotas_pactadas, b.cuotas_anticipadas, b.cuotas_pagadas) == (9, 1, 1)
    assert b.cobrador_id == x.cob.id                     # el de la zona
    assert b.condicion == CondicionBoleta.EN_COBRANZA
    assert (j["cuotas_pactadas"], j["cuotas_anticipadas"], j["cobrador_id"]) == (9, 1, x.cob.id)
    db.close()


def test_sin_fecha_usa_la_de_la_liquidacion():
    db, x = _armar()
    liq = models.LiquidacionVendedor(vendedor_id=x.ven.id, fecha=datetime(2026, 8, 28, 18, 0))
    db.add(liq); db.flush()
    b = _boleta(db, x, liquidacion_vendedor_id=liq.id)
    _alta(b.id, zona_id=x.zona.id, fecha_compra="")
    assert _b(db, b).cuotas_pactadas == 11               # agosto → 11
    db.close()


def test_contado_liquidado_sin_cobrador_y_todo_pago():
    for modalidad in ("contado", "contado2"):
        db, x = _armar()
        b = _boleta(db, x, modalidad_liquidacion=modalidad)
        _alta(b.id, zona_id=x.zona.id, cuotas_anticipadas=1, cobrador_id=x.cob2.id)
        b = _b(db, b)
        assert (b.cuotas_pactadas, b.cuotas_anticipadas, b.cuotas_pagadas) == (9, 9, 9), modalidad
        assert b.cobrador_id is None                     # ni el de la zona ni el manual
        assert b.condicion == CondicionBoleta.VENDIDO
        db.close()


def test_contado_por_numero_especial_en_data_vieja():
    db, x = _armar()
    b = _boleta(db, x, numero_especial=42, talonera_especial_id=x.pool.id)
    _alta(b.id, zona_id=x.zona.id)
    b = _b(db, b)
    assert (b.cuotas_pactadas, b.cuotas_pagadas, b.cobrador_id) == (9, 9, None)
    db.close()


def test_liquidada_en_cuotas_limpia_el_especial_colgado():
    db, x = _armar()
    b = _boleta(db, x, modalidad_liquidacion="cuotas", numero_especial=42,
                talonera_especial_id=x.pool.id)
    _alta(b.id, zona_id=x.zona.id)
    b = _b(db, b)
    assert b.numero_especial is None and b.talonera_especial_id is None
    assert (b.cuotas_pactadas, b.cuotas_pagadas) == (9, 1)
    assert b.condicion == CondicionBoleta.EN_COBRANZA
    db.close()


def test_toda_paga_desde_el_modal_no_lleva_cobrador():
    db, x = _armar()
    b = _boleta(db, x)
    _alta(b.id, zona_id=x.zona.id, cuotas_pactadas=9, cuotas_anticipadas=9)
    b = _b(db, b)
    assert (b.cuotas_pactadas, b.cuotas_pagadas, b.cobrador_id) == (9, 9, None)
    assert b.condicion == CondicionBoleta.VENDIDO
    db.close()


def test_no_se_anticipan_ni_pactan_mas_cuotas_de_las_que_hay():
    db, x = _armar()
    b = _boleta(db, x)
    _alta(b.id, zona_id=x.zona.id, cuotas_pactadas=12, cuotas_anticipadas=15)
    b = _b(db, b)
    assert (b.cuotas_pactadas, b.cuotas_anticipadas) == (9, 9)
    db.close()


def test_cobrador_manual_pisa_al_de_la_zona():
    db, x = _armar()
    b = _boleta(db, x)
    _alta(b.id, zona_id=x.zona.id, cobrador_id=x.cob2.id)
    assert _b(db, b).cobrador_id == x.cob2.id
    db.close()


def test_cobrador_manual_invalido_usa_el_de_la_zona():
    db, x = _armar()
    b = _boleta(db, x)
    _alta(b.id, zona_id=x.zona.id, cobrador_id="9999")
    assert _b(db, b).cobrador_id == x.cob.id
    db.close()


def test_reintentar_el_alta_no_duplica_el_socio():
    db, x = _armar()
    b = _boleta(db, x)
    r1 = _alta(b.id, zona_id=x.zona.id)
    r2 = _alta(b.id, zona_id=x.zona.id, apellido_nombre="perez juan carlos")
    assert r1.json()["comprador_id"] == r2.json()["comprador_id"]
    assert db.query(models.Comprador).count() == 1
    assert db.query(models.Comprador).one().apellido_nombre == "PEREZ JUAN CARLOS"
    db.close()


def test_sin_boleta_no_crea_socio():
    db, x = _armar()
    assert _alta(None).status_code == 400
    assert _alta(9999).status_code == 400
    assert db.query(models.Comprador).count() == 0
    db.close()


def test_zona_nueva_y_vendedor_de_la_zona():
    db, x = _armar()
    b = _boleta(db, x, vendedor_id=None)
    r = _alta(b.id, zona_nueva=" barrio norte ", vendedor_id=x.ven2.id)
    j = r.json()
    zona = db.query(models.Zona).filter_by(nombre="BARRIO NORTE").one()
    assert j["nueva_zona"]["id"] == zona.id and j["zona_id"] == zona.id
    assert zona.vendedor_id == x.ven2.id                 # zona sin vendedor: se vincula
    assert _b(db, b).vendedor_id == x.ven2.id
    db.close()


def test_zona_con_vendedor_manda_sobre_el_del_modal():
    db, x = _armar()
    b = _boleta(db, x, vendedor_id=None)
    _alta(b.id, zona_id=x.zona.id, vendedor_id=x.ven2.id)
    assert _b(db, b).vendedor_id == x.ven.id
    db.close()


def test_numeros_especiales_solo_si_estan_libres():
    db, x = _armar()
    otra = _boleta(db, x, numero=1111, numero_especial=7, talonera_especial_id=x.pool.id)
    b = _boleta(db, x, modalidad_liquidacion="contado")
    _alta(b.id, zona_id=x.zona.id, te1=x.pool.id, ne1=7, te2=x.pool.id, ne2=8)
    b = _b(db, b)
    assert b.numero_especial is None                     # el 7 ya lo tiene otra
    assert (b.numero_especial_2, b.talonera_especial_2_id) == (8, x.pool.id)
    db.close()


def test_boleta_de_baja_no_cambia_de_condicion():
    db, x = _armar()
    b = _boleta(db, x, condicion=CondicionBoleta.BAJA)
    _alta(b.id, zona_id=x.zona.id)
    assert _b(db, b).condicion == CondicionBoleta.BAJA
    db.close()


def test_sin_permiso_de_editar_socios_no_puede():
    import json
    db, x = _armar()
    db.add(models.User(username="solo_ver", hashed_password=auth.hash_password("x" * 10),
                       permissions=json.dumps({"compradores": {"ver": True, "editar": False}})))
    db.commit()
    b = _boleta(db, x)
    assert _alta(b.id, usuario="solo_ver").status_code == 403
    assert _b(db, b).comprador_id is None
    db.close()
