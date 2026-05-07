from fastapi import HTTPException, APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
import json
from .. import models, auth as auth_module
from ..models import CondicionBoleta
from ..templates_config import templates
from ..database import get_db

router = APIRouter(prefix="/vendedores", tags=["vendedores"])



def _stats_bulk(db):
    """Un solo query SQL con conteos por vendedor y condicion.
    Para CAJA distingue entre sin liquidar y liquidado-pendiente-comprador,
    usando func.count(col) que solo cuenta valores no-NULL.
    """
    rows = db.query(
        models.Boleta.vendedor_id,
        models.Boleta.condicion,
        func.count(models.Boleta.id).label("total"),
        func.count(models.Boleta.liquidacion_vendedor_id).label("con_liq")
    ).filter(
        models.Boleta.vendedor_id.isnot(None)
    ).group_by(models.Boleta.vendedor_id, models.Boleta.condicion).all()

    stats = {}
    for vid, cond, total, con_liq in rows:
        if vid not in stats:
            stats[vid] = {"caja": 0, "liq_pendiente": 0, "vendido": 0, "baja": 0}
        if cond == CondicionBoleta.CAJA:
            stats[vid]["caja"] = total - con_liq       # CAJA sin liquidar
            stats[vid]["liq_pendiente"] = con_liq      # CAJA con liq, pendiente comprador
        elif cond == CondicionBoleta.VENDIDO:
            stats[vid]["vendido"] = total
        elif cond == CondicionBoleta.BAJA:
            stats[vid]["baja"] = total
    return stats


@router.get("/", response_class=HTMLResponse)
async def listar(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, "vendedores", "ver"):
        raise HTTPException(403, "Sin permiso")
    vendedores = db.query(models.Vendedor).order_by(
        models.Vendedor.es_jefe_equipo.desc(), models.Vendedor.nombre
    ).all()
    taloneras = db.query(models.Talonera).order_by(
        models.Talonera.nombre, models.Talonera.numero_inicio
    ).all()
    # Taloneras COMUN: PATA 1, PATA 2, ... — generan Boletas reales
    grupos_talonera = list(dict.fromkeys(
        t.nombre for t in taloneras if (t.tipo or "COMUN") == "COMUN"
    ))
    # Taloneras CONTADO: pool de números especiales. No tienen Boletas propias,
    # pero el vendedor recibe físicamente el rango y lo entrega a los socios que pagan al contado.
    # Se muestran con su rango para que el usuario sepa qué números existen al elegirlos.
    grupos_contado = []
    for t in taloneras:
        if (t.tipo or "COMUN") != "CONTADO":
            continue
        nd = t.num_digitos or 3
        fmt = "{:0" + str(nd) + "d}"
        grupos_contado.append({
            "nombre": t.nombre,
            "label": f"{t.nombre} ({fmt.format(t.numero_inicio or 0)}–{fmt.format(t.numero_fin or 0)})",
            "inicio": t.numero_inicio,
            "fin": t.numero_fin,
            "num_digitos": nd,
        })
    entregas = db.query(models.EntregaCaja).order_by(
        models.EntregaCaja.fecha.desc()
    ).limit(200).all()
    stats = _stats_bulk(db)
    jefe = db.query(models.Vendedor).filter_by(es_jefe_equipo=True, activo=True).first()
    # Set de nombres CONTADO para que el template marque visualmente esas filas
    nombres_contado = sorted({g["nombre"] for g in grupos_contado})
    return templates.TemplateResponse(request, "vendedores.html", {
        "user": user,
        "vendedores": vendedores,
        "grupos_talonera": grupos_talonera,
        "grupos_contado": grupos_contado,
        "nombres_contado": nombres_contado,
        "entregas": entregas,
        "stats": stats,
        "jefe": jefe,
    })


