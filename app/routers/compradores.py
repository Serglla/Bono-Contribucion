from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from sqlalchemy.orm import Session, selectinload
from sqlalchemy import func as func_count
from typing import Optional
from datetime import date
from .. import models, auth as auth_module
from ..templates_config import templates
from ..database import get_db
from ..models import CondicionBoleta


def _parse_zona_id(zona_id_str: Optional[str]) -> Optional[int]:
    """Convierte zona_id del form a int, ignorando el valor '__nueva__'."""
    if not zona_id_str or zona_id_str == "__nueva__":
        return None
    try:
        return int(zona_id_str)
    except ValueError:
        return None

router = APIRouter(prefix="/compradores", tags=["compradores"])



@router.get("/", response_class=HTMLResponse)
async def listar(request: Request, db: Session = Depends(get_db), q: str = "", pata: str = ""):
    user = await auth_module.require_user(request, db)

    # Base query: compradores que tengan al menos una boleta
    query = db.query(models.Comprador).join(models.Zona, isouter=True)
    if q:
        query = query.filter(models.Comprador.apellido_nombre.ilike(f"%{q}%"))
    if pata:
        query = (query
                 .join(models.Boleta, models.Boleta.comprador_id == models.Comprador.id)
                 .join(models.Talonera, models.Talonera.id == models.Boleta.talonera_id)
                 .filter(models.Talonera.nombre == pata)
                 .distinct())
    compradores_raw = query.options(
        selectinload(models.Comprador.boletas).selectinload(models.Boleta.talonera)
    ).order_by(models.Comprador.apellido_nombre).all()
    # Ordenar por PATA (multiplicador) primero, luego por número de boleta
    compradores = sorted(
        compradores_raw,
        key=lambda c: (
            c.boletas[0].talonera.multiplicador if c.boletas and c.boletas[0].talonera else 999,
            c.boletas[0].numero_principal if c.boletas else 999999
        )
    )

    # Conteos por talonera para los tabs
    from sqlalchemy import func as sqlfunc
    taloneras_raw = (
        db.query(models.Talonera.nombre, sqlfunc.count(models.Comprador.id.distinct()))
        .join(models.Boleta, models.Boleta.talonera_id == models.Talonera.id)
        .join(models.Comprador, models.Comprador.id == models.Boleta.comprador_id)
        .group_by(models.Talonera.nombre)
        .order_by(models.Talonera.nombre)
        .all()
    )
    total_compradores = db.query(models.Comprador).count()
    tabs = [{"nombre": t[0], "total": t[1]} for t in taloneras_raw]

    # Cantidad de socios sin vendedor/cobrador (para mostrar alertas en el template)
    sin_vendedor = sum(1 for c in compradores if c.boletas and not c.boletas[0].vendedor_id)
    sin_cobrador = sum(1 for c in compradores if c.boletas and not c.boletas[0].cobrador_id)

    zonas = db.query(models.Zona).order_by(models.Zona.nombre).all()
    vendedores = db.query(models.Vendedor).filter(models.Vendedor.activo == True).order_by(models.Vendedor.nombre).all()
    return templates.TemplateResponse(request, "compradores.html", {
        "user": user,
        "compradores": compradores,
        "zonas": zonas,
        "vendedores": vendedores,
        "q": q,
        "pata": pata,
        "tabs": tabs,
        "total_compradores": total_compradores,
        "sin_vendedor": sin_vendedor,
        "sin_cobrador": sin_cobrador,
    })


@router.post("/completar-vendedores")
async def completar_vendedores(request: Request, db: Session = Depends(get_db)):
    """
    Para cada comprador sin vendedor asignado:
    1. Si la zona tiene vendedor_id → usa ese.
    2. Si no → busca el vendedor más frecuente entre los otros socios de la misma zona.
    3. Asigna el vendedor a la boleta y, si la zona no tenía vendedor, lo vincula también.
    """
    from collections import Counter
    await auth_module.require_admin(request, db)

    # Todos los compradores con boleta pero sin vendedor y con zona
    sin_vendedor = (
        db.query(models.Comprador)
        .join(models.Boleta, models.Boleta.comprador_id == models.Comprador.id)
        .filter(models.Boleta.vendedor_id == None, models.Comprador.zona_id != None)
        .distinct()
        .all()
    )

    arreglados = 0
    for c in sin_vendedor:
        zona = db.query(models.Zona).get(c.zona_id)
        if not zona:
            continue

        vendedor_id = zona.vendedor_id  # primero el de la zona

        if not vendedor_id:
            # Buscar el vendedor más frecuente en la zona (excluyendo a este comprador)
            conteos = (
                db.query(models.Boleta.vendedor_id, func_count(models.Boleta.id))
                .join(models.Comprador, models.Comprador.id == models.Boleta.comprador_id)
                .filter(
                    models.Comprador.zona_id == zona.id,
                    models.Comprador.id != c.id,
                    models.Boleta.vendedor_id != None
                )
                .group_by(models.Boleta.vendedor_id)
                .order_by(func_count(models.Boleta.id).desc())
                .first()
            )
            if conteos:
                vendedor_id = conteos[0]
                # Vincular zona con este vendedor para el futuro
                zona.vendedor_id = vendedor_id

        if vendedor_id:
            for b in c.boletas:
                if not b.vendedor_id:
                    b.vendedor_id = vendedor_id
            arreglados += 1

    db.commit()
    from fastapi.responses import JSONResponse
    return JSONResponse({"ok": True, "arreglados": arreglados})


