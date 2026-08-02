"""Backup / restore desde la propia app.

REESCRITO: antes el export leía las columnas del modelo (bien) pero el restore
tenía listas de columnas escritas a mano que quedaron congeladas en abril. Un ZIP
se restauraba "sin error" y en el camino se perdían 11 columnas de `boletas`
(entre ellas `historial_cuotas`, o sea TODA la cobranza, y los `numero_especial`
de los contados), 11 de `liquidaciones_vendedor`, 4 de `taloneras`, y 10 tablas
enteras que ni se exportaban (`users`, `liquidacion_contado_items`,
`premios_sorteo`, `config_bono`, ...).

Ahora tanto el export como el restore recorren models.Base.metadata: cualquier
columna o tabla nueva entra sola, sin tocar este archivo.
"""

import io
import json
import zipfile
from datetime import date, datetime, time
from decimal import Decimal

from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from .. import models, auth as auth_module
from ..templates_config import templates
from ..database import get_db, engine

router = APIRouter(prefix="/backup", tags=["backup"])


# ─────────────────────────────────────────
# Serialización
# ─────────────────────────────────────────

def _encode(v):
    """Tipos de la DB → JSON. El marcador __t__ permite reconstruir al restaurar."""
    if isinstance(v, (datetime, date, time)):
        return {"__t__": "dt", "v": v.isoformat()}
    if isinstance(v, Decimal):
        return {"__t__": "dec", "v": str(v)}
    if isinstance(v, (bytes, memoryview)):
        import base64
        return {"__t__": "b64", "v": base64.b64encode(bytes(v)).decode()}
    return v


def _decode(v):
    """Inversa de _encode(). Los strings ISO los castea el motor solo."""
    if isinstance(v, dict) and "__t__" in v:
        if v["__t__"] == "b64":
            import base64
            return base64.b64decode(v["v"])
        return v["v"]
    return v


def _tablas_ordenadas():
    """Tablas en orden de dependencias (padres primero). Para restaurar se
    recorre al derecho; para borrar, al revés."""
    return list(models.Base.metadata.sorted_tables)


def _stats(db: Session):
    """Conteos que muestra la página. Si una tabla todavía no existe (deploy a
    medio migrar), devuelve 0 en vez de tumbar la pantalla."""
    def _c(model):
        try:
            return db.query(model).count()
        except Exception:
            db.rollback()
            return 0
    return {
        "socios":        _c(models.Comprador),
        "taloneras":     _c(models.Talonera),
        "boletas":       _c(models.Boleta),
        "vendedores":    _c(models.Vendedor),
        "cobradores":    _c(models.Cobrador),
        "zonas":         _c(models.Zona),
        "sorteos":       _c(models.Sorteo),
        "planillas":     _c(models.Planilla),
        "liquidaciones": _c(models.Liquidacion),
    }


def _pagina(request, user, stats=None, msg=None, error=None):
    return templates.TemplateResponse(request, "backup.html", {
        "user": user,
        "stats": stats if stats is not None else {},
        "msg": msg,
        "error": error,
    })


# ─────────────────────────────────────────
# GET /backup/
# ─────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def backup_page(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)
    return _pagina(request, user, stats=_stats(db))


# ─────────────────────────────────────────
# GET /backup/descargar
# ─────────────────────────────────────────

@router.get("/descargar")
async def descargar_backup(request: Request, db: Session = Depends(get_db)):
    await auth_module.require_user(request, db)

    buf = io.BytesIO()
    conteos = {}

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for tabla in _tablas_ordenadas():
            cols = [c.name for c in tabla.columns]
            try:
                filas = db.execute(
                    text('SELECT %s FROM "%s"' % (
                        ", ".join('"%s"' % c for c in cols), tabla.name))
                ).fetchall()
            except Exception:
                # Tabla todavía inexistente en esta DB: se omite y queda registrado.
                db.rollback()
                conteos[tabla.name] = None
                continue

            datos = [{c: _encode(v) for c, v in zip(cols, fila)} for fila in filas]
            conteos[tabla.name] = len(datos)
            zf.writestr(f"{tabla.name}.json", json.dumps(datos, ensure_ascii=False, indent=1))

        zf.writestr("_meta.json", json.dumps({
            "fecha": datetime.now().isoformat(),
            "formato": 2,          # v1 = export viejo con columnas parciales
            "dialect": engine.dialect.name,
            "tablas": conteos,
            "orden": [t.name for t in _tablas_ordenadas()],
        }, ensure_ascii=False, indent=2))

    buf.seek(0)
    nombre = f"backup_bonos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


