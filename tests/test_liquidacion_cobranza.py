"""Liquidación de una planilla de cobranza (POST /cobranza/liquidacion/{id}/guardar).

Se prueba de punta a punta: se manda el mismo formulario que la pantalla y se
mira cómo quedan la boleta (historial, cuotas pagas, condición) y la liquidación
(cuotas, monto, comisión del cobrador, neto).

Reglas:
  * Cada cuota marcada se anota en historial_cuotas como {"nro": "YYYY-MM"} del
    período que se liquida. Guardar de nuevo el mismo período REEMPLAZA lo de ese
    período; los demás períodos no se tocan.
  * cuotas_pagadas = anticipadas + cuotas del historial (tope: pactadas).
  * No se puede liquidar un período futuro (se recorta al mes actual).
  * Solo se tocan boletas de ESA planilla.
  * Las cuotas que la boleta traía pagas de otro cobrador no se pisan ni suman.
"""
import json

import _entorno
from fastapi.testclient import TestClient

from app import auth, models
from app.main import app
from app.models import CondicionBoleta
from app.tiempo import hoy_ar

AGO, SEP = (2026, 8), (2026, 9)


def _armar(pactadas=9, valor_cuota=15000.0, comision_pct=10.0):
    db = _entorno.base_limpia()
    pata = models.Talonera(nombre="PATA 1", tipo="COMUN", valor_cuota=valor_cuota,
                           num_cuotas=12, multiplicador=1.0)
    cob = models.Cobrador(nombre="MABEL")
    db.add_all([pata, cob]); db.flush()
    pl = models.Planilla(cobrador_id=cob.id, numero=1, mes=8, anio=2026, comision_pct=comision_pct)
    otra = models.Planilla(cobrador_id=cob.id, numero=2, mes=8, anio=2026, comision_pct=comision_pct)
    db.add_all([pl, otra]); db.flush()
    b = models.Boleta(talonera_id=pata.id, numero_principal=1234, cobrador_id=cob.id,
                      planilla_id=pl.id, cuotas_pactadas=pactadas, cuotas_anticipadas=1,
                      cuotas_pagadas=1, condicion=CondicionBoleta.EN_COBRANZA)
    db.add(b)
    db.add(models.User(username="tester", hashed_password=auth.hash_password("x" * 10), is_admin=True))
    db.commit()
    return db, pl, otra, b


def _cliente(usuario="tester"):
    c = TestClient(app)
    c.cookies.update({"access_token": "Bearer " + auth.create_access_token({"sub": usuario})})
    return c


def _guardar(planilla_id, cuotas_por_boleta, periodo=AGO, bajas=None, usuario="tester"):
    """cuotas_por_boleta = {boleta_id: [nros de cuota marcados]}"""
    ids = list(cuotas_por_boleta)
    data = {
        "boleta_ids": [str(i) for i in ids],
        "cuotas_json": [json.dumps(cuotas_por_boleta[i]) for i in ids],
        "baja_mes": [str((bajas or {}).get(i, "")) for i in ids],
        "mes": str(periodo[1]), "anio": str(periodo[0]), "modo": "quedarse",
    }
    return _cliente(usuario).post(f"/cobranza/liquidacion/{planilla_id}/guardar",
                                  data=data, follow_redirects=False)


def _recargar(db, *objs):
    db.expire_all()
    return [db.get(type(o), o.id) for o in objs]


def test_liquidar_una_cuota():
    db, pl, _, b = _armar()
    r = _guardar(pl.id, {b.id: [2]})
    assert r.status_code == 302
    b, pl = _recargar(db, b, pl)
    assert json.loads(b.historial_cuotas) == {"2": "2026-08"}
    assert b.cuotas_pagadas == 2
    assert b.condicion == CondicionBoleta.EN_COBRANZA
    liq = pl.liquidacion
    assert (liq.total_cuotas, liq.monto_total, liq.comision, liq.neto) == (1, 15000, 1500, 13500)
    assert [(d.boleta_id, d.cuotas_cobradas) for d in liq.detalles] == [(b.id, 1)]
    db.close()


def test_volver_a_guardar_el_mismo_mes_reemplaza():
    db, pl, _, b = _armar()
    _guardar(pl.id, {b.id: [2]})
    _guardar(pl.id, {b.id: [2, 3]})
    (b,) = _recargar(db, b)
    assert json.loads(b.historial_cuotas) == {"2": "2026-08", "3": "2026-08"}
    assert b.cuotas_pagadas == 3
    # Desmarcar todo el mes lo deja como antes de liquidar.
    _guardar(pl.id, {b.id: []})
    (b,) = _recargar(db, b)
    assert json.loads(b.historial_cuotas) == {} and b.cuotas_pagadas == 1
    db.close()


