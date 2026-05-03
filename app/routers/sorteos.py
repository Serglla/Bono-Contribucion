from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from typing import Optional, List
from datetime import date as date_type, timedelta
from pydantic import BaseModel
import json

from sqlalchemy.orm import Session, joinedload
from .. import models, auth as auth_module
from ..templates_config import templates
from ..database import get_db
from ..scraper import buscar_resultado_tombola

router = APIRouter(prefix="/sorteos", tags=["sorteos"])


@router.get("/", response_class=HTMLResponse)
async def listar(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    sorteos = db.query(models.Sorteo).order_by(models.Sorteo.fecha.desc()).all()

    # Último sorteo por tipo (el primero en la lista desc ya es el más reciente)
    ultima_por_tipo: dict = {}
    for s in sorteos:
        tipo = s.tipo.value
        if tipo not in ultima_por_tipo:
            ultima_por_tipo[tipo] = {
                "fecha": s.fecha.isoformat(),
                "num_premios": s.num_premios or 20,
            }

    return templates.TemplateResponse(request, "sorteos.html", {
        "user": user,
        "sorteos": sorteos,
        "ultima_por_tipo": ultima_por_tipo,
    })


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
    await auth_module.require_user(request, db)
    cifras_str = ",".join(sorted(set(cifras), key=lambda x: int(x)))
    num_p = max(1, min(20, num_premios))

    # Semanal con rango → crear uno por cada sábado
    if tipo == "SEMANAL" and fecha_desde and fecha_hasta:
        d_desde = date_type.fromisoformat(fecha_desde)
        d_hasta = date_type.fromisoformat(fecha_hasta)
        sabados = _sabados_entre(d_desde, d_hasta)
        for sab in sabados:
            s = models.Sorteo(
                nombre=nombre.strip() or None,
                tipo=models.TipoSorteo(tipo),
                cifras=cifras_str,
                fecha=sab,
                num_premios=num_p,
            )
            db.add(s)
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
        db.commit()

    return RedirectResponse("/sorteos/", status_code=302)


@router.post("/{sid}/eliminar")
async def eliminar(sid: int, request: Request, db: Session = Depends(get_db)):
    await auth_module.require_user(request, db)
    s = db.query(models.Sorteo).get(sid)
    if s:
        db.delete(s)
        db.commit()
    return RedirectResponse("/sorteos/", status_code=302)


@router.get("/{sid}/ganadores", response_class=HTMLResponse)
async def ver_ganadores(sid: int, request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    s = db.query(models.Sorteo).get(sid)

    if not s:
        return RedirectResponse("/sorteos/", status_code=302)

    if not s.resultado_json:
        return templates.TemplateResponse(request, "ganadores.html", {
            "user": user, "sorteo": s, "ganadores": [], "cifras_disponibles": []
        })

    num_premios = s.num_premios or 20
    numeros_ganadores = json.loads(s.resultado_json)[:num_premios]  # respetar el límite del sorteo
    cifras_list = sorted([int(c) for c in s.cifras.split(",")], reverse=True)  # [4,3,2]

    # Cargar todas las boletas con sus relaciones
    boletas = db.query(models.Boleta).options(
        joinedload(models.Boleta.comprador),
        joinedload(models.Boleta.vendedor),
        joinedload(models.Boleta.cobrador),
        joinedload(models.Boleta.talonera),
    ).all()

    # Construir índice: suffix_map[n_cifras][sufijo] = [(boleta, numero_entero), ...]
    suffix_map = {n: {} for n in cifras_list}
    for boleta in boletas:
        todos = [boleta.numero_principal]
        if boleta.numeros_adicionales:
            for x in boleta.numeros_adicionales.split(", "):
                if x.isdigit():
                    todos.append(int(x))
        for num in todos:
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

                # Separar: número que matcheó vs. otros números de la misma boleta
                todos_nums = [boleta.numero_principal]
                if boleta.numeros_adicionales:
                    for x in boleta.numeros_adicionales.split(","):
                        x = x.strip()
                        if x.isdigit():
                            todos_nums.append(int(x))

                otros = [str(x).zfill(4) for x in todos_nums if x != num_match]

                filas.append({
                    "talonera": boleta.talonera.nombre if boleta.talonera else "—",
                    "talonera_id": boleta.talonera_id or 0,
                    "num_match": str(num_match).zfill(4),
                    "otros_numeros": otros,
                    "comprador": boleta.comprador.apellido_nombre if boleta.comprador else None,
                    "vendedor": boleta.vendedor.nombre if boleta.vendedor else None,
                    "cobrador": boleta.cobrador.nombre if boleta.cobrador else None,
                    "condicion": boleta.condicion.value if boleta.condicion else "SIN_VENDER",
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
            })

    return templates.TemplateResponse(request, "ganadores.html", {
        "user": user,
        "sorteo": s,
        "grupos": grupos,
        "cifras_disponibles": cifras_list,
        "total_ganadores": sum(len(g["filas"]) for g in grupos),
    })


class ResultadoPayload(BaseModel):
    numeros: List[str]  # lista de 20 strings de 4 dígitos


@router.post("/{sid}/guardar-resultado")
async def guardar_resultado(sid: int, payload: ResultadoPayload, request: Request, db: Session = Depends(get_db)):
    """Guarda los 20 números del resultado ingresados manualmente."""
    await auth_module.require_user(request, db)
    s = db.query(models.Sorteo).get(sid)
    if not s:
        return JSONResponse({"ok": False, "error": "Sorteo no encontrado"}, status_code=404)

    # Normalizar: asegurarse de que sean strings de 4 dígitos
    numeros = [str(n).zfill(4)[:4] for n in payload.numeros[:20]]
    s.resultado_json = json.dumps(numeros)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/{sid}/buscar-resultado")
async def buscar_resultado(sid: int, request: Request, db: Session = Depends(get_db)):
    """Busca y guarda los resultados de la Tómbola Nocturna de Entre Ríos para este sorteo."""
    await auth_module.require_user(request, db)
    s = db.query(models.Sorteo).get(sid)
    if not s:
        return JSONResponse({"ok": False, "error": "Sorteo no encontrado"}, status_code=404)

    resultado = await buscar_resultado_tombola(s.fecha)

    if resultado["ok"]:
        s.resultado_json = json.dumps(resultado["numeros"])
        db.commit()

    return JSONResponse(resultado)
