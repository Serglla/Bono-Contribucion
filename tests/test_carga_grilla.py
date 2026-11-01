"""Grilla de carga de socios (/compradores/carga), de punta a punta.

Reglas que se prueban:
  * Se busca por el PRIMER número de una talonera común; trae fecha, vendedor y
    cuotas de la liquidación.
  * Número inexistente, secundario, con socio o de baja: no se puede cargar.
  * Alta: usa el mismo servicio que el modal, pero el vendedor de la liquidación
    manda sobre el de la zona. Sin vendedor en la fila, manda la zona.
  * Una zona que no existe no se crea sola.
  * Reintentar un alta no duplica; otra persona sobre el mismo número da error.
  * Corregir una fila: datos del socio, vendedor, cuotas. Con pagos en cobranza las
    cuotas pagas no se tocan. Emplanillada, no cambia de cobrador.
"""
from datetime import date, datetime

import _entorno
from fastapi.testclient import TestClient

from app import auth, models
from app.main import app
from app.models import CondicionBoleta


class NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _armar():
    db = _entorno.base_limpia()
    pata = models.Talonera(nombre="PATA 1", tipo="COMUN", valor_cuota=15000.0, num_cuotas=12, multiplicador=1.0)
    pool = models.Talonera(nombre="CONTADO", tipo="CONTADO")
    ariel, hugo = models.Vendedor(nombre="ARIEL"), models.Vendedor(nombre="HUGO")
    mabel, caro = models.Cobrador(nombre="MABEL"), models.Cobrador(nombre="CARO")
    db.add_all([pata, pool, ariel, hugo, mabel, caro]); db.flush()
    z107 = models.Zona(nombre="107", vendedor_id=hugo.id)
    z100 = models.Zona(nombre="100")
    db.add_all([z107, z100]); db.flush()
    db.add(models.ZonaCobrador(zona_id=z107.id, cobrador_id=caro.id))
    db.add(models.ZonaCobrador(zona_id=z100.id, cobrador_id=mabel.id))
    liq = models.LiquidacionVendedor(vendedor_id=ariel.id, fecha=datetime(2026, 10, 28))
    db.add(liq); db.flush()
    db.add(models.User(username="tester", hashed_password=auth.hash_password("x" * 10), is_admin=True))
    db.commit()
    return db, NS(pata=pata, pool=pool, ariel=ariel, hugo=hugo, mabel=mabel, caro=caro, z107=z107, z100=z100, liq=liq)


def _boleta(db, x, numero=301, **kw):
    datos = dict(talonera_id=x.pata.id, numero_principal=numero, numeros_adicionales="1802,3303",
                 vendedor_id=x.ariel.id, liquidacion_vendedor_id=x.liq.id, modalidad_liquidacion="cuotas",
                 condicion=CondicionBoleta.CAJA)
    datos.update(kw)
    b = models.Boleta(**datos)
    db.add(b); db.commit()
    return b


def _c():
    c = TestClient(app)
    c.cookies.update({"access_token": "Bearer " + auth.create_access_token({"sub": "tester"})})
    return c


def _fila(b, x=None, **campos):
    form = {"boleta_id": b.id, "apellido_nombre": "pérez juan", "direccion": "mitre 10", "telefono": "442100",
            "zona": "107", "fecha": "2026-10-28", "vendedor_id": x.ariel.id if x else "",
            "cuotas_pactadas": 9, "cuotas_anticipadas": 1}
    form.update(campos)
    return _c().post("/compradores/carga/fila", data={k: str(v) for k, v in form.items()})


def _b(db, b):
    db.expire_all()
    return db.get(models.Boleta, b.id)


def test_busca_el_primer_numero_y_trae_la_liquidacion():
    db, x = _armar()
    _boleta(db, x)
    r = _c().get("/compradores/carga/boleta/301")
    d = r.json()
    assert r.status_code == 200
    assert d["numero"] == "0301" and d["otros"] == ["1802", "3303"] and d["talonera"] == "X1"
    assert d["fecha"] == "2026-10-28" and d["vendedor"] == "ARIEL" and d["liquidada"] is True
    assert d["cuotas"] == 9 and d["anticipadas"] == 1 and d["contado"] is False


