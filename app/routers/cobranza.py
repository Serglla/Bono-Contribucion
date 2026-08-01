from fastapi import APIRouter, Depends, Request, Query, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from datetime import date
from io import BytesIO
from typing import List, Optional
import json
import re
from xhtml2pdf import pisa
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
# Fecha argentina + periodos anio-mes del historial (auditoria A-2 / C-1):
# NUNCA usar date.today() aca - el server corre en UTC y de noche corre el mes.
from ..tiempo import (hoy_ar, periodo_actual, periodo_str, parse_periodo,
                      mes_de, match_periodo)

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


def _pata_valor(boleta) -> float:
    """Ponderación por PATA = multiplicador de la talonera
    (PATA 0=0.67, 1=1, 2=2, 3=3, 4=4, 6=6...).
    OJO: antes parseaba el nombre buscando los dígitos 1/2/3, por lo que PATA 0, 4,
    5, 6, 8 devolvían 1 (mal contabilizado). Ahora usa el multiplicador real."""
    if boleta.talonera and boleta.talonera.multiplicador:
        return float(boleta.talonera.multiplicador)
    return 1.0


def _planilla_todo_pata0(boletas) -> bool:
    """True si TODAS las boletas (con talonera) de la planilla son PATA 0
    (multiplicador < 1). En ese caso las cuotas se cuentan ×1 y valen el importe
    uniforme real ($10.000), sin aplicar el 0.67 (no hay PATA 1 con que comparar)."""
    bs = [b for b in boletas if b.talonera]
    return bool(bs) and all((b.talonera.multiplicador or 1.0) < 1.0 for b in bs)


def _columna_mult_valor(boletas, grid):
    """A partir del grid de 3 columnas físicas, devuelve:
      - columna_de_boleta: {boleta_id: 1|2|3} según en qué columna física cayó.
      - multiplicador_de_boleta: {boleta_id: ponderación por PATA} (ver _pata_valor).
      - valor_cuota: importe base de una cuota (uniforme si la planilla es
        TODA PATA 0, sino el valor base PATA 1).
    Compartido entre la vista de liquidación (interactiva) y el preview de
    solo lectura de la planilla, para que ambos calculen exactamente lo mismo."""
    c1, c2, c3 = grid["cols"]
    columna_de_boleta = {}
    for col_num, col in ((1, c1), (2, c2), (3, c3)):
        for cell in col:
            if cell and not isinstance(cell, dict):
                columna_de_boleta[cell.id] = col_num

    todo_pata0 = _planilla_todo_pata0(boletas)
    multiplicador_de_boleta = {b.id: (1.0 if todo_pata0 else _pata_valor(b)) for b in boletas}

    valor_cuota = 0.0
    for b in boletas:
        if b.talonera and b.talonera.valor_cuota:
            vc = float(b.talonera.valor_cuota)
            if todo_pata0:
                valor_cuota = vc
            else:
                m = float(b.talonera.multiplicador or 1.0) or 1.0
                valor_cuota = round(vc / m)
            break
    return columna_de_boleta, multiplicador_de_boleta, valor_cuota


def _resumen_mensual_rows(boletas, grid, meses_campana, comision_pct, paso_map=None):
    """Arma las filas de la tabla 'MES / COL1 / COL2 / COL3 / TOTAL / DINERO /
    COMISIÓN / NETO' con datos REALES (todo lo que ya está en historial_cuotas),
    pensadas para el preview de SOLO LECTURA de la planilla (a diferencia de la
    grilla interactiva de liquidación, acá se incluye también el mes actual,
    porque no hay nada más que seguir marcando: es una foto de lo ya cobrado).
    Devuelve (rows, totales, valor_cuota)."""
    columna_de_boleta, multiplicador_de_boleta, valor_cuota = _columna_mult_valor(boletas, grid)

    counts = {m: {1: 0.0, 2: 0.0, 3: 0.0} for m in range(1, 13)}
    for b in boletas:
        col = columna_de_boleta.get(b.id)
        if not col:
            continue
        mult = multiplicador_de_boleta.get(b.id, 1.0)
        try:
            hist = json.loads(b.historial_cuotas) if b.historial_cuotas else {}
        except (ValueError, TypeError):
            hist = {}
        # Boleta que PASÓ a otro cobrador/planilla: se sigue mostrando en esta
        # planilla, pero al resumen solo aportan las cuotas que se cobraron ACÁ
        # (hasta paso_cuota). Las posteriores las cobra el destino y contarlas
        # sería duplicarlas entre las dos planillas.
        _corte = None
        if paso_map and b.id in paso_map:
            _corte = int(paso_map[b.id].get("cuota") or 0)
        for k, v in hist.items():
            if _corte is not None:
                try:
                    if int(k) > _corte:
                        continue
                except (TypeError, ValueError):
                    continue
            m = mes_de(v)
            if m and 1 <= m <= 12:
                counts[m][col] += mult

    rows = []
    tot = {"c1": 0.0, "c2": 0.0, "c3": 0.0, "tot": 0.0, "dinero": 0.0, "comision": 0.0, "neto": 0.0}
    for nombre_mes, num_mes in meses_campana:
        c1 = counts[num_mes][1]
        c2 = counts[num_mes][2]
        c3 = counts[num_mes][3]
        t = c1 + c2 + c3
        dinero = t * valor_cuota
        comision = round(dinero * (comision_pct / 100.0), 2)
        neto = round(dinero - comision, 2)
        rows.append({
            "nombre": nombre_mes, "num": num_mes,
            "c1": c1, "c2": c2, "c3": c3, "tot": t,
            "dinero": round(dinero, 2), "comision": comision, "neto": neto,
        })
        tot["c1"] += c1; tot["c2"] += c2; tot["c3"] += c3; tot["tot"] += t
        tot["dinero"] += dinero; tot["comision"] += comision; tot["neto"] += neto
    for k in ("dinero", "comision", "neto"):
        tot[k] = round(tot[k], 2)
    return rows, tot, valor_cuota


def _resolver_periodo_liq(mes: int = 0, anio: int = 0):
    """Resuelve el período (año, mes) que se va a liquidar a partir de lo que
    pidió el usuario, con dos reglas duras:

      - Por defecto se liquida el mes calendario ACTUAL (hora argentina).
      - NUNCA se puede liquidar un período FUTURO. Si piden uno (por URL
        manipulada o por un selector desactualizado), se cae al actual y se
        avisa con la bandera `ajustado`.

    Un período PASADO sí se permite: es la corrección/edición de una cobranza
    que ya se liquidó (o que quedó sin liquidar) en ese mes.

    Devuelve (anio, mes, es_pasado, ajustado)."""
    hoy = hoy_ar()
    if not mes:  mes = hoy.month
    if not anio: anio = hoy.year
    try:
        mes, anio = int(mes), int(anio)
    except (TypeError, ValueError):
        return hoy.year, hoy.month, False, True
    if not (1 <= mes <= 12):
        return hoy.year, hoy.month, False, True
    # Comparación por período (año, mes): cualquier cosa posterior al mes en
    # curso es futuro y se rechaza.
    if (anio, mes) > (hoy.year, hoy.month):
        return hoy.year, hoy.month, False, True
    es_pasado = (anio, mes) < (hoy.year, hoy.month)
    return anio, mes, es_pasado, False


def _periodos_liquidables(planilla):
    """Períodos que se pueden elegir para liquidar una planilla: desde el mes
    en que se entregó la planilla hasta el mes ACTUAL inclusive (nunca hacia
    adelante). Alimenta el selector de mes de la pantalla de liquidación."""
    hoy = hoy_ar()
    ini_anio = int(planilla.anio or hoy.year)
    ini_mes = int(planilla.mes or hoy.month)
    if (ini_anio, ini_mes) > (hoy.year, hoy.month):
        ini_anio, ini_mes = hoy.year, hoy.month
    periodos = []
    a, m = ini_anio, ini_mes
    # Tope de seguridad: 60 meses, por si una planilla tiene un año raro
    # cargado y el bucle se iría a las nubes.
    for _ in range(60):
        periodos.append({
            "anio": a, "mes": m,
            "label": f"{MESES[m - 1]} {a}",
            "es_pasado": (a, m) < (hoy.year, hoy.month),
        })
        if (a, m) >= (hoy.year, hoy.month):
            break
        m += 1
        if m > 12:
            m, a = 1, a + 1
    return periodos


def _liquidacion_periodo_stats(planilla, anio_liq: int, mes_liq: int):
    """Cuántas cuotas de esta planilla ya están cargadas en el período que se
    está liquidando. Sirve para avisar 'esto ya se liquidó, lo estás editando'
    cuando se elige un mes pasado (o uno actual ya cargado)."""
    cuotas = 0
    boletas_con_cuotas = 0
    for b in planilla.boletas:
        try:
            hist = json.loads(b.historial_cuotas) if b.historial_cuotas else {}
        except (ValueError, TypeError):
            hist = {}
        n = sum(1 for v in hist.values() if match_periodo(v, anio_liq, mes_liq))
        if n:
            cuotas += n
            boletas_con_cuotas += 1
    return {"cuotas": cuotas, "boletas": boletas_con_cuotas, "ya_liquidado": cuotas > 0}


def _planilla_tiene_pendientes(planilla, anio_liq: int, mes_liq: int) -> bool:
    """True si la planilla tiene al menos una boleta viva a la que le falten
    cuotas y que TODAVÍA no tenga ninguna cuota marcada en el período
    (anio_liq/mes_liq) que se está liquidando. Se usa para armar la cola de
    'Liquidar todas las planillas, una a una': una planilla sale de la cola
    en cuanto se guardó algo para ella en esta ronda (aunque no haya quedado
    100% al día — eso se puede seguir corrigiendo abriéndola de nuevo)."""
    for b in planilla.boletas:
        if b.condicion == CondicionBoleta.BAJA:
            continue
        pactadas = b.cuotas_pactadas or 0
        pagadas = b.cuotas_pagadas or 0
        if pactadas and pagadas >= pactadas:
            continue
        try:
            hist = json.loads(b.historial_cuotas) if b.historial_cuotas else {}
        except (ValueError, TypeError):
            hist = {}
        if not any(match_periodo(v, anio_liq, mes_liq) for v in hist.values()):
            return True
    return False


