"""Consolidado del cobrador: cuánto cobró, cuánto se le debía y cuánto entrega.

Movido desde routers/cobranza.py (relevamiento, punto 7). Solo cálculos: el
router arma las pantallas (hoja del mes, su PDF, consolidado de todos, resumen
para la institución) y el dashboard / contabilidad importan estas funciones.
Tests: tests/test_consolidado_cobrador.py.

  consolidado_mes      hoja de UN mes: un renglón por planilla (cobrado, comisión,
                       neto, sin cobrar, bajas, efectividad) + entregas y saldo.
  resumen_meses        histórico: un renglón por mes con movimiento.
  tasas_mensuales      % cobrado de cada mes (mismos números que la hoja).
  resumen_institucion  un renglón por cobrador para la comisión directiva.
  periodos_cobranza    meses con movimiento (navegador ← → del resumen).

PONDERACIÓN POR PATA: una cuota de PATA 2 cuenta 2, de PATA 0 cuenta 0.67 (en
las hojas, las X0 se cuentan aparte de a una). Una planilla toda de PATA 0 cuenta
×1. Una planilla arranca a cobrarse el mes SIGUIENTE al de entrega.
"""
import json

from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from .. import models
from ..tiempo import hoy_ar, match_periodo, parse_periodo
from .liquidacion_cobranza import cuota_cuenta_aca, mapa_recibidas, valor_cuota

MESES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
         "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]


def pata_valor(boleta) -> float:
    """Ponderación por PATA = multiplicador de la talonera
    (PATA 0=0.67, 1=1, 2=2, 3=3, 4=4, 6=6...).
    OJO: antes parseaba el nombre buscando los dígitos 1/2/3, por lo que PATA 0, 4,
    5, 6, 8 devolvían 1 (mal contabilizado). Ahora usa el multiplicador real."""
    if boleta.talonera and boleta.talonera.multiplicador:
        return float(boleta.talonera.multiplicador)
    return 1.0


def es_x0(boleta) -> bool:
    """True si la boleta es de una talonera PATA 0 (las de importe chico, que no
    se ponderan: se cuentan de a una). Las X1, X2, X3... se suman por PATA."""
    return bool(boleta.talonera) and float(boleta.talonera.multiplicador or 1.0) < 1.0


def planilla_todo_pata0(boletas) -> bool:
    """True si TODAS las boletas (con talonera) de la planilla son PATA 0
    (multiplicador < 1). En ese caso las cuotas se cuentan ×1 y valen el importe
    uniforme real ($10.000), sin aplicar el 0.67 (no hay PATA 1 con que comparar)."""
    bs = [b for b in boletas if b.talonera]
    return bool(bs) and all((b.talonera.multiplicador or 1.0) < 1.0 for b in bs)


def mapa_pasados(boletas, planilla_id) -> dict:
    """Para las boletas que SALIERON de esta planilla (paso_origen_planilla_id ==
    planilla_id y ya no la tienen como planilla_id actual), devuelve
    {boleta_id: {"label": <texto>, "cuota": <cuotas pagadas al pasar>}}.
    El template dibuja sobre esas boletas la línea "PASÓ A <label>"."""
    out = {}
    for b in boletas:
        if (getattr(b, "paso_origen_planilla_id", None) == planilla_id
                and b.planilla_id != planilla_id):
            out[b.id] = {
                "label": (b.paso_a or "OTRA PLANILLA"),
                "cuota": int(b.paso_cuota or 0),
            }
    return out