def test_otro_mes_no_toca_lo_ya_liquidado():
    db, pl, _, b = _armar()
    _guardar(pl.id, {b.id: [2, 3]}, periodo=AGO)
    _guardar(pl.id, {b.id: [4]}, periodo=SEP)
    b, pl = _recargar(db, b, pl)
    assert json.loads(b.historial_cuotas) == {"2": "2026-08", "3": "2026-08", "4": "2026-09"}
    assert b.cuotas_pagadas == 4
    # La liquidación de la planilla refleja lo cobrado en el período guardado.
    assert pl.liquidacion.total_cuotas == 1
    db.close()


def test_ultima_cuota_pasa_a_vendido():
    db, pl, _, b = _armar(pactadas=3)
    _guardar(pl.id, {b.id: [2, 3]})
    (b,) = _recargar(db, b)
    assert b.cuotas_pagadas == 3
    assert b.condicion == CondicionBoleta.VENDIDO
    db.close()


def test_cuotas_pagadas_no_supera_las_pactadas():
    db, pl, _, b = _armar(pactadas=3)
    _guardar(pl.id, {b.id: [2, 3, 4, 5]})
    (b,) = _recargar(db, b)
    assert b.cuotas_pagadas == 3
    db.close()


def test_boleta_de_otra_planilla_se_ignora():
    db, pl, otra, b = _armar()
    _guardar(otra.id, {b.id: [2, 3]})
    b, otra = _recargar(db, b, otra)
    assert not b.historial_cuotas and b.cuotas_pagadas == 1
    assert otra.liquidacion.total_cuotas == 0
    db.close()


def test_periodo_futuro_se_recorta_al_mes_actual():
    db, pl, _, b = _armar()
    _guardar(pl.id, {b.id: [2]}, periodo=(2099, 1))
    (b,) = _recargar(db, b)
    hoy = hoy_ar()
    assert json.loads(b.historial_cuotas) == {"2": f"{hoy.year:04d}-{hoy.month:02d}"}
    db.close()


def test_cuotas_recibidas_de_otro_cobrador_no_se_pisan_ni_suman():
    # El número vino pasado de otra planilla con 3 cuotas pagas: la 2 y la 3 las
    # cobró el cobrador anterior en agosto. Acá en agosto se cobra solo la 4.
    db, pl, otra, b = _armar()
    b.paso_origen_planilla_id = otra.id
    b.paso_cuota = 3
    b.historial_cuotas = json.dumps({"2": "2026-08", "3": "2026-08"})
    b.cuotas_pagadas = 3
    db.commit()
    _guardar(pl.id, {b.id: [2, 3, 4]})
    b, pl = _recargar(db, b, pl)
    assert json.loads(b.historial_cuotas) == {"2": "2026-08", "3": "2026-08", "4": "2026-08"}
    assert b.cuotas_pagadas == 4
    assert pl.liquidacion.total_cuotas == 1 and pl.liquidacion.monto_total == 15000
    db.close()


def test_baja_es_solo_registro():
    db, pl, _, b = _armar()
    _guardar(pl.id, {b.id: [2]}, bajas={b.id: 9})
    (b,) = _recargar(db, b)
    assert b.mes_baja == 9
    assert b.condicion == CondicionBoleta.EN_COBRANZA     # no pasa a BAJA
    assert b.planilla_id == pl.id                         # sigue en la planilla
    db.close()


def test_valor_cuota_en_cero_toma_el_de_otra_tanda_de_la_pata():
    # Caso real (PATA 0, tanda 9024→9073): la tanda quedó con valor $0.
    db, pl, _, b = _armar(valor_cuota=0.0)
    db.add(models.Talonera(nombre="PATA 1", tipo="COMUN", valor_cuota=15000.0, multiplicador=1.0))
    db.commit()
    _guardar(pl.id, {b.id: [2, 3]})
    (pl,) = _recargar(db, pl)
    assert pl.liquidacion.monto_total == 30000
    db.close()


