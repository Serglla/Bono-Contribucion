"""Tests de la plata de la liquidación del vendedor (app/routers/vendedores.py).

Reglas:
  * Boleta en CUOTAS: el vendedor se queda con la cuota 1 (comision_cuotas =
    valor_cuota). No rinde nada por ella.
  * Boleta al CONTADO: vale cuotas_vigentes(fecha de la liquidación) × valor_cuota;
    el vendedor se queda con el % (30 por defecto) y rinde el resto.
  * Cada boleta pesa según el multiplicador de su PATA (PATA 0 ×0.67, PATA 2 ×2).
  * Sumar y sacar una boleta tienen que ser espejo exacto (si no, la liquidación
    se descuadra al editarla desde el historial).
"""
from datetime import date
from types import SimpleNamespace as NS

import _entorno
from app.routers.vendedores import (
    _monto_contado_boleta,
    _restar_boleta_de_liq,
    _sumar_boleta_a_liq,
)

OCT = date(2026, 10, 5)   # contado de octubre = 9 cuotas

PATA0 = NS(valor_cuota=10000.0, num_cuotas=12, multiplicador=2 / 3)
PATA1 = NS(valor_cuota=15000.0, num_cuotas=12, multiplicador=1.0)
PATA2 = NS(valor_cuota=30000.0, num_cuotas=12, multiplicador=2.0)

CAMPOS = ("contados_vendidos", "contados_equiv", "monto_contados", "comision_contados",
          "cuotas_vendidas", "cuotas_equiv", "cuota_1_total", "monto_cuotas",
          "comision_cuotas", "total_a_rendir", "total_comision")


def liq_vacia(pct=30.0, fecha=OCT):
    base = {c: 0 for c in CAMPOS}
    base.update(fecha=fecha, comision_contados_pct=pct,
                cuotas_extras_monto=0, comision_cuotas_extras=0,
                cuotas_extras_p0_monto=0, comision_cuotas_extras_p0=0)
    return NS(**base)


def boleta(pata, modalidad=None):
    return NS(talonera=pata, modalidad_liquidacion=modalidad, numero_especial=None,
              numero_especial_2=None, cuotas_pactadas=9, cuotas_anticipadas=1)


def test_monto_contado_usa_las_cuotas_de_la_fecha_de_liquidacion():
    liq = liq_vacia()
    assert _monto_contado_boleta(boleta(PATA1), liq) == 9 * 15000
    # Misma boleta, liquidación de agosto → 11 cuotas.
    assert _monto_contado_boleta(boleta(PATA1), liq, fecha=date(2026, 8, 1)) == 11 * 15000


def test_contado_pata1():
    liq = liq_vacia()
    _sumar_boleta_a_liq(liq, boleta(PATA1), "contado")
    assert liq.contados_vendidos == 1
    assert liq.contados_equiv == 1.0
    assert liq.monto_contados == 135000
    assert liq.comision_contados == 40500          # 30 %
    assert liq.total_a_rendir == 94500             # rinde el 70 %
    assert liq.total_comision == 40500


def test_contado2_vale_lo_mismo_que_contado():
    a, b = liq_vacia(), liq_vacia()
    _sumar_boleta_a_liq(a, boleta(PATA1), "contado")
    _sumar_boleta_a_liq(b, boleta(PATA1), "contado2")
    assert (a.monto_contados, a.comision_contados) == (b.monto_contados, b.comision_contados)


def test_cuotas_el_vendedor_se_queda_la_cuota_1():
    liq = liq_vacia()
    _sumar_boleta_a_liq(liq, boleta(PATA2), "cuotas")
    assert liq.cuotas_vendidas == 1
    assert liq.cuotas_equiv == 2.0                 # PATA 2 pesa doble
    assert liq.comision_cuotas == 30000
    assert liq.total_a_rendir == 0                 # por cuotas no rinde nada
    assert liq.total_comision == 30000


def test_multiplicador_pata0():
    liq = liq_vacia()
    _sumar_boleta_a_liq(liq, boleta(PATA0), "contado")
    assert abs(liq.contados_equiv - 2 / 3) < 1e-9
    assert liq.monto_contados == 9 * 10000


