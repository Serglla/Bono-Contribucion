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
async def listar(request: Request, db: Session = Depends(get_db), error: str = "", nombre: str = ""):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'taloneras', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    taloneras = db.query(models.Talonera).order_by(models.Talonera.multiplicador, models.Talonera.numero_inicio).all()
    # Agrupar por nombre para la vista — separamos COMUN de CONTADO
    grupos: dict = {}
    grupos_contado: list = []
    for t in taloneras:
        if (t.tipo or "COMUN") == "CONTADO":
            # Calcular cuántos números fueron asignados a boletas comunes
            asignados = db.query(models.Boleta).filter(
                models.Boleta.talonera_especial_id == t.id
            ).count()
            grupos_contado.append({
                "talonera": t,
                "asignados": asignados,
                "rango": (t.numero_fin or 0) - (t.numero_inicio or 0) + 1 if t.numero_inicio and t.numero_fin else 0,
            })
            continue
        if t.nombre not in grupos:
            grupos[t.nombre] = {"nombre": t.nombre, "num_series": t.num_series,
                                "multiplicador": t.multiplicador, "color": t.color or "#ffffff",
                                "taloneras": []}
        grupos[t.nombre]["taloneras"].append(t)
    return templates.TemplateResponse(request, "taloneras.html", {
        "user": user, "taloneras": taloneras, "grupos": list(grupos.values()),
        "grupos_contado": grupos_contado,
        "error": error, "error_nombre": nombre
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
    valor_cuota: float = Form(0.0),
    db: Session = Depends(get_db)
):
    await auth_module.require_admin(request, db)
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
        offset_series=offset,
        valor_cuota=valor_cuota,
        tipo="COMUN",
    )
    db.add(t)
    db.commit()
    return RedirectResponse("/taloneras/", status_code=302)


