import io
import re
from collections import defaultdict

from fastapi import HTTPException,  APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from typing import Optional

from sqlalchemy.orm import Session, selectinload
from .. import models, auth as auth_module
from ..templates_config import templates
from ..database import get_db

router = APIRouter(prefix="/zonas", tags=["zonas"])


def _norm_txt(s) -> str:
    """Normaliza texto para comparar: mayúsculas, espacios colapsados, sin acentos básicos."""
    s = (str(s) if s is not None else "").upper().strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_zona(s) -> str:
    """Normaliza nombre de zona. Si es numérica, sin ceros a la izquierda (035 -> 35)."""
    s = _norm_txt(s)
    return str(int(s)) if s.isdigit() else s


def _norm_key(nombre, direccion) -> str:
    """Clave de identidad de un comprador: nombre + dirección normalizados."""
    return _norm_txt(nombre) + "|" + _norm_txt(direccion)


def _prev_zona_by_key(ba_rows):
    """Mapa identidad-de-comprador (nombre+dirección) -> zona normalizada que tenía
    en el bono anterior. Se usa como respaldo cuando este bono no tiene zona cargada."""
    m = {}
    for r in ba_rows:
        k = _norm_key(r.apellido_nombre, r.direccion)
        if k not in m:
            zk = _norm_zona(r.zona)
            if zk:
                m[k] = zk
    return m


def _stats_bono_anterior(db):
    """Calcula, por zona, el rendimiento ponderado del bono actual vs el anterior
    y la cantidad de compradores que aún no renovaron (match por nombre+dirección).

    Si un comprador de este bono NO tiene zona cargada pero SÍ la tenía en el bono
    anterior, su venta se atribuye a esa zona anterior (respaldo automático)."""
    ba_rows = db.query(models.BonoAnterior).all()
    compradores = (
        db.query(models.Comprador)
        .options(selectinload(models.Comprador.boletas).selectinload(models.Boleta.talonera))
        .all()
    )
    current_keys = {_norm_key(c.apellido_nombre, c.direccion) for c in compradores}
    prev_zona = _prev_zona_by_key(ba_rows)
    zona_norm_by_id = {z.id: _norm_zona(z.nombre) for z in db.query(models.Zona).all()}
    zonas_norm_set = set(zona_norm_by_id.values())

    pond_actual = defaultdict(float)        # norm zona name -> ponderado vendido este bono
    sin_zona = 0
    asignables = 0
    for c in compradores:
        pond_c = sum(float(b.talonera.multiplicador or 1.0) for b in c.boletas if b.talonera)
        if c.zona_id and c.zona_id in zona_norm_by_id:
            zk = zona_norm_by_id[c.zona_id]
        else:
            zk = prev_zona.get(_norm_key(c.apellido_nombre, c.direccion))
            if not c.zona_id:
                sin_zona += 1
                if zk and zk in zonas_norm_set:
                    asignables += 1
        if pond_c > 0 and zk:
            pond_actual[zk] += pond_c

    ba_por_zona = defaultdict(lambda: {"pond": 0.0, "pendientes": 0, "total": 0})
    for r in ba_rows:
        g = ba_por_zona[_norm_zona(r.zona)]
        g["pond"] += float(r.multiplicador or 1.0)
        g["total"] += 1
        if _norm_key(r.apellido_nombre, r.direccion) not in current_keys:
            g["pendientes"] += 1

    return ba_rows, ba_por_zona, pond_actual, {"sin_zona": sin_zona, "asignables": asignables}


@router.get("/", response_class=HTMLResponse)
async def listar(
    request: Request,
    db: Session = Depends(get_db),
    ok: Optional[str] = None,
    err: Optional[str] = None,
    n: Optional[str] = None,
):
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'zonas', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')
    zonas = db.query(models.Zona).order_by(models.Zona.nombre).all()
    vendedores = db.query(models.Vendedor).filter(models.Vendedor.activo == True).order_by(models.Vendedor.nombre).all()

    ba_rows, ba_por_zona, pond_actual, extra = _stats_bono_anterior(db)
    stats = {}
    for z in zonas:
        zk = _norm_zona(z.nombre)
        ba = ba_por_zona.get(zk, {"pond": 0.0, "pendientes": 0, "total": 0})
        pa = pond_actual.get(zk, 0.0)
        stats[z.id] = {
            "pond_actual": pa,
            "pond_anterior": ba["pond"],
            "pendientes": ba["pendientes"],
            "total_anterior": ba["total"],
            "rend": (pa / ba["pond"] * 100) if ba["pond"] > 0 else None,
        }
    zonas_sin_hacer = sum(1 for z in zonas if not z.hecha)
    total_pendientes = sum(s["pendientes"] for s in stats.values())

    return templates.TemplateResponse(request, "zonas.html", {
        "user": user,
        "zonas": zonas,
        "vendedores": vendedores,
        "msg_ok": ok,
        "msg_err": err,
        "msg_nombre": n,
        "stats": stats,
        "zonas_sin_hacer": zonas_sin_hacer,
        "total_pendientes": total_pendientes,
        "tiene_bono_anterior": len(ba_rows) > 0,
        "total_bono_anterior": len(ba_rows),
        "compradores_sin_zona": extra["sin_zona"],
        "zonas_asignables": extra["asignables"],
    })


