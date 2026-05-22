from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session, joinedload
from datetime import date
from typing import Optional
from .. import models, auth as auth_module
from ..templates_config import templates
from ..database import get_db

router = APIRouter(prefix="/contabilidad", tags=["contabilidad"])

MESES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
         "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

CATEGORIAS = ["PREMIO", "VIAJE", "ALOJAMIENTO", "OTRO"]


def _get_config(db, clave, default=0.0):
    row = db.query(models.ConfigBono).filter_by(clave=clave).first()
    return row.valor_float if row else default


def _set_config(db, clave, valor):
    row = db.query(models.ConfigBono).filter_by(clave=clave).first()
    if row:
        row.valor_float = valor
    else:
        db.add(models.ConfigBono(clave=clave, valor_float=valor))
    db.commit()


@router.get("/", response_class=HTMLResponse)
async def contabilidad_index(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "Solo administradores")

    boletas = (
        db.query(models.Boleta)
        .filter(
            models.Boleta.comprador_id.isnot(None),
            models.Boleta.condicion != models.CondicionBoleta.BAJA,
        )
        .options(joinedload(models.Boleta.talonera))
        .all()
    )

    total_recaudado = sum(b.total_pagado or 0 for b in boletas)
    total_esperado  = sum(
        (b.cuotas_pactadas or 0) * (b.talonera.valor_cuota if b.talonera else 0)
        for b in boletas
    )
    falta_cobrar = sum(
        max(0, (b.cuotas_pactadas or 0) - (b.cuotas_pagadas or 0))
        * (b.talonera.valor_cuota if b.talonera else 0)
        for b in boletas
    )
    pct_avance = round(total_recaudado / total_esperado * 100, 1) if total_esperado else 0

    liqs_v = (
        db.query(models.LiquidacionVendedor)
        .options(joinedload(models.LiquidacionVendedor.vendedor))
        .order_by(models.LiquidacionVendedor.fecha)
        .all()
    )
    # _lv_total: incluye cuota_1_total para registros viejos donde comision_cuotas=0
    def _lv_total(lv):
        base = lv.total_comision or 0
        # registros viejos: comision_cuotas=0 pero cuota_1_total tiene el valor correcto
        if not (lv.comision_cuotas or 0) and (lv.cuota_1_total or 0):
            base += lv.cuota_1_total
        return base
    total_com_vendedores = sum(_lv_total(lv) for lv in liqs_v)

    vendedores_dict = {}
    for lv in liqs_v:
        vid    = lv.vendedor_id
        nombre = lv.vendedor.nombre if lv.vendedor else "---"
        if vid not in vendedores_dict:
            vendedores_dict[vid] = {"nombre": nombre, "total": 0.0, "liquidaciones": []}
        com = _lv_total(lv)
        vendedores_dict[vid]["total"] += com
        fecha_str = lv.fecha.strftime("%d/%m/%Y") if lv.fecha else ""
        vendedores_dict[vid]["liquidaciones"].append({
            "fecha":        fecha_str,
            "mes_nombre":   MESES[lv.fecha.month - 1] if lv.fecha else "",
            "anio":         lv.fecha.year if lv.fecha else 0,
            "cuotas":       int(round(lv.cuotas_equiv or lv.cuotas_vendidas or 0)),
            "com_cuotas":   (lv.comision_cuotas or 0) if (lv.comision_cuotas or 0) > 0 else (lv.cuota_1_total or 0),
            "com_contados": lv.comision_contados or 0,
            "total":        com,
        })
    vendedores_list = sorted(vendedores_dict.values(), key=lambda x: -x["total"])

    liqs_c = (
        db.query(models.Liquidacion)
        .options(
            joinedload(models.Liquidacion.planilla)
            .joinedload(models.Planilla.cobrador)
        )
        .order_by(models.Liquidacion.fecha)
        .all()
    )
    total_com_cobradores = sum(lc.comision or 0 for lc in liqs_c)

    cobradores_dict = {}
    for lc in liqs_c:
        p = lc.planilla
        if not p:
            continue
        cid    = p.cobrador_id
        nombre = p.cobrador.nombre if p.cobrador else "---"
        if cid not in cobradores_dict:
            cobradores_dict[cid] = {
                "nombre": nombre,
                "total_monto": 0.0, "total_comision": 0.0, "total_neto": 0.0,
                "liquidaciones": [],
            }
        cobradores_dict[cid]["total_monto"]    += lc.monto_total or 0
        cobradores_dict[cid]["total_comision"] += lc.comision or 0
        cobradores_dict[cid]["total_neto"]     += lc.neto or 0
        cobradores_dict[cid]["liquidaciones"].append({
            "fecha":      lc.fecha.strftime("%d/%m/%Y") if lc.fecha else "",
            "mes_nombre": MESES[p.mes - 1] if p and p.mes else "",
            "anio":       p.anio if p else 0,
            "monto":      lc.monto_total or 0,
            "comision":   lc.comision or 0,
            "neto":       lc.neto or 0,
        })
    cobradores_list = sorted(cobradores_dict.values(), key=lambda x: -x["total_comision"])

    rec_por_mes = {}
    for lc in liqs_c:
        p = lc.planilla
        if not p:
            continue
        key = (p.anio, p.mes)
        if key not in rec_por_mes:
            rec_por_mes[key] = {
                "mes_nombre": MESES[p.mes - 1],
                "anio": p.anio,
                "monto": 0.0, "comision": 0.0, "neto": 0.0,
            }
        rec_por_mes[key]["monto"]    += lc.monto_total or 0
        rec_por_mes[key]["comision"] += lc.comision or 0
        rec_por_mes[key]["neto"]     += lc.neto or 0
    rec_por_mes_list = sorted(rec_por_mes.values(), key=lambda x: (x["anio"], x["mes_nombre"]))

    pago_mensual_bomberos = _get_config(db, "pago_mensual_bomberos", 0.0)
    meses_liquidados      = len(rec_por_mes)
    total_bomberos        = pago_mensual_bomberos * meses_liquidados

    gastos = (
        db.query(models.GastoContabilidad)
        .order_by(models.GastoContabilidad.fecha.desc().nullslast(),
                  models.GastoContabilidad.id.desc())
        .all()
    )
    total_gastos = sum(g.monto or 0 for g in gastos)

    gastos_list = [
        {
            "id":          g.id,
            "descripcion": g.descripcion,
            "categoria":   g.categoria,
            "fecha":       g.fecha.strftime("%d/%m/%Y") if g.fecha else "",
            "fecha_iso":   g.fecha.isoformat() if g.fecha else "",
            "monto":       g.monto or 0,
        }
        for g in gastos
    ]

    total_egresos = total_com_vendedores + total_com_cobradores + total_bomberos + total_gastos
    ganancia_neta = total_recaudado - total_egresos

    return templates.TemplateResponse(request, "contabilidad.html", {
        "user":                  user,
        "total_recaudado":       total_recaudado,
        "total_esperado":        total_esperado,
        "falta_cobrar":          falta_cobrar,
        "pct_avance":            pct_avance,
        "total_com_vendedores":  total_com_vendedores,
        "total_com_cobradores":  total_com_cobradores,
        "pago_mensual_bomberos": pago_mensual_bomberos,
        "meses_liquidados":      meses_liquidados,
        "total_bomberos":        total_bomberos,
        "total_gastos":          total_gastos,
        "gastos_list":           gastos_list,
        "categorias":            CATEGORIAS,
        "total_egresos":         total_egresos,
        "ganancia_neta":         ganancia_neta,
        "vendedores_list":       vendedores_list,
        "cobradores_list":       cobradores_list,
        "rec_por_mes_list":      rec_por_mes_list,
        "total_socios":          len(boletas),
    })


