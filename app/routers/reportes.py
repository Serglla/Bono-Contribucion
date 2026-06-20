from fastapi import HTTPException,  APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from sqlalchemy.orm import Session
from sqlalchemy import func
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
                "tipo": (t.tipo or "COMUN"),
                "ids": [],
            }
        grupos[key]["ids"].append(t.id)

    stats_por_talonera = []
    for key, g in grupos.items():
        ids = g["ids"]
        factor = max(1, (g["num_series"] or 3) // 3)
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
            models.Boleta.condicion == CondicionBoleta.BAJA
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
            "total_ponderado": total * factor,
            "vendidas_ponderado": vendidas * factor,
            "baja_ponderado": baja * factor,
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
    # Cantidad de planillas + cuotas cobradas + monto cobrado (de las planillas
    # ya liquidadas). Una planilla cuenta aunque todavía no esté liquidada.
    planillas_por_cob = dict(
        db.query(
            models.Planilla.cobrador_id,
            func.count(models.Planilla.id),
        ).group_by(models.Planilla.cobrador_id).all()
    )
    liq_por_cob = {
        row[0]: row
        for row in db.query(
            models.Planilla.cobrador_id,
            func.coalesce(func.sum(models.Liquidacion.total_cuotas), 0),
            func.coalesce(func.sum(models.Liquidacion.monto_total), 0.0),
            func.coalesce(func.sum(models.Liquidacion.neto), 0.0),
        ).join(
            models.Liquidacion, models.Liquidacion.planilla_id == models.Planilla.id
        ).group_by(models.Planilla.cobrador_id).all()
    }
    cobradores_resumen = []
    for c in db.query(models.Cobrador).order_by(models.Cobrador.nombre).all():
        lr = liq_por_cob.get(c.id)
        cobradores_resumen.append({
            "nombre": c.nombre,
            "planillas": int(planillas_por_cob.get(c.id, 0) or 0),
            "cuotas": int(lr[1]) if lr else 0,
            "monto": float(lr[2]) if lr else 0.0,
        })
    cobradores_resumen.sort(key=lambda x: x["cuotas"], reverse=True)
    cobradores_total = {
        "planillas": sum(c["planillas"] for c in cobradores_resumen),
        "cuotas":    sum(c["cuotas"]    for c in cobradores_resumen),
        "monto":     sum(c["monto"]     for c in cobradores_resumen),
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
        # Baja — solo boletas con condición BAJA
        "baja": _sum_mult(models.Boleta.condicion == CondicionBoleta.BAJA),
        # Compradores — personas únicas (no se pondera)
        "compradores": db.query(func.count(models.Comprador.id)).scalar(),
    }

    # Zonas trabajadas = cantidad de zonas distintas donde hay números vendidos
    # (boletas con comprador asignado). Una sola consulta, en vez del detalle  por zona.
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
