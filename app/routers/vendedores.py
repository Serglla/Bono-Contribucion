from fastapi import HTTPException, APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, case, and_, or_
from typing import Optional
import json
from .. import models, auth as auth_module
from ..models import CondicionBoleta
from ..templates_config import templates
from ..database import get_db

router = APIRouter(prefix="/vendedores", tags=["vendedores"])



def _stats_bulk(db):
    """Conteos por vendedor.
    - caja: CAJA sin liquidar (vendedor las tiene físicamente, sin liq_id)
    - liq_pendiente: CAJA con liq pero sin comprador cargado todavía
    - baja: BAJA (sub-estado informativo, solo viene de cobranza tras carga de socio)
    - liquidados_cuotas: ponderado por PATA de las boletas liquidadas EN CUOTAS
      (snapshot guardado en LiquidacionVendedor.cuotas_equiv).
    - liquidados_contados: ponderado por PATA de las boletas liquidadas AL CONTADO
      (snapshot guardado en LiquidacionVendedor.contados_equiv) + items del pool
      CONTADO declarados (números puros sin boleta).
    - liquidados: TOTAL liquidados = liquidados_cuotas + liquidados_contados.
      Reemplaza a "vendido" en la vista de listado — Sergio prefiere ver el total
      liquidado, mismo criterio que la tarjeta "Total liquidados" del detalle.
      OJO: se usan los SNAPSHOTS (cuotas_equiv, contados_equiv) y no la suma actual
      de talonera.multiplicador, porque la boleta no guarda su modalidad — solo la
      liquidación sabe distinguir cuotas vs contado.
    - vendido: se conserva la clave para compatibilidad (boletas con comprador_id).
    """
    # `liq_sin_comp` cuenta boletas CAJA con liq_id pero AÚN sin socio cargado.
    # Las al-contado pagadas se quedan en condicion=CAJA con liq_id Y comprador_id
    # cargado — esas NO son "pendientes de socio", ya están en sistema, así que
    # se cuentan en `vendido` (query separada abajo) y no acá.
    rows = db.query(
        models.Boleta.vendedor_id,
        models.Boleta.condicion,
        func.count(models.Boleta.id).label("total"),
        func.sum(
            case(
                (and_(
                    models.Boleta.liquidacion_vendedor_id.isnot(None),
                    models.Boleta.comprador_id.is_(None),
                ), 1),
                else_=0,
            )
        ).label("liq_sin_comp"),
        func.sum(
            case(
                (models.Boleta.liquidacion_vendedor_id.is_(None), 1),
                else_=0,
            )
        ).label("sin_liq"),
    ).filter(
        models.Boleta.vendedor_id.isnot(None)
    ).group_by(models.Boleta.vendedor_id, models.Boleta.condicion).all()

    def _empty():
        return {
            "caja": 0, "liq_pendiente": 0, "vendido": 0, "baja": 0,
            "liquidados": 0, "liquidados_cuotas": 0, "liquidados_contados": 0,
        }

    stats = {}
    for vid, cond, total, liq_sin_comp, sin_liq in rows:
        if vid not in stats:
            stats[vid] = _empty()
        if cond == CondicionBoleta.CAJA:
            stats[vid]["caja"] = int(sin_liq or 0)             # CAJA sin liquidar (físicas en mano)
            stats[vid]["liq_pendiente"] = int(liq_sin_comp or 0)  # CAJA con liq, sin socio aún

    # Baja: cuenta las boletas dadas de baja por el vendedor — tanto la baja real
    # de Socios (condicion BAJA) como la baja registrada en cobranza (mes_baja,
    # que deja la boleta en planilla). Cada boleta se cuenta una sola vez.
    baja_rows = db.query(
        models.Boleta.vendedor_id,
        func.count(models.Boleta.id)
    ).filter(
        models.Boleta.vendedor_id.isnot(None),
        or_(models.Boleta.condicion == CondicionBoleta.BAJA,
            models.Boleta.mes_baja.isnot(None))
    ).group_by(models.Boleta.vendedor_id).all()
    for vid, total in baja_rows:
        if vid not in stats:
            stats[vid] = _empty()
        stats[vid]["baja"] = int(total or 0)

    # Vendido: query separada para contar boletas con socio cargado,
    # sin importar la condicion (VENDIDO/CAJA-con-socio/EN_COBRANZA/BAJA).
    vendidos_rows = db.query(
        models.Boleta.vendedor_id,
        func.count(models.Boleta.id)
    ).filter(
        models.Boleta.vendedor_id.isnot(None),
        models.Boleta.comprador_id.isnot(None)
    ).group_by(models.Boleta.vendedor_id).all()
    for vid, total in vendidos_rows:
        if vid not in stats:
            stats[vid] = _empty()
        stats[vid]["vendido"] = total

    # Liquidados — desglose cuotas vs contados (ambos ponderados por PATA:
    # PATA 1 ×1, PATA 2 ×2, PATA 0 ×0.67). Usamos los snapshots cuotas_equiv y
    # contados_equiv de cada LiquidacionVendedor (la boleta sola no guarda su modalidad).
    #
    # IMPORTANTE (regla de negocio): "contado" = la TALONERA (la boleta) pagada de una
    # sola vez, ponderada por su PATA. Los números de sorteo extra (pool CONTADO /
    # CONTADO 2 VECES, modelo LiquidacionContadoItem) NO son ventas: son premios que
    # recibe quien paga al contado. Por eso NO se suman a "liquidados_contados" ni a
    # "liquidados" — se muestran aparte en las columnas Contado ★ / Contado 2× ★.
    liq_split_rows = db.query(
        models.LiquidacionVendedor.vendedor_id,
        func.coalesce(func.sum(models.LiquidacionVendedor.cuotas_equiv), 0),
        func.coalesce(func.sum(models.LiquidacionVendedor.contados_equiv), 0),
    ).group_by(models.LiquidacionVendedor.vendedor_id).all()
    for vid, cuotas_eq, contados_eq in liq_split_rows:
        if vid not in stats:
            stats[vid] = _empty()
        stats[vid]["liquidados_cuotas"]   = int(round(float(cuotas_eq or 0)))
        stats[vid]["liquidados_contados"] = int(round(float(contados_eq or 0)))

    # Total liquidados = cuotas + contados (ponderado por PATA). Sin pool/sorteo extra.
    for vid in stats:
        stats[vid]["liquidados"] = (
            stats[vid]["liquidados_cuotas"] + stats[vid]["liquidados_contados"]
        )

    return stats


def _contado_stats_bulk(db):
    """Por cada vendedor, cuenta cuántos números de pool CONTADO y CONTADO 2 VECES
    tiene pendientes (entregados vía EntregaCaja menos los ya asignados a boletas).
    Retorna: { vid: {"contado": N, "contado2": N} }
    """
    taloneras_contado = db.query(models.Talonera).filter(
        models.Talonera.tipo == "CONTADO"
    ).all()
    if not taloneras_contado:
        return {}

    def _tipo_key(nombre):
        up = (nombre or "").strip().upper()
        return "contado2" if "2" in up else "contado"

    # nombre.lower() → (id, tipo_key)
    nom_a_info = {
        (t.nombre or "").strip().lower(): (t.id, _tipo_key(t.nombre))
        for t in taloneras_contado
    }

    # Total entregado por vendor + talonera_nombre
    entregas = db.query(
        models.EntregaCaja.vendedor_id,
        models.EntregaCaja.talonera_nombre,
        func.sum(models.EntregaCaja.hasta - models.EntregaCaja.desde + 1).label("total")
    ).filter(
        models.EntregaCaja.vendedor_id.isnot(None)
    ).group_by(
        models.EntregaCaja.vendedor_id,
        models.EntregaCaja.talonera_nombre
    ).all()

    entregados = {}  # (vid, tipo_key) → count
    for vid, tnombre, total in entregas:
        key = (tnombre or "").strip().lower()
        if key not in nom_a_info:
            continue
        tal_id, tipo_key = nom_a_info[key]
        k = (vid, tipo_key)
        entregados[k] = entregados.get(k, 0) + int(total or 0)

    if not entregados:
        return {}

    id_a_tipo = {t.id: _tipo_key(t.nombre) for t in taloneras_contado}
    tal_ids = [t.id for t in taloneras_contado]

    # Asignados slot 1 (numero_especial)
    asig1 = db.query(
        models.Boleta.vendedor_id,
        models.Boleta.talonera_especial_id,
        func.count(models.Boleta.id).label("cnt")
    ).filter(
        models.Boleta.numero_especial.isnot(None),
        models.Boleta.talonera_especial_id.in_(tal_ids),
        models.Boleta.vendedor_id.isnot(None),
    ).group_by(
        models.Boleta.vendedor_id,
        models.Boleta.talonera_especial_id
    ).all()

    # Asignados slot 2 (numero_especial_2)
    asig2 = db.query(
        models.Boleta.vendedor_id,
        models.Boleta.talonera_especial_2_id,
        func.count(models.Boleta.id).label("cnt")
    ).filter(
        models.Boleta.numero_especial_2.isnot(None),
        models.Boleta.talonera_especial_2_id.in_(tal_ids),
        models.Boleta.vendedor_id.isnot(None),
    ).group_by(
        models.Boleta.vendedor_id,
        models.Boleta.talonera_especial_2_id
    ).all()

    for vid, tal_id, cnt in asig1:
        tipo_key = id_a_tipo.get(tal_id)
        if tipo_key:
            k = (vid, tipo_key)
            entregados[k] = max(0, entregados.get(k, 0) - int(cnt))

    for vid, tal_id, cnt in asig2:
        tipo_key = id_a_tipo.get(tal_id)
        if tipo_key:
            k = (vid, tipo_key)
            entregados[k] = max(0, entregados.get(k, 0) - int(cnt))

    result = {}
    for (vid, tipo_key), cnt in entregados.items():
        if cnt <= 0:
            continue
        if vid not in result:
            result[vid] = {"contado": 0, "contado2": 0}
        result[vid][tipo_key] = cnt
    return result


