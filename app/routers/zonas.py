import io
import re
from collections import defaultdict

from fastapi import HTTPException,  APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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


_STOP_DIR = {"DE", "LA", "EL", "Y", "DEL", "LOS", "LAS", "N", "NRO", "N°", "DTO", "PB"}


def _dir_parse(direccion):
    """Devuelve (número de casa, set de palabras de calle) de una dirección.
    El número de casa es el número MÁS GRANDE (en 'CALLE 25 DE AGOSTO 723' el 723,
    no el 25). Las palabras de calle excluyen el número de casa y palabras comunes.
    Se ignora la puntuación ('V.' / 'V,' -> 'V') y las abreviaturas de 1 letra
    (ej. la 'V' de 'V. ETCHEVERRY' = Villa), que solo aportan ruido al match."""
    limpio = re.sub(r"[^\w\s]", " ", _norm_txt(direccion))
    toks = limpio.split()
    nums = [t for t in toks if t.isdigit()]
    house = max(nums, key=int) if nums else ""
    calles, quitado = set(), False
    for t in toks:
        if t in _STOP_DIR:
            continue
        if t == house and not quitado:
            quitado = True
            continue
        if len(t) <= 1:            # ignora abreviaturas/ruido de 1 letra
            continue
        calles.add(t)
    return house, calles


def _calles_sim(c1, c2):
    """True si dos conjuntos de palabras de calle comparten alguna palabra
    'parecida': exacta, prefijo (nombres largos) o 1 typo. Tolera variantes de
    grafía del mismo nombre de calle (ej. ETCHEVERRY / ECHEVERRY)."""
    if c1 & c2:
        return True
    for a in c1:
        for b in c2:
            if _apellido_sim(a, b):
                return True
    return False


def _apellido_num(nombre, direccion):
    """Descompone un socio en (apellido, número de casa, set de nombres de pila).
    Se usa para match difuso entre bonos: "MAHER MARIA" y "MAHER MARIA CECILIA"
    en "25 DE AGOSTO 723" son la misma persona."""
    toks = _norm_txt(nombre).split()
    apellido = toks[0] if toks else ""
    given = set(toks[1:])
    house, _ = _dir_parse(direccion)
    return apellido, house, given