def resumen_meses(db, cobrador, solo_anio: int = 0, solo_mes: int = 0):
    """Resumen de cobranza de un cobrador con UN RENGLÓN POR MES liquidado.

    Recorre TODAS las planillas del cobrador y agrupa por el mes calendario en
    que se cobró cada cuota (historial_cuotas). Por cada mes devuelve:
      - cuotas: cuotas cobradas ese mes (ponderadas por PATA, igual criterio
        que el resto del sistema: PATA 2 vale 2, PATA 0 vale 0.67).
      - bajas: socios dados de baja ese mes.
      - saldo: saldo a entregar = cobrado − comisión − adelantos del mes.

    Además, por cada mes se calcula lo que se ESPERABA cobrar: las boletas
    vivas emplanilladas (no dadas de baja hasta ese mes, y que todavía tenían
    cuotas por pagar) de las planillas entregadas hasta ese mes, ponderadas por
    PATA. Las que no registraron ningún pago en el mes suman a `sin_cobrar`, y
    el `pct` del renglón es cobradas / (cobradas + sin cobrar).

    `entregas` es la plata que el cobrador ya entregó por ese mes (adelantos +
    lo entregado al liquidar) y `saldo` lo que le queda por entregar
    (neto − entregas).

    Solo aparecen los meses con movimiento (cuotas, bajas o adelantos).

    Con `solo_anio`/`solo_mes` el resultado se RECORTA a ese período (para pasar
    a la institución solo el mes nuevo, ya que los anteriores se pasaron antes).
    El cálculo se hace igual sobre todos los meses — recortar antes rompería el
    arrastre de cuotas pagas — y el filtro se aplica al final. `periodos` viene
    siempre completo, para poder armar el selector."""
    planillas = (db.query(models.Planilla)
                 .filter_by(cobrador_id=cobrador.id)
                 .order_by(models.Planilla.anio, models.Planilla.mes,
                           models.Planilla.numero)
                 .all())

    por_periodo = {}          # (anio, mes) -> {cuotas, monto, comision}
    bajas_por_mes = {}        # mes (1-12) -> cantidad de socios dados de baja
    infos = []                # una entrada por boleta, para el cálculo de esperado
    anios_vistos = set()
    for p in planillas:
        pct = float(p.comision_pct or 0)
        boletas = db.query(models.Boleta).filter_by(planilla_id=p.id).all()
        todo0 = planilla_todo_pata0(boletas)
        p_anio = int(p.anio or 0)
        p_mes = int(p.mes or 0)
        if p_anio:
            anios_vistos.add(p_anio)
        for b in boletas:
            # Baja: mes_baja es un registro único por boleta (sin año).
            mes_baja = 0
            if b.mes_baja:
                try:
                    _mb = int(b.mes_baja)
                    if 1 <= _mb <= 12:
                        mes_baja = _mb
                        bajas_por_mes[_mb] = bajas_por_mes.get(_mb, 0) + 1
                except (TypeError, ValueError):
                    pass
            try:
                hist = json.loads(b.historial_cuotas) if b.historial_cuotas else {}
            except (ValueError, TypeError):
                hist = {}
            vc = valor_cuota(b)
            peso = 1.0 if todo0 else pata_valor(b)
            pagos = []            # períodos (anio|0, mes) en que pagó
            for v in hist.values():
                per = parse_periodo(v)
                if per is None:
                    continue
                anio_p, mes_p = per
                key = (anio_p or 0, mes_p)      # legacy sin año -> 0
                if anio_p:
                    anios_vistos.add(anio_p)
                pagos.append(key)
                d = por_periodo.setdefault(key, {"cuotas": 0.0, "monto": 0.0, "comision": 0.0})
                d["cuotas"] += peso
                d["monto"] += vc
                d["comision"] += vc * pct / 100.0
            infos.append({
                "peso": peso,
                "desde": (p_anio, p_mes),
                "mes_baja": mes_baja,
                "pactadas": int(b.cuotas_pactadas or 0),
                "anticipadas": int(b.cuotas_anticipadas or 0),
                "pagos": pagos,
            })

    # Adelantos entregados por el cobrador, agrupados por período.
    adel_por_periodo = {}
    for a in (db.query(models.EntregaCobrador)
              .filter_by(cobrador_id=cobrador.id).all()):
        key = (int(a.anio or 0), int(a.mes or 0))
        adel_por_periodo[key] = adel_por_periodo.get(key, 0.0) + float(a.monto or 0)
        if a.anio:
            anios_vistos.add(int(a.anio))

    # Un renglón por mes con movimiento (cuotas cobradas o adelantos).
    claves = sorted(set(por_periodo) | set(k for k in adel_por_periodo if 1 <= k[1] <= 12))

    # Los datos legacy no guardan año: para poder ORDENAR períodos entre sí les
    # asignamos el año de referencia de la campaña (el más chico que se haya
    # visto), así "mes 7 sin año" no se compara contra 0*12+7 y rompe todo.
    anio_ref = min(anios_vistos) if anios_vistos else hoy_ar().year

    def _ord(anio, mes):
        return (anio or anio_ref) * 12 + (mes or 1)

    # Primer renglón (cronológico) que tiene cada mes: ahí se cuelgan las bajas,
    # que no guardan año.
    baja_ord_de_mes = {}
    for (a_k, m_k) in claves:
        baja_ord_de_mes.setdefault(m_k, _ord(a_k, m_k))

    filas = []
    bajas_asignadas = set()
    for (anio_p, mes_p) in claves:
        d = por_periodo.get((anio_p, mes_p), {"cuotas": 0.0, "monto": 0.0, "comision": 0.0})
        adel = adel_por_periodo.get((anio_p, mes_p), 0.0)
        # Las bajas no guardan año: se cuelgan del primer renglón con ese mes.
        if mes_p in bajas_asignadas:
            bajas = 0
        else:
            bajas = bajas_por_mes.get(mes_p, 0)
            bajas_asignadas.add(mes_p)

        # ── Cuotas del mes que quedaron SIN COBRAR ──────────────────────────
        # Boletas vivas emplanilladas ese mes que no registraron ningún pago.
        fila_ord = _ord(anio_p, mes_p)
        sin_cobrar = 0.0
        for bi in infos:
            # La cobranza de una planilla arranca el MES SIGUIENTE al de
            # entrega (planilla de mayo → cuota 1 en junio), así que una
            # entregada en este mes todavía no se le puede exigir.
            if _ord(*bi["desde"]) >= fila_ord:
                continue
            # Dada de baja en ese mes o antes → ya no se le espera cobranza.
            if bi["mes_baja"] and baja_ord_de_mes.get(bi["mes_baja"], 0) <= fila_ord:
                continue
            # Ya había terminado de pagar sus cuotas antes de este mes.
            pagadas_antes = bi["anticipadas"] + sum(
                1 for k in bi["pagos"] if _ord(*k) < fila_ord)
            if bi["pactadas"] and pagadas_antes >= bi["pactadas"]:
                continue
            # Viva y sin ningún pago registrado en el mes.
            if not any(_ord(*k) == fila_ord for k in bi["pagos"]):
                sin_cobrar += bi["peso"]

        cobradas = d["cuotas"]
        base_pct = cobradas + sin_cobrar
        pct_cobrado = round(cobradas / base_pct * 100) if base_pct > 0 else 0

        monto = round(d["monto"], 2)
        comision = round(d["comision"], 2)
        filas.append({
            "anio": anio_p, "mes": mes_p,
            "label": f"{MESES[mes_p - 1]}" + (f" {anio_p}" if anio_p else ""),
            "cuotas": cobradas,
            "sin_cobrar": round(sin_cobrar, 2),
            "pct": pct_cobrado,
            "bajas": bajas,
            "monto": monto,
            "comision": comision,
            "neto": round(monto - comision, 2),
            # `entregas` = plata ya entregada por ese mes. Se mantiene la clave
            # vieja `adelantos` por compatibilidad con lo que ya la consumía.
            "entregas": round(adel, 2),
            "adelantos": round(adel, 2),
            "saldo": round(monto - comision - adel, 2),
        })

    # Selector de mes: SIEMPRE con todos los períodos, aunque se esté filtrando.
    periodos = [{"anio": f["anio"], "mes": f["mes"], "label": f["label"],
                 "key": f"{f['anio']}-{f['mes']}"} for f in filas]

    if solo_mes:
        filas = [f for f in filas
                 if f["mes"] == int(solo_mes)
                 and (not solo_anio or not f["anio"] or f["anio"] == int(solo_anio))]

    tot_cuotas = sum(f["cuotas"] for f in filas)
    tot_sin = round(sum(f["sin_cobrar"] for f in filas), 2)
    base_tot = tot_cuotas + tot_sin
    totales = {
        "cuotas": tot_cuotas,
        "sin_cobrar": tot_sin,
        "pct": round(tot_cuotas / base_tot * 100) if base_tot > 0 else 0,
        "bajas": sum(f["bajas"] for f in filas),
        "monto": round(sum(f["monto"] for f in filas), 2),
        "comision": round(sum(f["comision"] for f in filas), 2),
        "neto": round(sum(f["neto"] for f in filas), 2),
        "entregas": round(sum(f["entregas"] for f in filas), 2),
        "adelantos": round(sum(f["adelantos"] for f in filas), 2),
        "saldo": round(sum(f["saldo"] for f in filas), 2),
    }
    return {"cobrador": cobrador, "filas": filas, "totales": totales,
            "periodos": periodos,
            "filtro": {"anio": int(solo_anio or 0), "mes": int(solo_mes or 0)}}


