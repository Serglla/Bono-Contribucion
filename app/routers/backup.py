import io
import json
import zipfile
from datetime import date, datetime

from fastapi import APIRouter, Depends, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from .. import models, auth as auth_module
from ..templates_config import templates
from ..database import get_db, engine

router = APIRouter(prefix="/backup", tags=["backup"])


# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────

def _serialize(val):
    """Convierte tipos especiales a algo serializable en JSON."""
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    return val


def _row_to_dict(row):
    """Convierte una fila de SQLAlchemy (modelo ORM) a dict serializable."""
    d = {}
    for col in row.__table__.columns:
        d[col.name] = _serialize(getattr(row, col.name))
    return d


def _export_table(db: Session, model_class):
    rows = db.query(model_class).all()
    return [_row_to_dict(r) for r in rows]


def _export_zona_cobradores(db: Session):
    rows = db.execute(text("SELECT zona_id, cobrador_id, asignado_en FROM zona_cobradores")).fetchall()
    result = []
    for r in rows:
        result.append({
            "zona_id": r[0],
            "cobrador_id": r[1],
            "asignado_en": r[2].isoformat() if r[2] else None,
        })
    return result


# ─────────────────────────────────────────
# GET /backup/  — página
# ─────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def backup_page(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.require_user(request, db)

    # Contar registros por tabla para mostrar en la página
    stats = {
        "socios":      db.query(models.Comprador).count(),
        "taloneras":   db.query(models.Talonera).count(),
        "boletas":     db.query(models.Boleta).count(),
        "vendedores":  db.query(models.Vendedor).count(),
        "cobradores":  db.query(models.Cobrador).count(),
        "zonas":       db.query(models.Zona).count(),
        "sorteos":     db.query(models.Sorteo).count(),
        "planillas":   db.query(models.Planilla).count(),
        "liquidaciones": db.query(models.Liquidacion).count(),
    }

    return templates.TemplateResponse(request, "backup.html", {
        "user": user,
        "stats": stats,
        "msg": None,
        "error": None,
    })


# ─────────────────────────────────────────
# GET /backup/descargar  — genera ZIP con JSONs
# ─────────────────────────────────────────