def _lev_le1(a, b):
    """True si la distancia de edición entre a y b es <= 1 (1 typo)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    i = 0
    while i < min(la, lb) and a[i] == b[i]:
        i += 1
    if la == lb:
        return a[i + 1:] == b[i + 1:]        # sustitución
    if la < lb:
        return a[i:] == b[i + 1:]            # inserción en b
    return a[i + 1:] == b[i:]                # borrado en b


def _apellido_sim(a, b):
    """Apellidos 'iguales' tolerando typos/plurales: exacto, uno prefijo del otro
    (nombres largos, ej. TRANSPORTE/TRANSPORTES), o 1 letra de diferencia."""
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
        return True
    return _lev_le1(a, b)


def _build_addr_index(items):
    """items: (nombre, direccion, zona_display). Índice número de casa -> lista de
    (calles, apellido, given, nombre, zona). Para buscar quién está en una dirección."""
    idx = defaultdict(list)
    for nombre, direccion, zona in items:
        ap, num, given = _apellido_num(nombre, direccion)
        _, calles = _dir_parse(direccion)
        if num:
            idx[num].append((calles, ap, given, nombre, zona))
    return idx


def _otro_en_direccion(idx, nombre, direccion):
    """Personas DISTINTAS (no el mismo socio) que están en la misma dirección.
    Devuelve lista de etiquetas 'NOMBRE (zona X)'."""
    ap, num, given = _apellido_num(nombre, direccion)
    _, calles = _dir_parse(direccion)
    out = []
    if not num:
        return out
    for calles2, ap2, given2, nombre2, zona2 in idx.get(num, []):
        if not _calles_sim(calles, calles2):            # misma dirección (num + calle, difuso)
            continue
        if _apellido_sim(ap, ap2) and ((given & given2) or not given or not given2):
            continue                                     # es el mismo socio → no avisar
        etiqueta = nombre2 + (" (zona " + str(zona2) + ")" if zona2 else "")
        if etiqueta not in out:
            out.append(etiqueta)
    return out


def _build_nombre_index(items):
    """items: (nombre, direccion, zona). Índice apellido -> lista de
    (given, num, calles, nombre, direccion, zona). Para buscar mismo nombre en otra dir."""
    idx = defaultdict(list)
    for nombre, direccion, zona in items:
        ap, num, given = _apellido_num(nombre, direccion)
        _, calles = _dir_parse(direccion)
        if ap:
            idx[ap].append((given, num, calles, nombre, direccion, zona))
    return idx


def _mismo_nombre_otra_dir(idx, nombre, direccion):
    """Personas con el MISMO nombre (apellido + nombre de pila) pero en OTRA dirección.
    Devuelve etiquetas 'NOMBRE — DIRECCION (zona X)'."""
    ap, num, given = _apellido_num(nombre, direccion)
    _, calles = _dir_parse(direccion)
    out = []
    if not ap:
        return out
    for given2, num2, calles2, nombre2, dir2, zona2 in idx.get(ap, []):
        if not ((given & given2) or not given or not given2):
            continue                                  # distinto nombre de pila
        if num and num == num2 and _calles_sim(calles, calles2):
            continue                                  # misma dirección → es la misma persona
        etiqueta = nombre2 + " — " + (dir2 or "") + (" (zona " + str(zona2) + ")" if zona2 else "")
        if etiqueta not in out:
            out.append(etiqueta)
    return out


def _build_socio_matcher(personas):
    """Índice número de casa -> lista de (apellido, set de nombres de pila).
    Se busca por número y se compara el apellido en forma tolerante (typos/plurales)."""
    idx = defaultdict(list)
    for nombre, direccion in personas:
        ap, num, given = _apellido_num(nombre, direccion)
        if ap and num:
            idx[num].append((ap, given))
    return idx


def _build_zona_index(items):
    """items: iterable de (nombre, direccion, zona_display).
    Devuelve número de casa -> lista de (apellido, set de nombres de pila, zona)."""
    idx = defaultdict(list)
    for nombre, direccion, zona in items:
        ap, num, given = _apellido_num(nombre, direccion)
        if ap and num:
            idx[num].append((ap, given, zona))
    return idx


def _socio_zonas(idx, nombre, direccion):
    """Set de zonas (display) donde matchea esta persona (difuso)."""
    ap, num, given = _apellido_num(nombre, direccion)
    out = set()
    if not ap or not num:
        return out
    for ap2, given2, zona in idx.get(num, []):
        if _apellido_sim(ap, ap2) and ((given & given2) or not given or not given2):
            if zona:
                out.add(zona)
    return out


def _zona_prev_unica(prev_idx, nombre, direccion):
    """Zona (normalizada) donde figuraba la persona en el bono anterior, por match
    difuso. Devuelve None si no hay match o si es ambiguo (figuraba en >1 zona)."""
    norms = {_norm_zona(z) for z in _socio_zonas(prev_idx, nombre, direccion) if z}
    return next(iter(norms)) if len(norms) == 1 else None


def _socio_match(idx, nombre, direccion) -> bool:
    """True si la persona coincide (difuso) con alguien del índice:
    mismo apellido + mismo número de casa + comparten algún nombre de pila
    (o uno de los dos no tiene nombres de pila adicionales)."""
    ap, num, given = _apellido_num(nombre, direccion)
    if not ap or not num:
        return False
    for ap2, g2 in idx.get(num, []):
        if _apellido_sim(ap, ap2) and ((given & g2) or not given or not g2):
            return True
    return False


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
    current_matcher = _build_socio_matcher((c.apellido_nombre, c.direccion) for c in compradores)
    # Índice difuso del bono anterior con su zona (apellido + número de casa)
    prev_idx = _build_zona_index(
        (r.apellido_nombre, r.direccion, (r.zona or "").strip()) for r in ba_rows
    )
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
            # respaldo: zona del bono anterior por match difuso (PINTO/PINTOS, etc.)
            zk = _zona_prev_unica(prev_idx, c.apellido_nombre, c.direccion)
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
        if not _socio_match(current_matcher, r.apellido_nombre, r.direccion):
            g["pendientes"] += 1

    return ba_rows, ba_por_zona, pond_actual, {"sin_zona": sin_zona, "asignables": asignables}


@router.get("/", response_class=HTMLResponse)
async def listar(
    request: Request,
    db: Session = Depends(get_db),
    ok: Optional[str] = None,
    err: Optional[str] = None,
    n: Optional[str] = None,
    sinmult: Optional[str] = None,
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
    # Estado de cada zona:
    #   hecha    → marcada manualmente como terminada
    #   empezada → no hecha pero ya tiene compradores cargados este bono
    #   sin hacer→ no hecha y sin compradores este bono (todavía sin tocar)
    zonas_hechas = sum(1 for z in zonas if z.hecha)
    zonas_empezadas = sum(1 for z in zonas if not z.hecha and len(z.compradores) > 0)
    zonas_sin_hacer = sum(1 for z in zonas if not z.hecha and len(z.compradores) == 0)
    total_pendientes = sum(s["pendientes"] for s in stats.values())

    # Zonas que vienen del bono anterior (a migrar) y cuántas faltan crear en el sistema
    ba_zonas_orig = {}   # norm -> nombre original (display)
    for r in ba_rows:
        zk = _norm_zona(r.zona)
        if zk and zk not in ba_zonas_orig:
            ba_zonas_orig[zk] = (r.zona or "").strip()
    current_norms = {_norm_zona(z.nombre) for z in zonas}
    zonas_faltan_crear = sum(1 for nk in ba_zonas_orig if nk not in current_norms)

    return templates.TemplateResponse(request, "zonas.html", {
        "user": user,
        "zonas": zonas,
        "vendedores": vendedores,
        "msg_ok": ok,
        "msg_err": err,
        "msg_nombre": n,
        "msg_sinmult": sinmult,
        "stats": stats,
        "zonas_sin_hacer": zonas_sin_hacer,
        "zonas_empezadas": zonas_empezadas,
        "zonas_hechas": zonas_hechas,
        "total_pendientes": total_pendientes,
        "tiene_bono_anterior": len(ba_rows) > 0,
        "total_bono_anterior": len(ba_rows),
        "compradores_sin_zona": extra["sin_zona"],
        "zonas_asignables": extra["asignables"],
        "total_zonas": len(zonas),
        "zonas_bono_anterior_count": len(ba_zonas_orig),
        "zonas_faltan_crear": zonas_faltan_crear,
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
    patas_sin_match = set()   # PATAs del Excel que no coinciden con ninguna talonera
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
        pata_key = _norm_txt(pata)
        if pata and pata_key not in mult_por_pata:
            patas_sin_match.add(pata)
        db.add(models.BonoAnterior(
            pata=pata,
            apellido_nombre=nom,
            direccion=cell(row, c_dir),
            zona=zona_raw,
            cobrador=cell(row, c_cob),
            condicion=cell(row, c_cond),
            vendedor=cell(row, c_vend),
            multiplicador=mult_por_pata.get(pata_key, 1.0),
        ))
        n_ins += 1

    db.commit()
    from urllib.parse import quote_plus
    url = f"/zonas/?ok=importado&n={n_ins}"
    if patas_sin_match:
        url += "&sinmult=" + quote_plus(", ".join(sorted(patas_sin_match)))
    return RedirectResponse(url, status_code=302)


@router.post("/asignar-zonas-faltantes")
async def asignar_zonas_faltantes(request: Request, db: Session = Depends(get_db)):
    """Completa la zona de los compradores que NO tienen zona cargada este bono,
    usando la zona que tenían en el bono anterior (match por nombre + dirección).
    Solo asigna si existe una zona actual con ese nombre."""
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'zonas', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')

    ba_rows = db.query(models.BonoAnterior).all()
    prev_idx = _build_zona_index(
        (r.apellido_nombre, r.direccion, (r.zona or "").strip()) for r in ba_rows
    )
    zona_by_norm = {}
    for z in db.query(models.Zona).all():
        zona_by_norm.setdefault(_norm_zona(z.nombre), z)

    n = 0
    for c in db.query(models.Comprador).filter(models.Comprador.zona_id.is_(None)).all():
        zk = _zona_prev_unica(prev_idx, c.apellido_nombre, c.direccion)
        if zk and zk in zona_by_norm:
            c.zona_id = zona_by_norm[zk].id
            n += 1
    db.commit()
    return RedirectResponse(f"/zonas/?ok=zonas_asignadas&n={n}", status_code=302)


@router.post("/crear-zonas-faltantes")
async def crear_zonas_faltantes(request: Request, db: Session = Depends(get_db)):
    """Crea en el sistema las zonas que aparecen en el bono anterior y todavía no
    existen (migración de zonas). El nombre se toma tal cual viene en el Excel."""
    _perm_user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(_perm_user, 'zonas', 'editar'):
        raise HTTPException(403, 'No tenés permiso para editar en esta sección')

    current_norms = {_norm_zona(z.nombre) for z in db.query(models.Zona).all()}
    ba_zonas_orig = {}
    for r in db.query(models.BonoAnterior).all():
        zk = _norm_zona(r.zona)
        if zk and zk not in ba_zonas_orig:
            ba_zonas_orig[zk] = (r.zona or "").strip()

    n = 0
    for nk, nombre in ba_zonas_orig.items():
        if nk in current_norms or not nombre:
            continue
        db.add(models.Zona(nombre=nombre))
        current_norms.add(nk)
        n += 1
    db.commit()
    return RedirectResponse(f"/zonas/?ok=zonas_creadas&n={n}", status_code=302)


@router.get("/{zid}/ventas")
async def ventas_zona(zid: int, request: Request, db: Session = Depends(get_db)):
    """Devuelve, para una zona, las ventas actuales (compradores cargados este bono)
    y las del bono anterior, con PATA, nombre y dirección."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'zonas', 'ver'):
        raise HTTPException(403, 'Sin permiso')
    z = db.query(models.Zona).get(zid)
    if not z:
        raise HTTPException(404, "Zona no encontrada")

    # Ventas actuales: compradores asignados a esta zona
    compradores = (
        db.query(models.Comprador)
        .options(
            selectinload(models.Comprador.boletas).selectinload(models.Boleta.talonera),
            selectinload(models.Comprador.boletas).selectinload(models.Boleta.vendedor),
        )
        .filter(models.Comprador.zona_id == zid)
        .all()
    )
    zk = _norm_zona(z.nombre)
    ba_zona = [r for r in db.query(models.BonoAnterior).all() if _norm_zona(r.zona) == zk]

    # Índices GLOBALES (todas las zonas) con la zona de cada registro: así detectamos
    # que la persona renovó aunque haya cambiado de zona, y avisamos dónde figuraba.
    zona_nombre_by_id = {z2.id: z2.nombre for z2 in db.query(models.Zona).all()}
    prev_idx = _build_zona_index(
        (r.apellido_nombre, r.direccion, (r.zona or "").strip())
        for r in db.query(models.BonoAnterior).all()
    )
    curr_idx = _build_zona_index(
        (c.apellido_nombre, c.direccion, zona_nombre_by_id.get(c.zona_id, ""))
        for c in db.query(models.Comprador).all()
    )

    actuales = []
    for c in compradores:
        patas = sorted({b.talonera.nombre for b in c.boletas if b.talonera})
        vendedores = sorted({b.vendedor.nombre for b in c.boletas if b.vendedor})
        zonas_prev = _socio_zonas(prev_idx, c.apellido_nombre, c.direccion)
        otras = sorted({zp for zp in zonas_prev if _norm_zona(zp) != zk})
        actuales.append({
            "pata": ", ".join(patas) if patas else "—",
            "nombre": c.apellido_nombre or "",
            "direccion": c.direccion or "",
            "vendedor": ", ".join(vendedores) if vendedores else "—",
            "renovo": bool(zonas_prev),          # renovó (en esta u otra zona)
            "zona_otro": ", ".join(otras),       # si renovó pero figuraba en otra zona
        })
    actuales.sort(key=lambda x: _norm_txt(x["nombre"]))

    anteriores = []
    for r in ba_zona:
        zonas_act = _socio_zonas(curr_idx, r.apellido_nombre, r.direccion)
        otras = sorted({za for za in zonas_act if _norm_zona(za) != zk})
        anteriores.append({
            "pata": r.pata or "—",
            "nombre": r.apellido_nombre or "",
            "direccion": r.direccion or "",
            "vendedor": (r.vendedor or "").strip() or "—",
            "renovo": bool(zonas_act),
            "zona_otro": ", ".join(otras),       # si renovó pero ahora está en otra zona
        })
    anteriores.sort(key=lambda x: _norm_txt(x["nombre"]))

    # Aviso "misma dirección, distinto comprador" indicando NOMBRE y ZONA del otro:
    #   - en actuales: quién tenía esa dirección en el bono ANTERIOR (otra persona)
    #   - en anteriores: quién la tiene AHORA en el bono ACTUAL (otra persona)
    curr_addr_idx = _build_addr_index(
        (c.apellido_nombre, c.direccion, zona_nombre_by_id.get(c.zona_id, ""))
        for c in db.query(models.Comprador).all()
    )
    prev_addr_idx = _build_addr_index(
        (r.apellido_nombre, r.direccion, (r.zona or "").strip())
        for r in db.query(models.BonoAnterior).all()
    )
    for it in actuales:
        otros = _otro_en_direccion(prev_addr_idx, it["nombre"], it["direccion"])
        it["alerta_dir"] = bool(otros)
        it["alerta_txt"] = "; ".join(otros)
    for it in anteriores:
        otros = _otro_en_direccion(curr_addr_idx, it["nombre"], it["direccion"])
        it["alerta_dir"] = bool(otros)
        it["alerta_txt"] = "; ".join(otros)

    return JSONResponse({
        "zona": z.nombre,
        "actuales": actuales,
        "anteriores": anteriores,
        "n_actuales": len(actuales),
        "n_anteriores": len(anteriores),
    })


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