def test_contado_trae_todas_las_cuotas_pagas():
    db, x = _armar()
    _boleta(db, x, modalidad_liquidacion="contado")
    d = _c().get("/compradores/carga/boleta/301").json()
    assert d["contado"] is True and d["cuotas"] == 9 and d["anticipadas"] == 9


def test_numeros_que_no_se_pueden_cargar():
    db, x = _armar()
    _boleta(db, x)
    _boleta(db, x, numero=500, condicion=CondicionBoleta.BAJA)
    _boleta(db, x, numero=777, talonera_id=x.pool.id)
    c = _c()
    assert c.get("/compradores/carga/boleta/9999").status_code == 404
    assert c.get("/compradores/carga/boleta/1802").status_code == 404      # no es el primero
    assert c.get("/compradores/carga/boleta/777").status_code == 404       # pool de contado
    assert c.get("/compradores/carga/boleta/500").status_code == 409       # de baja


def test_el_pool_de_contado_no_tapa_a_la_boleta_comun():
    db, x = _armar()
    _boleta(db, x, numero=301, talonera_id=x.pool.id)
    comun = _boleta(db, x, numero=301)
    assert _c().get("/compradores/carga/boleta/301").json()["boleta_id"] == comun.id


def test_alta_el_vendedor_de_la_liquidacion_manda_sobre_la_zona():
    db, x = _armar()
    b = _boleta(db, x)
    r = _fila(b, x)     # zona 107 es de HUGO, la liquidó ARIEL
    assert r.status_code == 200
    b = _b(db, b)
    assert b.vendedor_id == x.ariel.id
    assert b.comprador.apellido_nombre == "PÉREZ JUAN" and b.comprador.zona_id == x.z107.id
    assert b.cobrador_id == x.caro.id and b.fecha_venta == date(2026, 10, 28)
    assert (b.cuotas_pactadas, b.cuotas_pagadas) == (9, 1)
    assert b.condicion == CondicionBoleta.EN_COBRANZA
    assert db.get(models.Zona, x.z107.id).vendedor_id == x.hugo.id     # la zona no cambia


def test_alta_sin_vendedor_manda_la_zona():
    db, x = _armar()
    b = _boleta(db, x, vendedor_id=None, liquidacion_vendedor_id=None, modalidad_liquidacion=None)
    assert _fila(b).status_code == 200
    assert _b(db, b).vendedor_id == x.hugo.id


def test_alta_de_contado_sin_cobrador():
    db, x = _armar()
    b = _boleta(db, x, modalidad_liquidacion="contado")
    d = _fila(b, x, cuotas_anticipadas=1).json()
    b = _b(db, b)
    assert (b.cuotas_pactadas, b.cuotas_pagadas) == (9, 9) and b.cobrador_id is None
    assert b.condicion == CondicionBoleta.VENDIDO and d["contado"] is True


def test_zona_inexistente_no_se_crea_ni_se_guarda():
    db, x = _armar()
    b = _boleta(db, x)
    r = _fila(b, x, zona="999")
    assert r.status_code == 400 and "999" in r.json()["error"]
    assert _b(db, b).comprador_id is None
    assert db.query(models.Zona).filter_by(nombre="999").first() is None


def test_falta_nombre():
    db, x = _armar()
    b = _boleta(db, x)
    assert _fila(b, x, apellido_nombre="  ").status_code == 400
    assert _b(db, b).comprador_id is None


def test_reintento_no_duplica_y_otra_persona_da_error():
    db, x = _armar()
    b = _boleta(db, x)
    assert _fila(b, x).status_code == 200
    assert _fila(b, x).status_code == 200
    assert db.query(models.Comprador).count() == 1
    r = _fila(b, x, apellido_nombre="OTRA PERSONA")
    assert r.status_code == 409 and "PÉREZ JUAN" in r.json()["error"]
    assert _b(db, b).comprador.apellido_nombre == "PÉREZ JUAN"
    assert _c().get("/compradores/carga/boleta/301").status_code == 409