@router.get("/liquidaciones/{liq_id}/detalle", response_class=JSONResponse)
async def liquidacion_detalle(liq_id: int, request: Request, db: Session = Depends(get_db)):
    """Devuelve el detalle de una liquidacion: boletas asociadas + items pool CONTADO."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, "vendedores", "ver"):
        raise HTTPException(403, "Sin permiso")
    liq = db.query(models.LiquidacionVendedor).get(liq_id)
    if not liq:
        raise HTTPException(404, "Liquidacion no encontrada")

    # Boletas reales asociadas a esta liquidacion
    boletas = db.query(models.Boleta).filter_by(
        liquidacion_vendedor_id=liq_id
    ).all()

    # Para detectar reasignaciones: el jefe de equipo (Ariel) liquida boletas que
    # luego pasan a otros vendedores via "Pasar Caja" / "Entrega a Caja". Al cargar
    # el socio, b.vendedor_id puede quedar distinto al liq.vendedor_id original.
    # Cache de nombres de vendedores para no hacer N+1.
    liq_vid = liq.vendedor_id
    nombres_vendedores: dict[int, str] = {}
    otros_vids = {b.vendedor_id for b in boletas if b.vendedor_id and b.vendedor_id != liq_vid}
    if otros_vids:
        for vrow in db.query(models.Vendedor.id, models.Vendedor.nombre).filter(
            models.Vendedor.id.in_(otros_vids)
        ).all():
            nombres_vendedores[int(vrow[0])] = vrow[1] or "?"

    boletas_out = []
    for b in boletas:
        nd = (b.talonera.num_digitos or 4) if b.talonera else 4
        fmt = "{:0" + str(nd) + "d}"
        # Si la boleta fue reasignada despues de liquidarse, indicamos el vendedor actual
        reasignado_a_id = None
        reasignado_a_nombre = None
        if b.vendedor_id and b.vendedor_id != liq_vid:
            reasignado_a_id = int(b.vendedor_id)
            reasignado_a_nombre = nombres_vendedores.get(reasignado_a_id, "?")
        boletas_out.append({
            "id": b.id,
            "num": b.numero_principal,
            "num_str": fmt.format(b.numero_principal),
            "pata": b.talonera.nombre if b.talonera else "?",
            "color": b.talonera.color if b.talonera else "#cccccc",
            "condicion": b.condicion.value if b.condicion else "?",
            "comprador": b.comprador.apellido_nombre if b.comprador else None,
            "multiplicador": float((b.talonera.multiplicador or 1.0) if b.talonera else 1.0),
            "reasignado_a_id":     reasignado_a_id,
            "reasignado_a_nombre": reasignado_a_nombre,
        })
    boletas_out.sort(key=lambda x: (x["pata"], x["num"]))

    # Items pool CONTADO declarados en esta liquidacion
    pool_items = []
    try:
        items = db.query(models.LiquidacionContadoItem).filter_by(
            liquidacion_id=liq_id
        ).all()
        tal_cache = {}
        for it in items:
            t = tal_cache.get(it.talonera_id)
            if t is None:
                t = db.query(models.Talonera).get(it.talonera_id)
                tal_cache[it.talonera_id] = t
            nd = (t.num_digitos or 3) if t else 3
            fmt = "{:0" + str(nd) + "d}"
            pool_items.append({
                "talonera_nombre": t.nombre if t else "?",
                "color": (t.color or "#fff8e1") if t else "#fff8e1",
                "num": it.numero,
                "num_str": fmt.format(it.numero),
            })
    except Exception:
        pass
    pool_items.sort(key=lambda x: (x["talonera_nombre"], x["num"]))

    # cuotas_equiv puede estar en 0 para registros previos a la migracion: fallback
    # a la suma de multiplicadores de las boletas asociadas si no hay contados, o
    # al conteo literal si hay mezcla (no podemos distinguir por boleta).
    # Float desde 11/05/2026 (PATA 0 con mult 0.67).
    _cuotas_equiv = float(getattr(liq, "cuotas_equiv", 0) or 0)
    if _cuotas_equiv == 0:
        if int(liq.contados_vendidos or 0) == 0 and boletas:
            _cuotas_equiv = sum(float(b.get("multiplicador") or 1.0) for b in boletas_out)
        else:
            _cuotas_equiv = float(liq.cuotas_vendidas or 0)

    # Números que el vendedor todavía tiene en CAJA sin liquidar: candidatos para
    # agregar a esta liquidación si se olvidó alguno (mismo criterio que la sección
    # "En caja (sin liquidar)" del detalle del vendedor).
    disponibles = []
    for b in db.query(models.Boleta).filter(
        models.Boleta.vendedor_id == liq_vid,
        models.Boleta.condicion == CondicionBoleta.CAJA,
        models.Boleta.liquidacion_vendedor_id.is_(None),
    ).all():
        nd = (b.talonera.num_digitos or 4) if b.talonera else 4
        fmt = "{:0" + str(nd) + "d}"
        disponibles.append({
            "id": b.id,
            "num": b.numero_principal,
            "num_str": fmt.format(b.numero_principal),
            "pata": b.talonera.nombre if b.talonera else "?",
            "color": (b.talonera.color if (b.talonera and b.talonera.color) else "#0d6efd"),
            "multiplicador": float(b.talonera.multiplicador or 1.0) if b.talonera else 1.0,
        })
    disponibles.sort(key=lambda x: (x["pata"], x["num"]))

    return JSONResponse({
        "id": liq.id,
        "vendedor_id": liq_vid,
        "disponibles": disponibles,
        "fecha": liq.fecha.strftime("%d/%m/%Y %H:%M") if liq.fecha else "",
        "cuotas_vendidas":         int(liq.cuotas_vendidas or 0),
        "cuotas_equiv":            _cuotas_equiv,
        "contados_vendidos":       int(liq.contados_vendidos or 0),
        "monto_contados":          float(liq.monto_contados or 0),
        "comision_contados_pct":   float(liq.comision_contados_pct or 0),
        "comision_contados":       float(liq.comision_contados or 0),
        "cuotas_extras_cantidad":     int(getattr(liq, "cuotas_extras_cantidad", 0) or 0),
        "cuotas_extras_valor":        float(getattr(liq, "cuotas_extras_valor", 0) or 0),
        "cuotas_extras_monto":        float(getattr(liq, "cuotas_extras_monto", 0) or 0),
        "comision_cuotas_pct":        float(liq.comision_cuotas_pct or 0),
        "comision_cuotas_extras":     float(getattr(liq, "comision_cuotas_extras", 0) or 0),
        "cuotas_extras_p0_cantidad":  int(getattr(liq, "cuotas_extras_p0_cantidad", 0) or 0),
        "cuotas_extras_p0_valor":     float(getattr(liq, "cuotas_extras_p0_valor", 0) or 0),
        "cuotas_extras_p0_monto":     float(getattr(liq, "cuotas_extras_p0_monto", 0) or 0),
        "comision_cuotas_extras_p0":  float(getattr(liq, "comision_cuotas_extras_p0", 0) or 0),
        "total_a_rendir":             float(getattr(liq, "total_a_rendir", 0) or 0),
        "cuota_1_total":           float(liq.cuota_1_total or 0),
        "observacion":             liq.observacion or "",
        "boletas":                 boletas_out,
        "pool_items":              pool_items,
    })


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
    contado_stats = _contado_stats_bulk(db)
    jefe = db.query(models.Vendedor).filter_by(es_jefe_equipo=True, activo=True).first()
    # Set de nombres CONTADO para que el template marque visualmente esas filas
    nombres_contado = sorted({g["nombre"] for g in grupos_contado})

    # ------------------------------------------------------------------
    # Totales globales (suma de todas las filas — activos + inactivos)
    # y Historial mensual (entregas a caja + liquidaciones agrupado por mes).
    # Lo cosumimos en la fila <tfoot> y en el modal #modalHistorialTotal
    # (doble-click sobre la fila TOTAL).
    # ------------------------------------------------------------------
    totales = {
        "caja":               sum(int(s.get("caja", 0))               for s in stats.values()),
        "liq_pendiente":      sum(int(s.get("liq_pendiente", 0))      for s in stats.values()),
        "contado":            sum(int(c.get("contado", 0))            for c in contado_stats.values()),
        "contado2":           sum(int(c.get("contado2", 0))           for c in contado_stats.values()),
        "liquidados_cuotas":  sum(int(s.get("liquidados_cuotas", 0))  for s in stats.values()),
        "liquidados_contados":sum(int(s.get("liquidados_contados", 0))for s in stats.values()),
        "liquidados":         sum(int(s.get("liquidados", 0))         for s in stats.values()),
        "baja":               sum(int(s.get("baja", 0))               for s in stats.values()),
    }

    # --- Histórico mensual ---
    _MESES_ES = [
        "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    ]
    hist_acc: dict = {}

    def _bucket(key):
        if key not in hist_acc:
            hist_acc[key] = {
                "entregas": 0, "n_entregas": 0,
                "liquidaciones": 0, "n_liquidaciones": 0,
            }
        return hist_acc[key]

    # Entregas a caja: sumamos boletas_afectadas por mes de fecha de entrega.
    for e in db.query(models.EntregaCaja).all():
        if not e.fecha:
            continue
        bucket = _bucket((e.fecha.year, e.fecha.month))
        bucket["entregas"] += int(e.boletas_afectadas or 0)
        bucket["n_entregas"] += 1

    # Liquidaciones: cuotas + contados, ambos ponderados por PATA (PATA 1 ×1, PATA 2 ×2,
    # PATA 0 ×0.67). El pool de sorteo extra (CONTADO / CONTADO 2 VECES) NO se cuenta
    # como venta — son premios de quien paga al contado, no boletas vendidas.
    for liq in db.query(models.LiquidacionVendedor).all():
        if not liq.fecha:
            continue
        bucket = _bucket((liq.fecha.year, liq.fecha.month))
        cuotas_eq   = float(liq.cuotas_equiv or 0)
        contados_eq = float(liq.contados_equiv or liq.contados_vendidos or 0)
        bucket["liquidaciones"] += int(round(cuotas_eq + contados_eq))
        bucket["n_liquidaciones"] += 1

    historial_mensual = []
    for (y, m) in sorted(hist_acc.keys(), reverse=True):
        d = hist_acc[(y, m)]
        historial_mensual.append({
            "year": y,
            "month": m,
            "label": f"{_MESES_ES[m]} {y}",
            "entregas":         d["entregas"],
            "n_entregas":       d["n_entregas"],
            "liquidaciones":    d["liquidaciones"],
            "n_liquidaciones":  d["n_liquidaciones"],
        })

    historial_total = {
        "entregas":        sum(r["entregas"]        for r in historial_mensual),
        "liquidaciones":   sum(r["liquidaciones"]   for r in historial_mensual),
        "n_entregas":      sum(r["n_entregas"]      for r in historial_mensual),
        "n_liquidaciones": sum(r["n_liquidaciones"] for r in historial_mensual),
    }

    # Historial de liquidaciones (mismo dataset que la página dedicada) para
    # embeberlo al pie de la página de Vendedores. Usa nombres hl_* para no
    # chocar con `historial_mensual` (que acá es el histórico de entregas a caja).
    from datetime import datetime as _dt_hl, timedelta as _td_hl
    _nombres_hl = {v.id: v.nombre for v in vendedores}
    _liqs_hl = db.query(models.LiquidacionVendedor).all()
    hl_meses = _build_meses(_liqs_hl, _nombres_hl)
    _hoy_hl = _dt_hl.utcnow().date()
    _lunes_hl = _hoy_hl - _td_hl(days=_hoy_hl.weekday())
    _domingo_hl = _lunes_hl + _td_hl(days=6)
    hl_semana_key = _lunes_hl.isoformat()
    hl_mes_key = f"{_domingo_hl.year:04d}-{_domingo_hl.month:02d}"

    return templates.TemplateResponse(request, "vendedores.html", {
        "user": user,
        "vendedores": vendedores,
        "grupos_talonera": grupos_talonera,
        "grupos_contado": grupos_contado,
        "nombres_contado": nombres_contado,
        "entregas": entregas,
        "stats": stats,
        "contado_stats": contado_stats,
        "jefe": jefe,
        "totales": totales,
        "historial_mensual": historial_mensual,
        "historial_total": historial_total,
        "hl_meses": hl_meses,
        "hl_semana_key": hl_semana_key,
        "hl_mes_key": hl_mes_key,
    })


_MESES_NOMBRE = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                 "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
_MESES_ABREV = ["", "ene", "feb", "mar", "abr", "may", "jun",
                "jul", "ago", "sep", "oct", "nov", "dic"]


def _mes_key_semana(f):
    """Mes (YYYY-MM) al que pertenece la semana de la fecha f: el mes donde TERMINA
    la semana (domingo)."""
    from datetime import timedelta as _td
    lunes = (f - _td(days=f.weekday())).date()
    domingo = lunes + _td(days=6)
    return f"{domingo.year:04d}-{domingo.month:02d}"


def _acumular_filas(liqs, nombres):
    """Agrupa una lista de liquidaciones por vendedor y devuelve (filas, totales).
    Cada celda va ponderada por multiplicador de PATA (cuotas_equiv / contados_equiv)."""
    vend = {}
    tot = {"cuotas_eq": 0.0, "cuotas_crudo": 0, "contados_eq": 0.0,
           "contados_crudo": 0, "extras": 0, "rinde": 0.0, "n_liquidaciones": 0}
    for liq in liqs:
        vid = liq.vendedor_id
        if vid not in vend:
            vend[vid] = {
                "vendedor_id": vid, "nombre": nombres.get(vid, "?"),
                "n_liquidaciones": 0, "cuotas_eq": 0.0, "cuotas_crudo": 0,
                "contados_eq": 0.0, "contados_crudo": 0, "extras": 0, "rinde": 0.0,
            }
        f = vend[vid]
        cu = float(liq.cuotas_equiv or liq.cuotas_vendidas or 0)
        co = float(liq.contados_equiv or liq.contados_vendidos or 0)
        ccu = int(liq.cuotas_vendidas or 0)
        cco = int(liq.contados_vendidos or 0)
        ex = int(liq.cuotas_extras_cantidad or 0)
        ri = float(liq.total_a_rendir or 0)
        f["n_liquidaciones"] += 1; f["cuotas_eq"] += cu; f["cuotas_crudo"] += ccu
        f["contados_eq"] += co; f["contados_crudo"] += cco; f["extras"] += ex; f["rinde"] += ri
        tot["n_liquidaciones"] += 1; tot["cuotas_eq"] += cu; tot["cuotas_crudo"] += ccu
        tot["contados_eq"] += co; tot["contados_crudo"] += cco; tot["extras"] += ex; tot["rinde"] += ri
    filas = sorted(vend.values(), key=lambda r: (r["nombre"] or "").lower())
    return filas, tot


def _build_meses(liquidaciones, nombres):
    """Agrupa las liquidaciones por MES calendario y, dentro de cada mes, por SEMANA
    (lunes a domingo). Mes/semana se derivan de la fecha de cada liquidación."""
    from datetime import timedelta as _td
    by_mes = {}   # mes_key -> {sem_key -> [liqs]}
    meta_mes = {}
    meta_sem = {}
    for liq in liquidaciones:
        if not liq.fecha:
            continue
        f = liq.fecha
        lunes = (f - _td(days=f.weekday())).date()
        domingo = lunes + _td(days=6)
        # La semana se asigna íntegra al mes donde TERMINA (el domingo).
        mk = f"{domingo.year:04d}-{domingo.month:02d}"
        sk = lunes.isoformat()
        by_mes.setdefault(mk, {}).setdefault(sk, []).append(liq)
        meta_mes[mk] = (domingo.year, domingo.month)
        meta_sem[sk] = (lunes, domingo)

    def _totales_a_tot(prefix, tot):
        return {f"{prefix}{k}": v for k, v in tot.items() if k != "n_liquidaciones"}

    meses_list = []
    for mk in sorted(by_mes.keys(), reverse=True):
        y, mo = meta_mes[mk]
        mes_liqs = [l for wl in by_mes[mk].values() for l in wl]
        _, mtot = _acumular_filas(mes_liqs, nombres)
        vset = {l.vendedor_id for l in mes_liqs}
        semanas = []
        for sk in sorted(by_mes[mk].keys(), reverse=True):
            lunes, domingo = meta_sem[sk]
            filas, stot = _acumular_filas(by_mes[mk][sk], nombres)
            sem = {
                "key": sk,
                "label": (f"{lunes.day:02d}/{_MESES_ABREV[lunes.month]} - "
                          f"{domingo.day:02d}/{_MESES_ABREV[domingo.month]}/{domingo.year}"),
                "vendedores": filas,
                "n_liquidaciones": stot["n_liquidaciones"],
            }
            sem.update(_totales_a_tot("tot_", stot))
            semanas.append(sem)
        mes = {
            "key": mk, "year": y, "month": mo,
            "label": f"{_MESES_NOMBRE[mo]} {y}",
            "semanas": semanas,
            "n_vendedores": len(vset),
            "n_liquidaciones": mtot["n_liquidaciones"],
        }
        mes.update(_totales_a_tot("tot_", mtot))
        meses_list.append(mes)
    return meses_list


@router.get("/historial-liquidaciones", response_class=HTMLResponse)
async def historial_liquidaciones(request: Request, db: Session = Depends(get_db)):
    """Historial de liquidaciones agrupado por MES y, dentro de cada mes, por SEMANA
    (lunes a domingo) y por vendedor. Cada celda VENDIDOS va ponderada por
    multiplicador de PATA (PATA 0 x0.67, PATA 1 x1, PATA 2 x2, ...)."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, "vendedores", "ver"):
        raise HTTPException(403, "Sin permiso")

    from datetime import datetime as _dt, timedelta as _td
    nombres_vendedores = {v.id: v.nombre for v in db.query(models.Vendedor).all()}
    liquidaciones = db.query(models.LiquidacionVendedor).all()
    historial_mensual = _build_meses(liquidaciones, nombres_vendedores)

    _hoy = _dt.utcnow().date()
    _lunes_hoy = _hoy - _td(days=_hoy.weekday())
    _domingo_hoy = _lunes_hoy + _td(days=6)
    semana_actual_key = _lunes_hoy.isoformat()
    # El mes "actual" es donde termina la semana en curso (mismo criterio de agrupación)
    mes_actual_key = f"{_domingo_hoy.year:04d}-{_domingo_hoy.month:02d}"

    return templates.TemplateResponse(request, "vendedor_historial_liquidaciones.html", {
        "user": user,
        "hl_meses": historial_mensual,
        "hl_semana_key": semana_actual_key,
        "hl_mes_key": mes_actual_key,
    })


