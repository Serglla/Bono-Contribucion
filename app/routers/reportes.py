from fastapi import HTTPException,  APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse

from sqlalchemy.orm import Session
from sqlalchemy import func, or_
import json
from .. import models, auth as auth_module
from ..templates_config import templates
from ..models import CondicionBoleta
from ..database import get_db
from ..tiempo import parse_periodo, hoy_ar
from .vendedores import _stats_bulk

router = APIRouter(prefix="/reportes", tags=["reportes"])



@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'reportes', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')

    taloneras = db.query(models.Talonera).order_by(models.Talonera.nombre).all()

    # Agrupar taloneras por nombre (ej: todas las "PATA 1" en una sola fila)
    grupos: dict = {}
    for t in taloneras:
        key = t.nombre
        if key not in grupos:
            grupos[key] = {
                "nombre": t.nombre,
                "num_series": t.num_series,
                "multiplicador": t.multiplicador,
                "tipo": (t.tipo or "COMUN"),
                "ids": [],
            }
        grupos[key]["ids"].append(t.id)

    stats_por_talonera = []
    for key, g in grupos.items():
        ids = g["ids"]
        factor = float(g["multiplicador"] or 1.0)   # ponderación real por PATA (PATA 0=0.67, 4=4, 6=6...)
        tipo = g["tipo"]

        if tipo == "CONTADO":
            total_entregados = db.query(
                func.coalesce(
                    func.sum(models.EntregaCaja.hasta - models.EntregaCaja.desde + 1),
                    0,
                )
            ).filter(
                func.lower(func.trim(models.EntregaCaja.talonera_nombre)) == (g["nombre"] or "").strip().lower(),
                func.coalesce(models.EntregaCaja.tipo, "ENTREGA") != "RETIRO",
            ).scalar() or 0
            # Asignados a boletas — slot 1 (numero_especial)
            asignados_1 = db.query(func.count(models.Boleta.id)).filter(
                models.Boleta.numero_especial.isnot(None),
                models.Boleta.talonera_especial_id.in_(ids),
            ).scalar() or 0
            # Asignados a boletas — slot 2 (numero_especial_2)
            asignados_2 = db.query(func.count(models.Boleta.id)).filter(
                models.Boleta.numero_especial_2.isnot(None),
                models.Boleta.talonera_especial_2_id.in_(ids),
            ).scalar() or 0
            # Asignados CON socio cargado — slot 1
            vendidas_1 = db.query(func.count(models.Boleta.id)).filter(
                models.Boleta.numero_especial.isnot(None),
                models.Boleta.talonera_especial_id.in_(ids),
                models.Boleta.comprador_id.isnot(None),
            ).scalar() or 0
            vendidas_2 = db.query(func.count(models.Boleta.id)).filter(
                models.Boleta.numero_especial_2.isnot(None),
                models.Boleta.talonera_especial_2_id.in_(ids),
                models.Boleta.comprador_id.isnot(None),
            ).scalar() or 0
            asignados = int(asignados_1) + int(asignados_2)
            vendidas  = int(vendidas_1) + int(vendidas_2)
            total     = int(total_entregados)
            en_caja   = max(0, total - asignados)
            stats_por_talonera.append({
                "nombre": g["nombre"],
                "tipo": tipo,
                "factor": factor,
                "total": total,
                "vendidas": vendidas,
                "vendidas_1": int(vendidas_1),
                "vendidas_2": int(vendidas_2),
                "baja": 0,
                "en_caja": en_caja,
                "en_cobranza": 0,
                "sin_vender": 0,
                "cuotas_cobradas": 0,
                "total_ponderado": total,
                "vendidas_ponderado": vendidas,
                "baja_ponderado": 0,
                "contado_1": 0,
                "contado_2": 0,
            })
            continue

        # Caso COMUN — lógica original
        total = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids)
        ).scalar()
        vendidas = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.comprador_id.isnot(None)
        ).scalar()
        baja = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            or_(models.Boleta.condicion == CondicionBoleta.BAJA,
                models.Boleta.mes_baja.isnot(None))
        ).scalar()
        en_cobranza = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.condicion == CondicionBoleta.EN_COBRANZA
        ).scalar()
        en_caja = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.condicion == CondicionBoleta.CAJA,
            models.Boleta.comprador_id.is_(None)
        ).scalar()
        sin_vender = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.condicion == CondicionBoleta.SIN_VENDER
        ).scalar()
        cuotas_cobradas = db.query(func.sum(models.Boleta.cuotas_pagadas)).filter(
            models.Boleta.talonera_id.in_(ids)
        ).scalar() or 0
        # Desglose contado: boletas de esta talonera con número contado asignado
        contado_1 = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.numero_especial.isnot(None),
        ).scalar() or 0
        contado_2 = db.query(func.count(models.Boleta.id)).filter(
            models.Boleta.talonera_id.in_(ids),
            models.Boleta.numero_especial_2.isnot(None),
        ).scalar() or 0
        stats_por_talonera.append({
            "nombre": g["nombre"],
            "tipo": tipo,
            "factor": factor,
            "total": total,
            "vendidas": vendidas,
            "vendidas_1": 0,
            "vendidas_2": 0,
            "baja": baja,
            "en_caja": en_caja,
            "en_cobranza": en_cobranza,
            "sin_vender": sin_vender,
            "cuotas_cobradas": cuotas_cobradas,
            "total_ponderado": int(round(total * factor)),
            "vendidas_ponderado": int(round(vendidas * factor)),
            "baja_ponderado": int(round(baja * factor)),
            "contado_1": int(contado_1),
            "contado_2": int(contado_2),
        })

    # ── Liquidado por vendedor ─────────────────────────────────────────────
    # Reusa la misma lógica que la tabla de Vendedores: ponderado por PATA
    # (PATA 1 ×1, PATA 2 ×2, PATA 0 ×0.67) y SIN contar los números de sorteo
    # extra (pool CONTADO / CONTADO 2 VECES) como ventas.
    _stats = _stats_bulk(db)

    # Composición por PATA de cada vendedor (ponderada por Talonera.multiplicador:
    # X1×1, X2×2, X0×0.67…). Se arma sobre las boletas que liquidó — uniendo por la
    # liquidación (LiquidacionVendedor) para respetar el criterio "liquidado por
    # vendedor" aunque la boleta se haya reasignado a otro vendedor después.
    # Solo taloneras COMUN (las CONTADO son pool de sorteo, no son PATAs).
    pata_by_vend: dict = {}
    _pata_rows = db.query(
        models.LiquidacionVendedor.vendedor_id,
        models.Talonera.nombre,
        func.coalesce(func.sum(models.Talonera.multiplicador), 0),
    ).select_from(models.Boleta).join(
        models.LiquidacionVendedor,
        models.LiquidacionVendedor.id == models.Boleta.liquidacion_vendedor_id,
    ).join(
        models.Talonera, models.Talonera.id == models.Boleta.talonera_id,
    ).filter(
        models.Talonera.tipo == "COMUN",
    ).group_by(
        models.LiquidacionVendedor.vendedor_id, models.Talonera.nombre,
    ).all()
    for _vid, _nom, _w in _pata_rows:
        if _vid is None:
            continue
        pata_by_vend.setdefault(_vid, {})[_nom] = float(_w or 0)

    def _pata_breakdown(vid):
        """Lista [{label: 'X1', pct: 70}, …] con el mix de PATAs del vendedor
        (suma 100%). Ordenada por etiqueta (X0, X1, X2…)."""
        m = pata_by_vend.get(vid, {})
        tot = sum(m.values())
        out = []
        for nom, w in m.items():
            out.append({
                "label": (nom or "").replace("PATA ", "X"),
                "pct": round(w / tot * 100) if tot else 0,
            })
        out.sort(key=lambda p: p["label"])
        return out

    vendedores_liq = []
    for v in db.query(models.Vendedor).order_by(models.Vendedor.nombre).all():
        s = _stats.get(v.id, {})
        _vend = int(s.get("vendido", 0))
        _baja = int(s.get("baja", 0))
        vendedores_liq.append({
            "nombre": v.nombre,
            "cuotas": int(s.get("liquidados_cuotas", 0)),
            "contados": int(s.get("liquidados_contados", 0)),
            "total": int(s.get("liquidados", 0)),
            "vendido": _vend,
            "bajas": _baja,
            # % de baja del vendedor = bajas ÷ lo que vendió (su tasa personal)
            "bajas_pct": (round(_baja / _vend * 100, 1) if _vend else None),
            "patas": _pata_breakdown(v.id),
        })
    # Ordenar por total liquidado (descendente), los que tienen 0 al final
    vendedores_liq.sort(key=lambda x: x["total"], reverse=True)
    # % que representa cada vendedor sobre el total liquidado de todos
    _gtot = sum(v["total"] for v in vendedores_liq) or 0
    for v in vendedores_liq:
        v["share"] = round(v["total"] / _gtot * 100, 1) if _gtot else 0
    vendedores_liq_total = {
        "cuotas":   sum(v["cuotas"]   for v in vendedores_liq),
        "contados": sum(v["contados"] for v in vendedores_liq),
        "total":    sum(v["total"]    for v in vendedores_liq),
        "vendido":  sum(v["vendido"]  for v in vendedores_liq),
        "bajas":    sum(v["bajas"]    for v in vendedores_liq),
    }
    _gv = vendedores_liq_total["vendido"]
    vendedores_liq_total["bajas_pct"] = (
        round(vendedores_liq_total["bajas"] / _gv * 100, 1) if _gv else None
    )

    # ── Resumen de cobradores ──────────────────────────────────────────────
    # MISMO criterio que el módulo Cobranza (/cobranza/), para que coincida con las
    # tarjetas por cobrador:
    #   - "A cobrar" = cuotas de boletas YA emplanilladas (planillas entregadas) y no
    #     terminadas, ponderadas por PATA. Son las que están por cobrar / se liquidan.
    #   - "Del mes"  = cuotas pendientes de emplanillar (todavía sin planilla),
    #     ponderadas por PATA. Excluye contado (numero_especial_2).
    #   - "% de cobrado" = progreso de cobro de lo emplanillado (pagadas/pactadas).
    #     Si no se cobró nada más allá de las anticipadas → "Sin iniciar".
    def _pata_valor_b(b):
        # Ponderación por PATA = multiplicador real (PATA 0=0.67, 1=1, 2=2, 4=4, 6=6...).
        if b.talonera and b.talonera.multiplicador:
            return float(b.talonera.multiplicador)
        return 1.0

    planillas_por_cob = dict(
        db.query(
            models.Planilla.cobrador_id,
            func.count(models.Planilla.id),
        ).group_by(models.Planilla.cobrador_id).all()
    )

    # % de cobrado = PROMEDIO de las tasas mes a mes de las liquidaciones.
    # Para cada mes calendario en que hubo cobranza (registrado en
    # historial_cuotas de las boletas), la tasa del mes = cuotas cobradas ese mes
    # ÷ boletas activas del cobrador. El % global del cobrador es el promedio de
    # esas tasas mensuales. Así el número refleja la gestión mensual y no queda
    # diluido contra las 12 cuotas de toda la campaña.
    #   meses_cob[m] = cuotas cobradas en el mes calendario m (de historial_cuotas)
    #   activas       = boletas emplanilladas activas (denominador por mes)
    _cob_acc: dict = {}
    _g_meses: dict = {}   # tally global de cuotas cobradas por mes (para el total)
    _g_activas = 0        # boletas activas globales (denominador del total)
    _g_infos: list = []   # info por boleta para el denominador mes a mes global
    # Solo las planillas YA liquidadas entran en el % de cobrado (igual criterio
    # que las tarjetas de /cobranza/): una planilla recién armada todavía no tiene
    # cobranza y, si se contara, sumaría al denominador sin aportar cuotas cobradas
    # y diluiría el promedio (era el bug: el dashboard daba ~40% vs ~97% real).
    _liq_planilla_ids = {
        pid for (pid,) in db.query(models.Liquidacion.planilla_id)
                            .filter(models.Liquidacion.planilla_id.isnot(None)).all()
    }
    # Período (anio, mes) en que se entregó cada planilla: hace falta para el
    # denominador MES A MES del % cobrado (ver más abajo).
    _pl_periodo = {
        pid: (int(a or 0), int(m or 0))
        for pid, a, m in db.query(models.Planilla.id, models.Planilla.anio,
                                  models.Planilla.mes).all()
    }
    _anios_vistos = {a for (a, _m) in _pl_periodo.values() if a}
    boletas_con_cob = db.query(models.Boleta).filter(
        models.Boleta.cobrador_id.isnot(None)
    ).all()
    for b in boletas_con_cob:
        cid = b.cobrador_id
        acc = _cob_acc.setdefault(cid, {
            "a_cobrar": 0, "del_mes": 0, "bajas": 0,
            "pactadas": 0, "pagadas": 0, "anticipadas": 0,
            "meses_cob": {}, "activas": 0, "total": 0, "infos": [],
        })
        acc["total"] += 1   # todas las boletas del cobrador (denominador del % de baja)
        if b.condicion == CondicionBoleta.BAJA or b.mes_baja:
            acc["bajas"] += 1
            continue
        no_terminada = (b.cuotas_pagadas or 0) < (b.cuotas_pactadas or 0)
        pv = _pata_valor_b(b)
        if b.planilla_id is not None:
            if no_terminada:
                acc["a_cobrar"] += pv          # emplanillada (planilla entregada)
            acc["pactadas"]    += (b.cuotas_pactadas or 0)
            acc["pagadas"]     += (b.cuotas_pagadas or 0)
            acc["anticipadas"] += (b.cuotas_anticipadas or 0)
            # Cuotas cobradas mes a mes (cobranza real; la cuota 1 de venta NO
            # está en historial_cuotas, es la anticipada). Una boleta activa = un
            # punto del denominador de cada mes. Solo cuentan las boletas en
            # planillas YA liquidadas (las recién armadas no tienen cobranza aún
            # y diluirían el promedio).
            if b.planilla_id in _liq_planilla_ids:
                acc["activas"] += 1
                _g_activas += 1
                try:
                    _hist = json.loads(b.historial_cuotas) if b.historial_cuotas else {}
                except (ValueError, TypeError):
                    _hist = {}
                _pagos_b = []
                for _k, _v in _hist.items():
                    # Valores "YYYY-MM" (nuevo) o int 1-12 (legacy). Agrupar por
                    # (anio, mes) para no mezclar julio 2026 con julio 2027 (C-1).
                    _p = parse_periodo(_v)
                    if _p is None:
                        continue
                    _pk = (_p[0] or 0, _p[1])
                    _pagos_b.append(_pk)
                    if _p[0]:
                        _anios_vistos.add(_p[0])
                    acc["meses_cob"][_pk] = acc["meses_cob"].get(_pk, 0) + 1
                    _g_meses[_pk] = _g_meses.get(_pk, 0) + 1
                _info_b = {
                    "desde": _pl_periodo.get(b.planilla_id, (0, 0)),
                    "ant": int(b.cuotas_anticipadas or 0),
                    "pact": int(b.cuotas_pactadas or 0),
                    "pagos": _pagos_b,
                }
                acc["infos"].append(_info_b)
                _g_infos.append(_info_b)
        elif no_terminada and b.numero_especial_2 is None:
            acc["del_mes"] += pv               # pendiente de emplanillar

    # ── Denominador MES A MES ───────────────────────────────────────────────
    # No todas las boletas estuvieron en cobranza todos los meses. La cobranza
    # de una planilla arranca EL MES SIGUIENTE al de entrega (planilla de mayo
    # → cuota 1 en junio), así que una planilla entregada en junio recién suma
    # al denominador de julio. Tampoco cuentan las boletas que ya habían
    # terminado de pagar. Antes se dividía siempre por el total de activas y
    # eso hundía el % de los meses viejos.
    _anio_ref = min(_anios_vistos) if _anios_vistos else hoy_ar().year

    def _ordp(anio_p, mes_p):
        return (anio_p or _anio_ref) * 12 + (mes_p or 1)

    def _activas_en(infos, anio_p, mes_p):
        _o = _ordp(anio_p, mes_p)
        n = 0
        for bi in infos:
            if _ordp(*bi["desde"]) >= _o:
                continue
            pagadas_antes = bi["ant"] + sum(1 for k in bi["pagos"] if _ordp(*k) < _o)
            if bi["pact"] and pagadas_antes >= bi["pact"]:
                continue
            n += 1
        return n

    def _pct_promedio(infos, meses):
        rates = []
        for (y, m), cnt in meses.items():
            act = _activas_en(infos, y, m)
            if act > 0:
                rates.append(cnt / act)
        return round(sum(rates) / len(rates) * 100, 1) if rates else None

    cobradores_resumen = []
    _g_pac = _g_pag = _g_ant = 0
    for c in db.query(models.Cobrador).order_by(models.Cobrador.nombre).all():
        acc = _cob_acc.get(c.id, {"a_cobrar": 0, "del_mes": 0, "bajas": 0,
                                  "pactadas": 0, "pagadas": 0, "anticipadas": 0,
                                  "meses_cob": {}, "activas": 0, "total": 0,
                                  "infos": []})
        pac = acc["pactadas"]; pag = acc["pagadas"]; ant = acc["anticipadas"]
        _g_pac += pac; _g_pag += pag; _g_ant += ant
        # % = promedio de las tasas mensuales (cuotas cobradas ese mes ÷ boletas
        # que estaban efectivamente en cobranza ESE mes).
        pct = _pct_promedio(acc.get("infos", []), acc["meses_cob"])
        cobradores_resumen.append({
            "nombre": c.nombre,
            "planillas": int(planillas_por_cob.get(c.id, 0) or 0),
            "a_cobrar": int(round(acc["a_cobrar"])),
            "del_mes": int(round(acc["del_mes"])),
            "pct": pct,
            "bajas": int(acc["bajas"]),
            # % de baja del cobrador = bajas ÷ todas las boletas que tiene asignadas
            "bajas_pct": (round(acc["bajas"] / acc["total"] * 100, 1) if acc.get("total") else None),
        })
    cobradores_resumen.sort(key=lambda x: (x["a_cobrar"], x["del_mes"]), reverse=True)

    # Total: promedio de las tasas mensuales globales, con el mismo denominador
    # mes a mes que se usa por cobrador.
    _pct_total = _pct_promedio(_g_infos, _g_meses)
    _g_total_cob = sum(a.get("total", 0) for a in _cob_acc.values())
    _g_bajas_cob = sum(c["bajas"] for c in cobradores_resumen)
    cobradores_total = {
        "planillas": sum(c["planillas"] for c in cobradores_resumen),
        "a_cobrar":  sum(c["a_cobrar"] for c in cobradores_resumen),
        "del_mes":   sum(c["del_mes"]  for c in cobradores_resumen),
        "pct":       _pct_total,
        "bajas":     _g_bajas_cob,
        "bajas_pct": (round(_g_bajas_cob / _g_total_cob * 100, 1) if _g_total_cob else None),
    }

    # Tarjetas del dashboard — ponderadas por Talonera.multiplicador
    def _sum_mult(filtro=None):
        q = db.query(
            func.coalesce(func.sum(models.Talonera.multiplicador), 0)
        ).select_from(models.Boleta).join(
            models.Talonera, models.Talonera.id == models.Boleta.talonera_id
        )
        if filtro is not None:
            q = q.filter(filtro)
        return q.scalar() or 0

    # "al contado" = tiene numero_especial asignado  O  pagó todas las cuotas por adelantado
    # (equivalente a la lógica `es_contado or anticipo_total` de compradores.html)
    _anticipo_total = (
        (models.Boleta.cuotas_pactadas.isnot(None)) &
        (models.Boleta.cuotas_pactadas > 0) &
        (models.Boleta.cuotas_anticipadas.isnot(None)) &
        (models.Boleta.cuotas_anticipadas >= models.Boleta.cuotas_pactadas)
    )
    _es_contado = (models.Boleta.numero_especial.isnot(None)) | _anticipo_total

    totales = {
        # Taloneras vendidas — toda boleta con comprador asignado
        "vendidas": _sum_mult(models.Boleta.comprador_id.isnot(None)),
        # Vendidas en cuotas — tienen comprador, no son contado ni anticipo total
        "cuotas": _sum_mult(
            (models.Boleta.comprador_id.isnot(None)) &
            ~_es_contado
        ),
        # Al contado — tienen número especial Y/O pagaron todas las cuotas anticipadas
        "contado": _sum_mult(_es_contado),
        # Baja — boletas con condición BAJA (Socios) o marcadas de baja en cobranza
        "baja": _sum_mult(or_(models.Boleta.condicion == CondicionBoleta.BAJA,
                              models.Boleta.mes_baja.isnot(None))),
        # Liquidados por el vendedor pero SIN socio cargado todavía (pendientes de
        # cargar el comprador). Ponderado por PATA, igual que vendidas.
        "sin_cargar": _sum_mult(
            (models.Boleta.liquidacion_vendedor_id.isnot(None)) &
            (models.Boleta.comprador_id.is_(None))
        ),
        # Compradores — personas únicas (no se pondera)
        "compradores": db.query(func.count(models.Comprador.id)).scalar(),
    }

    # Zonas trabajadas = cantidad de zonas distintas donde hay números vendidos
    # (boletas con comprador asignado). Una sola consulta, en vez del detalle por zona.
    zonas_trabajadas = db.query(
        func.count(func.distinct(models.Comprador.zona_id))
    ).select_from(models.Boleta).join(
        models.Comprador, models.Comprador.id == models.Boleta.comprador_id
    ).filter(
        models.Comprador.zona_id.isnot(None)
    ).scalar() or 0

    return templates.TemplateResponse(request, "reportes.html", {"user": user,
        "stats_por_talonera": stats_por_talonera,
        "vendedores_liq": vendedores_liq,
        "vendedores_liq_total": vendedores_liq_total,
        "cobradores_resumen": cobradores_resumen,
        "cobradores_total": cobradores_total,
        "totales": totales,
        "zonas_trabajadas": zonas_trabajadas})


