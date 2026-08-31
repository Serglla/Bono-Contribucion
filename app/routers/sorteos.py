from fastapi import HTTPException,  APIRouter, Depends, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from typing import Optional, List
from datetime import date as date_type, timedelta
from pydantic import BaseModel
from io import BytesIO
import json
import re
# PDF del recibo de premio (mismo motor que usa cobranza para sus hojas)
from xhtml2pdf import pisa

from sqlalchemy.orm import Session, joinedload
from .. import models, auth as auth_module
from ..templates_config import templates
from ..database import get_db
from ..tiempo import match_periodo
from ..scraper import buscar_resultado_tombola

router = APIRouter(prefix="/sorteos", tags=["sorteos"])


_MESES_ES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]


@router.get("/", response_class=HTMLResponse)
async def listar(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'sorteos', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    sorteos = db.query(models.Sorteo).order_by(models.Sorteo.fecha.asc()).all()

    # Último sorteo por tipo (lista asc → el último visto por tipo es el más reciente)
    ultima_por_tipo: dict = {}
    for s in sorteos:
        tipo = s.tipo.value
        ultima_por_tipo[tipo] = {
            "fecha": s.fecha.isoformat(),
            "num_premios": s.num_premios or 20,
        }

    # Agrupar sorteos por mes (mes-año). Se mantiene el orden cronológico:
    # el mes actual y los meses futuros primero (más cercanos arriba), después los pasados.
    grupos = {}
    for s in sorteos:
        key = f"{s.fecha.year}-{s.fecha.month:02d}"
        if key not in grupos:
            grupos[key] = {
                "key": key,
                "year": s.fecha.year,
                "month": s.fecha.month,
                "label": f"{_MESES_ES[s.fecha.month - 1]} {s.fecha.year}",
                "sorteos": [],
                "con_resultado": 0,
                "total": 0,
            }
        grupos[key]["sorteos"].append(s)
        grupos[key]["total"] += 1
        if s.resultado_json:
            grupos[key]["con_resultado"] += 1

    hoy = date_type.today()
    mes_actual_key = f"{hoy.year}-{hoy.month:02d}"

    # Orden: primero los del mes actual y futuros (cronológico), después los pasados (recientes arriba)
    futuros = sorted(
        [g for g in grupos.values() if g["key"] >= mes_actual_key],
        key=lambda g: g["key"],
    )
    pasados = sorted(
        [g for g in grupos.values() if g["key"] < mes_actual_key],
        key=lambda g: g["key"],
        reverse=True,
    )
    sorteos_por_mes = futuros + pasados

    return templates.TemplateResponse(request, "sorteos.html", {
        "user": user,
        "sorteos": sorteos,
        "sorteos_por_mes": sorteos_por_mes,
        "mes_actual_key": mes_actual_key,
        "ultima_por_tipo": ultima_por_tipo,
    })


_TIPO_LABEL_PLURAL = {
    "SEMANAL": "SEMANALES",
    "MENSUAL": "MENSUALES",
    "CONTADO": "AL CONTADO",
    "FINAL":   "FINALES",
}


def _ultimo_dia_mes(year: int, month: int) -> date_type:
    if month == 12:
        return date_type(year, 12, 31)
    return date_type(year, month + 1, 1) - timedelta(days=1)


def _premios_por_tipo(tipo: str) -> int:
    """Cuántos premios cruzar con boletas según el tipo de sorteo."""
    return 3 if tipo == "FINAL" else 1


@router.get("/extracto/{year}/{month}", response_class=HTMLResponse)
async def extracto_mes(
    year: int,
    month: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Genera el extracto del mes: cruza los premios de cada sorteo con las boletas
    cuyo `fecha_venta <= fecha_sorteo` y arma la lista de ganadores.

    REGLA (24/08/2026): la boleta comprada EL MISMO DÍA del sorteo SÍ participa.
    El socio compró antes de que se sorteara, así que el número juega. Solo
    quedan afuera las compradas DESPUÉS. Antes el corte era estricto (`<`) y
    dejaba afuera ventas del propio día del sorteo.
    """
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'sorteos', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    if month < 1 or month > 12:
        raise HTTPException(400, 'Mes inválido')

    inicio = date_type(year, month, 1)
    fin = _ultimo_dia_mes(year, month)

    # Sorteos del mes con resultado cargado
    sorteos = db.query(models.Sorteo).filter(
        models.Sorteo.fecha >= inicio,
        models.Sorteo.fecha <= fin,
        models.Sorteo.resultado_json.isnot(None),
    ).order_by(models.Sorteo.fecha.asc()).all()

    mes_label = _MESES_ES[month - 1].upper()

    if not sorteos:
        return templates.TemplateResponse(request, "sorteo_extracto.html", {
            "user": user,
            "year": year,
            "month": month,
            "mes_label": mes_label,
            "bloques": [],
            "vacio": True,
        })

    # Cargar boletas con socio cargado y fecha_venta dentro del rango necesario
    fecha_max = max(s.fecha for s in sorteos)
    boletas = db.query(models.Boleta).options(
        joinedload(models.Boleta.comprador),
        joinedload(models.Boleta.talonera),
    ).filter(
        models.Boleta.comprador_id.isnot(None),
        models.Boleta.fecha_venta.isnot(None),
        models.Boleta.fecha_venta <= fecha_max,   # el día del sorteo cuenta
    ).all()

    # Overrides manuales de habilitación del mes (clave (sorteo_id, boleta_id))
    sorteo_ids = [s.id for s in sorteos]
    overrides = {
        (h.sorteo_id, h.boleta_id): h
        for h in db.query(models.HabilitacionSorteo).filter(
            models.HabilitacionSorteo.sorteo_id.in_(sorteo_ids)
        ).all()
    }
    # Overrides manuales del NIVEL DE CIFRAS (para el resaltado del extracto).
    # Tabla aparte de HabilitacionSorteo a propósito — ver models.py.
    cifras_overrides = {
        (co.sorteo_id, co.boleta_id): co
        for co in db.query(models.CifrasGanadorOverride).filter(
            models.CifrasGanadorOverride.sorteo_id.in_(sorteo_ids)
        ).all()
    }

    # Agrupar sorteos por tipo
    sorteos_por_tipo: dict = {}
    for s in sorteos:
        tipo = s.tipo.value
        sorteos_por_tipo.setdefault(tipo, []).append(s)

    # Orden preferido de tipos en el extracto
    orden_tipos = ["SEMANAL", "MENSUAL", "CONTADO", "FINAL"]

    bloques = []
    for tipo in orden_tipos:
        if tipo not in sorteos_por_tipo:
            continue
        sorteos_tipo = sorteos_por_tipo[tipo]
        max_premios = _premios_por_tipo(tipo)

        # Lista de premios a mostrar en la cabecera (fecha + posición + número)
        premios = []
        # Ganadores únicos por boleta (deduplicar)
        ganadores_dict: dict = {}
        # Conjunto de cifras usadas (para el "A 4 Y 3 CIFRAS" de la cabecera)
        cifras_set = set()

        for s in sorteos_tipo:
            try:
                numeros_ganadores = json.loads(s.resultado_json)
            except Exception:
                continue
            cifras_list = sorted([int(c) for c in str(s.cifras).split(",")], reverse=True)
            cifras_set.update(cifras_list)

            # Boletas vendidas hasta el día del sorteo, ese día incluido
            boletas_anteriores = [
                b for b in boletas
                if b.fecha_venta and b.fecha_venta <= s.fecha
            ]

            # Recorrer los N primeros premios
            for i, num_str in enumerate(numeros_ganadores[:max_premios], start=1):
                num4 = str(num_str).zfill(4)
                premios.append({
                    "fecha": s.fecha,
                    "posicion": i,
                    "numero": num4,
                })

                # Cruzar con cada cifra del sorteo
                for c in cifras_list:
                    sufijo = num4[-c:]
                    for b in boletas_anteriores:
                        if b.id in ganadores_dict:
                            continue
                        # Para sorteo CONTADO usamos numero_especial / numero_especial_2
                        if tipo == "CONTADO":
                            candidatos = []
                            if b.numero_especial is not None:
                                candidatos.append(b.numero_especial)
                            if b.numero_especial_2 is not None:
                                candidatos.append(b.numero_especial_2)
                            for n in candidatos:
                                bol4 = f"{n:04d}"
                                if bol4[-c:] == sufijo:
                                    ganadores_dict[b.id] = _build_ganador(
                                        b, bol4, c, s,
                                        overrides.get((s.id, b.id)),
                                        cifras_overrides.get((s.id, b.id)))
                                    break
                        else:
                            # Excluir boletas de talonera CONTADO (pool, no son boletas reales)
                            if b.talonera and (b.talonera.tipo or "COMUN") == "CONTADO":
                                continue
                            # Cruzar contra TODOS los números de la boleta (principal +
                            # adicionales). El número ganador puede ser cualquiera de la
                            # pata, no solo el principal.
                            for n in _numeros_boleta(b):
                                bol4 = f"{n:04d}"
                                if bol4[-c:] == sufijo:
                                    ganadores_dict[b.id] = _build_ganador(
                                        b, bol4, c, s,
                                        overrides.get((s.id, b.id)),
                                        cifras_overrides.get((s.id, b.id)))
                                    break

        # Ordenar premios por fecha y posición
        premios.sort(key=lambda p: (p["fecha"], p["posicion"]))

        # Ganadores ordenados por importancia del premio y despues alfabetico:
        # primero los que pegaron mas cifras (4 antes que 3 = premio mayor), y
        # dentro de cada nivel por apellido y nombre.
        ganadores_lista = sorted(
            ganadores_dict.values(),
            key=lambda g: (-int(g["cifras_match"] or 0), g["nombre"]),
        )

        cifras_label = " Y ".join(str(c) for c in sorted(cifras_set, reverse=True))

        # Nivel MÁS ALTO de cifras de este bloque (4 en un sorteo "a 4 y 3 cifras").
        # El extracto pone en negrita a los ganadores que llegaron a ese nivel —
        # son los "premios mayores" que pidió distinguir la institución. Se toma el
        # máximo real del bloque y no un 4 fijo, para que un sorteo que se juegue
        # solo a 3 y 2 cifras igual resalte a los de 3.
        cifras_max = max(cifras_set) if cifras_set else 0

        bloques.append({
            "tipo": tipo,
            "tipo_label": _TIPO_LABEL_PLURAL.get(tipo, tipo),
            "premios": premios,
            "ganadores": ganadores_lista,
            "total_ganadores": len(ganadores_lista),
            "total_habilitados": sum(1 for g in ganadores_lista if g["habilitado"]),
            "cifras_label": cifras_label,
            "cifras_max": cifras_max,
        })

    return templates.TemplateResponse(request, "sorteo_extracto.html", {
        "user": user,
        "year": year,
        "month": month,
        "mes_label": mes_label,
        "bloques": bloques,
        "vacio": False,
    })


def _pdf_response(html: str, nombre: str, que: str) -> Response:
    """Renderiza `html` con pisa y lo devuelve como adjunto. `que` solo se usa
    en el mensaje de error."""
    buf = BytesIO()
    if pisa.CreatePDF(html, dest=buf, encoding="utf-8").err:
        raise HTTPException(500, f"No se pudo generar el PDF {que}")
    nombre = re.sub(r"[^A-Za-z0-9_.-]", "_", nombre)
    return Response(
        content=buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@router.get("/extracto/{year}/{month}/pdf")
async def extracto_mes_pdf(
    year: int,
    month: int,
    request: Request,
    db: Session = Depends(get_db),
    cols: int = Query(default=1),
    filas: int = Query(default=1),
    size: str = Query(default="md"),
):
    """El mismo extracto, en PDF, para mandarlo por WhatsApp.

    `cols` / `filas` / `size` son los de la barra de opciones de la pantalla: el
    boton Compartir manda lo que el usuario tenga elegido, asi el PDF sale igual
    a lo que ve.

    El contexto se toma de la vista HTML (`extracto_mes`) en vez de recalcular el
    cruce: si manana cambia el criterio de ganadores o el orden, cambia en los dos
    lados a la vez.
    """
    resp = await extracto_mes(year, month, request, db)
    ctx = dict(getattr(resp, "context", {}) or {})
    ctx.pop("request", None)
    if not ctx or ctx.get("vacio"):
        raise HTTPException(404, "No hay sorteos con resultado cargado en ese mes")

    cols = min(3, max(1, cols))
    filas = min(10, max(1, filas))
    size = size if size in ("sm", "md", "lg") else "md"

    html = templates.get_template("sorteo_extracto_pdf.html").render(
        request=request, cols=cols, filas=filas, size=size,
        multi=(cols * filas > 1), **ctx)
    nombre = f"extracto_{_MESES_ES[month - 1].lower()}_{year}.pdf"
    return _pdf_response(html, nombre, "del extracto")


@router.get("/informe/{year}/{month}/pdf")
async def informe_mes_pdf(
    year: int,
    month: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """El informe del mes en PDF (apaisado). Mismo criterio que el extracto:
    reusa el contexto de la vista HTML."""
    resp = await informe_mes(year, month, request, db)
    ctx = dict(getattr(resp, "context", {}) or {})
    ctx.pop("request", None)
    if not ctx:
        raise HTTPException(404, "No se pudo armar el informe de ese mes")

    html = templates.get_template("sorteo_informe_pdf.html").render(request=request, **ctx)
    nombre = f"informe_sorteos_{_MESES_ES[month - 1].lower()}_{year}.pdf"
    return _pdf_response(html, nombre, "del informe")


@router.get("/informe/{year}/{month}", response_class=HTMLResponse)
async def informe_mes(
    year: int,
    month: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Informe detallado del mes: todos los sorteos con resultado, sus datos
    (nombre, tipo, cifras, fecha, premios y el detalle cargado al crearlos) y
    los ganadores válidos (socio + boleta vendida antes del sorteo) con el
    número y a cuántas cifras ganaron."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'sorteos', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    if month < 1 or month > 12:
        raise HTTPException(400, 'Mes inválido')

    inicio = date_type(year, month, 1)
    fin = _ultimo_dia_mes(year, month)
    mes_label = _MESES_ES[month - 1].upper()

    sorteos = db.query(models.Sorteo).filter(
        models.Sorteo.fecha >= inicio,
        models.Sorteo.fecha <= fin,
        models.Sorteo.resultado_json.isnot(None),
    ).order_by(models.Sorteo.fecha.asc()).all()

    # Overrides manuales del nivel de cifras (los mismos que usa el extracto para
    # decidir quién va en negrita). Se cargan de una para no hacer N+1.
    _cif_ov = {
        (co.sorteo_id, co.boleta_id): co
        for co in db.query(models.CifrasGanadorOverride).filter(
            models.CifrasGanadorOverride.sorteo_id.in_([s.id for s in sorteos])
        ).all()
    } if sorteos else {}

    # Entregas ya asignadas del mes, para linkear el RECIBO directo desde el informe
    # (antes había que ir a Entregas del sorteo a buscarlo). Clave (sorteo, boleta).
    _entregas = {}
    # Lista completa para el resumen de costos del pie: `_entregas` se queda con
    # UNA entrega por (sorteo, boleta) para el link del recibo, pero un mismo
    # ganador puede llevarse DOS premios (la orden de compra y la bicicleta) y el
    # resumen tiene que contar los dos.
    _todas_entregas = []
    if sorteos:
        for _e in (db.query(models.EntregaPremio)
                     .join(models.PremioSorteo,
                           models.EntregaPremio.premio_id == models.PremioSorteo.id)
                     .filter(models.PremioSorteo.sorteo_id.in_([s.id for s in sorteos]))
                     .all()):
            _entregas.setdefault((_e.premio.sorteo_id, _e.boleta_id), _e.id)
            _todas_entregas.append(_e)

    bloques = []
    total_ganadores_mes = 0
    for s in sorteos:
        grupos, cifras_list = _calcular_grupos_ganadores(s, db)
        cifras_max = max(cifras_list) if cifras_list else 0
        ganadores = []
        for g in grupos:
            for f in g["filas"]:
                if not f["es_ganador_valido"]:
                    continue
                _ov = _cif_ov.get((s.id, f["boleta_id"]))
                ganadores.append({
                    "boleta_id": f["boleta_id"],
                    "nombre": f["comprador"],
                    "numero": f["num_match"],
                    "cifras": g["cifras"],
                    # Nivel que va a usar el extracto para el resaltado: el forzado
                    # a mano si existe, sino el que salió del cruce.
                    "cifras_efectivas": int(_ov.cifras) if _ov else g["cifras"],
                    "cifras_manual": _ov is not None,
                    "cifras_motivo": (_ov.motivo if _ov else None),
                    "es_mayor": (int(_ov.cifras) if _ov else g["cifras"]) == cifras_max,
                    # id de la entrega asignada, si ya la hay → botón de recibo
                    "entrega_id": _entregas.get((s.id, f["boleta_id"])),
                    "posicion": g["posicion"],
                    "talonera": f["talonera"],
                    "vendedor": f["vendedor"],
                    "cobrador": f["cobrador"],
                    "fecha_venta": f["fecha_venta"],
                    "habilitado": f["habilitado"],
                    "habilitado_motivo": f["habilitado_motivo"],
                    "habilitado_manual": f["habilitado_manual"],
                })
        # Orden: cifras desc, posición del premio, nombre
        ganadores.sort(key=lambda x: (-x["cifras"], x["posicion"], x["nombre"] or ""))

        try:
            numeros = json.loads(s.resultado_json)[: (s.num_premios or 20)]
        except Exception:
            numeros = []

        bloques.append({
            "sorteo": s,
            "tipo_label": _TIPO_LABEL_PLURAL.get(s.tipo.value, s.tipo.value).title(),
            "cifras_label": " y ".join(
                str(c) for c in sorted(
                    [int(c) for c in str(s.cifras).split(",")], reverse=True
                )
            ),
            "numeros_ganadores": [str(n).zfill(4) for n in numeros],
            "cifras_max": cifras_max,
            "ganadores": ganadores,
            "total_ganadores": len(ganadores),
            "total_habilitados": sum(1 for g in ganadores if g["habilitado"]),
        })
        total_ganadores_mes += len(ganadores)

    # ── Resumen de premios del mes (pie del informe, 30/08/2026) ─────────────
    # Cuantos ganadores hubo por nivel de cifras, cuanto cobra cada uno y cuanto
    # sale en total. Se calcula desde las ENTREGAS ASIGNADAS, no desde los premios
    # cargados: asi el numero coincide exactamente con lo que suma Contabilidad
    # (contabilidad.py solo cuenta premios ORDEN con ganador asignado) y refleja
    # lo que de verdad se va a pagar.
    #
    # El nivel es el `cifras_efectivas` que muestra la tabla de arriba (respeta el
    # override manual "B"), para que el resumen no contradiga lo que se ve.
    # Nivel de cifras de cada ganador habilitado, por (sorteo, boleta)
    _nivel = {}
    _sin_premio = 0
    for _b in bloques:
        _sid = _b["sorteo"].id
        for _g in _b["ganadores"]:
            if not _g["habilitado"]:
                continue
            _nivel[(_sid, _g["boleta_id"])] = _g["cifras_efectivas"]
            if not _g["entrega_id"]:
                _sin_premio += 1

    _res = {}
    for _e in _todas_entregas:
        _cif = _nivel.get((_e.premio.sorteo_id, _e.boleta_id))
        if _cif is None:      # ganador no habilitado: no se le paga
            continue
        _pr = _e.premio
        _fila = _res.setdefault((_cif, _pr.id), {
            "cifras": _cif,
            "descripcion": _pr.descripcion,
            "clase": _pr.clase,
            "monto": float(_pr.monto or 0),
            "cantidad": 0,
        })
        _fila["cantidad"] += 1
    resumen_premios = sorted(_res.values(), key=lambda f: (-f["cifras"], -f["monto"]))
    for _f in resumen_premios:
        _f["subtotal"] = _f["monto"] * _f["cantidad"]
    # Los FISICO (moto, TV, bici) NO suman al total en $: ese monto es de
    # referencia y el gasto real se carga a mano al comprarlos (mismo criterio
    # que usa contabilidad.py). Se muestran igual, con su aclaracion.
    resumen_totales = {
        "premios": sum(f["cantidad"] for f in resumen_premios),
        "ganadores": sum(b["total_habilitados"] for b in bloques),
        "sin_premio": _sin_premio,
        "total_orden": sum(f["subtotal"] for f in resumen_premios if f["clase"] == "ORDEN"),
        "fisicos": sum(f["cantidad"] for f in resumen_premios if f["clase"] != "ORDEN"),
    }

    return templates.TemplateResponse(request, "sorteo_informe.html", {
        "user": user,
        "year": year,
        "month": month,
        "mes_label": mes_label,
        "bloques": bloques,
        "total_ganadores_mes": total_ganadores_mes,
        "resumen_premios": resumen_premios,
        "resumen_totales": resumen_totales,
        "vacio": len(sorteos) == 0,
        # Un boton por cobrador con todos sus recibos del MES (todos los sorteos)
        "recibos_por_cobrador": _entregas_de_sorteos(sorteos, db),
    })


def _vista_redirect(vista: str, year: int, month: int) -> str:
    """Devuelve la URL de la vista desde la que se editó (informe o extracto)."""
    vista = vista if vista in ("informe", "extracto") else "informe"
    return f"/sorteos/{vista}/{year}/{month}"


@router.post("/habilitar")
async def habilitar_manual(
    request: Request,
    sorteo_id: int = Form(...),
    boleta_id: int = Form(...),
    habilitado: int = Form(1),
    motivo: str = Form(""),
    year: int = Form(...),
    month: int = Form(...),
    vista: str = Form("informe"),
    db: Session = Depends(get_db),
):
    """Crea/actualiza un override manual de habilitación para un ganador (excepción).

    Se usa para habilitar (o deshabilitar) a mano un ganador desde el informe
    o el extracto.
    """
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'sorteos', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')

    over = db.query(models.HabilitacionSorteo).filter(
        models.HabilitacionSorteo.sorteo_id == sorteo_id,
        models.HabilitacionSorteo.boleta_id == boleta_id,
    ).first()
    if over is None:
        over = models.HabilitacionSorteo(sorteo_id=sorteo_id, boleta_id=boleta_id)
        db.add(over)
    over.habilitado = bool(habilitado)
    over.motivo = (motivo or "").strip() or None
    db.commit()

    # Al habilitar a mano, el ganador recien entra como candidato: se le crea la
    # entrega y le queda el recibo. Al DESHABILITAR no se borra la entrega que ya
    # exista (puede estar marcada como entregada); el recibo igual se niega a
    # emitirse mientras no este habilitado.
    _reasignar_si_corresponde(db.query(models.Sorteo).get(sorteo_id), db)

    return RedirectResponse(_vista_redirect(vista, year, month), status_code=302)


@router.post("/habilitar/quitar")
async def habilitar_quitar(
    request: Request,
    sorteo_id: int = Form(...),
    boleta_id: int = Form(...),
    year: int = Form(...),
    month: int = Form(...),
    vista: str = Form("informe"),
    db: Session = Depends(get_db),
):
    """Quita el override manual y vuelve al cálculo automático de habilitación."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'sorteos', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')

    db.query(models.HabilitacionSorteo).filter(
        models.HabilitacionSorteo.sorteo_id == sorteo_id,
        models.HabilitacionSorteo.boleta_id == boleta_id,
    ).delete()
    db.commit()

    # Volver al automatico puede dejar habilitado a alguien que estaba forzado en
    # NO: se recalcula igual que arriba.
    _reasignar_si_corresponde(db.query(models.Sorteo).get(sorteo_id), db)

    return RedirectResponse(_vista_redirect(vista, year, month), status_code=302)


@router.post("/cifras")
async def cifras_manual(
    request: Request,
    sorteo_id: int = Form(...),
    boleta_id: int = Form(...),
    cifras: int = Form(...),
    motivo: str = Form(""),
    year: int = Form(...),
    month: int = Form(...),
    vista: str = Form("informe"),
    db: Session = Depends(get_db),
):
    """Fuerza a mano el NIVEL DE CIFRAS de un ganador (excepción).

    El nivel se calcula solo al cruzar los números; esto es para los casos raros
    que salen mal clasificados. Del nivel depende que el ganador salga en negrita
    en el extracto impreso (los "mayores"). No toca la habilitación.
    """
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'sorteos', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    if cifras not in (2, 3, 4):
        raise HTTPException(400, 'Cifras inválidas (2, 3 o 4)')

    over = db.query(models.CifrasGanadorOverride).filter(
        models.CifrasGanadorOverride.sorteo_id == sorteo_id,
        models.CifrasGanadorOverride.boleta_id == boleta_id,
    ).first()
    if over is None:
        over = models.CifrasGanadorOverride(sorteo_id=sorteo_id, boleta_id=boleta_id)
        db.add(over)
    over.cifras = int(cifras)
    over.motivo = (motivo or "").strip() or None
    db.commit()

    return RedirectResponse(_vista_redirect(vista, year, month), status_code=302)


@router.post("/cifras/quitar")
async def cifras_quitar(
    request: Request,
    sorteo_id: int = Form(...),
    boleta_id: int = Form(...),
    year: int = Form(...),
    month: int = Form(...),
    vista: str = Form("informe"),
    db: Session = Depends(get_db),
):
    """Quita el override y vuelve al nivel de cifras calculado automáticamente."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'sorteos', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')

    db.query(models.CifrasGanadorOverride).filter(
        models.CifrasGanadorOverride.sorteo_id == sorteo_id,
        models.CifrasGanadorOverride.boleta_id == boleta_id,
    ).delete()
    db.commit()

    return RedirectResponse(_vista_redirect(vista, year, month), status_code=302)


def _habilitacion_boleta(boleta, sorteo, override=None):
    """Determina si un ganador está HABILITADO para cobrar el premio.

    Regla (definida con Sergio): un número queda habilitado para el sorteo de un
    mes si se cumple AL MENOS UNA de estas condiciones:
      1) Pagó al menos una cuota en el mes del sorteo (aunque deba meses
         anteriores) → figura en `historial_cuotas` un pago con mes == mes sorteo.
      2) Se vendió en el mismo mes del sorteo, hasta el día del sorteo
         inclusive (comprar el día del sorteo cuenta — regla del 24/08/2026).

    Un `override` (fila HabilitacionSorteo) reemplaza el cálculo automático por
    excepción manual.

    Devuelve (habilitado: bool, motivo: str, manual: bool).
    """
    mes_sorteo = sorteo.fecha.month

    # 1) Pago alguna cuota en el mes/ANIO del sorteo. match_periodo entiende el
    # formato nuevo "YYYY-MM" y el legacy (mes suelto); con el legacy matchea
    # solo por mes, igual que antes (fix C-1: julio 2026 != julio 2027).
    pago_mes = False
    if boleta.historial_cuotas:
        try:
            _hist = json.loads(boleta.historial_cuotas)
            pago_mes = any(
                match_periodo(v, sorteo.fecha.year, mes_sorteo)
                for v in _hist.values()
            )
        except Exception:
            pago_mes = False

    # 2) Vendida en el mismo mes/año del sorteo, hasta el día del sorteo inclusive
    vendida_en_mes = (
        boleta.fecha_venta is not None
        and boleta.fecha_venta.year == sorteo.fecha.year
        and boleta.fecha_venta.month == mes_sorteo
        and boleta.fecha_venta <= sorteo.fecha
    )

    if pago_mes:
        auto_ok, auto_motivo = True, "Pagó cuota del mes"
    elif vendida_en_mes:
        auto_ok, auto_motivo = True, "Vendida en el mes (hasta el día del sorteo)"
    else:
        auto_ok, auto_motivo = False, "No registra pago en el mes del sorteo"

    # Override manual (excepción) tiene prioridad
    if override is not None:
        if override.habilitado:
            motivo = override.motivo or "Habilitado manualmente (excepción)"
        else:
            motivo = override.motivo or "Deshabilitado manualmente"
        return bool(override.habilitado), motivo, True

    return auto_ok, auto_motivo, False


def _boleta_habilitada(boleta, sorteo, db):
    """Habilitación de una boleta concreta en un sorteo, cargando su override.

    Devuelve (habilitado: bool, motivo: str, manual: bool).
    """
    over = db.query(models.HabilitacionSorteo).filter(
        models.HabilitacionSorteo.sorteo_id == sorteo.id,
        models.HabilitacionSorteo.boleta_id == boleta.id,
    ).first()
    return _habilitacion_boleta(boleta, sorteo, over)


def _numeros_boleta(b) -> List[int]:
    """Todos los números jugables de una boleta: el principal + los adicionales."""
    nums = [b.numero_principal]
    if b.numeros_adicionales:
        for x in b.numeros_adicionales.split(","):
            x = x.strip()
            if x.isdigit():
                nums.append(int(x))
    return nums


def _build_ganador(b, numero_match: str, cifras_match: int, sorteo, override=None,
                   cifras_override=None) -> dict:
    nombre = (b.comprador.apellido_nombre or "").strip().upper() if b.comprador else ""
    direccion = (b.comprador.direccion or "").strip().upper() if b.comprador else ""
    # Misma habilitación que el informe (control de pago del mes / venta en el mes)
    habilitado, hab_motivo, hab_manual = _habilitacion_boleta(b, sorteo, override)
    # Nivel de cifras: automático salvo excepción manual (CifrasGanadorOverride).
    # El extracto lo usa para poner en negrita a los ganadores del nivel más alto.
    cifras_auto = cifras_match
    cifras_manual = cifras_override is not None
    if cifras_manual:
        cifras_match = int(cifras_override.cifras)
    return {
        "boleta_id": b.id,
        "sorteo_id": sorteo.id,
        "nombre": nombre,
        "direccion": direccion,
        "numero_match": numero_match,
        "cifras_match": cifras_match,
        "cifras_auto": cifras_auto,
        "cifras_manual": cifras_manual,
        "cifras_motivo": (cifras_override.motivo if cifras_manual else None),
        "fecha_sorteo": sorteo.fecha,
        "habilitado": habilitado,
        "habilitado_motivo": hab_motivo,
        "habilitado_manual": hab_manual,
    }


def _sabados_entre(desde: date_type, hasta: date_type) -> List[date_type]:
    """Devuelve todos los sábados entre dos fechas inclusive."""
    fechas = []
    d = desde
    # avanzar al primer sábado (weekday 5)
    while d.weekday() != 5:
        d += timedelta(days=1)
    while d <= hasta:
        fechas.append(d)
        d += timedelta(days=7)
    return fechas


@router.post("/crear")
async def crear(
    request: Request,
    nombre: str = Form(""),
    tipo: str = Form(...),
    cifras: List[str] = Form(...),
    fecha: Optional[str] = Form(None),
    fecha_desde: Optional[str] = Form(None),
    fecha_hasta: Optional[str] = Form(None),
    num_premios: int = Form(20),
    db: Session = Depends(get_db)
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'sorteos', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    cifras_str = ",".join(sorted(set(cifras), key=lambda x: int(x)))
    num_p = max(1, min(20, num_premios))

    # ── Premios por nivel de cifras, cargados de una en el alta (03/08/2026) ──
    # En los semanales el premio es SIEMPRE el mismo (4 cifras tanto, 3 cifras
    # tanto), así que se definen acá una sola vez y cada sorteo del rango nace
    # con los suyos. Antes había que entrar sorteo por sorteo a cargarlos, y
    # hasta que no hubiera un premio no se podía emitir ningún recibo.
    # El form manda `premio_monto_4`, `premio_desc_4`, `premio_monto_3`, ...
    _form = await request.form()
    premios_cfg = []
    for _c in sorted({int(x) for x in cifras}, reverse=True):
        try:
            _monto = float(_form.get(f"premio_monto_{_c}") or 0)
        except (ValueError, TypeError):
            _monto = 0.0
        if _monto <= 0:
            continue   # sin monto no se crea premio: ese nivel no paga
        _desc = (_form.get(f"premio_desc_{_c}") or "").strip() or f"Premio a {_c} cifras"
        premios_cfg.append({"cifras": _c, "monto": _monto, "descripcion": _desc})

    def _crear_premios(sorteo):
        """Crea los premios POR_CIFRAS de un sorteo recién creado."""
        for i, cfg in enumerate(premios_cfg, start=1):
            db.add(models.PremioSorteo(
                sorteo_id=sorteo.id,
                orden=i,
                descripcion=cfg["descripcion"],
                clase="ORDEN",
                monto=cfg["monto"],
                modalidad="POR_CIFRAS",
                cifras=cfg["cifras"],
            ))

    # Semanal con rango → crear uno por cada sábado
    if tipo == "SEMANAL" and fecha_desde and fecha_hasta:
        d_desde = date_type.fromisoformat(fecha_desde)
        d_hasta = date_type.fromisoformat(fecha_hasta)
        sabados = _sabados_entre(d_desde, d_hasta)
        nuevos = []
        for sab in sabados:
            s = models.Sorteo(
                nombre=nombre.strip() or None,
                tipo=models.TipoSorteo(tipo),
                cifras=cifras_str,
                fecha=sab,
                num_premios=num_p,
            )
            db.add(s)
            nuevos.append(s)
        db.flush()          # asigna los id sin cerrar la transacción
        for s in nuevos:
            _crear_premios(s)
        db.commit()
    else:
        # Mensual, Final o Semanal con fecha única
        fecha_val = fecha or fecha_desde
        if not fecha_val:
            return RedirectResponse("/sorteos/", status_code=302)
        s = models.Sorteo(
            nombre=nombre.strip() or None,
            tipo=models.TipoSorteo(tipo),
            cifras=cifras_str,
            fecha=date_type.fromisoformat(fecha_val),
            num_premios=num_p,
        )
        db.add(s)
        db.flush()
        _crear_premios(s)
        db.commit()

    return RedirectResponse("/sorteos/", status_code=302)


@router.post("/{sid}/editar")
async def editar(
    sid: int,
    request: Request,
    nombre: str = Form(""),
    descripcion: str = Form(""),
    tipo: str = Form(...),
    cifras: List[str] = Form(...),
    fecha: str = Form(...),
    num_premios: int = Form(20),
    db: Session = Depends(get_db)
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'sorteos', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    s = db.query(models.Sorteo).get(sid)
    if not s:
        return RedirectResponse("/sorteos/", status_code=302)

    s.nombre = nombre.strip() or None
    s.descripcion = descripcion.strip() or None
    s.tipo = models.TipoSorteo(tipo)
    s.cifras = ",".join(sorted(set(cifras), key=lambda x: int(x)))
    s.fecha = date_type.fromisoformat(fecha)
    s.num_premios = max(1, min(20, num_premios))
    db.commit()
    return RedirectResponse("/sorteos/", status_code=302)


@router.post("/{sid}/eliminar")
async def eliminar(sid: int, request: Request, db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'sorteos', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    s = db.query(models.Sorteo).get(sid)
    if s:
        db.delete(s)
        db.commit()
    return RedirectResponse("/sorteos/", status_code=302)


# ── Premios del sorteo ───────────────────────────────────────────────────────

_CLASES_PREMIO = {"ORDEN", "FISICO"}
_MODALIDADES_PREMIO = {"POSICION", "CADA_UNO", "POR_CIFRAS"}


def _candidatos_del_premio(premio, candidatos):
    """Filtra los ganadores que le corresponden a un premio.

    Solo POR_CIFRAS filtra: se queda con los que pegaron EXACTAMENTE el nivel de
    cifras del premio (en un semanal a 4 y 3, el premio de 4 no le toca al que
    pego 3). POSICION y CADA_UNO ven todos los candidatos, como siempre.
    """
    if (premio.modalidad or "") != "POR_CIFRAS" or not premio.cifras:
        return candidatos
    return [c for c in candidatos if int(c.get("cifras") or 0) == int(premio.cifras)]


@router.get("/{sid}/premios", response_class=HTMLResponse)
async def premios_form(sid: int, request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'sorteos', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    s = db.query(models.Sorteo).get(sid)
    if not s:
        return RedirectResponse("/sorteos/", status_code=302)
    # Niveles de cifras que juega este sorteo (ej: [4, 3]) — el selector de la
    # modalidad POR_CIFRAS solo ofrece estos, no un 2 que este sorteo no juega.
    try:
        cifras_sorteo = sorted({int(c) for c in str(s.cifras).split(",") if str(c).strip()},
                               reverse=True)
    except (ValueError, TypeError):
        cifras_sorteo = []
    return templates.TemplateResponse(request, "sorteo_premios.html", {
        "user": user,
        "sorteo": s,
        "premios": s.premios,
        "cifras_sorteo": cifras_sorteo,
        "tipo_label": _TIPO_LABEL_PLURAL.get(s.tipo.value, s.tipo.value),
    })


@router.post("/{sid}/premios")
async def premio_crear(
    sid: int,
    request: Request,
    descripcion: str = Form(...),
    clase: str = Form("ORDEN"),
    monto: float = Form(0.0),
    modalidad: str = Form("POSICION"),
    cifras: int = Form(0),
    orden: int = Form(0),
    db: Session = Depends(get_db),
):
    _perm = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm, 'sorteos', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    s = db.query(models.Sorteo).get(sid)
    if not s:
        return RedirectResponse("/sorteos/", status_code=302)
    desc = descripcion.strip()
    if not desc:
        return RedirectResponse(f"/sorteos/{sid}/premios", status_code=302)
    clase = clase if clase in _CLASES_PREMIO else "ORDEN"
    modalidad = modalidad if modalidad in _MODALIDADES_PREMIO else "POSICION"
    # orden por defecto = siguiente disponible
    if not orden or orden < 1:
        orden = (max([p.orden for p in s.premios], default=0) + 1)
    p = models.PremioSorteo(
        sorteo_id=sid,
        orden=orden,
        descripcion=desc,
        clase=clase,
        monto=max(0.0, monto),
        modalidad=modalidad,
        # El nivel de cifras solo tiene sentido en POR_CIFRAS; en el resto queda NULL
        # para no dejar datos que confundan si después se cambia la modalidad.
        cifras=(int(cifras) if modalidad == "POR_CIFRAS" and cifras in (2, 3, 4) else None),
    )
    db.add(p)
    db.commit()
    # Si el sorteo ya tenia el resultado cargado, este premio recien creado deja
    # a sus ganadores listos con recibo sin tener que volver a guardar el resultado.
    _reasignar_si_corresponde(s, db)
    return RedirectResponse(f"/sorteos/{sid}/premios", status_code=302)


@router.post("/premios/{pid}/editar")
async def premio_editar(
    pid: int,
    request: Request,
    descripcion: str = Form(...),
    clase: str = Form("ORDEN"),
    monto: float = Form(0.0),
    modalidad: str = Form("POSICION"),
    cifras: int = Form(0),
    orden: int = Form(1),
    db: Session = Depends(get_db),
):
    _perm = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm, 'sorteos', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    p = db.query(models.PremioSorteo).get(pid)
    if not p:
        return RedirectResponse("/sorteos/", status_code=302)
    desc = descripcion.strip()
    if desc:
        p.descripcion = desc
    p.clase = clase if clase in _CLASES_PREMIO else "ORDEN"
    p.modalidad = modalidad if modalidad in _MODALIDADES_PREMIO else "POSICION"
    # Se limpia el nivel si el premio deja de ser POR_CIFRAS (ver premio_crear)
    p.cifras = (int(cifras) if p.modalidad == "POR_CIFRAS" and cifras in (2, 3, 4) else None)
    p.monto = max(0.0, monto)
    p.orden = max(1, orden)
    db.commit()
    # Cambiar la modalidad o el nivel de cifras cambia a quien le toca este premio.
    _reasignar_si_corresponde(p.sorteo, db)
    return RedirectResponse(f"/sorteos/{p.sorteo_id}/premios", status_code=302)


@router.post("/premios/{pid}/eliminar")
async def premio_eliminar(pid: int, request: Request, db: Session = Depends(get_db)):
    _perm = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm, 'sorteos', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    p = db.query(models.PremioSorteo).get(pid)
    if p:
        sid = p.sorteo_id
        db.delete(p)
        db.commit()
        return RedirectResponse(f"/sorteos/{sid}/premios", status_code=302)
    return RedirectResponse("/sorteos/", status_code=302)


def _calcular_grupos_ganadores(s, db):
    """Cruza el resultado del sorteo `s` con todas las boletas y arma los grupos
    de ganadores (uno por premio × cifra). Devuelve (grupos, cifras_list).

    Cada fila marca si es un ganador VÁLIDO: tiene socio (comprador) y la boleta
    se vendió HASTA EL DÍA DEL SORTEO inclusive (`fecha_venta <= s.fecha`).

    REGLA (24/08/2026): comprar el mismo día del sorteo SÍ da derecho a jugar —
    la venta es anterior al sorteo aunque caiga la misma fecha. Solo se marca
    `posterior` (y no cuenta) si se compró DESPUÉS del sorteo, o si no tiene
    fecha de venta cargada.
    """
    num_premios = s.num_premios or 20
    numeros_ganadores = json.loads(s.resultado_json)[:num_premios]  # respetar el límite del sorteo
    cifras_list = sorted([int(c) for c in str(s.cifras).split(",")], reverse=True)  # [4,3,2]

    # Cargar todas las boletas con sus relaciones
    boletas = db.query(models.Boleta).options(
        joinedload(models.Boleta.comprador),
        joinedload(models.Boleta.vendedor),
        joinedload(models.Boleta.cobrador),
        joinedload(models.Boleta.talonera),
    ).all()

    # Overrides manuales de habilitación para este sorteo (excepciones)
    overrides = {
        h.boleta_id: h
        for h in db.query(models.HabilitacionSorteo).filter(
            models.HabilitacionSorteo.sorteo_id == s.id
        ).all()
    }

    # Construir índice: suffix_map[n_cifras][sufijo] = [(boleta, numero_entero), ...]
    suffix_map = {n: {} for n in cifras_list}
    for boleta in boletas:
        for num in _numeros_boleta(boleta):
            num_str = str(num).zfill(4)
            for n in cifras_list:
                sufijo = num_str[-n:]
                suffix_map[n].setdefault(sufijo, []).append((boleta, num))

    # Regla: un número solo gana al nivel más alto que le corresponda.
    # Si ya ganó a 4 cifras, no puede aparecer también en 3 ni en 2.
    # Si ya ganó a 3 cifras, no puede aparecer en 2.
    # Rastreamos (boleta_id, num_match) que ya fueron adjudicados a un nivel superior.
    ya_adjudicados = set()   # (boleta_id, num_match)

    grupos = []
    for n in cifras_list:   # cifras desc (4 → 3 → 2)
        for pos, num_ganador in enumerate(numeros_ganadores, 1):
            num_str = str(num_ganador).zfill(4)
            sufijo = num_str[-n:]
            matches_raw = suffix_map[n].get(sufijo, [])

            vistos_boleta = set()
            filas = []
            for (boleta, num_match) in matches_raw:
                key = (boleta.id, num_match)
                if key in vistos_boleta:
                    continue
                # Si este número ya ganó a un nivel más alto, se excluye
                if key in ya_adjudicados:
                    continue
                vistos_boleta.add(key)

                otros = [str(x).zfill(4) for x in _numeros_boleta(boleta) if x != num_match]

                tiene_socio = boleta.comprador is not None
                # Ganador válido: tiene socio Y se vendió hasta el día del sorteo.
                es_valido = (
                    tiene_socio
                    and boleta.fecha_venta is not None
                    and boleta.fecha_venta <= s.fecha
                )
                # Tiene socio pero comprada DESPUÉS del sorteo (o sin fecha): no cuenta.
                posterior = tiene_socio and not es_valido

                # Habilitación para cobrar (control de pago del mes / venta en el mes)
                if es_valido:
                    habilitado, hab_motivo, hab_manual = _habilitacion_boleta(
                        boleta, s, overrides.get(boleta.id)
                    )
                else:
                    habilitado, hab_motivo, hab_manual = False, "Comprada después del sorteo", False

                filas.append({
                    "boleta_id": boleta.id,
                    "talonera": boleta.talonera.nombre if boleta.talonera else "—",
                    "talonera_id": boleta.talonera_id or 0,
                    "num_match": str(num_match).zfill(4),
                    "otros_numeros": otros,
                    "comprador": boleta.comprador.apellido_nombre if boleta.comprador else None,
                    "vendedor": boleta.vendedor.nombre if boleta.vendedor else None,
                    "cobrador": boleta.cobrador.nombre if boleta.cobrador else None,
                    "condicion": boleta.condicion.value if boleta.condicion else "SIN_VENDER",
                    "fecha_venta": boleta.fecha_venta.strftime("%d/%m/%Y") if boleta.fecha_venta else None,
                    "es_ganador_valido": es_valido,
                    "posterior": posterior,
                    "habilitado": habilitado,
                    "habilitado_motivo": hab_motivo,
                    "habilitado_manual": hab_manual,
                })

            # Marcar estos como adjudicados para que no aparezcan en niveles inferiores
            ya_adjudicados.update(vistos_boleta)

            filas.sort(key=lambda f: (f["talonera_id"], f["num_match"]))

            grupos.append({
                "posicion": pos,
                "cifras": n,
                "numero_ganador": num_str,
                "sufijo": sufijo,
                "filas": filas,
                # Ganadores REALES: socio cargado Y comprado antes del sorteo
                "ganadores_reales": sum(1 for f in filas if f["es_ganador_valido"]),
                "posteriores": sum(1 for f in filas if f["posterior"]),
            })

    return grupos, cifras_list


@router.get("/{sid}/ganadores", response_class=HTMLResponse)
async def ver_ganadores(sid: int, request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'sorteos', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    s = db.query(models.Sorteo).get(sid)

    if not s:
        return RedirectResponse("/sorteos/", status_code=302)

    if not s.resultado_json:
        return templates.TemplateResponse(request, "ganadores.html", {
            "user": user, "sorteo": s, "ganadores": [], "cifras_disponibles": []
        })

    grupos, cifras_list = _calcular_grupos_ganadores(s, db)

    return templates.TemplateResponse(request, "ganadores.html", {
        "user": user,
        "sorteo": s,
        "grupos": grupos,
        "cifras_disponibles": cifras_list,
        # Solo cuentan como ganadores las filas válidas (socio + comprada antes del sorteo)
        "total_ganadores": sum(g["ganadores_reales"] for g in grupos),
    })


@router.get("/{sid}/ganadores-json")
async def ganadores_json(sid: int, request: Request, db: Session = Depends(get_db)):
    """Resumen liviano de ganadores VÁLIDOS para el acordeón del listado de sorteos."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'sorteos', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    s = db.query(models.Sorteo).get(sid)
    if not s or not s.resultado_json:
        return {"ganadores": [], "total": 0}

    grupos, _ = _calcular_grupos_ganadores(s, db)
    ganadores = []
    for g in grupos:
        for f in g["filas"]:
            if f["es_ganador_valido"]:
                ganadores.append({
                    "nombre": f["comprador"],
                    "numero": f["num_match"],
                    "cifras": g["cifras"],
                    "posicion": g["posicion"],
                    "talonera": f["talonera"],
                    "condicion": f["condicion"],
                })
    # Ordenar por cifras desc, luego posición, luego nombre
    ganadores.sort(key=lambda x: (-x["cifras"], x["posicion"], x["nombre"] or ""))
    return {"ganadores": ganadores, "total": len(ganadores)}


