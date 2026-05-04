from fastapi import HTTPException, APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from sqlalchemy.orm import Session
from .. import models, auth as auth_module
from ..models import CondicionBoleta
from ..templates_config import templates
from ..database import get_db

router = APIRouter(prefix="/vendedores", tags=["vendedores"])



@router.get("/", response_class=HTMLResponse)
async def listar(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'vendedores', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    vendedores = db.query(models.Vendedor).order_by(models.Vendedor.nombre).all()
    taloneras = db.query(models.Talonera).order_by(models.Talonera.nombre, models.Talonera.numero_inicio).all()
    grupos_talonera = list(dict.fromkeys(t.nombre for t in taloneras))
    return templates.TemplateResponse(request, "vendedores.html", {
        "user": user, "vendedores": vendedores, "grupos_talonera": grupos_talonera
    })


@router.post("/entrega-caja")
async def entrega_caja(
    request: Request,
    talonera_nombre: str = Form(...),
    desde: int = Form(...),
    hasta: int = Form(...),
    db: Session = Depends(get_db)
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'vendedores', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    if hasta < desde:
        return JSONResponse({"ok": False, "error": "Rango inválido"}, status_code=400)
    talonera_ids = [
        t.id for t in db.query(models.Talonera).filter_by(nombre=talonera_nombre).all()
    ]
    if not talonera_ids:
        return JSONResponse({"ok": False, "error": "Talonera no encontrada"}, status_code=404)
    actualizadas = db.query(models.Boleta).filter(
        models.Boleta.talonera_id.in_(talonera_ids),
        models.Boleta.numero_principal >= desde,
        models.Boleta.numero_principal <= hasta,
        models.Boleta.condicion == CondicionBoleta.SIN_VENDER
    ).update({"condicion": CondicionBoleta.CAJA}, synchronize_session=False)
    db.commit()
    return JSONResponse({"ok": True, "actualizadas": actualizadas})


@router.post("/crear")
async def crear(
    request: Request,
    nombre: str = Form(...),
    telefono: str = Form(""),
    db: Session = Depends(get_db)
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'vendedores', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    v = models.Vendedor(nombre=nombre.strip().upper(), telefono=telefono.strip() or None)
    db.add(v)
    db.commit()
    return RedirectResponse("/vendedores/", status_code=302)


@router.post("/{vid}/toggle")
async def toggle(vid: int, request: Request, db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'vendedores', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
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
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    v = db.query(models.Vendedor).get(vid)
    if v:
        v.nombre = nombre.strip().upper()
        v.telefono = telefono.strip() or None
        db.commit()
    return RedirectResponse("/vendedores/", status_code=302)