def test_sin_permiso_de_editar_cobranza_no_puede():
    db, pl, _, b = _armar()
    db.add(models.User(username="solo_ver", hashed_password=auth.hash_password("x" * 10),
                       is_admin=False,
                       permissions=json.dumps({"cobranza": {"ver": True, "editar": False}})))
    db.commit()
    r = _guardar(pl.id, {b.id: [2]}, usuario="solo_ver")
    assert r.status_code == 403
    (b,) = _recargar(db, b)
    assert not b.historial_cuotas
    db.close()


# ── Servicio directo (services/liquidacion_cobranza.py), sin base ───────────

from types import SimpleNamespace as NS  # noqa: E402

from app.services import liquidacion_cobranza as liq_cob  # noqa: E402


def _b(historial=None, pactadas=9, anticipadas=1, **kw):
    base = dict(historial_cuotas=json.dumps(historial) if historial is not None else None,
                cuotas_pactadas=pactadas, cuotas_anticipadas=anticipadas, cuotas_pagadas=anticipadas,
                condicion=CondicionBoleta.EN_COBRANZA, cobrador_id=1, planilla_id=10,
                paso_origen_planilla_id=None, paso_cuota=None, mes_baja=None,
                modalidad_liquidacion=None, numero_especial=None, numero_especial_2=None)
    base.update(kw)
    return NS(**base)


def test_aplicar_cobro_historial_viejo_sin_anio():
    # Historial legacy {"2": 8} (agosto sin año): liquidar agosto 2026 lo reemplaza.
    b = _b({"2": 8})
    assert liq_cob.aplicar_cobro(b, [2, 3], 2026, 8, 10) == 2
    assert json.loads(b.historial_cuotas) == {"2": "2026-08", "3": "2026-08"}


def test_aplicar_cobro_historial_roto_arranca_de_cero():
    b = _b()
    b.historial_cuotas = "{roto"
    assert liq_cob.aplicar_cobro(b, [2], 2026, 8, 10) == 1
    assert b.cuotas_pagadas == 2


def test_aplicar_cobro_no_toca_bajas_reales_ni_caja():
    for cond in (CondicionBoleta.BAJA, CondicionBoleta.CAJA, CondicionBoleta.SIN_VENDER):
        b = _b(condicion=cond, pactadas=3)
        liq_cob.aplicar_cobro(b, [2, 3], 2026, 8, 10)
        assert b.condicion == cond


def test_aplicar_cobro_contado_queda_vendido():
    b = _b(modalidad_liquidacion="contado")
    liq_cob.aplicar_cobro(b, [], 2026, 8, 10)
    assert b.condicion == CondicionBoleta.VENDIDO


def test_totales_redondea_a_centavos():
    assert liq_cob.totales(45000, 10) == (45000, 4500, 40500)
    assert liq_cob.totales(10000.0 / 3, 7.5) == (3333.33, 250.0, 3083.33)
    assert liq_cob.totales(30000, None) == (30000, 0.0, 30000)


def test_resolver_periodo():
    hoy = hoy_ar()
    assert liq_cob.resolver_periodo(0, 0) == (hoy.year, hoy.month, False, False)
    assert liq_cob.resolver_periodo(8, 2026)[:2] == (2026, 8)
    assert liq_cob.resolver_periodo(13, 2026) == (hoy.year, hoy.month, False, True)
    assert liq_cob.resolver_periodo(1, 2099) == (hoy.year, hoy.month, False, True)
    assert liq_cob.resolver_periodo("x", "y") == (hoy.year, hoy.month, False, True)


def test_cuota_cuenta_aca():
    # Salió de acá con 4 pagas (corte): la 5 ya es de la planilla destino.
    assert liq_cob.cuota_cuenta_aca("4", 4, 0) and not liq_cob.cuota_cuenta_aca("5", 4, 0)
    # Entró con 3 pagas del cobrador anterior: la 3 no es de acá, la 4 sí.
    assert not liq_cob.cuota_cuenta_aca("3", None, 3) and liq_cob.cuota_cuenta_aca("4", None, 3)
    assert not liq_cob.cuota_cuenta_aca("x", None, 0)


def test_recibidas_y_mapa():
    vino = _b(paso_origen_planilla_id=7, paso_cuota=3, id=1)
    propia = _b(id=2)
    salio_de_aca = _b(paso_origen_planilla_id=10, paso_cuota=4, id=3)
    assert liq_cob.recibidas_de(vino, 10) == 3
    assert liq_cob.recibidas_de(propia, 10) == 0
    assert liq_cob.recibidas_de(salio_de_aca, 10) == 0
    assert liq_cob.mapa_recibidas([vino, propia, salio_de_aca], 10) == {1: 3}