@router.post("/crear-contado")
async def crear_contado(
    request: Request,
    nombre: str = Form(...),
    numero_inicio: int = Form(...),
    numero_fin: int = Form(...),
    color: str = Form("#fff8e1"),
    db: Session = Depends(get_db)
):
    """Crear talonera tipo CONTADO — pool de números especiales para pago al contado.
    No tiene series ni offset; es una secuencia simple de números."""
    await auth_module.require_admin(request, db)
    if numero_fin < numero_inicio:
        return RedirectResponse("/taloneras/?error=rango_invalido", status_code=302)
    t = models.Talonera(
        nombre=nombre.strip() or "CONTADO",
        multiplicador=1,
        numero_inicio=numero_inicio,
        numero_fin=numero_fin,
        num_series=1,
        offset_series=0,
        color=color or "#fff8e1",
        valor_cuota=0.0,
        tipo="CONTADO",
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


@router.get("/enumeracion", response_class=HTMLResponse)
async def enumeracion(request: Request, db: Session = Depends(get_db)):
    """Vista global: todos los números del 0001 al 9999 con su condición."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'taloneras', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')

    # Mapa numero_principal -> condicion para TODAS las boletas
    boletas_dict: dict = {}
    todas = db.query(models.Boleta).all()
    for b in todas:
        boletas_dict[b.numero_principal] = b.condicion.value

    # Detectar repetidos: número_principal que también aparece como adicional de otra boleta
    adicionales_set: set = set()
    for b in todas:
        if b.numeros_adicionales:
            for parte in b.numeros_adicionales.split(","):
                try:
                    adicionales_set.add(int(parte.strip()))
                except ValueError:
                    pass
    repetidos = sum(1 for n in boletas_dict if n in adicionales_set)

    # Rango fijo 0001-9999
    numeros = []
    for n in range(1, 10000):
        condicion = boletas_dict.get(n, "SIN_IMPRIMIR")
        numeros.append({"numero": n, "condicion": condicion})

    # Estadísticas
    stats: dict = {}
    for item in numeros:
        c = item["condicion"]
        stats[c] = stats.get(c, 0) + 1

    return templates.TemplateResponse(request, "enumeracion.html", {
        "user": user,
        "numeros": numeros,
        "stats": stats,
        "repetidos": repetidos,
    })


@router.post("/{talonera_id}/editar")
async def editar_talonera(
    talonera_id: int, request: Request,
    nombre: str = Form(...),
    num_series: int = Form(3),
    offset_series: int = Form(0),
    numero_inicio: int = Form(...),
    numero_fin: int = Form(...),
    valor_cuota: float = Form(0.0),
    db: Session = Depends(get_db)
):
    await auth_module.require_admin(request, db)
    t = db.query(models.Talonera).get(talonera_id)
    if not t:
        raise HTTPException(404)
    t.nombre = nombre
    t.num_series = num_series
    t.multiplicador = num_series // 3
    t.offset_series = offset_series
    t.numero_inicio = numero_inicio
    t.numero_fin = numero_fin
    t.valor_cuota = valor_cuota
    db.commit()
    return RedirectResponse("/taloneras/", status_code=302)


@router.post("/{talonera_id}/editar-contado")
async def editar_talonera_contado(
    talonera_id: int, request: Request,
    nombre: str = Form(...),
    numero_inicio: int = Form(...),
    numero_fin: int = Form(...),
    db: Session = Depends(get_db)
):
    """Editar nombre/rango de una talonera CONTADO."""
    await auth_module.require_admin(request, db)
    t = db.query(models.Talonera).get(talonera_id)
    if not t or (t.tipo or "COMUN") != "CONTADO":
        raise HTTPException(404)
    if numero_fin < numero_inicio:
        return RedirectResponse("/taloneras/?error=rango_invalido", status_code=302)
    t.nombre = nombre
    t.numero_inicio = numero_inicio
    t.numero_fin = numero_fin
    db.commit()
    return RedirectResponse("/taloneras/", status_code=302)


@router.post("/{talonera_id}/eliminar")
async def eliminar_talonera(talonera_id: int, request: Request, db: Session = Depends(get_db)):
    await auth_module.require_admin(request, db)
    t = db.query(models.Talonera).get(talonera_id)
    if not t:
        raise HTTPException(404)
    # Talonera CONTADO: solo se puede eliminar si no hay números asignados
    if (t.tipo or "COMUN") == "CONTADO":
        asignados = db.query(models.Boleta).filter(
            models.Boleta.talonera_especial_id == talonera_id
        ).count()
        if asignados > 0:
            return RedirectResponse(f"/taloneras/?error=contado_asignados&nombre={t.nombre}", status_code=302)
        db.delete(t)
        db.commit()
        return RedirectResponse("/taloneras/", status_code=302)
    vendidas = db.query(models.Boleta).filter(
        models.Boleta.talonera_id == talonera_id,
        models.Boleta.condicion == CondicionBoleta.VENDIDO
    ).count()
    if vendidas > 0:
        return RedirectResponse(f"/taloneras/?error=tiene_vendidas&nombre={t.nombre}", status_code=302)
    # Eliminar boletas y luego la talonera
    db.query(models.Boleta).filter(models.Boleta.talonera_id == talonera_id).delete()
    db.delete(t)
    db.commit()
    return RedirectResponse("/taloneras/", status_code=302)


@router.get("/{talonera_id}/boletas", response_class=HTMLResponse)
async def boletas(
    talonera_id: int, request: Request, db: Session = Depends(get_db),
    condicion: str = "", q: str = ""
):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'taloneras', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
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
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'taloneras', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
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
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'taloneras', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
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


@router.post("/boletas/{boleta_id}/condicion")
async def cambiar_condicion_boleta(
    boleta_id: int, request: Request,
    condicion: str = Form(...),
    db: Session = Depends(get_db)
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'taloneras', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    b = db.query(models.Boleta).get(boleta_id)
    if not b:
        raise HTTPException(404)
    b.condicion = CondicionBoleta(condicion)
    db.commit()
    return JSONResponse({"ok": True, "condicion": b.condicion.value})


@router.post("/boletas/{boleta_id}/eliminar")
async def eliminar_boleta(boleta_id: int, request: Request, db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'taloneras', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    b = db.query(models.Boleta).get(boleta_id)
    if b:
        talonera_id = b.talonera_id
        db.delete(b)
        db.commit()
        return RedirectResponse(f"/taloneras/{talonera_id}/boletas", status_code=302)
    return RedirectResponse("/taloneras/", status_code=302)