@router.get("/historial-liquidaciones/informe", response_class=HTMLResponse)
async def historial_liquidaciones_informe(
    request: Request, tipo: str = "semana", key: str = "",
    db: Session = Depends(get_db),
):
    """Informe imprimible (PDF) de liquidaciones de un período: un mes (tipo=mes,
    key=YYYY-MM) o una semana (tipo=semana, key=YYYY-MM-DD del lunes)."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, "vendedores", "ver"):
        raise HTTPException(403, "Sin permiso")

    from datetime import timedelta as _td, date as _date
    nombres = {v.id: v.nombre for v in db.query(models.Vendedor).all()}
    liqs = db.query(models.LiquidacionVendedor).all()

    if tipo == "mes":
        sel = [l for l in liqs if l.fecha and _mes_key_semana(l.fecha) == key]
    else:
        sel = [l for l in liqs
               if l.fecha and (l.fecha - _td(days=l.fecha.weekday())).date().isoformat() == key]

    meses = _build_meses(sel, nombres)

    if tipo == "mes":
        titulo = meses[0]["label"] if meses else key
        subtitulo = "Informe mensual de liquidaciones"
    else:
        try:
            lunes = _date.fromisoformat(key)
            domingo = lunes + _td(days=6)
            titulo = (f"Semana {lunes.day:02d}/{_MESES_ABREV[lunes.month]} - "
                      f"{domingo.day:02d}/{_MESES_ABREV[domingo.month]}/{domingo.year}")
        except Exception:
            titulo = key
        subtitulo = "Informe semanal de liquidaciones"

    return templates.TemplateResponse(request, "liquidaciones_informe.html", {
        "user": user, "meses": meses, "tipo": tipo,
        "titulo": titulo, "subtitulo": subtitulo,
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

    # Mapeo liq_id -> (vendedor_id, nombre, es_jefe) para detectar boletas que
    # fueron liquidadas por OTRO vendedor (caso tipico: jefe de equipo liquido
    # las boletas que luego pasaron a este vendedor via "Pasar Caja").
    liq_ids = {b.liquidacion_vendedor_id for b in boletas if b.liquidacion_vendedor_id}
    liq_info: dict[int, dict] = {}
    if liq_ids:
        rows_liq = db.query(
            models.LiquidacionVendedor.id,
            models.LiquidacionVendedor.vendedor_id,
        ).filter(models.LiquidacionVendedor.id.in_(liq_ids)).all()
        vid_de_otros = {r[1] for r in rows_liq if r[1] and r[1] != vid}
        nombres_otros: dict[int, tuple[str, bool]] = {}
        if vid_de_otros:
            for vrow in db.query(
                models.Vendedor.id, models.Vendedor.nombre, models.Vendedor.es_jefe_equipo
            ).filter(models.Vendedor.id.in_(vid_de_otros)).all():
                nombres_otros[int(vrow[0])] = (vrow[1] or "?", bool(vrow[2]))
        for lid, lvid in rows_liq:
            if lvid and lvid != vid and lvid in nombres_otros:
                nm, es_jefe = nombres_otros[lvid]
                liq_info[int(lid)] = {
                    "vendedor_id":     int(lvid),
                    "vendedor_nombre": nm,
                    "es_jefe":         es_jefe,
                }

    # Agrupar por pata
    patas = {}
    for b in boletas:
        pata_nombre = b.talonera.nombre if b.talonera else "?"
        pata_color  = b.talonera.color  if b.talonera else "#ffffff"
        nd = (b.talonera.num_digitos or 4) if b.talonera else 4
        fmt = "{:0" + str(nd) + "d}"
        if pata_nombre not in patas:
            patas[pata_nombre] = {"color": pata_color, "boletas": [], "num_digitos": nd}
        # Si la liquidacion la hizo otro vendedor (tipicamente el jefe de equipo
        # antes de pasar la caja a este vendedor), mostramos un indicador.
        liq_por_otro = liq_info.get(b.liquidacion_vendedor_id) if b.liquidacion_vendedor_id else None
        patas[pata_nombre]["boletas"].append({
            "id":      b.id,
            "num":     b.numero_principal,
            "num_str": fmt.format(b.numero_principal),
            "cond":    b.condicion.value if b.condicion else "?",
            "liq":     b.liquidacion_vendedor_id is not None,
            "tiene_socio": b.comprador_id is not None,
            "contado": (b.numero_especial is not None) or (b.numero_especial_2 is not None),
            "pool":    False,
            "liq_por_otro_nombre": liq_por_otro["vendedor_nombre"] if liq_por_otro else None,
            "liq_por_otro_es_jefe": liq_por_otro["es_jefe"] if liq_por_otro else False,
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
        if (getattr(e, "tipo", "ENTREGA") or "ENTREGA") == "RETIRO":
            continue  # los retiros no suman al pool del vendedor
        nm = (e.talonera_nombre or "").strip().lower()
        t_match = contado_taloneras_norm.get(nm)
        if t_match:
            # Agrupamos por el nombre canonico (el de la talonera actual)
            ranges_por_talonera.setdefault(t_match.nombre, []).append(
                (int(e.desde), int(e.hasta))
            )
    # Lista de items pool para incluir en pendientes_json (liquidables como contado)
    pool_pendientes_items = []
    # Numeros pool ya liquidados (NO incluir en pool pendiente)
    try:
        liq_items_rows = db.query(
            models.LiquidacionContadoItem.talonera_id,
            models.LiquidacionContadoItem.numero,
        ).join(
            models.LiquidacionVendedor,
            models.LiquidacionVendedor.id == models.LiquidacionContadoItem.liquidacion_id,
        ).filter(
            models.LiquidacionVendedor.vendedor_id == vid
        ).all()
        nums_ya_liquidados = {(int(r[0]), int(r[1])) for r in liq_items_rows}
    except Exception:
        nums_ya_liquidados = set()

    for nombre_c, rangos in ranges_por_talonera.items():
        # Re-localizamos la talonera por su nombre canonico
        t = next((x for x in taloneras if x.nombre == nombre_c), None)
        if t is None:
            continue
        # Tipo pool: "contado2" si el nombre contiene "2" (CONTADO 2 VECES), sino "contado".
        _tipo_pool = "contado2" if "2" in (nombre_c or "").upper() else "contado"
        nums_entregados = set()
        for d, h in rangos:
            if h < d:
                continue
            nums_entregados.update(range(d, h + 1))
        if not nums_entregados:
            continue
        # Numeros del pool ya asignados a alguna boleta (vendido al contado)
        # Mira ambos slots: numero_especial (slot 1) y numero_especial_2 (slot 2)
        asignados_rows = db.query(
            models.Boleta.numero_especial,
            models.Boleta.talonera_especial_id,
            models.Boleta.numero_especial_2,
            models.Boleta.talonera_especial_2_id,
        ).filter(
            ((models.Boleta.talonera_especial_id == t.id) &
             (models.Boleta.numero_especial.isnot(None))) |
            ((models.Boleta.talonera_especial_2_id == t.id) &
             (models.Boleta.numero_especial_2.isnot(None)))
        ).all()
        nums_asignados = set()
        for ne, tei, ne2, tei2 in asignados_rows:
            if tei == t.id and ne is not None and ne in nums_entregados:
                nums_asignados.add(int(ne))
            if tei2 == t.id and ne2 is not None and ne2 in nums_entregados:
                nums_asignados.add(int(ne2))
        # Números del pool ya liquidados (pendientes de cargar al socio)
        nums_liquidados_t = {n for (tid, n) in nums_ya_liquidados if tid == t.id}
        # Tres grupos dentro de lo entregado al vendedor:
        #   pendientes → en mano, sin vender (azul, liquidables)
        #   liquidados → liquidados, pend. comprador (verde, tachado)
        #   vendidos   → numero_especial ya asignado a un socio (gris, tachado)
        pendientes_pool = sorted(nums_entregados - nums_asignados - nums_liquidados_t)
        liquidados_pool = sorted(nums_liquidados_t & nums_entregados)
        vendidos_pool   = sorted(nums_asignados & nums_entregados)
        if not (pendientes_pool or liquidados_pool or vendidos_pool):
            continue
        nd_c = t.num_digitos or 3
        fmt_c = "{:0" + str(nd_c) + "d}"
        if nombre_c not in patas:
            patas[nombre_c] = {"color": t.color or "#fff8e1", "boletas": [], "num_digitos": nd_c}
        for n in pendientes_pool:
            patas[nombre_c]["boletas"].append({
                "id":      None,
                "num":     n,
                "num_str": fmt_c.format(n),
                "cond":    "CAJA",
                "liq":     False,
                "tiene_socio": False,
                "contado": True,
                "pool":    True,
            })
            # Items para el modal (liquidables al contado)
            pool_pendientes_items.append({
                "id":          f"pool:{t.id}:{n}",   # id sintetico
                "num":         n,
                "num_str":     fmt_c.format(n),
                "pata":        nombre_c,
                "color":       t.color or "#fff8e1",
                "valor_cuota": t.valor_cuota or 0.0,
                "num_cuotas":  t.num_cuotas or 12,
                "contado":     True,
                "pool":        True,
                "tipo_pool":   _tipo_pool,           # "contado" | "contado2"
                "talonera_id": t.id,
            })
        # Liquidados pendientes de comprador → verde tachado (no liquidables)
        for n in liquidados_pool:
            patas[nombre_c]["boletas"].append({
                "id": None, "num": n, "num_str": fmt_c.format(n),
                "cond": "CAJA", "liq": True, "tiene_socio": False,
                "contado": True, "pool": True,
                "liq_por_otro_nombre": None, "liq_por_otro_es_jefe": False,
            })
        # Vendidos (numero_especial asignado a un socio) → gris tachado
        for n in vendidos_pool:
            patas[nombre_c]["boletas"].append({
                "id": None, "num": n, "num_str": fmt_c.format(n),
                "cond": "VENDIDO", "liq": True, "tiene_socio": True,
                "contado": True, "pool": True,
                "liq_por_otro_nombre": None, "liq_por_otro_es_jefe": False,
            })
        patas[nombre_c]["boletas"].sort(key=lambda x: x["num"])

    # Orden jerarquico de las PATAs:
    #   1) PATA con numero (PATA 1, 2, 3, ...) ordenadas numericamente
    #   2) Otras COMUN (ej: VOLAS) alfabeticamente
    #   3) CONTADO y CONTADO N VECES al final, "CONTADO" primero, luego por su numero
    import re as _re
    def _pata_sort_key(nombre: str):
        nm = (nombre or "").strip()
        up = nm.upper()
        m = _re.search(r"(\d+)", nm)
        num = int(m.group(1)) if m else 0
        if up.startswith("PATA"):
            return (0, num, up)
        if up.startswith("CONTADO"):
            # "CONTADO" solo => 1 (primero), "CONTADO 2 VECES" => 2, etc.
            return (2, num if num > 0 else 1, up)
        return (1, num, up)
    patas = dict(sorted(patas.items(), key=lambda kv: _pata_sort_key(kv[0])))

    # Boletas CAJA sin liquidar = las que el vendedor aun tiene en mano
    # Boletas CAJA con liq_id  = vendidas por el vendedor, pendientes de cargar comprador
    # Boletas VENDIDO           = ya cargadas en el sistema con datos del comprador
    pendientes = [b for b in boletas
                  if b.condicion == CondicionBoleta.CAJA
                  and b.liquidacion_vendedor_id is None]

    # Datos individuales de cada boleta pendiente → para el modal de selección manual
    # Solo boletas COMUN (no taloneras CONTADO/CONTADO 2 VECES). El vendedor marca
    # modalidad inline (cuotas / contado / contado 2 veces) por boleta.
    # PATA 1 = la talonera COMUN con multiplicador 1 (num_series == 3).
    # Es la unidad base para todos los calculos del modal de liquidacion:
    #   cuota1 por boleta    = mult × pata1_vc
    #   contado por boleta   = mult × pata1_nc × pata1_vc
    pata1_t = next(
        (t for t in taloneras if (t.tipo or "COMUN") == "COMUN" and (t.multiplicador or 1) == 1),
        None
    )
    pata1_vc = float(pata1_t.valor_cuota or 0) if pata1_t else 0.0
    pata1_nc = int(pata1_t.num_cuotas or 12)   if pata1_t else 12
    # PATA 0: talonera con multiplicador ≈ 2/3 (0.6667)
    pata0_t  = next(
        (t for t in taloneras if (t.tipo or "COMUN") == "COMUN" and abs((t.multiplicador or 1) - 2/3) < 0.01),
        None
    )
    pata0_vc = float(pata0_t.valor_cuota or 0) if pata0_t else 0.0

    pendientes_items = [
        {
            "id":           b.id,
            "num":          b.numero_principal,
            "num_str":      ("{:0" + str(b.talonera.num_digitos or 4) + "d}").format(b.numero_principal) if b.talonera else str(b.numero_principal),
            "pata":         b.talonera.nombre        if b.talonera else "?",
            "color":        b.talonera.color         if b.talonera else "#cccccc",
            "valor_cuota":  b.talonera.valor_cuota   if b.talonera else 0.0,
            "num_cuotas":   (b.talonera.num_cuotas   if b.talonera and b.talonera.num_cuotas else 12),
            "multiplicador": float(b.talonera.multiplicador or 1.0) if b.talonera else 1.0,
            "talonera_id":  b.talonera_id            if b.talonera else None,
            "pool":         False,
        }
        for b in sorted(pendientes, key=lambda x: (x.talonera.nombre if x.talonera else "", x.numero_principal))
    ]
    # Pool items (CONTADO/CONTADO 2 VECES) — liquidables solo para sacarlos de caja
    # del vendedor (sin valor monetario). Multiplicador=0 para que no afecten la
    # cuenta de monto; se cuentan aparte en "Entregados".
    for _pool in pool_pendientes_items:
        _pool.setdefault("multiplicador", 0.0)
    pendientes_json = json.dumps(pendientes_items + pool_pendientes_items)

    liquidaciones = db.query(models.LiquidacionVendedor).filter_by(
        vendedor_id=vid
    ).order_by(models.LiquidacionVendedor.fecha.desc()).all()

    # ── Métricas acumuladas para tarjetas del header ─────────────────────
    # Total boletas liquidadas: solo las que SIGUEN siendo de este vendedor.
    # Si una boleta se reasignó después de liquidar (ej. jefe de equipo que la
    # liquidó pero después pasó a otro vendedor real), NO debe contarse para él
    # porque no la vendió realmente. La liquidación queda en su historial como
    # registro contable, pero el conteo refleja lo efectivamente vendido por él.
    # `boletas` ya viene filtrado por vendedor_id=vid, así que iteramos ese set.
    _liq_ids_propias: set[int] = {
        b.liquidacion_vendedor_id for b in boletas if b.liquidacion_vendedor_id
    }
    # Pool items CONTADO declarados en sus liquidaciones (números del pool, no boletas).
    # Estos se rinden con la liquidación y no se reasignan, así que cuentan siempre.
    _pool_count = 0
    if liquidaciones:
        try:
            _pool_count = db.query(func.count(models.LiquidacionContadoItem.id)).filter(
                models.LiquidacionContadoItem.liquidacion_id.in_([liq.id for liq in liquidaciones])
            ).scalar() or 0
        except Exception:
            _pool_count = 0

    # Literal: cantidad de boletas propias liquidadas (sin ponderar) + pool items
    total_boletas_liquidadas = sum(
        1 for b in boletas if b.liquidacion_vendedor_id
    ) + _pool_count
    # Ponderado ("Total liquidados" de la tarjeta): usa EXACTAMENTE el mismo criterio
    # que el dashboard (_stats_bulk) para que los números coincidan con la pantalla
    # principal: suma de los snapshots cuotas_equiv + contados_equiv de las
    # liquidaciones de este vendedor (ponderado por PATA), redondeando cada componente
    # por separado igual que el dashboard. NO incluye los números de sorteo extra del
    # pool (CONTADO / CONTADO 2 VECES): son premios, no ventas (regla 21/06). Queda
    # entero (sin floats feos tipo 180.0000000003).
    _cuotas_eq_total   = sum(float(liq.cuotas_equiv or 0)   for liq in liquidaciones)
    _contados_eq_total = sum(float(liq.contados_equiv or 0) for liq in liquidaciones)
    total_boletas_liquidadas_eq = int(round(_cuotas_eq_total)) + int(round(_contados_eq_total))
    # Ingresos del vendedor: lo que se queda en su bolsillo.
    # = cuota 1 (de boletas TODAVÍA propias) + comisión contado (de boletas TODAVÍA
    #   propias) + comisión cuotas extras (input manual de la liquidación, no se
    #   reasigna entre vendedores).
    # Si una boleta liquidada por este vendedor se reasignó después a otro vendedor
    # (caso jefe de equipo: Ariel liquida y luego pasa caja a Pajaro), esa boleta NO
    # suma porque la cuota 1 y el pago contado los cobró el otro vendedor.
    # Mismo criterio que `total_boletas_liquidadas` (ver Sesión 10/05/2026 cont. 5).
    _liqs_by_id = {liq.id: liq for liq in liquidaciones}
    # Acumulador por liquidación → permite también desglose por mes más abajo.
    # Usamos los valores GUARDADOS en la liquidación (calculados al momento de liquidar):
    #   - cuota_1_total:      cuota 1 de boletas en cuotas (el vendedor la cobró directamente)
    #   - comision_contados:  % del monto total de boletas al contado (comisión del vendedor)
    #   - comision_cuotas_extras / p0: comisiones manuales de cuotas adicionales
    # No se recalcula por boleta porque numero_especial puede no estar seteado aún
    # (se setea al cargar el comprador, DESPUÉS de la liquidación) y la zona puede
    # haber cambiado el vendedor_id de la boleta sin alterar la liquidación original.
    ingreso_por_liq = {
        liq.id: (float(liq.cuota_1_total or 0)
                 + float(liq.comision_contados or 0)
                 + float(liq.comision_cuotas_extras or 0)
                 + float(getattr(liq, "comision_cuotas_extras_p0", 0) or 0))
        for liq in liquidaciones
    }
    # Ajuste: restar cuota 1 de boletas de CUOTAS que fueron reasignadas a otro vendedor
    # (cambió vendedor_id por zona al cargar comprador; el otro vendedor cobra la cuota 1).
    # NO se restan boletas contado, aunque no tengan numero_especial aún:
    #   - numero_especial seteado → contado obvio (excluido)
    #   - numero_especial NULL pero cuotas_anticipadas >= cuotas_pactadas → contado
    #     (los especiales se cargan en grupos de 5; hasta entonces solo se detecta
    #     por cuotas_anticipadas). Su comision_contados ya está en ingreso_por_liq;
    #     restarle valor_cuota la reduciría incorrectamente.
    _liq_ids = {liq.id for liq in liquidaciones}
    if _liq_ids:
        from sqlalchemy import or_ as _or_
        _pasadas = db.query(models.Boleta).filter(
            models.Boleta.liquidacion_vendedor_id.in_(_liq_ids),
            models.Boleta.vendedor_id != vid,
            models.Boleta.numero_especial.is_(None),
            models.Boleta.numero_especial_2.is_(None),
            # Excluir boletas al contado sin numero_especial todavía
            # (cuotas_anticipadas >= cuotas_pactadas indica pago total de entrada)
            _or_(
                models.Boleta.cuotas_anticipadas.is_(None),
                models.Boleta.cuotas_pactadas.is_(None),
                models.Boleta.cuotas_anticipadas < models.Boleta.cuotas_pactadas,
            ),
        ).all()
        for _b in _pasadas:
            if _b.liquidacion_vendedor_id in ingreso_por_liq and _b.talonera:
                ingreso_por_liq[_b.liquidacion_vendedor_id] -= float(_b.talonera.valor_cuota or 0)

    ingresos_total = sum(ingreso_por_liq.values())
    # Total vendidas: boletas con comprador cargado, ponderado por multiplicador de PATA (Float)
    total_vendidas_pond = sum(
        float((b.talonera.multiplicador or 1.0) if b.talonera else 1.0)
        for b in boletas if b.comprador_id is not None
    )

    # ── Agrupación de liquidaciones por mes (para acordeón) ──────────────
    _MESES_ES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
                 "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
    from datetime import datetime as _dt
    _hoy = _dt.utcnow()
    mes_actual_key = f"{_hoy.year:04d}-{_hoy.month:02d}"

    # Items pool CONTADO por liquidación de este vendedor (números entregados a
    # institución, sin valor monetario). Desglose: c1 = pool CONTADO,
    # c2 = pool CONTADO 2 VECES (detectado por "2" en el nombre de la talonera).
    pool_by_liq_v: dict = {}
    if liquidaciones:
        try:
            _rows = db.query(
                models.LiquidacionContadoItem.liquidacion_id,
                models.LiquidacionContadoItem.talonera_id,
                func.count(models.LiquidacionContadoItem.id),
            ).filter(
                models.LiquidacionContadoItem.liquidacion_id.in_(
                    [liq.id for liq in liquidaciones]
                )
            ).group_by(
                models.LiquidacionContadoItem.liquidacion_id,
                models.LiquidacionContadoItem.talonera_id,
            ).all()
            _tal_es_c2 = {
                int(t.id): ("2" in (t.nombre or "").upper())
                for t in taloneras if (t.tipo or "COMUN") == "CONTADO"
            }
            for lid, tid, cnt in _rows:
                lid, tid, cnt = int(lid), int(tid), int(cnt or 0)
                bucket = pool_by_liq_v.setdefault(lid, {"c1": 0, "c2": 0, "total": 0})
                if _tal_es_c2.get(tid, False):
                    bucket["c2"] += cnt
                else:
                    bucket["c1"] += cnt
                bucket["total"] += cnt
        except Exception:
            pool_by_liq_v = {}

    grupos_mes_dict = {}
    for liq in liquidaciones:
        if not liq.fecha:
            continue
        y, m = liq.fecha.year, liq.fecha.month
        key = f"{y:04d}-{m:02d}"
        if key not in grupos_mes_dict:
            grupos_mes_dict[key] = {
                "key": key, "year": y, "month": m,
                "label": f"{_MESES_ES[m-1]} {y}",
                "liquidaciones": [],
                "dias_dict": {},
                "total_ingresos": 0.0,
                "total_rinde": 0.0,
                "total_boletas": 0,
                # Totales ponderados para el resumen en columnas VENDIDOS
                "total_cuotas_eq": 0.0,
                "total_cuotas_crudo": 0,
                "total_contados_eq": 0.0,
                "total_contados_crudo": 0,
                "total_entregados": 0,
                "total_entregados_c1": 0,
                "total_entregados_c2": 0,
                "total_extras": 0,
            }
        g = grupos_mes_dict[key]
        g["liquidaciones"].append(liq)
        # total_ingresos por mes: usa el ingreso "real" por liq (excluye boletas
        # reasignadas a otro vendedor — mismo criterio que la tarjeta header).
        g["total_ingresos"] += ingreso_por_liq.get(liq.id, 0.0)
        g["total_rinde"] += (liq.total_a_rendir or 0)
        _liq_cuotas_eq   = float(liq.cuotas_equiv or liq.cuotas_vendidas or 0)
        _liq_contados_eq = float(liq.contados_equiv or liq.contados_vendidos or 0)
        _liq_pool        = pool_by_liq_v.get(liq.id) or {"c1": 0, "c2": 0, "total": 0}
        g["total_boletas"]        += _liq_cuotas_eq + _liq_contados_eq
        g["total_cuotas_eq"]      += _liq_cuotas_eq
        g["total_cuotas_crudo"]   += int(liq.cuotas_vendidas or 0)
        g["total_contados_eq"]    += _liq_contados_eq
        g["total_contados_crudo"] += int(liq.contados_vendidos or 0)
        g["total_entregados"]     += int(_liq_pool.get("total", 0))
        g["total_entregados_c1"]  += int(_liq_pool.get("c1", 0))
        g["total_entregados_c2"]  += int(_liq_pool.get("c2", 0))
        g["total_extras"]         += (int(liq.cuotas_extras_cantidad or 0)
                                      + int(getattr(liq, "cuotas_extras_p0_cantidad", 0) or 0))

        # ── Agrupado por DÍA (las liquidaciones del mismo día se suman en una fila) ──
        dkey = liq.fecha.date()
        d = g["dias_dict"].get(dkey)
        if d is None:
            d = {
                "fecha": liq.fecha,
                "fecha_str": liq.fecha.strftime("%d/%m/%y"),
                "ids": [],
                "n_liq": 0,
                "cuotas_eq": 0.0, "cuotas_crudo": 0,
                "contados_eq": 0.0, "contados_crudo": 0,
                "pool_c1": 0, "pool_c2": 0, "pool_total": 0,
                "extras_cantidad": 0,
                "total_a_rendir": 0.0,
                "_obs": [],
            }
            g["dias_dict"][dkey] = d
        d["ids"].append(liq.id)
        d["n_liq"] += 1
        d["cuotas_eq"]      += _liq_cuotas_eq
        d["cuotas_crudo"]   += int(liq.cuotas_vendidas or 0)
        d["contados_eq"]    += _liq_contados_eq
        d["contados_crudo"] += int(liq.contados_vendidos or 0)
        d["pool_c1"]    += int(_liq_pool.get("c1", 0))
        d["pool_c2"]    += int(_liq_pool.get("c2", 0))
        d["pool_total"] += int(_liq_pool.get("total", 0))
        d["extras_cantidad"] += (int(liq.cuotas_extras_cantidad or 0)
                                 + int(getattr(liq, "cuotas_extras_p0_cantidad", 0) or 0))
        d["total_a_rendir"]  += float(liq.total_a_rendir or 0)
        _obs = (liq.observacion or "").strip()
        if _obs:
            d["_obs"].append(_obs)

    # Convertir el dict de días a lista ordenada (día más reciente arriba)
    for g in grupos_mes_dict.values():
        dias = sorted(g["dias_dict"].values(), key=lambda d: d["fecha"], reverse=True)
        for d in dias:
            # Observaciones únicas, conservando orden
            d["obs_str"] = " · ".join(dict.fromkeys(d["_obs"]))
        g["dias"] = dias

    # Orden cronológico inverso: mes actual primero, luego pasados (más reciente arriba)
    liquidaciones_por_mes = sorted(
        grupos_mes_dict.values(),
        key=lambda g: (g["year"], g["month"]),
        reverse=True,
    )

    can_edit = auth_module.has_permission(user, "vendedores", "editar")

    # Mapa nombre_talonera → num_digitos para formatear rangos en tabla de entregas
    nd_por_talonera = {t.nombre: (t.num_digitos or 4) for t in taloneras}

    return templates.TemplateResponse(request, "vendedor_detalle.html", {
        "user": user, "v": v, "patas": patas,
        "pendientes_json": pendientes_json,
        "liquidaciones": liquidaciones,
        "can_edit": can_edit,
        "pendientes_count": len(pendientes),
        "ultima_liq": liquidaciones[0] if liquidaciones else None,
        "vendedores_all": vendedores_all,
        "grupos_talonera": grupos_talonera,
        "grupos_contado": grupos_contado,
        "nombres_contado": nombres_contado,
        "nd_por_talonera": nd_por_talonera,
        "entregas_vendedor": entregas_vendedor,
        "pata1_vc": pata1_vc,
        "pata0_vc": pata0_vc,
        "pata1_nc": pata1_nc,
        # nuevas métricas para el header
        "total_liquidaciones": len(liquidaciones),
        "total_boletas_liquidadas": total_boletas_liquidadas,
        "total_boletas_liquidadas_eq": total_boletas_liquidadas_eq,
        "ingresos_total": ingresos_total,
        "total_vendidas_pond": total_vendidas_pond,
        # acordeón mensual
        "liquidaciones_por_mes": liquidaciones_por_mes,
        "mes_actual_key": mes_actual_key,
        # items pool CONTADO por liquidación (números entregados a institución)
        "pool_by_liq_v": pool_by_liq_v,
    })


@router.post("/{vid}/liquidar")
async def liquidar(
    vid: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Liquida al vendedor: registra lo que el vendedor RINDE a la organización.
    Modelo:
      - Cuota 1 (boletas en cuotas): YA la tiene el vendedor en mano. NO entra al rinde.
      - Boletas al CONTADO (incluye CONTADO 2 VECES): el vendedor cobró el total de la
        talonera (num_cuotas × valor_cuota). Rinde el total MENOS comision_contados_pct%.
      - Cuotas extras cobradas (cuota 2, 3, ... que el vendedor cobró directo al socio):
        rinde el monto cobrado MENOS comision_cuotas_pct%.

    Total a rendir = monto_contados × (1 - %contado/100)
                   + cuotas_extras_monto × (1 - %cuotas/100)
    """
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    v = db.query(models.Vendedor).get(vid)
    if not v:
        raise HTTPException(404)

    form = await request.form()
    raw_ids = form.getlist("boleta_ids")
    comision_cuotas_pct   = float(form.get("comision_cuotas_pct",   5.0))
    comision_contados_pct = float(form.get("comision_contados_pct", 30.0))
    cuotas_extras_cantidad    = int(float(form.get("cuotas_extras_cantidad", 0) or 0))
    cuotas_extras_valor       = float(form.get("cuotas_extras_valor", 0) or 0)
    cuotas_extras_p0_cantidad = int(float(form.get("cuotas_extras_p0_cantidad", 0) or 0))
    cuotas_extras_p0_valor    = float(form.get("cuotas_extras_p0_valor", 0) or 0)
    observacion               = (form.get("observacion") or "").strip()

    boleta_ids = []
    # pool_ids: tuplas (talonera_id, numero) declaradas como entregadas a institución
    # (sin valor monetario). Se persisten como LiquidacionContadoItem.
    pool_ids: list[tuple[int, int]] = []
    for x in raw_ids:
        s = str(x).strip()
        if s.startswith("pool:"):
            try:
                _, tid_s, num_s = s.split(":", 2)
                pool_ids.append((int(tid_s), int(num_s)))
            except Exception:
                continue
        else:
            try:
                boleta_ids.append(int(s))
            except Exception:
                continue

    if (not boleta_ids and not pool_ids
            and cuotas_extras_cantidad == 0 and cuotas_extras_p0_cantidad == 0):
        return RedirectResponse(f"/vendedores/{vid}/detalle?msg=sin_pendientes", status_code=302)

    # Verificar que las boletas pertenezcan al vendedor y estén en CAJA sin liquidar
    boletas_sel = []
    if boleta_ids:
        boletas_sel = db.query(models.Boleta).filter(
            models.Boleta.id.in_(boleta_ids),
            models.Boleta.vendedor_id == vid,
            models.Boleta.condicion == CondicionBoleta.CAJA,
            models.Boleta.liquidacion_vendedor_id.is_(None),
        ).all()

    if (not boletas_sel and not pool_ids
            and cuotas_extras_cantidad == 0 and cuotas_extras_p0_cantidad == 0):
        return RedirectResponse(f"/vendedores/{vid}/detalle?msg=sin_pendientes", status_code=302)

    # Modalidad por boleta: 'cuotas' (default) | 'contado' | 'contado2'
    cuotas, contados_b = [], []
    for b in boletas_sel:
        modalidad = (form.get(f"modalidad_{b.id}") or "cuotas").strip().lower()
        if modalidad not in ("cuotas", "contado", "contado2"):
            modalidad = "cuotas"
        b.modalidad_liquidacion = modalidad
        if modalidad in ("contado", "contado2"):
            contados_b.append(b)
        else:
            cuotas.append(b)

    # Cuota 1 (informativa): el vendedor YA la cobró directo del socio. NO se le paga ni se le cobra.
    cuota_1_total  = sum((b.talonera.valor_cuota if b.talonera else 0.0) for b in cuotas)
    # Cuotas equivalentes = ponderado por multiplicador de PATA (Float desde 11/05/2026)
    # (PATA 0 ×0.67, PATA 1 ×1, PATA 2 ×2, etc.). Se guarda aparte para mostrarlo en la UI sin
    # tener que recalcular cada vez (y por si cambia el valor_cuota de PATA 1).
    cuotas_equiv = sum(float((b.talonera.multiplicador or 1.0) if b.talonera else 1.0) for b in cuotas)

    # Monto de cuotas (referencial — coincide con cuota_1_total porque es 1 cuota por boleta)
    monto_cuotas   = cuota_1_total
    com_cuotas     = round(cuota_1_total, 2)  # el vendedor retiene íntegramente la cuota 1 de boletas por cuotas

    # Comisión contado: % sobre el valor TOTAL de la talonera (num_cuotas × valor_cuota)
    # Usa la PATA de cada boleta marcada como contado.
    monto_contados = sum(
        ((b.talonera.num_cuotas or 12) * (b.talonera.valor_cuota if b.talonera else 0.0))
        for b in contados_b
    )
    contados_count = len(contados_b)
    # Ponderado por multiplicador de PATA (PATA 0 x0.67, PATA 1 x1, PATA 2 x2, ...)
    contados_equiv = sum(
        float((b.talonera.multiplicador or 1.0) if b.talonera else 1.0)
        for b in contados_b
    )
    com_contados   = round(monto_contados * comision_contados_pct / 100, 2)

    # Cuotas extras normales: input manual (cantidad × valor PATA 1)
    cuotas_extras_monto = round(cuotas_extras_cantidad * cuotas_extras_valor, 2)
    com_cuotas_extras   = round(cuotas_extras_monto * comision_cuotas_pct / 100, 2)

    # Cuotas extras PATA 0: input manual (cantidad × valor PATA 0, ~$10.000)
    cuotas_extras_p0_monto    = round(cuotas_extras_p0_cantidad * cuotas_extras_p0_valor, 2)
    com_cuotas_extras_p0      = round(cuotas_extras_p0_monto * comision_cuotas_pct / 100, 2)

    # Total a rendir = lo que el vendedor entrega a la organización
    total_rendir = round(
        (monto_contados - com_contados)
        + (cuotas_extras_monto    - com_cuotas_extras)
        + (cuotas_extras_p0_monto - com_cuotas_extras_p0),
        2
    )

    # total_comision (legacy) = suma de comisiones que se queda el vendedor (sin cuota 1)
    total_comision_legacy = round(com_cuotas + com_contados + com_cuotas_extras + com_cuotas_extras_p0, 2)

    liq = models.LiquidacionVendedor(
        vendedor_id=vid,
        cuotas_vendidas=len(cuotas),
        cuotas_equiv=cuotas_equiv,
        cuota_1_total=cuota_1_total,
        monto_cuotas=monto_cuotas,
        comision_cuotas_pct=comision_cuotas_pct,
        comision_cuotas=com_cuotas,
        contados_vendidos=contados_count,
        contados_equiv=contados_equiv,
        monto_contados=monto_contados,
        comision_contados_pct=comision_contados_pct,
        comision_contados=com_contados,
        cuotas_extras_cantidad=cuotas_extras_cantidad,
        cuotas_extras_valor=cuotas_extras_valor,
        cuotas_extras_monto=cuotas_extras_monto,
        comision_cuotas_extras=com_cuotas_extras,
        cuotas_extras_p0_cantidad=cuotas_extras_p0_cantidad,
        cuotas_extras_p0_valor=cuotas_extras_p0_valor,
        cuotas_extras_p0_monto=cuotas_extras_p0_monto,
        comision_cuotas_extras_p0=com_cuotas_extras_p0,
        total_comision=total_comision_legacy,
        total_a_rendir=total_rendir,
        observacion=observacion or None,
    )
    db.add(liq)
    db.flush()

    # Al liquidar, TODAS las boletas (cuotas y contado) pasan a VENDIDO. La decisión
    # de cobranza (EN_COBRANZA) se toma después, cuando se carga el socio: si la
    # boleta termina con cobrador y cuotas pendientes y no es contado → EN_COBRANZA.
    # Si es contado o ya está toda paga → se queda en VENDIDO.
    for b in boletas_sel:
        b.liquidacion_vendedor_id = liq.id
        b.condicion = CondicionBoleta.VENDIDO

    # Pool CONTADO/CONTADO 2 VECES: números entregados a la institución. Se
    # persisten como LiquidacionContadoItem para sacarlos de la caja del vendedor.
    # No tienen valor monetario (la talonera CONTADO ya se cobró aparte).
    # Dedup: evitar duplicar (talonera_id, numero) si vienen repetidos en el form.
    seen_pool: set[tuple[int, int]] = set()
    for tid, num in pool_ids:
        key = (tid, num)
        if key in seen_pool:
            continue
        seen_pool.add(key)
        db.add(models.LiquidacionContadoItem(
            liquidacion_id=liq.id,
            talonera_id=tid,
            numero=num,
        ))

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
    es_contado = all((t.tipo or "COMUN") == "CONTADO" for t in taloneras_match)

    nuevas = 0
    reasignadas = 0
    vendedores_origen = []

    if es_contado:
        nuevas = max(0, hasta - desde + 1)
        if vendedor_id and nuevas > 0:
            talonera_nombre_norm = talonera_nombre.strip().lower()
            otras_entregas = db.query(models.EntregaCaja).filter(
                models.EntregaCaja.talonera_nombre.ilike(talonera_nombre_norm),
                models.EntregaCaja.vendedor_id.isnot(None),
                models.EntregaCaja.vendedor_id != vendedor_id,
                models.EntregaCaja.hasta >= desde,
                models.EntregaCaja.desde <= hasta,
            ).all()
            for e in otras_entregas:
                ed, eh = int(e.desde), int(e.hasta)
                if ed >= desde and eh <= hasta:
                    if e.vendedor_id not in vendedores_origen:
                        vendedores_origen.append(e.vendedor_id)
                    db.delete(e)
                elif ed < desde and eh <= hasta:
                    if e.vendedor_id not in vendedores_origen:
                        vendedores_origen.append(e.vendedor_id)
                    e.hasta = desde - 1
                    e.boletas_afectadas = max(0, e.hasta - e.desde + 1)
                elif ed >= desde and eh > hasta:
                    if e.vendedor_id not in vendedores_origen:
                        vendedores_origen.append(e.vendedor_id)
                    e.desde = hasta + 1
                    e.boletas_afectadas = max(0, e.hasta - e.desde + 1)
                else:
                    if e.vendedor_id not in vendedores_origen:
                        vendedores_origen.append(e.vendedor_id)
                    e.hasta = desde - 1
                    e.boletas_afectadas = max(0, e.hasta - e.desde + 1)
                    nuevo_frag = models.EntregaCaja(
                        talonera_nombre=e.talonera_nombre,
                        desde=hasta + 1,
                        hasta=eh,
                        boletas_afectadas=max(0, eh - (hasta + 1) + 1),
                        vendedor_id=e.vendedor_id,
                        usuario_id=e.usuario_id,
                        observacion=e.observacion,
                    )
                    db.add(nuevo_frag)
    else:
        update_data = {"condicion": CondicionBoleta.CAJA}
        if vendedor_id:
            update_data["vendedor_id"] = vendedor_id

        nuevas = db.query(models.Boleta).filter(
            models.Boleta.talonera_id.in_(talonera_ids),
            models.Boleta.numero_principal >= desde,
            models.Boleta.numero_principal <= hasta,
            models.Boleta.condicion == CondicionBoleta.SIN_VENDER,
        ).update(update_data, synchronize_session=False)

        if vendedor_id:
            q_reasign = db.query(models.Boleta).filter(
                models.Boleta.talonera_id.in_(talonera_ids),
                models.Boleta.numero_principal >= desde,
                models.Boleta.numero_principal <= hasta,
                models.Boleta.condicion == CondicionBoleta.CAJA,
                models.Boleta.liquidacion_vendedor_id.is_(None),
                (models.Boleta.vendedor_id.is_(None)) | (models.Boleta.vendedor_id != vendedor_id),
            )
            vendedores_origen = [
                vid for (vid,) in q_reasign.with_entities(models.Boleta.vendedor_id).distinct().all()
                if vid is not None
            ]
            reasignadas = q_reasign.update({"vendedor_id": vendedor_id}, synchronize_session=False)

    total = nuevas + reasignadas

    if total == 0:
        db.rollback()
        return JSONResponse({
            "ok": True,
            "nuevas": 0,
            "reasignadas": 0,
            "total": 0,
            "actualizadas": 0,
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
        "actualizadas": total,
        "entrega_id": entrega.id,
        "vendedor_nombre": vend_nombre,
        "vendedor_id": vendedor_id,
        "vendedores_origen": vendedores_origen,
        "es_contado": es_contado,
    })


