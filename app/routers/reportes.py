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
            grupos[key] = {"nombre": t.nombre, "num_series": t.num_series, "multiplicador": t.multiplicador, "ids": []}
        grupos[key]["ids"].append(t.id)

    stats_por_talonera = []
    for key, g in grupos.items():
        ids = g["ids"]
        factor = g["multiplicador"] or max(1, (g["num_series"] or 3) // 3)
        total = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids)
        ).scalar()
        vendidas = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.condicion == CondicionBoleta.VENDIDO
        ).scalar()
        baja = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.condicion == CondicionBoleta.BAJA
        ).scalar()
        en_caja = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.condicion == CondicionBoleta.CAJA
        ).scalar()
        en_cobranza = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.condicion == CondicionBoleta.EN_COBRANZA
        ).scalar()
        cuotas_cobradas = db.query(func.sum(models.Boleta.cuotas_pagadas)).filter(
            models.Boleta.talonera_id.in_(ids)
        ).scalar() or 0
        stats_por_talonera.append({
            "nombre": g["nombre"],
            "factor": factor,
            "total": total,
            "vendidas": vendidas,
            "baja": baja,
            "en_caja": en_caja,
            "en_cobranza": en_cobranza,
            "sin_vender": total - vendidas - baja - en_caja - en_cobranza,
            "cuotas_cobradas": cuotas_cobradas,
            "total_ponderado": total * factor,
            "vendidas_ponderado": vendidas * factor,
        })

    # Top vendedores — solo VENDIDO, ponderado por multiplicador de talonera
    # PATA 1 = 1, PATA 2 = 2, PATA 3 = 3 (equivalentes en PATA 1)
    top_vendedores = db.query(
        models.Vendedor.nombre,
        func.coalesce(func.sum(models.Talonera.multiplicador), 0).label("cantidad")
    ).join(models.Boleta, models.Vendedor.id == models.Boleta.vendedor_id, isouter=True
    ).join(models.Talonera, models.Boleta.talonera_id == models.Talonera.id, isouter=True
    ).filter(
        (models.Boleta.condicion == CondicionBoleta.VENDIDO) | (models.Boleta.id == None)
    ).group_by(models.Vendedor.nombre).order_by(
        func.coalesce(func.sum(models.Talonera.multiplicador), 0).desc()
    ).limit(10).all()

    # Top cobradores
    top_cobradores = db.query(
        models.Cobrador.nombre,
        func.count(models.Boleta.id).label("cantidad")
    ).join(models.Boleta, isouter=True).group_by(models.Cobrador.nombre).order_by(
        func.count(models.Boleta.id).desc()
    ).limit(10).all()

    totales = {
        "compradores": db.query(func.count(models.Comprador.id)).scalar(),
        "boletas": sum(s["total_ponderado"] for s in stats_por_talonera),
        "vendidas": sum(s["vendidas_ponderado"] for s in stats_por_talonera),
        "baja": sum(s["baja"] for s in stats_por_talonera),
    }

    # Stats por zona
    zonas = db.query(models.Zona).order_by(models.Zona.nombre).all()
    stats_por_zona = []
    for z in zonas:
        compradores_zona = db.query(func.count(models.Comprador.id)).filter(
            models.Comprador.zona_id == z.id
        ).scalar()
        vendidas_zona = db.query(func.count(models.Boleta.id)).join(
            models.Comprador, models.Comprador.id == models.Boleta.comprador_id
        ).filter(
            models.Comprador.zona_id == z.id,
            models.Boleta.condicion == CondicionBoleta.VENDIDO
        ).scalar()
        baja_zona = db.query(func.count(models.Boleta.id)).join(
            models.Comprador, models.Comprador.id == models.Boleta.comprador_id
        ).filter(
            models.Comprador.zona_id == z.id,
            models.Boleta.condicion == CondicionBoleta.BAJA
        ).scalar()
        en_cobranza_zona = db.query(func.count(models.Boleta.id)).join(
            models.Comprador, models.Comprador.id == models.Boleta.comprador_id
        ).filter(
            models.Comprador.zona_id == z.id,
            models.Boleta.condicion == CondicionBoleta.EN_COBRANZA
        ).scalar()
        sin_vender_zona = db.query(func.count(models.Boleta.id)).join(
            models.Comprador, models.Comprador.id == models.Boleta.comprador_id
        ).filter(
            models.Comprador.zona_id == z.id,
            models.Boleta.condicion == CondicionBoleta.SIN_VENDER
        ).scalar()
        stats_por_zona.append({
            "zona": z,
            "compradores": compradores_zona,
            "vendidas": vendidas_zona,
            "baja": baja_zona,
            "en_cobranza": en_cobranza_zona,
            "sin_vender": sin_vender_zona,
            "vendedor": z.vendedor.nombre if z.vendedor else "—",
            "cobrador": z.cobrador.nombre if z.cobrador else "—",
        })

    return templates.TemplateResponse(request, "reportes.html", {"user": user,
        "stats_por_talonera": stats_por_talonera,
        "top_vendedores": top_vendedores,
        "top_cobradores": top_cobradores,
        "totales": totales,
        "stats_por_zona": stats_por_zona})