def tasas_mensuales(db, cobrador, periodos):
    """% cobrado de cada mes, con LOS MISMOS números que la hoja del mes.

    Por cada período (anio, mes) se pide el consolidado del cobrador y se mide
    lo que entró en la liquidación de ese mes contra lo que se le podía cobrar:

        % del mes = cobradas / (cobradas + sin cobrar)

    No importa cuándo se armó la planilla: si sus cuotas entraron en la
    liquidación del mes, cuentan arriba Y abajo. Antes las tarjetas hacían su
    propia cuenta (cuotas cobradas ÷ boletas activas), que contaba cuotas arriba
    y boletas abajo y dejaba fuera del denominador a las planillas armadas ese
    mismo mes: por eso mostraban meses de 135%.
    """
    out = []
    # Las planillas y sus boletas son las mismas para todos los meses: se leen
    # una sola vez (antes, un SELECT por planilla y por mes).
    cache = {}
    for (a, m) in sorted(periodos):
        d = consolidado_mes(db, cobrador, m, a, cache=cache)
        cob = d["cuotas_pata"] + d["cuotas_x0"]
        base = cob + d["sin_pata"] + d["sin_x0"]
        if base <= 0:
            continue
        out.append({
            "mes": f"{MESES[m - 1]} {a}", "num": m, "anio": a,
            "cobradas": round(cob, 2), "activas": round(base, 2),
            "pct": round(cob / base * 100),
        })
    return out