def _calcular_renovaciones(db):
    """Calcula, por zona, los compradores del bono ANTERIOR que TODAVÍA no
    renovaron este bono (cruce difuso por apellido + número de casa + nombre),
    con avisos de misma dirección / mismo nombre en otra dirección.
    Devuelve (grupos_list, flat, tiene_bono_anterior)."""
    ba_rows = db.query(models.BonoAnterior).all()
    compradores = db.query(models.Comprador).all()
    current_matcher = _build_socio_matcher((c.apellido_nombre, c.direccion) for c in compradores)

    # Nombre legible de zona: si coincide con una zona actual, usar su nombre
    zonas_por_norm = {_norm_zona(z.nombre): z.nombre for z in db.query(models.Zona).all()}
    zona_nombre_by_id = {z.id: z.nombre for z in db.query(models.Zona).all()}

    # Índices del bono ACTUAL para los avisos:
    #   - misma dirección con OTRO nombre (alguien nuevo ocupa esa casa)
    #   - mismo nombre en OTRA dirección (quizás se mudó y renovó sin detectarse)
    curr_addr_idx = _build_addr_index(
        (c.apellido_nombre, c.direccion, zona_nombre_by_id.get(c.zona_id, "")) for c in compradores
    )
    curr_nombre_idx = _build_nombre_index(
        (c.apellido_nombre, c.direccion, zona_nombre_by_id.get(c.zona_id, "")) for c in compradores
    )

    grupos = {}
    flat = []
    for r in ba_rows:
        if _socio_match(current_matcher, r.apellido_nombre, r.direccion):
            continue
        zk = _norm_zona(r.zona)
        label = zonas_por_norm.get(zk, r.zona or "— sin zona —")
        row = {
            "zona": label,
            "zona_sort": (0, int(zk)) if zk.isdigit() else (1, zk),
            "pata": r.pata or "—",
            "nombre": r.apellido_nombre or "",
            "direccion": r.direccion or "",
            "vendedor": r.vendedor or "—",
            "misma_dir_otro": "; ".join(_otro_en_direccion(curr_addr_idx, r.apellido_nombre, r.direccion)),
            "mismo_nom_otra": "; ".join(_mismo_nombre_otra_dir(curr_nombre_idx, r.apellido_nombre, r.direccion)),
        }
        grupos.setdefault((zk, label), []).append(row)
        flat.append(row)

    grupos_list = []
    for (zk, label), filas in sorted(grupos.items(), key=lambda it: (0, int(it[0][0])) if it[0][0].isdigit() else (1, it[0][0])):
        filas.sort(key=lambda x: _norm_txt(x["nombre"]))
        grupos_list.append({"zona": label, "filas": filas, "cantidad": len(filas)})

    flat.sort(key=lambda x: (x["zona_sort"], _norm_txt(x["nombre"])))
    return grupos_list, flat, len(ba_rows) > 0


