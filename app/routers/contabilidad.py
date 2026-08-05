from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session, joinedload
from datetime import date
from typing import Optional
from .. import models, auth as auth_module
from ..templates_config import templates
from ..database import get_db
# Cuotas cobrables segun la fecha (las ultimas van de regalo). Ver app/cuotas.py.
from ..cuotas import cuotas_vigentes

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
    # Contado activo: ingreso = precio total del bono A SU FECHA DE VENTA.
    # Antes era `num_cuotas × valor_cuota` (siempre 12), lo que inflaba el bruto:
    # desde ago-2026 las cuotas que no entran antes del sorteo final van de regalo,
    # así que un contado de octubre vale 9 cuotas, no 12. Ver app/cuotas.py.
    # Se prefiere `cuotas_pactadas` (lo que quedó fijado al dar de alta el socio) y
    # solo se cae al cálculo por fecha si la boleta es vieja y no lo tiene.
    gross_contado = sum(
        (b.cuotas_pactadas or cuotas_vigentes(
            b.talonera.num_cuotas if b.talonera else None, b.fecha_venta))
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

    # ── Fuente confiable: mismo motor que las hojas de liquidación ────────
    # Contabilidad debe coincidir peso por peso con la hoja de cada mes, que se
    # calcula desde historial_cuotas por período REAL de pago. La tabla
    # Liquidacion (una por planilla, se pisa al re-liquidar y agrupa por el mes
    # de la planilla) daba un acumulado incompleto — "la suma de mayo y junio
    # daba solo el último mes liquidado". Ver _resumen_institucion en
    # app/routers/cobranza.py.
    from .cobranza import _resumen_institucion, _periodos_cobranza
    _periodos = _periodos_cobranza(db)                 # [{anio, mes, ...}]
    rec_real      = {}   # (anio, mes) -> {"monto","comision","neto",...}
    _real_por_cob = {}   # cid -> {(mes, anio): {"bruto","comision","neto"}}
    _cob_real     = {}   # cid -> acumulado por cobrador (para el historial)
    for _p in _periodos:
        _a, _m = _p["anio"], _p["mes"]
        _data = _resumen_institucion(db, _m, _a)
        _tot = _data["totales"]
        if not _tot.get("monto"):
            continue
        rec_real[(_a, _m)] = _tot
        for _f in _data["filas"]:
            _cid = _f["cobrador"].id
            _real_por_cob.setdefault(_cid, {})[(_m, _a)] = {
                "bruto":    _f["monto"],
                "comision": _f["comision"],
                "neto":     _f["neto"],
            }
            _cr = _cob_real.setdefault(_cid, {
                "nombre":        _f["cobrador"].nombre,
                "total_monto":   0.0, "total_comision": 0.0, "total_neto": 0.0,
                "liquidaciones": [],
            })
            _cr["total_monto"]    += _f["monto"]
            _cr["total_comision"] += _f["comision"]
            _cr["total_neto"]     += _f["neto"]
            _cr["liquidaciones"].append({
                "fecha":      "",
                "mes_nombre": MESES[_m - 1],
                "anio":       _a,
                "monto":      _f["monto"],
                "comision":   _f["comision"],
                "neto":       _f["neto"],
            })

    total_com_cobradores = sum(t["comision"] for t in rec_real.values())
    # Recaudado real = lo efectivamente cobrado según las hojas de liquidación
    total_recaudado = sum(t["monto"] for t in rec_real.values())
    pct_avance = round(total_recaudado / total_esperado * 100, 1) if total_esperado else 0

    for _cr in _cob_real.values():
        _cr["liquidaciones"].sort(key=lambda x: (x["anio"], MESES.index(x["mes_nombre"])))
    cobradores_list = sorted(_cob_real.values(), key=lambda x: -x["total_comision"])

    rec_por_mes_list = [
        {
            "mes_nombre": MESES[_m - 1],
            "anio":       _a,
            "monto":      _t["monto"],
            "comision":   _t["comision"],
            "neto":       _t["neto"],
        }
        for (_a, _m), _t in sorted(rec_real.items())
    ]

    pago_mensual_bomberos = _get_config(db, "pago_mensual_bomberos", 0.0)
    meses_liquidados      = len(rec_real)
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

    # ── Premios de sorteo (orden de compra) con ganador asignado ──────────
    # Solo cuentan los premios clase ORDEN ($) que ya tienen ganador asignado
    # (EntregaPremio). Cada entrega suma el monto del premio: un premio "a cada
    # uno" con N ganadores suma monto × N. Los premios físicos (moto, TV…) NO
    # entran acá — se cargan aparte como gasto manual al comprarlos.
    premios_orden = (
        db.query(models.PremioSorteo)
        .options(joinedload(models.PremioSorteo.entregas),
                 joinedload(models.PremioSorteo.sorteo))
        .filter(models.PremioSorteo.clase == "ORDEN")
        .all()
    )
    _TIPO_LBL = {"SEMANAL": "Semanal", "MENSUAL": "Mensual",
                 "CONTADO": "Al contado", "FINAL": "Final"}
    premios_list = []
    total_premios = 0.0
    for p in premios_orden:
        n = len(p.entregas)
        if n == 0:
            continue
        subtotal = (p.monto or 0) * n
        total_premios += subtotal
        so = p.sorteo
        tipo_lbl = _TIPO_LBL.get(so.tipo.value, so.tipo.value) if so else ""
        premios_list.append({
            "descripcion": p.descripcion,
            "sorteo":      (so.nombre + " · " if so and so.nombre else "") + tipo_lbl if so else "",
            "fecha":       so.fecha.strftime("%d/%m/%Y") if so and so.fecha else "",
            "monto":       p.monto or 0,
            "ganadores":   n,
            "subtotal":    subtotal,
        })
    premios_list.sort(key=lambda x: (x["fecha"], x["descripcion"]))

    # Egresos reales (comisiones ya liquidadas + premios con ganador)
    total_egresos = (total_com_vendedores + total_com_cobradores
                     + total_bomberos + total_gastos + total_premios)
    ganancia_neta = total_recaudado - total_egresos
    # Egresos proyectados (para ganancia proyectada — usa com.cobradores proyectada)
    total_egresos_proyectado = (total_com_vendedores + com_cobradores_proyectada
                                + total_bomberos + total_gastos + total_premios)


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

    # _real_por_cob ya se construyó arriba desde el motor de las hojas de
    # liquidación (historial_cuotas por período real). Keys: (mes, anio).

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

    # ── Resumen consolidado mes a mes (todos los cobradores juntos) ───────
    # Para cada mes de campaña suma, sobre todos los cobradores:
    #   · cuotas a cobrar  → cuántas cuotas quedan por cobrar (solo proyectado)
    #   · proyectado       → cobranza esperada (real ya cobrado + proyectado × tasa)
    #   · comisión         → comisión de cobradores sobre esa cobranza
    #   · neto             → proyectado − comisión
    # El estado del mes es "real" si ya se liquidó, "proy." si es proyección,
    # o "mixto" si algunos cobradores ya liquidaron y otros no.
    resumen_meses = []
    for i, pm in enumerate(proyeccion_meses):
        cuotas = 0
        bruto = comision = neto = 0.0
        n_real = n_tot = 0
        for c in proyeccion_list:
            m = c["meses"][i]
            bruto    += m["bruto"]
            comision += m["comision"]
            neto     += m["neto"]
            if m.get("cant"):
                cuotas += m["cant"]
            # solo contamos como "mes con actividad" si hay monto o es real
            if m["es_real"]:
                n_real += 1
                n_tot  += 1
            elif m["bruto"] or m.get("cant"):
                n_tot += 1
        if n_tot == 0:
            estado = "vacio"
        elif n_real == n_tot:
            estado = "real"
        elif n_real == 0:
            estado = "proy"
        else:
            estado = "mixto"
        resumen_meses.append({
            "mes_nombre": pm["mes_nombre"],
            "anio":       pm["anio"],
            "cuotas":     cuotas,
            "bruto":      bruto,
            "comision":   comision,
            "neto":       neto,
            "estado":     estado,
        })

    resumen_cuotas    = sum(r["cuotas"]   for r in resumen_meses)
    resumen_bruto     = sum(r["bruto"]    for r in resumen_meses)
    resumen_comision  = sum(r["comision"] for r in resumen_meses)
    resumen_neto      = sum(r["neto"]     for r in resumen_meses)
    # Neto final = neto de cobranza − comisiones de vendedores por contado
    resumen_neto_final = resumen_neto - com_vendedores_contado

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
        "total_premios":         total_premios,
        "premios_list":          premios_list,
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
        "resumen_meses":         resumen_meses,
        "resumen_cuotas":        resumen_cuotas,
        "resumen_bruto":         resumen_bruto,
        "resumen_comision":      resumen_comision,
        "resumen_neto":          resumen_neto,
        "resumen_neto_final":    resumen_neto_final,
        "com_vendedores_contado": com_vendedores_contado,
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
