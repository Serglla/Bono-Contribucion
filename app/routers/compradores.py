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
async def listar(request: Request, db: Session = Depends(get_db),
                 q: str = "", pata: str = "", zona: str = ""):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'compradores', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')

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
    if zona:
        # Filtro por zona — acepta id numérico o nombre exacto
        try:
            zona_id_int = int(zona)
            query = query.filter(models.Comprador.zona_id == zona_id_int)
        except (TypeError, ValueError):
            query = query.filter(models.Zona.nombre == zona)
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

    # Conteos por talonera para los tabs. También traemos multiplicador para
    # calcular el total "Todas" ponderado (PATA 1 ×1, PATA 2 ×2, ...). Las
    # pestañas individuales siguen mostrando el conteo literal de socios.
    from sqlalchemy import func as sqlfunc
    taloneras_raw = (
        db.query(
            models.Talonera.nombre,
            sqlfunc.count(models.Comprador.id.distinct()),
            models.Talonera.multiplicador,
        )
        .join(models.Boleta, models.Boleta.talonera_id == models.Talonera.id)
        .join(models.Comprador, models.Comprador.id == models.Boleta.comprador_id)
        .group_by(models.Talonera.nombre, models.Talonera.multiplicador)
        .order_by(models.Talonera.nombre)
        .all()
    )
    tabs = [
        {"nombre": t[0], "total": t[1], "multiplicador": float(t[2] or 1.0)}
        for t in taloneras_raw
    ]
    # Total ponderado: 39 PATA1×1 + 9 PATA2×2 + 1 PATA3×3 + 1 PATA4×4 + 3 PATA8×8
    # = 39 + 18 + 3 + 4 + 24 = 88. Con PATA 0 (×0.67) el total puede tener decimales —
    # redondeo a entero al mostrar (decisión 11/05/2026).
    total_compradores = round(sum(t["total"] * t["multiplicador"] for t in tabs))

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
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'compradores', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
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


def _derivar_condicion(boleta) -> str:
    """Calcula la condición de una boleta a partir de su estado real.
    Reglas (en orden de prioridad):
      - BAJA: si la condición actual es BAJA (sticky, requiere acción manual)
      - CAJA (Al contado): si todas las cuotas están pagas
      - EN_COBRANZA: si tiene cobrador asignado Y está emplanillado
      - VENDIDO (Activo): si tiene comprador y fecha de venta
      - SIN_VENDER: cualquier otro caso
    """
    if not boleta:
        return "SIN_VENDER"
    if boleta.condicion and boleta.condicion.value == "BAJA":
        return "BAJA"
    if (boleta.cuotas_pactadas or 0) > 0 and (boleta.cuotas_pagadas or 0) >= boleta.cuotas_pactadas:
        return "CAJA"
    if boleta.cobrador_id and boleta.planilla_id:
        return "EN_COBRANZA"
    if boleta.comprador_id and boleta.fecha_venta:
        return "VENDIDO"
    return "SIN_VENDER"