@router.post("/importar-bono-anterior")
async def importar_bono_anterior(
    request: Request,
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Importa el Excel del bono anterior. Columnas esperadas (por encabezado):
    PATA, APELLIDO Y NOMBRE, DIRECCION, ZONA, COBRADOR, CONDICION, VENDEDOR.
    Reemplaza todo el historial anterior cargado."""
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'zonas', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')

    try:
        import openpyxl
    except Exception:
        return RedirectResponse("/zonas/?err=openpyxl", status_code=302)

    content = await archivo.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception:
        return RedirectResponse("/zonas/?err=archivo_invalido", status_code=302)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    header = next(it, None)
    if not header:
        return RedirectResponse("/zonas/?err=archivo_vacio", status_code=302)

    idx = {}
    for i, h in enumerate(header):
        idx[_norm_txt(h)] = i

    def col(*names):
        for nm in names:
            if nm in idx:
                return idx[nm]
        return None

    c_pata = col("PATA")
    c_nom = col("APELLIDO Y NOMBRE", "APELLIDO Y NOMBRES", "NOMBRE", "APELLIDO")
    c_dir = col("DIRECCION", "DIRECCIÓN")
    c_zona = col("ZONA")
    c_cob = col("COBRADOR")
    c_cond = col("CONDICION", "CONDICIÓN")
    c_vend = col("VENDEDOR")

    if c_pata is None or c_nom is None:
        return RedirectResponse("/zonas/?err=columnas", status_code=302)

    mult_por_pata = {
        _norm_txt(t.nombre): float(t.multiplicador or 1.0)
        for t in db.query(models.Talonera).all()
    }

    def cell(row, i):
        if i is None or i >= len(row) or row[i] is None:
            return ""
        return str(row[i]).strip()

    # Reemplazar todo el historial anterior
    db.query(models.BonoAnterior).delete()

    n_ins = 0
    for row in it:
        if row is None:
            continue
        pata = cell(row, c_pata)
        nom = cell(row, c_nom)
        if not nom and not pata:
            continue
        zona_raw = cell(row, c_zona)
        # ZONA puede venir como float (35.0) → normalizar a entero string
        try:
            if zona_raw and float(zona_raw) == int(float(zona_raw)):
                zona_raw = str(int(float(zona_raw)))
        except (ValueError, TypeError):
            pass
        db.add(models.BonoAnterior(
            pata=pata,
            apellido_nombre=nom,
            direccion=cell(row, c_dir),
            zona=zona_raw,
            cobrador=cell(row, c_cob),
            condicion=cell(row, c_cond),
            vendedor=cell(row, c_vend),
            multiplicador=mult_por_pata.get(_norm_txt(pata), 1.0),
        ))
        n_ins += 1

    db.commit()
    return RedirectResponse(f"/zonas/?ok=importado&n={n_ins}", status_code=302)


@router.post("/asignar-zonas-faltantes")
async def asignar_zonas_faltantes(request: Request, db: Session = Depends(get_db)):
    """Completa la zona de los compradores que NO tienen zona cargada este bono,
    usando la zona que tenían en el bono anterior (match por nombre + dirección).
    Solo asigna si existe una zona actual con ese nombre."""
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'zonas', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')

    ba_rows = db.query(models.BonoAnterior).all()
    prev_zona = _prev_zona_by_key(ba_rows)
    zona_by_norm = {}
    for z in db.query(models.Zona).all():
        zona_by_norm.setdefault(_norm_zona(z.nombre), z)

    n = 0
    for c in db.query(models.Comprador).filter(models.Comprador.zona_id.is_(None)).all():
        zk = prev_zona.get(_norm_key(c.apellido_nombre, c.direccion))
        if zk and zk in zona_by_norm:
            c.zona_id = zona_by_norm[zk].id
            n += 1
    db.commit()
    return RedirectResponse(f"/zonas/?ok=zonas_asignadas&n={n}", status_code=302)


@router.post("/{zid}/toggle-hecha")
async def toggle_hecha(zid: int, request: Request, db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'zonas', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    z = db.query(models.Zona).get(zid)
    if z:
        z.hecha = not bool(z.hecha)
        db.commit()
    return RedirectResponse("/zonas/", status_code=302)


@router.get("/renovaciones", response_class=HTMLResponse)
async def renovaciones(request: Request, db: Session = Depends(get_db)):
    """Subsección: por zona, compradores del bono anterior que TODAVÍA no renovaron
    este bono (match por nombre + dirección)."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'zonas', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')

    ba_rows = db.query(models.BonoAnterior).all()
    compradores = db.query(models.Comprador).all()
    current_keys = {_norm_key(c.apellido_nombre, c.direccion) for c in compradores}

    # Nombre legible de zona: si coincide con una zona actual, usar su nombre
    zonas_por_norm = {_norm_zona(z.nombre): z.nombre for z in db.query(models.Zona).all()}

    grupos = {}
    for r in ba_rows:
        if _norm_key(r.apellido_nombre, r.direccion) in current_keys:
            continue
        zk = _norm_zona(r.zona)
        label = zonas_por_norm.get(zk, r.zona or "— sin zona —")
        grupos.setdefault((zk, label), []).append(r)

    # Ordenar zonas (numéricas primero) y filas por nombre
    def _zona_sort(item):
        zk = item[0][0]
        return (0, int(zk)) if zk.isdigit() else (1, zk)

    grupos_list = []
    for (zk, label), filas in sorted(grupos.items(), key=_zona_sort):
        filas.sort(key=lambda r: _norm_txt(r.apellido_nombre))
        grupos_list.append({"zona": label, "filas": filas, "cantidad": len(filas)})

    total = sum(g["cantidad"] for g in grupos_list)
    return templates.TemplateResponse(request, "zonas_renovaciones.html", {
        "user": user,
        "grupos": grupos_list,
        "total": total,
        "tiene_bono_anterior": len(ba_rows) > 0,
    })


@router.post("/crear")
async def crear(
    request: Request,
    nombre: str = Form(...),
    descripcion: str = Form(""),
    db: Session = Depends(get_db)
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'zonas', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    z = models.Zona(nombre=nombre.strip(), descripcion=descripcion.strip() or None)
    db.add(z)
    db.commit()
    return RedirectResponse("/zonas/", status_code=302)


@router.post("/{zid}/editar")
async def editar(
    zid: int, request: Request,
    nombre: str = Form(...),
    descripcion: str = Form(""),
    db: Session = Depends(get_db),
):
    """Renombra una zona / actualiza su descripción. Los socios (compradores)
    están vinculados por zona_id, así que el nuevo nombre se propaga sólo."""
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'zonas', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    z = db.query(models.Zona).get(zid)
    if not z:
        raise HTTPException(404, "Zona no encontrada")

    nuevo_nombre = (nombre or "").strip()
    if not nuevo_nombre:
        return RedirectResponse("/zonas/?err=nombre_vacio", status_code=302)

    # Si el nombre no cambió respecto al actual, sólo actualizar descripcion.
    if nuevo_nombre != z.nombre:
        # Validar unicidad (excluyendo la propia zona).
        ya_existe = (
            db.query(models.Zona)
              .filter(models.Zona.nombre == nuevo_nombre, models.Zona.id != zid)
              .first()
        )
        if ya_existe:
            return RedirectResponse(
                f"/zonas/?err=nombre_duplicado&n={nuevo_nombre}",
                status_code=302,
            )
        z.nombre = nuevo_nombre

    z.descripcion = (descripcion or "").strip() or None
    db.commit()
    return RedirectResponse("/zonas/?ok=editada", status_code=302)


@router.post("/{zid}/vendedor")
async def asignar_vendedor(
    zid: int, request: Request,
    vendedor_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'zonas', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    z = db.query(models.Zona).get(zid)
    if z:
        z.vendedor_id = vendedor_id or None
        db.commit()
    return RedirectResponse("/zonas/", status_code=302)


@router.post("/{zid}/eliminar")
async def eliminar(zid: int, request: Request, db: Session = Depends(get_db)):
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'zonas', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')
    z = db.query(models.Zona).get(zid)
    if z:
        for comp in z.compradores:
            comp.zona_id = None
        db.delete(z)
        db.commit()
    return RedirectResponse("/zonas/", status_code=302)