@router.post("/completar-cobradores")
async def completar_cobradores(request: Request, db: Session = Depends(get_db)):
    """
    Para cada comprador sin cobrador asignado:
    Usa el cobrador_id de la zona directamente.
    """
    await auth_module.require_admin(request, db)

    sin_cobrador = (
        db.query(models.Comprador)
        .join(models.Boleta, models.Boleta.comprador_id == models.Comprador.id)
        .filter(models.Boleta.cobrador_id == None, models.Comprador.zona_id != None)
        .distinct()
        .all()
    )

    arreglados = 0
    for c in sin_cobrador:
        zona = db.query(models.Zona).get(c.zona_id)
        if not zona or not zona.cobrador_id:
            continue
        for b in c.boletas:
            if not b.cobrador_id:
                b.cobrador_id = zona.cobrador_id
        arreglados += 1

    db.commit()
    from fastapi.responses import JSONResponse
    return JSONResponse({"ok": True, "arreglados": arreglados})


def _resolver_zona(zona_id: Optional[int], zona_nueva: str, db: Session) -> Optional[int]:
    """Devuelve el zona_id a usar: crea la zona si es nueva."""
    nombre = zona_nueva.strip().upper() if zona_nueva else ""
    if nombre:
        z = db.query(models.Zona).filter(models.Zona.nombre == nombre).first()
        if not z:
            z = models.Zona(nombre=nombre)
            db.add(z)
            db.flush()
        return z.id
    return zona_id or None


