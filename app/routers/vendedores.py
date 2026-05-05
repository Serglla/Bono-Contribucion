from fastapi import HTTPException, APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from sqlalchemy.orm import Session
from typing import Optional
from .. import models, auth as auth_module
from ..models import CondicionBoleta
from ..templates_config import templates
from ..database import get_db

router = APIRouter(prefix="/vendedores", tags=["vendedores"])


def _stats_vendedor(v, db):
    """Devuelve dict con conteos y lista de boletas CAJA/BAJA agrupadas por pata."""
    boletas = db.query(models.Boleta).filter_by(vendedor_id=v.id).all()
    caja    = [b for b in boletas if b.condicion == CondicionBoleta.CAJA]
    baja    = [b for b in boletas if b.condicion == CondicionBoleta.BAJA]
    vendido = [b for b in boletas if b.condicion == CondicionBoleta.VENDIDO]
    caja_por_pata = {}
    for b in caja:
        pata = b.talonera.nombre if b.talonera else "?"
        caja_por_pata.setdefault(pata, []).append(b.numero_principal)
    for pata in caja_por_pata:
        caja_por_pata[pata].sort()
    return {
        "total": len(boletas),
        "caja": len(caja),
        "baja": len(baja),
        "vendido": len(vendido),
        "caja_por_pata": caja_por_pata,
    }


@router.get("/", response_class=HTMLResponse)
async def listar(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'vendedores', 'ver'):
        raise HTTPException(403, 'No tenes permiso para ver esta seccion')
    vendedores = db.query(models.Vendedor).order_by(
        models.Vendedor.es_jefe_equipo.desc(), models.Vendedor.nombre
    ).all()
    taloneras = db.query(models.Talonera).order_by(models.Talonera.nombre, models.Talonera.numero_inicio).all()
    grupos_talonera = list(dict.fromkeys(t.nombre for t in taloneras))
    entregas = db.query(models.EntregaCaja).order_by(models.EntregaCaja.fecha.desc()).limit(200).all()
    stats = {v.id: _stats_vendedor(v, db) for v in vendedores}
    jefe = db.query(models.Vendedor).filter_by(es_jefe_equipo=True, activo=True).first()
    return templates.TemplateResponse(request, "vendedores.html", {
        "user": user, "vendedores": vendedores,
        "grupos_talonera": grupos_talonera, "entregas": entregas,
        "stats": stats, "jefe": jefe,
    })


@router.post("/entrega-caja")
async def entrega_caja(
    request: Request,
    talonera_nombre: str = Form(...),
    desde: int = Form(...),
    hasta: int = Form(...),
    vendedor_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'vendedores', 'editar'):
        raise HTTPException(403, 'No tenes permiso para editar en esta seccion')
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

    boletas_q = db.query(models.Boleta).filter(
        models.Boleta.talonera_id.in_(talonera_ids),
        models.Boleta.numero_principal >= desde,
        models.Boleta.numero_principal <= hasta,
        models.Boleta.condicion == CondicionBoleta.SIN_VENDER
    )
    update_data = {"condicion": CondicionBoleta.CAJA}
    if vendedor_id:
        update_data["vendedor_id"] = vendedor_id
    actualizadas = boletas_q.update(update_data, synchronize_session=False)

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

    vendedor_nombre = entrega.vendedor.nombre if entrega.vendedor else None
    return JSONResponse({
        "ok": True,
        "actualizadas": actualizadas,
        "entrega_id": entrega.id,
        "vendedor_nombre": vendedor_nombre,
    })


@router.post("/entrega-caja/{entrega_id}/editar")
async def editar_entrega(
    entrega_id: int, request: Request,
    talonera_nombre: str = Form(...),
    desde: int = Form(...),
    hasta: int = Form(...),
    observacion: str = Form(""),
    vendedor_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'vendedores', 'editar'):
        raise HTTPException(403, 'Sin permiso')
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
    if not auth_module.has_permission(_perm_user, 'vendedores', 'editar'):
        raise HTTPException(403, 'Sin permiso')
    e = db.query(models.EntregaCaja).get(entrega_id)
    if e:
        db.delete(e)
        db.commit()
    return RedirectResponse("/vendedores/", status_code=302)


@router.post("/{vid}/toggle-jefe")
async def toggle_jefe(vid: int, request: Request, db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'vendedores', 'editar'):
        raise HTTPException(403, 'Sin permiso')
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
    db: Session = Depends(get_db)
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'vendedores', 'editar'):
        raise HTTPException(403, 'No tenes permiso para editar en esta seccion')
    v = models.Vendedor(nombre=nombre.strip().upper(), telefono=telefono.strip() or None)
    db.add(v)
    db.commit()
    return RedirectResponse("/vendedores/", status_code=302)


@router.post("/{vid}/toggle")
async def toggle(vid: int, request: Request, db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'vendedores', 'editar'):
        raise HTTPException(403, 'No tenes permiso para editar en esta seccion')
    v = db.query(models.Vendedor).get(vid)
    if v:
        v.activo = not v.activo
        db.commit()
    return RedirectResponse("/vendedores/", status_code=302)


@router.post("/{vid}/editar")
async def editar(
    vid: int, request: Request,
    nombre: str = Form(...),
    telefono: str = Form(""),
    db: Session = Depends(get_db)
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'vendedores', 'editar'):
        raise HTTPException(403, 'No tenes permiso para editar en esta seccion')
    v = db.query(models.Vendedor).get(vid)
    if v:
        v.nombre = nombre.strip().upper()
        v.telefono = telefono.strip() or None
        db.commit()
    return RedirectResponse("/vendedores/", status_code=302)
