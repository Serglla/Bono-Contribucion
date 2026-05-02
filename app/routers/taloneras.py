from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import date
from .. import models, auth as auth_module
from ..templates_config import templates
from ..models import CondicionBoleta
from ..database import get_db

router = APIRouter(prefix="/taloneras", tags=["taloneras"])



@router.get("/", response_class=HTMLResponse)
async def listar(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    taloneras = db.query(models.Talonera).order_by(models.Talonera.multiplicador, models.Talonera.numero_inicio).all()
    # Agrupar por nombre para la vista
    grupos: dict = {}
    for t in taloneras:
        if t.nombre not in grupos:
            grupos[t.nombre] = {"nombre": t.nombre, "num_series": t.num_series,
                                "multiplicador": t.multiplicador, "color": t.color or "#ffffff",
                                "taloneras": []}
        grupos[t.nombre]["taloneras"].append(t)
    return templates.TemplateResponse(request, "taloneras.html", {
        "user": user, "taloneras": taloneras, "grupos": list(grupos.values())
    })


@router.post("/grupo/color")
async def actualizar_color_grupo(
    request: Request,
    nombre: str = Form(...),
    color: str = Form(...),
    db: Session = Depends(get_db)
):
    await auth_module.require_admin(request, db)
    db.query(models.Talonera).filter(models.Talonera.nombre == nombre).update({"color": color})
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/crear")
async def crear(
    request: Request,
    nombre: str = Form(...),
    num_series: int = Form(3),
    serie_inicio: List[int] = Form(...),
    serie_fin: List[int] = Form(...),
    db: Session = Depends(get_db)
):
    await auth_module.require_admin(request, db)
    # Calcular multiplicador y offset desde las series enviadas
    multiplicador = num_series // 3
    offset = (serie_inicio[1] - serie_inicio[0]) if len(serie_inicio) >= 2 else 0
    numero_inicio = serie_inicio[0] if serie_inicio else None
    numero_fin = serie_fin[0] if serie_fin else None
    t = models.Talonera(
        nombre=nombre,
        multiplicador=multiplicador,
        numero_inicio=numero_inicio,
        numero_fin=numero_fin,
        num_series=num_series,
        offset_series=offset
    )
    db.add(t)
    db.commit()
    return RedirectResponse("/taloneras/", status_code=302)


def calcular_numeros(numero_principal: int, num_series: int, offset: int) -> str:
    """Genera los números adicionales de una boleta según el offset de la talonera."""
    if num_series <= 1 or offset == 0:
        return ""
    adicionales = [str(numero_principal + offset * i) for i in range(1, num_series)]
    return ", ".join(adicionales)


@router.get("/buscar-boleta/{numero}")
async def buscar_boleta_global(numero: int, request: Request, db: Session = Depends(get_db)):
    """Busca una boleta por número principal en todas las taloneras."""
    await auth_module.require_user(request, db)
    b = db.query(models.Boleta).filter(
        models.Boleta.numero_principal == numero
    ).first()
    if not b:
        raise HTTPException(404, detail="Boleta no encontrada")
    return JSONResponse({
        "id": b.id,
        "talonera": b.talonera.nombre if b.talonera else "",
        "numero_principal": b.numero_principal,
        "numeros_adicionales": b.numeros_adicionales or "",
        "comprador_id": b.comprador_id,
        "comprador": b.comprador.apellido_nombre if b.comprador else None,
    })


@router.get("/{talonera_id}/boleta-info/{numero}")
async def boleta_info(talonera_id: int, numero: int, request: Request, db: Session = Depends(get_db)):
    """Devuelve info de una boleta por su número principal (para búsqueda AJAX)."""
    await auth_module.require_user(request, db)
    b = db.query(models.Boleta).filter(
        models.Boleta.talonera_id == talonera_id,
        models.Boleta.numero_principal == numero
    ).first()
    if not b:
        raise HTTPException(404, detail="Boleta no encontrada")
    return JSONResponse({
        "id": b.id,
        "numero_principal": b.numero_principal,
        "numeros_adicionales": b.numeros_adicionales or "",
        "comprador_id": b.comprador_id,
        "comprador": b.comprador.apellido_nombre if b.comprador else None,
        "vendedor_id": b.vendedor_id,
        "cobrador_id": b.cobrador_id,
        "fecha_venta": b.fecha_venta.isoformat() if b.fecha_venta else "",
        "condicion": b.condicion.value,
        "cuotas_pactadas": b.cuotas_pactadas,
        "cuotas_pagadas": b.cuotas_pagadas,
        "total_pagado": b.total_pagado,
    })


@router.get("/{talonera_id}/boletas", response_class=HTMLResponse)
async def boletas(
    talonera_id: int, request: Request, db: Session = Depends(get_db),
    condicion: str = "", q: str = ""
):
    user = await auth_module.require_user(request, db)
    talonera = db.query(models.Talonera).get(talonera_id)
    if not talonera:
        raise HTTPException(404)
    query = db.query(models.Boleta).filter(models.Boleta.talonera_id == talonera_id)
    if condicion:
        query = query.filter(models.Boleta.condicion == condicion)
    if q:
        query = query.join(models.Comprador, isouter=True).filter(
            models.Comprador.apellido_nombre.ilike(f"%{q}%")
        )
    boletas_list = query.order_by(models.Boleta.numero_principal).all()
    compradores = db.query(models.Comprador).order_by(models.Comprador.apellido_nombre).all()
    cobradores = db.query(models.Cobrador).filter_by(activo=True).all()
    vendedores = db.query(models.Vendedor).filter_by(activo=True).all()
    condiciones = [e.value for e in CondicionBoleta]
    return templates.TemplateResponse(request, "boletas.html", {"user": user, "talonera": talonera,
        "boletas": boletas_list, "compradores": compradores,
        "cobradores": cobradores, "vendedores": vendedores,
        "condiciones": condiciones, "condicion_sel": condicion, "q": q})


@router.post("/{talonera_id}/boletas/crear")
async def crear_boleta(
    talonera_id: int, request: Request,
    numero_principal: int = Form(...),
    numeros_adicionales: str = Form(""),
    comprador_id: Optional[int] = Form(None),
    cobrador_id: Optional[int] = Form(None),
    vendedor_id: Optional[int] = Form(None),
    fecha_venta: Optional[str] = Form(None),
    condicion: str = Form("SIN_VENDER"),
    cuotas_pactadas: int = Form(11),
    cuotas_pagadas: int = Form(0),
    total_pagado: float = Form(0.0),
    db: Session = Depends(get_db)
):
    await auth_module.require_user(request, db)
    talonera = db.query(models.Talonera).get(talonera_id)
    fecha = date.fromisoformat(fecha_venta) if fecha_venta else None
    # Auto-calcular números adicionales según la configuración de la talonera
    if talonera and talonera.offset_series and talonera.num_series > 1:
        numeros_adicionales = calcular_numeros(numero_principal, talonera.num_series, talonera.offset_series)
    b = models.Boleta(
        talonera_id=talonera_id,
        numero_principal=numero_principal,
        numeros_adicionales=numeros_adicionales or None,
        comprador_id=comprador_id or None,
        cobrador_id=cobrador_id or None,
        vendedor_id=vendedor_id or None,
        fecha_venta=fecha,
        condicion=CondicionBoleta(condicion),
        cuotas_pactadas=cuotas_pactadas,
        cuotas_pagadas=cuotas_pagadas,
        total_pagado=total_pagado
    )
    db.add(b)
    db.commit()
    return RedirectResponse(f"/taloneras/{talonera_id}/boletas", status_code=302)


@router.post("/boletas/{boleta_id}/editar")
async def editar_boleta(
    boleta_id: int, request: Request,
    comprador_id: Optional[int] = Form(None),
    cobrador_id: Optional[int] = Form(None),
    vendedor_id: Optional[int] = Form(None),
    fecha_venta: Optional[str] = Form(None),
    condicion: str = Form("SIN_VENDER"),
    cuotas_pactadas: int = Form(11),
    cuotas_pagadas: int = Form(0),
    total_pagado: float = Form(0.0),
    db: Session = Depends(get_db)
):
    await auth_module.require_user(request, db)
    b = db.query(models.Boleta).get(boleta_id)
    if not b:
        raise HTTPException(404)
    b.comprador_id = comprador_id or None
    b.cobrador_id = cobrador_id or None
    b.vendedor_id = vendedor_id or None
    b.fecha_venta = date.fromisoformat(fecha_venta) if fecha_venta else None
    b.condicion = CondicionBoleta(condicion)
    b.cuotas_pactadas = cuotas_pactadas
    b.cuotas_pagadas = cuotas_pagadas
    b.total_pagado = total_pagado
    db.commit()
    return RedirectResponse(f"/taloneras/{b.talonera_id}/boletas", status_code=302)


@router.post("/{talonera_id}/generar-boletas")
async def generar_boletas(
    talonera_id: int, request: Request,
    numero_inicio: int = Form(...),
    numero_fin: int = Form(...),
    cuotas_pactadas: int = Form(11),
    db: Session = Depends(get_db)
):
    await auth_module.require_admin(request, db)
    talonera = db.query(models.Talonera).get(talonera_id)
    if not talonera:
        raise HTTPException(404)

    # Números ya existentes para no duplicar
    existentes = {
        b.numero_principal
        for b in db.query(models.Boleta.numero_principal)
                   .filter(models.Boleta.talonera_id == talonera_id)
                   .all()
    }

    nuevas = []
    for n in range(numero_inicio, numero_fin + 1):
        if n in existentes:
            continue
        adicionales = calcular_numeros(n, talonera.num_series, talonera.offset_series)
        nuevas.append(models.Boleta(
            talonera_id=talonera_id,
            numero_principal=n,
            numeros_adicionales=adicionales or None,
            condicion=CondicionBoleta.SIN_VENDER,
            cuotas_pactadas=cuotas_pactadas,
            cuotas_pagadas=0,
            total_pagado=0.0,
        ))

    db.bulk_save_objects(nuevas)
    db.commit()
    return RedirectResponse(f"/taloneras/{talonera_id}/boletas", status_code=302)


@router.post("/boletas/{boleta_id}/eliminar")
async def eliminar_boleta(boleta_id: int, request: Request, db: Session = Depends(get_db)):
    await auth_module.require_user(request, db)
    b = db.query(models.Boleta).get(boleta_id)
    if b:
        talonera_id = b.talonera_id
        db.delete(b)
        db.commit()
        return RedirectResponse(f"/taloneras/{talonera_id}/boletas", status_code=302)
    return RedirectResponse("/taloneras/", status_code=302)
