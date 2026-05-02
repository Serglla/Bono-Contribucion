from fastapi import APIRouter, Depends, Request, Query, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date
from typing import List, Optional
from .. import models, auth as auth_module
from ..templates_config import templates
from ..models import CondicionBoleta
from ..database import get_db

router = APIRouter(prefix="/cobranza", tags=["cobranza"])

MESES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
         "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]


def _pata_valor(boleta) -> int:
    """Retorna el valor de PATA de una boleta (1, 2 o 3) según el nombre de la talonera."""
    if boleta.talonera:
        nombre = boleta.talonera.nombre.upper()
        for n in (3, 2, 1):
            if str(n) in nombre:
                return n
    return 1


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db),
                mes: int = Query(default=0), anio: int = Query(default=0)):
    user = await auth_module.require_user(request, db)

    hoy = date.today()
    if not mes:  mes  = hoy.month
    if not anio: anio = hoy.year

    cobradores = db.query(models.Cobrador).order_by(models.Cobrador.nombre).all()

    resumen = []
    for co in cobradores:
        base_filter = [
            models.Boleta.cobrador_id == co.id,
            models.Boleta.condicion.in_([CondicionBoleta.VENDIDO, CondicionBoleta.EN_COBRANZA]),
        ]
        total = (db.query(func.count(models.Boleta.id))
                 .filter(*base_filter)
                 .scalar() or 0)
        boletas_pendientes = (db.query(models.Boleta)
                              .filter(*base_filter,
                                      models.Boleta.cuotas_pagadas < models.Boleta.cuotas_pactadas)
                              .all())
        pendientes = sum(_pata_valor(b) for b in boletas_pendientes)
        planilla = (db.query(models.Planilla)
                    .filter_by(cobrador_id=co.id, mes=mes, anio=anio)
                    .first())
        resumen.append({"cobrador": co, "total": total, "pendientes": pendientes, "planilla": planilla})

    return templates.TemplateResponse(request, "cobranza.html", {
        "user": user,
        "resumen": resumen,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
        "meses": MESES,
        "anios": list(range(hoy.year - 1, hoy.year + 2)),
    })


@router.post("/{cobrador_id}/planilla/armar")
async def armar_planilla(request: Request, cobrador_id: int,
                         mes: int = Form(...), anio: int = Form(...),
                         comision_pct: float = Form(10.0),
                         db: Session = Depends(get_db)):
    await auth_module.require_user(request, db)

    cobrador = db.query(models.Cobrador).get(cobrador_id)
    if not cobrador:
        return RedirectResponse("/cobranza/emplantillado", status_code=302)

    # Si ya existe planilla para este cobrador+mes+anio, solo redirigir
    existente = db.query(models.Planilla).filter_by(cobrador_id=cobrador_id, mes=mes, anio=anio).first()
    if not existente:
        siguiente_numero = (db.query(func.count(models.Planilla.id))
                            .filter_by(cobrador_id=cobrador_id)
                            .scalar() or 0) + 1
        planilla = models.Planilla(
            cobrador_id=cobrador_id,
            numero=siguiente_numero,
            mes=mes,
            anio=anio,
            comision_pct=comision_pct,
        )
        db.add(planilla)
        db.flush()

        # Asignar boletas sin planilla de este cobrador a la nueva planilla
        (db.query(models.Boleta)
           .filter(models.Boleta.cobrador_id == cobrador_id,
                   models.Boleta.planilla_id.is_(None),
                   models.Boleta.condicion.in_([CondicionBoleta.VENDIDO, CondicionBoleta.EN_COBRANZA]))
           .update({"planilla_id": planilla.id}, synchronize_session=False))
        db.commit()

    return RedirectResponse(f"/cobranza/emplantillado?mes={mes}&anio={anio}", status_code=302)


# ── EMPLANTILLADO ──────────────────────────────────────────────────────────────

@router.get("/emplantillado", response_class=HTMLResponse)
async def emplantillado(request: Request, db: Session = Depends(get_db),
                        mes: int = Query(default=0), anio: int = Query(default=0)):
    user = await auth_module.require_user(request, db)
    hoy = date.today()
    if not mes:  mes  = hoy.month
    if not anio: anio = hoy.year

    cobradores = db.query(models.Cobrador).filter_by(activo=True).order_by(models.Cobrador.nombre).all()
    resumen = []
    for co in cobradores:
        # Boletas activas de este cobrador
        activas = (db.query(models.Boleta)
                   .filter(models.Boleta.cobrador_id == co.id,
                           models.Boleta.condicion.in_([CondicionBoleta.VENDIDO, CondicionBoleta.EN_COBRANZA]))
                   .all())
        sin_planilla = [b for b in activas if b.planilla_id is None]
        planilla = db.query(models.Planilla).filter_by(cobrador_id=co.id, mes=mes, anio=anio).first()
        if activas:
            resumen.append({
                "cobrador": co,
                "total_activas": len(activas),
                "sin_planilla": len(sin_planilla),
                "planilla": planilla,
            })

    return templates.TemplateResponse(request, "cobranza_emplantillado.html", {
        "user": user,
        "resumen": resumen,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
        "meses": MESES,
        "anios": list(range(hoy.year - 1, hoy.year + 2)),
    })