@router.post("/crear")
async def crear(
    request: Request,
    apellido_nombre: str = Form(...),
    direccion: str = Form(""),
    zona_id: Optional[str] = Form(None),
    zona_nueva: str = Form(""),
    telefono: str = Form(""),
    fecha_compra: Optional[str] = Form(None),
    boleta_id: Optional[int] = Form(None),
    vendedor_id: Optional[int] = Form(None),
    cuotas_pactadas: Optional[int] = Form(None),
    cuotas_anticipadas: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    await auth_module.require_user(request, db)
    zona = _resolver_zona(_parse_zona_id(zona_id), zona_nueva, db)

    # ── Vínculo zona ↔ vendedor (una zona tiene un solo vendedor) ─────────
    effective_vendedor_id = vendedor_id
    if zona:
        z_obj = db.query(models.Zona).get(zona)
        if z_obj:
            if z_obj.vendedor_id:
                # La zona ya tiene vendedor asignado → usarlo siempre
                effective_vendedor_id = z_obj.vendedor_id
            elif vendedor_id:
                # La zona no tiene vendedor → vincularlo ahora
                z_obj.vendedor_id = vendedor_id
                effective_vendedor_id = vendedor_id

    c = models.Comprador(
        apellido_nombre=apellido_nombre.strip().upper(),
        direccion=direccion.strip().upper() or None,
        zona_id=zona,
        telefono=telefono.strip() or None
    )
    db.add(c)
    db.flush()
    if boleta_id:
        b = db.query(models.Boleta).get(boleta_id)
        if b:
            b.comprador_id = c.id
            b.fecha_venta = date.fromisoformat(fecha_compra) if fecha_compra else b.fecha_venta
            if effective_vendedor_id:
                b.vendedor_id = effective_vendedor_id
            if cuotas_pactadas is not None and cuotas_pactadas > 0:
                b.cuotas_pactadas = cuotas_pactadas
            ant = cuotas_anticipadas if cuotas_anticipadas and cuotas_anticipadas > 0 else 1
            b.cuotas_anticipadas = ant
            b.cuotas_pagadas = ant   # las cuotas anticipadas ya están cobradas
            # Auto-asignar cobrador según la zona del comprador
            if c.zona_id:
                z = db.query(models.Zona).get(c.zona_id)
                if z and z.cobrador_id:
                    b.cobrador_id = z.cobrador_id
            if b.condicion == CondicionBoleta.SIN_VENDER:
                b.condicion = CondicionBoleta.VENDIDO
    db.commit()
    return RedirectResponse("/compradores/", status_code=302)


@router.get("/{comprador_id}/editar", response_class=HTMLResponse)
async def editar_form(comprador_id: int, request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    c = db.query(models.Comprador).get(comprador_id)
    if not c:
        raise HTTPException(404)
    zonas = db.query(models.Zona).order_by(models.Zona.nombre).all()
    vendedores = db.query(models.Vendedor).filter(models.Vendedor.activo == True).order_by(models.Vendedor.nombre).all()
    return templates.TemplateResponse(request, "comprador_editar.html", {
        "user": user,
        "comprador": c,
        "zonas": zonas,
        "vendedores": vendedores,
    })


@router.post("/{comprador_id}/editar")
async def editar(
    comprador_id: int, request: Request,
    apellido_nombre: str = Form(...),
    direccion: str = Form(""),
    zona_id: Optional[str] = Form(None),
    zona_nueva: str = Form(""),
    telefono: str = Form(""),
    fecha_compra: Optional[str] = Form(None),
    boleta_id: Optional[int] = Form(None),
    vendedor_id: Optional[int] = Form(None),
    cuotas_anticipadas: Optional[int] = Form(None),
    condicion: Optional[str] = Form(None),
    cuotas_pagadas_json: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    await auth_module.require_user(request, db)
    c = db.query(models.Comprador).get(comprador_id)
    if not c:
        raise HTTPException(404)
    zona = _resolver_zona(_parse_zona_id(zona_id), zona_nueva, db)
    c.apellido_nombre = apellido_nombre.strip().upper()
    c.direccion = direccion.strip().upper() or None
    c.zona_id = zona
    c.telefono = telefono.strip() or None

    # Actualizar cuotas_pagadas por boleta desde JSON
    if cuotas_pagadas_json:
        import json
        try:
            cuotas_map = json.loads(cuotas_pagadas_json)
            for b_exist in c.boletas:
                val = cuotas_map.get(str(b_exist.id))
                if val is not None:
                    nuevas = int(val)
                    if 0 <= nuevas <= (b_exist.cuotas_pactadas or 99):
                        b_exist.cuotas_pagadas = nuevas
        except (ValueError, TypeError, json.JSONDecodeError):
            pass

    # Actualizar condición en todas las boletas existentes
    if condicion:
        try:
            cond_enum = CondicionBoleta(condicion)
            for b_exist in c.boletas:
                b_exist.condicion = cond_enum
        except ValueError:
            pass

    # Actualizar vendedor en todas las boletas existentes
    if vendedor_id:
        for b_exist in c.boletas:
            b_exist.vendedor_id = vendedor_id

    # Auto-asignar cobrador y condición en boletas existentes si cambió la zona
    if zona:
        z = db.query(models.Zona).get(zona)
        for b_exist in c.boletas:
            if z and z.cobrador_id:
                b_exist.cobrador_id = z.cobrador_id
            if b_exist.condicion == CondicionBoleta.SIN_VENDER and b_exist.fecha_venta:
                b_exist.condicion = CondicionBoleta.VENDIDO

    # Agregar nueva boleta si se buscó una
    if boleta_id:
        b = db.query(models.Boleta).get(boleta_id)
        if b:
            b.comprador_id = comprador_id
            b.fecha_venta = date.fromisoformat(fecha_compra) if fecha_compra else b.fecha_venta
            if vendedor_id:
                b.vendedor_id = vendedor_id
            if cuotas_anticipadas and cuotas_anticipadas > 0:
                b.cuotas_anticipadas = cuotas_anticipadas
                b.cuotas_pagadas = cuotas_anticipadas
            if zona:
                z = db.query(models.Zona).get(zona)
                if z and z.cobrador_id:
                    b.cobrador_id = z.cobrador_id
            if b.condicion == CondicionBoleta.SIN_VENDER:
                b.condicion = CondicionBoleta.VENDIDO
    db.commit()
    return RedirectResponse("/compradores/", status_code=302)


@router.post("/{comprador_id}/eliminar")
async def eliminar(comprador_id: int, request: Request, db: Session = Depends(get_db)):
    await auth_module.require_user(request, db)
    c = db.query(models.Comprador).get(comprador_id)
    if c:
        for b in c.boletas:
            b.comprador_id = None
            b.condicion = CondicionBoleta.SIN_VENDER
            b.fecha_venta = None
        db.delete(c)
        db.commit()
    return RedirectResponse("/compradores/", status_code=302)


from fastapi.responses import JSONResponse


@router.get("/exportar")
async def exportar_excel(request: Request, db: Session = Depends(get_db)):
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from fastapi.responses import StreamingResponse

    await auth_module.require_user(request, db)

    compradores = (db.query(models.Comprador)
                   .join(models.Zona, isouter=True)
                   .order_by(models.Comprador.apellido_nombre)
                   .all())

    wb = Workbook()
    ws = wb.active
    ws.title = "Socios"

    # ── Estilos ──────────────────────────────────────────────────────────
    header_font    = Font(name="Arial", bold=True, color="FFFFFF", size=10)
    header_fill    = PatternFill("solid", start_color="C00000")
    center         = Alignment(horizontal="center", vertical="center")
    left           = Alignment(horizontal="left",   vertical="center")
    thin           = Side(style="thin", color="CCCCCC")
    border         = Border(left=thin, right=thin, top=thin, bottom=thin)
    data_font      = Font(name="Arial", size=9)
    alt_fill       = PatternFill("solid", start_color="FFF0F0")

    # ── Encabezados ──────────────────────────────────────────────────────
    headers = [
        ("N° Boleta",       12),
        ("Talonera",        10),
        ("Apellido y Nombre", 28),
        ("Dirección",       28),
        ("Zona",            14),
        ("Fecha Compra",    13),
        ("Vendedor",        18),
        ("Cobrador",        18),
        ("Teléfono",        13),
        ("Cuotas Pactadas",  8),
        ("Al Contado",       8),
        ("Condición",       13),
    ]

    for col_idx, (title, width) in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=title)
        cell.font      = header_font
        cell.fill      = header_fill
        cell.alignment = center
        cell.border    = border
        ws.column_dimensions[cell.column_letter].width = width

    ws.row_dimensions[1].height = 18
    ws.freeze_panes = "A2"

    # ── Datos ─────────────────────────────────────────────────────────────
    row_num = 2
    for c in compradores:
        for b in (c.boletas if c.boletas else [None]):
            fill = alt_fill if row_num % 2 == 0 else None

            def cell(col, value, align=left):
                ce = ws.cell(row=row_num, column=col, value=value)
                ce.font      = data_font
                ce.alignment = align
                ce.border    = border
                if fill:
                    ce.fill = fill
                return ce

            cell(1,  b.numero_principal if b else "",                                center)
            cell(2,  b.talonera.nombre  if b and b.talonera  else "",                center)
            cell(3,  c.apellido_nombre)
            cell(4,  c.direccion or "")
            cell(5,  c.zona.nombre      if c.zona            else "")
            cell(6,  b.fecha_venta.strftime("%d/%m/%Y") if b and b.fecha_venta else "", center)
            cell(7,  b.vendedor.nombre  if b and b.vendedor  else "")
            cell(8,  b.cobrador.nombre  if b and b.cobrador  else "")
            cell(9,  c.telefono or "")
            cell(10, b.cuotas_pactadas   if b else "",                               center)
            cell(11, b.cuotas_anticipadas if b else "",                              center)
            cell(12, b.condicion.value   if b and b.condicion else "",               center)

            ws.row_dimensions[row_num].height = 15
            row_num += 1

    # ── Autofilter ───────────────────────────────────────────────────────
    ws.auto_filter.ref = f"A1:L{row_num - 1}"

    # ── Stream ───────────────────────────────────────────────────────────
    try:
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
    except Exception as exc:
        from fastapi import HTTPException as _HTTPException
        raise _HTTPException(500, detail=f"Error generando el archivo Excel: {exc}")

    from datetime import date as dt
    filename = f"socios_{dt.today().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/{comprador_id}/detalle")
async def detalle_json(comprador_id: int, request: Request, db: Session = Depends(get_db)):
    await auth_module.require_user(request, db)
    c = db.query(models.Comprador).get(comprador_id)
    if not c:
        raise HTTPException(404)

    boletas = []
    for b in c.boletas:
        numeros = [b.numero_principal]
        if b.numeros_adicionales:
            for n in b.numeros_adicionales.split(", "):
                try:
                    numeros.append(int(n.strip()))
                except ValueError:
                    pass
        boletas.append({
            "id": b.id,
            "talonera": b.talonera.nombre if b.talonera else None,
            "numero_principal": b.numero_principal,
            "numeros_adicionales": b.numeros_adicionales or "",
            "todos_numeros": numeros,
            "fecha_venta": b.fecha_venta.strftime("%d/%m/%Y") if b.fecha_venta else None,
            "condicion": b.condicion,
            "vendedor": b.vendedor.nombre if b.vendedor else None,
            "cobrador": b.cobrador.nombre if b.cobrador else None,
            "cuotas_pactadas": b.cuotas_pactadas,
            "cuotas_pagadas": b.cuotas_pagadas,
            "total_pagado": b.total_pagado,
        })

    return JSONResponse({
        "id": c.id,
        "apellido_nombre": c.apellido_nombre,
        "direccion": c.direccion or "",
        "telefono": c.telefono or "",
        "zona": c.zona.nombre if c.zona else None,
        "boletas": boletas,
    })
