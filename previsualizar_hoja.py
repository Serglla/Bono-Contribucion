# -*- coding: utf-8 -*-
"""
previsualizar_hoja.py
=====================

Muestra como quedaria la hoja "LIQUIDACION DEL MES" con los dos cambios
propuestos, SIN tocar la app todavia:

  1) Las cuotas se abren en PATA (multiples de $15.000, contadas por PATA:
     una X2 cuenta 2) y X0 (las de $10.000, contadas de a una).
     Control: PATA x 15.000 + X0 x 10.000 tiene que dar el monto cobrado.

  2) Una planilla entregada en el MISMO mes que se liquida deja de mostrar
     "sin cobrar" vacio y 100% cuando en realidad ya se le cobro: si tuvo
     cobranza en el mes, lo no cobrado cuenta como en cualquier otra.

Solo lectura. Uso:  previsualizar_hoja.bat MABEL 8 2026
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

# Copia local de los 3 helpers de app/routers/cobranza.py. No se importan de
# alla a proposito: ese modulo arrastra xhtml2pdf y este script tiene que poder
# correrse en cualquier PC sin instalar las dependencias del server.


def _cuota_cuenta_aca(clave, corte, recibidas) -> bool:
    try:
        kn = int(clave)
    except (TypeError, ValueError):
        return False
    if corte is not None and kn > corte:
        return False
    if recibidas and kn <= recibidas:
        return False
    return True


def _build_paso_map(boletas, planilla_id) -> dict:
    return {b.id: {"label": (b.paso_a or "OTRA PLANILLA"),
                   "cuota": int(b.paso_cuota or 0)}
            for b in boletas
            if b.paso_origen_planilla_id == planilla_id and b.planilla_id != planilla_id}


def _build_recibida_map(boletas, planilla_id) -> dict:
    return {b.id: int(b.paso_cuota or 0)
            for b in boletas
            if b.paso_origen_planilla_id and b.paso_origen_planilla_id != planilla_id
            and b.planilla_id == planilla_id}


def plata(x):
    return "$" + f"{x:,.0f}".replace(",", ".")


def n(x):
    return f"{x:g}" if x else "—"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    nombre = (args[0] if args else "MABEL").upper()
    hoy = hoy_ar()
    mes = int(args[1]) if len(args) > 1 else hoy.month
    anio = int(args[2]) if len(args) > 2 else hoy.year
    ordm = anio * 12 + mes

    db = SessionLocal()
    cob = db.query(models.Cobrador).filter(models.Cobrador.nombre.ilike(nombre)).first()
    if not cob:
        print("No encontre el cobrador", nombre)
        return

    print("=" * 90)
    print(f"  ASI QUEDARIA LA HOJA — {cob.nombre} — {mes:02d}/{anio}")
    print("=" * 90)
    print(f"\n{'Planilla':<9}{'Cuotas cobradas':^18}{'Sin cobrar':^16}"
          f"{'Bajas':>7}{'% cobr':>8}{'Monto':>14}{'Comision':>13}{'Neto':>14}")
    print(f"{'':<9}{'PATA':>9}{'X0':>9}{'PATA':>9}{'X0':>7}")
    print("-" * 90)

    T = dict(cp=0.0, cz=0.0, sp=0.0, sz=0.0, b=0, m=0.0, c=0.0)
    alerta = []
    planillas = (db.query(models.Planilla).filter_by(cobrador_id=cob.id)
                 .order_by(models.Planilla.anio, models.Planilla.mes,
                           models.Planilla.numero).all())
    for p in planillas:
        boletas = (db.query(models.Boleta)
                   .options(undefer(models.Boleta.paso_origen_planilla_id),
                            undefer(models.Boleta.paso_cuota),
                            undefer(models.Boleta.paso_a))
                   .filter(or_(models.Boleta.planilla_id == p.id,
                               models.Boleta.paso_origen_planilla_id == p.id)).all())
        paso_map = _build_paso_map(boletas, p.id)
        recibida_map = _build_recibida_map(boletas, p.id)
        pct = float(p.comision_pct or 0)
        entregada_antes = (int(p.anio or 0) * 12 + int(p.mes or 0)) < ordm

        # 1a pasada: cuanto se cobro en el mes (define si la planilla ya se cobraba)
        datos = []
        for b in boletas:
            try:
                h = json.loads(b.historial_cuotas) if b.historial_cuotas else {}
            except (ValueError, TypeError):
                h = {}
            salio = b.id in paso_map
            corte = int(paso_map[b.id].get("cuota") or 0) if salio else None
            rec = int(recibida_map.get(b.id, 0) or 0)
            cM = sum(1 for k, v in h.items()
                     if _cuota_cuenta_aca(k, corte, rec) and match_periodo(v, anio, mes))
            datos.append((b, h, salio, cM))
        hubo_cobranza = any(d[3] for d in datos)

        cp = cz = sp = sz = mto = 0.0
        bj = 0
        for b, h, salio, cM in datos:
            mult = float(b.talonera.multiplicador or 1.0) if b.talonera else 1.0
            vc = float(b.talonera.valor_cuota or 0) if b.talonera else 0.0
            es_x0 = mult < 1.0
            try:
                mb = int(b.mes_baja) if b.mes_baja else 0
            except (TypeError, ValueError):
                mb = 0
            if mb == mes and not salio:
                bj += 1
            if cM:
                mto += cM * vc
                if es_x0:
                    cz += cM
                else:
                    cp += cM * mult
                continue
            if salio:
                continue
            # ── no cobro nada: ¿se le podia cobrar? ──
            if not entregada_antes and not hubo_cobranza:
                continue          # planilla recien entregada y todavia sin cobrar
            if mb and mb <= mes:
                continue
            pag = int(b.cuotas_anticipadas or 0) + sum(
                1 for v in h.values()
                if parse_periodo(v) and ((parse_periodo(v)[0] or anio) * 12
                                         + parse_periodo(v)[1]) < ordm)
            if (b.cuotas_pactadas or 0) and pag >= (b.cuotas_pactadas or 0):
                continue
            if es_x0:
                sz += 1
            else:
                sp += mult
        if not (mto or cp or cz or sp or sz or bj):
            continue
        com = round(mto * pct / 100.0, 2)
        base = cp + cz
        basesin = base + sp + sz
        efect = round(base / basesin * 100) if basesin > 0 else 0
        print(f"P{p.numero:<8}{n(cp):>9}{n(cz):>9}{n(sp):>9}{n(sz):>7}"
              f"{n(bj):>7}{efect:>7}%"
              f"{plata(mto):>14}{plata(com):>13}{plata(mto-com):>14}")
        if abs((cp * 15000 + cz * 10000) - mto) > 0.5:
            alerta.append((p.numero, cp * 15000 + cz * 10000, mto))
        T["cp"] += cp; T["cz"] += cz; T["sp"] += sp; T["sz"] += sz
        T["b"] += bj; T["m"] += mto; T["c"] += com

    base = T["cp"] + T["cz"]
    basesin = base + T["sp"] + T["sz"]
    print("-" * 90)
    print(f"{'TOTAL':<9}{n(T['cp']):>9}{n(T['cz']):>9}{n(T['sp']):>9}{n(T['sz']):>7}"
          f"{n(T['b']):>7}{round(base/basesin*100) if basesin else 0:>7}%"
          f"{plata(T['m']):>14}{plata(T['c']):>13}{plata(T['m']-T['c']):>14}")

    print(f"\nControl: PATA {n(T['cp'])} x $15.000 + X0 {n(T['cz'])} x $10.000 = "
          f"{plata(T['cp']*15000 + T['cz']*10000)}   |   monto cobrado {plata(T['m'])}")
    if alerta:
        print("OJO: en estas planillas el control no da (hay taloneras con otro valor):")
        for num, calc, real in alerta:
            print(f"  P{num}: control {plata(calc)} vs real {plata(real)}")

    ade = (db.query(models.EntregaCobrador)
           .filter_by(cobrador_id=cob.id, mes=mes, anio=anio)
           .order_by(models.EntregaCobrador.fecha).all())
    te = sum(float(a.monto or 0) for a in ade)
    print(f"\nEntregado {plata(te)}   ->   SALDO A ENTREGAR "
          f"{plata(T['m'] - T['c'] - te)}   (no cambia con estos ajustes)")
    db.close()


if __name__ == "__main__":
    main()
