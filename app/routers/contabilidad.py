from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session, joinedload
from datetime import date
from typing import Optional
from .. import models, auth as auth_module
from ..templates_config import templates
from ..database import get_db
# Cuotas cobrables segun la fecha (las ultimas van de regalo). Ver app/cuotas.py.
from ..cuotas import (cuotas_vigentes, campana, invalidar_campana,
                      CAMPANA_DEFAULTS, CAMPANA_CLAVES)

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
    from .cobranza import _consolidado_cobrador, _periodos_cobranza
    from datetime import date as _date_hoy
    _mes_actual_key = (_date_hoy.today().year, _date_hoy.today().month)

    _cobradores_all = db.query(models.Cobrador).order_by(models.Cobrador.nombre).all()
    _periodos = _periodos_cobranza(db)                 # [{anio, mes, ...}]
    rec_real      = {}   # (anio, mes) -> {"monto","comision","neto"}
    _real_por_cob = {}   # cid -> {(mes, anio): {"bruto","comision","neto"}}
    _cob_real     = {}   # cid -> acumulado por cobrador (para el historial)
    # Efectividad real de cobranza por cobrador (cobradas / (cobradas+sin cobrar)),
    # SOLO sobre meses ya cerrados. El mes en curso todavía no terminó de cobrarse,
    # así que arrastra "sin cobrar" y hundiría la tasa (por eso antes daba ~50% en
    # vez del ~90% real). Es la misma efectividad que muestran las hojas.
    _cob_efect    = {}   # cid -> [cobradas_ponderadas, sin_cobrar_ponderadas]
    for _p in _periodos:
        _a, _m = _p["anio"], _p["mes"]
        _mes_cerrado = (_a, _m) < _mes_actual_key
        for _c in _cobradores_all:
            _d = _consolidado_cobrador(db, _c, _m, _a)
            if not (_d["monto"] or _d["sin_cobrar"]):
                continue
            _cid = _c.id
            if _mes_cerrado:
                _ef = _cob_efect.setdefault(_cid, [0.0, 0.0])
                _ef[0] += _d["cuotas_exact"]
                _ef[1] += _d["sin_cobrar"]
            if not _d["monto"]:
                continue
            _t = rec_real.setdefault((_a, _m), {"monto": 0.0, "comision": 0.0, "neto": 0.0})
            _t["monto"]    += _d["monto"]
            _t["comision"] += _d["comision"]
            _t["neto"]     += _d["neto"]
            _real_por_cob.setdefault(_cid, {})[(_m, _a)] = {
                "bruto":    _d["monto"],
                "comision": _d["comision"],
                "neto":     _d["neto"],
                "cuotas":   _d["cuotas_exact"],   # cuotas realmente cobradas (ponderadas)
            }
            _cr = _cob_real.setdefault(_cid, {
                "nombre":        _c.nombre,
                "total_monto":   0.0, "total_comision": 0.0, "total_neto": 0.0,
                "liquidaciones": [],
            })
            _cr["total_monto"]    += _d["monto"]
            _cr["total_comision"] += _d["comision"]
            _cr["total_neto"]     += _d["neto"]
            _cr["liquidaciones"].append({
                "fecha":      "",
                "mes_nombre": MESES[_m - 1],
                "anio":       _a,
                "monto":      _d["monto"],
                "comision":   _d["comision"],
                "neto":       _d["neto"],
            })

    total_com_cobradores = sum(t["comision"] for t in rec_real.values())
    # Recaudado por COBRANZA = lo efectivamente cobrado según las hojas
    total_recaudado = sum(t["monto"] for t in rec_real.values())
    # "Cobrado hasta la fecha": lo de los cobradores hasta el último mes liquidado
    cobrado_hasta_neto = sum(t["neto"] for t in rec_real.values())
    _ult_rec = max(rec_real) if rec_real else None
    cobrado_hasta_label = f"{MESES[_ult_rec[1] - 1]} {_ult_rec[0]}" if _ult_rec else "—"

    # ── Ingreso real total (para el % de avance) ─────────────────────────
    # No toda la plata entra por cobranza: los contados y la cuota 1 (anticipadas)
    # las cobra el vendedor al vender. Medir el avance solo con la cobranza
    # subestimaba el porcentaje (daba 8% cuando en realidad había entrado mucho más).
    ingreso_anticipadas = sum(
        (b.cuotas_anticipadas or 0) * (b.talonera.valor_cuota if b.talonera else 0)
        for b in boletas if not _es_contado(b)
    )
    ingreso_contado = gross_contado
    total_ingresado = total_recaudado + ingreso_anticipadas + ingreso_contado
    pct_avance = round(total_ingresado / total_esperado * 100, 1) if total_esperado else 0

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
    total_premios_comprometidos = 0.0
    for p in premios_orden:
        n = len(p.entregas)
        so = p.sorteo
        # Premios del sorteo FINAL por posición (1°, 2°, 3°): salen sí o sí,
        # siempre hay ganador. Se cuentan como egreso comprometido aunque
        # todavía no esté asignado el ganador — si no, la ganancia proyectada
        # queda inflada hasta el día del sorteo.
        _comprometido = False
        if n == 0:
            _es_final_posicion = (
                so is not None
                and getattr(so.tipo, "value", so.tipo) == "FINAL"
                and (p.modalidad or "POSICION") == "POSICION"
            )
            if not _es_final_posicion:
                continue
            n = 1
            _comprometido = True
        subtotal = (p.monto or 0) * n
        total_premios += subtotal
        if _comprometido:
            total_premios_comprometidos += subtotal
        tipo_lbl = _TIPO_LBL.get(so.tipo.value, so.tipo.value) if so else ""
        premios_list.append({
            "descripcion": p.descripcion,
            "sorteo":      (so.nombre + " · " if so and so.nombre else "") + tipo_lbl if so else "",
            "fecha":       so.fecha.strftime("%d/%m/%Y") if so and so.fecha else "",
            "monto":       p.monto or 0,
            "ganadores":   n,
            "subtotal":    subtotal,
            "comprometido": _comprometido,
        })
    premios_list.sort(key=lambda x: (x["fecha"], x["descripcion"]))

    # Egresos reales (comisiones ya liquidadas + premios con ganador)
    total_egresos = (total_com_vendedores + total_com_cobradores
                     + total_bomberos + total_gastos + total_premios)
    # Ganancia real = TODO lo que entró (cobranza + contados + cuota 1) − egresos.
    # Antes usaba solo la cobranza y daba un rojo enorme que no era real.
    ganancia_neta = total_ingresado - total_egresos
    # Egresos que no dependen de la cobranza (las comisiones se descuentan
    # más abajo dentro de la proyección, para no contarlas dos veces).
    com_vendedores_cuotas = total_com_vendedores - com_vendedores_contado
    egresos_fijos = total_bomberos + total_gastos + total_premios


    # ── Proyección mensual por cobrador ─────────────────────────────────
    # Cada boleta tiene SU PROPIO calendario: la cuota 1 la cobra el vendedor en
    # el mes de la venta y la cuota k vence en (mes de venta + k − 1). Antes la
    # cuota N de cualquier boleta se imputaba al mes N de la campaña (cuota 1 =
    # mayo), lo que solo valía para las vendidas en mayo: a las vendidas después
    # se les perdían cuotas y la proyección "se caía" en marzo/abril (ahí cortaban
    # las de 10 y 11 cuotas pactadas). Ver app/cuotas.py (cuotas_vigentes).
    # Las cuotas atrasadas (vencidas sin cobrar) se corren al final del
    # calendario del socio: una cuota por mes desde el mes actual, sin pasar de
    # Julio 2027 (lo que no entra se acumula en ese mes).
    from ..tiempo import hoy_ar

    def _idx(anio, mes):
        return anio * 12 + mes - 1

    # Fechas de la campaña: configurables (Contabilidad → Egresos → Campaña).
    _camp = campana()
    _IDX_INICIO = _idx(*_camp["inicio"])     # inicio de venta (mayo 2026)
    _IDX_SORTEO = _idx(*_camp["sorteo"])     # sorteo final (junio 2027)
    # Último mes con cobranza: sorteo + N meses (configurable; julio 2027). Lo
    # que quede atrasado más allá de ese mes se acumula ahí — no se proyecta después.
    _IDX_LIMITE = _idx(*_camp["limite"])
    _hoy = hoy_ar()
    _IDX_HOY = _idx(_hoy.year, _hoy.month)

    # _real_por_cob ya se construyó arriba desde el motor de las hojas de
    # liquidación (historial_cuotas por período real). Keys: (mes, anio).

    # Boletas activas con cobrador y talonera
    boletas_con_cob = [
        b for b in boletas
        if b.cobrador_id is not None and b.talonera is not None
    ]

    # Ponderación por PATA, igual criterio que las hojas de liquidación:
    # el multiplicador de la talonera, salvo que TODA la planilla sea PATA 0
    # (ahí las cuotas cuentan ×1, porque no hay PATA 1 con que comparar).
    from .cobranza import _pata_valor, _planilla_todo_pata0
    _pl_boletas = {}
    for b in boletas_con_cob:
        if b.planilla_id:
            _pl_boletas.setdefault(b.planilla_id, []).append(b)
    _pl_todo0 = {pid: _planilla_todo_pata0(bs) for pid, bs in _pl_boletas.items()}

    def _peso_pata(b):
        if b.planilla_id and _pl_todo0.get(b.planilla_id):
            return 1.0
        return _pata_valor(b)

    # Info de cobradores únicos
    _cob_info = {}
    for b in boletas_con_cob:
        if b.cobrador_id not in _cob_info and b.cobrador:
            _cob_info[b.cobrador_id] = {
                "nombre":       b.cobrador.nombre,
                "comision_pct": float(b.cobrador.comision_pct or 0),
            }

    # Tasa = efectividad real de cobranza de cada cobrador, la MISMA que muestran
    # las hojas de liquidación: cobradas / (cobradas + sin cobrar) sobre los meses
    # ya cerrados. (El cálculo anterior contaba contra "cuotas vencidas" incluyendo
    # el mes en curso sin cobrar todavía, y daba ~50% cuando en la práctica se
    # cobra ~90%.)
    _cob_tasa = {}
    for cid in _cob_info:
        _ef = _cob_efect.get(cid)
        if _ef and (_ef[0] + _ef[1]) > 0:
            _cob_tasa[cid] = round(_ef[0] / (_ef[0] + _ef[1]), 4)
        else:
            _cob_tasa[cid] = 1.0   # sin historial cerrado → proyección al 100%

    # Promedios de la institución (para lo que todavía no tiene cobrador y para
    # las ventas futuras estimadas): tasa global de los meses cerrados y comisión
    # de cobrador ponderada por cantidad de boletas.
    _ef_ok = sum(v[0] for v in _cob_efect.values())
    _ef_no = sum(v[1] for v in _cob_efect.values())
    _tasa_global = round(_ef_ok / (_ef_ok + _ef_no), 4) if (_ef_ok + _ef_no) > 0 else 0.9
    _n_por_cob = {}
    for b in boletas_con_cob:
        _n_por_cob[b.cobrador_id] = _n_por_cob.get(b.cobrador_id, 0) + 1
    _n_cob_tot = sum(n for c, n in _n_por_cob.items() if c in _cob_info)
    _com_prom_pct = (sum(_cob_info[c]["comision_pct"] * n for c, n in _n_por_cob.items()
                         if c in _cob_info) / _n_cob_tot) if _n_cob_tot else 15.0

    # ── "Sin cobrador asignado": ventas ya liquidadas al vendedor que todavía
    # no tienen cobrador (o ni siquiera socio cargado). Son ventas FIRMES: la
    # cuota 1 ya la cobró el vendedor y el resto se va a cobrar igual, así que
    # entran a la proyección con la tasa y comisión promedio. Cuando se les
    # asigna cobrador pasan solas a la columna de ese cobrador.
    _fecha_liq = {lv.id: lv.fecha for lv in liqs_v}
    _sin_socio = (
        db.query(models.Boleta)
        .filter(models.Boleta.comprador_id.is_(None),
                models.Boleta.liquidacion_vendedor_id.isnot(None),
                models.Boleta.condicion != models.CondicionBoleta.BAJA)
        .options(joinedload(models.Boleta.talonera))
        .all()
    )
    _SIN = "sin_cobrador"
    _boletas_sin_cob = [
        b for b in list(boletas) + _sin_socio
        if b.cobrador_id is None and b.talonera is not None
        and (b.talonera.tipo or "COMUN") == "COMUN" and not _es_contado(b)
    ]
    if _boletas_sin_cob:
        _cob_info[_SIN] = {"nombre": "Sin cobrador asignado",
                           "comision_pct": round(_com_prom_pct, 1)}
        _cob_tasa[_SIN] = _tasa_global

    def _calendario_pendiente(b, desde_idx):
        """Índices de mes (anio*12+mes-1) en que se espera cobrar cada cuota
        pendiente de la boleta. Nunca antes de su vencimiento ni antes de
        `desde_idx`; las atrasadas se corren una por mes hacia el final, con
        tope en _IDX_LIMITE (lo que no entra se acumula en ese último mes)."""
        pactadas = b.cuotas_pactadas or 0
        nc       = b.talonera.num_cuotas or 12
        tope     = min(pactadas, nc)
        hechas   = max(b.cuotas_pagadas or 0, b.cuotas_anticipadas or 0,
                       1 if b.liquidacion_vendedor_id else 0)  # cuota 1 = vendedor
        if tope <= hechas:
            return []
        fv   = b.fecha_venta or _fecha_liq.get(b.liquidacion_vendedor_id)
        base = _idx(fv.year, fv.month) if fv else _IDX_INICIO
        out, prox = [], desde_idx
        for k in range(hechas + 1, tope + 1):
            mi = min(max(base + k - 1, prox), _IDX_LIMITE)
            out.append(mi)
            prox = mi + 1
        return out

    # Calendario proyectado por cobrador: cid -> {idx_mes: [bruto, cant]}
    _cob_cal = {}
    for cid in _cob_info:
        real_idx = [_idx(a, m) for (m, a) in _real_por_cob.get(cid, {})]
        # Proyectar desde el mes actual, o desde el siguiente al último liquidado
        desde = max([_IDX_HOY] + [i + 1 for i in real_idx])
        cal = {}
        _bs = _boletas_sin_cob if cid == _SIN else \
            [b for b in boletas_con_cob if b.cobrador_id == cid]
        for b in _bs:
            vc = b.talonera.valor_cuota or 0
            peso = _peso_pata(b)
            for mi in _calendario_pendiente(b, desde):
                acc = cal.setdefault(mi, [0.0, 0.0])
                acc[0] += vc
                acc[1] += peso
        _cob_cal[cid] = cal

    # Meses de la tabla: Mayo 2026 → hasta el sorteo final, o más si hay
    # cuotas corridas después (atrasadas) o liquidaciones reales posteriores.
    _idx_fin = _IDX_SORTEO
    for cid in _cob_info:
        _idx_fin = max([_idx_fin] + list(_cob_cal[cid].keys())
                       + [_idx(a, m) for (m, a) in _real_por_cob.get(cid, {})])
    proyeccion_meses = []
    for mi in range(_IDX_INICIO, _idx_fin + 1):
        _m, _a = mi % 12 + 1, mi // 12
        proyeccion_meses.append({"mes": _m, "anio": _a, "mes_nombre": MESES[_m - 1]})

    # Para cada cobrador, armar los meses mezclando reales + proyectados
    _cob_proyeccion = {}
    for cid, info in _cob_info.items():
        meses_proj = []
        pct  = info["comision_pct"] / 100.0
        tasa = _cob_tasa[cid]
        real_mes = _real_por_cob.get(cid, {})
        cal = _cob_cal[cid]

        for n, pm in enumerate(proyeccion_meses, start=1):
            mes, anio = pm["mes"], pm["anio"]
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
                    "cant":       r.get("cuotas", 0),   # cuotas realmente cobradas
                    "es_real":    True,
                    "tasa":       None,
                })
            else:
                # ── Mes futuro: cuotas que vencen ese mes × tasa de cobro ──
                # Cuotas PONDERADAS POR PATA, igual que en las hojas de
                # liquidación (una PATA 2 cuenta como 2 cuotas).
                bruto_teorico, cant = cal.get(_idx(anio, mes), (0.0, 0.0))
                # Si se cobra el 93%, no se van a cobrar las 668, sino ~621.
                cant     = round(cant * tasa, 2)
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
    ], key=lambda x: (x["nombre"] == "Sin cobrador asignado", x["nombre"]))

    # ── Ventas al contado por mes ────────────────────────────────────────
    # Cada contado se imputa al mes de su FECHA DE VENTA (es cuando entra la
    # plata). El bruto usa el mismo criterio que gross_contado. La comisión del
    # vendedor se prorratea: cada liquidación reparte su comision_contados entre
    # las boletas de contado que la integran, en proporción al monto de cada una.
    _contado_boletas = [b for b in boletas if _es_contado(b)]

    def _bruto_contado(b):
        return (b.cuotas_pactadas or cuotas_vigentes(
            b.talonera.num_cuotas if b.talonera else None, b.fecha_venta)) \
            * (b.talonera.valor_cuota if b.talonera else 0)

    # Monto total de contado agrupado por liquidación, para prorratear
    _lv_bruto = {}
    for b in _contado_boletas:
        if b.liquidacion_vendedor_id:
            _lv_bruto[b.liquidacion_vendedor_id] = \
                _lv_bruto.get(b.liquidacion_vendedor_id, 0.0) + _bruto_contado(b)
    _lv_com = {lv.id: (lv.comision_contados or 0) for lv in liqs_v}

    contados_por_mes = {}   # (anio, mes) -> {"cant","bruto","comision","neto"}
    for b in _contado_boletas:
        if not b.fecha_venta:
            continue
        _key = (b.fecha_venta.year, b.fecha_venta.month)
        _br = _bruto_contado(b)
        # Comisión proporcional dentro de su liquidación (0 si aún no se liquidó)
        _com = 0.0
        _lvid = b.liquidacion_vendedor_id
        if _lvid and _lv_bruto.get(_lvid):
            _com = _lv_com.get(_lvid, 0.0) * (_br / _lv_bruto[_lvid])
        _c = contados_por_mes.setdefault(
            _key, {"cant": 0, "bruto": 0.0, "comision": 0.0, "neto": 0.0})
        _c["cant"]     += 1
        _c["bruto"]    += _br
        _c["comision"] += _com
        _c["neto"]     += _br - _com

    # ── Ventas futuras ESTIMADAS ─────────────────────────────────────────
    # Ritmo de venta = boletas liquidadas a vendedores por mes, PONDERADAS por
    # PATA (una PATA 2 = 2, una PATA 0 = 0.67). Caída mensual = la que hubo
    # desde el mes de más ventas hasta el último mes completo (promedio
    # geométrico; si después del pico no hubo caída, 0%). Se vende mientras la
    # boleta tenga al menos _MIN_CUOTAS_VENTA cuotas vigentes (enero 2027 con el
    # sorteo en junio). Cada venta del mes M: la cuota 1 es del vendedor; la
    # institución cobra las cuotas 2..cv desde M+1, con tasa y comisión
    # promedio. Una fracción se vende al contado (entra todo en M, menos la
    # comisión de contado). Es una ESTIMACIÓN: se muestra aparte y NO entra en
    # el neto firme ni en la ganancia proyectada (el simulador hace lo suyo).
    _MIN_CUOTAS_VENTA = _camp["min_cuotas"]
    _pata1_vc = 0.0
    for _t in db.query(models.Talonera).all():
        if (_t.tipo or "COMUN") == "COMUN" and abs(float(_t.multiplicador or 0) - 1.0) < 1e-6 \
                and (_t.valor_cuota or 0) > 0:
            _pata1_vc = float(_t.valor_cuota)
            break
    _vend_mes = {}        # idx_mes -> boletas ponderadas vendidas
    _vend_n = _vend_cont = 0
    for b in list(todas_boletas) + _sin_socio:
        if not b.liquidacion_vendedor_id or b.talonera is None:
            continue
        if (b.talonera.tipo or "COMUN") != "COMUN":
            continue
        _f = _fecha_liq.get(b.liquidacion_vendedor_id) or b.fecha_venta
        if not _f:
            continue
        _k = _idx(_f.year, _f.month)
        _vend_mes[_k] = _vend_mes.get(_k, 0.0) + float(b.talonera.multiplicador or 1.0)
        _vend_n += 1
        if _es_contado(b):
            _vend_cont += 1
    _pct_contado_v = (_vend_cont / _vend_n) if _vend_n else 0.0
    _completos = sorted(k for k in _vend_mes if k < _IDX_HOY)
    est_pico_idx = est_ult_idx = None
    est_caida = 0.0
    est_base = 0.0
    if _completos:
        est_pico_idx = max(_completos, key=lambda k: _vend_mes[k])
        est_ult_idx = _completos[-1]
        est_base = _vend_mes[est_ult_idx]
        _gap = est_ult_idx - est_pico_idx
        if _gap > 0 and _vend_mes[est_pico_idx] > 0:
            _r = (est_base / _vend_mes[est_pico_idx]) ** (1.0 / _gap)
            est_caida = max(0.0, 1.0 - _r)
    # Último mes de venta: mientras queden >= _MIN_CUOTAS_VENTA cuotas vigentes
    est_ult_venta_idx = _IDX_SORTEO - (_MIN_CUOTAS_VENTA - 1)
    _com_cont_pct = float(getattr(liqs_v[-1], "comision_contados_pct", 0) or 30.0) \
        if liqs_v else 30.0
    _ventas_est = []      # [{"idx","mes_nombre","anio","boletas","cv"}]
    _est_cob = {}         # idx_mes -> [bruto, comision, cuotas]
    _est_cont = {}        # idx_mes -> [bruto, comision, cant]
    if est_ult_idx is not None and _pata1_vc > 0:
        for M in range(_IDX_HOY, est_ult_venta_idx + 1):
            P = est_base * (1.0 - est_caida) ** (M - est_ult_idx)
            if M == _IDX_HOY:                       # descontar lo ya vendido este mes
                P = max(0.0, P - _vend_mes.get(M, 0.0))
            if P <= 0:
                continue
            _ma, _mm = M // 12, M % 12 + 1
            cv = cuotas_vigentes(12, date(_ma, _mm, 1))
            _ventas_est.append({"idx": M, "mes_nombre": MESES[_mm - 1], "anio": _ma,
                                "boletas": round(P, 1), "cv": cv})
            # Al contado: entra todo en el mes de venta
            _bc = P * _pct_contado_v * cv * _pata1_vc
            if _bc:
                _e = _est_cont.setdefault(M, [0.0, 0.0, 0.0])
                _e[0] += _bc
                _e[1] += _bc * _com_cont_pct / 100.0
                _e[2] += P * _pct_contado_v
            # Por cuotas: cuotas 2..cv, una por mes desde M+1
            _pc = P * (1.0 - _pct_contado_v)
            for k in range(2, cv + 1):
                mi = min(M + k - 1, _IDX_LIMITE)
                _br = _pc * _pata1_vc * _tasa_global
                _e = _est_cob.setdefault(mi, [0.0, 0.0, 0.0])
                _e[0] += _br
                _e[1] += _br * _com_prom_pct / 100.0
                _e[2] += _pc * _tasa_global

    def _lbl(i):
        return f"{MESES[i % 12]} {i // 12}" if i is not None else "—"
    est_info = {
        "pico":        _lbl(est_pico_idx),
        "pico_boletas": round(_vend_mes.get(est_pico_idx, 0.0)) if est_pico_idx is not None else 0,
        "ultimo":      _lbl(est_ult_idx),
        "base":        round(est_base),
        "caida_pct":   round(est_caida * 100, 1),
        "ult_venta":   _lbl(est_ult_venta_idx),
        "min_cuotas":  _MIN_CUOTAS_VENTA,
        "pct_contado": round(_pct_contado_v * 100, 1),
        "tasa":        round(_tasa_global * 100, 1),
        "ventas":      _ventas_est,
        "total_boletas": round(sum(v["boletas"] for v in _ventas_est)),
    }

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
        _ct = contados_por_mes.get((pm["anio"], pm["mes"]),
                                   {"cant": 0, "bruto": 0.0, "comision": 0.0, "neto": 0.0})
        if _ct["cant"] and estado == "vacio":
            estado = "real"       # hubo ventas de contado ese mes
        _ki = _idx(pm["anio"], pm["mes"])
        _ec = _est_cob.get(_ki, [0.0, 0.0, 0.0])
        _en = _est_cont.get(_ki, [0.0, 0.0, 0.0])
        _est_neto = (_ec[0] - _ec[1]) + (_en[0] - _en[1])
        if _est_neto and estado == "vacio":
            estado = "proy"
        resumen_meses.append({
            "mes_nombre":     pm["mes_nombre"],
            "anio":           pm["anio"],
            "cuotas":         cuotas,
            "bruto":          bruto,
            "comision":       comision,
            "neto":           neto,
            "contados":       _ct["cant"],
            "contado_bruto":  _ct["bruto"],
            "contado_com":    _ct["comision"],
            "contado_neto":   _ct["neto"],
            "neto_total":     neto + _ct["neto"],
            "estado":         estado,
            "est_cuotas":     _ec[2],
            "cuotas_total_est": cuotas + _ec[2],
            "est_neto":       _est_neto,
            "neto_total_est": neto + _ct["neto"] + _est_neto,
        })

    resumen_cuotas         = sum(r["cuotas"]       for r in resumen_meses)
    resumen_bruto          = sum(r["bruto"]        for r in resumen_meses)
    resumen_comision       = sum(r["comision"]     for r in resumen_meses)
    resumen_neto           = sum(r["neto"]         for r in resumen_meses)
    resumen_contados       = sum(r["contados"]     for r in resumen_meses)
    resumen_contado_bruto  = sum(r["contado_bruto"] for r in resumen_meses)
    resumen_contado_com    = sum(r["contado_com"]  for r in resumen_meses)
    resumen_contado_neto   = sum(r["contado_neto"] for r in resumen_meses)
    # Neto final = neto de cobranza + neto de contados (ya sin comisiones)
    resumen_neto_final = resumen_neto + resumen_contado_neto
    # Ventas futuras estimadas (aparte, no entran en el neto firme)
    resumen_est_neto = sum(r["est_neto"] for r in resumen_meses)
    resumen_est_cuotas = sum(r["est_cuotas"] for r in resumen_meses)
    resumen_neto_final_est = resumen_neto_final + resumen_est_neto

    # ── Ganancia proyectada REALISTA ─────────────────────────────────────
    # Coherente con la tabla de arriba: parte del neto proyectado (que ya aplica
    # la tasa real de cobranza y descuenta comisiones de cobradores y de
    # vendedores por contado), le suma la cuota 1 que cobra el vendedor, y le
    # resta la comisión de vendedores por cuotas más los egresos fijos
    # (bomberos, gastos varios y premios, incluidos los comprometidos del final).
    # La versión anterior asumía cobranza al 100% y quedaba inflada.
    ganancia_proyectada = (resumen_neto_final + ingreso_anticipadas
                           - com_vendedores_cuotas - egresos_fijos)
    total_egresos_proyectado = (total_com_vendedores + resumen_comision
                                + egresos_fijos)

    # ── Datos base del SIMULADOR de ventas futuras ───────────────────────
    # Economía de una boleta NUEVA vendida en el mes M (ver app/cuotas.py):
    #   cv = cuotas_vigentes(num_cuotas, M)  → las últimas van de regalo
    #   · Por cuotas: la cuota 1 se la queda ENTERA el vendedor, así que a la
    #     institución le rinden las cuotas 2..cv, por la tasa de cobro y menos
    #     la comisión del cobrador.
    #   · Al contado: entra cv × valor_cuota menos la comisión del vendedor.
    # Vender tarde rinde mucho menos: en junio 2027 una venta por cuotas deja $0.
    _tot_ef_ok = sum(v[0] for v in _cob_efect.values())
    _tot_ef_no = sum(v[1] for v in _cob_efect.values())
    sim_tasa_cobro = round(_tot_ef_ok / (_tot_ef_ok + _tot_ef_no) * 100, 1) \
        if (_tot_ef_ok + _tot_ef_no) > 0 else 90.0

    # Comisión de cobradores: promedio ponderado por cantidad de boletas
    _cob_cnt = {}
    for b in boletas_con_cob:
        _cob_cnt[b.cobrador_id] = _cob_cnt.get(b.cobrador_id, 0) + 1
    _sum_pct = sum(_cob_info[c]["comision_pct"] * n
                   for c, n in _cob_cnt.items() if c in _cob_info)
    _sum_n = sum(n for c, n in _cob_cnt.items() if c in _cob_info)
    sim_com_cobrador_pct = round(_sum_pct / _sum_n, 1) if _sum_n else 15.0

    # Comisión de contado: la última usada por los vendedores
    _ult_lv = liqs_v[-1] if liqs_v else None
    sim_com_contado_pct = float(getattr(_ult_lv, "comision_contados_pct", 0) or 30.0)

    # Mezcla actual de ventas: proporción de cada talonera y % al contado
    _mix_cnt = {}
    _n_contado = 0
    for b in boletas:
        if not b.talonera_id:
            continue
        _mix_cnt[b.talonera_id] = _mix_cnt.get(b.talonera_id, 0) + 1
        if _es_contado(b):
            _n_contado += 1
    _n_total = sum(_mix_cnt.values())
    sim_pct_contado = round(_n_contado / _n_total * 100, 1) if _n_total else 0.0

    sim_taloneras = [
        {
            "id":          t.id,
            "nombre":      t.nombre,
            "valor_cuota": float(t.valor_cuota or 0),
            "num_cuotas":  int(t.num_cuotas or 12),
            "pct":         round(_mix_cnt.get(t.id, 0) / _n_total * 100, 1) if _n_total else 0.0,
        }
        for t in db.query(models.Talonera).order_by(models.Talonera.nombre).all()
        if _mix_cnt.get(t.id, 0) > 0 or t.activa
    ]

    # Zonas trabajadas — contexto de cuánto mercado queda en la ciudad
    _z_tot = db.query(models.Zona).count()
    _z_hechas = db.query(models.Zona).filter(models.Zona.hecha.is_(True)).count()
    sim_zonas_pct = round(_z_hechas / _z_tot * 100) if _z_tot else 0

    return templates.TemplateResponse(request, "contabilidad.html", {
        "sim_tasa_cobro":       sim_tasa_cobro,
        "sim_com_cobrador_pct": sim_com_cobrador_pct,
        "sim_com_contado_pct":  sim_com_contado_pct,
        "sim_pct_contado":      sim_pct_contado,
        "sim_taloneras":        sim_taloneras,
        "sim_zonas_pct":        sim_zonas_pct,
        "sim_zonas_hechas":     _z_hechas,
        "sim_zonas_total":      _z_tot,
        "sim_sorteo_anio":      _camp["sorteo"][0],
        "sim_sorteo_mes":       _camp["sorteo"][1],
        "camp":                 _camp,
        "sim_hoy_anio":         _mes_actual_key[0],
        "sim_hoy_mes":          _mes_actual_key[1],
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
        "total_premios_comprometidos": total_premios_comprometidos,
        "total_ingresado":       total_ingresado,
        "ingreso_anticipadas":   ingreso_anticipadas,
        "ingreso_contado":       ingreso_contado,
        "ganancia_proyectada":   ganancia_proyectada,
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
        "resumen_est_neto":      resumen_est_neto,
        "resumen_est_cuotas":    resumen_est_cuotas,
        "cobrado_hasta_neto":    cobrado_hasta_neto,
        "cobrado_hasta_label":   cobrado_hasta_label,
        "resumen_neto_final_est": resumen_neto_final_est,
        "est_info":              est_info,
        "proyeccion_meses":      proyeccion_meses,
        "resumen_meses":         resumen_meses,
        "resumen_cuotas":        resumen_cuotas,
        "resumen_bruto":         resumen_bruto,
        "resumen_comision":      resumen_comision,
        "resumen_neto":          resumen_neto,
        "resumen_contados":      resumen_contados,
        "resumen_contado_bruto": resumen_contado_bruto,
        "resumen_contado_com":   resumen_contado_com,
        "resumen_contado_neto":  resumen_contado_neto,
        "resumen_neto_final":    resumen_neto_final,
        "com_vendedores_contado": com_vendedores_contado,
    })