class ResultadoPayload(BaseModel):
    numeros: List[str]  # lista de 20 strings de 4 dígitos


@router.post("/{sid}/guardar-resultado")
async def guardar_resultado(sid: int, payload: ResultadoPayload, request: Request, db: Session = Depends(get_db)):
    """Guarda los 20 números del resultado ingresados manualmente."""
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'sorteos', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    s = db.query(models.Sorteo).get(sid)
    if not s:
        return JSONResponse({"ok": False, "error": "Sorteo no encontrado"}, status_code=404)

    # Normalizar: cada número como string de 4 dígitos.
    # El modal manda exactamente s.num_premios números (con padStart a 4).
    nums_norm: List[str] = []
    for n in payload.numeros:
        n_str = str(n or "").strip()
        if n_str == "":
            n_str = "0000"
        if not n_str.isdigit():
            return JSONResponse(
                {"ok": False, "error": f"Número inválido: '{n}'"},
                status_code=400,
            )
        nums_norm.append(n_str.zfill(4))

    # Cantidad esperada según el sorteo
    num_premios = s.num_premios or 20
    if len(nums_norm) < num_premios:
        # padding con "0000" hasta llegar a num_premios (consistente con el modal)
        nums_norm += ["0000"] * (num_premios - len(nums_norm))
    elif len(nums_norm) > num_premios:
        nums_norm = nums_norm[:num_premios]

    # Validar que al menos el 1° premio esté cargado (mismo criterio que el JS)
    if all(n == "0000" for n in nums_norm):
        return JSONResponse(
            {"ok": False, "error": "Ingresá al menos un número distinto de 0000."},
            status_code=400,
        )

    try:
        s.resultado_json = json.dumps(nums_norm)
        db.commit()
    except Exception as e:
        db.rollback()
        return JSONResponse(
            {"ok": False, "error": f"Error al guardar en base: {e.__class__.__name__}"},
            status_code=500,
        )

    # ── Asignación automática de ganadores a sus premios (03/08/2026) ─────────
    # El sistema ya sabe quién ganó y con cuántas cifras: no tiene sentido pedirle
    # al operador que lo repita a mano. Apenas se carga el resultado, cada ganador
    # habilitado queda asignado al premio de SU nivel y el recibo queda listo.
    # Es revisable: se puede desasignar o reasignar desde Entregas como siempre.
    # Corre en un try aparte para que un problema acá NO invalide el resultado
    # que ya se guardó bien — es un extra, no parte del guardado.
    asignados = 0
    try:
        asignados = _autoasignar_entregas(s, db)
    except Exception as e:
        db.rollback()
        print(f"Autoasignación de entregas (sorteo {sid}): {e.__class__.__name__}: {e}")

    return JSONResponse({"ok": True, "numeros": nums_norm, "asignados": asignados})


