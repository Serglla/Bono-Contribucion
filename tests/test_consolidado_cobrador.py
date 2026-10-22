"""Consolidado del cobrador: la hoja de liquidación de UN mes y el resumen mes a mes.

Funciones (app/routers/cobranza.py → services): `_consolidado_cobrador` (hoja
del mes: un renglón por planilla) y `_resumen_meses_cobrador` (histórico: un
renglón por mes). Alimentan la hoja, su PDF, el consolidado de todos, el
resumen para la institución, las tasas del dashboard y contabilidad.

ESCENARIO (cobradora MABEL, comisión 10 %). Los números esperados están
calculados a mano con las reglas de cada función.

  P1 entregada en julio 2026:
    A  PATA 1 ($15.000)  pagó cuota 2 en ago y 3 en sep
    B  PATA 2 ($30.000)  pagó cuota 2 en ago; sep nada        → debe en sep (pesa 2)
    C  PATA 0 ($10.000)  ago nada; pagó cuota 2 en sep         → debe en ago (X0)
    D  PATA 1            baja en agosto, nunca pagó            → baja, no debe
    E  PATA 1, 2 cuotas  pagó la 2 en ago: terminó             → en sep no debe
    G  PATA 1            pagó la 2 en ago ACÁ y se pasó a P2 (corte 2);
                         la 3 la pagó en sep en P2
  P2 entregada en septiembre 2026:
    F  PATA 1            nunca pagó
    G  (recibida de P1 con 2 pagas)

  Entregas: ago $20.000 efectivo + $5.000 premio; sep saldo anterior $3.000.
"""
import json
from datetime import date

import _entorno
from fastapi.testclient import TestClient

from app import auth, models
from app.main import app
from app.models import CondicionBoleta
from app.routers.cobranza import _consolidado_cobrador, _resumen_meses_cobrador


def _armar():
    db = _entorno.base_limpia()
    p0 = models.Talonera(nombre="PATA 0", tipo="COMUN", valor_cuota=10000.0, multiplicador=2 / 3)
    p1 = models.Talonera(nombre="PATA 1", tipo="COMUN", valor_cuota=15000.0, multiplicador=1.0)
    p2 = models.Talonera(nombre="PATA 2", tipo="COMUN", valor_cuota=30000.0, multiplicador=2.0)
    cob = models.Cobrador(nombre="MABEL")
    db.add_all([p0, p1, p2, cob]); db.flush()
    P1 = models.Planilla(cobrador_id=cob.id, numero=1, mes=7, anio=2026, comision_pct=10.0)
    P2 = models.Planilla(cobrador_id=cob.id, numero=2, mes=9, anio=2026, comision_pct=10.0)
    db.add_all([P1, P2]); db.flush()

    def bol(n, tal, planilla, hist, pactadas=9, **kw):
        b = models.Boleta(talonera_id=tal.id, numero_principal=n, planilla_id=planilla.id,
                          cobrador_id=cob.id, cuotas_pactadas=pactadas, cuotas_anticipadas=1,
                          cuotas_pagadas=1 + len(hist), historial_cuotas=json.dumps(hist),
                          condicion=CondicionBoleta.EN_COBRANZA, **kw)
        db.add(b)
        return b

    bol(1, p1, P1, {"2": "2026-08", "3": "2026-09"})                       # A
    bol(2, p2, P1, {"2": "2026-08"})                                       # B
    bol(3, p0, P1, {"2": "2026-09"})                                       # C
    bol(4, p1, P1, {}, mes_baja=8)                                         # D
    bol(5, p1, P1, {"2": "2026-08"}, pactadas=2)                           # E
    bol(6, p1, P2, {"2": "2026-08", "3": "2026-09"},                       # G
        paso_origen_planilla_id=P1.id, paso_cuota=2, paso_a="P2")
    bol(7, p1, P2, {})                                                     # F
    for mes, monto, tipo in ((8, 20000, "EFECTIVO"), (8, 5000, "PREMIO"), (9, -3000, "SALDO_ANT")):
        db.add(models.EntregaCobrador(cobrador_id=cob.id, fecha=date(2026, mes, 20),
                                      mes=mes, anio=2026, monto=monto, tipo=tipo))
    db.add(models.User(username="tester", hashed_password=auth.hash_password("x" * 10), is_admin=True))
    db.commit()
    return db, cob


