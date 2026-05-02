from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import List, Optional

from sqlalchemy.orm import Session
from .. import models, auth as auth_module
from ..templates_config import templates
from ..database import get_db

router = APIRouter(prefix="/cobradores", tags=["cobradores"])


@router.get("/", response_class=HTMLResponse)
async def listar(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    cobradores = db.query(models.Cobrador).order_by(models.Cobrador.nombre).all()
    zonas = db.query(models.Zona).order_by(models.Zona.nombre).all()
    return templates.TemplateResponse(request, "cobradores.html", {
        "user": user,
        "cobradores": cobradores,
        "zonas": zonas,
    })


def _actualizar_zonas(cobrador_id: int, zona_ids: List[int], db: Session):
    """Desasigna todas las zonas anteriores del cobrador y asigna las nuevas."""
    # Limpiar zonas que antes tenían este cobrador
    db.query(models.Zona).filter(
        models.Zona.cobrador_id == cobrador_id
    ).update({"cobrador_id": None})
    # Asignar las zonas seleccionadas a este cobrador
    if zona_ids:
        db.query(models.Zona).filter(
            models.Zona.id.in_(zona_ids)
        ).update({"cobrador_id": cobrador_id}, synchronize_session="fetch")


@router.post("/crear")
async def crear(
    request: Request,
    nombre: str = Form(...),
    telefono: str = Form(""),
    db: Session = Depends(get_db)
):
    await auth_module.require_user(request, db)
    form_data = await request.form()
    zona_ids = [int(v) for v in form_data.getlist("zona_ids") if v]
    c = models.Cobrador(nombre=nombre.strip().upper(), telefono=telefono.strip() or None)
    db.add(c)
    db.flush()
    _actualizar_zonas(c.id, zona_ids, db)
    db.commit()
    return RedirectResponse("/cobradores/", status_code=302)


@router.post("/{cid}/toggle")
async def toggle(cid: int, request: Request, db: Session = Depends(get_db)):
    await auth_module.require_user(request, db)
    c = db.query(models.Cobrador).get(cid)
    if c:
        c.activo = not c.activo
        db.commit()
    return RedirectResponse("/cobradores/", status_code=302)


@router.post("/{cid}/editar")
async def editar(
    cid: int, request: Request,
    nombre: str = Form(...),
    telefono: str = Form(""),
    db: Session = Depends(get_db)
):
    await auth_module.require_user(request, db)
    form_data = await request.form()
    zona_ids = [int(v) for v in form_data.getlist("zona_ids") if v]
    c = db.query(models.Cobrador).get(cid)
    if c:
        c.nombre = nombre.strip().upper()
        c.telefono = telefono.strip() or None
        _actualizar_zonas(cid, zona_ids, db)
        db.commit()
    return RedirectResponse("/cobradores/", status_code=302)