def _reasignar_si_corresponde(s, db) -> int:
    """Vuelve a correr la autoasignacion de entregas para el sorteo `s`.

    La autoasignacion original solo corria al guardar el resultado, y arranca con
    `if not s.premios: return 0`. Consecuencia (detectada 30/08/2026): un sorteo
    cuyo resultado se guardo ANTES de cargarle los premios se quedaba sin
    entregas para siempre, y en el informe no aparecia el icono del recibo.
    Ahora tambien se dispara cuando cambian los premios o la habilitacion.

    Es idempotente (no duplica entregas ya cargadas) y va en try/except para que
    un problema aca NO tumbe la accion que la disparo: cargar el premio o
    habilitar al ganador tiene que quedar guardado igual.
    """
    if not s or not s.resultado_json:
        return 0
    try:
        return _autoasignar_entregas(s, db)
    except Exception as e:
        db.rollback()
        print(f"Reasignacion de entregas (sorteo {s.id}): {e.__class__.__name__}: {e}")
        return 0


def _autoasignar_entregas(s, db) -> int:
    """Asigna cada ganador habilitado al premio que le corresponde. Devuelve cuántos.

    - POR_CIFRAS → solo los ganadores de ese nivel de cifras.
    - CADA_UNO   → todos los ganadores.
    - POSICION   → NO se toca: es un premio de un único ganador (FINAL 1°/2°/3°)
      y elegir cuál es una decisión humana.

    Idempotente: no duplica entregas ya cargadas, así que se puede volver a
    guardar el resultado sin ensuciar nada.
    """
    if not s.premios:
        return 0
    candidatos = _candidatos_ganadores(s, db)
    if not candidatos:
        return 0
    nuevos = 0
    for p in s.premios:
        if (p.modalidad or "POSICION") not in ("POR_CIFRAS", "CADA_UNO"):
            continue
        ya = {(e.boleta_id, e.numero_ganador) for e in p.entregas}
        for c in _candidatos_del_premio(p, candidatos):
            if (c["boleta_id"], c["numero"]) in ya:
                continue
            db.add(models.EntregaPremio(
                premio_id=p.id, boleta_id=c["boleta_id"], numero_ganador=c["numero"]))
            nuevos += 1
    if nuevos:
        db.commit()
    return nuevos