@router.post("/{vid}/pasar-caja")
async def pasar_caja(vid: int, request: Request, db: Session = Depends(get_db)):
    """Reasigna boletas que están en la CAJA de {vid} (sin liquidar) hacia
    otro vendedor. Caso típico: el jefe de equipo (ARIEL) recibe boletas de la
    institución, las distribuye físicamente a los demás vendedores y antes de
    liquidar las pasa a la caja del vendedor que realmente las va a vender.

    Form:
      - boleta_ids[]: IDs de boletas a pasar (deben pertenecer al vendedor {vid},
        estar en CAJA y sin liquidación_vendedor_id)
      - vendedor_destino_id: vendedor que va a recibirlas
    """
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")

    v_origen = db.query(models.Vendedor).get(vid)
    if not v_origen:
        return JSONResponse({"ok": False, "error": "Vendedor origen no encontrado"}, status_code=404)

    form = await request.form()
    raw_ids = form.getlist("boleta_ids")
    try:
        destino_id = int(form.get("vendedor_destino_id") or 0)
    except Exception:
        destino_id = 0

    if not destino_id:
        return JSONResponse({"ok": False, "error": "Falta vendedor destino"}, status_code=400)
    if destino_id == vid:
        return JSONResponse({"ok": False, "error": "El vendedor destino no puede ser el mismo"}, status_code=400)

    v_destino = db.query(models.Vendedor).get(destino_id)
    if not v_destino:
        return JSONResponse({"ok": False, "error": "Vendedor destino no encontrado"}, status_code=404)

    boleta_ids = []
    for x in raw_ids:
        try:
            boleta_ids.append(int(str(x)))
        except Exception:
            continue
    if not boleta_ids:
        return JSONResponse({"ok": False, "error": "No seleccionaste boletas"}, status_code=400)

    # Solo boletas del vendedor origen, en CAJA, sin liquidar
    boletas = db.query(models.Boleta).filter(
        models.Boleta.id.in_(boleta_ids),
        models.Boleta.vendedor_id == vid,
        models.Boleta.condicion == CondicionBoleta.CAJA,
        models.Boleta.liquidacion_vendedor_id.is_(None),
    ).all()

    if not boletas:
        return JSONResponse({"ok": False, "error": "Ninguna boleta válida para pasar"}, status_code=400)

    # Agrupar por talonera y generar rangos contiguos para el historial EntregaCaja
    from collections import defaultdict
    por_talonera = defaultdict(list)
    for b in boletas:
        if b.talonera_id is None:
            continue
        por_talonera[b.talonera_id].append(b)

    entregas_creadas = []
    for tid, bs in por_talonera.items():
        t = db.query(models.Talonera).get(tid)
        if not t:
            continue
        nums = sorted(b.numero_principal for b in bs)
        # Comprimir en rangos contiguos
        rangos = []
        ini = prev = nums[0]
        for n in nums[1:]:
            if n == prev + 1:
                prev = n
            else:
                rangos.append((ini, prev))
                ini = prev = n
        rangos.append((ini, prev))
        for d, h in rangos:
            entrega = models.EntregaCaja(
                talonera_nombre=t.nombre,
                desde=d,
                hasta=h,
                boletas_afectadas=h - d + 1,
                usuario_id=_perm_user.id,
                vendedor_id=destino_id,
                observacion=f"Pasada desde {v_origen.nombre}",
            )
            db.add(entrega)
            entregas_creadas.append(entrega)

    # Reasignar las boletas al vendedor destino
    total = 0
    for b in boletas:
        b.vendedor_id = destino_id
        total += 1

    db.commit()

    return JSONResponse({
        "ok": True,
        "total": total,
        "vendedor_destino_id": destino_id,
        "vendedor_destino_nombre": v_destino.nombre,
        "entregas_creadas": len(entregas_creadas),
    })


