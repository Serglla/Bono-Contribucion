"""Ganadores de un sorteo: la pantalla de ganadores y el extracto tienen que coincidir.

Hay DOS cálculos en app/routers/sorteos.py:
  * `_calcular_grupos_ganadores` → pantalla /sorteos/{id}/ganadores (y el informe).
  * `extracto_mes` → la hoja que se publica (/sorteos/extracto/{año}/{mes}).

Reglas (ver memoria bono_sorteos_dominio):
  * El número ganador puede ser CUALQUIERA de la pata (principal o adicionales).
  * Ganador válido = tiene socio Y se vendió hasta el día del sorteo inclusive.
  * Un número gana al nivel de cifras MÁS ALTO que le corresponda (4 → 3 → 2).
  * El extracto cruza solo el premio 1 (3 en el FINAL); la pantalla, todos.
"""
import json
from datetime import date

import _entorno
from fastapi.testclient import TestClient

from app import auth, models
from app.main import app
from app.models import TipoSorteo
from app.routers.sorteos import _calcular_grupos_ganadores
from app.templates_config import templates


def _armar(db, tipo, fecha, cifras, resultado, boletas, especiales=None):
    """Crea talonera, socios, boletas y el sorteo. `boletas` = lista de dicts
    {nombre?, principal, adicionales?, venta?, especial?, especial_2?}."""
    pata = models.Talonera(nombre="PATA 1", tipo="COMUN", valor_cuota=15000, num_cuotas=12)
    pool = models.Talonera(nombre="CONTADO", tipo="CONTADO")
    db.add_all([pata, pool]); db.flush()
    ids = {}
    for i, d in enumerate(boletas):
        comp = None
        if d.get("nombre"):
            comp = models.Comprador(apellido_nombre=d["nombre"]); db.add(comp); db.flush()
        b = models.Boleta(
            talonera_id=pata.id, numero_principal=d["principal"],
            numeros_adicionales=d.get("adicionales"), fecha_venta=d.get("venta"),
            comprador_id=comp.id if comp else None,
            numero_especial=d.get("especial"),
            talonera_especial_id=pool.id if d.get("especial") is not None else None,
            numero_especial_2=d.get("especial_2"),
            talonera_especial_2_id=pool.id if d.get("especial_2") is not None else None,
        )
        db.add(b); db.flush()
        ids[d.get("nombre") or f"sin_socio_{i}"] = b.id
    # Los números del pool de CONTADO también existen como boletas (sin socio).
    for n in (especiales or []):
        db.add(models.Boleta(talonera_id=pool.id, numero_principal=n))
    s = models.Sorteo(nombre="test", tipo=tipo, cifras=cifras, fecha=fecha,
                      num_premios=20, resultado_json=json.dumps(resultado))
    db.add(s)
    db.add(models.User(username="tester", hashed_password=auth.hash_password("x" * 10), is_admin=True))
    db.commit()
    return s, ids


def _validos_pantalla(s, db, solo_premios=None):
    """{boleta_id: cifras} de ganadores válidos según la pantalla de ganadores."""
    grupos, _ = _calcular_grupos_ganadores(s, db)
    out = {}
    for g in grupos:
        if solo_premios and g["posicion"] > solo_premios:
            continue
        for f in g["filas"]:
            if f["es_ganador_valido"]:
                out[f["boleta_id"]] = max(out.get(f["boleta_id"], 0), g["cifras"])
    return out


def _extracto(fecha, tipo):
    """{boleta_id: cifras} de los ganadores del bloque `tipo` del extracto del mes.
    Pide la página de verdad y captura el contexto que recibe el template."""
    capturado = {}
    original = templates.TemplateResponse

    def _espia(request, name, context=None, *a, **kw):
        capturado.update(context or {})
        return original(request, name, context, *a, **kw)

    c = TestClient(app)
    c.cookies.update({"access_token": "Bearer " + auth.create_access_token({"sub": "tester"})})
    templates.TemplateResponse = _espia
    try:
        r = c.get(f"/sorteos/extracto/{fecha.year}/{fecha.month}")
    finally:
        templates.TemplateResponse = original
    assert r.status_code == 200, r.text[:300]
    bloque = next(b for b in capturado["bloques"] if b["tipo"] == tipo)
    return {g["boleta_id"]: g["cifras_match"] for g in bloque["ganadores"]}