def test_corregir_fila_cambia_datos_vendedor_y_cobrador_por_zona():
    db, x = _armar()
    b = _boleta(db, x)
    _fila(b, x)
    r = _fila(b, x, editar=1, apellido_nombre="pérez juana", zona="100", vendedor_id=x.hugo.id, cuotas_anticipadas=3)
    assert r.status_code == 200
    b = _b(db, b)
    assert b.comprador.apellido_nombre == "PÉREZ JUANA" and b.comprador.zona_id == x.z100.id
    assert b.vendedor_id == x.hugo.id and b.cobrador_id == x.mabel.id
    assert (b.cuotas_anticipadas, b.cuotas_pagadas) == (3, 3)
    assert db.query(models.Comprador).count() == 1


def test_corregir_con_pagos_no_toca_cuotas_ni_fecha():
    db, x = _armar()
    b = _boleta(db, x)
    _fila(b, x)
    b = _b(db, b)
    b.historial_cuotas = '{"2": "2026-11"}'
    b.cuotas_pagadas = 2
    db.commit()
    r = _fila(b, x, editar=1, apellido_nombre="pérez juana", zona="100", fecha="2026-05-01", cuotas_anticipadas=1)
    assert r.status_code == 200 and r.json()["tiene_pagos"] is True
    b = _b(db, b)
    assert b.comprador.apellido_nombre == "PÉREZ JUANA"
    assert b.cuotas_pagadas == 2 and b.fecha_venta == date(2026, 10, 28) and b.cobrador_id == x.caro.id


def test_corregir_emplanillada_no_cambia_de_cobrador():
    db, x = _armar()
    b = _boleta(db, x)
    _fila(b, x)
    pl = models.Planilla(cobrador_id=x.caro.id, numero=1, mes=11, anio=2026, comision_pct=10.0)
    db.add(pl); db.flush()
    b = _b(db, b)
    b.planilla_id = pl.id
    db.commit()
    assert _fila(b, x, editar=1, zona="100").status_code == 200
    assert _b(db, b).cobrador_id == x.caro.id


def test_ventana_flotante_en_el_resto_del_sistema():
    _armar()
    c = _c()
    html = c.get("/compradores/").text
    assert 'id="ventanaCarga"' in html and "data-abrir-carga" in html
    assert 'id="ventanaCarga"' in c.get("/zonas/").text


def test_la_grilla_embebida_no_anida_la_ventana_ni_el_menu():
    _armar()
    html = _c().get("/compradores/carga?embed=1").text
    assert "cgTabla" in html and "cargaGrilla" in html
    assert 'id="ventanaCarga"' not in html and 'id="mainMenu"' not in html
    # La página suelta sigue andando, sin ventana encima de sí misma.
    html = _c().get("/compradores/carga").text
    assert "cgTabla" in html and 'id="ventanaCarga"' not in html and 'id="mainMenu"' in html


def test_sin_permiso_de_edicion_no_aparece_la_ventana():
    db, _ = _armar()
    db.add(models.User(username="mirador", hashed_password=auth.hash_password("x" * 10), is_admin=False))
    db.commit()
    c = TestClient(app)
    c.cookies.update({"access_token": "Bearer " + auth.create_access_token({"sub": "mirador"})})
    r = c.get("/compradores/", follow_redirects=False)
    assert 'id="ventanaCarga"' not in r.text
    assert c.get("/compradores/carga?embed=1", follow_redirects=False).status_code != 200


def test_la_pantalla_muestra_las_ultimas_filas():
    db, x = _armar()
    b = _boleta(db, x)
    _fila(b, x)
    html = _c().get("/compradores/carga").text
    assert "PÉREZ JUAN" in html.replace("\\u00c9", "É") and "cgTabla" in html
