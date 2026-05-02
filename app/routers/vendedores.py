from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from sqlalchemy.orm import Session
from .. import models, auth as auth_module
from ..templates_config import templates
from ..database import get_db

router = APIRouter(prefix="/vendedores", tags=["vendedores"])



@router.get("/", response_class=HTMLResponse)
async def listar(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    vendedores = db.query(models.Vendedor).order_by(models.Vendedor.nombre).all()
    return templates.TemplateResponse(request, "vendedores.html", {"user": user, "vendedores": vendedores})


@router.post("/crear")
async def crear(
    request: Request,
    nombre: str = Form(...),
    telefono: str = Form(""),
    db: Session = Depends(get_db)
):
    await auth_module.require_user(request, db)
    v = models.Vendedor(nombre=nombre.strip().upper(), telefono=telefono.strip() or None)
    db.add(v)
    db.commit()
    return RedirectResponse("/vendedores/", status_code=302)


@router.post("/{vid}/toggle")
async def toggle(vid: int, request: Request, db: Session = Depends(get_db)):
    await auth_module.require_user(request, db)
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
    await auth_module.require_user(request, db)
    v = db.query(models.Vendedor).get(vid)
    if v:
        v.nombre = nombre.strip().upper()
        v.telefono = telefono.strip() or None
        db.commit()
    return RedirectResponse("/vendedores/", status_code=302)