@router.get("/{vid}/detalle", response_class=HTMLResponse)
async def detalle(vid: int, request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, "vendedores", "ver"):
        raise HTTPException(403, "Sin permiso")
    v = db.query(models.Vendedor).get(vid)
    if not v:
        raise HTTPException(404, "Vendedor no encontrado")

    # Lista completa de vendedores (para reasignacion en el modal de Entrega a Caja)
    vendedores_all = db.query(models.Vendedor).order_by(
        models.Vendedor.es_jefe_equipo.desc(), models.Vendedor.nombre
    ).all()

    # Taloneras COMUN y CONTADO para el selector de Entrega a Caja
    taloneras = db.query(models.Talonera).order_by(
        models.Talonera.nombre, models.Talonera.numero_inicio
    ).all()
    grupos_talonera = list(dict.fromkeys(
        t.nombre for t in taloneras if (t.tipo or "COMUN") == "COMUN"
    ))
    grupos_contado = []
    for t in taloneras:
        if (t.tipo or "COMUN") != "CONTADO":
            continue
        nd = t.num_digitos or 3
        fmt = "{:0" + str(nd) + "d}"
        grupos_contado.append({
            "nombre": t.nombre,
            "label": f"{t.nombre} ({fmt.format(t.numero_inicio or 0)}–{fmt.format(t.numero_fin or 0)})",
            "inicio": t.numero_inicio,
            "fin": t.numero_fin,
            "num_digitos": nd,
        })
    nombres_contado = sorted({g["nombre"] for g in grupos_contado})

    # Historial de entregas a caja para ESTE vendedor
    entregas_vendedor = db.query(models.EntregaCaja).filter_by(
        vendedor_id=vid
    ).order_by(models.EntregaCaja.fecha.desc()).limit(200).all()

    boletas = db.query(models.Boleta).filter_by(vendedor_id=vid).all()

    # Agrupar por pata
    patas = {}
    for b in boletas:
        pata_nombre = b.talonera.nombre if b.talonera else "?"
        pata_color  = b.talonera.color  if b.talonera else "#ffffff"
        if pata_nombre not in patas:
            patas[pata_nombre] = {"color": pata_color, "boletas": []}
        patas[pata_nombre]["boletas"].append({
            "num":     b.numero_principal,
            "cond":    b.condicion.value if b.condicion else "?",
            "liq":     b.liquidacion_vendedor_id is not None,
            "contado": b.numero_especial is not None,
        })
    for p in patas:
        patas[p]["boletas"].sort(key=lambda x: x["num"])

    # ── CONTADO (pool): agregar los numeros entregados al vendedor que aun
    # no fueron asignados a ninguna boleta. Estos viven en una talonera
    # tipo CONTADO y se entregan via EntregaCaja con talonera_nombre = el
    # nombre de la talonera CONTADO. Mientras no se asignen a una boleta
    # (numero_especial) permanecen en mano del vendedor, pero NO son
    # liquidables (no tienen valor_cuota propio).
    # Match case-insensitive y con trim por si el nombre se cargo distinto
    # entre la talonera y la entrega (ej. "CONTADO 2 VECES" vs "CONTADO 2 veces").
    contado_taloneras_norm: dict = {}
    for t in taloneras:
        if (t.tipo or "COMUN") == "CONTADO":
            key = (t.nombre or "").strip().lower()
            contado_taloneras_norm[key] = t
    ranges_por_talonera: dict = {}
    for e in entregas_vendedor:
        nm = (e.talonera_nombre or "").strip().lower()
        t_match = contado_taloneras_norm.get(nm)
        if t_match:
            # Agrupamos por el nombre canonico (el de la talonera actual)
            ranges_por_talonera.setdefault(t_match.nombre, []).append(
                (int(e.desde), int(e.hasta))
            )
    for nombre_c, rangos in ranges_por_talonera.items():
        # Re-localizamos la talonera por su nombre canonico
        t = next((x for x in taloneras if x.nombre == nombre_c), None)
        if t is None:
            continue
        nums_entregados = set()
        for d, h in rangos:
            if h < d:
                continue
            nums_entregados.update(range(d, h + 1))
        if not nums_entregados:
            continue
        # Numeros del pool ya asignados a alguna boleta (vendido al contado)
        asignados_rows = db.query(models.Boleta.numero_especial).filter(
            models.Boleta.talonera_especial_id == t.id,
            models.Boleta.numero_especial.in_(list(nums_entregados)),
        ).all()
        nums_asignados = {r[0] for r in asignados_rows if r[0] is not None}
        pendientes_pool = sorted(nums_entregados - nums_asignados)
        if not pendientes_pool:
            continue
        if nombre_c not in patas:
            patas[nombre_c] = {"color": t.color or "#fff8e1", "boletas": []}
        for n in pendientes_pool:
            patas[nombre_c]["boletas"].append({
                "num":     n,
                "cond":    "CAJA",
                "liq":     False,
                "contado": True,
            })
        patas[nombre_c]["boletas"].sort(key=lambda x: x["num"])

    # Ordenar las PATAs: primero las que tienen numero (PATA 1, 2, 3, ...)
    # ordenadas numericamente; luego las demas (ej: CONTADO, VOLAS) alfabeticamente.
    import re as _re
    def _pata_sort_key(nombre: str):
        m = _re.search(r"(\d+)", nombre or "")
        if m:
            return (0, int(m.group(1)), nombre or "")
        return (1, 0, nombre or "")
    patas = dict(sorted(patas.items(), key=lambda kv: _pata_sort_key(kv[0])))

    # Boletas CAJA sin liquidar = las que el vendedor aun tiene en mano
    # Boletas CAJA con liq_id  = vendidas por el vendedor, pendientes de cargar comprador
    # Boletas VENDIDO           = ya cargadas en el sistema con datos del comprador
    pendientes = [b for b in boletas
                  if b.condicion == CondicionBoleta.CAJA
                  and b.liquidacion_vendedor_id is None]

    # Datos individuales de cada boleta pendiente → para el modal de selección manual
    pendientes_json = json.dumps([
        {
            "id":         b.id,
            "num":        b.numero_principal,
            "pata":       b.talonera.nombre     if b.talonera else "?",
            "color":      b.talonera.color      if b.talonera else "#cccccc",
            "valor_cuota":b.talonera.valor_cuota if b.talonera else 0.0,
            "num_cuotas": (b.talonera.num_cuotas if b.talonera and b.talonera.num_cuotas else 12),
            "contado":    b.numero_especial is not None,
        }
        for b in sorted(pendientes, key=lambda x: (x.talonera.nombre if x.talonera else "", x.numero_principal))
    ])

    liquidaciones = db.query(models.LiquidacionVendedor).filter_by(
        vendedor_id=vid
    ).order_by(models.LiquidacionVendedor.fecha.desc()).all()

    can_edit = auth_module.has_permission(user, "vendedores", "editar")

    return templates.TemplateResponse(request, "vendedor_detalle.html", {
        "user": user, "v": v, "patas": patas,
        "pendientes_json": pendientes_json,
        "liquidaciones": liquidaciones,
        "can_edit": can_edit,
        "pendientes_count": len(pendientes),
        "vendedores_all": vendedores_all,
        "grupos_talonera": grupos_talonera,
        "grupos_contado": grupos_contado,
        "nombres_contado": nombres_contado,
        "entregas_vendedor": entregas_vendedor,
    })