def test_porcentaje_vacio_usa_30():
    liq = liq_vacia(pct=0)
    _sumar_boleta_a_liq(liq, boleta(PATA1), "contado")
    assert liq.comision_contados == 40500
    assert liq.comision_contados_pct == 30.0


def test_varias_boletas_suman():
    liq = liq_vacia()
    for pata, mod in ((PATA1, "contado"), (PATA0, "contado"), (PATA1, "cuotas"), (PATA2, "cuotas")):
        _sumar_boleta_a_liq(liq, boleta(pata), mod)
    assert liq.contados_vendidos == 2 and liq.cuotas_vendidas == 2
    assert liq.monto_contados == 135000 + 90000
    assert liq.comision_contados == 40500 + 27000
    assert liq.total_a_rendir == (135000 + 90000) - (40500 + 27000)
    assert liq.comision_cuotas == 15000 + 30000


def test_sumar_y_sacar_son_espejo():
    liq = liq_vacia()
    boletas = [(boleta(PATA1), "contado"), (boleta(PATA0), "contado2"),
               (boleta(PATA2), "cuotas"), (boleta(PATA1), "cuotas")]
    for b, mod in boletas:
        _sumar_boleta_a_liq(liq, b, mod)
    for b, mod in boletas:
        _restar_boleta_de_liq(liq, b, mod)
    for campo in CAMPOS:
        assert abs(float(getattr(liq, campo))) < 1e-6, (campo, getattr(liq, campo))


def test_sacar_sin_modalidad_la_deduce_de_la_boleta():
    liq = liq_vacia()
    b = boleta(PATA1, modalidad="contado")
    _sumar_boleta_a_liq(liq, b, "contado")
    _restar_boleta_de_liq(liq, b)              # sin pasar modalidad
    assert liq.monto_contados == 0 and liq.contados_vendidos == 0


def test_modalidad_invalida_cuenta_como_cuotas():
    liq = liq_vacia()
    _sumar_boleta_a_liq(liq, boleta(PATA1), "cualquier-cosa")
    assert liq.cuotas_vendidas == 1 and liq.contados_vendidos == 0


# ── calcular_liquidacion (liquidación nueva) y cambiar_fecha ────────────────

from app.services import liquidacion_vendedor as liq_svc  # noqa: E402


def test_calcular_liquidacion_completa():
    t = liq_svc.calcular_liquidacion(
        cuotas=[boleta(PATA1), boleta(PATA2)],
        contados=[boleta(PATA1), boleta(PATA0)],
        fecha_liq=OCT,
        comision_cuotas_pct=5.0, comision_contados_pct=30.0,
        cuotas_extras_cantidad=4, cuotas_extras_valor=15000,
        cuotas_extras_p0_cantidad=2, cuotas_extras_p0_valor=10000,
    )
    assert (t["cuotas_vendidas"], t["contados_vendidos"]) == (2, 2)
    assert t["cuotas_equiv"] == 3.0                                   # PATA1 + PATA2
    assert abs(t["contados_equiv"] - (1 + 2 / 3)) < 1e-9
    assert t["cuota_1_total"] == t["comision_cuotas"] == 45000        # se queda la cuota 1
    assert t["monto_contados"] == 9 * 15000 + 9 * 10000               # octubre: 9 cuotas
    assert t["comision_contados"] == 67500
    assert (t["cuotas_extras_monto"], t["comision_cuotas_extras"]) == (60000, 3000)
    assert (t["cuotas_extras_p0_monto"], t["comision_cuotas_extras_p0"]) == (20000, 1000)
    assert t["total_a_rendir"] == (225000 - 67500) + (60000 - 3000) + (20000 - 1000)
    assert t["total_comision"] == 45000 + 67500 + 3000 + 1000


def test_calcular_da_lo_mismo_que_sumar_de_a_una():
    # Liquidar de una vez y armar la liquidación agregando números desde el
    # historial tienen que dar la misma plata (si no, editar la descuadra).
    cuotas = [boleta(PATA1), boleta(PATA0), boleta(PATA2)]
    contados = [boleta(PATA1), boleta(PATA2), boleta(PATA0)]
    t = liq_svc.calcular_liquidacion(cuotas, contados, OCT, comision_contados_pct=30.0)
    liq = liq_vacia()
    for b in cuotas:
        liq_svc.sumar_boleta(liq, b, "cuotas")
    for b in contados:
        liq_svc.sumar_boleta(liq, b, "contado")
    for campo in ("cuotas_vendidas", "cuotas_equiv", "cuota_1_total", "comision_cuotas",
                  "contados_vendidos", "contados_equiv", "monto_contados", "comision_contados",
                  "total_a_rendir", "total_comision"):
        assert abs(float(t[campo]) - float(getattr(liq, campo))) < 0.01, campo