def _cola_liquidacion(db, anio_liq: int = None, mes_liq: int = None,
                      cobrador_id: int = None):
    """Cola de planillas a liquidar, ordenada por cobrador y número de planilla.

    La cadena de 'Guardar y seguir' es SIEMPRE por cobrador: se recorren las
    planillas de UN cobrador (P1, P2, P3...) y ahí termina la ronda — no salta
    al cobrador siguiente. Por eso `cobrador_id` es el uso normal; sin él
    devuelve todas (lo usa el conteo de pendientes de la pantalla principal).

    Cada item indica si todavía tiene pendientes en el período que se está
    liquidando (por defecto, el mes calendario de HOY)."""
    if anio_liq is None or mes_liq is None:
        _hoy = hoy_ar()
        anio_liq, mes_liq = _hoy.year, _hoy.month
    q = (db.query(models.Planilla)
         .join(models.Cobrador)
         .order_by(models.Cobrador.nombre, models.Planilla.anio,
                   models.Planilla.mes, models.Planilla.numero))
    if cobrador_id is not None:
        q = q.filter(models.Planilla.cobrador_id == cobrador_id)
    planillas = q.all()
    return [
        {
            "id": p.id,
            "cobrador_nombre": p.cobrador.nombre,
            "numero": p.numero,
            "mes": p.mes,
            "anio": p.anio,
            "pendiente": _planilla_tiene_pendientes(p, anio_liq, mes_liq),
        }
        for p in planillas
    ]


def _get_pata_boleta(b) -> str:
    """Dígito de PATA de una boleta ('0', '1', '2'...) o '?' si no tiene talonera."""
    if b and b.talonera:
        return _extraer_pata(b.talonera.nombre)
    return "?"


def _build_paso_map(boletas, planilla_id) -> dict:
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


