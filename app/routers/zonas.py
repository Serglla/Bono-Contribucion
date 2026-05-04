from fastapi import HTTPException,  APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Optional

from sqlalchemy.orm import Session
from .. import models, auth as auth_module
from ..templates_config import templates
from ..database import get_db

router = APIRouter(prefix="/zonas", tags=["zonas"])



@router.get("/", response_class=HTMLResponse)
async def listar(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'zonas', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    zonas = db.query(models.Zona).order_by(models.Zona.nombre).all()
    vendedores = db.query(models.Vendedor).filter(models.Vendedor.activo == True).order_by(models.Vendedor.nombre).all()
    return templates.TemplateResponse(request, "zonas.html", {"user": user, "zonas": zonas, "vendedores": vendedores})


@router.post("/crear")
async def crear(
    request: Request,
    nombre: str = Form(...),
    descripcion: str = Form(""),
    db: Session = Depends(get_db)
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'zonas', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    z = models.Zona(nombre=nombre.strip(), descripcion=descripcion.strip() or None)
    db.add(z)
    db.commit()
    return RedirectResponse("/zonas/", status_code=302)


@router.post("/{zid}/vendedor")
async def asignar_vendedor(
    zid: int, request: Request,
    vendedor_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'zonas', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    z = db.query(models.Zona).get(zid)
    if z:
        z.vendedor_id = vendedor_id or None
        db.commit()
    return RedirectResponse("/zonas/", status_code=302)


@router.post("/{zid}/eliminar")
async def eliminar(zid: int, request: Request, db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'zonas', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    z = db.query(models.Zona).get(zid)
    if z:
        for comp in z.compradores:
            comp.zona_id = None
        db.delete(z)
        db.commit()
    return RedirectResponse("/zonas/", status_code=302)