# ── Entregas de premios + recibos ────────────────────────────────────────────

# Datos fijos de la institución para el recibo.
INSTITUCION_NOMBRE = "Asociación de Bomberos Voluntarios de Concepción del Uruguay"


def _candidatos_ganadores(s, db):
    """Lista plana de ganadores HABILITADOS del sorteo, lista para asignar a premios.

    Solo incluye ganadores habilitados para cobrar (pagaron la cuota del mes o se
    vendieron en el mes antes del sorteo, o habilitados a mano por excepción). Los
    NO habilitados no son candidatos a premio / recibo.

    Cada candidato: {boleta_id, numero, socio, talonera, cifras, posicion}.
    """
    if not s.resultado_json:
        return []
    grupos, _ = _calcular_grupos_ganadores(s, db)
    cand, vistos = [], set()
    for g in grupos:
        for f in g["filas"]:
            if not f["habilitado"]:
                continue
            key = (f["boleta_id"], f["num_match"])
            if key in vistos:
                continue
            vistos.add(key)
            cand.append({
                "boleta_id": f["boleta_id"],
                "numero": f["num_match"],
                "socio": f["comprador"],
                "talonera": f["talonera"],
                "cifras": g["cifras"],
                "posicion": g["posicion"],
            })
    cand.sort(key=lambda c: (-c["cifras"], c["posicion"], c["socio"] or ""))
    return cand


