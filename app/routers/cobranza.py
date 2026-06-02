from fastapi import APIRouter, Depends, Request, Query, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from datetime import date
from typing import List, Optional
import json
import re
from .. import models, auth as auth_module

# Regex para extraer el número de PATA. Acepta "PATA 0", "PATA 12", "X4", etc.
# Da prioridad al patrón "PATA N"; si no, busca "Xn" como fallback.
_PATA_RE = re.compile(r'PATA\s*(\d+)', re.IGNORECASE)
_X_RE = re.compile(r'X(\d+)', re.IGNORECASE)


def _extraer_pata(nombre: str) -> str:
    """Devuelve el dígito de la PATA (ej: '4' para 'PATA 4' o 'X4') o '?' si no encuentra.

    Centraliza la lógica para que el bug 'X?' no se repita en cada uso suelto.
    """
    if not nombre:
        return "?"
    n = nombre.upper()
    m = _PATA_RE.search(n) or _X_RE.search(n)
    return m.group(1) if m else "?"
from ..templates_config import templates
from ..models import CondicionBoleta
from ..database import get_db

router = APIRouter(prefix="/cobranza", tags=["cobranza"])

MESES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
         "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

_MESES_UPPER = [m.upper() for m in MESES]

def _meses_campana_desde(mes_planilla: int, num_cuotas: int):
    """Genera la lista de (nombre, num_mes) para la tabla de cuotas.
    Cuota 1 = mes siguiente al mes de la planilla (ej: planilla Mayo → cuota 1 = Junio)."""
    inicio = mes_planilla % 12  # 5 → 5 (índice 0-based de Junio)
    return [
        (_MESES_UPPER[(inicio + i) % 12], (inicio + i) % 12 + 1)
        for i in range(num_cuotas)
    ]


def _pata_valor(boleta) -> int:
    """Retorna el valor de PATA de una boleta (1, 2 o 3) según el nombre de la talonera."""
    if boleta.talonera:
        nombre = boleta.talonera.nombre.upper()
        for n in (3, 2, 1):
            if str(n) in nombre:
                return n
    return 1


def _get_pata_boleta(b) -> str:
    """Dígito de PATA de una boleta ('0', '1', '2'...) o '?' si no tiene talonera."""
    if b and b.talonera:
        return _extraer_pata(b.talonera.nombre)
    return "?"


def _get_color_boleta(b) -> str:
    """Color de la PATA de una boleta; '#cccccc' por defecto / si es blanco."""
    if b and b.talonera and b.talonera.color:
        c = b.talonera.color.strip()
        return c if c and c != "#ffffff" else "#cccccc"
    return "#cccccc"


