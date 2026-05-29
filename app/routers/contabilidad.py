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

CATEGORIAS = ["PREMIO", "VIAJE", "ALOJAMIENTO", "SUELDO", "OTRO"]
PERIODICIDADES = ["UNICO", "MENSUAL"]


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

    # Todas las boletas con socio (incluyendo BAJA) para calcular Total Bruto
    todas_boletas = (
        db.query(models.Boleta)
        .filter(models.Boleta.comprador_id.isnot(None))
        .options(
            joinedload(models.Boleta.talonera),
            joinedload(models.Boleta.cobrador),
        )
        .all()
    )

    # Boletas activas (sin BAJA) — para total_esperado, falta_cobrar, avance
    boletas = [b for b in todas_boletas if b.condicion != models.CondicionBoleta.BAJA]
    baja_boletas = [b for b in todas_boletas if b.condicion == models.CondicionBoleta.BAJA]

    # Helper: ¿es boleta al contado?
    def _es_contado(b):
        if b.numero_especial is not None or b.numero_especial_2 is not None:
            return True
        pac = b.cuotas_pactadas or 0
        ant = b.cuotas_anticipadas or 0
        return pac > 0 and ant >= pac

    # ── Total en Brutos ──────────────────────────────────────────────────
    # Cuotas activas (no BAJA, no contado): ingreso proyectado = cuotas_pactadas × valor_cuota
    gross_cuotas = sum(
        (b.cuotas_pactadas or 0) * (b.talonera.valor_cuota if b.talonera else 0)
        for b in boletas if not _es_contado(b)
    )
    # Boletas BAJA: solo lo que pagaron antes de darse de baja
    gross_baja = sum(
        (b.cuotas_pagadas or 0) * (b.talonera.valor_cuota if b.talonera else 0)
        for b in baja_boletas
    )
    # Contado activo: ingreso = num_cuotas × valor_cuota (precio total del bono)
    gross_contado = sum(
        (b.talonera.num_cuotas if b.talonera and b.talonera.num_cuotas else 0)
        * (b.talonera.valor_cuota if b.talonera else 0)
        for b in boletas if _es_contado(b)
    )

    # Comisión cobradores proyectada: cobrador.comision_pct sobre cuotas proyectadas
    # (excluye BAJA y boletas sin cobrador asignado)
    com_cobradores_proyectada = sum(
        (b.cobrador.comision_pct if b.cobrador else 0) / 100.0
        * (b.cuotas_pactadas or 0) * (b.talonera.valor_cuota if b.talonera else 0)
        for b in boletas
        if not _es_contado(b) and b.cobrador_id is not None
    )

    # Comisión vendedores sobre contado ya liquidada (lo que se llevan los vendedores)
    # Se calcula más abajo cuando tengamos liqs_v; ponemos placeholder aquí y calculamos después

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
    # Comisión de vendedores SOLO sobre contado (lo que ya cobraron, de liquidaciones)
    com_vendedores_contado = sum(lv.comision_contados or 0 for lv in liqs_v)
    # Total en Brutos = cuotas proyectadas + BAJA (pagado) + contado - com.cobradores proyect. - com.vendedores contado
    total_bruto = gross_cuotas + gross_baja + gross_contado - com_cobradores_proyectada - com_vendedores_contado

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
            "contados":     int(round(float(lv.contados_equiv or lv.contados_vendidos or 0))),
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
    def _monto_real(g):
        """Para gastos MENSUAL el monto total = monto_por_mes × meses_liquidados."""
        perio = getattr(g, "periodicidad", "UNICO") or "UNICO"
        if perio == "MENSUAL":
            return (g.monto or 0) * meses_liquidados
        return g.monto or 0

    total_gastos = sum(_monto_real(g) for g in gastos)

    gastos_list = [
        {
            "id":           g.id,
            "descripcion":  g.descripcion,
            "categoria":    g.categoria,
            "periodicidad": getattr(g, "periodicidad", "UNICO") or "UNICO",
            "fecha":        g.fecha.strftime("%d/%m/%Y") if g.fecha else "",
            "fecha_iso":    g.fecha.isoformat() if g.fecha else "",
            "monto":        g.monto or 0,
            "monto_real":   _monto_real(g),
        }
        for g in gastos
    ]

    # Egresos reales (comisiones ya liquidadas)
    total_egresos = total_com_vendedores + total_com_cobradores + total_bomberos + total_gastos
    ganancia_neta = total_recaudado - total_egresos
    # Egresos proyectados (para ganancia proyectada — usa com.cobradores proyectada)
    total_egresos_proyectado = total_com_vendedores + com_cobradores_proyectada + total_bomberos + total_gastos


    # ── Proyección mensual por cobrador ─────────────────────────────────
    # Cuota 1 = Junio 2026, cuota 2 = Julio 2026, …, cuota 12 = Mayo 2027
    _CAMPANA_INICIO = 4    # índice 0-based de Mayo (cuota 1 = Mayo 2026, cubre el mes de venta)
    _CAMPANA_ANIO   = 2026
    _CAMPANA_MES_BASE = 5  # número de mes de inicio (Mayo=5); meses < 5 son del año siguiente

    def _cuota_a_mes_anio(n):
        idx  = (_CAMPANA_INICIO + n - 1) % 12
        mes  = idx + 1
        anio = _CAMPANA_ANIO if mes >= _CAMPANA_MES_BASE else _CAMPANA_ANIO + 1
        return mes, anio

    # Meses de campaña (encabezados fijos): Mayo 2026 → Abril 2027
    proyeccion_meses = [
        {
            "mes":      (_CAMPANA_INICIO + i) % 12 + 1,
            "anio":     _CAMPANA_ANIO if ((_CAMPANA_INICIO + i) % 12 + 1) >= _CAMPANA_MES_BASE else _CAMPANA_ANIO + 1,
            "mes_nombre": MESES[(_CAMPANA_INICIO + i) % 12],
        }
        for i in range(12)
    ]

    # Liquidaciones reales ya registradas, por cobrador y (mes, anio)
    _real_por_cob = {}   # cid → {(mes, anio): {bruto, comision, neto}}
    for lc in liqs_c:
        p = lc.planilla
        if not p:
            continue
        cid = p.cobrador_id
        key = (p.mes, p.anio)
        _real_por_cob.setdefault(cid, {}).setdefault(key, {"bruto": 0.0, "comision": 0.0, "neto": 0.0})
        _real_por_cob[cid][key]["bruto"]    += lc.monto_total or 0
        _real_por_cob[cid][key]["comision"] += lc.comision    or 0
        _real_por_cob[cid][key]["neto"]     += lc.neto        or 0

    # Boletas activas con cobrador y talonera
    boletas_con_cob = [
        b for b in boletas
        if b.cobrador_id is not None and b.talonera is not None
    ]

    # Info de cobradores únicos
    _cob_info = {}
    for b in boletas_con_cob:
        if b.cobrador_id not in _cob_info and b.cobrador:
            _cob_info[b.cobrador_id] = {
                "nombre":       b.cobrador.nombre,
                "comision_pct": float(b.cobrador.comision_pct or 0),
            }

    # Tasa de cobro por cobrador:
    # De las cuotas que YA VENCIERON (según meses de campaña transcurridos),
    # cuántas se cobraron efectivamente.
    # Cuota 1 venció en Junio 2026, cuota 2 en Julio 2026, etc.
    from datetime import date as _date
    _hoy = _date.today()
    _camp_start_anio = 2026
    _camp_start_mes  = 5   # Mayo (cuota 1 = mes de venta)
    # Cuántos meses de campaña han transcurrido (0 = aún no empezó)
    _meses_transcurridos = max(
        0,
        (_hoy.year - _camp_start_anio) * 12 + (_hoy.month - _camp_start_mes) + 1
    )

    _cob_tasa = {}
    for cid in _cob_info:
        tot_vencido = tot_pag = 0
        for b in boletas_con_cob:
            if b.cobrador_id != cid:
                continue
            if _es_contado(b):
                continue
            pac  = b.cuotas_pactadas    or 0
            pag  = b.cuotas_pagadas     or 0
            ant  = b.cuotas_anticipadas or 0
            # Cuotas vencidas de esta boleta = min(meses transcurridos, pactadas) - anticipadas
            # (las anticipadas ya estaban pagas antes de vencer)
            vencidas_boleta = max(0, min(_meses_transcurridos, pac) - ant)
            # Cuotas pagas a través de cobranza = pagadas - anticipadas
            pagas_cob = max(0, pag - ant)
            tot_vencido += vencidas_boleta
            tot_pag     += min(pagas_cob, vencidas_boleta)   # no puede superar lo vencido
        if tot_vencido > 0:
            _cob_tasa[cid] = round(tot_pag / tot_vencido, 4)
        else:
            _cob_tasa[cid] = 1.0   # campaña no comenzó → proyección al 100%

    # Para cada cobrador, armar 12 meses mezclando reales + proyectados
    _cob_proyeccion = {}
    for cid, info in _cob_info.items():
        meses_proj = []
        pct  = info["comision_pct"] / 100.0
        tasa = _cob_tasa[cid]
        real_mes = _real_por_cob.get(cid, {})

        for n in range(1, 13):
            mes, anio = _cuota_a_mes_anio(n)
            key = (mes, anio)

            if key in real_mes:
                # ── Mes ya liquidado: usar cifras reales ──────────────
                r = real_mes[key]
                meses_proj.append({
                    "cuota":      n,
                    "mes":        mes,
                    "anio":       anio,
                    "mes_nombre": MESES[mes - 1],
                    "bruto":      r["bruto"],
                    "comision":   r["comision"],
                    "neto":       r["neto"],
                    "cant":       None,
                    "es_real":    True,
                    "tasa":       None,
                })
            else:
                # ── Mes futuro: proyección × tasa de cobro ───────────
                bruto_teorico = 0.0
                cant = 0
                for b in boletas_con_cob:
                    if b.cobrador_id != cid:
                        continue
                    pactadas    = b.cuotas_pactadas    or 0
                    pagadas     = b.cuotas_pagadas     or 0
                    anticipadas = b.cuotas_anticipadas or 0
                    nc          = b.talonera.num_cuotas or 12
                    vc          = b.talonera.valor_cuota or 0
                    if n > nc:           continue
                    if n > pactadas:     continue
                    if n <= pagadas:     continue
                    if n <= anticipadas: continue
                    bruto_teorico += vc
                    cant          += 1

                bruto_aj = round(bruto_teorico * tasa)
                comision = round(bruto_aj * pct)
                meses_proj.append({
                    "cuota":      n,
                    "mes":        mes,
                    "anio":       anio,
                    "mes_nombre": MESES[mes - 1],
                    "bruto":      bruto_aj,
                    "bruto_teorico": bruto_teorico,
                    "comision":   comision,
                    "neto":       bruto_aj - comision,
                    "cant":       cant,
                    "es_real":    False,
                    "tasa":       tasa,
                })

        _cob_proyeccion[cid] = meses_proj

    proyeccion_list = sorted([
        {
            "nombre":        _cob_info[cid]["nombre"],
            "comision_pct":  _cob_info[cid]["comision_pct"],
            "tasa_cobro":    round(_cob_tasa[cid] * 100, 1),
            "meses":         _cob_proyeccion[cid],
            "total_bruto":   sum(m["bruto"]    for m in _cob_proyeccion[cid]),
            "total_comision":sum(m["comision"] for m in _cob_proyeccion[cid]),
            "total_neto":    sum(m["neto"]     for m in _cob_proyeccion[cid]),
        }
        for cid in _cob_info
    ], key=lambda x: x["nombre"])

    return templates.TemplateResponse(request, "contabilidad.html", {
        "user":                  user,
        "total_recaudado":       total_recaudado,
        "total_bruto":           total_bruto,
        "gross_cuotas":          gross_cuotas,
        "gross_baja":            gross_baja,
        "gross_contado":         gross_contado,
        "com_cobradores_proyectada": com_cobradores_proyectada,
        "total_egresos_proyectado":  total_egresos_proyectado,
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
        "periodicidades":        PERIODICIDADES,
        "total_egresos":         total_egresos,
        "ganancia_neta":         ganancia_neta,
        "vendedores_list":       vendedores_list,
        "cobradores_list":       cobradores_list,
        "rec_por_mes_list":      rec_por_mes_list,
        "total_socios":          len(boletas),
        "proyeccion_list":       proyeccion_list,
        "proyeccion_meses":      proyeccion_meses,
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
    descripcion:  str = Form(...),
    categoria:    str = Form("OTRO"),
    periodicidad: str = Form("UNICO"),
    fecha:        Optional[str] = Form(None),
    monto:        float = Form(...),
    db: Session = Depends(get_db),
):
    user = await auth_module.require_user(request, db)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403)
    if periodicidad not in ("UNICO", "MENSUAL"):
        periodicidad = "UNICO"
    fecha_obj = date.fromisoformat(fecha) if fecha else None
    g = models.GastoContabilidad(
        descripcion=descripcion.strip(),
        categoria=categoria,
        periodicidad=periodicidad,
        fecha=fecha_obj,
        monto=monto,
    )
    db.add(g)
    db.commit()
    db.refresh(g)
    return JSONResponse({
        "ok":           True,
        "id":           g.id,
        "descripcion":  g.descripcion,
        "categoria":    g.categoria,
        "periodicidad": g.periodicidad,
        "fecha":        g.fecha.strftime("%d/%m/%Y") if g.fecha else "",
        "fecha_iso":    g.fecha.isoformat() if g.fecha else "",
        "monto":        g.monto,
    })


@router.post("/gastos/{gasto_id}/editar")
async def editar_gasto(
    request: Request,
    gasto_id: int,
    descripcion:  str = Form(...),
    categoria:    str = Form("OTRO"),
    periodicidad: str = Form("UNICO"),
    fecha:        Optional[str] = Form(None),
    monto:        float = Form(...),
    db: Session = Depends(get_db),
):
    user = await auth_module.require_user(request, db)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403)
    g = db.query(models.GastoContabilidad).get(gasto_id)
    if not g:
        raise HTTPException(404)
    if periodicidad not in ("UNICO", "MENSUAL"):
        periodicidad = "UNICO"
    g.descripcion  = descripcion.strip()
    g.categoria    = categoria
    g.periodicidad = periodicidad
    g.fecha        = date.fromisoformat(fecha) if fecha else None
    g.monto        = monto
    db.commit()
    return JSONResponse({
        "ok":           True,
        "id":           g.id,
        "descripcion":  g.descripcion,
        "categoria":    g.categoria,
        "periodicidad": g.periodicidad,
        "fecha":        g.fecha.strftime("%d/%m/%Y") if g.fecha else "",
        "fecha_iso":    g.fecha.isoformat() if g.fecha else "",
        "monto":        g.monto,
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
