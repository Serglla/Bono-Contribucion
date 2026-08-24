"""
diag_dashboard.py  --  SOLO LECTURA, no escribe nada.

Explica por que las TARJETAS del dashboard (En cuotas / Al contado / Vendidas)
no coinciden con la tabla "LIQUIDADO POR VENDEDOR".

Son DOS METRICAS DISTINTAS:

  * Tarjetas      -> estado ACTUAL de las boletas (comprador_id cargado),
                     ponderado por el multiplicador de HOY de cada talonera.
  * Liquidado     -> SNAPSHOT guardado al liquidar
                     (LiquidacionVendedor.cuotas_equiv / contados_equiv).

Este script descompone la diferencia en sus causas, una por una, y lista las
boletas concretas de cada grupo para que se puedan revisar a mano.

USO (PowerShell, desde bono-app/):
    $env:DATABASE_URL="postgresql://...proxy.rlwy.net:PUERTO/railway"
    py -3.12 diag_dashboard.py

    # con el detalle de cada boleta involucrada:
    py -3.12 diag_dashboard.py --detalle
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, or_

from app.database import SessionLocal
from app import models
from app.models import CondicionBoleta

DETALLE = "--detalle" in sys.argv


def fmt(x):
    return f"{float(x):,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def linea(c="-", n=78):
    print(c * n)


def titulo(t):
    print()
    linea("=")
    print(t)
    linea("=")


def _num(b, tal_by_id):
    t = tal_by_id.get(b.talonera_id)
    nom = (t.nombre if t else "?").replace("PATA ", "X")
    return f"{b.numero_principal:04d} {nom}"


def main():
    db = SessionLocal()
    try:
        taloneras = db.query(models.Talonera).all()
        tal_by_id = {t.id: t for t in taloneras}
        mult = {t.id: float(t.multiplicador or 1.0) for t in taloneras}
        vend_by_id = {v.id: v.nombre for v in db.query(models.Vendedor).all()}

        boletas = db.query(models.Boleta).all()

        def es_contado_hoy(b):
            """Mismo criterio que la tarjeta AL CONTADO del dashboard."""
            if b.numero_especial is not None:
                return True
            cp = b.cuotas_pactadas or 0
            ca = b.cuotas_anticipadas or 0
            return cp > 0 and ca >= cp

        def w(b):
            return mult.get(b.talonera_id, 1.0)

        # ── 1. Tarjetas ────────────────────────────────────────────────────
        card_vendidas = sum(w(b) for b in boletas if b.comprador_id is not None)
        card_contado = sum(w(b) for b in boletas if es_contado_hoy(b))
        card_cuotas = sum(
            w(b) for b in boletas
            if b.comprador_id is not None and not es_contado_hoy(b)
        )
        card_sin_cargar = sum(
            w(b) for b in boletas
            if b.liquidacion_vendedor_id is not None and b.comprador_id is None
        )
        card_baja = sum(
            w(b) for b in boletas
            if b.condicion == CondicionBoleta.BAJA or b.mes_baja is not None
        )

        titulo("1) TARJETAS DEL DASHBOARD  (estado actual, ponderado por PATA de hoy)")
        print(f"  EN CUOTAS ......... {fmt(card_cuotas):>10}   -> se muestra {round(card_cuotas)}")
        print(f"  AL CONTADO ........ {fmt(card_contado):>10}   -> se muestra {round(card_contado)}")
        print(f"  VENDIDAS (TOTAL) .. {fmt(card_vendidas):>10}   -> se muestra {round(card_vendidas)}")
        print(f"  SIN CARGAR ........ {fmt(card_sin_cargar):>10}   -> se muestra {round(card_sin_cargar)}")
        print(f"  BAJA .............. {fmt(card_baja):>10}   -> se muestra {round(card_baja)}")

        # ── 2. Tabla Liquidado por vendedor (snapshots) ────────────────────
        snap_rows = db.query(
            models.LiquidacionVendedor.vendedor_id,
            func.coalesce(func.sum(models.LiquidacionVendedor.cuotas_equiv), 0),
            func.coalesce(func.sum(models.LiquidacionVendedor.contados_equiv), 0),
        ).group_by(models.LiquidacionVendedor.vendedor_id).all()

        titulo("2) TABLA 'LIQUIDADO POR VENDEDOR'  (snapshots guardados al liquidar)")
        print(f"  {'VENDEDOR':<16}{'CUOTAS':>10}{'CONTADOS':>10}{'TOTAL':>10}")
        linea()
        tot_c = tot_k = 0.0
        for vid, ce, ke in sorted(snap_rows, key=lambda r: vend_by_id.get(r[0], "")):
            ce, ke = float(ce or 0), float(ke or 0)
            tot_c += ce
            tot_k += ke
            print(f"  {vend_by_id.get(vid, f'#{vid}'):<16}"
                  f"{round(ce):>10}{round(ke):>10}{round(ce + ke):>10}")
        linea()
        print(f"  {'TOTAL':<16}{round(tot_c):>10}{round(tot_k):>10}{round(tot_c + tot_k):>10}")

        # ── 3. Recalculo EN VIVO de lo liquidado ───────────────────────────
        # Suma el multiplicador de HOY de las boletas atadas a cada liquidacion,
        # separando por modalidad_liquidacion (la boleta sola no la guarda -> null
        # se asume 'cuotas', igual que hace la app al editar liquidaciones viejas).
        liq_boletas = [b for b in boletas if b.liquidacion_vendedor_id is not None]
        vivo_cuotas = sum(
            w(b) for b in liq_boletas
            if (b.modalidad_liquidacion or "cuotas") == "cuotas"
        )
        vivo_contados = sum(
            w(b) for b in liq_boletas
            if (b.modalidad_liquidacion or "cuotas") != "cuotas"
        )

        titulo("3) SNAPSHOT vs REALIDAD DE HOY  (misma poblacion: boletas liquidadas)")
        print(f"  {'':<20}{'SNAPSHOT':>12}{'HOY':>12}{'DIFERENCIA':>14}")
        linea()
        print(f"  {'Cuotas':<20}{round(tot_c):>12}{round(vivo_cuotas):>12}"
              f"{round(vivo_cuotas - tot_c):>14}")
        print(f"  {'Contados':<20}{round(tot_k):>12}{round(vivo_contados):>12}"
              f"{round(vivo_contados - tot_k):>14}")
        print(f"  {'Total':<20}{round(tot_c + tot_k):>12}{round(vivo_cuotas + vivo_contados):>12}"
              f"{round((vivo_cuotas + vivo_contados) - (tot_c + tot_k)):>14}")
        print()
        print("  Si esta columna DIFERENCIA no es 0, el snapshot quedo viejo:")
        print("  boletas liberadas/eliminadas sin descontar de la liquidacion,")
        print("  reasignadas a otra PATA, o multiplicador de talonera cambiado.")

        # ── 4. Descomposicion de la brecha tarjeta vs tabla ────────────────
        # a) liquidadas SIN socio cargado  -> suman en la tabla, no en la tarjeta
        g_sin_socio = [b for b in liq_boletas if b.comprador_id is None]
        # b) con socio pero NUNCA liquidadas -> suman en la tarjeta, no en la tabla
        g_sin_liq = [b for b in boletas
                     if b.comprador_id is not None and b.liquidacion_vendedor_id is None]
        # c) liquidadas como CUOTAS pero hoy son CONTADO -> la tarjeta las movio
        #    de columna, el snapshot no
        g_cambio_mod = [b for b in liq_boletas
                        if b.comprador_id is not None
                        and (b.modalidad_liquidacion or "cuotas") == "cuotas"
                        and es_contado_hoy(b)]
        # d) liquidadas como CONTADO pero hoy NO figuran como contado
        g_cambio_mod_inv = [b for b in liq_boletas
                            if b.comprador_id is not None
                            and (b.modalidad_liquidacion or "cuotas") != "cuotas"
                            and not es_contado_hoy(b)]

        titulo("4) POR QUE NO COINCIDEN  (descomposicion de la brecha)")
        print(f"  {'CAUSA':<52}{'PESO':>10}{'BOLETAS':>9}")
        linea()
        for etiqueta, grupo, signo in [
            ("Liquidadas SIN socio cargado (suman solo en la tabla)", g_sin_socio, "+"),
            ("Con socio pero NUNCA liquidadas (solo en la tarjeta)", g_sin_liq, "-"),
            ("Liquidadas en cuotas, hoy figuran AL CONTADO", g_cambio_mod, "~"),
            ("Liquidadas al contado, hoy figuran EN CUOTAS", g_cambio_mod_inv, "~"),
        ]:
            print(f"  {signo} {etiqueta:<50}{fmt(sum(w(b) for b in grupo)):>10}{len(grupo):>9}")
        linea()
        neto = (sum(w(b) for b in g_sin_socio) - sum(w(b) for b in g_sin_liq)
                + (vivo_cuotas + vivo_contados - (tot_c + tot_k)) * -1)
        print(f"  Tarjeta VENDIDAS ({round(card_vendidas)})"
              f"  vs  tabla TOTAL ({round(tot_c + tot_k)})"
              f"  ->  brecha {round(tot_c + tot_k - card_vendidas):+}")
        print(f"  Explicado por las causas de arriba: {round(neto):+}")
        print("  (Las filas '~' no mueven el total: corren peso entre CUOTAS y CONTADO.)")

        if DETALLE:
            for etiqueta, grupo in [
                ("LIQUIDADAS SIN SOCIO CARGADO", g_sin_socio),
                ("CON SOCIO Y SIN LIQUIDAR", g_sin_liq),
                ("LIQUIDADAS EN CUOTAS, HOY AL CONTADO", g_cambio_mod),
                ("LIQUIDADAS AL CONTADO, HOY EN CUOTAS", g_cambio_mod_inv),
            ]:
                if not grupo:
                    continue
                titulo(f"DETALLE — {etiqueta}  ({len(grupo)})")
                for b in sorted(grupo, key=lambda x: (x.talonera_id, x.numero_principal)):
                    comp = ""
                    if b.comprador_id:
                        c = db.query(models.Comprador).get(b.comprador_id)
                        comp = c.apellido_nombre if c else f"#{b.comprador_id}"
                    print(f"  {_num(b, tal_by_id):<14}"
                          f"vend={vend_by_id.get(b.vendedor_id, '—'):<10}"
                          f"cond={(b.condicion.value if b.condicion else '—'):<13}"
                          f"mod={(b.modalidad_liquidacion or '—'):<9}"
                          f"cuotas={b.cuotas_pagadas or 0}/{b.cuotas_pactadas or 0}  {comp}")

        print()
        print("Nada de esto se modifico: el script es solo de lectura.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
