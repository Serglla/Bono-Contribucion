from fastapi import HTTPException,  APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from .. import models, auth as auth_module
from ..templates_config import templates
from ..models import CondicionBoleta
from ..database import get_db
from .vendedores import _stats_bulk

router = APIRouter(prefix="/reportes", tags=["reportes"])



@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'reportes', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')

    taloneras = db.query(models.Talonera).order_by(models.Talonera.nombre).all()

    # Agrupar taloneras por nombre (ej: todas las "PATA 1" en una sola fila)
    grupos: dict = {}
    for t in taloneras:
        key = t.nombre
        if key not in grupos:
            grupos[key] = {
                "nombre": t.nombre,
                "num_series": t.num_series,
                "multiplicador": t.multiplicador,
                "tipo": (t.tipo or "COMUN"),
                "ids": [],
            }
        grupos[key]["ids"].append(t.id)

    stats_por_talonera = []
    for key, g in grupos.items():
        ids = g["ids"]
        factor = float(g["multiplicador"] or 1.0)   # ponderación real por PATA (PATA 0=0.67, 4=4, 6=6...)
        tipo = g["tipo"]

        if tipo == "CONTADO":
            total_entregados = db.query(
                func.coalesce(
                    func.sum(models.EntregaCaja.hasta - models.EntregaCaja.desde + 1),
                    0,
                )
            ).filter(
                func.lower(func.trim(models.EntregaCaja.talonera_nombre)) == (g["nombre"] or "").strip().lower(),
                func.coalesce(models.EntregaCaja.tipo, "ENTREGA") != "RETIRO",
            ).scalar() or 0
            # Asignados a boletas — slot 1 (numero_especial)
            asignados_1 = db.query(func.count(models.Boleta.id)).filter(
                models.Boleta.numero_especial.isnot(None),
                models.Boleta.talonera_especial_id.in_(ids),
            ).scalar() or 0
            # Asignados a boletas — slot 2 (numero_especial_2)
            asignados_2 = db.query(func.count(models.Boleta.id)).filter(
                models.Boleta.numero_especial_2.isnot(None),
                models.Boleta.talonera_especial_2_id.in_(ids),
            ).scalar() or 0
            # Asignados CON socio cargado — slot 1
            vendidas_1 = db.query(func.count(models.Boleta.id)).filter(
                models.Boleta.numero_especial.isnot(None),
                models.Boleta.talonera_especial_id.in_(ids),
                models.Boleta.comprador_id.isnot(None),
            ).scalar() or 0
            vendidas_2 = db.query(func.count(models.Boleta.id)).filter(
                models.Boleta.numero_especial_2.isnot(None),
                models.Boleta.talonera_especial_2_id.in_(ids),
                models.Boleta.comprador_id.isnot(None),
            ).scalar() or 0
            asignados = int(asignados_1) + int(asignados_2)
            vendidas  = int(vendidas_1) + int(vendidas_2)
            total     = int(total_entregados)
            en_caja   = max(0, total - asignados)
            stats_por_talonera.append({
                "nombre": g["nombre"],
                "tipo": tipo,
                "factor": factor,
                "total": total,
                "vendidas": vendidas,
                "vendidas_1": int(vendidas_1),
                "vendidas_2": int(vendidas_2),
                "baja": 0,
                "en_caja": en_caja,
                "en_cobranza": 0,
                "sin_vender": 0,
                "cuotas_cobradas": 0,
                "total_ponderado": total,
                "vendidas_ponderado": vendidas,
                "baja_ponderado": 0,
                "contado_1": 0,
                "contado_2": 0,
            })
            continue

        # Caso COMUN — lógica original
        total = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids)
        ).scalar()
        vendidas = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.comprador_id.isnot(None)
        ).scalar()
        baja = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            or_(models.Boleta.condicion == CondicionBoleta.BAJA,
                models.Boleta.mes_baja.isnot(None))
        ).scalar()
        en_cobranza = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.condicion == CondicionBoleta.EN_COBRANZA
        ).scalar()
        en_caja = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.condicion == CondicionBoleta.CAJA,
            models.Boleta.comprador_id.is_(None)
        ).scalar()
        sin_vender = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.condicion == CondicionBoleta.SIN_VENDER
        ).scalar()
        cuotas_cobradas = db.query(func.sum(models.Boleta.cuotas_pagadas)).filter(
            models.Boleta.talonera_id.in_(ids)
        ).scalar() or 0
        # Desglose contado: boletas de esta talonera con número contado asignado
        contado_1 = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.numero_especial.isnot(None),
        ).scalar() or 0
        contado_2 = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.numero_especial_2.isnot(None),
        ).scalar() or 0
        stats_por_talonera.append({
            "nombre": g["nombre"],
            "tipo": tipo,
            "factor": factor,
            "total": total,
            "vendidas": vendidas,
            "vendidas_1": 0,
            "vendidas_2": 0,
            "baja": baja,
            "en_caja": en_caja,
            "en_cobranza": en_cobranza,
            "sin_vender": sin_vender,
            "cuotas_cobradas": cuotas_cobradas,
            "total_ponderado": int(round(total * factor)),
            "vendidas_ponderado": int(round(vendidas * factor)),
            "baja_ponderado": int(round(baja * factor)),
            "contado_1": int(contado_1),
            "contado_2": int(contado_2),
        })

    # ── Liquidado por vendedor ─────────────────────────────────────────────
    # Reusa la misma lógica que la tabla de Vendedores: ponderado por PATA
    # (PATA 1 ×1, PATA 2 ×2, PATA 0 ×0.67) y SIN contar los números de sorteo
    # extra (pool CONTADO / CONTADO 2 VECES) como ventas.
    _stats = _stats_bulk(db)
    vendedores_liq = []
    for v in db.query(models.Vendedor).order_by(models.Vendedor.nombre).all():
        s = _stats.get(v.id, {})
        vendedores_liq.append({
            "nombre": v.nombre,
            "cuotas": int(s.get("liquidados_cuotas", 0)),
            "contados": int(s.get("liquidados_contados", 0)),
            "total": int(s.get("liquidados", 0)),
        })
    # Ordenar por total liquidado (descendente), los que tienen 0 al final
    vendedores_liq.sort(key=lambda x: x["total"], reverse=True)
    vendedores_liq_total = {
        "cuotas":   sum(v["cuotas"]   for v in vendedores_liq),
        "contados": sum(v["contados"] for v in vendedores_liq),
        "total":    sum(v["total"]    for v in vendedores_liq),
    }

    # ── Resumen de cobradores ──────────────────────────────────────────────
    # MISMO criterio que el módulo Cobranza (/cobranza/), para que coincida con las
    # tarjetas por cobrador:
    #   - "A cobrar" = cuotas de boletas YA emplanilladas (planillas entregadas) y no
    #     terminadas, ponderadas por PATA. Son las que están por cobrar / se liquidan.
    #   - "Del mes"  = cuotas pendientes de emplanillar (todavía sin planilla),
    #     ponderadas por PATA. Excluye contado (numero_especial_2).
    #   - "% de cobrado" = progreso de cobro de lo emplanillado (pagadas/pactadas).
    #     Si no se cobró nada más allá de las anticipadas → "Sin iniciar".
    def _pata_valor_b(b):
        # Ponderación por PATA = multiplicador real (PATA 0=0.67, 1=1, 2=2, 4=4, 6=6...).
        if b.talonera and b.talonera.multiplicador:
            return float(b.talonera.multiplicador)
        return 1.0

    planillas_por_cob = dict(
        db.query(
            models.Planilla.cobrador_id,
            func.count(models.Planilla.id),
        ).group_by(models.Planilla.cobrador_id).all()
    )

    _cob_acc: dict = {}
    boletas_con_cob = db.query(models.Boleta).filter(
        models.Boleta.cobrador_id.isnot(None)
    ).all()
    for b in boletas_con_cob:
        cid = b.cobrador_id
        acc = _cob_acc.setdefault(cid, {
            "a_cobrar": 0, "del_mes": 0, "bajas": 0,
            "pactadas": 0, "pagadas": 0, "anticipadas": 0,
        })
        if b.condicion == CondicionBoleta.BAJA or b.mes_baja:
            acc["bajas"] += 1
            continue
        no_terminada = (b.cuotas_pagadas or 0) < (b.cuotas_pactadas or 0)
        pv = _pata_valor_b(b)
        if b.planilla_id is not None:
            if no_terminada:
                acc["a_cobrar"] += pv          # emplanillada (planilla entregada)
            acc["pactadas"]    += (b.cuotas_pactadas or 0)
            acc["pagadas"]     += (b.cuotas_pagadas or 0)
            acc["anticipadas"] += (b.cuotas_anticipadas or 0)
        elif no_terminada and b.numero_especial_2 is None:
            acc["del_mes"] += pv               # pendiente de emplanillar

    cobradores_resumen = []
    _g_pac = _g_pag = _g_ant = 0
    for c in db.query(models.Cobrador).order_by(models.Cobrador.nombre).all():
        acc = _cob_acc.get(c.id, {"a_cobrar": 0, "del_mes": 0, "bajas": 0,
                                  "pactadas": 0, "pagadas": 0, "anticipadas": 0})
        pac = acc["pactadas"]; pag = acc["pagadas"]; ant = acc["anticipadas"]
        _g_pac += pac; _g_pag += pag; _g_ant += ant
        iniciada = pac > 0 and (pag - ant) > 0
        pct = round(pag / pac * 100, 1) if (iniciada and pac > 0) else None
        cobradores_resumen.append({
            "nombre": c.nombre,
            "planillas": int(planillas_por_cob.get(c.id, 0) or 0),
            "a_cobrar": int(round(acc["a_cobrar"])),
            "del_mes": int(round(acc["del_mes"])),
            "pct": pct,
            "bajas": int(acc["bajas"]),
        })
    cobradores_resumen.sort(key=lambda x: (x["a_cobrar"], x["del_mes"]), reverse=True)

    _ini_tot = _g_pac > 0 and (_g_pag - _g_ant) > 0
    cobradores_total = {
        "planillas": sum(c["planillas"] for c in cobradores_resumen),
        "a_cobrar":  sum(c["a_cobrar"] for c in cobradores_resumen),
        "del_mes":   sum(c["del_mes"]  for c in cobradores_resumen),
        "pct":       round(_g_pag / _g_pac * 100, 1) if (_ini_tot and _g_pac > 0) else None,
        "bajas":     sum(c["bajas"] for c in cobradores_resumen),
    }

    # Tarjetas del dashboard — ponderadas por Talonera.multiplicador
    def _sum_mult(filtro=None):
        q = db.query(
            func.coalesce(func.sum(models.Talonera.multiplicador), 0)
        ).select_from(models.Boleta).join(
            models.Talonera, models.Talonera.id == models.Boleta.talonera_id
        )
        if filtro is not None:
            q = q.filter(filtro)
        return q.scalar() or 0

    # "al contado" = tiene numero_especial asignado  O  pagó todas las cuotas por adelantado
    # (equivalente a la lógica `es_contado or anticipo_total` de compradores.html)
    _anticipo_total = (
        (models.Boleta.cuotas_pactadas.isnot(None)) &
        (models.Boleta.cuotas_pactadas > 0) &
        (models.Boleta.cuotas_anticipadas.isnot(None)) &
        (models.Boleta.cuotas_anticipadas >= models.Boleta.cuotas_pactadas)
    )
    _es_contado = (models.Boleta.numero_especial.isnot(None)) | _anticipo_total

    totales = {
        # Taloneras vendidas — toda boleta con comprador asignado
        "vendidas": _sum_mult(models.Boleta.comprador_id.isnot(None)),
        # Vendidas en cuotas — tienen comprador, no son contado ni anticipo total
        "cuotas": _sum_mult(
            (models.Boleta.comprador_id.isnot(None)) &
            ~_es_contado
        ),
        # Al contado — tienen número especial Y/O pagaron todas las cuotas anticipadas
        "contado": _sum_mult(_es_contado),
        # Baja — boletas con condición BAJA (Socios) o marcadas de baja en cobranza
        "baja": _sum_mult(or_(models.Boleta.condicion == CondicionBoleta.BAJA,
                              models.Boleta.mes_baja.isnot(None))),
        # Liquidados por el vendedor pero SIN socio cargado todavía (pendientes de
        # cargar el comprador). Ponderado por PATA, igual que vendidas.
        "sin_cargar": _sum_mult(
            (models.Boleta.liquidacion_vendedor_id.isnot(None)) &
            (models.Boleta.comprador_id.is_(None))
        ),
        # Compradores — personas únicas (no se pondera)
        "compradores": db.query(func.count(models.Comprador.id)).scalar(),
    }

    # Zonas trabajadas = cantidad de zonas distintas donde hay números vendidos
    # (boletas con comprador asignado). Una sola consulta, en vez del detalle por zona.
    zonas_trabajadas = db.query(
        func.count(func.distinct(models.Comprador.zona_id))
    ).select_from(models.Boleta).join(
        models.Comprador, models.Comprador.id == models.Boleta.comprador_id
    ).filter(
        models.Comprador.zona_id.isnot(None)
    ).scalar() or 0

    return templates.TemplateResponse(request, "reportes.html", {"user": user,
        "stats_por_talonera": stats_por_talonera,
        "vendedores_liq": vendedores_liq,
        "vendedores_liq_total": vendedores_liq_total,
        "cobradores_resumen": cobradores_resumen,
        "cobradores_total": cobradores_total,
        "totales": totales,
        "zonas_trabajadas": zonas_trabajadas})