def consolidado_mes(db, cobrador, mes, anio, cache=None):
    """Cobranza de UN mes de un cobrador, con un renglón por planilla.

    Por cada planilla, además de lo cobrado, se calcula lo que quedó SIN COBRAR
    ese mes y las bajas registradas, para poder mostrar el % de efectividad:
      - sin_cobrar: boletas que estaban en cobranza ese mes y no registraron
        ningún pago, ponderadas por PATA. Una planilla arranca a cobrarse el mes
        SIGUIENTE al de entrega, así que la recién entregada no suma.
      - bajas: socios de esa planilla dados de baja en ese mes.
      - efect (%): cobradas / (cobradas + sin cobrar).
    """
    detalle = []
    tot_monto = 0.0
    tot_com = 0.0
    tot_cuotas = 0.0
    tot_sin = 0.0
    tot_bajas = 0
    tot_cp = 0.0    # cobradas PATA (X1, X2...)
    tot_cz = 0.0    # cobradas X0
    tot_sp = 0.0    # sin cobrar PATA
    tot_sz = 0.0    # sin cobrar X0
    _ord_mes = anio * 12 + mes

    def _ord_pago(v):
        """Ordinal (anio*12+mes) de una entrada de historial. Los valores legacy
        no traen año: se asume el del período que se está mirando."""
        per = parse_periodo(v)
        if per is None:
            return None
        return (per[0] or anio) * 12 + per[1]

    # cache (opcional): dict que comparte quien pide varios meses seguidos del
    # mismo cobrador; solo guarda lecturas, no cambia ningún cálculo.
    if cache is None:
        cache = {}
    _kpl = ("planillas", cobrador.id)
    if _kpl not in cache:
        cache[_kpl] = (db.query(models.Planilla)
                       .filter_by(cobrador_id=cobrador.id)
                       .order_by(models.Planilla.anio, models.Planilla.mes, models.Planilla.numero)
                       .all())
    planillas = cache[_kpl]
    for p in planillas:
        pct = float(p.comision_pct or 0)
        # Además de las boletas que ESTÁN en la planilla se traen las que
        # SALIERON de ella ("pasar números"): las cuotas cobradas ACÁ antes del
        # pase son cobranza de ESTE cobrador y tienen que seguir contando en los
        # meses ya liquidados. Sin esto, mover un número en septiembre le
        # cambiaba el total de agosto y el saldo del cobrador dejaba de cerrar.
        _kbo = ("boletas", p.id)
        if _kbo not in cache:
            cache[_kbo] = (db.query(models.Boleta)
                           .options(joinedload(models.Boleta.talonera))
                           .filter(or_(models.Boleta.planilla_id == p.id,
                                       models.Boleta.paso_origen_planilla_id == p.id))
                           .all())
        boletas = cache[_kbo]
        propias = [b for b in boletas if b.planilla_id == p.id]
        paso_map = mapa_pasados(boletas, p.id)          # se fueron de acá
        recibida_map = mapa_recibidas(boletas, p.id)  # llegaron de otra
        todo0 = planilla_todo_pata0(propias or boletas)
        # La cobranza arranca el mes siguiente al de entrega de la planilla.
        _entregada_antes = (int(p.anio or 0) * 12 + int(p.mes or 0)) < _ord_mes
        m_pl = 0.0
        c_pl = 0.0
        s_pl = 0.0
        b_pl = 0
        # Cuotas abiertas en dos: las que se suman por PATA (X1, X2, X3... una
        # X2 cuenta 2, todas al mismo valor base) y las X0, que se cuentan de a
        # una. Sumarlas en un solo número mezclaba unidades y no dejaba
        # verificar el monto a ojo.
        cp_pl = 0.0   # cobradas PATA
        cz_pl = 0.0   # cobradas X0
        sp_pl = 0.0   # sin cobrar PATA
        sz_pl = 0.0   # sin cobrar X0
        # Pre-pasada: el historial y las cuotas del mes de cada boleta. Hace
        # falta saber ANTES si la planilla ya registró cobranza (ver abajo).
        _hist = {}
        _cuotas_mes = {}
        for b in boletas:
            try:
                h = json.loads(b.historial_cuotas) if b.historial_cuotas else {}
            except (ValueError, TypeError):
                h = {}
            _hist[b.id] = h
            # Corte por "pasar números": de una boleta que SALIÓ solo cuentan
            # acá las cuotas hasta paso_cuota; de una que ENTRÓ no cuentan las
            # que ya traía pagas (esas las cobró el cobrador anterior).
            _salio = b.id in paso_map
            _corte = int(paso_map[b.id].get("cuota") or 0) if _salio else None
            _rec = int(recibida_map.get(b.id, 0) or 0)
            # Cuotas cobradas en ESTE periodo (anio+mes). Antes comparaba solo
            # el mes y mezclaba julio 2026 con julio 2027 (fix C-1).
            _cuotas_mes[b.id] = sum(1 for k, v in h.items()
                                    if cuota_cuenta_aca(k, _corte, _rec)
                                    and match_periodo(v, anio, mes))
        # Una planilla entregada ESTE mes recién se cobra el mes que viene, así
        # que lo no cobrado no es culpa del cobrador. Pero si ya registró
        # cobranza, sí se estaba cobrando: mostrar "sin cobrar" vacío y 100%
        # inflaba la efectividad del mes.
        _cobrable = _entregada_antes or any(_cuotas_mes.values())
        for b in boletas:
            h = _hist[b.id]
            _mb = 0
            try:
                _mb = int(b.mes_baja) if b.mes_baja else 0
            except (TypeError, ValueError):
                _mb = 0
            _salio = b.id in paso_map
            if _mb == mes and not _salio:
                b_pl += 1
            peso = 1.0 if todo0 else pata_valor(b)
            _x0 = es_x0(b)
            cM = _cuotas_mes[b.id]
            if cM:
                vc = valor_cuota(b)
                m_pl += cM * vc
                c_pl += cM * peso
                if _x0:
                    cz_pl += cM
                else:
                    cp_pl += cM * pata_valor(b)
                continue
            # ── No cobró nada este mes: ¿se le podía cobrar? ────────────────
            if _salio:
                continue                       # ya no está en esta planilla
            if not _cobrable:
                continue                       # planilla recién entregada
            if _mb and _mb <= mes:
                continue                       # dada de baja en este mes o antes
            _pagadas_antes = int(b.cuotas_anticipadas or 0) + sum(
                1 for v in h.values()
                if (_ord_pago(v) or 0) and _ord_pago(v) < _ord_mes)
            if (b.cuotas_pactadas or 0) and _pagadas_antes >= (b.cuotas_pactadas or 0):
                continue                       # ya había terminado de pagar
            s_pl += peso
            if _x0:
                sz_pl += 1
            else:
                sp_pl += pata_valor(b)
        if m_pl or c_pl or s_pl or b_pl:
            com = round(m_pl * pct / 100.0, 2)
            _cobradas = cp_pl + cz_pl
            _base = _cobradas + sp_pl + sz_pl
            detalle.append({
                "numero": p.numero, "mes_planilla": p.mes, "anio_planilla": p.anio,
                "cuotas": c_pl, "monto": round(m_pl, 2), "pct": pct,
                "comision": com, "neto": round(m_pl - com, 2),
                "sin_cobrar": round(s_pl, 2), "bajas": b_pl,
                "cuotas_pata": round(cp_pl, 2), "cuotas_x0": round(cz_pl, 2),
                "sin_pata": round(sp_pl, 2), "sin_x0": round(sz_pl, 2),
                "efect": round(_cobradas / _base * 100) if _base > 0 else 0,
            })
            tot_cp += cp_pl
            tot_cz += cz_pl
            tot_sp += sp_pl
            tot_sz += sz_pl
            tot_monto += m_pl
            tot_com += com
            tot_cuotas += c_pl
            tot_sin += s_pl
            tot_bajas += b_pl
    neto = round(tot_monto - tot_com, 2)
    adelantos = (db.query(models.EntregaCobrador)
                 .filter_by(cobrador_id=cobrador.id, mes=mes, anio=anio)
                 .order_by(models.EntregaCobrador.fecha)
                 .all())
    tot_adel = sum(float(a.monto or 0) for a in adelantos)
    adel_premio = sum(float(a.monto or 0) for a in adelantos if (a.tipo or "EFECTIVO").upper() == "PREMIO")
    # Saldo que quedó pendiente del mes anterior (tipo SALDO_ANT, guardado en
    # negativo). Se muestra aparte: suma al saldo, no es plata entregada.
    saldo_anterior = -sum(float(a.monto or 0) for a in adelantos
                          if (a.tipo or "").upper() == "SALDO_ANT")
    entregado_real = tot_adel + saldo_anterior
    adel_efectivo = round(entregado_real - adel_premio, 2)
    return {
        "cobrador": cobrador,
        "detalle": detalle,
        "adelantos": adelantos,
        "cuotas": int(round(tot_cuotas)),
        "cuotas_exact": round(tot_cuotas, 2),
        # Si todas las planillas comparten el mismo % de comisión, la hoja lo
        # aclara UNA vez al pie en vez de repetirlo en cada renglón (con 8
        # columnas, el "(15%)" adentro de la celda la partía en dos líneas).
        "pct_uniforme": ({d["pct"] for d in detalle}.pop()
                         if len({d["pct"] for d in detalle}) == 1 else None),
        "sin_cobrar": round(tot_sin, 2),
        "bajas": tot_bajas,
        "cuotas_pata": round(tot_cp, 2),
        "cuotas_x0": round(tot_cz, 2),
        "sin_pata": round(tot_sp, 2),
        "sin_x0": round(tot_sz, 2),
        "efect": (round((tot_cp + tot_cz) / (tot_cp + tot_cz + tot_sp + tot_sz) * 100)
                  if (tot_cp + tot_cz + tot_sp + tot_sz) > 0 else 0),
        "monto": round(tot_monto, 2),
        "comision": round(tot_com, 2),
        "neto": neto,
        "total_adelantos": round(tot_adel, 2),
        "adel_efectivo": adel_efectivo,
        "adel_premio": round(adel_premio, 2),
        "saldo_anterior": round(saldo_anterior, 2),
        "entregado_real": round(entregado_real, 2),
        "entregas_reales": [a for a in adelantos
                            if (a.tipo or "").upper() != "SALDO_ANT"],
        "saldos_anteriores": [a for a in adelantos
                              if (a.tipo or "").upper() == "SALDO_ANT"],
        "saldo": round(neto - tot_adel, 2),
    }