def test_cambiar_fecha_re_precia_los_contados():
    liq = liq_vacia(fecha=date(2026, 8, 10))          # agosto: 11 cuotas
    contado, cuota = boleta(PATA1, "contado"), boleta(PATA1, "cuotas")
    liq_svc.sumar_boleta(liq, contado, "contado")
    liq_svc.sumar_boleta(liq, cuota, "cuotas")
    assert liq.monto_contados == 11 * 15000
    delta = liq_svc.cambiar_fecha(liq, [contado, cuota], OCT)   # octubre: 9
    assert delta == -2 * 15000
    assert liq.monto_contados == 9 * 15000
    assert liq.comision_contados == 40500
    assert liq.total_a_rendir == 94500
    assert liq.comision_cuotas == 15000                # las de cuotas no cambian


def test_cambiar_fecha_mismo_mes_no_cambia_nada():
    liq = liq_vacia(fecha=date(2026, 10, 1))
    b = boleta(PATA1, "contado")
    liq_svc.sumar_boleta(liq, b, "contado")
    antes = liq.monto_contados
    assert liq_svc.cambiar_fecha(liq, [b], date(2026, 10, 30)) == 0
    assert liq.monto_contados == antes


# ── liquidar de punta a punta (POST /vendedores/{id}/liquidar) ──────────────

def test_liquidar_endpoint():
    import json
    from fastapi.testclient import TestClient
    from app import auth, models
    from app.main import app
    from app.models import CondicionBoleta

    db = _entorno.base_limpia()
    pata = models.Talonera(nombre="PATA 1", tipo="COMUN", valor_cuota=15000.0,
                           num_cuotas=12, multiplicador=1.0)
    ven = models.Vendedor(nombre="ARIEL")
    db.add_all([pata, ven]); db.flush()
    bs = [models.Boleta(talonera_id=pata.id, numero_principal=n, vendedor_id=ven.id,
                        condicion=CondicionBoleta.CAJA) for n in (1001, 1002, 1003)]
    ajena = models.Boleta(talonera_id=pata.id, numero_principal=1004,
                          condicion=CondicionBoleta.CAJA)          # caja de nadie
    db.add_all(bs + [ajena])
    db.add(models.User(username="tester", hashed_password=auth.hash_password("x" * 10), is_admin=True))
    db.commit()
    c = TestClient(app)
    c.cookies.update({"access_token": "Bearer " + auth.create_access_token({"sub": "tester"})})
    form = {
        "boleta_ids": [str(b.id) for b in bs] + [str(ajena.id)],
        f"modalidad_{bs[0].id}": "contado", f"modalidad_{bs[1].id}": "contado2",
        f"modalidad_{bs[2].id}": "cuotas",
        "comision_cuotas_pct": "5", "comision_contados_pct": "30",
        "fecha": "2026-10-05",
    }
    r = c.post(f"/vendedores/{ven.id}/liquidar", data=form, follow_redirects=False)
    assert r.status_code in (200, 302, 303), r.text[:300]
    db.expire_all()
    liq = db.query(models.LiquidacionVendedor).one()
    assert (liq.contados_vendidos, liq.cuotas_vendidas) == (2, 1)
    assert liq.monto_contados == 2 * 9 * 15000
    assert liq.comision_contados == 81000
    assert liq.total_a_rendir == 270000 - 81000
    assert liq.comision_cuotas == 15000
    for b in bs:
        b = db.get(models.Boleta, b.id)
        assert b.liquidacion_vendedor_id == liq.id
        assert b.condicion == CondicionBoleta.VENDIDO
        assert b.cuotas_pactadas == 9                # estampadas a la fecha de liquidación
    assert db.get(models.Boleta, bs[0].id).modalidad_liquidacion == "contado"
    assert db.get(models.Boleta, ajena.id).liquidacion_vendedor_id is None   # no era suya
    db.close()