@router.get("/sin-cargar-lista", response_class=JSONResponse)
async def sin_cargar_lista(request: Request, db: Session = Depends(get_db)):
    """Lista de boletas liquidadas por el vendedor pero SIN socio cargado todavia.
    Devuelve items {vendedor, talonera, numero_fmt, fecha_liq} ordenados por
    vendedor/talonera/numero. fecha_liq = fecha en que se liquido la boleta."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'reportes', 'ver'):
        raise HTTPException(403, 'Sin permiso')
    rows = db.query(
        models.Vendedor.nombre,
        models.Talonera.nombre,
        models.Talonera.num_digitos,
        models.Boleta.numero_principal,
        models.LiquidacionVendedor.fecha,
    ).select_from(models.Boleta).join(
        models.Talonera, models.Talonera.id == models.Boleta.talonera_id
    ).outerjoin(
        models.Vendedor, models.Vendedor.id == models.Boleta.vendedor_id
    ).outerjoin(
        models.LiquidacionVendedor,
        models.LiquidacionVendedor.id == models.Boleta.liquidacion_vendedor_id
    ).filter(
        models.Boleta.liquidacion_vendedor_id.isnot(None),
        models.Boleta.comprador_id.is_(None),
    ).order_by(
        models.Vendedor.nombre, models.Talonera.nombre, models.Boleta.numero_principal
    ).all()
    items = []
    for vn, tn, nd, num, fl in rows:
        items.append({
            "vendedor": vn or "(sin vendedor)",
            "talonera": (tn or "").replace("PATA ", "X"),
            "numero_fmt": str(num).zfill(nd or 4),
            "fecha_liq": fl.strftime("%d/%m/%Y") if fl else "",
        })
    return JSONResponse({"items": items, "total": len(items)})
