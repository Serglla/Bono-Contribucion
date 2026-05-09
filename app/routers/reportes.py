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
            grupos[key] = {"nombre": t.nombre, "num_series": t.num_series, "ids": []}
        grupos[key]["ids"].append(t.id)

    stats_por_talonera = []
    for key, g in grupos.items():
        ids = g["ids"]
        factor = max(1, (g["num_series"] or 3) // 3)
        total = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids)
        ).scalar()
        # Vendidas — MISMO criterio que Top Vendedores y tarjetas (08/05/2026):
        # boletas cargadas con socio (comprador_id IS NOT NULL),
        # sin filtrar por condicion. Incluye VENDIDO + CAJA-con-socio +
        # EN_COBRANZA + BAJA, porque todas siguen siendo "vendidas".
        vendidas = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.comprador_id.isnot(None)
        ).scalar()
        # Baja y En Cobranza — sub-estados informativos DENTRO de vendidas
        baja = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.condicion == CondicionBoleta.BAJA
        ).scalar()
        en_cobranza = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.condicion == CondicionBoleta.EN_COBRANZA
        ).scalar()
        # En Caja — solo boletas en CAJA sin comprador asignado (físicamente
        # con el vendedor, aún no cargadas como vendidas). Las CAJA-con-socio
        # ya están contadas dentro de "vendidas".
        en_caja = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.condicion == CondicionBoleta.CAJA,
            models.Boleta.comprador_id.is_(None)
        ).scalar()
        # Sin Vender — consulta directa por condicion (no por resta) para
        # evitar errores con el overlap de baja/en_cobranza dentro de vendidas
        sin_vender = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.condicion == CondicionBoleta.SIN_VENDER
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
            "sin_vender": sin_vender,
            "cuotas_cobradas": cuotas_cobradas,
            "total_ponderado": total * factor,
            "vendidas_ponderado": vendidas * factor,
            "baja_ponderado": baja * factor,
        })

    # Top vendedores — cuenta boletas cargadas con socio (comprador_id != NULL),
    # sin importar la condicion posterior (VENDIDO, CAJA, EN_COBRANZA, BAJA).
    # Se cuenta via Boleta.vendedor_id (seteado al cargar el comprador).
    # Ponderado por Talonera.multiplicador: PATA 1 x1, PATA 2 x2, PATA 3 x3, etc.
    top_vendedores = db.query(
        models.Vendedor.nombre,
        func.coalesce(func.sum(models.Talonera.multiplicador), 0).label("cantidad")
    ).outerjoin(models.Boleta,
        (models.Boleta.vendedor_id == models.Vendedor.id) &
        (models.Boleta.comprador_id.isnot(None))
    ).outerjoin(models.Talonera, models.Boleta.talonera_id == models.Talonera.id
    ).group_by(models.Vendedor.id, models.Vendedor.nombre).order_by(
        func.coalesce(func.sum(models.Talonera.multiplicador), 0).desc()
    ).limit(10).all()

    # Top cobradores — mismo criterio que top vendedores:
    # ponderado por Talonera.multiplicador, sobre boletas cargadas con socio
    # (Boleta.comprador_id IS NOT NULL), sin filtrar por condicion.
    top_cobradores = db.query(
        models.Cobrador.nombre,
        func.coalesce(func.sum(models.Talonera.multiplicador), 0).label("cantidad")
    ).outerjoin(models.Boleta,
        (models.Boleta.cobrador_id == models.Cobrador.id) &
        (models.Boleta.comprador_id.isnot(None))
    ).outerjoin(models.Talonera, models.Boleta.talonera_id == models.Talonera.id
    ).group_by(models.Cobrador.id, models.Cobrador.nombre).order_by(
        func.coalesce(func.sum(models.Talonera.multiplicador), 0).desc()
    ).limit(10).all()

    # Tarjetas del dashboard — todas con el mismo patrón:
    # SUM(Talonera.multiplicador) con el filtro correspondiente.
    # PATA 1 x1, PATA 2 x2, PATA 3 x3, etc.
    def _sum_mult(filtro=None):
        q = db.query(
            func.coalesce(func.sum(models.Talonera.multiplicador), 0)
        ).select_from(models.Boleta).join(
            models.Talonera, models.Talonera.id == models.Boleta.talonera_id
        )
        if filtro is not None:
            q = q.filter(filtro)
        return q.scalar() or 0

    totales = {
        # Inventario total (todas las boletas creadas, sin filtrar)
        "boletas": _sum_mult(),
        # Taloneras vendidas — boletas cargadas con socio, sin filtrar por condicion
        # (cuadra con Top Vendedores y con la columna "Taloneras" por zona)
        "vendidas": _sum_mult(models.Boleta.comprador_id.isnot(None)),
        # Baja — solo boletas con condicion BAJA, ponderadas
        "baja": _sum_mult(models.Boleta.condicion == CondicionBoleta.BAJA),
        # Compradores — cantidad de personas únicas (no se pondera)
        "compradores": db.query(func.count(models.Comprador.id)).scalar(),
    }

    # Stats por zona
    # Ponderado por Talonera.multiplicador (PATA 1 x1, PATA 2 x2, PATA 3 x3, etc.)
    # Compradores queda como conteo de personas (no se pondera).
    zonas = db.query(models.Zona).order_by(models.Zona.nombre).all()
    stats_por_zona = []
    for z in zonas:
        compradores_zona = db.query(func.count(models.Comprador.id)).filter(
            models.Comprador.zona_id == z.id
        ).scalar()
        # Taloneras de la zona — MISMO criterio que Top Vendedores:
        # boletas cargadas con socio (comprador_id IS NOT NULL),
        # sin filtrar por condicion, ponderadas por Talonera.multiplicador.
        # Permite cuadrar la suma por zona con el total del vendedor.
        taloneras_zona = db.query(
            func.coalesce(func.sum(models.Talonera.multiplicador), 0)
        ).select_from(models.Boleta).join(
            models.Comprador, models.Comprador.id == models.Boleta.comprador_id
        ).join(
            models.Talonera, models.Talonera.id == models.Boleta.talonera_id
        ).filter(
            models.Comprador.zona_id == z.id
        ).scalar() or 0
        # Vendidas por zona — MISMO criterio que Top Vendedores y stats_por_talonera:
        # toda boleta cargada con socio (comprador_id IS NOT NULL),
        # sin filtrar por condicion. Como el join ya pasa por Comprador,
        # comprador_id está garantizado != NULL, así que basta con la zona.
        vendidas_zona = db.query(
            func.coalesce(func.sum(models.Talonera.multiplicador), 0)
        ).select_from(models.Boleta).join(
            models.Comprador, models.Comprador.id == models.Boleta.comprador_id
        ).join(
            models.Talonera, models.Talonera.id == models.Boleta.talonera_id
        ).filter(
            models.Comprador.zona_id == z.id
        ).scalar() or 0
        baja_zona = db.query(
            func.coalesce(func.sum(models.Talonera.multiplicador), 0)
        ).select_from(models.Boleta).join(
            models.Comprador, models.Comprador.id == models.Boleta.comprador_id
        ).join(
            models.Talonera, models.Talonera.id == models.Boleta.talonera_id
        ).filter(
            models.Comprador.zona_id == z.id,
            models.Boleta.condicion == CondicionBoleta.BAJA
        ).scalar() or 0
        en_cobranza_zona = db.query(
            func.coalesce(func.sum(models.Talonera.multiplicador), 0)
        ).select_from(models.Boleta).join(
            models.Comprador, models.Comprador.id == models.Boleta.comprador_id
        ).join(
            models.Talonera, models.Talonera.id == models.Boleta.talonera_id
        ).filter(
            models.Comprador.zona_id == z.id,
            models.Boleta.condicion == CondicionBoleta.EN_COBRANZA
        ).scalar() or 0
        # SIN_VENDER no tiene comprador, así que se atribuye vía el vendedor
        # de la zona (Zona.vendedor_id). OJO: si un vendedor tiene múltiples
        # zonas, su stock SIN_VENDER aparece en cada zona — el stock no está
        # pre-asignado a ninguna zona específica, es del vendedor.
        if z.vendedor_id:
            sin_vender_zona = db.query(
                func.coalesce(func.sum(models.Talonera.multiplicador), 0)
            ).select_from(models.Boleta).join(
                models.Talonera, models.Talonera.id == models.Boleta.talonera_id
            ).filter(
                models.Boleta.vendedor_id == z.vendedor_id,
                models.Boleta.condicion == CondicionBoleta.SIN_VENDER
            ).scalar() or 0
        else:
            sin_vender_zona = 0
        stats_por_zona.append({
            "zona": z,
            "taloneras": taloneras_zona,
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