# ─────────────────────────────────────────
# POST /backup/restaurar
# ─────────────────────────────────────────

@router.post("/restaurar", response_class=HTMLResponse)
async def restaurar_backup(
    request: Request,
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = await auth_module.require_user(request, db)

    if not (archivo.filename or "").lower().endswith(".zip"):
        return _pagina(request, user, error="El archivo debe ser un .zip generado por esta app.")

    contenido = await archivo.read()
    try:
        with zipfile.ZipFile(io.BytesIO(contenido)) as zf:
            nombres = set(zf.namelist())
            datos = {
                n[:-5]: json.loads(zf.read(n).decode("utf-8"))
                for n in nombres if n.endswith(".json") and n != "_meta.json"
            }
            meta = json.loads(zf.read("_meta.json")) if "_meta.json" in nombres else {}
    except Exception as e:
        return _pagina(request, user, error=f"Error al leer el ZIP: {e}")

    if not datos:
        return _pagina(request, user, error="El ZIP no contiene datos de ninguna tabla.")

    tablas = [t for t in _tablas_ordenadas() if t.name in datos]
    if not tablas:
        return _pagina(request, user, error="El ZIP no coincide con ninguna tabla de esta base.")

    dialect = engine.dialect.name
    restauradas = {}

    try:
        # TODO en una sola transacción. El código anterior hacía commit después
        # del DELETE: si la inserción fallaba, la base quedaba vacía y sin vuelta
        # atrás. Acá, si algo falla, el rollback deja todo como estaba.
        if dialect == "sqlite":
            db.execute(text("PRAGMA foreign_keys = OFF"))

        for tabla in reversed(tablas):
            db.execute(text('DELETE FROM "%s"' % tabla.name))

        for tabla in tablas:
            filas = datos.get(tabla.name) or []
            if not filas:
                restauradas[tabla.name] = 0
                continue

            # Intersección entre lo que trae el backup y lo que existe hoy en la
            # tabla. Un backup viejo al que le falta una columna nueva entra igual
            # (la columna toma su default); una columna que ya no existe se ignora.
            presentes = set(filas[0].keys())
            cols = [c.name for c in tabla.columns if c.name in presentes]
            if not cols:
                restauradas[tabla.name] = 0
                continue

            sql = text('INSERT INTO "%s" (%s) VALUES (%s)' % (
                tabla.name,
                ", ".join('"%s"' % c for c in cols),
                ", ".join(":%s" % c for c in cols),
            ))
            db.execute(sql, [{c: _decode(f.get(c)) for c in cols} for f in filas])
            restauradas[tabla.name] = len(filas)

        # Postgres: reencauzar las secuencias. Sin esto, como los id vienen
        # explícitos en el backup, el contador queda atrás y el próximo alta
        # revienta con "duplicate key value violates unique constraint".
        if dialect == "postgresql":
            for tabla in tablas:
                for col in tabla.columns:
                    seq = db.execute(
                        text("SELECT pg_get_serial_sequence(:t, :c)"),
                        {"t": tabla.name, "c": col.name},
                    ).scalar()
                    if seq:
                        db.execute(text(
                            'SELECT setval(:s, COALESCE((SELECT MAX("%s") FROM "%s"), 0) + 1, false)'
                            % (col.name, tabla.name)
                        ), {"s": seq})

        if dialect == "sqlite":
            db.execute(text("PRAGMA foreign_keys = ON"))

        db.commit()

    except Exception as e:
        db.rollback()
        if dialect == "sqlite":
            try:
                db.execute(text("PRAGMA foreign_keys = ON"))
            except Exception:
                pass
        return _pagina(
            request, user,
            error=f"Error al restaurar: {e} — no se modificó nada, la base quedó como estaba.",
        )

    total = sum(restauradas.values())
    fecha = meta.get("fecha", "")[:19].replace("T", " ")
    detalle = ", ".join(f"{t} {n}" for t, n in restauradas.items() if n)
    aviso = ""
    if meta.get("formato") != 2:
        aviso = (" ATENCIÓN: el ZIP es de la versión vieja del backup y puede no "
                 "traer todas las columnas (historial de cobranza, contados).")

    return _pagina(
        request, user,
        stats=_stats(db),
        msg=f"Backup restaurado desde «{archivo.filename}»"
            + (f" (del {fecha})" if fecha else "")
            + f": {total:,} filas en {len([n for n in restauradas.values() if n])} tablas."
            + (f" [{detalle}]" if detalle else "") + aviso,
    )