@router.post("/{vid}/liquidar")
async def liquidar(
    vid: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Liquida al vendedor por las boletas seleccionadas manualmente.
    Modelo de comision:
      - Cuotas: el vendedor se queda con cuota 1 (= valor_cuota por boleta)
                + comision_cuotas_pct% sobre el monto de cuotas
      - Contados: comision_contados_pct% sobre el valor TOTAL de la talonera
                  (num_cuotas × valor_cuota) por boleta contado
    """
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    v = db.query(models.Vendedor).get(vid)
    if not v:
        raise HTTPException(404)

    form = await request.form()
    boleta_ids_raw = form.getlist("boleta_ids")
    comision_cuotas_pct   = float(form.get("comision_cuotas_pct",   5.0))
    comision_contados_pct = float(form.get("comision_contados_pct", 30.0))
    observacion           = (form.get("observacion") or "").strip()

    if not boleta_ids_raw:
        return RedirectResponse(f"/vendedores/{vid}/detalle?msg=sin_pendientes", status_code=302)

    boleta_ids = [int(x) for x in boleta_ids_raw]

    # Verificar que las boletas pertenezcan al vendedor y estén en CAJA sin liquidar
    boletas_sel = db.query(models.Boleta).filter(
        models.Boleta.id.in_(boleta_ids),
        models.Boleta.vendedor_id == vid,
        models.Boleta.condicion == CondicionBoleta.CAJA,
        models.Boleta.liquidacion_vendedor_id.is_(None),
    ).all()

    if not boletas_sel:
        return RedirectResponse(f"/vendedores/{vid}/detalle?msg=sin_pendientes", status_code=302)

    cuotas   = [b for b in boletas_sel if b.numero_especial is None]
    contados = [b for b in boletas_sel if b.numero_especial is not None]

    # Cuota 1: lo que el vendedor ya cobró directamente del comprador (valor_cuota × N)
    cuota_1_total  = sum((b.talonera.valor_cuota if b.talonera else 0.0) for b in cuotas)

    # Comisión adicional sobre cuotas (% sobre monto de cuotas)
    monto_cuotas   = sum((b.talonera.valor_cuota if b.talonera else 0.0) for b in cuotas)
    com_cuotas     = round(monto_cuotas * comision_cuotas_pct / 100, 2)

    # Comisión contado: % sobre el valor TOTAL de la talonera (num_cuotas × valor_cuota)
    monto_contados = sum(
        ((b.talonera.num_cuotas or 12) * (b.talonera.valor_cuota if b.talonera else 0.0))
        for b in contados
    )
    com_contados   = round(monto_contados * comision_contados_pct / 100, 2)

    total = round(cuota_1_total + com_cuotas + com_contados, 2)

    liq = models.LiquidacionVendedor(
        vendedor_id=vid,
        cuotas_vendidas=len(cuotas),
        cuota_1_total=cuota_1_total,
        monto_cuotas=monto_cuotas,
        comision_cuotas_pct=comision_cuotas_pct,
        comision_cuotas=com_cuotas,
        contados_vendidos=len(contados),
        monto_contados=monto_contados,
        comision_contados_pct=comision_contados_pct,
        comision_contados=com_contados,
        total_comision=total,
        observacion=observacion or None,
    )
    db.add(liq)
    db.flush()

    # Marcar boletas: conservan condicion CAJA hasta que se cargue el comprador
    for b in boletas_sel:
        b.liquidacion_vendedor_id = liq.id
    db.commit()

    return RedirectResponse(f"/vendedores/{vid}/detalle?msg=liquidado", status_code=302)


@router.post("/entrega-caja")
async def entrega_caja(
    request: Request,
    talonera_nombre: str = Form(...),
    desde: int = Form(...),
    hasta: int = Form(...),
    vendedor_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    if hasta < desde:
        return JSONResponse({"ok": False, "error": "Rango invalido"}, status_code=400)

    if not vendedor_id:
        jefe = db.query(models.Vendedor).filter_by(es_jefe_equipo=True, activo=True).first()
        vendedor_id = jefe.id if jefe else None

    taloneras_match = db.query(models.Talonera).filter_by(nombre=talonera_nombre).all()
    if not taloneras_match:
        return JSONResponse({"ok": False, "error": "Talonera no encontrada"}, status_code=404)
    talonera_ids = [t.id for t in taloneras_match]
    # ¿Es una talonera CONTADO (especial)? — no tiene Boletas propias en la DB.
    # En ese caso, la entrega solo registra que el vendedor recibió ese rango de
    # números especiales; no se modifica ninguna Boleta porque aún no existen.
    es_contado = all((t.tipo or "COMUN") == "CONTADO" for t in taloneras_match)

    nuevas = 0
    reasignadas = 0
    vendedores_origen = []  # ids de vendedores que perdieron boletas (para refrescar UI)

    if es_contado:
        # Entrega de talonera especial: usar el tamaño del rango como "boletas afectadas"
        nuevas = max(0, hasta - desde + 1)
    else:
        # 1) SIN_VENDER -> CAJA (asigna vendedor)
        update_data = {"condicion": CondicionBoleta.CAJA}
        if vendedor_id:
            update_data["vendedor_id"] = vendedor_id

        nuevas = db.query(models.Boleta).filter(
            models.Boleta.talonera_id.in_(talonera_ids),
            models.Boleta.numero_principal >= desde,
            models.Boleta.numero_principal <= hasta,
            models.Boleta.condicion == CondicionBoleta.SIN_VENDER,
        ).update(update_data, synchronize_session=False)

        # 2) Reasignar boletas que ya estan en CAJA sin liquidar a otro vendedor.
        #    Solo si se especifico vendedor_id; no se tocan las liquidadas.
        if vendedor_id:
            q_reasign = db.query(models.Boleta).filter(
                models.Boleta.talonera_id.in_(talonera_ids),
                models.Boleta.numero_principal >= desde,
                models.Boleta.numero_principal <= hasta,
                models.Boleta.condicion == CondicionBoleta.CAJA,
                models.Boleta.liquidacion_vendedor_id.is_(None),
                (models.Boleta.vendedor_id.is_(None)) | (models.Boleta.vendedor_id != vendedor_id),
            )
            # capturo los vendedores origen ANTES del update
            vendedores_origen = [
                vid for (vid,) in q_reasign.with_entities(models.Boleta.vendedor_id).distinct().all()
                if vid is not None
            ]
            reasignadas = q_reasign.update({"vendedor_id": vendedor_id}, synchronize_session=False)

    total = nuevas + reasignadas

    # No ensuciar el historial si no hubo movimientos
    if total == 0:
        db.rollback()
        return JSONResponse({
            "ok": True,
            "nuevas": 0,
            "reasignadas": 0,
            "total": 0,
            "actualizadas": 0,  # backward compat
            "entrega_id": None,
            "vendedor_nombre": None,
            "vendedor_id": vendedor_id,
            "vendedores_origen": [],
            "es_contado": es_contado,
        })

    entrega = models.EntregaCaja(
        talonera_nombre=talonera_nombre,
        desde=desde,
        hasta=hasta,
        boletas_afectadas=total,
        usuario_id=_perm_user.id,
        vendedor_id=vendedor_id,
    )
    db.add(entrega)
    db.commit()
    db.refresh(entrega)

    vend_nombre = entrega.vendedor.nombre if entrega.vendedor else None
    return JSONResponse({
        "ok": True,
        "nuevas": nuevas,
        "reasignadas": reasignadas,
        "total": total,
        "actualizadas": total,  # backward compat
        "entrega_id": entrega.id,
        "vendedor_nombre": vend_nombre,
        "vendedor_id": vendedor_id,
        "vendedores_origen": vendedores_origen,
        "es_contado": es_contado,
    })


@router.post("/entrega-caja/{entrega_id}/editar")
async def editar_entrega(
    entrega_id: int, request: Request,
    talonera_nombre: str = Form(...),
    desde: int = Form(...),
    hasta: int = Form(...),
    observacion: str = Form(""),
    vendedor_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    e = db.query(models.EntregaCaja).get(entrega_id)
    if not e:
        raise HTTPException(404)
    e.talonera_nombre = talonera_nombre
    e.desde = desde
    e.hasta = hasta
    e.observacion = observacion.strip() or None
    if vendedor_id:
        e.vendedor_id = vendedor_id
    db.commit()
    return RedirectResponse("/vendedores/", status_code=302)


@router.post("/entrega-caja/{entrega_id}/eliminar")
async def eliminar_entrega(entrega_id: int, request: Request, db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    e = db.query(models.EntregaCaja).get(entrega_id)
    if e:
        db.delete(e)
        db.commit()
    return RedirectResponse("/vendedores/", status_code=302)


@router.post("/{vid}/toggle-jefe")
async def toggle_jefe(vid: int, request: Request, db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    v = db.query(models.Vendedor).get(vid)
    if not v:
        raise HTTPException(404)
    if v.es_jefe_equipo:
        v.es_jefe_equipo = False
    else:
        db.query(models.Vendedor).filter_by(es_jefe_equipo=True).update(
            {"es_jefe_equipo": False}, synchronize_session=False
        )
        v.es_jefe_equipo = True
    db.commit()
    return RedirectResponse("/vendedores/", status_code=302)


@router.post("/crear")
async def crear(
    request: Request,
    nombre: str = Form(...),
    telefono: str = Form(""),
    db: Session = Depends(get_db),
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    v = models.Vendedor(nombre=nombre.strip().upper(), telefono=telefono.strip() or None)
    db.add(v)
    db.commit()
    return RedirectResponse("/vendedores/", status_code=302)


@router.post("/{vid}/toggle")
async def toggle(vid: int, request: Request, db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
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
    db: Session = Depends(get_db),
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    v = db.query(models.Vendedor).get(vid)
    if v:
        v.nombre = nombre.strip().upper()
        v.telefono = telefono.strip() or None
        db.commit()
    return RedirectResponse("/vendedores/", status_code=302)