@router.get("/sin-cargar-lista", response_class=JSONResponse)
async def sin_cargar_lista(request: Request, db: Session = Depends(get_db)):
    """Lista de boletas liquidadas por el vendedor pero SIN socio cargado todavia.
    Devuelve items {vendedor, talonera, numero_fmt, fecha_liq} ordenados por
    vendedor/talonera/numero. fecha_liq = fecha en que se liquido la boleta."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'reportes', 'ver'):
        raise HTTPException(403, 'Sin permiso')
    rows = db.query(
        models.Vendedor.nombre,
        models.Talonera.nombre,
        models.Talonera.num_digitos,
        models.Boleta.numero_principal,
        models.LiquidacionVendedor.fecha,
    ).select_from(models.Boleta).join(
        models.Talonera, models.Talonera.id == models.Boleta.talonera_id
    ).outerjoin(
        models.Vendedor, models.Vendedor.id == models.Boleta.vendedor_id
    ).outerjoin(
        models.LiquidacionVendedor,
        models.LiquidacionVendedor.id == models.Boleta.liquidacion_vendedor_id
    ).filter(
        models.Boleta.liquidacion_vendedor_id.isnot(None),
        models.Boleta.comprador_id.is_(None),
    ).order_by(
        models.Vendedor.nombre, models.Talonera.nombre, models.Boleta.numero_principal
    ).all()
    items = []
    for vn, tn, nd, num, fl in rows:
        items.append({
            "vendedor": vn or "(sin vendedor)",
            "talonera": (tn or "").replace("PATA ", "X"),
            "numero_fmt": str(num).zfill(nd or 4),
            "fecha_liq": fl.strftime("%d/%m/%Y") if fl else "",
        })
    return JSONResponse({"items": items, "total": len(items)})