@router.get("/descargar")
async def descargar_backup(request: Request, db: Session = Depends(get_db)):
    await auth_module.require_user(request, db)

    data = {
        "vendedores":           _export_table(db, models.Vendedor),
        "cobradores":           _export_table(db, models.Cobrador),
        "zonas":                _export_table(db, models.Zona),
        "zona_cobradores":      _export_zona_cobradores(db),
        "taloneras":            _export_table(db, models.Talonera),
        "compradores":          _export_table(db, models.Comprador),
        "boletas":              _export_table(db, models.Boleta),
        "sorteos":              _export_table(db, models.Sorteo),
        "planillas":            _export_table(db, models.Planilla),
        "liquidaciones":        _export_table(db, models.Liquidacion),
        "liquidacion_detalles": _export_table(db, models.LiquidacionDetalle),
        "liquidaciones_vendedor": _export_table(db, models.LiquidacionVendedor),
        "entregas_caja":        _export_table(db, models.EntregaCaja),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for nombre, registros in data.items():
            contenido = json.dumps(registros, ensure_ascii=False, indent=2)
            zf.writestr(f"{nombre}.json", contenido)

        # Metadato del backup
        meta = {
            "fecha": datetime.now().isoformat(),
            "tablas": {k: len(v) for k, v in data.items()},
        }
        zf.writestr("_meta.json", json.dumps(meta, ensure_ascii=False, indent=2))

    buf.seek(0)
    fecha_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"backup_bonos_{fecha_str}.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────────────────────
# POST /backup/restaurar  — restaura desde ZIP
# ─────────────────────────────────────────

@router.post("/restaurar", response_class=HTMLResponse)
async def restaurar_backup(
    request: Request,
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    user = await auth_module.require_user(request, db)

    if not archivo.filename.endswith(".zip"):
        return templates.TemplateResponse(request, "backup.html", {
            "user": user,
            "stats": {},
            "msg": None,
            "error": "El archivo debe ser un .zip generado por esta app.",
        })

    contenido = await archivo.read()
    try:
        with zipfile.ZipFile(io.BytesIO(contenido)) as zf:
            nombres = zf.namelist()

            def leer(nombre):
                if nombre in nombres:
                    return json.loads(zf.read(nombre).decode("utf-8"))
                return []

            vendedores_data      = leer("vendedores.json")
            cobradores_data      = leer("cobradores.json")
            zonas_data           = leer("zonas.json")
            zona_cob_data        = leer("zona_cobradores.json")
            taloneras_data       = leer("taloneras.json")
            compradores_data     = leer("compradores.json")
            boletas_data         = leer("boletas.json")
            sorteos_data         = leer("sorteos.json")
            planillas_data       = leer("planillas.json")
            liquidaciones_data   = leer("liquidaciones.json")
            liq_det_data         = leer("liquidacion_detalles.json")
            liq_vend_data        = leer("liquidaciones_vendedor.json")
            entregas_data        = leer("entregas_caja.json")

    except Exception as e:
        return templates.TemplateResponse(request, "backup.html", {
            "user": user,
            "stats": {},
            "msg": None,
            "error": f"Error al leer el ZIP: {e}",
        })

    try:
        dialect = engine.dialect.name

        # Deshabilitar FK en SQLite durante la restauración
        if dialect == "sqlite":
            db.execute(text("PRAGMA foreign_keys = OFF"))

        # Limpiar tablas en orden inverso de dependencias
        for tabla in [
            "entregas_caja", "liquidaciones_vendedor",
            "liquidacion_detalles", "liquidaciones", "boletas",
            "planillas", "compradores", "zona_cobradores",
            "zonas", "cobradores", "vendedores", "taloneras", "sorteos",
        ]:
            db.execute(text(f"DELETE FROM {tabla}"))
        db.commit()

        def insertar(tabla, filas, columnas):
            if not filas:
                return
            for fila in filas:
                vals = {c: fila.get(c) for c in columnas}
                cols_str = ", ".join(columnas)
                params_str = ", ".join(f":{c}" for c in columnas)
                db.execute(text(f"INSERT INTO {tabla} ({cols_str}) VALUES ({params_str})"), vals)
            db.commit()

        insertar("vendedores",  vendedores_data,  ["id", "nombre", "telefono", "activo"])
        insertar("cobradores",  cobradores_data,  ["id", "nombre", "telefono", "activo", "comision_pct"])
        insertar("zonas",       zonas_data,       ["id", "nombre", "descripcion", "vendedor_id"])
        insertar("zona_cobradores", zona_cob_data, ["zona_id", "cobrador_id", "asignado_en"])
        insertar("taloneras",   taloneras_data,   ["id", "nombre", "multiplicador", "numero_inicio", "numero_fin",
                                                    "num_series", "offset_series", "activa", "color"])
        insertar("compradores", compradores_data, ["id", "apellido_nombre", "direccion", "zona_id", "telefono"])
        insertar("sorteos",     sorteos_data,     ["id", "nombre", "tipo", "cifras", "fecha",
                                                    "num_premios", "resultado_json"])
        insertar("planillas",   planillas_data,   ["id", "cobrador_id", "numero", "mes", "anio",
                                                    "comision_pct", "fecha_creacion"])
        insertar("liquidaciones_vendedor", liq_vend_data,
                 ["id", "vendedor_id", "fecha", "cuotas_vendidas", "cuota_1_total", "monto_cuotas",
                  "comision_cuotas_pct", "comision_cuotas", "contados_vendidos", "monto_contados",
                  "comision_contados_pct", "comision_contados", "total_comision", "observacion"])
        insertar("boletas",     boletas_data,     ["id", "talonera_id", "numero_principal", "numeros_adicionales",
                                                    "comprador_id", "cobrador_id", "vendedor_id", "planilla_id",
                                                    "fecha_venta", "condicion", "cuotas_pactadas",
                                                    "cuotas_anticipadas", "cuotas_pagadas", "total_pagado",
                                                    "liquidacion_vendedor_id"])
        insertar("liquidaciones", liquidaciones_data,
                 ["id", "planilla_id", "fecha", "total_cuotas", "monto_total", "comision", "neto"])
        insertar("liquidacion_detalles", liq_det_data,
                 ["id", "liquidacion_id", "boleta_id", "cuotas_cobradas"])
        insertar("entregas_caja", entregas_data,
                 ["id", "talonera_nombre", "desde", "hasta", "boletas_afectadas",
                  "observacion", "fecha", "usuario_id", "vendedor_id"])

        if dialect == "sqlite":
            db.execute(text("PRAGMA foreign_keys = ON"))

        # Estadísticas post-restauración
        stats = {
            "socios":      db.query(models.Comprador).count(),
            "taloneras":   db.query(models.Talonera).count(),
            "boletas":     db.query(models.Boleta).count(),
            "vendedores":  db.query(models.Vendedor).count(),
            "cobradores":  db.query(models.Cobrador).count(),
            "zonas":       db.query(models.Zona).count(),
            "sorteos":     db.query(models.Sorteo).count(),
            "planillas":   db.query(models.Planilla).count(),
            "liquidaciones": db.query(models.Liquidacion).count(),
        }

        return templates.TemplateResponse(request, "backup.html", {
            "user": user,
            "stats": stats,
            "msg": f"✅ Backup restaurado correctamente desde «{archivo.filename}».",
            "error": None,
        })

    except Exception as e:
        db.rollback()
        if dialect == "sqlite":
            try:
                db.execute(text("PRAGMA foreign_keys = ON"))
            except Exception:
                pass
        return templates.TemplateResponse(request, "backup.html", {
            "user": user,
            "stats": {},
            "msg": None,
            "error": f"Error al restaurar: {e}",
        })
