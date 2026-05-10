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
    """Conteos por vendedor.
    - caja: CAJA sin liquidar (vendedor las tiene físicamente, sin liq_id)
    - liq_pendiente: CAJA con liq pero sin comprador cargado todavía
    - baja: BAJA (sub-estado informativo, solo viene de cobranza tras carga de socio)
    - vendido: TODAS las boletas cargadas con socio (comprador_id IS NOT NULL),
      sin importar la condicion posterior (VENDIDO + CAJA-Al-contado +
      EN_COBRANZA + BAJA). Mismo criterio que el dashboard de Reportes y
      Top Vendedores. Confirmado con Sergio (09/05/2026): aunque la boleta
      pase a EN_COBRANZA o BAJA, sigue siendo trabajo del vendedor.
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
        elif cond == CondicionBoleta.BAJA:
            stats[vid]["baja"] = total

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
            stats[vid] = {"caja": 0, "liq_pendiente": 0, "vendido": 0, "baja": 0}
        stats[vid]["vendido"] = total

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
    boletas_out = []
    for b in boletas:
        nd = (b.talonera.num_digitos or 4) if b.talonera else 4
        fmt = "{:0" + str(nd) + "d}"
        boletas_out.append({
            "id": b.id,
            "num": b.numero_principal,
            "num_str": fmt.format(b.numero_principal),
            "pata": b.talonera.nombre if b.talonera else "?",
            "color": b.talonera.color if b.talonera else "#cccccc",
            "condicion": b.condicion.value if b.condicion else "?",
            "comprador": b.comprador.apellido_nombre if b.comprador else None,
            "multiplicador": int((b.talonera.multiplicador or 1) if b.talonera else 1),
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
    _cuotas_equiv = int(getattr(liq, "cuotas_equiv", 0) or 0)
    if _cuotas_equiv == 0:
        if int(liq.contados_vendidos or 0) == 0 and boletas:
            _cuotas_equiv = sum(int(b.get("multiplicador") or 1) for b in boletas_out)
        else:
            _cuotas_equiv = int(liq.cuotas_vendidas or 0)

    return JSONResponse({
        "id": liq.id,
        "fecha": liq.fecha.strftime("%d/%m/%Y %H:%M") if liq.fecha else "",
        "cuotas_vendidas":         int(liq.cuotas_vendidas or 0),
        "cuotas_equiv":            _cuotas_equiv,
        "contados_vendidos":       int(liq.contados_vendidos or 0),
        "monto_contados":          float(liq.monto_contados or 0),
        "comision_contados_pct":   float(liq.comision_contados_pct or 0),
        "comision_contados":       float(liq.comision_contados or 0),
        "cuotas_extras_cantidad":  int(getattr(liq, "cuotas_extras_cantidad", 0) or 0),
        "cuotas_extras_valor":     float(getattr(liq, "cuotas_extras_valor", 0) or 0),
        "cuotas_extras_monto":     float(getattr(liq, "cuotas_extras_monto", 0) or 0),
        "comision_cuotas_pct":     float(liq.comision_cuotas_pct or 0),
        "comision_cuotas_extras":  float(getattr(liq, "comision_cuotas_extras", 0) or 0),
        "total_a_rendir":          float(getattr(liq, "total_a_rendir", 0) or 0),
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
        nd = (b.talonera.num_digitos or 4) if b.talonera else 4
        fmt = "{:0" + str(nd) + "d}"
        if pata_nombre not in patas:
            patas[pata_nombre] = {"color": pata_color, "boletas": [], "num_digitos": nd}
        patas[pata_nombre]["boletas"].append({
            "id":      b.id,
            "num":     b.numero_principal,
            "num_str": fmt.format(b.numero_principal),
            "cond":    b.condicion.value if b.condicion else "?",
            "liq":     b.liquidacion_vendedor_id is not None,
            "contado": (b.numero_especial is not None) or (b.numero_especial_2 is not None),
            "pool":    False,
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
        # Excluir numeros ya liquidados (pendientes de cargar al socio)
        nums_liquidados_t = {n for (tid, n) in nums_ya_liquidados if tid == t.id}
        pendientes_pool = sorted(nums_entregados - nums_asignados - nums_liquidados_t)
        if not pendientes_pool:
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
                "talonera_id": t.id,
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

    pendientes_items = [
        {
            "id":           b.id,
            "num":          b.numero_principal,
            "num_str":      ("{:0" + str(b.talonera.num_digitos or 4) + "d}").format(b.numero_principal) if b.talonera else str(b.numero_principal),
            "pata":         b.talonera.nombre        if b.talonera else "?",
            "color":        b.talonera.color         if b.talonera else "#cccccc",
            "valor_cuota":  b.talonera.valor_cuota   if b.talonera else 0.0,
            "num_cuotas":   (b.talonera.num_cuotas   if b.talonera and b.talonera.num_cuotas else 12),
            "multiplicador": int(b.talonera.multiplicador or 1) if b.talonera else 1,
            "talonera_id":  b.talonera_id            if b.talonera else None,
        }
        for b in sorted(pendientes, key=lambda x: (x.talonera.nombre if x.talonera else "", x.numero_principal))
    ]
    pendientes_json = json.dumps(pendientes_items)

    liquidaciones = db.query(models.LiquidacionVendedor).filter_by(
        vendedor_id=vid
    ).order_by(models.LiquidacionVendedor.fecha.desc()).all()

    # ── Métricas acumuladas para tarjetas del header ─────────────────────
    # Total boletas liquidadas (literal, suma cuotas + contados)
    total_boletas_liquidadas = sum(
        (liq.cuotas_vendidas or 0) + (liq.contados_vendidos or 0)
        for liq in liquidaciones
    )
    # Total boletas liquidadas ponderadas por PATA (cuotas_equiv ya está ponderado;
    # para contados usamos contados_vendidos crudo porque su monto ya viene ponderado)
    total_boletas_liquidadas_eq = sum(
        (liq.cuotas_equiv or liq.cuotas_vendidas or 0) + (liq.contados_vendidos or 0)
        for liq in liquidaciones
    )
    # Ingresos del vendedor: lo que se queda en su bolsillo
    # = cuota 1 (cobrada directo al socio) + comisión contados + comisión cuotas extras
    ingresos_total = sum(
        (liq.cuota_1_total or 0)
        + (liq.comision_contados or 0)
        + (liq.comision_cuotas_extras or 0)
        for liq in liquidaciones
    )
    # Total vendidas: boletas con comprador cargado, ponderado por multiplicador de PATA
    total_vendidas_pond = sum(
        int((b.talonera.multiplicador or 1) if b.talonera else 1)
        for b in boletas if b.comprador_id is not None
    )

    # ── Agrupación de liquidaciones por mes (para acordeón) ──────────────
    _MESES_ES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
                 "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
    from datetime import datetime as _dt
    _hoy = _dt.utcnow()
    mes_actual_key = f"{_hoy.year:04d}-{_hoy.month:02d}"

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
                "total_ingresos": 0.0,
                "total_rinde": 0.0,
                "total_boletas": 0,
            }
        g = grupos_mes_dict[key]
        g["liquidaciones"].append(liq)
        g["total_ingresos"] += (liq.cuota_1_total or 0) + (liq.comision_contados or 0) + (liq.comision_cuotas_extras or 0)
        g["total_rinde"] += (liq.total_a_rendir or 0)
        g["total_boletas"] += (liq.cuotas_equiv or liq.cuotas_vendidas or 0) + (liq.contados_vendidos or 0)

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
    cuotas_extras_cantidad = int(float(form.get("cuotas_extras_cantidad", 0) or 0))
    cuotas_extras_valor    = float(form.get("cuotas_extras_valor", 0) or 0)
    observacion           = (form.get("observacion") or "").strip()

    boleta_ids = []
    for x in raw_ids:
        try:
            boleta_ids.append(int(str(x)))
        except Exception:
            continue

    if not boleta_ids and cuotas_extras_cantidad == 0:
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

    if not boletas_sel and cuotas_extras_cantidad == 0:
        return RedirectResponse(f"/vendedores/{vid}/detalle?msg=sin_pendientes", status_code=302)

    # Modalidad por boleta: 'cuotas' (default) | 'contado' | 'contado2'
    cuotas, contados_b = [], []
    for b in boletas_sel:
        modalidad = (form.get(f"modalidad_{b.id}") or "cuotas").strip().lower()
        if modalidad in ("contado", "contado2"):
            contados_b.append(b)
        else:
            cuotas.append(b)

    # Cuota 1 (informativa): el vendedor YA la cobró directo del socio. NO se le paga ni se le cobra.
    cuota_1_total  = sum((b.talonera.valor_cuota if b.talonera else 0.0) for b in cuotas)
    # Cuotas equivalentes = ponderado por multiplicador de PATA
    # (PATA 1 ×1, PATA 2 ×2, etc.). Se guarda aparte para mostrarlo en la UI sin
    # tener que recalcular cada vez (y por si cambia el valor_cuota de PATA 1).
    cuotas_equiv = sum(int((b.talonera.multiplicador or 1) if b.talonera else 1) for b in cuotas)

    # Monto de cuotas (referencial — coincide con cuota_1_total porque es 1 cuota por boleta)
    monto_cuotas   = cuota_1_total
    com_cuotas     = 0.0  # ya no se calcula sobre cuota 1; la comisión de cuotas se aplica sobre EXTRAS

    # Comisión contado: % sobre el valor TOTAL de la talonera (num_cuotas × valor_cuota)
    # Usa la PATA de cada boleta marcada como contado.
    monto_contados = sum(
        ((b.talonera.num_cuotas or 12) * (b.talonera.valor_cuota if b.talonera else 0.0))
        for b in contados_b
    )
    contados_count = len(contados_b)
    com_contados   = round(monto_contados * comision_contados_pct / 100, 2)

    # Cuotas extras: input manual (cantidad × valor)
    cuotas_extras_monto = round(cuotas_extras_cantidad * cuotas_extras_valor, 2)
    com_cuotas_extras   = round(cuotas_extras_monto * comision_cuotas_pct / 100, 2)

    # Total a rendir = lo que el vendedor entrega a la organización
    total_rendir = round(
        (monto_contados - com_contados) + (cuotas_extras_monto - com_cuotas_extras),
        2
    )

    # total_comision (legacy) = suma de comisiones que se queda el vendedor (sin cuota 1)
    total_comision_legacy = round(com_contados + com_cuotas_extras, 2)

    liq = models.LiquidacionVendedor(
        vendedor_id=vid,
        cuotas_vendidas=len(cuotas),
        cuotas_equiv=cuotas_equiv,
        cuota_1_total=cuota_1_total,
        monto_cuotas=monto_cuotas,
        comision_cuotas_pct=comision_cuotas_pct,
        comision_cuotas=com_cuotas,
        contados_vendidos=contados_count,
        monto_contados=monto_contados,
        comision_contados_pct=comision_contados_pct,
        comision_contados=com_contados,
        cuotas_extras_cantidad=cuotas_extras_cantidad,
        cuotas_extras_valor=cuotas_extras_valor,
        cuotas_extras_monto=cuotas_extras_monto,
        comision_cuotas_extras=com_cuotas_extras,
        total_comision=total_comision_legacy,
        total_a_rendir=total_rendir,
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