def resumen_institucion(db, mes: int, anio: int):
    """Resumen de la cobranza de UN mes para la institución: un renglón por
    cobrador con cuotas cobradas, monto, comisión, neto, entregado y saldo.
    Es la foto que se pasa a la comisión directiva al cerrar el mes."""
    cobradores = db.query(models.Cobrador).order_by(models.Cobrador.nombre).all()
    filas = []
    for c in cobradores:
        d = consolidado_mes(db, c, mes, anio)
        # Solo los que tuvieron movimiento en el mes: un listado con diez
        # cobradores en cero no le dice nada a nadie.
        if not (d["monto"] or d["total_adelantos"]):
            continue
        filas.append({
            "cobrador": c,
            "planillas": len(d["detalle"]),
            "cuotas": d["cuotas"],
            "cuotas_pata": d["cuotas_pata"],
            "cuotas_x0": d["cuotas_x0"],
            "monto": d["monto"],
            "comision": d["comision"],
            "neto": d["neto"],
            "entregas": d["total_adelantos"],
            "adel_efectivo": d["adel_efectivo"],
            "adel_premio": d["adel_premio"],
            "saldo": d["saldo"],
        })
    totales = {
        k: round(sum(f[k] for f in filas), 2)
        for k in ("monto", "comision", "neto", "entregas",
                  "adel_efectivo", "adel_premio", "saldo",
                  "cuotas_pata", "cuotas_x0")
    }
    totales["cuotas"] = sum(f["cuotas"] for f in filas)
    totales["planillas"] = sum(f["planillas"] for f in filas)
    return {"filas": filas, "totales": totales}