@router.post("/{vid}/retirar-caja")
async def retirar_caja(vid: int, request: Request, db: Session = Depends(get_db)):
    """Retira boletas de la caja de {vid} y las DEVUELVE A LA INSTITUCIÓN.
    Caso: números que el vendedor no vendió y ya no va a vender; no pasan a
    otro vendedor — vuelven al stock (SIN_VENDER, sin vendedor).

    Solo afecta boletas en CAJA, sin liquidar y sin comprador (las que el
    vendedor todavía tiene físicamente en mano). Deja registro en el historial
    como tipo='RETIRO'.

    Form:
      - boleta_ids[]: IDs de boletas a retirar.
    """
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")

    v_origen = db.query(models.Vendedor).get(vid)
    if not v_origen:
        return JSONResponse({"ok": False, "error": "Vendedor no encontrado"}, status_code=404)

    form = await request.form()
    raw_ids = form.getlist("boleta_ids")
    boleta_ids = []
    for x in raw_ids:
        try:
            boleta_ids.append(int(str(x)))
        except Exception:
            continue
    if not boleta_ids:
        return JSONResponse({"ok": False, "error": "No seleccionaste boletas"}, status_code=400)

    # Solo boletas del vendedor, en CAJA, sin liquidar y sin comprador
    boletas = db.query(models.Boleta).filter(
        models.Boleta.id.in_(boleta_ids),
        models.Boleta.vendedor_id == vid,
        models.Boleta.condicion == CondicionBoleta.CAJA,
        models.Boleta.liquidacion_vendedor_id.is_(None),
        models.Boleta.comprador_id.is_(None),
    ).all()
    if not boletas:
        return JSONResponse({"ok": False, "error": "Ninguna boleta válida para retirar"}, status_code=400)

    # Agrupar por talonera y comprimir en rangos contiguos para el historial
    from collections import defaultdict
    por_talonera = defaultdict(list)
    for b in boletas:
        if b.talonera_id is None:
            continue
        por_talonera[b.talonera_id].append(b)

    entregas_creadas = 0
    for tid, bs in por_talonera.items():
        t = db.query(models.Talonera).get(tid)
        if not t:
            continue
        nums = sorted(b.numero_principal for b in bs)
        rangos = []
        ini = prev = nums[0]
        for n in nums[1:]:
            if n == prev + 1:
                prev = n
            else:
                rangos.append((ini, prev))
                ini = prev = n
        rangos.append((ini, prev))
        for d, h in rangos:
            db.add(models.EntregaCaja(
                talonera_nombre=t.nombre,
                desde=d,
                hasta=h,
                boletas_afectadas=h - d + 1,
                usuario_id=_perm_user.id,
                vendedor_id=vid,
                tipo="RETIRO",
                observacion="Retiro — vuelve a la institución",
            ))
            entregas_creadas += 1

    # Devolver las boletas al stock de la institución
    total = 0
    for b in boletas:
        b.condicion = CondicionBoleta.SIN_VENDER
        b.vendedor_id = None
        total += 1

    db.commit()

    return JSONResponse({
        "ok": True,
        "total": total,
        "entregas_creadas": entregas_creadas,
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

    # Valores ANTERIORES de la entrega (para revertir las boletas reales)
    old_nombre = e.talonera_nombre
    old_desde = int(e.desde)
    old_hasta = int(e.hasta)
    old_vendedor_id = e.vendedor_id

    if hasta < desde:
        raise HTTPException(400, "Rango inválido")

    # Vendedor al que aplica la entrega editada (si no se cambia, el actual)
    target_vid = vendedor_id or old_vendedor_id

    # ── Resolver taloneras viejas y nuevas por nombre ──────────────────────
    def _resolver(nombre):
        ts = db.query(models.Talonera).filter_by(nombre=nombre).all()
        ids = [t.id for t in ts]
        es_contado = bool(ts) and all((t.tipo or "COMUN") == "CONTADO" for t in ts)
        return ids, es_contado

    old_ids, old_es_contado = _resolver(old_nombre)
    new_ids, new_es_contado = _resolver(talonera_nombre)

    revertidas = 0
    saltadas = 0          # boletas del rango viejo que NO se pudieron revertir (vendidas/liquidadas)
    aplicadas = 0

    # 1) REVERTIR el rango VIEJO → solo boletas COMUN que siguen en CAJA,
    #    sin liquidar y sin comprador (seguras de devolver a SIN_VENDER).
    if old_ids and not old_es_contado:
        q_old = db.query(models.Boleta).filter(
            models.Boleta.talonera_id.in_(old_ids),
            models.Boleta.numero_principal >= old_desde,
            models.Boleta.numero_principal <= old_hasta,
        )
        if old_vendedor_id:
            q_old = q_old.filter(models.Boleta.vendedor_id == old_vendedor_id)

        revertibles = q_old.filter(
            models.Boleta.condicion == CondicionBoleta.CAJA,
            models.Boleta.liquidacion_vendedor_id.is_(None),
            models.Boleta.comprador_id.is_(None),
        )
        # Contar las que NO son revertibles para avisar (vendidas/liquidadas)
        saltadas = q_old.filter(
            (models.Boleta.condicion != CondicionBoleta.CAJA)
            | (models.Boleta.liquidacion_vendedor_id.isnot(None))
            | (models.Boleta.comprador_id.isnot(None)),
        ).count()
        revertidas = revertibles.update(
            {"condicion": CondicionBoleta.SIN_VENDER, "vendedor_id": None},
            synchronize_session=False,
        )

    # 2) APLICAR el rango NUEVO → mismas reglas que "Entrega a Caja".
    if new_ids and not new_es_contado:
        update_data = {"condicion": CondicionBoleta.CAJA}
        if target_vid:
            update_data["vendedor_id"] = target_vid
        nuevas = db.query(models.Boleta).filter(
            models.Boleta.talonera_id.in_(new_ids),
            models.Boleta.numero_principal >= desde,
            models.Boleta.numero_principal <= hasta,
            models.Boleta.condicion == CondicionBoleta.SIN_VENDER,
        ).update(update_data, synchronize_session=False)

        reasignadas = 0
        if target_vid:
            reasignadas = db.query(models.Boleta).filter(
                models.Boleta.talonera_id.in_(new_ids),
                models.Boleta.numero_principal >= desde,
                models.Boleta.numero_principal <= hasta,
                models.Boleta.condicion == CondicionBoleta.CAJA,
                models.Boleta.liquidacion_vendedor_id.is_(None),
                (models.Boleta.vendedor_id.is_(None)) | (models.Boleta.vendedor_id != target_vid),
            ).update({"vendedor_id": target_vid}, synchronize_session=False)
        aplicadas = nuevas + reasignadas

    # 3) Actualizar el registro de historial
    e.talonera_nombre = talonera_nombre
    e.desde = desde
    e.hasta = hasta
    e.observacion = observacion.strip() or None
    e.vendedor_id = target_vid
    if not new_es_contado:
        e.boletas_afectadas = aplicadas
    else:
        e.boletas_afectadas = max(0, hasta - desde + 1)
    db.commit()

    # Redirige al detalle del vendedor con un resumen del impacto
    _vid = target_vid or old_vendedor_id
    if _vid:
        return RedirectResponse(
            f"/vendedores/{_vid}/detalle?msg=caja_editada"
            f"&apl={aplicadas}&rev={revertidas}&salt={saltadas}",
            status_code=302,
        )
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


@router.post("/liquidaciones/{liq_id}/eliminar")
async def eliminar_liquidacion(liq_id: int, request: Request, db: Session = Depends(get_db)):
    """Elimina una liquidacion completa. SOLO admin.
    Efectos:
      - Boletas asociadas: liquidacion_vendedor_id -> NULL (vuelven a CAJA sin liq).
        Conservan su vendedor_id actual (no se reasignan).
      - LiquidacionContadoItem de la liq: se borran (numeros del pool vuelven al pool libre).
      - LiquidacionVendedor: se borra.
    """
    user = await auth_module.require_user(request, db)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "Solo admin puede eliminar liquidaciones")
    liq = db.query(models.LiquidacionVendedor).get(liq_id)
    if not liq:
        raise HTTPException(404, "Liquidacion no encontrada")
    vid = liq.vendedor_id
    # Resetear boletas asociadas -> vuelven a CAJA sin liquidacion
    db.query(models.Boleta).filter_by(liquidacion_vendedor_id=liq_id).update(
        {"liquidacion_vendedor_id": None, "condicion": CondicionBoleta.CAJA},
        synchronize_session=False
    )
    # Borrar items pool CONTADO (cascade tambien deberia hacerlo, lo hacemos explicito)
    db.query(models.LiquidacionContadoItem).filter_by(
        liquidacion_id=liq_id
    ).delete(synchronize_session=False)
    db.delete(liq)
    db.commit()
    return RedirectResponse(f"/vendedores/{vid}/detalle?msg=liq_eliminada", status_code=302)


@router.post("/liquidaciones/eliminar-dia")
async def eliminar_liquidaciones_dia(request: Request, db: Session = Depends(get_db)):
    """Elimina TODAS las liquidaciones de un día (las que figuran agrupadas en una
    misma fila del historial). SOLO admin. Mismo efecto que eliminar cada una:
    las boletas vuelven a CAJA sin liquidar y el pool CONTADO vuelve al pool libre.
    Recibe `ids` (lista de IDs separados por coma)."""
    user = await auth_module.require_user(request, db)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "Solo admin puede eliminar liquidaciones")
    form = await request.form()
    raw = (form.get("ids") or "").strip()
    try:
        ids = [int(x) for x in raw.split(",") if x.strip()]
    except Exception:
        ids = []
    if not ids:
        raise HTTPException(400, "No se indicaron liquidaciones a eliminar")
    liqs = db.query(models.LiquidacionVendedor).filter(
        models.LiquidacionVendedor.id.in_(ids)
    ).all()
    if not liqs:
        raise HTTPException(404, "Liquidaciones no encontradas")
    vid = liqs[0].vendedor_id
    for liq in liqs:
        db.query(models.Boleta).filter_by(liquidacion_vendedor_id=liq.id).update(
            {"liquidacion_vendedor_id": None, "condicion": CondicionBoleta.CAJA},
            synchronize_session=False
        )
        db.query(models.LiquidacionContadoItem).filter_by(
            liquidacion_id=liq.id
        ).delete(synchronize_session=False)
        db.delete(liq)
    db.commit()
    return RedirectResponse(f"/vendedores/{vid}/detalle?msg=liq_eliminada", status_code=302)