@router.post("/config/campana")
async def guardar_config_campana(
    request: Request,
    inicio: str = Form(...),          # "YYYY-MM"
    sorteo: str = Form(...),          # "YYYY-MM"
    post_sorteo: int = Form(1),
    min_cuotas: int = Form(6),
    db: Session = Depends(get_db),
):
    """Guarda las fechas de la campaña (cambian bono a bono). Afecta a las
    boletas que se carguen DESDE AHORA: las cuotas pactadas de las ya cargadas
    quedaron estampadas y no se recalculan."""
    user = await auth_module.require_user(request, db)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403)
    try:
        ia, im = (int(x) for x in inicio[:7].split("-"))
        sa, sm = (int(x) for x in sorteo[:7].split("-"))
    except (ValueError, TypeError):
        raise HTTPException(400, "Fechas inválidas (formato AAAA-MM)")
    if not (1 <= im <= 12 and 1 <= sm <= 12):
        raise HTTPException(400, "Mes inválido")
    if (sa, sm) <= (ia, im):
        raise HTTPException(400, "El sorteo final tiene que ser posterior al inicio")
    if not (0 <= post_sorteo <= 6) or not (1 <= min_cuotas <= 24):
        raise HTTPException(400, "Valores fuera de rango")
    for clave, val in (("campana_inicio_anio", ia), ("campana_inicio_mes", im),
                       ("sorteo_final_anio", sa), ("sorteo_final_mes", sm),
                       ("cobranza_meses_post_sorteo", post_sorteo),
                       ("venta_min_cuotas", min_cuotas)):
        _set_config(db, clave, float(val))
    invalidar_campana()
    return JSONResponse({"ok": True, "campana": campana()["valores"]})


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