def _cerca(a, b, tol=0.01):
    return abs(float(a) - float(b)) <= tol


# ── Hoja de UN mes (_consolidado_cobrador) ──────────────────────────────────

def test_hoja_agosto():
    db, cob = _armar()
    d = _consolidado_cobrador(db, cob, 8, 2026)
    # P2 se entregó en septiembre y en agosto no cobró nada: no tiene renglón.
    assert [r["numero"] for r in d["detalle"]] == [1]
    p1 = d["detalle"][0]
    # Cobrado en ago: A 15.000 + B 30.000 + E 15.000 + G 15.000 (la 2, antes del pase).
    assert p1["monto"] == 75000 and p1["comision"] == 7500 and p1["neto"] == 67500
    assert _cerca(p1["cuotas"], 5)                       # A1 + B2 + E1 + G1
    assert (p1["cuotas_pata"], p1["cuotas_x0"]) == (5, 0)
    # Sin cobrar en ago: solo C (X0). D está de baja en agosto.
    assert (p1["sin_pata"], p1["sin_x0"]) == (0, 1)
    assert _cerca(p1["sin_cobrar"], 2 / 3)
    assert p1["bajas"] == 1
    assert p1["efect"] == 83                             # 5 / (5 + 1)
    # Entregas: 20.000 efectivo + 5.000 premio.
    assert (d["total_adelantos"], d["adel_efectivo"], d["adel_premio"]) == (25000, 20000, 5000)
    assert d["saldo_anterior"] == 0
    assert d["saldo"] == 67500 - 25000
    assert d["pct_uniforme"] == 10.0
    db.close()


def test_hoja_septiembre_con_numero_pasado():
    db, cob = _armar()
    d = _consolidado_cobrador(db, cob, 9, 2026)
    p1, p2 = d["detalle"]
    # P1: A (15.000) + C (10.000). La 3 de G ya es de P2. B debe (pesa 2);
    # E terminó de pagar; D sigue de baja.
    assert p1["monto"] == 25000 and p1["comision"] == 2500
    assert (p1["cuotas_pata"], p1["cuotas_x0"]) == (1, 1)
    assert (p1["sin_pata"], p1["sin_x0"]) == (2, 0)
    assert p1["bajas"] == 0 and p1["efect"] == 50
    # P2 se entregó este mes, pero ya cobró (la 3 de G): F cuenta como sin cobrar.
    assert p2["monto"] == 15000 and p2["comision"] == 1500
    assert (p2["cuotas_pata"], p2["sin_pata"]) == (1, 1)
    assert d["monto"] == 40000 and d["comision"] == 4000 and d["neto"] == 36000
    assert d["cuotas_exact"] == 2.67 and d["cuotas"] == 3
    assert (d["cuotas_pata"], d["cuotas_x0"], d["sin_pata"], d["sin_x0"]) == (2, 1, 3, 0)
    assert d["efect"] == 50
    # Saldo anterior: se guarda en negativo; suma al saldo, no es plata entregada.
    assert d["saldo_anterior"] == 3000 and d["entregado_real"] == 0
    assert d["saldo"] == 36000 + 3000
    assert [a.tipo for a in d["saldos_anteriores"]] == ["SALDO_ANT"] and d["entregas_reales"] == []
    db.close()


def test_mover_un_numero_no_cambia_el_mes_ya_liquidado():
    # La cuota de agosto de G se cobró en P1 antes del pase: tiene que seguir
    # contando en la hoja de agosto de P1 (si no, el saldo de agosto cambiaba).
    db, cob = _armar()
    ago = _consolidado_cobrador(db, cob, 8, 2026)
    assert ago["detalle"][0]["monto"] == 75000
    db.close()


def test_mes_sin_movimiento():
    db, cob = _armar()
    d = _consolidado_cobrador(db, cob, 3, 2026)
    assert d["detalle"] == [] and d["monto"] == 0 and d["saldo"] == 0 and d["efect"] == 0
    assert d["pct_uniforme"] is None
    db.close()


