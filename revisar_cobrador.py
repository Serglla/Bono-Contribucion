# -*- coding: utf-8 -*-
"""
revisar_cobrador.py
===================

Explica, para UN cobrador y UN mes, de donde sale cada peso de la hoja
"LIQUIDACION DEL MES": cobranza por planilla, entregas y saldo.

Por que existe
--------------
La hoja del mes (app/routers/cobranza.py -> _consolidado_cobrador) contaba las
cuotas de una planilla mirando SOLO las boletas que estan en la planilla HOY,
sin aplicar el corte de "pasar numeros" (paso_cuota / cuotas recibidas) que si
aplican la planilla impresa y la hoja de liquidacion. Consecuencia: si un
numero se pasa a otra planilla o a otro cobrador, la cobranza de los meses YA
LIQUIDADOS se le va con el numero, el neto del mes cambia despues de haber
cerrado, y el saldo del cobrador deja de dar cero (queda negativo: aparece como
que entrego mas de lo que debia).

Este script muestra las dos cuentas — REGLA VIEJA vs REGLA NUEVA (con corte) —
para ver si la diferencia del mes se explica por numeros pasados.

USO (desde la carpeta bono-app/):

    revisar_cobrador.bat MABEL 8 2026

    o bien:
    set "DATABASE_URL=postgresql://..."
    py -3.12 revisar_cobrador.py MABEL 8 2026

No escribe NADA en la base: es solo lectura.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import or_
from sqlalchemy.orm import undefer

from app.database import SessionLocal
from app import models
from app.tiempo import hoy_ar, match_periodo, parse_periodo


def plata(x):
    return "$" + f"{x:,.0f}".replace(",", ".")


def hist(b):
    try:
        return json.loads(b.historial_cuotas) if b.historial_cuotas else {}
    except (ValueError, TypeError):
        return {}


def cuenta_aca(clave, corte, recibidas):
    try:
        kn = int(clave)
    except (TypeError, ValueError):
        return False
    if corte is not None and kn > corte:
        return False
    if recibidas and kn <= recibidas:
        return False
    return True


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    nombre = (args[0] if args else "MABEL").upper()
    hoy = hoy_ar()
    mes = int(args[1]) if len(args) > 1 else hoy.month
    anio = int(args[2]) if len(args) > 2 else hoy.year

    db = SessionLocal()
    cob = (db.query(models.Cobrador)
           .filter(models.Cobrador.nombre.ilike(nombre)).first())
    if not cob:
        print("No encontre el cobrador", nombre)
        print("Hay:", [c.nombre for c in db.query(models.Cobrador).all()])
        return

    print("=" * 78)
    print(f"  {cob.nombre} — periodo {mes:02d}/{anio}")
    print("=" * 78)

    planillas = (db.query(models.Planilla)
                 .filter_by(cobrador_id=cob.id)
                 .order_by(models.Planilla.anio, models.Planilla.mes,
                           models.Planilla.numero).all())
    ordm = anio * 12 + mes
    tot_v = {"monto": 0.0, "com": 0.0}
    tot_n = {"monto": 0.0, "com": 0.0}
    pases = []

    print(f"\n{'Planilla':<10}{'REGLA VIEJA (hoy)':>26}{'CON CORTE DE PASE':>26}")
    print(f"{'':<10}{'cuotas':>8}{'monto':>18}{'cuotas':>8}{'monto':>18}")
    for p in planillas:
        boletas = (db.query(models.Boleta)
                   .options(undefer(models.Boleta.paso_origen_planilla_id),
                            undefer(models.Boleta.paso_cuota),
                            undefer(models.Boleta.paso_a))
                   .filter(or_(models.Boleta.planilla_id == p.id,
                               models.Boleta.paso_origen_planilla_id == p.id))
                   .all())
        pct = float(p.comision_pct or 0)
        v_c = v_m = n_c = n_m = 0.0
        for b in boletas:
            h = hist(b)
            vc = float(b.talonera.valor_cuota) if (b.talonera and b.talonera.valor_cuota) else 0.0
            salio = (b.paso_origen_planilla_id == p.id and b.planilla_id != p.id)
            recib = int(b.paso_cuota or 0) if (b.paso_origen_planilla_id
                                               and b.paso_origen_planilla_id != p.id
                                               and b.planilla_id == p.id) else 0
            corte = int(b.paso_cuota or 0) if salio else None
            # regla vieja: solo las boletas que estan HOY en la planilla, todas
            # sus cuotas del mes, sin corte.
            if b.planilla_id == p.id:
                c = sum(1 for v in h.values() if match_periodo(v, anio, mes))
                v_c += c
                v_m += c * vc
            # regla nueva: con corte de pase
            c2 = sum(1 for k, v in h.items()
                     if cuenta_aca(k, corte, recib) and match_periodo(v, anio, mes))
            n_c += c2
            n_m += c2 * vc
            if b.paso_origen_planilla_id:
                cm = [k for k, v in h.items() if match_periodo(v, anio, mes)]
                if cm:
                    pases.append((p.numero, b.numero_principal, b.paso_a,
                                  "SALIO" if salio else "ENTRO",
                                  int(b.paso_cuota or 0), sorted(cm, key=int), vc))
        tot_v["monto"] += v_m
        tot_v["com"] += round(v_m * pct / 100.0, 2)
        tot_n["monto"] += n_m
        tot_n["com"] += round(n_m * pct / 100.0, 2)
        if v_m or n_m:
            print(f"P{p.numero:<9}{v_c:>8.2f}{plata(v_m):>18}{n_c:>8.2f}{plata(n_m):>18}")

    for tag, t in (("HOY (regla vieja)", tot_v), ("CON CORTE (arreglado)", tot_n)):
        neto = t["monto"] - t["com"]
        print(f"\n{tag}:  cobrado {plata(t['monto'])}   comision {plata(t['com'])}"
              f"   NETO {plata(neto)}")

    ade = (db.query(models.EntregaCobrador)
           .filter_by(cobrador_id=cob.id, mes=mes, anio=anio)
           .order_by(models.EntregaCobrador.fecha).all())
    print("\nENTREGAS DEL PERIODO")
    for a in ade:
        print(f"  {a.fecha}  {(a.tipo or 'EFECTIVO'):<9}{plata(float(a.monto or 0)):>16}"
              f"   {a.observacion or ''}")
    te = sum(float(a.monto or 0) for a in ade)
    print(f"  TOTAL ENTREGADO {plata(te)}")
    for tag, t in (("HOY", tot_v), ("CON CORTE", tot_n)):
        print(f"  SALDO {tag}: {plata(t['monto'] - t['com'] - te)}"
              "   (negativo = la hoja dice que entrego de mas)")

    if pases:
        print("\nNUMEROS PASADOS que tienen cuotas cobradas en este periodo")
        print("(son los que le cambian el total del mes despues de liquidado)")
        for num_pl, nro, label, dir_, pc, cuotas, vc in pases:
            print(f"  P{num_pl}  nro {nro}  {dir_} ({label})  paso_cuota={pc}"
                  f"  cuotas del mes: {cuotas}  valor {plata(vc)}")
    else:
        print("\nNo hay numeros pasados con cobranza en este periodo:")
        print("la diferencia del mes NO viene del pase de numeros.")

    db.close()


if __name__ == "__main__":
    main()
