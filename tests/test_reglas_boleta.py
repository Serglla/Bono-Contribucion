"""Tests de las reglas de boleta (app/services/reglas_boleta.py).

Correr con pytest:      py -3.12 -m pytest tests
o sin pytest instalado: py -3.12 tests/test_reglas_boleta.py

Cada caso es una boleta armada a mano; no hace falta base de datos salvo en el
test que compara la versión SQL con la Python (usa SQLite en memoria).
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import CondicionBoleta  # noqa: E402
from app.services import reglas_boleta as R  # noqa: E402


def boleta(**kw):
    base = dict(modalidad_liquidacion=None, numero_especial=None, numero_especial_2=None,
                cuotas_pactadas=11, cuotas_anticipadas=1, cuotas_pagadas=1, cobrador_id=None)
    base.update(kw)
    return SimpleNamespace(**base)


# ── es_contado ──────────────────────────────────────────────────────────────

def test_cuotas_normal_no_es_contado():
    assert not R.es_contado(boleta())


def test_liquidada_contado_es_contado_aunque_no_tenga_especial():
    assert R.es_contado(boleta(modalidad_liquidacion="contado"))
    assert R.es_contado(boleta(modalidad_liquidacion="contado2"))
    assert R.es_contado(boleta(modalidad_liquidacion=" Contado "))   # mayúsculas/espacios


def test_especial_slot1_o_slot2_es_contado():
    assert R.es_contado(boleta(numero_especial=12))
    # Bug viejo del dashboard y el mapa: ignoraban el slot 2.
    assert R.es_contado(boleta(numero_especial_2=34))


def test_liquidada_en_cuotas_gana_sobre_especial_espurio():
    # Número especial colgado de data vieja en una boleta liquidada en cuotas.
    assert not R.es_contado(boleta(modalidad_liquidacion="cuotas", numero_especial=12))


def test_anticipo_total_es_contado():
    assert R.es_contado(boleta(cuotas_pactadas=9, cuotas_anticipadas=9))
    # Aunque la liquidación diga cuotas: el socio pagó todo de entrada.
    assert R.es_contado(boleta(modalidad_liquidacion="cuotas", cuotas_pactadas=9, cuotas_anticipadas=9))


def test_terminar_de_pagar_en_cobranza_no_es_contado():
    # cuotas_pagadas completas pero anticipadas = 1 → pagó mes a mes, no es contado.
    assert not R.es_contado(boleta(cuotas_pactadas=9, cuotas_anticipadas=1, cuotas_pagadas=9))


def test_sin_cuotas_pactadas_no_es_contado_por_anticipo():
    assert not R.es_contado(boleta(cuotas_pactadas=0, cuotas_anticipadas=0))
    assert not R.es_contado(boleta(cuotas_pactadas=None, cuotas_anticipadas=None))


def test_none_no_es_contado():
    assert not R.es_contado(None)


# ── modalidad_de_boleta (liquidación del vendedor) ──────────────────────────

def test_modalidad_explicita_manda():
    assert R.modalidad_de_boleta(boleta(modalidad_liquidacion="cuotas",
                                        cuotas_pactadas=9, cuotas_anticipadas=9)) == "cuotas"
    assert R.modalidad_de_boleta(boleta(modalidad_liquidacion="contado2")) == "contado2"


def test_modalidad_inferida_en_data_vieja():
    assert R.modalidad_de_boleta(boleta(numero_especial=5)) == "contado"
    assert R.modalidad_de_boleta(boleta(cuotas_pactadas=9, cuotas_anticipadas=9)) == "contado"
    assert R.modalidad_de_boleta(boleta()) == "cuotas"
    assert R.modalidad_de_boleta(None) == "cuotas"


# ── cobrador y condición ────────────────────────────────────────────────────

def test_lleva_cobrador():
    assert R.lleva_cobrador(boleta())
    assert not R.lleva_cobrador(boleta(cuotas_pactadas=9, cuotas_anticipadas=9, cuotas_pagadas=9))
    assert not R.lleva_cobrador(boleta(modalidad_liquidacion="contado"))
    assert not R.lleva_cobrador(boleta(cuotas_pagadas=11))   # terminó de pagar


def test_condicion_vendida():
    assert R.condicion_vendida(boleta(cobrador_id=3)) == CondicionBoleta.EN_COBRANZA
    assert R.condicion_vendida(boleta()) == CondicionBoleta.VENDIDO                     # sin cobrador
    assert R.condicion_vendida(boleta(cobrador_id=3, numero_especial=7)) == CondicionBoleta.VENDIDO
    assert R.condicion_vendida(boleta(cobrador_id=3, cuotas_pagadas=11)) == CondicionBoleta.VENDIDO


# ── SQL == Python ───────────────────────────────────────────────────────────

def test_version_sql_coincide_con_python():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base, Boleta, Talonera

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Talonera(id=1, nombre="PATA 1"))
    casos = [
        {},
        dict(modalidad_liquidacion="contado"),
        dict(modalidad_liquidacion="contado2"),
        dict(modalidad_liquidacion=" Contado "),
        dict(numero_especial=12),
        dict(numero_especial_2=34),
        dict(modalidad_liquidacion="cuotas", numero_especial=12),
        dict(cuotas_pactadas=9, cuotas_anticipadas=9),
        dict(modalidad_liquidacion="cuotas", cuotas_pactadas=9, cuotas_anticipadas=9),
        dict(cuotas_pactadas=9, cuotas_anticipadas=1, cuotas_pagadas=9),
        dict(cuotas_pactadas=0, cuotas_anticipadas=0),
        dict(cuotas_pactadas=None, cuotas_anticipadas=None),
    ]
    for i, kw in enumerate(casos, start=1):
        datos = dict(cuotas_pactadas=11, cuotas_anticipadas=1, cuotas_pagadas=1)
        datos.update(kw)
        db.add(Boleta(id=i, talonera_id=1, numero_principal=i, **datos))
    db.commit()

    sql = {i for (i,) in db.query(Boleta.id).filter(R.es_contado_sql())}
    py = {b.id for b in db.query(Boleta).all() if R.es_contado(b)}
    assert sql == py, (sql, py)
    no_sql = {i for (i,) in db.query(Boleta.id).filter(R.no_es_contado_sql())}
    assert no_sql == {b.id for b in db.query(Boleta).all()} - py


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok   ", nombre)
            except AssertionError as e:
                fallos += 1
                print("FALLA", nombre, e)
    print(f"\n{'TODO OK' if not fallos else f'{fallos} FALLARON'}")
    sys.exit(1 if fallos else 0)