def test_cache_no_cambia_el_resultado():
    db, cob = _armar()
    cache = {}
    a = _consolidado_cobrador(db, cob, 9, 2026, cache=cache)
    b = _consolidado_cobrador(db, cob, 9, 2026, cache=cache)
    c = _consolidado_cobrador(db, cob, 9, 2026)
    sacar = lambda d: {k: v for k, v in d.items() if k not in ("cobrador", "adelantos", "entregas_reales", "saldos_anteriores")}
    assert sacar(a) == sacar(b) == sacar(c)
    db.close()


# ── Resumen mes a mes (_resumen_meses_cobrador) ─────────────────────────────

def test_resumen_mes_a_mes():
    db, cob = _armar()
    r = _resumen_meses_cobrador(db, cob)
    ago, sep = r["filas"]
    assert (ago["anio"], ago["mes"], sep["mes"]) == (2026, 8, 9)
    assert ago["label"] == "Agosto 2026"
    # Agosto: A1 + B2 + E1 + G1; C no pagó (X0); D de baja.
    assert _cerca(ago["cuotas"], 5) and ago["monto"] == 75000 and ago["comision"] == 7500
    assert _cerca(ago["sin_cobrar"], 0.67) and ago["pct"] == 88 and ago["bajas"] == 1
    assert ago["entregas"] == ago["adelantos"] == 25000 and ago["saldo"] == 75000 - 7500 - 25000
    # Septiembre: A1 + C 0.67 + G1; B debe 2. (P2 se entregó en septiembre: acá
    # todavía no se le exige — distinto de la hoja del mes, que sí cuenta a F
    # porque P2 ya registró cobranza. Diferencia existente, documentada.)
    assert _cerca(sep["cuotas"], 2 + 2 / 3) and sep["monto"] == 40000
    assert sep["sin_cobrar"] == 2 and sep["pct"] == 57 and sep["bajas"] == 0
    assert sep["saldo"] == 40000 - 4000 + 3000
    t = r["totales"]
    assert t["monto"] == 115000 and t["comision"] == 11500 and t["bajas"] == 1
    assert t["saldo"] == 42500 + 39000
    assert [p["key"] for p in r["periodos"]] == ["2026-8", "2026-9"]
    db.close()


def test_resumen_filtrado_a_un_mes():
    db, cob = _armar()
    r = _resumen_meses_cobrador(db, cob, solo_anio=2026, solo_mes=9)
    assert [f["mes"] for f in r["filas"]] == [9]
    assert len(r["periodos"]) == 2                       # el selector sigue completo
    assert r["totales"]["monto"] == 40000
    assert r["filtro"] == {"anio": 2026, "mes": 9}
    db.close()


def test_historial_viejo_sin_anio():
    # Valores legacy (mes suelto, sin año): la hoja los toma como del año pedido.
    db, cob = _armar()
    b = db.query(models.Boleta).filter_by(numero_principal=1).one()
    b.historial_cuotas = json.dumps({"2": 8})
    db.commit()
    d = _consolidado_cobrador(db, cob, 8, 2026)
    assert d["detalle"][0]["monto"] == 75000
    db.close()


# ── Pantallas que usan estos cálculos ───────────────────────────────────────

def test_pantallas_del_consolidado():
    db, cob = _armar()
    c = TestClient(app)
    c.cookies.update({"access_token": "Bearer " + auth.create_access_token({"sub": "tester"})})
    for u in (f"/cobranza/consolidado/{cob.id}/hoja?mes=8&anio=2026",
              f"/cobranza/consolidado/{cob.id}/hoja",
              f"/cobranza/consolidado/{cob.id}/hoja.pdf?mes=9&anio=2026",
              "/cobranza/consolidado?mes=9&anio=2026",
              "/cobranza/resumen-mes?mes=8&anio=2026",
              "/cobranza/resumen-mes.pdf?mes=8&anio=2026",
              "/cobranza/", "/reportes/", "/contabilidad/"):
        r = c.get(u)
        assert r.status_code == 200, (u, r.status_code, r.text[:200])
    hoja = c.get(f"/cobranza/consolidado/{cob.id}/hoja?mes=8&anio=2026").text
    assert "67.500" in hoja or "67500" in hoja           # neto de agosto en la hoja
    db.close()
