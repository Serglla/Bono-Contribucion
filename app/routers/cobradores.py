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
    """Actualiza la asignación de zonas para este cobrador.
    - Las zonas desmarcadas se eliminan de zona_cobradores (solo para este cobrador).
    - Las zonas marcadas nuevas se agregan con timestamp actual.
    - Otros cobradores de las mismas zonas NO se ven afectados.
    """
    from datetime import datetime
    existentes = db.query(models.ZonaCobrador).filter(
        models.ZonaCobrador.cobrador_id == cobrador_id
    ).all()
    existentes_ids = {zc.zona_id for zc in existentes}
    nuevos_ids = set(zona_ids)

    # Eliminar zonas que se desmarcaron
    ids_a_eliminar = existentes_ids - nuevos_ids
    if ids_a_eliminar:
        db.query(models.ZonaCobrador).filter(
            models.ZonaCobrador.cobrador_id == cobrador_id,
            models.ZonaCobrador.zona_id.in_(ids_a_eliminar)
        ).delete(synchronize_session="fetch")

    # Agregar zonas nuevas
    ids_a_agregar = nuevos_ids - existentes_ids
    for zona_id in ids_a_agregar:
        db.add(models.ZonaCobrador(
            zona_id=zona_id,
            cobrador_id=cobrador_id,
            asignado_en=datetime.utcnow()
        ))


@router.post("/crear")
async def crear(
    request: Request,
    nombre: str = Form(...),
    telefono: str = Form(""),
    comision_pct: float = Form(10.0),
    db: Session = Depends(get_db)
):
    await auth_module.require_user(request, db)
    form_data = await request.form()
    zona_ids = [int(v) for v in form_data.getlist("zona_ids") if v]
    c = models.Cobrador(nombre=nombre.strip().upper(), telefono=telefono.strip() or None,
                        comision_pct=comision_pct)
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
    comision_pct: float = Form(10.0),
    db: Session = Depends(get_db)
):
    await auth_module.require_user(request, db)
    form_data = await request.form()
    zona_ids = [int(v) for v in form_data.getlist("zona_ids") if v]
    c = db.query(models.Cobrador).get(cid)
    if c:
        c.nombre = nombre.strip().upper()
        c.telefono = telefono.strip() or None
        c.comision_pct = comision_pct
        _actualizar_zonas(cid, zona_ids, db)
        db.commit()
    return RedirectResponse("/cobradores/", status_code=302)