@router.get("/{comprador_id}/editar", response_class=HTMLResponse)
async def editar_form(comprador_id: int, request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'compradores', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    c = db.query(models.Comprador).get(comprador_id)
    if not c:
        raise HTTPException(404)
    zonas = db.query(models.Zona).order_by(models.Zona.nombre).all()
    vendedores = db.query(models.Vendedor).filter(models.Vendedor.activo == True).order_by(models.Vendedor.nombre).all()
    cobradores = db.query(models.Cobrador).filter(models.Cobrador.activo == True).order_by(models.Cobrador.nombre).all()
    cond_derivada = _derivar_condicion(c.boletas[0]) if c.boletas else "SIN_VENDER"
    return templates.TemplateResponse(request, "comprador_editar.html", {
        "user": user,
        "comprador": c,
        "zonas": zonas,
        "vendedores": vendedores,
        "cobradores": cobradores,
        "cond_derivada": cond_derivada,
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
    cobrador_id: Optional[int] = Form(None),
    cuotas_anticipadas: Optional[int] = Form(None),
    condicion: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'compradores', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    c = db.query(models.Comprador).get(comprador_id)
    if not c:
        raise HTTPException(404)
    zona = _resolver_zona(_parse_zona_id(zona_id), zona_nueva, db)
    c.apellido_nombre = apellido_nombre.strip().upper()
    c.direccion = direccion.strip().upper() or None
    c.zona_id = zona
    c.telefono = telefono.strip() or None

    # Si el usuario marca BAJA explícitamente, queda BAJA (sticky).
    # Si no, la condición se re-deriva al final basada en el estado real.
    marcar_baja = (condicion == "BAJA")
    reactivar = (condicion == "REACTIVAR")
    if marcar_baja:
        for b_exist in c.boletas:
            b_exist.condicion = CondicionBoleta.BAJA
    elif reactivar:
        # Limpiar BAJA — se re-derivará al final
        for b_exist in c.boletas:
            if b_exist.condicion == CondicionBoleta.BAJA:
                b_exist.condicion = CondicionBoleta.SIN_VENDER

    # Actualizar vendedor en todas las boletas existentes
    if vendedor_id:
        for b_exist in c.boletas:
            b_exist.vendedor_id = vendedor_id

    # Cobrador: si el usuario eligió uno manualmente, tiene prioridad;
    # si no, auto-asignar según la zona
    effective_cobrador_id = cobrador_id
    if not effective_cobrador_id and zona:
        z = db.query(models.Zona).get(zona)
        if z and z.cobrador_id:
            effective_cobrador_id = z.cobrador_id

    if effective_cobrador_id:
        for b_exist in c.boletas:
            b_exist.cobrador_id = effective_cobrador_id

    # Condición en boletas si cambió la zona
    if zona:
        for b_exist in c.boletas:
            if b_exist.condicion == CondicionBoleta.SIN_VENDER and b_exist.fecha_venta:
                b_exist.condicion = CondicionBoleta.VENDIDO

    # Actualizar cuotas_pagadas por boleta (campos cpag_<id>)
    form_data = await request.form()
    for b_exist in c.boletas:
        key = f"cpag_{b_exist.id}"
        if key in form_data:
            try:
                nuevas = int(form_data[key])
                if 0 <= nuevas <= (b_exist.cuotas_pactadas or 99):
                    b_exist.cuotas_pagadas = nuevas
            except (ValueError, TypeError):
                pass

    # ── Modalidad CONTADO (ETAPA 2) ────────────────────────────────────────
    # Por boleta:
    #   modalidad_<id> = "cuotas" | "1pago" | "2pagos"
    #   te_<id>  / ne_<id>   = talonera_especial_id  / numero_especial   (CONTADO)
    #   te2_<id> / ne2_<id>  = talonera_especial_2_id / numero_especial_2 (CONTADO 2 VECES)
    # Reglas:
    #   - cuotas: limpia ambos slots
    #   - 1pago : asigna slot1 (CONTADO) y slot2 (CONTADO 2 VECES)
    #   - 2pagos: asigna solo slot2 (CONTADO 2 VECES); slot1 queda vacio
    # Validacion: si el numero ya esta asignado a OTRA boleta para esa talonera,
    # se rechaza (silencioso) y no se persiste.
    def _to_int(x):
        try:
            v = int(x)
            return v if v > 0 else None
        except (ValueError, TypeError):
            return None

    def _esta_libre(num: int, talonera_id: int, except_boleta_id: int) -> bool:
        """¿Está libre este número (no asignado a otra boleta) para esa talonera?"""
        if not num or not talonera_id:
            return False
        q = db.query(models.Boleta).filter(
            models.Boleta.id != except_boleta_id,
            (
                ((models.Boleta.talonera_especial_id == talonera_id) &
                 (models.Boleta.numero_especial == num)) |
                ((models.Boleta.talonera_especial_2_id == talonera_id) &
                 (models.Boleta.numero_especial_2 == num))
            ),
        ).first()
        return q is None

    for b_exist in c.boletas:
        modal = (form_data.get(f"modalidad_{b_exist.id}") or "").strip().lower()
        if modal not in ("cuotas", "1pago", "2pagos"):
            continue  # no se mando, no tocamos
        te1 = _to_int(form_data.get(f"te_{b_exist.id}"))
        ne1 = _to_int(form_data.get(f"ne_{b_exist.id}"))
        te2 = _to_int(form_data.get(f"te2_{b_exist.id}"))
        ne2 = _to_int(form_data.get(f"ne2_{b_exist.id}"))

        if modal == "cuotas":
            b_exist.numero_especial = None
            b_exist.talonera_especial_id = None
            b_exist.numero_especial_2 = None
            b_exist.talonera_especial_2_id = None
        elif modal == "1pago":
            # slot 1
            if te1 and ne1 and _esta_libre(ne1, te1, b_exist.id):
                b_exist.numero_especial = ne1
                b_exist.talonera_especial_id = te1
            # slot 2
            if te2 and ne2 and _esta_libre(ne2, te2, b_exist.id):
                b_exist.numero_especial_2 = ne2
                b_exist.talonera_especial_2_id = te2
        elif modal == "2pagos":
            # solo slot 2
            b_exist.numero_especial = None
            b_exist.talonera_especial_id = None
            if te2 and ne2 and _esta_libre(ne2, te2, b_exist.id):
                b_exist.numero_especial_2 = ne2
                b_exist.talonera_especial_2_id = te2

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
            if effective_cobrador_id:
                b.cobrador_id = effective_cobrador_id

    # Re-derivar condición al final (salvo BAJA explícita que ya quedó seteada)
    if not marcar_baja:
        for b_exist in c.boletas:
            if b_exist.condicion != CondicionBoleta.BAJA:
                derivada = _derivar_condicion(b_exist)
                try:
                    b_exist.condicion = CondicionBoleta(derivada)
                except ValueError:
                    pass

    db.commit()
    return RedirectResponse("/compradores/", status_code=302)


@router.post("/{comprador_id}/boleta/{boleta_id}/cuotas")
async def actualizar_cuotas(
    comprador_id: int, boleta_id: int, request: Request,
    cuotas_pagadas: int = Form(...),
    db: Session = Depends(get_db)
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'compradores', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    b = db.query(models.Boleta).filter(models.Boleta.id == boleta_id).first()
    if not b:
        from fastapi.responses import JSONResponse
        return JSONResponse({"ok": False, "error": f"boleta {boleta_id} no encontrada"}, status_code=404)
    b.cuotas_pagadas = cuotas_pagadas
    db.commit()
    from fastapi.responses import JSONResponse
    return JSONResponse({"ok": True, "cuotas_pagadas": b.cuotas_pagadas})


@router.post("/{comprador_id}/eliminar")
async def eliminar(comprador_id: int, request: Request, db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'compradores', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
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


@router.get("/boleta/{boleta_id}/contado-disponibles")
async def contado_disponibles(boleta_id: int, request: Request, db: Session = Depends(get_db)):
    """Devuelve los numeros CONTADO y CONTADO 2 VECES que el vendedor de la
    boleta tiene en mano (entregados via EntregaCaja menos los ya asignados a
    cualquier boleta). Usado por la pantalla de editar comprador para que el
    operador elija manualmente que numero del pool del vendedor le corresponde
    a este socio segun la modalidad de pago.
    Respuesta:
      {
        "ok": true,
        "vendedor_id": int|null,
        "vendedor_nombre": str|null,
        "current": {"numero_especial": int|null, "talonera_especial_id": int|null,
                    "numero_especial_2": int|null, "talonera_especial_2_id": int|null},
        "taloneras_contado": [
          {"id": int, "nombre": str, "color": str, "num_digitos": int,
           "rol": "CONTADO"|"CONTADO_2"|"OTRO",
           "numeros_libres": [int, ...]},
          ...
        ]
      }
    rol se infiere por el nombre: si el nombre normalizado contiene "2" => CONTADO_2,
    si arranca con "CONTADO" sin numero => CONTADO, sino OTRO.
    """
    from fastapi.responses import JSONResponse
    await auth_module.require_user(request, db)
    b = db.query(models.Boleta).get(boleta_id)
    if not b:
        raise HTTPException(404, "Boleta no encontrada")

    vid = b.vendedor_id
    v = db.query(models.Vendedor).get(vid) if vid else None

    # Taloneras CONTADO existentes
    taloneras_c = db.query(models.Talonera).filter(models.Talonera.tipo == "CONTADO").all()

    def _rol(nombre: str) -> str:
        import re as _re
        nm = (nombre or "").strip().upper()
        if not nm.startswith("CONTADO"):
            return "OTRO"
        m = _re.search(r"(\d+)", nm)
        if m and int(m.group(1)) >= 2:
            return "CONTADO_2"
        return "CONTADO"

    # Entregas de este vendedor para taloneras CONTADO
    entregas_v = []
    if vid:
        entregas_v = db.query(models.EntregaCaja).filter_by(vendedor_id=vid).all()

    # Map nombre normalizado -> talonera para hacer match con entregas
    norm_map = {(t.nombre or "").strip().lower(): t for t in taloneras_c}

    # Para cada talonera CONTADO, computar los numeros entregados a este vendedor
    out_taloneras = []
    for t in taloneras_c:
        nombre_norm = (t.nombre or "").strip().lower()
        rangos = []
        for e in entregas_v:
            if (e.talonera_nombre or "").strip().lower() == nombre_norm:
                rangos.append((int(e.desde), int(e.hasta)))
        nums_entregados = set()
        for d, h in rangos:
            if h < d:
                continue
            nums_entregados.update(range(d, h + 1))
        if not nums_entregados:
            # El vendedor no tiene numeros de esta talonera; si la boleta YA
            # tiene asignado un numero de esta talonera lo incluimos igual.
            asignados_aqui = set()
            if b.talonera_especial_id == t.id and b.numero_especial:
                asignados_aqui.add(b.numero_especial)
            if b.talonera_especial_2_id == t.id and b.numero_especial_2:
                asignados_aqui.add(b.numero_especial_2)
            if not asignados_aqui:
                continue
            libres = sorted(asignados_aqui)
        else:
            # Numeros ya asignados a alguna boleta para esta talonera
            asignados_rows = db.query(
                models.Boleta.numero_especial,
                models.Boleta.talonera_especial_id,
                models.Boleta.numero_especial_2,
                models.Boleta.talonera_especial_2_id,
                models.Boleta.id,
            ).filter(
                ((models.Boleta.talonera_especial_id == t.id) &
                 models.Boleta.numero_especial.isnot(None)) |
                ((models.Boleta.talonera_especial_2_id == t.id) &
                 models.Boleta.numero_especial_2.isnot(None))
            ).all()
            asignados_otros = set()
            for ne, tei, ne2, tei2, bid in asignados_rows:
                if bid == b.id:
                    continue  # no contar lo que ya asigne a esta misma boleta
                if tei == t.id and ne is not None:
                    asignados_otros.add(int(ne))
                if tei2 == t.id and ne2 is not None:
                    asignados_otros.add(int(ne2))
            libres = sorted(nums_entregados - asignados_otros)

        out_taloneras.append({
            "id": t.id,
            "nombre": t.nombre,
            "color": t.color or "#fff8e1",
            "num_digitos": t.num_digitos or 3,
            "rol": _rol(t.nombre),
            "numeros_libres": libres,
        })

    # Orden: CONTADO primero, CONTADO_2 segundo, OTRO al final
    out_taloneras.sort(key=lambda x: (
        0 if x["rol"] == "CONTADO" else 1 if x["rol"] == "CONTADO_2" else 2,
        x["nombre"],
    ))

    return JSONResponse({
        "ok": True,
        "vendedor_id": vid,
        "vendedor_nombre": v.nombre if v else None,
        "current": {
            "numero_especial": b.numero_especial,
            "talonera_especial_id": b.talonera_especial_id,
            "numero_especial_2": b.numero_especial_2,
            "talonera_especial_2_id": b.talonera_especial_2_id,
        },
        "taloneras_contado": out_taloneras,
    })


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