def _recalc_liq_totales(liq):
    """Recalcula total_a_rendir y total_comision (legacy) desde los campos componentes.
    Se usa al editar una liquidación (agregar/sacar números) para mantener todo coherente."""
    liq.total_a_rendir = round(
        (float(liq.monto_contados or 0)         - float(liq.comision_contados or 0))
        + (float(liq.cuotas_extras_monto or 0)    - float(liq.comision_cuotas_extras or 0))
        + (float(liq.cuotas_extras_p0_monto or 0) - float(liq.comision_cuotas_extras_p0 or 0)),
        2,
    )
    liq.total_comision = round(
        float(liq.comision_cuotas or 0) + float(liq.comision_contados or 0)
        + float(liq.comision_cuotas_extras or 0) + float(liq.comision_cuotas_extras_p0 or 0),
        2,
    )


@router.post("/liquidaciones/{liq_id}/agregar-boleta", response_class=JSONResponse)
async def liquidacion_agregar_boleta(liq_id: int, request: Request, db: Session = Depends(get_db)):
    """Agrega un número olvidado a una liquidación existente.
    El número debe ser una boleta del MISMO vendedor, en CAJA y sin liquidar.
    modalidad: 'cuotas' (default) | 'contado' | 'contado2'.
    """
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    liq = db.query(models.LiquidacionVendedor).get(liq_id)
    if not liq:
        raise HTTPException(404, "Liquidacion no encontrada")

    form = await request.form()
    try:
        boleta_id = int(form.get("boleta_id"))
    except Exception:
        return JSONResponse({"ok": False, "error": "Boleta inválida."}, status_code=400)
    modalidad = (form.get("modalidad") or "cuotas").strip().lower()
    if modalidad not in ("cuotas", "contado", "contado2"):
        modalidad = "cuotas"

    b = db.query(models.Boleta).get(boleta_id)
    if not b:
        return JSONResponse({"ok": False, "error": "Boleta no encontrada."}, status_code=404)
    if b.vendedor_id != liq.vendedor_id:
        return JSONResponse({"ok": False, "error": "El número no es de este vendedor."}, status_code=400)
    if b.condicion != CondicionBoleta.CAJA or b.liquidacion_vendedor_id is not None:
        return JSONResponse({"ok": False, "error": "El número no está en caja sin liquidar."}, status_code=400)

    mult        = float((b.talonera.multiplicador or 1.0) if b.talonera else 1.0)
    valor_cuota = float((b.talonera.valor_cuota or 0.0) if b.talonera else 0.0)
    if modalidad in ("contado", "contado2"):
        num_cuotas = int((b.talonera.num_cuotas or 12) if b.talonera else 12)
        pct        = float(liq.comision_contados_pct or 0) or 30.0
        monto      = num_cuotas * valor_cuota
        com        = round(monto * pct / 100, 2)
        liq.contados_vendidos = int(liq.contados_vendidos or 0) + 1
        liq.contados_equiv    = float(liq.contados_equiv or 0) + mult
        liq.monto_contados    = round(float(liq.monto_contados or 0) + monto, 2)
        liq.comision_contados = round(float(liq.comision_contados or 0) + com, 2)
        if not liq.comision_contados_pct:
            liq.comision_contados_pct = pct
    else:
        liq.cuotas_vendidas = int(liq.cuotas_vendidas or 0) + 1
        liq.cuotas_equiv    = float(liq.cuotas_equiv or 0) + mult
        liq.cuota_1_total   = round(float(liq.cuota_1_total or 0) + valor_cuota, 2)
        liq.monto_cuotas    = round(float(liq.monto_cuotas or 0) + valor_cuota, 2)
        liq.comision_cuotas = round(float(liq.comision_cuotas or 0) + valor_cuota, 2)

    b.liquidacion_vendedor_id = liq.id
    b.condicion = CondicionBoleta.VENDIDO
    b.modalidad_liquidacion = modalidad
    _recalc_liq_totales(liq)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/liquidaciones/{liq_id}/quitar-boleta", response_class=JSONResponse)