def periodos_cobranza(db):
    """Meses (anio, mes) en los que la institución tuvo movimiento de cobranza:
    cuotas cobradas en cualquier planilla o entregas de cualquier cobrador.
    Ordenados de más viejo a más nuevo. Alimenta el navegador ← → del resumen
    del mes y define cuál se abre por defecto (el último)."""
    per = set()
    for (h,) in (db.query(models.Boleta.historial_cuotas)
                 .filter(models.Boleta.historial_cuotas.isnot(None)).all()):
        try:
            hist = json.loads(h) if h else {}
        except (ValueError, TypeError):
            continue
        for v in hist.values():
            p = parse_periodo(v)
            if p:
                per.add((p[0] or 0, p[1]))
    for a, m in db.query(models.EntregaCobrador.anio, models.EntregaCobrador.mes).all():
        if a and m and 1 <= int(m) <= 12:
            per.add((int(a), int(m)))
    # Los datos legacy no guardan año: se les asigna el año más viejo visto,
    # sino ordenarían antes de todo (0*12+mes).
    _anios = {a for (a, _m) in per if a}
    _ref = min(_anios) if _anios else hoy_ar().year
    norm = sorted({(a or _ref, m) for (a, m) in per})
    return [{"anio": a, "mes": m, "label": f"{MESES[m - 1]} {a}", "key": f"{a}-{m}"}
            for (a, m) in norm]