def _armar_grid_patas(boletas, rows_per_col: int = 40):
    """Distribuye las boletas en un grid de 3 columnas × `rows_per_col` filas,
    agrupándolas por PATA e insertando filas-etiqueta (separadores X0/X1/X2…)
    entre grupos. Las boletas deben venir ordenadas por numero_principal.

    Cada celda es una boleta, un dict {"type":"label",...} (separador) o None.

    Devuelve un dict con:
      - "cols":   tupla (c1, c2, c3) — las 3 columnas físicas (listas).
      - "rows":   lista de tuplas (c1[i], c2[i], c3[i]) para iterar el tbody.
      - "labels": tupla (X1, X2, X3) — PATA inicial de cada columna (header).
      - "colors": tupla de colores de header por columna.

    Las etiquetas se cuelgan en la última fila vacía del bloque previo; si el
    bloque quedó lleno justo en el límite de 10, la etiqueta ocupa la primera
    fila del bloque nuevo y empuja las boletas (evita perder el separador)."""
    ROWS_PER_COL = rows_per_col
    TOTAL = ROWS_PER_COL * 3
    grid = [None] * TOTAL

    # Agrupar boletas consecutivas por PATA
    pata_grupos, cur_pata, cur_grupo = [], None, []
    for b in boletas:
        p = _get_pata_boleta(b)
        if p != cur_pata:
            if cur_grupo:
                pata_grupos.append(cur_grupo)
            cur_pata, cur_grupo = p, [b]
        else:
            cur_grupo.append(b)
    if cur_grupo:
        pata_grupos.append(cur_grupo)

    # Llenar el grid: cada grupo arranca en un bloque de 10 con fila-etiqueta
    pos = 0
    for i, grupo in enumerate(pata_grupos):
        if i > 0:
            new_block = pos if pos % 10 == 0 else ((pos // 10) + 1) * 10
            # Si el nuevo bloque arranca al inicio de una columna, la etiqueta
            # va ARRIBA de esa columna (el header ya queda pintado), no huérfana
            # al final de la columna previa.
            is_new_column = new_block > 0 and new_block % ROWS_PER_COL == 0
            label = {"type": "label",
                     "pata": f"X{_get_pata_boleta(grupo[0])}",
                     "color": _get_color_boleta(grupo[0])}
            if is_new_column and new_block < TOTAL:
                pos = new_block
            elif pos % 10 == 0:
                # El bloque anterior se llenó justo en el límite de 10 filas:
                # no quedó celda vacía donde colgar la etiqueta, así que ocupa
                # la primera fila del bloque nuevo (empuja las boletas).
                if pos < TOTAL:
                    grid[pos] = label
                    pos += 1
            else:
                label_pos = new_block - 1
                if 0 <= label_pos < TOTAL and grid[label_pos] is None:
                    grid[label_pos] = label
                pos = new_block
        for b in grupo:
            if pos < TOTAL:
                grid[pos] = b
                pos += 1

    c1 = grid[0:ROWS_PER_COL]
    c2 = grid[ROWS_PER_COL:ROWS_PER_COL * 2]
    c3 = grid[ROWS_PER_COL * 2:ROWS_PER_COL * 3]
    rows = [(c1[i], c2[i], c3[i]) for i in range(ROWS_PER_COL)]

    def _col_header(col):
        for cell in col:
            if isinstance(cell, dict):
                return cell["pata"]
            elif cell:
                p = _get_pata_boleta(cell)
                if p != "?":
                    return f"X{p}"
        return ""

    def _col_color(col):
        for cell in col:
            if isinstance(cell, dict):
                return cell["color"]
            elif cell:
                c = _get_color_boleta(cell)
                if c:
                    return c
        return ""

    return {
        "cols": (c1, c2, c3),
        "rows": rows,
        "labels": (_col_header(c1), _col_header(c2), _col_header(c3)),
        "colors": (_col_color(c1), _col_color(c2), _col_color(c3)),
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db),
                mes: int = Query(default=0), anio: int = Query(default=0)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')

    hoy = date.today()
    if not mes:  mes  = hoy.month
    if not anio: anio = hoy.year

    cobradores = db.query(models.Cobrador).order_by(models.Cobrador.nombre).all()

    resumen = []
    for co in cobradores:
        base_filter = [
            models.Boleta.cobrador_id == co.id,
            models.Boleta.condicion != CondicionBoleta.BAJA,
        ]
        total = (db.query(func.count(models.Boleta.id))
                 .filter(*base_filter)
                 .scalar() or 0)
        boletas_pendientes = (db.query(models.Boleta)
                              .filter(*base_filter,
                                      models.Boleta.cuotas_pagadas < models.Boleta.cuotas_pactadas)
                              .all())
        pendientes = sum(_pata_valor(b) for b in boletas_pendientes)
        planillas = (db.query(models.Planilla)
                     .filter_by(cobrador_id=co.id, mes=mes, anio=anio)
                     .order_by(models.Planilla.numero)
                     .all())
        resumen.append({"cobrador": co, "total": total, "pendientes": pendientes, "planillas": planillas})

    # ── TODAS las planillas, agrupadas por período (mes/año) ──────────────────
    # Independiente del selector de período: el objetivo es ver el histórico
    # completo de planillas distinguiendo a qué mes y año pertenece cada una.
    cob_map = {c.id: c.nombre for c in cobradores}
    todas_planillas = (db.query(models.Planilla)
                       .order_by(models.Planilla.anio.desc(),
                                 models.Planilla.mes.desc(),
                                 models.Planilla.cobrador_id,
                                 models.Planilla.numero)
                       .all())
    periodos_dict = {}
    for pl in todas_planillas:
        grupo = periodos_dict.setdefault((pl.anio, pl.mes), {})
        grupo.setdefault(pl.cobrador_id, []).append(pl)

    periodos_todos = []
    for (a, m) in sorted(periodos_dict.keys(), reverse=True):
        cobradores_periodo = [
            {"cobrador_nombre": cob_map.get(cid, "—"), "planillas": pls}
            for cid, pls in periodos_dict[(a, m)].items()
        ]
        cobradores_periodo.sort(key=lambda x: x["cobrador_nombre"])
        periodos_todos.append({
            "anio": a,
            "mes": m,
            "mes_nombre": MESES[m - 1] if 1 <= m <= 12 else str(m),
            "total_planillas": sum(len(c["planillas"]) for c in cobradores_periodo),
            "cobradores": cobradores_periodo,
        })

    return templates.TemplateResponse(request, "cobranza.html", {
        "user": user,
        "resumen": resumen,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
        "meses": MESES,
        "anios": list(range(hoy.year - 1, hoy.year + 2)),
        "periodos_todos": periodos_todos,
    })


@router.get("/{cobrador_id}/planilla/armar", response_class=HTMLResponse)
async def armar_planilla_form(request: Request, cobrador_id: int,
                              mes: int = Query(default=0), anio: int = Query(default=0),
                              db: Session = Depends(get_db)):
    """Pantalla de armado de planilla nueva.

    Muestra TODAS las boletas activas sin planilla del cobrador con checkboxes
    (todas marcadas por defecto). Sergio puede desmarcar las que no van a esta
    planilla — quedan pendientes para armar una segunda planilla luego.

    Cada fila muestra el badge de PATA (X0, X1, X2, ...) con el color de la
    talonera para identificar visualmente a qué grupo pertenece cada número.
    """
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')

    cobrador = db.query(models.Cobrador).get(cobrador_id)
    if not cobrador:
        return RedirectResponse("/cobranza/emplanillado", status_code=302)

    hoy = date.today()
    if not mes:  mes  = hoy.month
    if not anio: anio = hoy.year

    # Mismas reglas de filtrado que el POST: boletas activas sin planilla,
    # excluyendo al contado y totalmente pagas.
    disponibles = (db.query(models.Boleta)
                   .filter(models.Boleta.cobrador_id == cobrador_id,
                           models.Boleta.planilla_id.is_(None),
                           models.Boleta.condicion != CondicionBoleta.BAJA,
                           models.Boleta.numero_especial_2.is_(None),
                           models.Boleta.cuotas_pagadas < models.Boleta.cuotas_pactadas)
                   .join(models.Comprador, isouter=True)
                   .order_by(models.Boleta.numero_principal)
                   .all())

    # Info de PATA + color por boleta (para el badge X0/X1/X2 en cada fila)
    pata_info = {}
    for b in disponibles:
        if b.talonera:
            pata = _extraer_pata(b.talonera.nombre)
            color = b.talonera.color.strip() if b.talonera.color else "#cccccc"
            if not color or color == "#ffffff":
                color = "#cccccc"
        else:
            pata, color = "?", "#cccccc"
        pata_info[b.id] = {"pata": pata, "color": color}

    planillas_existentes = (db.query(func.count(models.Planilla.id))
                            .filter_by(cobrador_id=cobrador_id)
                            .scalar() or 0)

    return templates.TemplateResponse(request, "cobranza_planilla_armar.html", {
        "user": user,
        "cobrador": cobrador,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
        "disponibles": disponibles,
        "pata_info": pata_info,
        "siguiente_numero": planillas_existentes + 1,
    })


@router.post("/{cobrador_id}/planilla/armar")
async def armar_planilla(request: Request, cobrador_id: int,
                         db: Session = Depends(get_db)):
    """Crea una nueva planilla y le asigna las boletas seleccionadas.

    Si el form trae `boleta_ids` (lista de IDs), solo asigna esas. Si NO trae
    (compat con el flujo viejo del modal), asigna TODAS las pendientes del
    cobrador. Esto permite armar varias planillas: en cada armado se elige el
    subconjunto y el resto queda pendiente para otra planilla.
    """
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'cobranza', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')

    cobrador = db.query(models.Cobrador).get(cobrador_id)
    if not cobrador:
        return RedirectResponse("/cobranza/emplanillado", status_code=302)

    form_data = await request.form()
    try:
        mes = int(form_data.get("mes") or date.today().month)
        anio = int(form_data.get("anio") or date.today().year)
    except (TypeError, ValueError):
        mes, anio = date.today().month, date.today().year
    try:
        comision_pct = float(form_data.get("comision_pct") or 10.0)
    except (TypeError, ValueError):
        comision_pct = 10.0

    ids_raw = form_data.getlist("boleta_ids")
    ids_seleccionados = set()
    for x in ids_raw:
        try:
            ids_seleccionados.add(int(x))
        except (TypeError, ValueError):
            continue

    base_q = (db.query(models.Boleta)
              .filter(models.Boleta.cobrador_id == cobrador_id,
                      models.Boleta.planilla_id.is_(None),
                      models.Boleta.condicion != CondicionBoleta.BAJA,
                      models.Boleta.numero_especial_2.is_(None),
                      models.Boleta.cuotas_pagadas < models.Boleta.cuotas_pactadas))

    pendientes_count = base_q.count()
    if pendientes_count == 0:
        return RedirectResponse(f"/cobranza/emplanillado?mes={mes}&anio={anio}", status_code=302)

    if ids_raw:
        if not ids_seleccionados:
            return RedirectResponse(
                f"/cobranza/{cobrador_id}/planilla/armar?mes={mes}&anio={anio}",
                status_code=302)
        boletas_q = base_q.filter(models.Boleta.id.in_(ids_seleccionados))
    else:
        boletas_q = base_q

    siguiente_numero = (db.query(func.count(models.Planilla.id))
                        .filter_by(cobrador_id=cobrador_id)
                        .scalar() or 0) + 1
    planilla = models.Planilla(
        cobrador_id=cobrador_id,
        numero=siguiente_numero,
        mes=mes,
        anio=anio,
        comision_pct=comision_pct,
    )
    db.add(planilla)
    db.flush()

    boletas_q.update({"planilla_id": planilla.id}, synchronize_session=False)
    db.commit()

    return RedirectResponse(f"/cobranza/emplanillado?mes={mes}&anio={anio}", status_code=302)


# ── EMPLANILLADO ───────────────────────────────────────────────────────────────

@router.get("/emplanillado", response_class=HTMLResponse)
async def emplanillado(request: Request, db: Session = Depends(get_db),
                        mes: int = Query(default=0), anio: int = Query(default=0)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    hoy = date.today()
    if not mes:  mes  = hoy.month
    if not anio: anio = hoy.year

    cobradores = db.query(models.Cobrador).filter_by(activo=True).order_by(models.Cobrador.nombre).all()
    resumen = []
    for co in cobradores:
        # Boletas activas de este cobrador (con cuotas para cobrar).
        # Excluye las pagadas al contado (numero_especial_2 IS NOT NULL): tanto
        # las "al contado" (1 pago, ambos slots) como las "contado 2 veces" (solo slot 2)
        # ya no tienen cuotas pendientes, no corresponde mostrarlas en el emplantillado.
        # Excluye también las que tienen TODAS las cuotas pagas (cuotas_pagadas >=
        # cuotas_pactadas) — pueden ser boletas "en cuotas" con cuotas_anticipadas
        # = num_cuotas, que en Socios figuran como "Al contado".
        activas = (db.query(models.Boleta)
                   .filter(models.Boleta.cobrador_id == co.id,
                           models.Boleta.condicion != CondicionBoleta.BAJA,
                           models.Boleta.numero_especial_2.is_(None),
                           models.Boleta.cuotas_pagadas < models.Boleta.cuotas_pactadas)
                   .all())
        sin_planilla = [b for b in activas if b.planilla_id is None]
        planillas = (db.query(models.Planilla)
                     .filter_by(cobrador_id=co.id, mes=mes, anio=anio)
                     .order_by(models.Planilla.numero)
                     .all())
        if activas:
            resumen.append({
                "cobrador": co,
                "total_activas": len(activas),
                "sin_planilla": len(sin_planilla),
                "planillas": planillas,
            })

    return templates.TemplateResponse(request, "cobranza_emplanillado.html", {
        "user": user,
        "resumen": resumen,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
        "meses": MESES,
        "anios": list(range(hoy.year - 1, hoy.year + 2)),
    })


# ── EDITAR PLANILLA ────────────────────────────────────────────────────────────

@router.get("/planilla/{planilla_id}/editar", response_class=HTMLResponse)
async def planilla_editar_form(request: Request, planilla_id: int,
                               db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    planilla = db.query(models.Planilla).get(planilla_id)
    if not planilla:
        raise HTTPException(404)

    # Limpieza silenciosa: si en esta planilla hay boletas pagadas al contado
    # (numero_especial_2 IS NOT NULL → no tienen cuotas para cobrar) o con
    # TODAS las cuotas pagas (cuotas_pagadas >= cuotas_pactadas, p.ej. boletas
    # "en cuotas" con cuotas_anticipadas = num_cuotas), las descontamos de la
    # planilla antes de mostrarla. Evita que queden huérfanas por data antigua
    # o por una baja anterior, o que el cobrador las vea con las 12 cuotas en X.
    limpiadas = (db.query(models.Boleta)
                 .filter(models.Boleta.planilla_id == planilla_id,
                         or_(models.Boleta.numero_especial_2.isnot(None),
                             models.Boleta.cuotas_pagadas >= models.Boleta.cuotas_pactadas))
                 .update({"planilla_id": None}, synchronize_session=False))
    if limpiadas:
        db.commit()

    # Boletas ya en esta planilla (después de la limpieza, todas tienen cuotas)
    en_planilla = (db.query(models.Boleta)
                   .filter(models.Boleta.planilla_id == planilla_id)
                   .join(models.Comprador, isouter=True)
                   .order_by(models.Boleta.numero_principal)
                   .all())

    # Boletas activas del mismo cobrador que NO están en esta planilla
    # (incluye las sin planilla y las de otras planillas del mismo cobrador).
    # Excluye las pagadas al contado: no tienen cuotas para cobrar, no se
    # emplantillan. Excluye también las totalmente pagas en cuotas
    # (cuotas_pagadas >= cuotas_pactadas → badge "Al contado" en Socios).
    disponibles = (db.query(models.Boleta)
                   .filter(models.Boleta.cobrador_id == planilla.cobrador_id,
                           or_(models.Boleta.planilla_id.is_(None),
                               models.Boleta.planilla_id != planilla_id),
                           models.Boleta.condicion != CondicionBoleta.BAJA,
                           models.Boleta.numero_especial_2.is_(None),
                           models.Boleta.cuotas_pagadas < models.Boleta.cuotas_pactadas)
                   .join(models.Comprador, isouter=True)
                   .order_by(models.Boleta.numero_principal)
                   .all())

    # Info de PATA + color por boleta (para el badge X0/X1/X2 en cada fila)
    pata_info = {}
    for b in list(en_planilla) + list(disponibles):
        if b.talonera:
            pata = _extraer_pata(b.talonera.nombre)
            color = b.talonera.color.strip() if b.talonera.color else "#cccccc"
            if not color or color == "#ffffff":
                color = "#cccccc"
        else:
            pata, color = "?", "#cccccc"
        pata_info[b.id] = {"pata": pata, "color": color}

    return templates.TemplateResponse(request, "cobranza_planilla_editar.html", {
        "user": user,
        "planilla": planilla,
        "en_planilla": en_planilla,
        "disponibles": disponibles,
        "pata_info": pata_info,
        "mes_nombre": MESES[planilla.mes - 1],
    })


@router.post("/planilla/{planilla_id}/editar")
async def planilla_editar_guardar(request: Request, planilla_id: int,
                                  db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'cobranza', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    planilla = db.query(models.Planilla).get(planilla_id)
    if not planilla:
        raise HTTPException(404)

    form_data = await request.form()
    ids_seleccionados = set(int(x) for x in form_data.getlist("boleta_ids"))

    # Quitar de planilla las que ya no están seleccionadas
    (db.query(models.Boleta)
       .filter(models.Boleta.planilla_id == planilla_id,
               models.Boleta.id.notin_(ids_seleccionados))
       .update({"planilla_id": None}, synchronize_session=False))

    # Agregar a la planilla las nuevas seleccionadas. Filtra defensivamente
    # las pagadas al contado y las totalmente pagas: aunque la UI no las
    # muestra, nunca asignar una boleta sin cuotas a una planilla de cobranza.
    if ids_seleccionados:
        (db.query(models.Boleta)
           .filter(models.Boleta.id.in_(ids_seleccionados),
                   models.Boleta.cobrador_id == planilla.cobrador_id,
                   models.Boleta.numero_especial_2.is_(None),
                   models.Boleta.cuotas_pagadas < models.Boleta.cuotas_pactadas)
           .update({"planilla_id": planilla_id}, synchronize_session=False))

    db.commit()
    return RedirectResponse(
        f"/cobranza/emplanillado?mes={planilla.mes}&anio={planilla.anio}",
        status_code=302
    )


@router.post("/planilla/{planilla_id}/eliminar")
async def planilla_eliminar(request: Request, planilla_id: int,
                            db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'cobranza', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    planilla = db.query(models.Planilla).get(planilla_id)
    if not planilla:
        raise HTTPException(404)

    mes, anio = planilla.mes, planilla.anio

    # Desvincular todas las boletas (quedan listas para re-emplanillar)
    (db.query(models.Boleta)
       .filter(models.Boleta.planilla_id == planilla_id)
       .update({"planilla_id": None}, synchronize_session=False))

    # Orphanar la liquidación si existe (se conserva el historial y cuotas_pagadas en cada boleta)
    liq = planilla.liquidacion
    if liq:
        liq.planilla_id = None
        db.flush()

    db.delete(planilla)
    db.commit()
    return RedirectResponse(
        f"/cobranza/emplanillado?mes={mes}&anio={anio}",
        status_code=302
    )


# ── LIQUIDACIÓN ────────────────────────────────────────────────────────────────

@router.get("/liquidacion", response_class=HTMLResponse)
async def liquidacion_index(request: Request, db: Session = Depends(get_db),
                             mes: int = Query(default=0), anio: int = Query(default=0)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    hoy = date.today()
    if not mes:  mes  = hoy.month
    if not anio: anio = hoy.year

    planillas_todas = (db.query(models.Planilla)
                       .filter_by(mes=mes, anio=anio)
                       .join(models.Cobrador)
                       .order_by(models.Cobrador.nombre, models.Planilla.numero)
                       .all())

    # Agrupar por cobrador
    resumen_liq = {}
    for p in planillas_todas:
        cid = p.cobrador_id
        if cid not in resumen_liq:
            resumen_liq[cid] = {"cobrador": p.cobrador, "planillas": []}
        resumen_liq[cid]["planillas"].append(p)
    resumen_liq = list(resumen_liq.values())

    return templates.TemplateResponse(request, "cobranza_liquidacion.html", {
        "user": user,
        "resumen": resumen_liq,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
        "meses": MESES,
        "anios": list(range(hoy.year - 1, hoy.year + 2)),
    })


@router.get("/liquidacion/{planilla_id}", response_class=HTMLResponse)
async def liquidacion_detalle(request: Request, planilla_id: int,
                               db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    planilla = db.query(models.Planilla).get(planilla_id)
    if not planilla:
        raise HTTPException(404)

    boletas = (db.query(models.Boleta)
               .filter(models.Boleta.planilla_id == planilla_id)
               .join(models.Comprador, isouter=True)
               .order_by(models.Boleta.numero_principal)
               .all())

    liq = planilla.liquidacion

    # Construir historial y selección actual por boleta
    historial_map = {}
    cuotas_mes_actual = {}
    for b in boletas:
        h = json.loads(b.historial_cuotas) if b.historial_cuotas else {}
        historial_map[b.id] = h
        cuotas_mes_actual[b.id] = [int(k) for k, v in h.items() if v == planilla.mes]

    # ── Mismo grid 3 columnas que la planilla ──────────────────────────────
    _grid = _armar_grid_patas(boletas)
    c1, c2, c3 = _grid["cols"]
    rows = _grid["rows"]
    col1_label, col2_label, col3_label = _grid["labels"]
    col1_color, col2_color, col3_color = _grid["colors"]

    num_cuotas = max((b.cuotas_pactadas or 0) for b in boletas) if boletas else 10
    num_cuotas = max(num_cuotas, 10)
    cuota_nums = list(range(1, num_cuotas + 1))

    # ── Mapa boleta_id → columna (1, 2 o 3) según el grid FÍSICO ────────────
    # COL. 1/2/3 del resumen = las 3 secciones verticales de la planilla.
    # El cobrador suma por columna física al recorrer la planilla.
    columna_de_boleta = {}
    for cell in c1:
        if cell and not isinstance(cell, dict):
            columna_de_boleta[cell.id] = 1
    for cell in c2:
        if cell and not isinstance(cell, dict):
            columna_de_boleta[cell.id] = 2
    for cell in c3:
        if cell and not isinstance(cell, dict):
            columna_de_boleta[cell.id] = 3

    # ── Mapa boleta_id → multiplicador (PATA 1 ×1, PATA 2 ×2, PATA 3 ×3, …) ──
    # Cada cuota cobrada SUMA según el número de la PATA de la boleta:
    # una cuota de PATA 3 cuenta como 3 unidades, una de PATA 2 como 2, etc.
    multiplicador_de_boleta = {}
    for b in boletas:
        p = _get_pata_boleta(b)
        try:
            multiplicador_de_boleta[b.id] = max(1, int(p))
        except (ValueError, TypeError):
            multiplicador_de_boleta[b.id] = 1

    # ── Valor de la cuota (uniforme) ────────────────────────────────────────
    valor_cuota = 0.0
    for b in boletas:
        if b.talonera and b.talonera.valor_cuota:
            valor_cuota = float(b.talonera.valor_cuota)
            break

    # ── Meses de la campaña (cuota 1 = mes siguiente al mes de la planilla) ──
    meses_campana = _meses_campana_desde(planilla.mes, num_cuotas)

    # ── Resumen inicial por MES CALENDARIO en que se cobró y columna ────────
    # Cada fila del resumen representa el mes (calendario) en que se cobraron
    # las cuotas. El mes actual lo recalcula el JS desde la selección.
    # resumen_otros[mes_calendario] = {1: count, 2: count, 3: count} — solo
    # cuotas cobradas en meses DISTINTOS al de la planilla.
    resumen_otros = {m: {1: 0, 2: 0, 3: 0} for m in range(1, 13)}
    for b in boletas:
        col = columna_de_boleta.get(b.id)
        if not col:
            continue
        mult = multiplicador_de_boleta.get(b.id, 1)
        for k, v in historial_map[b.id].items():
            try:
                mes_pago = int(v)
            except (TypeError, ValueError):
                continue
            if mes_pago == planilla.mes:
                continue  # el JS lo maneja en la fila del mes actual
            if 1 <= mes_pago <= 12:
                resumen_otros[mes_pago][col] += mult

    # ── Info por boleta para validación secuencial en JS ────────────────────
    boletas_info = {
        b.id: {
            "anticipadas": int(b.cuotas_anticipadas or 0),
            "pactadas": int(b.cuotas_pactadas or 0),
            "historial": {int(k): int(v) for k, v in historial_map[b.id].items()
                          if str(k).isdigit() and str(v).lstrip('-').isdigit()},
        }
        for b in boletas
    }

    return templates.TemplateResponse(request, "cobranza_liquidacion_detalle.html", {
        "user": user,
        "planilla": planilla,
        "boletas": boletas,
        "rows": rows,
        "col1_label": col1_label,
        "col2_label": col2_label,
        "col3_label": col3_label,
        "col1_color": col1_color,
        "col2_color": col2_color,
        "col3_color": col3_color,
        "historial_map": historial_map,
        "cuotas_mes_actual": cuotas_mes_actual,
        "liquidacion": liq,
        "mes_nombre": MESES[planilla.mes - 1],
        "mes_actual": planilla.mes,
        "cuota_nums": cuota_nums,
        "num_cuotas": num_cuotas,
        "columna_de_boleta": columna_de_boleta,
        "multiplicador_de_boleta": multiplicador_de_boleta,
        "valor_cuota": valor_cuota,
        "meses_campana": meses_campana,
        "resumen_otros": resumen_otros,
        "boletas_info": boletas_info,
    })


@router.post("/liquidacion/{planilla_id}/guardar")
async def liquidacion_guardar(request: Request, planilla_id: int,
                               boleta_ids: List[int] = Form(...),
                               cuotas_json: List[str] = Form(...),
                               db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'cobranza', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    planilla = db.query(models.Planilla).get(planilla_id)
    if not planilla:
        raise HTTPException(404)

    # Crear o actualizar liquidación
    liq = planilla.liquidacion
    if not liq:
        liq = models.Liquidacion(planilla_id=planilla_id, fecha=date.today())
        db.add(liq)
        db.flush()

    # Eliminar detalles anteriores y recrear
    db.query(models.LiquidacionDetalle).filter_by(liquidacion_id=liq.id).delete()

    total_cuotas = 0
    monto_total  = 0.0
    for bid, cjson in zip(boleta_ids, cuotas_json):
        cuotas_nuevas = json.loads(cjson)   # lista de enteros, ej: [2, 3, 4]
        boleta = db.query(models.Boleta).get(bid)
        if not boleta:
            continue

        # Actualizar historial_cuotas: quitar entradas del mes actual y reemplazar
        historial = json.loads(boleta.historial_cuotas) if boleta.historial_cuotas else {}
        historial = {k: v for k, v in historial.items() if v != planilla.mes}
        for cn in cuotas_nuevas:
            historial[str(cn)] = planilla.mes
        boleta.historial_cuotas = json.dumps(historial)

        # cuotas_pagadas = anticipadas + todas las del historial
        boleta.cuotas_pagadas = min(
            boleta.cuotas_anticipadas + len(historial),
            boleta.cuotas_pactadas
        )

        # Recalcular condicion después de actualizar cuotas_pagadas:
        #   - Si quedó toda paga (o es contado, o no tiene cobrador) → VENDIDO
        #   - Si todavía hay cuotas pendientes con cobrador y no contado → EN_COBRANZA
        # No tocamos BAJA / SIN_VENDER / CAJA.
        from ..models import CondicionBoleta as _CB
        _es_contado = (boleta.numero_especial is not None) or (boleta.numero_especial_2 is not None)
        _cuotas_pendientes = (boleta.cuotas_pagadas or 0) < (boleta.cuotas_pactadas or 0)
        if boleta.condicion not in (_CB.BAJA, _CB.SIN_VENDER, _CB.CAJA):
            if boleta.cobrador_id and _cuotas_pendientes and not _es_contado:
                boleta.condicion = _CB.EN_COBRANZA
            else:
                boleta.condicion = _CB.VENDIDO

        cobradas = len(cuotas_nuevas)
        if cobradas > 0:
            db.add(models.LiquidacionDetalle(
                liquidacion_id=liq.id,
                boleta_id=bid,
                cuotas_cobradas=cobradas,
            ))
        total_cuotas += cobradas

        # Aporte al monto total: cuotas cobradas × valor_cuota de la talonera
        valor = (boleta.talonera.valor_cuota
                 if boleta.talonera and boleta.talonera.valor_cuota else 0.0) or 0.0
        monto_total += cobradas * float(valor)

    comision_pct = float(planilla.comision_pct or 0.0)
    comision = round(monto_total * (comision_pct / 100.0), 2)
    neto     = round(monto_total - comision, 2)

    liq.total_cuotas = total_cuotas
    liq.monto_total  = round(monto_total, 2)
    liq.comision     = comision
    liq.neto         = neto
    liq.fecha = date.today()
    db.commit()

    return RedirectResponse(f"/cobranza/liquidacion/{planilla_id}", status_code=302)


@router.get("/planilla/{planilla_id}/ver", response_class=HTMLResponse)
async def planilla_ver(request: Request, planilla_id: int,
                       db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')

    planilla_obj = db.query(models.Planilla).get(planilla_id)
    if not planilla_obj:
        raise HTTPException(404)

    cobrador = db.query(models.Cobrador).get(planilla_obj.cobrador_id)
    mes  = planilla_obj.mes
    anio = planilla_obj.anio

    boletas = (db.query(models.Boleta)
               .filter(models.Boleta.planilla_id == planilla_id)
               .join(models.Comprador, isouter=True)
               .order_by(models.Boleta.numero_principal)
               .all())

    _grid = _armar_grid_patas(boletas)
    rows = _grid["rows"]
    col1_label, col2_label, col3_label = _grid["labels"]
    col1_color, col2_color, col3_color = _grid["colors"]

    num_cuotas = max((b.cuotas_pactadas or 0) for b in boletas) if boletas else 10
    num_cuotas = max(num_cuotas, 10)
    cuota_nums = list(range(1, num_cuotas + 1))
    meses_campana = _meses_campana_desde(mes, num_cuotas)

    return templates.TemplateResponse(request, "cobranza_planilla.html", {
        "user": user,
        "cobrador": cobrador,
        "planilla": planilla_obj,
        "planilla_label": f"P{planilla_obj.numero}",
        "boletas": boletas,
        "rows": rows,
        "col1_label": col1_label,
        "col2_label": col2_label,
        "col3_label": col3_label,
        "col1_color": col1_color,
        "col2_color": col2_color,
        "col3_color": col3_color,
        "num_cuotas": num_cuotas,
        "cuota_nums": cuota_nums,
        "meses_campana": meses_campana,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
    })


@router.get("/{cobrador_id}/planilla", response_class=HTMLResponse)
async def planilla(request: Request, cobrador_id: int,
                   mes: int = Query(default=0), anio: int = Query(default=0),
                   db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')

    hoy = date.today()
    if not mes:  mes  = hoy.month
    if not anio: anio = hoy.year

    cobrador = db.query(models.Cobrador).get(cobrador_id)
    if not cobrador:
        return RedirectResponse("/cobranza/", status_code=302)

    planilla_obj = db.query(models.Planilla).filter_by(cobrador_id=cobrador_id, mes=mes, anio=anio).first()

    if planilla_obj:
        # Mostrar las boletas fijas de esta planilla
        boletas = (db.query(models.Boleta)
                   .filter(models.Boleta.planilla_id == planilla_obj.id)
                   .join(models.Comprador, isouter=True)
                   .order_by(models.Boleta.numero_principal)
                   .all())
    else:
        # Planilla no armada aún: vista previa con boletas actuales sin planilla
        boletas = (db.query(models.Boleta)
                   .filter(models.Boleta.cobrador_id == cobrador_id,
                           models.Boleta.planilla_id.is_(None),
                           models.Boleta.condicion != CondicionBoleta.BAJA)
                   .join(models.Comprador, isouter=True)
                   .order_by(models.Boleta.numero_principal)
                   .all())

    # ── Grid 3 columnas agrupado por PATA (helper compartido) ──
    _grid = _armar_grid_patas(boletas)
    rows = _grid["rows"]
    col1_label, col2_label, col3_label = _grid["labels"]
    col1_color, col2_color, col3_color = _grid["colors"]

    # Cantidad de cuotas (máximo entre todas las boletas, mínimo 10)
    num_cuotas = max((b.cuotas_pactadas or 0) for b in boletas) if boletas else 10
    num_cuotas = max(num_cuotas, 10)
    cuota_nums = list(range(1, num_cuotas + 1))

    # Meses de la campaña (cuota 1 = mes siguiente al mes de la planilla)
    meses_campana = _meses_campana_desde(mes, num_cuotas)

    planilla_label = f"P{planilla_obj.numero}" if planilla_obj else None

    return templates.TemplateResponse(request, "cobranza_planilla.html", {
        "user": user,
        "cobrador": cobrador,
        "planilla": planilla_obj,
        "planilla_label": planilla_label,
        "boletas": boletas,
        "rows": rows,
        "col1_label": col1_label,
        "col2_label": col2_label,
        "col3_label": col3_label,
        "col1_color": col1_color,
        "col2_color": col2_color,
        "col3_color": col3_color,
        "num_cuotas": num_cuotas,
        "cuota_nums": cuota_nums,
        "meses_campana": meses_campana,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
    })