def test_semanal_pantalla_y_extracto_coinciden():
    db = _entorno.base_limpia()
    f = date(2026, 10, 10)
    s, ids = _armar(db, TipoSorteo.SEMANAL, f, "4,3", ["1234", "5678"], [
        dict(nombre="A PRINCIPAL 4 CIFRAS", principal=1234, venta=date(2026, 10, 1)),
        dict(nombre="B ADICIONAL 3 CIFRAS", principal=100, adicionales="9234, 5100",
             venta=date(2026, 9, 1)),
        dict(nombre="C MISMO DIA", principal=3234, venta=f),
        dict(nombre="D DESPUES", principal=7234, venta=date(2026, 10, 11)),
        dict(principal=6234),                                  # sin socio
    ])
    esperado = {ids["A PRINCIPAL 4 CIFRAS"]: 4, ids["B ADICIONAL 3 CIFRAS"]: 3, ids["C MISMO DIA"]: 3}
    assert _validos_pantalla(s, db, solo_premios=1) == esperado
    assert _extracto(f, "SEMANAL") == esperado
    db.close()


def test_comprado_despues_figura_como_posterior():
    db = _entorno.base_limpia()
    f = date(2026, 10, 10)
    s, ids = _armar(db, TipoSorteo.SEMANAL, f, "4,3", ["7234"], [
        dict(nombre="D DESPUES", principal=7234, venta=date(2026, 10, 11)),
    ])
    grupos, _ = _calcular_grupos_ganadores(s, db)
    fila = grupos[0]["filas"][0]
    assert fila["posterior"] and not fila["es_ganador_valido"]
    db.close()


def test_final_gana_al_nivel_mas_alto_en_las_dos_vistas():
    # La boleta pega 3 cifras con el premio 1 ("555") y 4 cifras con el premio 2.
    db = _entorno.base_limpia()
    f = date(2027, 6, 26)
    s, ids = _armar(db, TipoSorteo.FINAL, f, "4,3", ["0555", "8555", "1111"], [
        dict(nombre="F", principal=8555, venta=date(2026, 10, 1)),
    ])
    assert _validos_pantalla(s, db, solo_premios=3) == {ids["F"]: 4}
    assert _extracto(f, "FINAL") == {ids["F"]: 4}
    db.close()


def test_contado_cruza_los_numeros_especiales_en_las_dos_vistas():
    # Sorteo CONTADO: juegan los números especiales, no los de la pata.
    db = _entorno.base_limpia()
    f = date(2026, 10, 24)
    s, ids = _armar(db, TipoSorteo.CONTADO, f, "4,3", ["0042"], [
        dict(nombre="G CONTADO", principal=1042, especial=42, venta=date(2026, 10, 1)),
        dict(nombre="H EN CUOTAS", principal=2042, venta=date(2026, 10, 1)),
    ], especiales=[42])
    esperado = {ids["G CONTADO"]: 4}
    assert _extracto(f, "CONTADO") == esperado
    assert _validos_pantalla(s, db, solo_premios=1) == esperado
    db.close()


def test_contado_juega_tambien_el_slot_2():
    # "Contado 2 veces": solo tiene número especial en el slot 2.
    db = _entorno.base_limpia()
    f = date(2026, 11, 28)
    s, ids = _armar(db, TipoSorteo.CONTADO, f, "3", ["0517"], [
        dict(nombre="K DOS VECES", principal=3000, especial_2=517, venta=date(2026, 10, 1)),
    ], especiales=[517])
    esperado = {ids["K DOS VECES"]: 3}
    assert _extracto(f, "CONTADO") == esperado
    assert _validos_pantalla(s, db, solo_premios=1) == esperado
    db.close()


def test_semanal_el_pool_de_contado_no_juega():
    # Las boletas de la talonera CONTADO son el pool de especiales: aunque su
    # número coincida, no son boletas vendidas y no figuran en la pantalla.
    db = _entorno.base_limpia()
    f = date(2026, 10, 10)
    s, ids = _armar(db, TipoSorteo.SEMANAL, f, "4,3", ["0042"], [], especiales=[42])
    grupos, _ = _calcular_grupos_ganadores(s, db)
    assert all(not g["filas"] for g in grupos)
    db.close()


def test_dos_semanales_del_mes_queda_el_nivel_mas_alto():
    # Misma boleta: 3 cifras en el semanal del 3 y 4 cifras en el del 17.
    db = _entorno.base_limpia()
    s1, ids = _armar(db, TipoSorteo.SEMANAL, date(2026, 10, 3), "4,3", ["0777"], [
        dict(nombre="M", principal=5777, venta=date(2026, 9, 1)),
    ])
    db.add(models.Sorteo(nombre="test 2", tipo=TipoSorteo.SEMANAL, cifras="4,3",
                         fecha=date(2026, 10, 17), num_premios=20, resultado_json=json.dumps(["5777"])))
    db.commit()
    assert _extracto(date(2026, 10, 3), "SEMANAL") == {ids["M"]: 4}
    db.close()