@router.post("/config/bomberos")
async def guardar_config_bomberos(
    request: Request,
    pago_mensual: float = Form(...),
    db: Session = Depends(get_db),
):
    user = await auth_module.require_user(request, db)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403)
    _set_config(db, "pago_mensual_bomberos", pago_mensual)
    return JSONResponse({"ok": True, "pago_mensual": pago_mensual})


@router.post("/gastos")
async def crear_gasto(
    request: Request,
    descripcion: str = Form(...),
    categoria:   str = Form("OTRO"),
    fecha:       Optional[str] = Form(None),
    monto:       float = Form(...),
    db: Session = Depends(get_db),
):
    user = await auth_module.require_user(request, db)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403)
    fecha_obj = date.fromisoformat(fecha) if fecha else None
    g = models.GastoContabilidad(
        descripcion=descripcion.strip(),
        categoria=categoria,
        fecha=fecha_obj,
        monto=monto,
    )
    db.add(g)
    db.commit()
    db.refresh(g)
    return JSONResponse({
        "ok":          True,
        "id":          g.id,
        "descripcion": g.descripcion,
        "categoria":   g.categoria,
        "fecha":       g.fecha.strftime("%d/%m/%Y") if g.fecha else "",
        "fecha_iso":   g.fecha.isoformat() if g.fecha else "",
        "monto":       g.monto,
    })


@router.post("/gastos/{gasto_id}/editar")
async def editar_gasto(
    request: Request,
    gasto_id: int,
    descripcion: str = Form(...),
    categoria:   str = Form("OTRO"),
    fecha:       Optional[str] = Form(None),
    monto:       float = Form(...),
    db: Session = Depends(get_db),
):
    user = await auth_module.require_user(request, db)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403)
    g = db.query(models.GastoContabilidad).get(gasto_id)
    if not g:
        raise HTTPException(404)
    g.descripcion = descripcion.strip()
    g.categoria   = categoria
    g.fecha       = date.fromisoformat(fecha) if fecha else None
    g.monto       = monto
    db.commit()
    return JSONResponse({
        "ok":          True,
        "id":          g.id,
        "descripcion": g.descripcion,
        "categoria":   g.categoria,
        "fecha":       g.fecha.strftime("%d/%m/%Y") if g.fecha else "",
        "fecha_iso":   g.fecha.isoformat() if g.fecha else "",
        "monto":       g.monto,
    })


@router.post("/gastos/{gasto_id}/eliminar")
async def eliminar_gasto(
    request: Request,
    gasto_id: int,
    db: Session = Depends(get_db),
):
    user = await auth_module.require_user(request, db)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403)
    g = db.query(models.GastoContabilidad).get(gasto_id)
    if not g:
        raise HTTPException(404)
    db.delete(g)
    db.commit()
    return JSONResponse({"ok": True})