def _hist_maps_display(boletas):
    """Mapas de historial para las vistas de planilla (formato display).

    Devuelve (historial_map, hist_act):
      - historial_map[bid] = {cuota_str: mes_int} - TODAS las cuotas cobradas,
        con el mes ya parseado (acepta "YYYY-MM" nuevo e int legacy).
      - hist_act[bid] = [cuotas cobradas en el PERIODO actual (anio+mes AR)] -
        para pintar "mes actual" sin confundir julio 2026 con julio 2027.
    """
    _hoy = hoy_ar()
    historial_map, hist_act = {}, {}
    for b in boletas:
        try:
            h = json.loads(b.historial_cuotas) if b.historial_cuotas else {}
        except (ValueError, TypeError):
            h = {}
        disp, act = {}, []
        for k, v in h.items():
            _m = mes_de(v)
            if not _m:
                continue
            disp[str(k)] = _m
            if match_periodo(v, _hoy.year, _hoy.month):
                try:
                    act.append(int(k))
                except (TypeError, ValueError):
                    pass
        historial_map[b.id] = disp
        hist_act[b.id] = act
    return historial_map, hist_act


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
            # PATA 0 no es proporcional al resto (valor uniforme $10.000, no se
            # pondera), así que NUNCA comparte columna con otra PATA: si este
            # grupo es PATA 0, o el grupo anterior lo era, se arranca al inicio
            # de la próxima columna FÍSICA (bloque de ROWS_PER_COL) en vez del
            # próximo bloque de 10. Así cada columna del resumen es de una sola
            # PATA y su importe por columna queda bien calculado.
            cur_is_p0  = _get_pata_boleta(grupo[0]) == "0"
            prev_is_p0 = _get_pata_boleta(pata_grupos[i - 1][0]) == "0"
            _col_block = pos if pos % ROWS_PER_COL == 0 else ((pos // ROWS_PER_COL) + 1) * ROWS_PER_COL
            # Solo se fuerza la columna nueva si el grupo ENTRA en una columna
            # fresca de esta hoja. Si no hay columna libre (planilla con muchas
            # PATAs), NO se fuerza: el grupo se apila normalmente (queda VISIBLE,
            # compartiendo columna) en vez de quedar empujado fuera de la hoja.
            # El caso ideal "columna aparte / otra hoja" requiere multi-página.
            if (cur_is_p0 or prev_is_p0) and _col_block < TOTAL and (_col_block + len(grupo)) <= TOTAL:
                new_block = _col_block
            else:
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


# Capacidad física de una planilla (hoja A4): 3 columnas × 40 filas.
CAP_PLANILLA = 40 * 3  # 120 casilleros


def _boletas_ordenadas(boletas):
    """Ordena por numero_principal (el grid las espera así)."""
    return sorted(boletas, key=lambda b: (b.numero_principal or 0))


def _boletas_caben(boletas) -> bool:
    """True si TODAS las boletas entran en una planilla sin desbordar la hoja.

    Simula el layout real (`_armar_grid_patas`, que descarta lo que no entra) y
    verifica que cada boleta haya quedado ubicada en el grid."""
    lst = _boletas_ordenadas(boletas)
    grid = _armar_grid_patas(lst)
    ubicadas = 0
    for col in grid["cols"]:
        for cell in col:
            if cell is not None and not isinstance(cell, dict):
                ubicadas += 1
    return ubicadas >= len(lst)


def _libres_planilla(boletas) -> int:
    """Casilleros libres en la planilla = 120 menos boletas menos separadores
    de PATA (filas-etiqueta X0/X1/X2…). Refleja el layout real, sin contar los
    huecos de alineación de bloque como ocupados (esos siguen siendo usables)."""
    lst = _boletas_ordenadas(boletas)
    grid = _armar_grid_patas(lst)
    ocupados = 0
    for col in grid["cols"]:
        for cell in col:
            if cell is not None:  # boleta o etiqueta de PATA
                ocupados += 1
    return max(0, CAP_PLANILLA - ocupados)


@router.get("/boleta/{bid}/info")
async def boleta_info_popup(bid: int, request: Request, db: Session = Depends(get_db)):
    """Datos de una boleta para el popover flotante (long-press sobre el N°) en
    las vistas de cobranza: todos los números de la talonera, socio, dirección,
    zona, teléfono, fecha de compra y vendedor."""
    await auth_module.require_user(request, db)
    b = db.query(models.Boleta).filter(models.Boleta.id == bid).first()
    if not b:
        raise HTTPException(404, detail="Boleta no encontrada")

    # Todos los números de la talonera de esta boleta: principal + adicionales.
    numeros = [f"{b.numero_principal:04d}"]
    if b.numeros_adicionales:
        numeros += [n.strip() for n in str(b.numeros_adicionales).split(",") if n.strip()]

    # Vendedor: preferir el de la liquidación; fallback al vendedor_id directo.
    vendedor = None
    if b.liquidacion_vendedor_id and b.liquidacion_vendedor and b.liquidacion_vendedor.vendedor:
        vendedor = b.liquidacion_vendedor.vendedor.nombre
    elif b.vendedor_id and b.vendedor:
        vendedor = b.vendedor.nombre

    comp = b.comprador
    zona = comp.zona.nombre if (comp and comp.zona) else None
    fecha = b.fecha_venta.strftime("%d/%m/%Y") if b.fecha_venta else None

    return JSONResponse({
        "id": b.id,
        "talonera": (b.talonera.nombre if b.talonera else ""),
        "numeros": numeros,
        "socio": (comp.apellido_nombre if comp else None),
        "direccion": (comp.direccion if comp else None),
        "telefono": (comp.telefono if comp else None),
        "zona": zona,
        "fecha_compra": fecha,
        "vendedor": vendedor,
    })


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db),
                mes: int = Query(default=0), anio: int = Query(default=0)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')

    hoy = hoy_ar()
    if not mes:  mes  = hoy.month
    if not anio: anio = hoy.year

    cobradores = db.query(models.Cobrador).order_by(models.Cobrador.nombre).all()

    resumen = []
    for co in cobradores:
        # Todas las boletas vivas del cobrador (excluye solo las dadas de baja).
        boletas = (db.query(models.Boleta)
                   .filter(models.Boleta.cobrador_id == co.id,
                           models.Boleta.condicion != CondicionBoleta.BAJA)
                   .all())
        total = len(boletas)

        # Todas las planillas del cobrador (de la primera a la última), con su
        # número y la fecha en que se entregó (se armó) cada una.
        planillas = (db.query(models.Planilla)
                     .filter_by(cobrador_id=co.id)
                     .order_by(models.Planilla.anio,
                               models.Planilla.mes,
                               models.Planilla.numero)
                     .all())
        # Solo las planillas YA liquidadas entran en el cálculo del % cobrado.
        # Una planilla recién armada todavía no tiene cobranza y, si se contara,
        # diluiría el promedio (sumaría al denominador sin aportar cuotas cobradas).
        planillas_liq_ids = {p.id for p in planillas if p.liquidacion}

        # Métricas en CUOTAS (ponderadas por PATA: una boleta PATA 2 = 2 cuotas).
        #   - cuotas_cobranza: cuotas a cobrar de las boletas YA emplanilladas y
        #     no terminadas → "lo que el cobrador tiene en cobranza" (suma de
        #     todas sus planillas activas).
        #   - pend_emplanillar: cuotas de boletas activas que todavía NO están en
        #     ninguna planilla (pendientes de emplanillar). Excluye contado.
        # Progreso de cobro (en cuotas, sin ponderar): Σ pagadas / Σ pactadas
        # sobre las boletas emplanilladas. La cobranza se considera "iniciada"
        # solo si se cobró algo MÁS ALLÁ de las cuotas anticipadas (las que cobra
        # el vendedor en la venta); si no, se oculta el porcentaje.
        # % cobrado = PROMEDIO de las tasas mes a mes (igual que el dashboard de
        # Reportes): por cada mes con cobranza (historial_cuotas), tasa del mes =
        # cuotas cobradas ese mes / boletas activas; el % es el promedio de esas
        # tasas. Refleja la gestión mensual y no queda diluido contra las 12 cuotas
        # de toda la campaña. Las bajas de registro no cuentan como activas.
        cuotas_cobranza = 0
        pend_emplanillar = 0
        pactadas_tot = 0
        pagadas_tot = 0
        anticipadas_tot = 0
        meses_cob = {}        # (anio, mes) -> cuotas cobradas ese periodo
        activas_cob = 0       # boletas activas emplanilladas (denominador por mes)
        meses_baja = {}       # mes calendario -> bajas registradas ese mes
        bajas_tot = 0         # boletas dadas de baja (emplanilladas, liquidadas)
        for b in boletas:
            no_terminada = (b.cuotas_pagadas or 0) < (b.cuotas_pactadas or 0)
            pv = _pata_valor(b)
            if b.planilla_id is not None:
                if no_terminada:
                    cuotas_cobranza += pv
                pactadas_tot += (b.cuotas_pactadas or 0)
                pagadas_tot += (b.cuotas_pagadas or 0)
                anticipadas_tot += (b.cuotas_anticipadas or 0)
                if b.planilla_id in planillas_liq_ids:
                    if not b.mes_baja:
                        activas_cob += 1
                        # Un historial corrupto no debe tumbar TODO el dashboard.
                        try:
                            _hist = json.loads(b.historial_cuotas) if b.historial_cuotas else {}
                        except (ValueError, TypeError):
                            _hist = {}
                        for _k, _v in _hist.items():
                            # Valores "YYYY-MM" (nuevo) o int 1-12 (legacy, anio 0).
                            _p = parse_periodo(_v)
                            if _p is None:
                                continue
                            _key = (_p[0] or 0, _p[1])   # (anio, mes)
                            meses_cob[_key] = meses_cob.get(_key, 0) + 1
                    else:
                        # Baja de cobranza: el socio se dio de baja en mes_baja.
                        bajas_tot += 1
                        try:
                            _mb = int(b.mes_baja)
                            meses_baja[_mb] = meses_baja.get(_mb, 0) + 1
                        except (TypeError, ValueError):
                            pass
            elif no_terminada and b.numero_especial_2 is None:
                pend_emplanillar += pv

        cobranza_iniciada = bool(meses_cob) and activas_cob > 0
        if cobranza_iniciada:
            _rates = [cnt / activas_cob for cnt in meses_cob.values()]
            pct_cobro = round(sum(_rates) / len(_rates) * 100)
        else:
            pct_cobro = 0

        # Desglose por mes para mostrar cómo se forma el promedio: cada mes con
        # cobranza con su tasa (cuotas cobradas / boletas activas).
        meses_detalle = [
            {"mes": (MESES[m - 1] + (f" {y}" if y else "")), "num": m,
             "cobradas": cnt,
             "activas": activas_cob, "pct": round(cnt / activas_cob * 100)}
            for (y, m), cnt in sorted(meses_cob.items())
        ] if cobranza_iniciada else []

        # % de baja (TOTAL ACUMULADO, no promedio mes a mes): a diferencia del
        # cobrado, una baja ocurre una sola vez por boleta (mes_baja), así que el
        # criterio acordado con Sergio es el porcentaje acumulado de socios dados
        # de baja sobre el total de boletas emplanilladas-liquidadas (activas +
        # bajas). El desglose por mes es solo informativo (cuándo se dieron).
        base_baja = activas_cob + bajas_tot
        pct_baja = round(bajas_tot / base_baja * 100) if base_baja > 0 else 0
        bajas_detalle = [
            {"mes": MESES[m - 1], "num": m, "cant": cnt}
            for m, cnt in sorted(meses_baja.items())
        ]

        # Resumen de cobranza del cobrador (todos los meses liquidados) — se
        # usa para la miniatura "Resumen de cobranza" de esta tarjeta, con
        # acceso rápido a ver/exportar/compartir/imprimir.
        _res = _resumen_meses_cobrador(db, co)
        consolidado = _res["totales"] if _res["filas"] else None

        # Planillas de ESTE cobrador pendientes de liquidar en el mes en curso:
        # alimenta el botón "Liquidar pendientes" de la tarjeta. La ronda de
        # liquidación es por cobrador, no global.
        pend_liq = sum(1 for c in _cola_liquidacion(db, cobrador_id=co.id) if c["pendiente"])

        resumen.append({
            "cobrador": co,
            "total": total,
            "cuotas_cobranza": int(round(cuotas_cobranza)),
            "pend_emplanillar": int(round(pend_emplanillar)),
            "pct_cobro": pct_cobro,
            "pct_baja": pct_baja,
            "bajas_tot": bajas_tot,
            "base_baja": base_baja,
            "bajas_detalle": bajas_detalle,
            "cobranza_iniciada": cobranza_iniciada,
            "meses_detalle": meses_detalle,
            "planillas": planillas,
            "consolidado": consolidado,
            "pend_liq": pend_liq,
        })

    # --- Alineación de "Planillas entregadas" entre todas las tarjetas ---
    # Todas las tarjetas comparten la misma grilla de meses: una fila por cada
    # (anio, mes) en que ALGÚN cobrador entregó planillas, ordenadas por fecha.
    # Cada mes reserva tantas celdas como el máximo de planillas que cualquier
    # cobrador entregó ese mes, así la fila de (p. ej.) Mayo arranca a la misma
    # altura en TODAS las tarjetas, dejando huecos vacíos donde un cobrador no
    # tiene planilla ese mes. Si un mes tiene más de 3 planillas, el flex-wrap
    # las baja a una fila debajo (igual en todas las tarjetas por el padding).
    meses_keys = []           # orden (anio, mes)
    max_por_mes = {}          # (anio, mes) -> máx de planillas de un cobrador ese mes
    for r in resumen:
        cnt = {}
        for pl in r["planillas"]:
            k = (pl.anio, pl.mes)
            cnt[k] = cnt.get(k, 0) + 1
        for k, c in cnt.items():
            if k not in max_por_mes:
                meses_keys.append(k)
            max_por_mes[k] = max(max_por_mes.get(k, 0), c)
    meses_keys.sort()

    for r in resumen:
        por_mes = {}
        for pl in r["planillas"]:
            por_mes.setdefault((pl.anio, pl.mes), []).append(pl)
        grid = []
        for k in meses_keys:
            cells = list(por_mes.get(k, []))
            cells += [None] * (max_por_mes[k] - len(cells))   # padding -> alineación
            grid.append({"anio": k[0], "mes": k[1], "cells": cells})
        r["planillas_grid"] = grid

    # Total de planillas pendientes de liquidar este mes (solo informativo en
    # el encabezado; el botón para arrancar la ronda está en cada cobrador).
    pendientes_liquidar = sum(r["pend_liq"] for r in resumen)

    return templates.TemplateResponse(request, "cobranza.html", {
        "user": user,
        "resumen": resumen,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
        "meses": MESES,
        "anios": list(range(hoy.year - 1, hoy.year + 2)),
        "pendientes_liquidar": pendientes_liquidar,
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

    hoy = hoy_ar()
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

    # MAX(numero)+1 y no COUNT+1: si se elimino una planilla intermedia,
    # COUNT+1 repetia un numero ya usado (fix A-4).
    ultimo_numero = (db.query(func.max(models.Planilla.numero))
                     .filter_by(cobrador_id=cobrador_id)
                     .scalar() or 0)

    return templates.TemplateResponse(request, "cobranza_planilla_armar.html", {
        "user": user,
        "cobrador": cobrador,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
        "disponibles": disponibles,
        "pata_info": pata_info,
        "siguiente_numero": ultimo_numero + 1,
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
        mes = int(form_data.get("mes") or hoy_ar().month)
        anio = int(form_data.get("anio") or hoy_ar().year)
    except (TypeError, ValueError):
        mes, anio = hoy_ar().month, hoy_ar().year
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

    # MAX(numero)+1 y no COUNT+1 (fix A-4: COUNT+1 duplicaba numeros si se
    # habia eliminado una planilla intermedia).
    siguiente_numero = (db.query(func.max(models.Planilla.numero))
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
    hoy = hoy_ar()
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

    # Números (boletas) que están actualmente en cada planilla — para el modal de
    # "pasar números". Excluye las que ya salieron (pasaron a otro lado).
    planilla_numeros = {}
    for r in resumen:
        for pl in r["planillas"]:
            bs = (db.query(models.Boleta)
                  .filter(models.Boleta.planilla_id == pl.id)
                  .order_by(models.Boleta.numero_principal)
                  .all())
            planilla_numeros[pl.id] = [
                {"id": b.id, "numero": "%04d" % b.numero_principal} for b in bs
            ]

    # Todos los cobradores activos (para elegir destino al pasar números).
    cobradores_todos = db.query(models.Cobrador).filter_by(activo=True).order_by(models.Cobrador.nombre).all()

    # Planillas del mes por cobrador CON su lugar libre — para el modal "pasar
    # números": permite agregar el número a una planilla existente con lugar en
    # vez de crear siempre una planilla nueva y solitaria.
    # {cobrador_id: [{"id", "numero", "libres", "capacidad"}], ...} ordenado por
    # lugar libre descendente (para preseleccionar la que más lugar tiene).
    planillas_por_cobrador = {}
    todas_planillas = (db.query(models.Planilla)
                       .filter_by(mes=mes, anio=anio)
                       .order_by(models.Planilla.numero)
                       .all())
    for pl in todas_planillas:
        bs = (db.query(models.Boleta)
              .filter(models.Boleta.planilla_id == pl.id)
              .order_by(models.Boleta.numero_principal)
              .all())
        planillas_por_cobrador.setdefault(pl.cobrador_id, []).append({
            "id": pl.id,
            "numero": pl.numero,
            "libres": _libres_planilla(bs),
            "capacidad": CAP_PLANILLA,
        })
    for cid in planillas_por_cobrador:
        planillas_por_cobrador[cid].sort(key=lambda d: d["libres"], reverse=True)

    return templates.TemplateResponse(request, "cobranza_emplanillado.html", {
        "user": user,
        "resumen": resumen,
        "planilla_numeros": planilla_numeros,
        "cobradores_todos": cobradores_todos,
        "planillas_por_cobrador": planillas_por_cobrador,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
        "meses": MESES,
        "anios": list(range(hoy.year - 1, hoy.year + 2)),
    })


# ── EDITAR PLANILLA ────────────────────────────────────────────────────────────

@router.get("/planilla/{planilla_id}/editar", response_class=HTMLResponse)
async def planilla_editar_form(request: Request, planilla_id: int,
                               db: Session = Depends(get_db)):
    # Este form muta datos (limpieza de abajo) y es la puerta de edicion:
    # exige permiso de EDITAR, igual que el armado de planillas (fix C-2:
    # antes cualquier usuario logueado podia dispararlo).
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    planilla = db.query(models.Planilla).get(planilla_id)
    if not planilla:
        raise HTTPException(404)

    # Limpieza silenciosa: si en esta planilla hay boletas pagadas al contado
    # (numero_especial_2 IS NOT NULL → no tienen cuotas para cobrar) o con
    # TODAS las cuotas pagas (cuotas_pagadas >= cuotas_pactadas, p.ej. boletas
    # "en cuotas" con cuotas_anticipadas = num_cuotas), las descontamos de la
    # planilla antes de mostrarla. Evita que queden huérfanas por data antigua
    # o por una baja anterior, o que el cobrador las vea con las 12 cuotas en X.
    #
    # IMPORTANTE (fix C-2): SOLO se limpian boletas SIN historial de cobranza.
    # El consolidado del cobrador suma las cuotas cobradas recorriendo las
    # boletas por planilla_id: sacar de la planilla una boleta que termino de
    # pagar EN cobranza (tiene historial_cuotas) hacia desaparecer esa plata
    # del consolidado del mes con solo abrir esta pantalla.
    limpiadas = (db.query(models.Boleta)
                 .filter(models.Boleta.planilla_id == planilla_id,
                         or_(models.Boleta.numero_especial_2.isnot(None),
                             models.Boleta.cuotas_pagadas >= models.Boleta.cuotas_pactadas),
                         or_(models.Boleta.historial_cuotas.is_(None),
                             models.Boleta.historial_cuotas.in_(("", "{}"))))
                 .update({"planilla_id": None}, synchronize_session=False))
    if limpiadas:
        db.commit()

    # Boletas ya en esta planilla (después de la limpieza, todas tienen cuotas)
    en_planilla = (db.query(models.Boleta)
                   .filter(models.Boleta.planilla_id == planilla_id)
                   .join(models.Comprador, isouter=True)
                   .order_by(models.Boleta.numero_principal)
                   .all())

    # Boletas activas del mismo cobrador disponibles para agregar.
    # SOLO las que NO tienen planilla (planilla_id IS NULL → "liberadas"). Una
    # boleta que ya está en otra planilla (de otro mes u otra del mismo mes) NO
    # debe aparecer acá: primero hay que liberarla desde esa otra planilla.
    # Excluye las pagadas al contado (sin cuotas para cobrar) y las totalmente
    # pagas en cuotas (cuotas_pagadas >= cuotas_pactadas → badge "Al contado").
    disponibles = (db.query(models.Boleta)
                   .filter(models.Boleta.cobrador_id == planilla.cobrador_id,
                           models.Boleta.planilla_id.is_(None),
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
    ids_seleccionados = set()
    for x in form_data.getlist("boleta_ids"):
        try:
            ids_seleccionados.add(int(x))
        except (TypeError, ValueError):
            continue

    # Quitar de planilla las que ya no estan seleccionadas.
    # PROTECCION (fix C-2): nunca sacar de la planilla una boleta CON historial
    # de cobranza - el consolidado suma lo cobrado recorriendo las boletas por
    # planilla_id, asi que sacarla haria desaparecer esa plata del consolidado.
    # Para mover un numero cobrado a otro lado esta "Pasar numeros" (que
    # conserva el rastro y el destino sigue contando).
    (db.query(models.Boleta)
       .filter(models.Boleta.planilla_id == planilla_id,
               models.Boleta.id.notin_(ids_seleccionados),
               or_(models.Boleta.historial_cuotas.is_(None),
                   models.Boleta.historial_cuotas.in_(("", "{}"))))
       .update({"planilla_id": None}, synchronize_session=False))

    # Agregar a la planilla las nuevas seleccionadas. Solo toma boletas
    # LIBERADAS (planilla_id IS NULL) para nunca robarle una boleta a otra
    # planilla (las que ya estaban en ESTA planilla y siguen seleccionadas no
    # se tocan en el paso anterior, así que permanecen). Filtra además
    # defensivamente las pagadas al contado y las totalmente pagas: nunca
    # asignar una boleta sin cuotas a una planilla de cobranza.
    if ids_seleccionados:
        (db.query(models.Boleta)
           .filter(models.Boleta.id.in_(ids_seleccionados),
                   models.Boleta.cobrador_id == planilla.cobrador_id,
                   models.Boleta.planilla_id.is_(None),
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


@router.post("/planilla/{planilla_id}/pasar-numeros")
async def pasar_numeros(request: Request, planilla_id: int,
                        db: Session = Depends(get_db)):
    """El cobrador dejó de cobrar ciertos números de esta planilla.

    Los números seleccionados PASAN a un destino (otro cobrador, o una planilla
    nueva del mismo cobrador) conservando cuotas pagadas e historial de meses.
    En la planilla ORIGEN el número NO desaparece: queda con una línea tipo baja
    que dice "PASÓ A <label>" (nombre del cobrador, o "P<numero>" de la planilla).
    La liquidación vieja de la planilla origen NO se toca (es un registro).
    """
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'cobranza', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')

    planilla = db.query(models.Planilla).get(planilla_id)
    if not planilla:
        raise HTTPException(404)

    form = await request.form()
    destino = (form.get("destino") or "").strip()  # "cobrador" | "planilla"

    ids = set()
    for x in form.getlist("boleta_ids"):
        try:
            ids.add(int(x))
        except (TypeError, ValueError):
            continue
    if not ids:
        return RedirectResponse(
            f"/cobranza/emplanillado?mes={planilla.mes}&anio={planilla.anio}",
            status_code=302)

    # Solo boletas que efectivamente están en ESTA planilla.
    boletas = (db.query(models.Boleta)
               .filter(models.Boleta.planilla_id == planilla_id,
                       models.Boleta.id.in_(ids))
               .all())
    if not boletas:
        return RedirectResponse(
            f"/cobranza/emplanillado?mes={planilla.mes}&anio={planilla.anio}",
            status_code=302)

    # ── Determinar cobrador destino ─────────────────────────────────────────
    if destino == "cobrador":
        try:
            dest_cobrador_id = int(form.get("cobrador_destino_id") or 0)
        except (TypeError, ValueError):
            dest_cobrador_id = 0
        dest_cobrador = db.query(models.Cobrador).get(dest_cobrador_id)
        if not dest_cobrador:
            # destino inválido → no hacer nada
            return RedirectResponse(
                f"/cobranza/emplanillado?mes={planilla.mes}&anio={planilla.anio}",
                status_code=302)
    else:
        # Compat con el flujo viejo: planilla nueva del MISMO cobrador (PATA 0).
        dest_cobrador = db.query(models.Cobrador).get(planilla.cobrador_id)

    # ── Determinar planilla destino: existente (con lugar) o nueva ───────────
    # planilla_destino_id: id de una planilla existente del cobrador destino, o
    # 0 / vacío / "nueva" para crear una planilla nueva.
    try:
        planilla_destino_id = int(form.get("planilla_destino_id") or 0)
    except (TypeError, ValueError):
        planilla_destino_id = 0

    dest_planilla = None
    if planilla_destino_id:
        cand = db.query(models.Planilla).get(planilla_destino_id)
        # Válida solo si es del cobrador destino, mismo mes/anio, y NO es la
        # planilla origen. Además las boletas a mover deben ENTRAR sin desbordar.
        if (cand and cand.id != planilla_id
                and cand.cobrador_id == dest_cobrador.id
                and cand.mes == planilla.mes and cand.anio == planilla.anio):
            existentes = (db.query(models.Boleta)
                          .filter(models.Boleta.planilla_id == cand.id)
                          .all())
            if _boletas_caben(list(existentes) + list(boletas)):
                dest_planilla = cand
        # Si no entra o no es válida → dest_planilla queda None y se crea una nueva.

    if dest_planilla is None:
        # ── Crear la planilla destino (mismo mes/anio que la origen) ─────────
        # MAX(numero)+1 y no COUNT+1 (fix A-4, mismo criterio que armar_planilla).
        siguiente_numero = (db.query(func.max(models.Planilla.numero))
                            .filter_by(cobrador_id=dest_cobrador.id)
                            .scalar() or 0) + 1
        dest_planilla = models.Planilla(
            cobrador_id=dest_cobrador.id,
            numero=siguiente_numero,
            mes=planilla.mes,
            anio=planilla.anio,
            comision_pct=planilla.comision_pct,
        )
        db.add(dest_planilla)
        db.flush()

    # ── Etiqueta del rastro "PASÓ A …" en la planilla origen ─────────────────
    # Otro cobrador → su nombre; mismo cobrador → "P<numero>" de la planilla.
    if dest_cobrador.id != planilla.cobrador_id:
        label = dest_cobrador.nombre
    else:
        label = f"P{dest_planilla.numero}"

    # ── Mover cada boleta dejando el rastro en la planilla origen ────────────
    for b in boletas:
        b.paso_origen_planilla_id = planilla_id
        b.paso_cuota = b.cuotas_pagadas or 0
        b.paso_a = label
        b.planilla_id = dest_planilla.id
        b.cobrador_id = dest_cobrador.id
        # cuotas_pagadas, historial_cuotas, condicion, liquidacion: SIN TOCAR.

    db.commit()
    return RedirectResponse(
        f"/cobranza/emplanillado?mes={planilla.mes}&anio={planilla.anio}",
        status_code=302
    )


# ── LIQUIDACIÓN ────────────────────────────────────────────────────────────────

@router.get("/liquidacion/{planilla_id:int}", response_class=HTMLResponse)
async def liquidacion_detalle(request: Request, planilla_id: int,
                               mes: int = Query(default=0), anio: int = Query(default=0),
                               db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    planilla = db.query(models.Planilla).get(planilla_id)
    if not planilla:
        raise HTTPException(404)

    # Igual que el preview/impresión de la planilla: además de las boletas que
    # ESTÁN en esta planilla, se traen las que SALIERON de ella ("pasó a otro
    # cobrador / a otra planilla"). Se muestran con la línea "PASÓ A ..." y NO
    # son editables, pero tienen que seguir ocupando su renglón para que la
    # grilla coincida con la planilla impresa que tiene el cobrador en la mano.
    boletas = (db.query(models.Boleta)
               .filter(or_(models.Boleta.planilla_id == planilla_id,
                           models.Boleta.paso_origen_planilla_id == planilla_id))
               .join(models.Comprador, isouter=True)
               .order_by(models.Boleta.numero_principal)
               .all())
    paso_map = _build_paso_map(boletas, planilla_id)

    liq = planilla.liquidacion

    # El PERIODO que se esta liquidando: por DEFECTO el mes/anio CALENDARIO
    # real de hoy (el mes en que el cobrador esta cobrando). planilla.mes es el
    # mes de VENTA/entrega (Mayo), no el de cobranza, por eso NO se usa aca.
    # Se puede elegir un mes PASADO (= editar esa liquidacion); un mes FUTURO
    # nunca (el helper lo devuelve ajustado al actual).
    anio_liq, mes_liq, periodo_es_pasado, periodo_ajustado = _resolver_periodo_liq(mes, anio)
    periodos_disponibles = _periodos_liquidables(planilla)
    periodo_stats = _liquidacion_periodo_stats(planilla, anio_liq, mes_liq)

    # Construir historial y seleccion actual por boleta.
    # historial_map (para el template) lleva SOLO las cuotas de OTROS periodos,
    # como {cuota_str: mes_int} (el mes se muestra en la celda azul). Las del
    # periodo ACTUAL van en cuotas_mes_actual (celdas rojas editables).
    # historial_full guarda todas las cuotas pagas (para calcular la baja).
    historial_map = {}
    cuotas_mes_actual = {}
    historial_full = {}
    for b in boletas:
        try:
            h = json.loads(b.historial_cuotas) if b.historial_cuotas else {}
        except (ValueError, TypeError):
            h = {}
        _otros, _actual, _full = {}, [], set()
        for k, v in h.items():
            try:
                _cn = int(k)
            except (TypeError, ValueError):
                continue
            _full.add(_cn)
            if match_periodo(v, anio_liq, mes_liq):
                _actual.append(_cn)
            else:
                _m = mes_de(v)
                if _m:
                    _otros[str(_cn)] = _m
        historial_map[b.id] = _otros
        cuotas_mes_actual[b.id] = _actual
        historial_full[b.id] = _full

    # ── Mismo grid 3 columnas que la planilla ──────────────────────────────
    _grid = _armar_grid_patas(boletas)
    c1, c2, c3 = _grid["cols"]
    rows = _grid["rows"]
    col1_label, col2_label, col3_label = _grid["labels"]
    col1_color, col2_color, col3_color = _grid["colors"]

    num_cuotas = max((b.cuotas_pactadas or 0) for b in boletas) if boletas else 10
    num_cuotas = max(num_cuotas, 10)
    cuota_nums = list(range(1, num_cuotas + 1))

    # ── Mapa boleta_id → columna (1, 2 o 3) + ponderación + valor de cuota ──
    # COL. 1/2/3 del resumen = las 3 secciones verticales de la planilla.
    # El cobrador suma por columna física al recorrer la planilla.
    #   - Planilla MIXTA: valor base = PATA 1 (= valor_cuota / multiplicador) y cada
    #     boleta pondera por su multiplicador (PATA 0 = 0.67, PATA 2 = 2, ...).
    #     Así PATA 0 da 0.67 × $15.000 = $10.000 y PATA 2 da 2 × $15.000 = $30.000.
    #   - Planilla ÚNICAMENTE PATA 0: cada cuota cuenta ×1 y el valor es el uniforme
    #     real ($10.000). No se aplica el 0.67 (regla pedida por el negocio).
    columna_de_boleta, multiplicador_de_boleta, valor_cuota = _columna_mult_valor(boletas, _grid)

    # ── Meses de la campaña (cuota 1 = mes siguiente al mes de la planilla) ──
    meses_campana = _meses_campana_desde(planilla.mes, num_cuotas)

    # ── Resumen inicial por MES CALENDARIO en que se cobró y columna ────────
    # Cada fila del resumen representa el mes (calendario) en que se cobraron
    # las cuotas. El mes actual lo recalcula el JS desde la selección.
    # resumen_otros[mes_calendario] = {1: count, 2: count, 3: count} — solo
    # cuotas cobradas en meses DISTINTOS al de la planilla.
    # historial_map ya viene filtrado a OTROS periodos y con el mes parseado.
    resumen_otros = {m: {1: 0, 2: 0, 3: 0} for m in range(1, 13)}
    for b in boletas:
        col = columna_de_boleta.get(b.id)
        if not col:
            continue
        mult = multiplicador_de_boleta.get(b.id, 1)
        # De una boleta que PASÓ a otro cobrador/planilla solo cuentan las
        # cuotas cobradas ACÁ (hasta paso_cuota); las de después las cobra el
        # destino y aparecerían duplicadas en las dos liquidaciones.
        _corte = int(paso_map[b.id].get("cuota") or 0) if b.id in paso_map else None
        for k, mes_pago in historial_map[b.id].items():
            if _corte is not None:
                try:
                    if int(k) > _corte:
                        continue
                except (TypeError, ValueError):
                    continue
            if 1 <= mes_pago <= 12:
                resumen_otros[mes_pago][col] += mult

    # ── Info por boleta para validacion secuencial en JS ────────────────────
    # `historial` = cuotas pagas en OTROS periodos (cuota -> mes). Las del
    # periodo actual viajan en cuotas_mes_actual (fix C-1).
    boletas_info = {
        b.id: {
            "anticipadas": int(b.cuotas_anticipadas or 0),
            "pactadas": int(b.cuotas_pactadas or 0),
            "historial": {int(k): v for k, v in historial_map[b.id].items()},
        }
        for b in boletas
    }

    # ── Bajas ya guardadas: rango de la línea tachada por boleta ─────────────
    # La línea va desde la cuota siguiente a la última paga hasta la última
    # cuota de la talonera (cuotas_pactadas). En el medio se muestra el mes
    # de baja. Se renderiza en el server para que reabra ya dibujada.
    baja_info = {}
    for b in boletas:
        if not b.mes_baja:
            continue
        info = boletas_info[b.id]
        pact = info["pactadas"] or num_cuotas
        last_paid = info["anticipadas"]
        # historial_full incluye TODAS las cuotas pagas (otros periodos + el
        # actual), igual que el historial crudo que se usaba antes aca.
        _pagas = historial_full.get(b.id, set())
        for n in range(1, pact + 1):
            if n <= info["anticipadas"] or (n in _pagas):
                last_paid = max(last_paid, n)
        desde = last_paid + 1
        hasta = pact
        if desde <= hasta:
            baja_info[b.id] = {"mes": int(b.mes_baja), "desde": desde,
                               "hasta": hasta, "mid": (desde + hasta) // 2}
        else:
            baja_info[b.id] = {"mes": int(b.mes_baja), "desde": 0,
                               "hasta": 0, "mid": 0}

    # ── Cadena de liquidación: las planillas DE ESTE COBRADOR, una a una ────
    # La ronda es por cobrador (P1 → P2 → P3 de la misma persona) y termina
    # ahí: nunca salta al cobrador siguiente. Si la planilla actual ya no tiene
    # pendientes (se está revisando/corrigiendo), no cuenta posición pero igual
    # se calcula a dónde mandar "Siguiente" (la próxima pendiente, salteándola).
    cola = _cola_liquidacion(db, anio_liq, mes_liq, cobrador_id=planilla.cobrador_id)
    pendientes_ids = [c["id"] for c in cola if c["pendiente"]]
    total_cola = len(pendientes_ids)
    if planilla_id in pendientes_ids:
        idx = pendientes_ids.index(planilla_id)
        posicion = idx + 1
        resto = pendientes_ids[idx + 1:]
    else:
        posicion = None
        resto = [pid for pid in pendientes_ids if pid != planilla_id]
    siguiente_id = resto[0] if resto else None
    cadena = {
        "total": total_cola,
        "posicion": posicion,
        "restantes": len(resto),
        "siguiente_id": siguiente_id,
    }

    # ── Entregas del cobrador en el período que se está liquidando ───────────
    # Son por COBRADOR + mes/año (no por planilla), así que se ven iguales en
    # P1, P2, P3: es la plata que ya entregó por ese mes. `cob_mes` trae el
    # cobrado/comisión/neto de TODAS sus planillas en el mes, para mostrar el
    # saldo que le queda por entregar mientras se liquida.
    # ── Navegación con flechas: las hojas del cobrador, en orden ─────────────
    # P1 → P2 → P3 … → HOJA RESUMEN (siempre la última). Se recorre con las
    # flechas de la botonera, que guardan antes de moverse.
    planillas_cob = (db.query(models.Planilla)
                     .filter_by(cobrador_id=planilla.cobrador_id)
                     .order_by(models.Planilla.anio, models.Planilla.mes,
                               models.Planilla.numero)
                     .all())
    _ids = [p.id for p in planillas_cob]
    _idx = _ids.index(planilla_id) if planilla_id in _ids else 0
    es_ultima_planilla = bool(_ids) and _ids[-1] == planilla_id

    def _hoja_planilla(p):
        return {"tipo": "planilla", "destino": str(p.id), "label": f"P{p.numero}"}

    _anterior = _hoja_planilla(planillas_cob[_idx - 1]) if _idx > 0 else None
    if _idx < len(_ids) - 1:
        _siguiente = _hoja_planilla(planillas_cob[_idx + 1])
    else:
        # Después de la última planilla viene la hoja resumen del mes.
        _siguiente = {"tipo": "resumen", "destino": "resumen", "label": "Hoja resumen"}
    nav_hojas = {
        "pos": _idx + 1,
        "total": len(_ids) + 1,          # +1 por la hoja resumen
        "anterior": _anterior,
        "siguiente": _siguiente,
    }

    entregas_mes = []
    cob_mes = None
    if es_ultima_planilla:
        cobrador_obj = db.query(models.Cobrador).get(planilla.cobrador_id)
        entregas_mes = (db.query(models.EntregaCobrador)
                        .filter_by(cobrador_id=planilla.cobrador_id,
                                   mes=mes_liq, anio=anio_liq)
                        .order_by(models.EntregaCobrador.fecha,
                                  models.EntregaCobrador.id)
                        .all())
        cob_mes = _consolidado_cobrador(db, cobrador_obj, mes_liq, anio_liq) if cobrador_obj else None

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
        "paso_map": paso_map,
        "liquidacion": liq,
        "mes_nombre": MESES[mes_liq - 1],
        "mes_actual": mes_liq,
        "anio_actual": anio_liq,
        "cuota_nums": cuota_nums,
        "num_cuotas": num_cuotas,
        "columna_de_boleta": columna_de_boleta,
        "multiplicador_de_boleta": multiplicador_de_boleta,
        "valor_cuota": valor_cuota,
        "meses_campana": meses_campana,
        "resumen_otros": resumen_otros,
        "boletas_info": boletas_info,
        "baja_info": baja_info,
        "cadena": cadena,
        "periodos_disponibles": periodos_disponibles,
        "periodo_es_pasado": periodo_es_pasado,
        "periodo_ajustado": periodo_ajustado,
        "periodo_stats": periodo_stats,
        "entregas_mes": entregas_mes,
        "cob_mes": cob_mes,
        "nav_hojas": nav_hojas,
        "hoy_iso": hoy_ar().isoformat(),
    })


@router.post("/liquidacion/{planilla_id:int}/guardar")
async def liquidacion_guardar(request: Request, planilla_id: int,
                               boleta_ids: List[int] = Form(...),
                               cuotas_json: List[str] = Form(...),
                               baja_mes: List[str] = Form(default=[]),
                               modo: str = Form(default="quedarse"),
                               destino: str = Form(default=""),
                               mes: int = Form(default=0),
                               anio: int = Form(default=0),
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
        liq = models.Liquidacion(planilla_id=planilla_id, fecha=hoy_ar())
        db.add(liq)
        db.flush()

    # Eliminar detalles anteriores y recrear
    db.query(models.LiquidacionDetalle).filter_by(liquidacion_id=liq.id).delete()

    # Periodo que se esta liquidando (fecha ARGENTINA, no UTC del server).
    # Por defecto el mes en curso; el form puede mandar uno PASADO (edicion de
    # esa liquidacion). Uno FUTURO nunca: el helper lo recorta al actual, asi
    # que un form manipulado no puede cargar cuotas adelantadas.
    _anio_liq, _mes_liq, _es_pasado, _ = _resolver_periodo_liq(mes, anio)
    _per_liq = periodo_str(_anio_liq, _mes_liq)   # ej. "2026-07"

    total_cuotas = 0
    monto_total  = 0.0
    for idx, (bid, cjson) in enumerate(zip(boleta_ids, cuotas_json)):
        try:
            cuotas_nuevas = json.loads(cjson)   # lista de enteros, ej: [2, 3, 4]
        except (ValueError, TypeError):
            cuotas_nuevas = []
        # Solo boletas de ESTA planilla: un form manipulado/desfasado no debe
        # poder tocar el historial de boletas de otra planilla (fix A-1).
        boleta = db.query(models.Boleta).get(bid)
        if not boleta or boleta.planilla_id != planilla_id:
            continue

        # Mes de baja para esta boleta (vacio = sin baja). Paralelo a boleta_ids.
        _baja_raw = baja_mes[idx] if idx < len(baja_mes) else ""
        try:
            baja_val = int(_baja_raw) if str(_baja_raw).strip() else None
        except (TypeError, ValueError):
            baja_val = None

        # Actualizar historial_cuotas: quitar SOLO las entradas del PERIODO
        # actual (anio+mes) y reemplazarlas por la seleccion nueva. Con el
        # formato viejo (mes sin anio) esto borraba tambien las cuotas del
        # mismo mes de OTRO anio (fix C-1). Se guarda "YYYY-MM".
        try:
            historial = json.loads(boleta.historial_cuotas) if boleta.historial_cuotas else {}
        except (ValueError, TypeError):
            historial = {}
        historial = {k: v for k, v in historial.items()
                     if not match_periodo(v, _anio_liq, _mes_liq)}
        for cn in cuotas_nuevas:
            historial[str(cn)] = _per_liq
        boleta.historial_cuotas = json.dumps(historial)

        # cuotas_pagadas = anticipadas + todas las del historial
        boleta.cuotas_pagadas = min(
            (boleta.cuotas_anticipadas or 0) + len(historial),
            boleta.cuotas_pactadas or ((boleta.cuotas_anticipadas or 0) + len(historial))
        )

        # Baja (clic derecho): es solo un REGISTRO visual. La boleta NO se saca
        # de las planillas — sigue apareciendo siempre marcada con la línea.
        # Por eso guardamos/limpiamos mes_baja pero NO tocamos la condicion
        # (no la pasamos a BAJA, que la quitaría de las planillas futuras).
        boleta.mes_baja = baja_val if baja_val else None

        # Recalcular condicion según las cuotas, como siempre:
        #   - Si quedó toda paga (o es contado, o no tiene cobrador) → VENDIDO
        #   - Si todavía hay cuotas pendientes con cobrador y no contado → EN_COBRANZA
        # No tocamos BAJA / SIN_VENDER / CAJA (bajas reales de Socios).
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
    liq.fecha = hoy_ar()
    db.commit()

    # El período elegido viaja en la URL de todos los saltos, así una ronda de
    # liquidación de (por ejemplo) Junio sigue siendo de Junio planilla a planilla.
    _qs_periodo = f"?mes={_mes_liq}&anio={_anio_liq}"

    # ── Flechas: guarda y se mueve a la hoja pedida ─────────────────────────
    # `destino` es el id de otra planilla del mismo cobrador, o "resumen" para
    # la hoja de liquidación del mes (la última del recorrido). Se valida que
    # sea del MISMO cobrador: un form manipulado no debe saltar a otro.
    if modo == "ir":
        if destino == "resumen":
            return RedirectResponse(
                f"/cobranza/consolidado/{planilla.cobrador_id}/hoja"
                f"?mes={_mes_liq}&anio={_anio_liq}&volver={planilla_id}",
                status_code=302)
        try:
            _dest_id = int(destino)
        except (TypeError, ValueError):
            _dest_id = 0
        _dest = db.query(models.Planilla).get(_dest_id) if _dest_id else None
        if _dest and _dest.cobrador_id == planilla.cobrador_id:
            return RedirectResponse(
                f"/cobranza/liquidacion/{_dest.id}{_qs_periodo}", status_code=302)
        return RedirectResponse(
            f"/cobranza/liquidacion/{planilla_id}{_qs_periodo}", status_code=302)

    # ── "Guardar y seguir con la siguiente" (liquidación en cadena) ─────────
    # Recalcula la cola YA con los cambios recién guardados (esta planilla
    # sale de "pendientes" en cuanto tiene algo marcado en el período) y salta
    # a la próxima planilla pendiente DEL MISMO COBRADOR. Cuando no le queda
    # ninguna, la ronda de ese cobrador terminó y vuelve a Planillas.
    if modo == "siguiente":
        cola = _cola_liquidacion(db, _anio_liq, _mes_liq,
                                 cobrador_id=planilla.cobrador_id)
        resto = [c["id"] for c in cola if c["pendiente"] and c["id"] != planilla_id]
        if resto:
            return RedirectResponse(f"/cobranza/liquidacion/{resto[0]}{_qs_periodo}", status_code=302)
        return RedirectResponse("/cobranza/?cadena_completa=1", status_code=302)

    if modo == "salir":
        return RedirectResponse("/cobranza/?liquidado=1", status_code=302)

    return RedirectResponse(f"/cobranza/liquidacion/{planilla_id}{_qs_periodo}", status_code=302)


@router.post("/liquidacion/{planilla_id:int}/entrega")
async def liquidacion_entrega_crear(request: Request, planilla_id: int,
                                    monto: float = Form(...),
                                    fecha: str = Form(default=""),
                                    tipo: str = Form(default="EFECTIVO"),
                                    observacion: str = Form(default=""),
                                    mes: int = Form(default=0),
                                    anio: int = Form(default=0),
                                    db: Session = Depends(get_db)):
    """Registra plata entregada por el cobrador SIN salir de la liquidación.

    Es la misma tabla que los adelantos (`EntregaCobrador`): por cobrador +
    período, no por planilla. Sirve tanto para cargar una entrega que hubo
    durante el mes como para anotar lo que entrega en el momento de liquidar."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    planilla = db.query(models.Planilla).get(planilla_id)
    if not planilla:
        raise HTTPException(404)
    _anio, _mes, _, _ = _resolver_periodo_liq(mes, anio)
    try:
        _fecha = date.fromisoformat(fecha) if fecha else hoy_ar()
    except (ValueError, TypeError):
        _fecha = hoy_ar()
    _tipo = (tipo or "EFECTIVO").strip().upper()
    if _tipo not in ("EFECTIVO", "PREMIO"):
        _tipo = "EFECTIVO"
    if monto and monto > 0:
        db.add(models.EntregaCobrador(
            cobrador_id=planilla.cobrador_id,
            fecha=_fecha,
            mes=_mes, anio=_anio,
            monto=float(monto),
            tipo=_tipo,
            observacion=(observacion or "").strip() or None,
        ))
        db.commit()
    return RedirectResponse(
        f"/cobranza/liquidacion/{planilla_id}?mes={_mes}&anio={_anio}", status_code=302)


@router.post("/liquidacion/{planilla_id:int}/entrega/{entrega_id:int}/eliminar")
async def liquidacion_entrega_eliminar(request: Request, planilla_id: int,
                                       entrega_id: int,
                                       db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    e = db.query(models.EntregaCobrador).get(entrega_id)
    _hoy = hoy_ar()
    _mes = e.mes if e else _hoy.month
    _anio = e.anio if e else _hoy.year
    if e:
        db.delete(e)
        db.commit()
    return RedirectResponse(
        f"/cobranza/liquidacion/{planilla_id}?mes={_mes}&anio={_anio}", status_code=302)


@router.get("/liquidacion/siguiente")
async def liquidacion_siguiente(request: Request,
                                cobrador_id: int = Query(default=0),
                                mes: int = Query(default=0), anio: int = Query(default=0),
                                db: Session = Depends(get_db)):
    """Punto de entrada de 'Liquidar las planillas pendientes de un cobrador':
    salta a la primera planilla pendiente DE ESE COBRADOR. Si no queda ninguna,
    vuelve a Planillas. Acepta mes/anio para arrancar una ronda de un período
    pasado (nunca futuro)."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    anio_liq, mes_liq, _, _ = _resolver_periodo_liq(mes, anio)
    cola = _cola_liquidacion(db, anio_liq, mes_liq,
                             cobrador_id=cobrador_id or None)
    pendientes = [c for c in cola if c["pendiente"]]
    if not pendientes:
        return RedirectResponse("/cobranza/?sin_pendientes=1", status_code=302)
    return RedirectResponse(
        f"/cobranza/liquidacion/{pendientes[0]['id']}?mes={mes_liq}&anio={anio_liq}",
        status_code=302)


@router.get("/planilla/{planilla_id}/ver", response_class=HTMLResponse)
async def planilla_ver(request: Request, planilla_id: int,
                       thumb: int = Query(default=0),
                       embed: int = Query(default=0),
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

    # Incluye las boletas que ESTÁN en esta planilla, MÁS las que salieron de
    # esta planilla ("pasaron a otro cobrador/planilla"): esas se siguen
    # mostrando acá con la línea "PASÓ A ...".
    boletas = (db.query(models.Boleta)
               .filter(or_(models.Boleta.planilla_id == planilla_id,
                           models.Boleta.paso_origen_planilla_id == planilla_id))
               .join(models.Comprador, isouter=True)
               .order_by(models.Boleta.numero_principal)
               .all())

    _grid = _armar_grid_patas(boletas)
    rows = _grid["rows"]
    col1_label, col2_label, col3_label = _grid["labels"]
    col1_color, col2_color, col3_color = _grid["colors"]

    # Historial de cuotas cobradas por boleta: {boleta_id: {cuota_str: mes}}.
    # Permite mostrar en la planilla el mes en que se liquido cada cuota.
    # hist_act marca cuales son del periodo actual (anio+mes), ver fix C-1.
    historial_map, hist_act = _hist_maps_display(boletas)
    mes_actual = hoy_ar().month
    paso_map = _build_paso_map(boletas, planilla_id)

    num_cuotas = max((b.cuotas_pactadas or 0) for b in boletas) if boletas else 10
    num_cuotas = max(num_cuotas, 10)
    cuota_nums = list(range(1, num_cuotas + 1))
    meses_campana = _meses_campana_desde(mes, num_cuotas)

    # Resumen mensual REAL (cuotas liquidadas + informe de dinero/comisión/neto
    # mes a mes), para que el preview no muestre la tabla en blanco.
    comision_pct = float(planilla_obj.comision_pct or (cobrador.comision_pct if cobrador else 0) or 0)
    resumen_rows, resumen_totales, valor_cuota = _resumen_mensual_rows(
        boletas, _grid, meses_campana, comision_pct, paso_map)

    return templates.TemplateResponse(request, "cobranza_planilla.html", {
        "user": user,
        "cobrador": cobrador,
        "planilla": planilla_obj,
        "planilla_label": f"P{planilla_obj.numero}",
        "historial_map": historial_map,
        "hist_act": hist_act,
        "mes_actual": mes_actual,
        "paso_map": paso_map,
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
        "resumen_rows": resumen_rows,
        "resumen_totales": resumen_totales,
        "valor_cuota": valor_cuota,
        "comision_pct": comision_pct,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
        "thumb": bool(thumb),
        "embed": bool(embed),
    })


@router.get("/{cobrador_id}/planilla", response_class=HTMLResponse)
async def planilla(request: Request, cobrador_id: int,
                   mes: int = Query(default=0), anio: int = Query(default=0),
                   db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')

    hoy = hoy_ar()
    if not mes:  mes  = hoy.month
    if not anio: anio = hoy.year

    cobrador = db.query(models.Cobrador).get(cobrador_id)
    if not cobrador:
        return RedirectResponse("/cobranza/", status_code=302)

    planilla_obj = db.query(models.Planilla).filter_by(cobrador_id=cobrador_id, mes=mes, anio=anio).first()

    if planilla_obj:
        # Boletas de esta planilla + las que salieron de ella (pasaron a otro lado)
        boletas = (db.query(models.Boleta)
                   .filter(or_(models.Boleta.planilla_id == planilla_obj.id,
                               models.Boleta.paso_origen_planilla_id == planilla_obj.id))
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

    # Historial de cuotas cobradas por boleta: {boleta_id: {cuota_str: mes}}.
    # Permite mostrar en la planilla (y su preview) el mes de liquidacion.
    # hist_act marca cuales son del periodo actual (anio+mes), ver fix C-1.
    historial_map, hist_act = _hist_maps_display(boletas)
    mes_actual = hoy_ar().month
    paso_map = _build_paso_map(boletas, planilla_obj.id) if planilla_obj else {}

    # Cantidad de cuotas (máximo entre todas las boletas, mínimo 10)
    num_cuotas = max((b.cuotas_pactadas or 0) for b in boletas) if boletas else 10
    num_cuotas = max(num_cuotas, 10)
    cuota_nums = list(range(1, num_cuotas + 1))

    # Meses de la campaña (cuota 1 = mes siguiente al mes de la planilla)
    meses_campana = _meses_campana_desde(mes, num_cuotas)

    planilla_label = f"P{planilla_obj.numero}" if planilla_obj else None

    # Resumen mensual REAL (cuotas liquidadas + informe de dinero/comisión/neto
    # mes a mes), para que el preview no muestre la tabla en blanco.
    comision_pct = float((planilla_obj.comision_pct if planilla_obj else None)
                          or (cobrador.comision_pct if cobrador else 0) or 0)
    resumen_rows, resumen_totales, valor_cuota = _resumen_mensual_rows(
        boletas, _grid, meses_campana, comision_pct, paso_map)

    return templates.TemplateResponse(request, "cobranza_planilla.html", {
        "user": user,
        "cobrador": cobrador,
        "planilla": planilla_obj,
        "planilla_label": planilla_label,
        "historial_map": historial_map,
        "hist_act": hist_act,
        "mes_actual": mes_actual,
        "paso_map": paso_map,
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
        "resumen_rows": resumen_rows,
        "resumen_totales": resumen_totales,
        "valor_cuota": valor_cuota,
        "comision_pct": comision_pct,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
    })


# ─────────────────────────────────────────────────────────────────────────────
# ADELANTOS de cobradores (dinero entregado a cuenta durante el mes)
# Sueltos por cobrador + período (mes/año). Se descuentan en la liquidación
# consolidada del cobrador. Se puede imprimir un recibo A4 de cada entrega.
# ─────────────────────────────────────────────────────────────────────────────
INSTITUCION_NOMBRE = "Asociación de Bomberos Voluntarios de Concepción del Uruguay"


@router.get("/adelantos", response_class=HTMLResponse)
async def adelantos_index(request: Request, db: Session = Depends(get_db),
                          mes: int = Query(default=0), anio: int = Query(default=0)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    hoy = hoy_ar()
    if not mes:  mes = hoy.month
    if not anio: anio = hoy.year

    cobradores = db.query(models.Cobrador).order_by(models.Cobrador.nombre).all()
    adelantos = (db.query(models.EntregaCobrador)
                 .filter(models.EntregaCobrador.mes == mes,
                         models.EntregaCobrador.anio == anio)
                 .order_by(models.EntregaCobrador.fecha.desc(),
                           models.EntregaCobrador.id.desc())
                 .all())
    nombres = {c.id: c.nombre for c in cobradores}
    # Desglose por cobrador con corte por tipo (efectivo vs premio/gasto)
    desglose_por_cob = {}
    total_general = 0.0
    total_efectivo = 0.0
    total_premio = 0.0
    for a in adelantos:
        m = float(a.monto or 0)
        es_premio = (a.tipo or "EFECTIVO").upper() == "PREMIO"
        d = desglose_por_cob.setdefault(a.cobrador_id, {"efectivo": 0.0, "premio": 0.0, "total": 0.0})
        d["total"] += m
        total_general += m
        if es_premio:
            d["premio"] += m
            total_premio += m
        else:
            d["efectivo"] += m
            total_efectivo += m

    return templates.TemplateResponse(request, "cobranza_adelantos.html", {
        "user": user,
        "cobradores": cobradores,
        "adelantos": adelantos,
        "nombres": nombres,
        "desglose_por_cob": desglose_por_cob,
        "total_general": total_general,
        "total_efectivo": total_efectivo,
        "total_premio": total_premio,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
        "hoy_iso": hoy.isoformat(),
    })


@router.post("/adelantos")
async def adelantos_crear(request: Request,
                          cobrador_id: int = Form(...),
                          fecha: str = Form(...),
                          mes: int = Form(...),
                          anio: int = Form(...),
                          monto: float = Form(...),
                          tipo: str = Form(default="EFECTIVO"),
                          observacion: str = Form(default=""),
                          db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    try:
        _fecha = date.fromisoformat(fecha)
    except (ValueError, TypeError):
        _fecha = hoy_ar()
    _tipo = (tipo or "EFECTIVO").strip().upper()
    if _tipo not in ("EFECTIVO", "PREMIO"):
        _tipo = "EFECTIVO"
    if monto and monto > 0:
        db.add(models.EntregaCobrador(
            cobrador_id=cobrador_id,
            fecha=_fecha,
            mes=int(mes),
            anio=int(anio),
            monto=float(monto),
            tipo=_tipo,
            observacion=(observacion or "").strip() or None,
        ))
        db.commit()
    return RedirectResponse(f"/cobranza/adelantos?mes={mes}&anio={anio}", status_code=302)


@router.post("/adelantos/{adelanto_id}/eliminar")
async def adelantos_eliminar(request: Request, adelanto_id: int,
                             db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    e = db.query(models.EntregaCobrador).get(adelanto_id)
    mes = e.mes if e else hoy_ar().month
    anio = e.anio if e else hoy_ar().year
    if e:
        db.delete(e)
        db.commit()
    return RedirectResponse(f"/cobranza/adelantos?mes={mes}&anio={anio}", status_code=302)


@router.get("/adelantos/{adelanto_id}/recibo", response_class=HTMLResponse)
async def adelanto_recibo(request: Request, adelanto_id: int,
                          db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    e = db.query(models.EntregaCobrador).get(adelanto_id)
    if not e:
        raise HTTPException(404)
    cobrador = db.query(models.Cobrador).get(e.cobrador_id)
    return templates.TemplateResponse(request, "cobranza_adelanto_recibo.html", {
        "user": user,
        "adelanto": e,
        "cobrador": cobrador,
        "mes_nombre": MESES[e.mes - 1],
        "institucion": INSTITUCION_NOMBRE,
    })


# ─────────────────────────────────────────────────────────────────────────────
# LIQUIDACIÓN CONSOLIDADA por cobrador / mes de cobranza
# Suma lo cobrado en el mes en TODAS las planillas del cobrador, calcula comisión
# y neto, y le resta los ADELANTOS ya entregados de ese mes → saldo a entregar.
# El dinero se calcula con el valor REAL de cada cuota (PATA 0=$10.000, etc.), así
# que es exacto sin depender de ponderaciones.
# ─────────────────────────────────────────────────────────────────────────────
def _resumen_meses_cobrador(db, cobrador, solo_anio: int = 0, solo_mes: int = 0):
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
        todo0 = _planilla_todo_pata0(boletas)
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
            vc = float(b.talonera.valor_cuota) if (b.talonera and b.talonera.valor_cuota) else 0.0
            peso = 1.0 if todo0 else _pata_valor(b)
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
            # La planilla todavía no estaba entregada.
            if _ord(*bi["desde"]) > fila_ord:
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


def _consolidado_cobrador(db, cobrador, mes, anio):
    detalle = []
    tot_monto = 0.0
    tot_com = 0.0
    tot_cuotas = 0.0
    planillas = (db.query(models.Planilla)
                 .filter_by(cobrador_id=cobrador.id)
                 .order_by(models.Planilla.anio, models.Planilla.mes, models.Planilla.numero)
                 .all())
    for p in planillas:
        pct = float(p.comision_pct or 0)
        boletas = db.query(models.Boleta).filter_by(planilla_id=p.id).all()
        todo0 = _planilla_todo_pata0(boletas)
        m_pl = 0.0
        c_pl = 0.0
        for b in boletas:
            try:
                h = json.loads(b.historial_cuotas) if b.historial_cuotas else {}
            except (ValueError, TypeError):
                h = {}
            # Cuotas cobradas en ESTE periodo (anio+mes). Antes comparaba solo
            # el mes y mezclaba julio 2026 con julio 2027 (fix C-1).
            cM = sum(1 for v in h.values() if match_periodo(v, anio, mes))
            if cM:
                vc = float(b.talonera.valor_cuota) if (b.talonera and b.talonera.valor_cuota) else 0.0
                m_pl += cM * vc
                c_pl += cM * (1.0 if todo0 else _pata_valor(b))
        if m_pl or c_pl:
            com = round(m_pl * pct / 100.0, 2)
            detalle.append({
                "numero": p.numero, "mes_planilla": p.mes, "anio_planilla": p.anio,
                "cuotas": c_pl, "monto": round(m_pl, 2), "pct": pct,
                "comision": com, "neto": round(m_pl - com, 2),
            })
            tot_monto += m_pl
            tot_com += com
            tot_cuotas += c_pl
    neto = round(tot_monto - tot_com, 2)
    adelantos = (db.query(models.EntregaCobrador)
                 .filter_by(cobrador_id=cobrador.id, mes=mes, anio=anio)
                 .order_by(models.EntregaCobrador.fecha)
                 .all())
    tot_adel = sum(float(a.monto or 0) for a in adelantos)
    adel_premio = sum(float(a.monto or 0) for a in adelantos if (a.tipo or "EFECTIVO").upper() == "PREMIO")
    adel_efectivo = round(tot_adel - adel_premio, 2)
    return {
        "cobrador": cobrador,
        "detalle": detalle,
        "adelantos": adelantos,
        "cuotas": int(round(tot_cuotas)),
        "monto": round(tot_monto, 2),
        "comision": round(tot_com, 2),
        "neto": neto,
        "total_adelantos": round(tot_adel, 2),
        "adel_efectivo": adel_efectivo,
        "adel_premio": round(adel_premio, 2),
        "saldo": round(neto - tot_adel, 2),
    }


@router.get("/consolidado", response_class=HTMLResponse)
async def consolidado_index(request: Request, db: Session = Depends(get_db),
                            mes: int = Query(default=0), anio: int = Query(default=0)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    hoy = hoy_ar()
    if not mes:  mes = hoy.month
    if not anio: anio = hoy.year
    cobradores = db.query(models.Cobrador).order_by(models.Cobrador.nombre).all()
    filas = [_consolidado_cobrador(db, c, mes, anio) for c in cobradores]
    totales = {
        "monto":            round(sum(f["monto"] for f in filas), 2),
        "comision":         round(sum(f["comision"] for f in filas), 2),
        "neto":             round(sum(f["neto"] for f in filas), 2),
        "total_adelantos":  round(sum(f["total_adelantos"] for f in filas), 2),
        "adel_efectivo":    round(sum(f["adel_efectivo"] for f in filas), 2),
        "adel_premio":      round(sum(f["adel_premio"] for f in filas), 2),
        "saldo":            round(sum(f["saldo"] for f in filas), 2),
    }
    return templates.TemplateResponse(request, "cobranza_consolidado.html", {
        "user": user,
        "filas": filas,
        "totales": totales,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
    })


@router.get("/consolidado/{cobrador_id}/hoja", response_class=HTMLResponse)
async def consolidado_hoja(request: Request, cobrador_id: int,
                           db: Session = Depends(get_db),
                           mes: int = Query(default=0), anio: int = Query(default=0),
                           thumb: int = Query(default=0), embed: int = Query(default=0),
                           volver: int = Query(default=0)):
    """Hoja A4 de la liquidación de UN mes: un renglón por planilla (P1, P2, P3…)
    con cuotas cobradas, monto, comisión y neto; abajo el detalle de entregas
    del mes y el saldo a entregar. Es la hoja que acompaña el cierre mensual,
    aparte del Resumen de cobranza (que es el histórico mes a mes)."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    hoy = hoy_ar()
    cobrador = db.query(models.Cobrador).get(cobrador_id)
    if not cobrador:
        raise HTTPException(404)

    # Meses que este cobrador tiene liquidados (con cobranza o con entregas).
    # Alimentan el navegador ← → de la hoja y definen a cuál caer por defecto.
    periodos = [p for p in _resumen_meses_cobrador(db, cobrador)["periodos"]]
    for p in periodos:
        if not p["anio"]:
            p["anio"] = hoy.year          # datos legacy sin año
        p["key"] = f"{p['anio']}-{p['mes']}"

    # Sin mes explícito (entrada desde el preview) se abre el ÚLTIMO mes
    # liquidado, no el mes en curso: en el mes actual todavía puede no haberse
    # liquidado nada y la hoja saldría en cero.
    if not mes:
        if periodos:
            anio, mes = periodos[-1]["anio"], periodos[-1]["mes"]
        else:
            anio, mes = hoy.year, hoy.month
    if not anio:
        anio = hoy.year

    # Anterior / siguiente entre los meses liquidados. Si el mes pedido no está
    # en la lista (ej. agosto sin liquidar), "anterior" es el último liquidado
    # antes de él y "siguiente" el primero después.
    _ord_actual = anio * 12 + mes
    _antes = [p for p in periodos if p["anio"] * 12 + p["mes"] < _ord_actual]
    _despues = [p for p in periodos if p["anio"] * 12 + p["mes"] > _ord_actual]
    nav_meses = {
        "periodos": periodos,
        "actual_key": f"{anio}-{mes}",
        "anterior": _antes[-1] if _antes else None,
        "siguiente": _despues[0] if _despues else None,
        "liquidado": any(p["anio"] == anio and p["mes"] == mes for p in periodos),
    }

    data = _consolidado_cobrador(db, cobrador, mes, anio)
    return templates.TemplateResponse(request, "cobranza_hoja_liquidacion.html", {
        "user": user,
        "d": data,
        "cobrador": cobrador,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1],
        "institucion": INSTITUCION_NOMBRE,
        "hoy": hoy.strftime("%d/%m/%Y"),
        "thumb": bool(thumb),
        "embed": bool(embed),
        "volver": volver or 0,
        "nav_meses": nav_meses,
    })


@router.get("/consolidado/{cobrador_id}/hoja.pdf")
async def consolidado_hoja_pdf(request: Request, cobrador_id: int,
                               db: Session = Depends(get_db),
                               mes: int = Query(default=0), anio: int = Query(default=0)):
    """La misma hoja del mes en PDF, para guardar o mandar por WhatsApp."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    hoy = hoy_ar()
    if not mes:  mes = hoy.month
    if not anio: anio = hoy.year
    cobrador = db.query(models.Cobrador).get(cobrador_id)
    if not cobrador:
        raise HTTPException(404)
    data = _consolidado_cobrador(db, cobrador, mes, anio)
    html = templates.get_template("cobranza_hoja_liquidacion_pdf.html").render(
        request=request,
        d=data,
        cobrador=cobrador,
        mes=mes, anio=anio,
        mes_nombre=MESES[mes - 1],
        institucion=INSTITUCION_NOMBRE,
        hoy=hoy.strftime("%d/%m/%Y"),
    )
    buf = BytesIO()
    if pisa.CreatePDF(html, dest=buf, encoding="utf-8").err:
        raise HTTPException(500, "No se pudo generar el PDF de la hoja de liquidación")
    nombre = (f"liquidacion_{cobrador.nombre}_{MESES[mes - 1].lower()}_{anio}.pdf"
              .replace(" ", "_"))
    return Response(
        content=buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@router.get("/consolidado/{cobrador_id}/comprobante", response_class=HTMLResponse)
async def consolidado_comprobante(request: Request, cobrador_id: int,
                                  db: Session = Depends(get_db),
                                  mes: int = Query(default=0), anio: int = Query(default=0),
                                  thumb: int = Query(default=0), embed: int = Query(default=0)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    hoy = hoy_ar()
    cobrador = db.query(models.Cobrador).get(cobrador_id)
    if not cobrador:
        raise HTTPException(404)
    # Por defecto el resumen trae TODOS los meses liquidados (un renglón por
    # mes). Con ?mes=&anio= se recorta a un mes puntual: al pasarle la
    # liquidación a la institución normalmente los meses anteriores ya se
    # pasaron y solo interesa el nuevo.
    data = _resumen_meses_cobrador(db, cobrador, anio, mes)
    return templates.TemplateResponse(request, "cobranza_consolidado_comprobante.html", {
        "user": user,
        "d": data,
        "cobrador": cobrador,
        "mes": mes, "anio": anio,
        "mes_nombre": MESES[mes - 1] if 1 <= mes <= 12 else "",
        "institucion": INSTITUCION_NOMBRE,
        "hoy": hoy.strftime("%d/%m/%Y"),
        "thumb": bool(thumb),
        "embed": bool(embed),
    })


def _render_comprobante_pdf(request, data, cobrador, mes, anio) -> bytes:
    """Genera el PDF del comprobante de liquidación consolidada de un cobrador
    (mismos datos que la pantalla /comprobante), con xhtml2pdf: pura Python,
    sin dependencias de sistema (Cairo/Pango), para no complicar el Dockerfile
    de Railway. Usa una plantilla aparte (solo tablas, sin flex) porque el
    motor de xhtml2pdf soporta un subconjunto chico de CSS."""
    hoy_str = hoy_ar().strftime("%d/%m/%Y")
    html = templates.get_template("cobranza_consolidado_comprobante_pdf.html").render(
        request=request,
        d=data,
        cobrador=cobrador,
        mes=mes, anio=anio,
        mes_nombre=MESES[mes - 1] if 1 <= mes <= 12 else "",
        institucion=INSTITUCION_NOMBRE,
        hoy=hoy_str,
    )
    buf = BytesIO()
    result = pisa.CreatePDF(html, dest=buf, encoding="utf-8")
    if result.err:
        raise HTTPException(500, "No se pudo generar el PDF del comprobante")
    return buf.getvalue()


@router.get("/consolidado/{cobrador_id}/comprobante.pdf")
async def consolidado_comprobante_pdf(request: Request, cobrador_id: int,
                                      db: Session = Depends(get_db),
                                      mes: int = Query(default=0), anio: int = Query(default=0)):
    """Descarga el comprobante de liquidación consolidada en PDF (mismos datos
    que la pantalla, listo para guardar, mandar por WhatsApp o imprimir)."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'cobranza', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    cobrador = db.query(models.Cobrador).get(cobrador_id)
    if not cobrador:
        raise HTTPException(404)
    # Mismo criterio que la pantalla: sin mes → todos los meses; con mes → ese.
    data = _resumen_meses_cobrador(db, cobrador, anio, mes)
    pdf_bytes = _render_comprobante_pdf(request, data, cobrador, mes, anio)
    _suf = f"_{MESES[mes - 1].lower()}_{anio}" if 1 <= mes <= 12 else ""
    nombre_archivo = f"resumen_cobranza_{cobrador.nombre}{_suf}.pdf".replace(" ", "_")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'},
    )
# fin cobranza.py