@router.get("/{sid}/entregas", response_class=HTMLResponse)
async def entregas_form(sid: int, request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'sorteos', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    s = db.query(models.Sorteo).get(sid)
    if not s:
        return RedirectResponse("/sorteos/", status_code=302)

    candidatos = _candidatos_ganadores(s, db)

    premios_view = []
    for p in s.premios:
        ya = set()
        entregas = []
        for e in p.entregas:
            b = e.boleta
            c = b.comprador if b else None
            # Columna Cobrador: quién le cobra a este ganador, para saber a quién
            # mandarle el recibo. Si la boleta es al contado no hay cobrador →
            # se muestra "Contado". Mismo criterio que el badge de Socios:
            # tiene número especial (slot 1 o 2) O entró pagando todo de entrada
            # (cuotas_anticipadas >= cuotas_pactadas) — los especiales se cargan
            # recién cuando se agota el talonario de 5, así que puede no haberlo aún.
            _contado = False
            _cobrador = ""
            if b is not None:
                _pact = b.cuotas_pactadas or 0
                _contado = bool(
                    b.numero_especial or b.numero_especial_2
                    or (_pact > 0 and (b.cuotas_anticipadas or 0) >= _pact)
                )
                _cobrador = b.cobrador.nombre if b.cobrador else ""
            entregas.append({
                "id": e.id,
                "socio": c.apellido_nombre if c else "—",
                "talonera": b.talonera.nombre if (b and b.talonera) else "—",
                "numero": e.numero_ganador or "",
                "entregado": e.entregado,
                "fecha_entrega": e.fecha_entrega.strftime("%d/%m/%Y") if e.fecha_entrega else "",
                "contado": _contado,
                "cobrador": _cobrador,
            })
            ya.add((e.boleta_id, e.numero_ganador))
        # POR_CIFRAS: el desplegable solo ofrece los ganadores del nivel del premio,
        # así no se puede asignar por error el premio de 4 cifras al que pegó 3.
        _del_premio = _candidatos_del_premio(p, candidatos)
        disponibles = [c for c in _del_premio if (c["boleta_id"], c["numero"]) not in ya]
        premios_view.append({"premio": p, "entregas": entregas, "candidatos": disponibles})

    return templates.TemplateResponse(request, "sorteo_entregas.html", {
        "user": user,
        "sorteo": s,
        "premios_view": premios_view,
        "tipo_label": _TIPO_LABEL_PLURAL.get(s.tipo.value, s.tipo.value),
        "total_candidatos": len(candidatos),
        "con_resultado": bool(s.resultado_json),
        "hoy": date_type.today().isoformat(),
        # Un boton por cobrador para bajar TODOS sus recibos en un PDF (30/08/2026)
        "recibos_por_cobrador": _entregas_de_sorteos([s], db),
    })


@router.post("/premios/{pid}/asignar")
async def premio_asignar(pid: int, request: Request,
                         seleccion: str = Form(...), db: Session = Depends(get_db)):
    _perm = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm, 'sorteos', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    p = db.query(models.PremioSorteo).get(pid)
    if not p:
        return RedirectResponse("/sorteos/", status_code=302)
    # seleccion = "boleta_id|numero"
    try:
        bid_str, numero = seleccion.split("|", 1)
        bid = int(bid_str)
    except (ValueError, AttributeError):
        return RedirectResponse(f"/sorteos/{p.sorteo_id}/entregas", status_code=302)
    # Defensa: no asignar premio a una boleta NO habilitada para cobrar
    boleta = db.query(models.Boleta).get(bid)
    if boleta is not None:
        habilitado, _m, _man = _boleta_habilitada(boleta, p.sorteo, db)
        if not habilitado:
            return RedirectResponse(f"/sorteos/{p.sorteo_id}/entregas", status_code=302)
    ya = db.query(models.EntregaPremio).filter_by(
        premio_id=pid, boleta_id=bid, numero_ganador=numero).first()
    if not ya:
        db.add(models.EntregaPremio(premio_id=pid, boleta_id=bid, numero_ganador=numero))
        db.commit()
    return RedirectResponse(f"/sorteos/{p.sorteo_id}/entregas", status_code=302)


@router.post("/premios/{pid}/asignar-todos")
async def premio_asignar_todos(pid: int, request: Request, db: Session = Depends(get_db)):
    """Asigna a este premio TODOS los ganadores válidos no asignados aún
    (útil para premios 'a cada uno')."""
    _perm = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm, 'sorteos', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    p = db.query(models.PremioSorteo).get(pid)
    if not p:
        return RedirectResponse("/sorteos/", status_code=302)
    # POR_CIFRAS: solo los ganadores del nivel de este premio (ver _candidatos_del_premio)
    candidatos = _candidatos_del_premio(p, _candidatos_ganadores(p.sorteo, db))
    ya = {(e.boleta_id, e.numero_ganador) for e in p.entregas}
    nuevos = 0
    for c in candidatos:
        if (c["boleta_id"], c["numero"]) in ya:
            continue
        db.add(models.EntregaPremio(
            premio_id=pid, boleta_id=c["boleta_id"], numero_ganador=c["numero"]))
        nuevos += 1
    if nuevos:
        db.commit()
    return RedirectResponse(f"/sorteos/{p.sorteo_id}/entregas", status_code=302)


@router.post("/entregas/{eid}/entregar")
async def entrega_marcar(eid: int, request: Request,
                         fecha_entrega: str = Form(""), db: Session = Depends(get_db)):
    _perm = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm, 'sorteos', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    e = db.query(models.EntregaPremio).get(eid)
    if not e:
        return RedirectResponse("/sorteos/", status_code=302)
    e.entregado = True
    try:
        e.fecha_entrega = date_type.fromisoformat(fecha_entrega) if fecha_entrega else date_type.today()
    except ValueError:
        e.fecha_entrega = date_type.today()
    sid = e.premio.sorteo_id
    db.commit()
    return RedirectResponse(f"/sorteos/{sid}/entregas", status_code=302)


@router.post("/entregas/{eid}/desmarcar")
async def entrega_desmarcar(eid: int, request: Request, db: Session = Depends(get_db)):
    _perm = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm, 'sorteos', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    e = db.query(models.EntregaPremio).get(eid)
    if not e:
        return RedirectResponse("/sorteos/", status_code=302)
    e.entregado = False
    e.fecha_entrega = None
    sid = e.premio.sorteo_id
    db.commit()
    return RedirectResponse(f"/sorteos/{sid}/entregas", status_code=302)


@router.post("/entregas/{eid}/eliminar")
async def entrega_eliminar(eid: int, request: Request, db: Session = Depends(get_db)):
    _perm = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm, 'sorteos', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    e = db.query(models.EntregaPremio).get(eid)
    if e:
        sid = e.premio.sorteo_id
        db.delete(e)
        db.commit()
        return RedirectResponse(f"/sorteos/{sid}/entregas", status_code=302)
    return RedirectResponse("/sorteos/", status_code=302)


def _ctx_recibo(eid: int, db, user):
    """Contexto del recibo de una entrega. Compartido por la vista HTML y el PDF."""
    e = db.query(models.EntregaPremio).get(eid)
    if not e:
        return None
    p = e.premio
    s = p.sorteo
    b = e.boleta
    c = b.comprador if b else None
    # Quien le entrega el premio al socio: el cobrador de la boleta (30/08/2026).
    # `nombre_recibo` arma "Cobradora Carolina Moyano" y cae al apodo si todavia
    # no se cargo el nombre completo en el ABM de cobradores.
    cob = b.cobrador if b else None
    entregado_por = ""
    if cob is not None:
        _art = "la" if (cob.tratamiento or "Cobrador") == "Cobradora" else "el"
        entregado_por = f"{_art} {cob.nombre_recibo}"
    # No se emite recibo de un ganador NO habilitado para cobrar
    habilitado, hab_motivo, _man = (True, "", False)
    if b is not None:
        habilitado, hab_motivo, _man = _boleta_habilitada(b, s, db)
    return {
        "user": user,
        "institucion": INSTITUCION_NOMBRE,
        "entrega": e,
        "premio": p,
        "sorteo": s,
        "tipo_label": _TIPO_LABEL_PLURAL.get(s.tipo.value, s.tipo.value),
        "socio": c,
        "boleta": b,
        "cobrador": cob,
        "entregado_por": entregado_por,
        "numero": e.numero_ganador or "",
        "fecha_entrega": e.fecha_entrega.strftime("%d/%m/%Y") if e.fecha_entrega else "",
        "habilitado": habilitado,
        "habilitado_motivo": hab_motivo,
    }


def _entregas_de_sorteos(sorteos, db):
    """Entregas asignadas de esos sorteos, agrupadas por cobrador de la boleta.

    Devuelve [{"cobrador": Cobrador|None, "cobrador_id": int, "nombre": str,
    "entregas": [EntregaPremio]}] ordenado por nombre. Los ganadores sin cobrador
    (contado, o boleta sin cobrador asignado) caen en un grupo con id 0 para que
    su recibo igual se pueda sacar.

    Solo entran los ganadores HABILITADOS: de los otros no se emite recibo.
    """
    if not sorteos:
        return []
    entregas = (db.query(models.EntregaPremio)
                .join(models.PremioSorteo,
                      models.EntregaPremio.premio_id == models.PremioSorteo.id)
                .filter(models.PremioSorteo.sorteo_id.in_([s.id for s in sorteos]))
                .all())
    por_sorteo = {s.id: s for s in sorteos}
    grupos = {}
    for e in entregas:
        b = e.boleta
        if b is None:
            continue
        s = por_sorteo.get(e.premio.sorteo_id)
        if s is None:
            continue
        hab, _m, _man = _boleta_habilitada(b, s, db)
        if not hab:
            continue
        cob = b.cobrador
        cid = cob.id if cob else 0
        g = grupos.setdefault(cid, {
            "cobrador": cob,
            "cobrador_id": cid,
            "nombre": cob.nombre if cob else "Sin cobrador",
            "entregas": [],
        })
        g["entregas"].append(e)
    salida = sorted(grupos.values(), key=lambda g: (g["cobrador_id"] == 0, g["nombre"]))
    for g in salida:
        g["entregas"].sort(key=lambda e: (e.premio.sorteo.fecha,
                                          (e.boleta.comprador.apellido_nombre
                                           if e.boleta and e.boleta.comprador else "")))
    return salida


def _pdf_recibos(sorteos, cid: int, db, user, request, nombre_archivo: str):
    """Arma el PDF con todos los recibos de un cobrador para esos sorteos."""
    grupos = _entregas_de_sorteos(sorteos, db)
    grupo = next((g for g in grupos if g["cobrador_id"] == cid), None)
    if grupo is None or not grupo["entregas"]:
        raise HTTPException(404, "Ese cobrador no tiene recibos para emitir acá")
    recibos = []
    for e in grupo["entregas"]:
        ctx = _ctx_recibo(e.id, db, user)
        if ctx and ctx["habilitado"]:
            recibos.append(ctx)
    if not recibos:
        raise HTTPException(404, "No hay recibos habilitados para emitir")
    html = templates.get_template("sorteo_recibo_pdf.html").render(
        request=request, recibos=recibos)
    return _pdf_response(html, nombre_archivo, "de los recibos")


@router.get("/{sid}/recibos/{cid}/pdf")
async def recibos_cobrador_sorteo_pdf(sid: int, cid: int, request: Request,
                                      db: Session = Depends(get_db)):
    """Todos los recibos de UN cobrador para UN sorteo, uno por hoja."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'sorteos', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    s = db.query(models.Sorteo).get(sid)
    if not s:
        raise HTTPException(404, "Sorteo no encontrado")
    cob = db.query(models.Cobrador).get(cid) if cid else None
    nombre = f"recibos_{(cob.nombre if cob else 'sin_cobrador')}_{s.fecha.strftime('%d-%m-%Y')}.pdf"
    return _pdf_recibos([s], cid, db, user, request, nombre)


@router.get("/recibos/{year}/{month}/{cid}/pdf")
async def recibos_cobrador_mes_pdf(year: int, month: int, cid: int, request: Request,
                                   db: Session = Depends(get_db)):
    """Todos los recibos de UN cobrador para TODOS los sorteos del mes."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'sorteos', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    if month < 1 or month > 12:
        raise HTTPException(400, 'Mes inválido')
    sorteos = db.query(models.Sorteo).filter(
        models.Sorteo.fecha >= date_type(year, month, 1),
        models.Sorteo.fecha <= _ultimo_dia_mes(year, month),
        models.Sorteo.resultado_json.isnot(None),
    ).order_by(models.Sorteo.fecha.asc()).all()
    cob = db.query(models.Cobrador).get(cid) if cid else None
    nombre = f"recibos_{(cob.nombre if cob else 'sin_cobrador')}_{_MESES_ES[month - 1].lower()}_{year}.pdf"
    return _pdf_recibos(sorteos, cid, db, user, request, nombre)


@router.get("/entregas/{eid}/recibo.pdf")
async def entrega_recibo_pdf(eid: int, request: Request, db: Session = Depends(get_db)):
    """Recibo en PDF, para mandárselo por WhatsApp al cobrador que lo entrega.

    Se genera con xhtml2pdf desde `sorteo_recibo_pdf.html` (sin toolbar ni CSS
    externo — pisa no ejecuta JS ni baja hojas de estilo).
    """
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'sorteos', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    ctx = _ctx_recibo(eid, db, user)
    if ctx is None:
        raise HTTPException(404, "Entrega no encontrada")
    if not ctx["habilitado"]:
        raise HTTPException(400, "El ganador no está habilitado para cobrar: no se emite recibo")

    # La plantilla siempre recibe una LISTA de recibos (el lote por cobrador usa
    # la misma): acá va uno solo.
    html = templates.get_template("sorteo_recibo_pdf.html").render(
        request=request, recibos=[ctx])
    buf = BytesIO()
    if pisa.CreatePDF(html, dest=buf, encoding="utf-8").err:
        raise HTTPException(500, "No se pudo generar el PDF del recibo")

    socio = ctx["socio"]
    _quien = (socio.apellido_nombre if socio else "ganador")
    nombre = f"recibo_{_quien}_{ctx['numero']}.pdf"
    # Se limpia el nombre: acentos/espacios/barras rompen el Content-Disposition
    nombre = re.sub(r"[^A-Za-z0-9_.-]", "_", nombre)
    return Response(
        content=buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@router.get("/entregas/{eid}/recibo", response_class=HTMLResponse)
async def entrega_recibo(eid: int, request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'sorteos', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    # Usa el MISMO contexto que el PDF (antes lo armaba a mano y las dos copias
    # se podian desfasar — de hecho el cobrador iba a faltar en una de las dos).
    ctx = _ctx_recibo(eid, db, user)
    if ctx is None:
        return RedirectResponse("/sorteos/", status_code=302)
    return templates.TemplateResponse(request, "sorteo_recibo.html", ctx)