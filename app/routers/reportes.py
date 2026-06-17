from fastapi import HTTPException,  APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from sqlalchemy.orm import Session
from sqlalchemy import func
from .. import models, auth as auth_module
from ..templates_config import templates
from ..models import CondicionBoleta
from ..database import get_db

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

    # Top vendedores con desglose por talonera
    raw_v = db.query(
        models.Vendedor.id,
        models.Vendedor.nombre,
        models.Talonera.nombre.label("tal_nombre"),
        func.coalesce(func.sum(models.Talonera.multiplicador), 0).label("cant")
    ).outerjoin(
        models.Boleta,
        (models.Boleta.vendedor_id == models.Vendedor.id) &
        (models.Boleta.comprador_id.isnot(None))
    ).outerjoin(
        models.Talonera, models.Boleta.talonera_id == models.Talonera.id
    ).group_by(
        models.Vendedor.id, models.Vendedor.nombre, models.Talonera.nombre
    ).order_by(models.Vendedor.nombre, models.Talonera.nombre).all()

    v_map = {}
    for row in raw_v:
        if row.id not in v_map:
            v_map[row.id] = {"id": row.id, "nombre": row.nombre, "cantidad": 0, "taloneras": []}
        if row.tal_nombre:
            v_map[row.id]["taloneras"].append({
                "nombre": row.tal_nombre,
                "cantidad": int(row.cant or 0)
            })
            v_map[row.id]["cantidad"] += int(row.cant or 0)

    top_vendedores = sorted(v_map.values(), key=lambda x: x["cantidad"], reverse=True)[:10]

    # Top cobradores con desglose por talonera
    raw_c = db.query(
        models.Cobrador.id,
        models.Cobrador.nombre,
        models.Talonera.nombre.label("tal_nombre"),
        func.coalesce(func.sum(models.Talonera.multiplicador), 0).label("cant")
    ).outerjoin(
        models.Boleta,
        (models.Boleta.cobrador_id == models.Cobrador.id) &
        (models.Boleta.comprador_id.isnot(None))
    ).outerjoin(
        models.Talonera, models.Boleta.talonera_id == models.Talonera.id
    ).group_by(
        models.Cobrador.id, models.Cobrador.nombre, models.Talonera.nombre
    ).order_by(models.Cobrador.nombre, models.Talonera.nombre).all()

    c_map = {}
    for row in raw_c:
        if row.id not in c_map:
            c_map[row.id] = {"id": row.id, "nombre": row.nombre, "cantidad": 0, "taloneras": []}
        if row.tal_nombre:
            c_map[row.id]["taloneras"].append({
                "nombre": row.tal_nombre,
                "cantidad": int(row.cant or 0)
            })
            c_map[row.id]["cantidad"] += int(row.cant or 0)

    top_cobradores = sorted(c_map.values(), key=lambda x: x["cantidad"], reverse=True)[:10]

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
        "top_vendedores": top_vendedores,
        "top_cobradores": top_cobradores,
        "totales": totales,
        "zonas_trabajadas": zonas_trabajadas})
