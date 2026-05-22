from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, joinedload
from .. import models, auth as auth_module
from ..templates_config import templates
from ..database import get_db

router = APIRouter(prefix="/contabilidad", tags=["contabilidad"])

MESES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
         "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]


@router.get("/", response_class=HTMLResponse)
async def contabilidad_index(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "Solo administradores")

    # ── Boletas con socio (excluye BAJA) ──────────────────────────────────
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

    # ── Liquidaciones de vendedores ───────────────────────────────────────
    liqs_v = (
        db.query(models.LiquidacionVendedor)
        .options(joinedload(models.LiquidacionVendedor.vendedor))
        .order_by(models.LiquidacionVendedor.fecha)
        .all()
    )
    total_com_vendedores = sum(lv.total_comision or 0 for lv in liqs_v)

    vendedores_dict = {}
    for lv in liqs_v:
        vid   = lv.vendedor_id
        nombre = lv.vendedor.nombre if lv.vendedor else "—"
        if vid not in vendedores_dict:
            vendedores_dict[vid] = {"nombre": nombre, "total": 0.0, "liquidaciones": []}
        com = lv.total_comision or 0
        vendedores_dict[vid]["total"] += com
        fecha_str = lv.fecha.strftime("%d/%m/%Y") if lv.fecha else ""
        mes_key   = (lv.fecha.year, lv.fecha.month) if lv.fecha else (0, 0)
        vendedores_dict[vid]["liquidaciones"].append({
            "fecha":       fecha_str,
            "mes_nombre":  MESES[lv.fecha.month - 1] if lv.fecha else "",
            "anio":        lv.fecha.year if lv.fecha else 0,
            "cuotas":      int(round(lv.cuotas_equiv or lv.cuotas_vendidas or 0)),
            "com_cuotas":  lv.comision_cuotas or 0,
            "com_contados":lv.comision_contados or 0,
            "total":       com,
        })
    vendedores_list = sorted(vendedores_dict.values(), key=lambda x: -x["total"])

    # ── Liquidaciones de cobradores ───────────────────────────────────────
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
        cid   = p.cobrador_id
        nombre = p.cobrador.nombre if p.cobrador else "—"
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

    # ── Recaudación por mes (agrupada desde liquidaciones de cobranza) ────
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

    # ── Ganancia neta estimada institución ────────────────────────────────
    ganancia_neta = total_recaudado - total_com_vendedores - total_com_cobradores

    return templates.TemplateResponse(request, "contabilidad.html", {
        "user":                  user,
        "total_recaudado":       total_recaudado,
        "total_esperado":        total_esperado,
        "falta_cobrar":          falta_cobrar,
        "pct_avance":            pct_avance,
        "total_com_vendedores":  total_com_vendedores,
        "total_com_cobradores":  total_com_cobradores,
        "ganancia_neta":         ganancia_neta,
        "vendedores_list":       vendedores_list,
        "cobradores_list":       cobradores_list,
        "rec_por_mes_list":      rec_por_mes_list,
        "total_socios":          len(boletas),
    })
