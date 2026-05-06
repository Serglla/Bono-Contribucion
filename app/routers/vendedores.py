from fastapi import HTTPException, APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from typing import Optional
from datetime import date as _date
from .. import models, auth as auth_module
from ..models import CondicionBoleta
from ..templates_config import templates
from ..database import get_db

router = APIRouter(prefix="/vendedores", tags=["vendedores"])


def _stats_bulk(db):
    """Un solo query SQL con conteos por vendedor y condicion."""
    rows = db.query(
        models.Boleta.vendedor_id,
        models.Boleta.condicion,
        func.count(models.Boleta.id)
    ).filter(
        models.Boleta.vendedor_id.isnot(None)
    ).group_by(models.Boleta.vendedor_id, models.Boleta.condicion).all()

    stats = {}
    for vid, cond, cnt in rows:
        if vid not in stats:
            stats[vid] = {"caja": 0, "baja": 0, "vendido": 0, "sin_vender": 0}
        if cond == CondicionBoleta.CAJA:
            stats[vid]["caja"] = cnt
        elif cond == CondicionBoleta.BAJA:
            stats[vid]["baja"] = cnt
        elif cond == CondicionBoleta.VENDIDO:
            stats[vid]["vendido"] = cnt
        elif cond == CondicionBoleta.SIN_VENDER:
            stats[vid]["sin_vender"] = cnt
    return stats


@router.get("/", response_class=HTMLResponse)
async def listar(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, "vendedores", "ver"):
        raise HTTPException(403, "Sin permiso")
    vendedores = db.query(models.Vendedor).order_by(
        models.Vendedor.es_jefe_equipo.desc(), models.Vendedor.nombre
    ).all()
    taloneras = db.query(models.Talonera).order_by(
        models.Talonera.nombre, models.Talonera.numero_inicio
    ).all()
    grupos_talonera = list(dict.fromkeys(t.nombre for t in taloneras))
    entregas = db.query(models.EntregaCaja).order_by(
        models.EntregaCaja.fecha.desc()
    ).limit(200).all()
    stats = _stats_bulk(db)
    jefe = db.query(models.Vendedor).filter_by(es_jefe_equipo=True, activo=True).first()
    return templates.TemplateResponse(request, "vendedores.html", {
        "user": user,
        "vendedores": vendedores,
        "grupos_talonera": grupos_talonera,
        "entregas": entregas,
        "stats": stats,
        "jefe": jefe,
    })


@router.get("/{vid}/detalle", response_class=HTMLResponse)
async def detalle(vid: int, request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, "vendedores", "ver"):
        raise HTTPException(403, "Sin permiso")
    v = db.query(models.Vendedor).get(vid)
    if not v:
        raise HTTPException(404, "Vendedor no encontrado")

    # Todas las boletas de este vendedor
    boletas = db.query(models.Boleta).filter_by(vendedor_id=vid).all()

    # Agrupar por pata (talonera.nombre) + condicion
    patas = {}  # { nombre_pata: { "color": "#..", "boletas": [{"num":n, "cond":..., "liq":bool}] } }
    for b in boletas:
        pata_nombre = b.talonera.nombre if b.talonera else "?"
        pata_color  = b.talonera.color  if b.talonera else "#ffffff"
        if pata_nombre not in patas:
            patas[pata_nombre] = {"color": pata_color, "boletas": []}
        patas[pata_nombre]["boletas"].append({
            "num": b.numero_principal,
            "cond": b.condicion.value if b.condicion else "?",
            "liq": b.liquidacion_vendedor_id is not None,
            "contado": b.numero_especial is not None,
        })

    # Ordenar boletas por numero
    for p in patas:
        patas[p]["boletas"].sort(key=lambda x: x["num"])

    # Calcular previsualizacion de liquidacion (VENDIDO sin liquidar)
    pendientes = [b for b in boletas
                  if b.condicion == CondicionBoleta.VENDIDO
                  and b.liquidacion_vendedor_id is None]

    liq_preview = {}  # { pata_nombre: { valor_cuota, cuotas, contados } }
    for b in pendientes:
        pata = b.talonera.nombre if b.talonera else "?"
        vc   = b.talonera.valor_cuota if b.talonera else 0.0
        if pata not in liq_preview:
            liq_preview[pata] = {"valor_cuota": vc, "cuotas": 0, "contados": 0}
        if b.numero_especial is not None:
            liq_preview[pata]["contados"] += 1
        else:
            liq_preview[pata]["cuotas"] += 1

    total_cuotas  = sum(p["cuotas"]   for p in liq_preview.values())
    total_contados = sum(p["contados"] for p in liq_preview.values())
    monto_cuotas  = sum(p["cuotas"]   * p["valor_cuota"] for p in liq_preview.values())
    monto_contados= sum(p["contados"] * p["valor_cuota"] for p in liq_preview.values())

    # Historial de liquidaciones anteriores
    liquidaciones = db.query(models.LiquidacionVendedor).filter_by(
        vendedor_id=vid
    ).order_by(models.LiquidacionVendedor.fecha.desc()).all()

    can_edit = auth_module.has_permission(user, "vendedores", "editar")

    return templates.TemplateResponse(request, "vendedor_detalle.html", {
        "user": user,
        "v": v,
        "patas": patas,
        "liq_preview": liq_preview,
        "total_cuotas": total_cuotas,
        "total_contados": total_contados,
        "monto_cuotas": monto_cuotas,
        "monto_contados": monto_contados,
        "liquidaciones": liquidaciones,
        "can_edit": can_edit,
    })