async def liquidacion_quitar_boleta(liq_id: int, request: Request, db: Session = Depends(get_db)):
    """Saca un número de una liquidación existente (vuelve a CAJA sin liquidar).
    Solo si la boleta todavía NO fue cargada con un socio (comprador_id is None).
    """
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, "vendedores", "editar"):
        raise HTTPException(403, "Sin permiso")
    liq = db.query(models.LiquidacionVendedor).get(liq_id)
    if not liq:
        raise HTTPException(404, "Liquidacion no encontrada")

    form = await request.form()
    try:
        boleta_id = int(form.get("boleta_id"))
    except Exception:
        return JSONResponse({"ok": False, "error": "Boleta inválida."}, status_code=400)

    b = db.query(models.Boleta).get(boleta_id)
    if not b or b.liquidacion_vendedor_id != liq_id:
        return JSONResponse({"ok": False, "error": "El número no pertenece a esta liquidación."}, status_code=400)
    if b.comprador_id is not None:
        return JSONResponse({"ok": False, "error": "Ya tiene un socio cargado: no se puede sacar."}, status_code=400)

    modalidad   = (b.modalidad_liquidacion or "cuotas").strip().lower()
    mult        = float((b.talonera.multiplicador or 1.0) if b.talonera else 1.0)
    valor_cuota = float((b.talonera.valor_cuota or 0.0) if b.talonera else 0.0)
    if modalidad in ("contado", "contado2"):
        num_cuotas = int((b.talonera.num_cuotas or 12) if b.talonera else 12)
        pct        = float(liq.comision_contados_pct or 0) or 30.0
        monto      = num_cuotas * valor_cuota
        com        = round(monto * pct / 100, 2)
        liq.contados_vendidos = max(0, int(liq.contados_vendidos or 0) - 1)
        liq.contados_equiv    = max(0.0, float(liq.contados_equiv or 0) - mult)
        liq.monto_contados    = max(0.0, round(float(liq.monto_contados or 0) - monto, 2))
        liq.comision_contados = max(0.0, round(float(liq.comision_contados or 0) - com, 2))
    else:
        liq.cuotas_vendidas = max(0, int(liq.cuotas_vendidas or 0) - 1)
        liq.cuotas_equiv    = max(0.0, float(liq.cuotas_equiv or 0) - mult)
        liq.cuota_1_total   = max(0.0, round(float(liq.cuota_1_total or 0) - valor_cuota, 2))
        liq.monto_cuotas    = max(0.0, round(float(liq.monto_cuotas or 0) - valor_cuota, 2))
        liq.comision_cuotas = max(0.0, round(float(liq.comision_cuotas or 0) - valor_cuota, 2))

    b.liquidacion_vendedor_id = None
    b.condicion = CondicionBoleta.CAJA
    b.modalidad_liquidacion = None
    _recalc_liq_totales(liq)
    db.commit()
    return JSONResponse({"ok": True})


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