@router.get("/renovaciones", response_class=HTMLResponse)
async def renovaciones(request: Request, db: Session = Depends(get_db)):
    """Subsección: por zona, compradores del bono anterior que TODAVÍA no renovaron
    este bono (match por nombre + dirección)."""
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'zonas', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')

    grupos_list, flat, tiene = _calcular_renovaciones(db)
    return templates.TemplateResponse(request, "zonas_renovaciones.html", {
        "user": user,
        "grupos": grupos_list,
        "flat": flat,
        "total": len(flat),
        "tiene_bono_anterior": tiene,
    })


@router.get("/renovaciones/imprimir", response_class=HTMLResponse)
async def renovaciones_imprimir(request: Request, zona: str = "", embed: int = 0,
                                db: Session = Depends(get_db)):
    """Vista imprimible (PDF vía navegador) de las renovaciones pendientes.
    Si `zona` viene dado, imprime solo esa zona; si no, todas."""
    from datetime import datetime as _dt
    user = await auth_module.require_user(request, db)
    if not auth_module.has_permission(user, 'zonas', 'ver'):
        raise HTTPException(403, 'No tenés permiso para ver esta sección')

    grupos_list, _flat, tiene = _calcular_renovaciones(db)
    zona = (zona or "").strip()
    if zona:
        grupos_list = [g for g in grupos_list if str(g["zona"]) == zona]
    total = sum(g["cantidad"] for g in grupos_list)
    return templates.TemplateResponse(request, "zonas_renovaciones_print.html", {
        "user": user,
        "grupos": grupos_list,
        "total": total,
        "zona_filtro": zona,
        "tiene_bono_anterior": tiene,
        "generado": _dt.now().strftime("%d/%m/%Y %H:%M"),
        # embed=1 → se está viendo dentro del modal-visor: base.html oculta
        # navbar y menú lateral (ver <body class="embed-mode"> en base.html).
        "embed": bool(embed),
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