@router.post("/{vid}/liquidar")
async def liquidar(
    vid: int,
    request: Request,
    comision_cuotas_pct: float = Form(5.0),
    comision_contados_pct: float = Form(10.0),
    observacion: str = Form(""),
    db: Session = Depends(get_db),
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    v = db.query(models.Vendedor).get(vid)
    if not v:
        raise HTTPException(404)

    # Boletas VENDIDO sin liquidar para este vendedor
    pendientes = db.query(models.Boleta).filter(
        models.Boleta.vendedor_id == vid,
        models.Boleta.condicion == CondicionBoleta.VENDIDO,
        models.Boleta.liquidacion_vendedor_id.is_(None),
    ).all()

    if not pendientes:
        return RedirectResponse(f"/vendedores/{vid}/detalle?msg=sin_pendientes", status_code=302)

    cuotas   = [b for b in pendientes if b.numero_especial is None]
    contados = [b for b in pendientes if b.numero_especial is not None]

    monto_cuotas   = sum((b.talonera.valor_cuota if b.talonera else 0) for b in cuotas)
    monto_contados = sum((b.talonera.valor_cuota if b.talonera else 0) for b in contados)
    com_cuotas     = round(monto_cuotas   * comision_cuotas_pct   / 100, 2)
    com_contados   = round(monto_contados * comision_contados_pct / 100, 2)
    total          = round(com_cuotas + com_contados, 2)

    liq = models.LiquidacionVendedor(
        vendedor_id=vid,
        cuotas_vendidas=len(cuotas),
        monto_cuotas=monto_cuotas,
        comision_cuotas_pct=comision_cuotas_pct,
        comision_cuotas=com_cuotas,
        contados_vendidos=len(contados),
        monto_contados=monto_contados,
        comision_contados_pct=comision_contados_pct,
        comision_contados=com_contados,
        total_comision=total,
        observacion=observacion.strip() or None,
    )
    db.add(liq)
    db.flush()  # obtener liq.id

    # Marcar boletas como liquidadas
    for b in pendientes:
        b.liquidacion_vendedor_id = liq.id
    db.commit()

    return RedirectResponse(f"/vendedores/{vid}/detalle?msg=liquidado", status_code=302)


@router.post("/entrega-caja")
async def entrega_caja(
    request: Request,
    talonera_nombre: str = Form(...),
    desde: int = Form(...),
    hasta: int = Form(...),
    vendedor_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    if hasta < desde:
        return JSONResponse({"ok": False, "error": "Rango invalido"}, status_code=400)

    if not vendedor_id:
        jefe = db.query(models.Vendedor).filter_by(es_jefe_equipo=True, activo=True).first()
        vendedor_id = jefe.id if jefe else None

    talonera_ids = [
        t.id for t in db.query(models.Talonera).filter_by(nombre=talonera_nombre).all()
    ]
    if not talonera_ids:
        return JSONResponse({"ok": False, "error": "Talonera no encontrada"}, status_code=404)

    update_data = {"condicion": CondicionBoleta.CAJA}
    if vendedor_id:
        update_data["vendedor_id"] = vendedor_id

    actualizadas = db.query(models.Boleta).filter(
        models.Boleta.talonera_id.in_(talonera_ids),
        models.Boleta.numero_principal >= desde,
        models.Boleta.numero_principal <= hasta,
        models.Boleta.condicion == CondicionBoleta.SIN_VENDER,
    ).update(update_data, synchronize_session=False)

    entrega = models.EntregaCaja(
        talonera_nombre=talonera_nombre,
        desde=desde,
        hasta=hasta,
        boletas_afectadas=actualizadas,
        usuario_id=_perm_user.id,
        vendedor_id=vendedor_id,
    )
    db.add(entrega)
    db.commit()
    db.refresh(entrega)

    vend_nombre = entrega.vendedor.nombre if entrega.vendedor else None
    return JSONResponse({
        "ok": True,
        "actualizadas": actualizadas,
        "entrega_id": entrega.id,
        "vendedor_nombre": vend_nombre,
    })


@router.post("/entrega-caja/{entrega_id}/editar")
async def editar_entrega(
    entrega_id: int,
    request: Request,
    talonera_nombre: str = Form(...),
    desde: int = Form(...),
    hasta: int = Form(...),
    observacion: str = Form(""),
    vendedor_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    e = db.query(models.EntregaCaja).get(entrega_id)
    if not e:
        raise HTTPException(404)
    e.talonera_nombre = talonera_nombre
    e.desde = desde
    e.hasta = hasta
    e.observacion = observacion.strip() or None
    if vendedor_id:
        e.vendedor_id = vendedor_id
    db.commit()
    return RedirectResponse("/vendedores/", status_code=302)


@router.post("/entrega-caja/{entrega_id}/eliminar")
async def eliminar_entrega(entrega_id: int, request: Request, db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    e = db.query(models.EntregaCaja).get(entrega_id)
    if e:
        db.delete(e)
        db.commit()
    return RedirectResponse("/vendedores/", status_code=302)


@router.post("/{vid}/toggle-jefe")
async def toggle_jefe(vid: int, request: Request, db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    v = db.query(models.Vendedor).get(vid)
    if not v:
        raise HTTPException(404)
    if v.es_jefe_equipo:
        v.es_jefe_equipo = False
    else:
        db.query(models.Vendedor).filter_by(es_jefe_equipo=True).update(
            {"es_jefe_equipo": False}, synchronize_session=False
        )
        v.es_jefe_equipo = True
    db.commit()
    return RedirectResponse("/vendedores/", status_code=302)


@router.post("/crear")
async def crear(
    request: Request,
    nombre: str = Form(...),
    telefono: str = Form(""),
    db: Session = Depends(get_db),
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    v = models.Vendedor(nombre=nombre.strip().upper(), telefono=telefono.strip() or None)
    db.add(v)
    db.commit()
    return RedirectResponse("/vendedores/", status_code=302)


@router.post("/{vid}/toggle")
async def toggle(vid: int, request: Request, db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    v = db.query(models.Vendedor).get(vid)
    if v:
        v.activo = not v.activo
        db.commit()
    return RedirectResponse("/vendedores/", status_code=302)


@router.post("/{vid}/editar")
async def editar(
    vid: int,
    request: Request,
    nombre: str = Form(...),
    telefono: str = Form(""),
    db: Session = Depends(get_db),
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    v = db.query(models.Vendedor).get(vid)
    if v:
        v.nombre = nombre.strip().upper()
        v.telefono = telefono.strip() or None
        db.commit()
    return RedirectResponse("/vendedores/", status_code=302)
