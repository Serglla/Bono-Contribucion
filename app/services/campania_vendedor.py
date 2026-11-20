"""Cierre de campaña de un vendedor + informe de campaña.

Definiciones (acordadas con Sergio 19/11/2026):
  * La campaña del vendedor ARRANCA en su primera entrega a caja (EntregaCaja
    tipo ENTREGA a su nombre).
  * Días trabajados = desde esa primera entrega hasta su ÚLTIMA liquidación
    (días corridos, ambos inclusive).
  * Al finalizar, las boletas que tiene en mano (CAJA, sin liquidar, sin socio)
    vuelven al stock de la institución (SIN_VENDER, sin vendedor) y quedan
    registradas como RETIRO; después se reparten entre los que quedan con una
    entrega a caja normal.
  * El informe lista los números que el vendedor no entregó (no rindió) hasta
    la fecha. Como al cerrar esos números vuelven al stock, la lista se guarda
    en el snapshot del cierre.
  * Ingresos: SIEMPRE desde las liquidaciones (regla de oro), con el ajuste de
    boletas reasignadas a otro vendedor (`ingresos_por_liq`).
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy import func, or_

from .. import models
from ..models import CondicionBoleta
from ..tiempo import ahora_ar, hoy_ar

MESES_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
            "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


# ─────────────────────────── ingresos ───────────────────────────
def ingresos_por_liq(db, vid: int, liquidaciones) -> dict:
    """{liq_id: ingreso del vendedor} — lo que se queda en el bolsillo.

    = cuota 1 + comisión contado + comisiones de cuotas extras (valores GUARDADOS
    en la liquidación), menos la cuota 1 de boletas EN CUOTAS que después se
    reasignaron a otro vendedor (el otro cobra la cuota 1). Las boletas al
    contado no se restan aunque no tengan numero_especial todavía (se detectan
    por cuotas_anticipadas >= cuotas_pactadas). Ver fix 23/05/2026 (_pasadas).
    """
    ingreso = {
        liq.id: (float(liq.cuota_1_total or 0)
                 + float(liq.comision_contados or 0)
                 + float(liq.comision_cuotas_extras or 0)
                 + float(getattr(liq, "comision_cuotas_extras_p0", 0) or 0))
        for liq in liquidaciones
    }
    if not ingreso:
        return ingreso
    pasadas = db.query(models.Boleta).filter(
        models.Boleta.liquidacion_vendedor_id.in_(list(ingreso.keys())),
        models.Boleta.vendedor_id != vid,
        models.Boleta.numero_especial.is_(None),
        models.Boleta.numero_especial_2.is_(None),
        or_(
            models.Boleta.cuotas_anticipadas.is_(None),
            models.Boleta.cuotas_pactadas.is_(None),
            models.Boleta.cuotas_anticipadas < models.Boleta.cuotas_pactadas,
        ),
    ).all()
    for b in pasadas:
        if b.liquidacion_vendedor_id in ingreso and b.talonera:
            ingreso[b.liquidacion_vendedor_id] -= float(b.talonera.valor_cuota or 0)
    return ingreso


# ─────────────────────────── boletas ───────────────────────────
def boletas_en_mano(db, vid: int):
    """Boletas que el vendedor tiene físicamente y no rindió: CAJA, sin
    liquidar, sin socio. Son las que vuelven al stock al cerrar."""
    return db.query(models.Boleta).filter(
        models.Boleta.vendedor_id == vid,
        models.Boleta.condicion == CondicionBoleta.CAJA,
        models.Boleta.liquidacion_vendedor_id.is_(None),
        models.Boleta.comprador_id.is_(None),
    ).all()


def liquidadas_sin_socio(db, vid: int):
    """Liquidadas (rendidas) pero todavía sin datos del socio cargados."""
    return db.query(models.Boleta).filter(
        models.Boleta.vendedor_id == vid,
        models.Boleta.condicion == CondicionBoleta.CAJA,
        models.Boleta.liquidacion_vendedor_id.isnot(None),
        models.Boleta.comprador_id.is_(None),
    ).all()


def _rangos(nums):
    nums = sorted(set(nums))
    if not nums:
        return []
    out = []
    ini = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
        else:
            out.append((ini, prev))
            ini = prev = n
    out.append((ini, prev))
    return out


def _pata_orden(nombre: str):
    """Mismo criterio que el detalle: PATA n numérico, otras, CONTADO al final."""
    up = (nombre or "").upper()
    if up.startswith("PATA"):
        try:
            return (0, int(up.replace("PATA", "").strip()), up)
        except ValueError:
            pass
    if "CONTADO" in up:
        return (2, 0, up)
    return (1, 0, up)


def agrupar_numeros(boletas) -> list:
    """[{talonera, cantidad, rangos_txt}] ordenado por PATA, con rangos
    comprimidos y formateados con los dígitos de la talonera."""
    por_tal = defaultdict(list)
    digitos = {}
    for b in boletas:
        t = b.talonera
        nombre = t.nombre if t else "?"
        por_tal[nombre].append(int(b.numero_principal))
        digitos[nombre] = (t.num_digitos if t and t.num_digitos else 4)
    out = []
    for nombre in sorted(por_tal, key=_pata_orden):
        nd = digitos[nombre]
        fmt = "{:0" + str(nd) + "d}"
        partes = []
        for d, h in _rangos(por_tal[nombre]):
            partes.append(fmt.format(d) if d == h else f"{fmt.format(d)}–{fmt.format(h)}")
        out.append({"talonera": nombre, "cantidad": len(por_tal[nombre]),
                    "rangos_txt": ", ".join(partes)})
    return out


def devolver_a_stock(db, vid: int, boletas, usuario_id, observacion: str) -> tuple[int, int]:
    """Devuelve boletas a la institución (SIN_VENDER, sin vendedor) y deja un
    EntregaCaja tipo RETIRO por cada rango contiguo. NO hace commit.
    Devuelve (boletas_devueltas, registros_creados)."""
    por_tal = defaultdict(list)
    for b in boletas:
        if b.talonera_id is not None:
            por_tal[b.talonera_id].append(b)
    registros = 0
    for tid, bs in por_tal.items():
        t = db.query(models.Talonera).get(tid)
        if not t:
            continue
        for d, h in _rangos(b.numero_principal for b in bs):
            db.add(models.EntregaCaja(
                talonera_nombre=t.nombre, desde=d, hasta=h,
                boletas_afectadas=h - d + 1, usuario_id=usuario_id,
                vendedor_id=vid, tipo="RETIRO", observacion=observacion,
            ))
            registros += 1
    total = 0
    for b in boletas:
        b.condicion = CondicionBoleta.SIN_VENDER
        b.vendedor_id = None
        total += 1
    return total, registros


# ─────────────────────────── métricas ───────────────────────────
def _es_entrega(e) -> bool:
    return (getattr(e, "tipo", None) or "ENTREGA") != "RETIRO"


def _bajas_query(db):
    return db.query(models.Boleta).filter(
        or_(models.Boleta.condicion == CondicionBoleta.BAJA,
            models.Boleta.mes_baja.isnot(None)))


def _resumen_equipo(db, excluir_vid: int) -> dict:
    """Promedios del resto de los vendedores (con al menos una liquidación),
    calculados con las mismas definiciones que el informe."""
    inicios = dict(db.query(models.EntregaCaja.vendedor_id, func.min(models.EntregaCaja.fecha))
                   .filter(models.EntregaCaja.vendedor_id.isnot(None),
                           or_(models.EntregaCaja.tipo.is_(None), models.EntregaCaja.tipo != "RETIRO"))
                   .group_by(models.EntregaCaja.vendedor_id).all())
    liq_rows = db.query(
        models.LiquidacionVendedor.vendedor_id,
        func.max(models.LiquidacionVendedor.fecha),
        func.sum(func.coalesce(models.LiquidacionVendedor.cuotas_equiv, 0)),
        func.sum(func.coalesce(models.LiquidacionVendedor.contados_equiv, 0)),
    ).group_by(models.LiquidacionVendedor.vendedor_id).all()
    vend = dict(db.query(models.Boleta.vendedor_id, func.count(models.Boleta.id))
                .filter(models.Boleta.vendedor_id.isnot(None), models.Boleta.comprador_id.isnot(None))
                .group_by(models.Boleta.vendedor_id).all())
    bajas = dict(_bajas_query(db).with_entities(models.Boleta.vendedor_id, func.count(models.Boleta.id))
                 .filter(models.Boleta.vendedor_id.isnot(None))
                 .group_by(models.Boleta.vendedor_id).all())
    por_dia, pct_bajas = [], []
    for vid, ult, ceq, keq in liq_rows:
        if vid == excluir_vid or not ult:
            continue
        ini = inicios.get(vid)
        if ini:
            dias = (ult.date() - ini.date()).days + 1
            if dias > 0:
                por_dia.append((float(ceq or 0) + float(keq or 0)) / dias)
        if vend.get(vid):
            pct_bajas.append(100.0 * bajas.get(vid, 0) / vend[vid])
    return {
        "n": len(por_dia),
        "prom_por_dia": (sum(por_dia) / len(por_dia)) if por_dia else None,
        "pct_bajas": (sum(pct_bajas) / len(pct_bajas)) if pct_bajas else None,
    }


def calcular_informe(db, v, contado_pendiente: dict | None = None) -> dict:
    """Arma todas las cifras del informe de campaña del vendedor `v` (en vivo)."""
    vid = v.id
    liqs = (db.query(models.LiquidacionVendedor)
            .filter(models.LiquidacionVendedor.vendedor_id == vid)
            .order_by(models.LiquidacionVendedor.fecha).all())
    entregas = (db.query(models.EntregaCaja)
                .filter(models.EntregaCaja.vendedor_id == vid)
                .order_by(models.EntregaCaja.fecha).all())

    ent_fechas = [e.fecha for e in entregas if e.fecha and _es_entrega(e)]
    inicio = ent_fechas[0] if ent_fechas else None
    liq_fechas = [l.fecha for l in liqs if l.fecha]
    ultima_liq = liq_fechas[-1] if liq_fechas else None
    dias_trabajados = ((ultima_liq.date() - inicio.date()).days + 1
                       if inicio and ultima_liq and ultima_liq.date() >= inicio.date() else 0)
    dias_con_liq = len({f.date() for f in liq_fechas})

    entregadas = sum(int(e.boletas_afectadas or 0) for e in entregas if _es_entrega(e))
    retiradas = sum(int(e.boletas_afectadas or 0) for e in entregas if not _es_entrega(e))

    ing = ingresos_por_liq(db, vid, liqs)
    ingresos_total = sum(ing.values())

    cuotas_eq = sum(float(l.cuotas_equiv or 0) for l in liqs)
    contados_eq = sum(float(l.contados_equiv or 0) for l in liqs)
    cuotas_n = sum(int(l.cuotas_vendidas or 0) for l in liqs)
    contados_n = sum(int(l.contados_vendidos or 0) for l in liqs)
    extras_n = sum(int(l.cuotas_extras_cantidad or 0)
                   + int(getattr(l, "cuotas_extras_p0_cantidad", 0) or 0) for l in liqs)
    total_eq = cuotas_eq + contados_eq

    # Por mes y por día
    meses, dias = {}, {}
    for l in liqs:
        if not l.fecha:
            continue
        k = (l.fecha.year, l.fecha.month)
        m = meses.setdefault(k, {"label": f"{MESES_ES[k[1] - 1]} {k[0]}", "n_liq": 0,
                                 "dias": set(), "cuotas_eq": 0.0, "contados_eq": 0.0,
                                 "extras": 0, "ingresos": 0.0})
        m["n_liq"] += 1
        m["dias"].add(l.fecha.date())
        m["cuotas_eq"] += float(l.cuotas_equiv or 0)
        m["contados_eq"] += float(l.contados_equiv or 0)
        m["extras"] += (int(l.cuotas_extras_cantidad or 0)
                        + int(getattr(l, "cuotas_extras_p0_cantidad", 0) or 0))
        m["ingresos"] += ing.get(l.id, 0.0)
        d = dias.setdefault(l.fecha.date(), {"eq": 0.0, "ingresos": 0.0})
        d["eq"] += float(l.cuotas_equiv or 0) + float(l.contados_equiv or 0)
        d["ingresos"] += ing.get(l.id, 0.0)
    por_mes = []
    for k in sorted(meses):
        m = meses[k]
        por_mes.append({"label": m["label"], "n_liq": m["n_liq"], "dias_liq": len(m["dias"]),
                        "cuotas_eq": m["cuotas_eq"], "contados_eq": m["contados_eq"],
                        "total_eq": m["cuotas_eq"] + m["contados_eq"],
                        "extras": m["extras"], "ingresos": m["ingresos"]})
    mejor_mes = max(por_mes, key=lambda r: r["ingresos"]) if por_mes else None
    mejor_dia = None
    if dias:
        fd, dd = max(dias.items(), key=lambda kv: kv[1]["eq"])
        mejor_dia = {"fecha": fd.strftime("%d/%m/%Y"), "eq": dd["eq"], "ingresos": dd["ingresos"]}

    # Vendidas (con socio) y bajas — mismo criterio que el dashboard (vendedor_id actual)
    vendidas = (db.query(models.Boleta)
                .filter(models.Boleta.vendedor_id == vid, models.Boleta.comprador_id.isnot(None)).all())
    bajas = _bajas_query(db).filter(models.Boleta.vendedor_id == vid).all()
    n_vendidas = len(vendidas)
    pct_bajas = (100.0 * len(bajas) / n_vendidas) if n_vendidas else 0.0

    vend_pata = defaultdict(int)
    for b in vendidas:
        vend_pata[b.talonera.nombre if b.talonera else "?"] += 1
    baja_pata = defaultdict(int)
    baja_mes = defaultdict(int)
    for b in bajas:
        baja_pata[b.talonera.nombre if b.talonera else "?"] += 1
        baja_mes[int(b.mes_baja) if b.mes_baja else 0] += 1
    por_pata = [{"pata": p, "vendidas": vend_pata.get(p, 0), "bajas": baja_pata.get(p, 0),
                 "pct": (100.0 * baja_pata.get(p, 0) / vend_pata[p]) if vend_pata.get(p) else 0.0}
                for p in sorted(set(vend_pata) | set(baja_pata), key=_pata_orden)]
    bajas_por_mes = [{"mes": (f"Mes {k}" if k else "Sin mes"), "cantidad": c}
                     for k, c in sorted(baja_mes.items(), key=lambda kv: (kv[0] == 0, kv[0]))]

    en_mano = boletas_en_mano(db, vid)
    sin_socio = liquidadas_sin_socio(db, vid)
    zonas = [z.nombre for z in db.query(models.Zona).filter(models.Zona.vendedor_id == vid).all()
             if getattr(z, "nombre", None)]

    return {
        "inicio": inicio.strftime("%d/%m/%Y") if inicio else None,
        "ultima_liq": ultima_liq.strftime("%d/%m/%Y") if ultima_liq else None,
        "dias_trabajados": dias_trabajados,
        "dias_con_liq": dias_con_liq,
        "n_liquidaciones": len(liqs),
        "entregadas": entregadas,
        "retiradas": retiradas,
        "cuotas_eq": cuotas_eq, "contados_eq": contados_eq, "total_eq": total_eq,
        "cuotas_n": cuotas_n, "contados_n": contados_n, "extras_n": extras_n,
        "prom_eq_dia": (total_eq / dias_trabajados) if dias_trabajados else 0.0,
        "prom_ing_dia": (ingresos_total / dias_trabajados) if dias_trabajados else 0.0,
        "prom_ing_mes": (ingresos_total / len(por_mes)) if por_mes else 0.0,
        "ingresos_total": ingresos_total,
        "por_mes": por_mes,
        "mejor_mes": mejor_mes,
        "mejor_dia": mejor_dia,
        "n_vendidas": n_vendidas,
        "n_bajas": len(bajas),
        "pct_bajas": pct_bajas,
        "por_pata": por_pata,
        "bajas_por_mes": bajas_por_mes,
        "en_mano_n": len(en_mano),
        "en_mano": agrupar_numeros(en_mano),
        "sin_socio_n": len(sin_socio),
        "sin_socio": agrupar_numeros(sin_socio),
        "contado_pend": contado_pendiente or {"contado": 0, "contado2": 0},
        "zonas": zonas,
        "equipo": _resumen_equipo(db, vid),
        "generado": ahora_ar().strftime("%d/%m/%Y %H:%M"),
    }


# ─────────────────────────── cierre ───────────────────────────
def finalizar(db, v, usuario_id, contado_pendiente: dict | None = None) -> dict:
    """Cierra la campaña: guarda snapshot (con los números no entregados),
    devuelve al stock lo que tenía en mano y marca la fecha de fin. Commit."""
    informe = calcular_informe(db, v, contado_pendiente)
    en_mano = boletas_en_mano(db, v.id)
    devueltas, registros = devolver_a_stock(
        db, v.id, en_mano, usuario_id,
        "Cierre de campaña — vuelve a la institución")
    informe["devueltas_al_cierre"] = devueltas
    v.fecha_fin_campania = ahora_ar()
    v.cierre_snapshot = json.dumps(informe, ensure_ascii=False, default=str)
    db.commit()
    return {"devueltas": devueltas, "registros": registros}


def reabrir(db, v) -> None:
    """Deshace el cierre (las boletas devueltas NO vuelven solas: se entregan
    de nuevo con Entregar a Caja). Commit."""
    v.fecha_fin_campania = None
    v.cierre_snapshot = None
    db.commit()


def snapshot(v) -> dict | None:
    if not v.cierre_snapshot:
        return None
    try:
        return json.loads(v.cierre_snapshot)
    except Exception:
        return None