# ── LIQUIDACIÓN ────────────────────────────────────────────────────────────────

@router.get("/liquidacion", response_class=HTMLResponse)
async def liquidacion_index(request: Request, db: Session = Depends(get_db),
                             mes: int = Query(default=0), anio: int = Query(default=0)):
    user = await auth_module.require_user(request, db)
    hoy = date.today()
    if not mes:  mes  = hoy.month
    if not anio: anio = hoy.year

    planillas = (db.query(models.Planilla)
                 .filter_by(mes=mes, anio=anio)
                 .join(models.Cobrador)
                 .order_by(models.Cobrador.nombre)
                 .all())

    return templates.TemplateResponse(request, "cobranza_liquidacion.html", {
        "user": user,
        "planillas": planillas,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
        "meses": MESES,
        "anios": list(range(hoy.year - 1, hoy.year + 2)),
    })


@router.get("/liquidacion/{planilla_id}", response_class=HTMLResponse)
async def liquidacion_detalle(request: Request, planilla_id: int,
                               db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    planilla = db.query(models.Planilla).get(planilla_id)
    if not planilla:
        raise HTTPException(404)

    boletas = (db.query(models.Boleta)
               .filter(models.Boleta.planilla_id == planilla_id)
               .join(models.Comprador, isouter=True)
               .order_by(models.Boleta.numero_principal)
               .all())

    # Detalles de liquidación ya existentes
    liq = planilla.liquidacion
    detalles_map = {}
    if liq:
        for d in liq.detalles:
            detalles_map[d.boleta_id] = d.cuotas_cobradas

    return templates.TemplateResponse(request, "cobranza_liquidacion_detalle.html", {
        "user": user,
        "planilla": planilla,
        "boletas": boletas,
        "detalles_map": detalles_map,
        "liquidacion": liq,
        "mes_nombre": MESES[planilla.mes - 1],
    })


@router.post("/liquidacion/{planilla_id}/guardar")
async def liquidacion_guardar(request: Request, planilla_id: int,
                               boleta_ids: List[int] = Form(...),
                               cuotas_cobradas: List[int] = Form(...),
                               db: Session = Depends(get_db)):
    await auth_module.require_user(request, db)
    planilla = db.query(models.Planilla).get(planilla_id)
    if not planilla:
        raise HTTPException(404)

    # Crear o actualizar liquidación
    liq = planilla.liquidacion
    if not liq:
        liq = models.Liquidacion(planilla_id=planilla_id, fecha=date.today())
        db.add(liq)
        db.flush()

    # Eliminar detalles anteriores y recrear
    db.query(models.LiquidacionDetalle).filter_by(liquidacion_id=liq.id).delete()

    total_cuotas = 0
    for bid, cobradas in zip(boleta_ids, cuotas_cobradas):
        if cobradas > 0:
            db.add(models.LiquidacionDetalle(
                liquidacion_id=liq.id,
                boleta_id=bid,
                cuotas_cobradas=cobradas,
            ))
            # Actualizar cuotas_pagadas en la boleta
            boleta = db.query(models.Boleta).get(bid)
            if boleta:
                boleta.cuotas_pagadas = min(
                    boleta.cuotas_anticipadas + cobradas,
                    boleta.cuotas_pactadas
                )
            total_cuotas += cobradas

    # Calcular totales (sin monto por ahora, se puede agregar precio por cuota después)
    liq.total_cuotas = total_cuotas
    liq.fecha = date.today()
    db.commit()

    return RedirectResponse(f"/cobranza/liquidacion/{planilla_id}", status_code=302)


@router.get("/{cobrador_id}/planilla", response_class=HTMLResponse)
async def planilla(request: Request, cobrador_id: int,
                   mes: int = Query(default=0), anio: int = Query(default=0),
                   db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)

    hoy = date.today()
    if not mes:  mes  = hoy.month
    if not anio: anio = hoy.year

    cobrador = db.query(models.Cobrador).get(cobrador_id)
    if not cobrador:
        return RedirectResponse("/cobranza/", status_code=302)

    planilla_obj = db.query(models.Planilla).filter_by(cobrador_id=cobrador_id, mes=mes, anio=anio).first()

    if planilla_obj:
        # Mostrar las boletas fijas de esta planilla
        boletas = (db.query(models.Boleta)
                   .filter(models.Boleta.planilla_id == planilla_obj.id)
                   .join(models.Comprador, isouter=True)
                   .order_by(models.Boleta.numero_principal)
                   .all())
    else:
        # Planilla no armada aún: vista previa con boletas actuales sin planilla
        boletas = (db.query(models.Boleta)
                   .filter(models.Boleta.cobrador_id == cobrador_id,
                           models.Boleta.planilla_id.is_(None),
                           models.Boleta.condicion.in_([CondicionBoleta.VENDIDO, CondicionBoleta.EN_COBRANZA]))
                   .join(models.Comprador, isouter=True)
                   .order_by(models.Boleta.numero_principal)
                   .all())

    # ── Helpers PATA ──
    def _get_pata(b):
        if b and b.talonera:
            nombre = b.talonera.nombre.upper()
            for n in ("1", "2", "3"):
                if n in nombre:
                    return n
        return "?"

    def _get_color(b):
        if b and b.talonera and b.talonera.color:
            c = b.talonera.color.strip()
            return c if c and c != "#ffffff" else "#cccccc"
        return "#cccccc"

    def _col_header(col):
        """Primer PATA de la columna (X1/X2/X3) o cadena vacía si está vacía."""
        for cell in col:
            if isinstance(cell, dict):
                return cell["pata"]
            elif cell:
                p = _get_pata(cell)
                if p != "?":
                    return f"X{p}"
        return ""

    # ── Agrupar boletas consecutivas por PATA ──
    ROWS_PER_COL = 40
    TOTAL = ROWS_PER_COL * 3
    grid = [None] * TOTAL

    pata_grupos = []
    cur_pata, cur_grupo = None, []
    for b in boletas:
        p = _get_pata(b)
        if p != cur_pata:
            if cur_grupo:
                pata_grupos.append(cur_grupo)
            cur_pata, cur_grupo = p, [b]
        else:
            cur_grupo.append(b)
    if cur_grupo:
        pata_grupos.append(cur_grupo)

    # ── Llenar grid: cada grupo arranca en un bloque de 10 con fila de etiqueta ──
    pos = 0
    for i, grupo in enumerate(pata_grupos):
        if i > 0:
            # Saltar al inicio del próximo bloque de 10
            new_block = pos if pos % 10 == 0 else ((pos // 10) + 1) * 10
            # Poner etiqueta en la ÚLTIMA fila del bloque anterior (si está libre)
            label_pos = new_block - 1
            if 0 <= label_pos < TOTAL and grid[label_pos] is None:
                grid[label_pos] = {
                    "type": "label",
                    "pata": f"X{_get_pata(grupo[0])}",
                    "color": _get_color(grupo[0]),
                }
            pos = new_block
        # Boletas del grupo
        for b in grupo:
            if pos < TOTAL:
                grid[pos] = b
                pos += 1

    c1 = grid[0:ROWS_PER_COL]
    c2 = grid[ROWS_PER_COL:ROWS_PER_COL * 2]
    c3 = grid[ROWS_PER_COL * 2:ROWS_PER_COL * 3]
    rows = [(c1[i], c2[i], c3[i]) for i in range(ROWS_PER_COL)]

    col1_label = _col_header(c1)
    col2_label = _col_header(c2)
    col3_label = _col_header(c3)

    # Cantidad de cuotas (máximo entre todas las boletas, mínimo 10)
    num_cuotas = max((b.cuotas_pactadas or 0) for b in boletas) if boletas else 10
    num_cuotas = max(num_cuotas, 10)
    cuota_nums = list(range(1, num_cuotas + 1))

    # Meses de la campaña (octubre = cuota 1)
    _meses_camp = [
        ("OCTUBRE", 10), ("NOVIEMBRE", 11), ("DICIEMBRE", 12),
        ("ENERO", 1),    ("FEBRERO", 2),    ("MARZO", 3),
        ("ABRIL", 4),    ("MAYO", 5),       ("JUNIO", 6),
        ("JULIO", 7),    ("AGOSTO", 8),     ("SEPTIEMBRE", 9),
    ]
    meses_campana = _meses_camp[:num_cuotas]

    planilla_label = f"P{planilla_obj.numero}" if planilla_obj else None

    return templates.TemplateResponse(request, "cobranza_planilla.html", {
        "user": user,
        "cobrador": cobrador,
        "planilla": planilla_obj,
        "planilla_label": planilla_label,
        "boletas": boletas,
        "rows": rows,
        "col1_label": col1_label,
        "col2_label": col2_label,
        "col3_label": col3_label,
        "num_cuotas": num_cuotas,
        "cuota_nums": cuota_nums,
        "meses_campana": meses_campana,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
    })
