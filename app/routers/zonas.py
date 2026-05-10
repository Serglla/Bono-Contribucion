from fastapi import HTTPException,  APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Optional

from sqlalchemy.orm import Session
from .. import models, auth as auth_module
from ..templates_config import templates
from ..database import get_db

router = APIRouter(prefix="/zonas", tags=["zonas"])



@router.get("/", response_class=HTMLResponse)
async def listar(
    request: Request,
    db: Session = Depends(get_db),
    ok: Optional[str] = None,
    err: Optional[str] = None,
    n: Optional[str] = None,
):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'zonas', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    zonas = db.query(models.Zona).order_by(models.Zona.nombre).all()
    vendedores = db.query(models.Vendedor).filter(models.Vendedor.activo == True).order_by(models.Vendedor.nombre).all()
    return templates.TemplateResponse(request, "zonas.html", {
        "user": user,
        "zonas": zonas,
        "vendedores": vendedores,
        "msg_ok": ok,
        "msg_err": err,
        "msg_nombre": n,
    })


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


@router.post("/{zid}/editar")
async def editar(
    zid: int, request: Request,
    nombre: str = Form(...),
    descripcion: str = Form(""),
    db: Session = Depends(get_db),
):
    """Renombra una zona / actualiza su descripción. Los socios (compradores)
    están vinculados por zona_id, así que el nuevo nombre se propaga sólo."""
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'zonas', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    z = db.query(models.Zona).get(zid)
    if not z:
        raise HTTPException(404, "Zona no encontrada")

    nuevo_nombre = (nombre or "").strip()
    if not nuevo_nombre:
        return RedirectResponse("/zonas/?err=nombre_vacio", status_code=302)

    # Si el nombre no cambió respecto al actual, sólo actualizar descripcion.
    if nuevo_nombre != z.nombre:
        # Validar unicidad (excluyendo la propia zona).
        ya_existe = (
            db.query(models.Zona)
              .filter(models.Zona.nombre == nuevo_nombre, models.Zona.id != zid)
              .first()
        )
        if ya_existe:
            return RedirectResponse(
                f"/zonas/?err=nombre_duplicado&n={nuevo_nombre}",
                status_code=302,
            )
        z.nombre = nuevo_nombre

    z.descripcion = (descripcion or "").strip() or None
    db.commit()
    return RedirectResponse("/zonas/?ok=editada", status_code=302)


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
