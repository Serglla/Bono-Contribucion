from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import engine, get_db
from . import models, auth as auth_module
from .routers import auth, compradores, taloneras, vendedores, cobradores, reportes, zonas, sorteos, cobranza, backup, contabilidad

models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Bonos Bomberos CDELU", version="1.0")

# Comprimir respuestas (HTML/JS/CSS/JSON) para reducir el tiempo de transferencia
# sobre la latencia hacia Railway. minimum_size evita comprimir respuestas chicas.
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=500)

# Diagnóstico: medir cuánto tarda el SERVIDOR en procesar cada request (sin contar
# la red). Se expone en el header `Server-Timing: app;dur=<ms>`, visible en DevTools
# → Network → request → Timing/Headers. Si app;dur ≈ TTFB, el cuello es el servidor;
# si app;dur es chico pero el TTFB grande, el cuello es la red o la cola de requests.
import time as _time
@app.middleware("http")
async def _server_timing(request, call_next):
    _t0 = _time.perf_counter()
    response = await call_next(request)
    _ms = (_time.perf_counter() - _t0) * 1000.0
    response.headers["Server-Timing"] = f"app;dur={_ms:.1f}"
    return response

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

app.include_router(auth.router)
app.include_router(compradores.router)
app.include_router(taloneras.router)
app.include_router(vendedores.router)
app.include_router(cobradores.router)
app.include_router(reportes.router)
app.include_router(zonas.router)
app.include_router(sorteos.router)
app.include_router(cobranza.router)
app.include_router(backup.router)
app.include_router(contabilidad.router)


@app.get("/health")
def health():
    return {"status": "ok"}


# ─── PWA endpoints ────────────────────────────────────────────────────────────
# El service worker y el manifest se sirven desde la raíz para que el "scope"
# del SW cubra toda la app (si lo servimos desde /static/ solo cubriría /static/).
@app.get("/service-worker.js", include_in_schema=False)
def service_worker():
    return FileResponse(
        "app/static/service-worker.js",
        media_type="application/javascript",
        headers={
            # Evitamos que el navegador cachee el SW: si lo cachea no detecta
            # actualizaciones nuevas. El propio SW versiona los caches internos.
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Service-Worker-Allowed": "/",
        },
    )


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(
        "app/static/manifest.webmanifest",
        media_type="application/manifest+json",
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    user = await auth_module.get_current_user(request, db)
    if not user:
        return RedirectResponse("/auth/login", status_code=302)
    return RedirectResponse("/reportes/", status_code=302)


@app.on_event("startup")
def create_default_admin():
    from .database import SessionLocal
    from sqlalchemy import text, inspect
    db = SessionLocal()
    try:
        inspector = inspect(engine)

        # Índices de performance en columnas usadas en JOINs/filtros.
        # ForeignKey() NO crea índice por sí solo: sin esto, las queries de la
        # página de Socios (y otras) hacen seq-scan sobre Boleta. CREATE INDEX
        # IF NOT EXISTS es idempotente y funciona en Postgres y SQLite.
        for _ix_sql in (
            "CREATE INDEX IF NOT EXISTS ix_boletas_comprador_id ON boletas (comprador_id)",
            "CREATE INDEX IF NOT EXISTS ix_boletas_talonera_id ON boletas (talonera_id)",
            "CREATE INDEX IF NOT EXISTS ix_boletas_cobrador_id ON boletas (cobrador_id)",
            "CREATE INDEX IF NOT EXISTS ix_boletas_vendedor_id ON boletas (vendedor_id)",
            "CREATE INDEX IF NOT EXISTS ix_boletas_planilla_id ON boletas (planilla_id)",
            "CREATE INDEX IF NOT EXISTS ix_compradores_zona_id ON compradores (zona_id)",
        ):
            try:
                db.execute(text(_ix_sql))
                db.commit()
            except Exception as e:
                db.rollback()
                print(f"Indice perf: {e}")

        # Asegurar columna legacy cobrador_id en zonas
        try:
            cols = [c["name"] for c in inspector.get_columns("zonas")]
            if "cobrador_id" not in cols:
                db.execute(text("ALTER TABLE zonas ADD COLUMN cobrador_id INTEGER REFERENCES cobradores(id)"))
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion cobrador_id: {e}")

        # Crear tabla zona_cobradores (muchos-a-muchos con timestamp)
        try:
            existing_tables = inspector.get_table_names()
            if "zona_cobradores" not in existing_tables:
                db.execute(text(
                    "CREATE TABLE zona_cobradores ("
                    "  zona_id INTEGER NOT NULL REFERENCES zonas(id),"
                    "  cobrador_id INTEGER NOT NULL REFERENCES cobradores(id),"
                    "  asignado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
                    "  PRIMARY KEY (zona_id, cobrador_id)"
                    ")"
                ))
                db.commit()
                db.execute(text(
                    "INSERT INTO zona_cobradores (zona_id, cobrador_id)"
                    " SELECT id, cobrador_id FROM zonas WHERE cobrador_id IS NOT NULL"
                ))
                db.commit()
                print("Migracion zona_cobradores: tabla creada y datos migrados OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion zona_cobradores: {e}")

        # Migrar columna num_premios en sorteos
        try:
            cols_sorteos = [c["name"] for c in inspector.get_columns("sorteos")]
            if "num_premios" not in cols_sorteos:
                db.execute(text("ALTER TABLE sorteos ADD COLUMN num_premios INTEGER DEFAULT 20"))
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion num_premios: {e}")

        # Migrar columna resultado_json en sorteos
        try:
            cols_sorteos = [c["name"] for c in inspector.get_columns("sorteos")]
            if "resultado_json" not in cols_sorteos:
                db.execute(text("ALTER TABLE sorteos ADD COLUMN resultado_json TEXT"))
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion resultado_json: {e}")

        # Migrar columna descripcion en sorteos
        try:
            cols_sorteos = [c["name"] for c in inspector.get_columns("sorteos")]
            if "descripcion" not in cols_sorteos:
                db.execute(text("ALTER TABLE sorteos ADD COLUMN descripcion TEXT"))
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion descripcion sorteos: {e}")

        # Migrar cifras de entero a string en sorteos existentes
        try:
            rows = db.execute(text("SELECT id, cifras FROM sorteos")).fetchall()
            for row in rows:
                val = row[1]
                if val is not None and not isinstance(val, str):
                    db.execute(text("UPDATE sorteos SET cifras = :c WHERE id = :i"),
                               {"c": str(int(val)), "i": row[0]})
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion cifras sorteos: {e}")

        # Migrar columna color en taloneras
        try:
            cols_taloneras = [c["name"] for c in inspector.get_columns("taloneras")]
            if "color" not in cols_taloneras:
                db.execute(text("ALTER TABLE taloneras ADD COLUMN color VARCHAR DEFAULT '#ffffff'"))
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion color taloneras: {e}")

        # Migrar columna valor_cuota en taloneras
        try:
            cols_taloneras = [c["name"] for c in inspector.get_columns("taloneras")]
            if "valor_cuota" not in cols_taloneras:
                db.execute(text("ALTER TABLE taloneras ADD COLUMN valor_cuota REAL DEFAULT 0.0"))
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion valor_cuota taloneras: {e}")

        # Migrar columna tipo en taloneras (COMUN/CONTADO) — talonera especial pago al contado
        try:
            cols_taloneras = [c["name"] for c in inspector.get_columns("taloneras")]
            if "tipo" not in cols_taloneras:
                db.execute(text("ALTER TABLE taloneras ADD COLUMN tipo VARCHAR DEFAULT 'COMUN'"))
                db.commit()
                db.execute(text("UPDATE taloneras SET tipo = 'COMUN' WHERE tipo IS NULL"))
                db.commit()
                print("Migracion tipo taloneras: columna creada OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion tipo taloneras: {e}")

        # Migrar columnas numero_especial y talonera_especial_id en boletas
        try:
            cols_boletas = [c["name"] for c in inspector.get_columns("boletas")]
            if "numero_especial" not in cols_boletas:
                db.execute(text("ALTER TABLE boletas ADD COLUMN numero_especial INTEGER"))
                db.commit()
                print("Migracion numero_especial boletas: columna creada OK")
            if "talonera_especial_id" not in cols_boletas:
                db.execute(text("ALTER TABLE boletas ADD COLUMN talonera_especial_id INTEGER REFERENCES taloneras(id)"))
                db.commit()
                print("Migracion talonera_especial_id boletas: columna creada OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion numero_especial/talonera_especial_id boletas: {e}")

        # Migrar columnas numero_especial_2 y talonera_especial_2_id en boletas
        # (slot 2 = sorteo CONTADO 2 VECES, ETAPA 2 de talonera especial)
        try:
            cols_boletas = [c["name"] for c in inspector.get_columns("boletas")]
            if engine.dialect.name == "postgresql":
                db.execute(text("ALTER TABLE boletas ADD COLUMN IF NOT EXISTS numero_especial_2 INTEGER"))
                db.execute(text("ALTER TABLE boletas ADD COLUMN IF NOT EXISTS talonera_especial_2_id INTEGER REFERENCES taloneras(id)"))
                db.commit()
                print("Migracion numero_especial_2/talonera_especial_2_id boletas (PG): OK")
            else:
                if "numero_especial_2" not in cols_boletas:
                    db.execute(text("ALTER TABLE boletas ADD COLUMN numero_especial_2 INTEGER"))
                    db.commit()
                    print("Migracion numero_especial_2 boletas: columna creada OK")
                if "talonera_especial_2_id" not in cols_boletas:
                    db.execute(text("ALTER TABLE boletas ADD COLUMN talonera_especial_2_id INTEGER REFERENCES taloneras(id)"))
                    db.commit()
                    print("Migracion talonera_especial_2_id boletas: columna creada OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion numero_especial_2/talonera_especial_2_id boletas: {e}")

        # Migrar columna cuotas_anticipadas en boletas
        try:
            cols_boletas = [c["name"] for c in inspector.get_columns("boletas")]
            if "cuotas_anticipadas" not in cols_boletas:
                db.execute(text("ALTER TABLE boletas ADD COLUMN cuotas_anticipadas INTEGER DEFAULT 1"))
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion cuotas_anticipadas: {e}")

        # Migrar columna cifras en premios_sorteo (modalidad POR_CIFRAS, 03/08/2026).
        # Nivel de cifras al que aplica el premio: en un semanal a 4 y 3 cifras el
        # que pega 4 cobra mas que el que pega 3. NULL en los premios ya cargados,
        # que siguen funcionando como POSICION / CADA_UNO igual que antes.
        try:
            cols_prem = [c["name"] for c in inspector.get_columns("premios_sorteo")]
            if "cifras" not in cols_prem:
                db.execute(text("ALTER TABLE premios_sorteo ADD COLUMN cifras INTEGER"))
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion cifras premios_sorteo: {e}")

        # Migrar columna comision_pct en cobradores
        try:
            cols_cob = [c["name"] for c in inspector.get_columns("cobradores")]
            if "comision_pct" not in cols_cob:
                db.execute(text("ALTER TABLE cobradores ADD COLUMN comision_pct REAL DEFAULT 10.0"))
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion comision_pct cobradores: {e}")

        # Migrar columna comision_pct en planillas
        try:
            cols_planillas = [c["name"] for c in inspector.get_columns("planillas")]
            if "comision_pct" not in cols_planillas:
                db.execute(text("ALTER TABLE planillas ADD COLUMN comision_pct REAL DEFAULT 10.0"))
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion comision_pct planillas: {e}")

        # Migrar liquidaciones.planilla_id a nullable
        try:
            cols_liq = inspector.get_columns("liquidaciones")
            planilla_col = next((c for c in cols_liq if c["name"] == "planilla_id"), None)
            if planilla_col and not planilla_col.get("nullable", True):
                dialect = engine.dialect.name
                if dialect == "postgresql":
                    db.execute(text("ALTER TABLE liquidaciones ALTER COLUMN planilla_id DROP NOT NULL"))
                    db.commit()
                else:
                    db.execute(text("PRAGMA foreign_keys = OFF"))
                    db.execute(text(
                        "CREATE TABLE liquidaciones_new ("
                        "  id INTEGER PRIMARY KEY NOT NULL,"
                        "  planilla_id INTEGER,"
                        "  fecha DATE NOT NULL,"
                        "  total_cuotas INTEGER DEFAULT 0,"
                        "  monto_total REAL DEFAULT 0.0,"
                        "  comision REAL DEFAULT 0.0,"
                        "  neto REAL DEFAULT 0.0,"
                        "  created_at DATETIME DEFAULT (CURRENT_TIMESTAMP)"
                        ")"
                    ))
                    db.execute(text(
                        "INSERT INTO liquidaciones_new"
                        " SELECT id, planilla_id, fecha, total_cuotas, monto_total, comision, neto, created_at"
                        " FROM liquidaciones"
                    ))
                    db.execute(text("DROP TABLE liquidaciones"))
                    db.execute(text("ALTER TABLE liquidaciones_new RENAME TO liquidaciones"))
                    db.execute(text("PRAGMA foreign_keys = ON"))
                    db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion liquidaciones planilla_id nullable: {e}")

        # Migrar historial_cuotas en boletas
        try:
            cols_boletas = [c["name"] for c in inspector.get_columns("boletas")]
            if "historial_cuotas" not in cols_boletas:
                db.execute(text("ALTER TABLE boletas ADD COLUMN historial_cuotas TEXT"))
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion historial_cuotas: {e}")

        # ── Migracion de DATOS: historial_cuotas de {"cuota": mes} a
        # {"cuota": "YYYY-MM"} (fix C-1, auditoria 03/07/2026). El formato viejo
        # guardaba solo el mes (1-12): con la campania 2026-2027, julio 2026 y
        # julio 2027 colisionaban (se borraban/mezclaban cuotas entre anios).
        # Todos los valores legacy existentes son de 2026: la app cobra desde
        # abril 2026 y esta migracion corre al deploy (2026), asi que se les
        # asigna anio 2026. Es idempotente: los valores ya migrados ("YYYY-MM")
        # no se tocan; si no queda ninguno legacy, no escribe nada.
        try:
            import json as _json
            _ANIO_LEGACY = 2026   # anio de campania de todos los datos pre-migracion
            _rows = db.execute(text(
                "SELECT id, historial_cuotas FROM boletas "
                "WHERE historial_cuotas IS NOT NULL AND historial_cuotas NOT IN ('', '{}')"
            )).fetchall()
            _migradas = 0
            for _bid, _raw in _rows:
                try:
                    _h = _json.loads(_raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(_h, dict):
                    continue
                _cambio = False
                for _k, _v in list(_h.items()):
                    # legacy = int 1-12 (o string de digitos); nuevo = "YYYY-MM"
                    _mes = None
                    if isinstance(_v, int) and 1 <= _v <= 12:
                        _mes = _v
                    elif isinstance(_v, str) and _v.strip().isdigit():
                        _n = int(_v.strip())
                        if 1 <= _n <= 12:
                            _mes = _n
                    if _mes is not None:
                        _h[_k] = f"{_ANIO_LEGACY:04d}-{_mes:02d}"
                        _cambio = True
                if _cambio:
                    db.execute(
                        text("UPDATE boletas SET historial_cuotas = :h WHERE id = :i"),
                        {"h": _json.dumps(_h), "i": _bid},
                    )
                    _migradas += 1
            if _migradas:
                db.commit()
                print(f"Migracion historial anio (C-1): {_migradas} boletas convertidas a YYYY-MM")
        except Exception as e:
            db.rollback()
            print(f"Migracion historial anio (C-1): {e}")

        # ── Migracion de DATOS (A-2b): timestamps guardados con utcnow() → hora
        # argentina. LiquidacionVendedor.fecha y EntregaCaja.fecha se generaban
        # en UTC: el historial semanal/mensual de vendedores corria 3 hs (una
        # liquidacion del domingo 22:00 caia en la semana/mes siguiente). Los
        # defaults nuevos usan ahora_ar(); aca se corrigen -3 hs los datos ya
        # guardados, UNA sola vez (flag en config_bono para no repetir).
        try:
            _flag = db.execute(text(
                "SELECT valor_float FROM config_bono WHERE clave = 'fechas_utc_a_ar_v1'"
            )).fetchone()
            if not _flag:
                if engine.dialect.name == "postgresql":
                    db.execute(text("UPDATE liquidaciones_vendedor SET fecha = fecha - interval '3 hours' WHERE fecha IS NOT NULL"))
                    db.execute(text("UPDATE entregas_caja SET fecha = fecha - interval '3 hours' WHERE fecha IS NOT NULL"))
                else:
                    db.execute(text("UPDATE liquidaciones_vendedor SET fecha = datetime(fecha, '-3 hours') WHERE fecha IS NOT NULL"))
                    db.execute(text("UPDATE entregas_caja SET fecha = datetime(fecha, '-3 hours') WHERE fecha IS NOT NULL"))
                db.execute(text("INSERT INTO config_bono (clave, valor_float) VALUES ('fechas_utc_a_ar_v1', 1)"))
                db.commit()
                print("Migracion fechas UTC->AR (A-2b): timestamps corridos -3 hs (una sola vez)")
        except Exception as e:
            db.rollback()
            print(f"Migracion fechas UTC->AR (A-2b): {e}")

        # Migrar enum tiposorteo en PostgreSQL para agregar valor CONTADO
        try:
            dialect = engine.dialect.name
            if dialect == "postgresql":
                db.execute(text("ALTER TYPE tiposorteo ADD VALUE IF NOT EXISTS 'CONTADO' AFTER 'MENSUAL'"))
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion tiposorteo CONTADO: {e}")

        # Crear tabla entregas_caja
        try:
            if "entregas_caja" not in inspector.get_table_names():
                db.execute(text(
                    "CREATE TABLE entregas_caja ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  talonera_nombre TEXT NOT NULL,"
                    "  desde INTEGER NOT NULL,"
                    "  hasta INTEGER NOT NULL,"
                    "  boletas_afectadas INTEGER DEFAULT 0,"
                    "  observacion TEXT,"
                    "  fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
                    "  usuario_id INTEGER REFERENCES users(id)"
                    ")"
                ))
                db.commit()
                print("Migracion entregas_caja: tabla creada OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion entregas_caja: {e}")

        # Migrar columna permissions en users
        try:
            cols_users = [c["name"] for c in inspector.get_columns("users")]
            if "permissions" not in cols_users:
                db.execute(text("ALTER TABLE users ADD COLUMN permissions TEXT"))
                db.commit()
                print("Migracion permissions users: columna creada OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion permissions users: {e}")

        # Migrar columna es_jefe_equipo en vendedores
        try:
            cols_vend = [c["name"] for c in inspector.get_columns("vendedores")]
            if "es_jefe_equipo" not in cols_vend:
                db.execute(text("ALTER TABLE vendedores ADD COLUMN es_jefe_equipo BOOLEAN DEFAULT FALSE"))
                db.commit()
                print("Migracion es_jefe_equipo vendedores: columna creada OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion es_jefe_equipo vendedores: {e}")

        # Migrar columna vendedor_id en entregas_caja
        try:
            cols_ec = [c["name"] for c in inspector.get_columns("entregas_caja")]
            if "vendedor_id" not in cols_ec:
                db.execute(text("ALTER TABLE entregas_caja ADD COLUMN vendedor_id INTEGER REFERENCES vendedores(id)"))
                db.commit()
                print("Migracion vendedor_id entregas_caja: columna creada OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion vendedor_id entregas_caja: {e}")

        # Migrar columna hecha en zonas (marca manual de zona terminada)
        try:
            cols_z = [c["name"] for c in inspector.get_columns("zonas")]
            if "hecha" not in cols_z:
                db.execute(text("ALTER TABLE zonas ADD COLUMN hecha BOOLEAN DEFAULT FALSE"))
                db.commit()
                print("Migracion hecha zonas: columna creada OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion hecha zonas: {e}")

        # Migrar columna tipo en entregas_caja (ENTREGA | RETIRO)
        try:
            cols_ec2 = [c["name"] for c in inspector.get_columns("entregas_caja")]
            if "tipo" not in cols_ec2:
                db.execute(text("ALTER TABLE entregas_caja ADD COLUMN tipo VARCHAR DEFAULT 'ENTREGA'"))
                db.execute(text("UPDATE entregas_caja SET tipo = 'ENTREGA' WHERE tipo IS NULL"))
                db.commit()
                print("Migracion tipo entregas_caja: columna creada OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion tipo entregas_caja: {e}")

        # Migrar columna tipo en entregas_cobrador (EFECTIVO | PREMIO)
        # EFECTIVO = adelanto de plata; PREMIO = premio/gasto pagado por el cobrador
        # por cuenta de la institución. Ambos bajan el saldo; se muestran separados.
        try:
            if "entregas_cobrador" in inspector.get_table_names():
                cols_ecob = [c["name"] for c in inspector.get_columns("entregas_cobrador")]
                if "tipo" not in cols_ecob:
                    db.execute(text("ALTER TABLE entregas_cobrador ADD COLUMN tipo VARCHAR DEFAULT 'EFECTIVO'"))
                    db.execute(text("UPDATE entregas_cobrador SET tipo = 'EFECTIVO' WHERE tipo IS NULL"))
                    db.commit()
                    print("Migracion tipo entregas_cobrador: columna creada OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion tipo entregas_cobrador: {e}")

        # Crear tabla liquidaciones_vendedor
        try:
            if "liquidaciones_vendedor" not in inspector.get_table_names():
                db.execute(text(
                    "CREATE TABLE liquidaciones_vendedor ("
                    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "  vendedor_id INTEGER NOT NULL REFERENCES vendedores(id),"
                    "  fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
                    "  cuotas_vendidas INTEGER DEFAULT 0,"
                    "  monto_cuotas REAL DEFAULT 0.0,"
                    "  comision_cuotas_pct REAL DEFAULT 5.0,"
                    "  comision_cuotas REAL DEFAULT 0.0,"
                    "  contados_vendidos INTEGER DEFAULT 0,"
                    "  monto_contados REAL DEFAULT 0.0,"
                    "  comision_contados_pct REAL DEFAULT 10.0,"
                    "  comision_contados REAL DEFAULT 0.0,"
                    "  total_comision REAL DEFAULT 0.0,"
                    "  observacion TEXT"
                    ")"
                ))
                db.commit()
                print("Migracion liquidaciones_vendedor: tabla creada OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion liquidaciones_vendedor: {e}")

        # Migrar liquidacion_vendedor_id en boletas
        try:
            cols_boletas = [c["name"] for c in inspector.get_columns("boletas")]
            if "liquidacion_vendedor_id" not in cols_boletas:
                db.execute(text("ALTER TABLE boletas ADD COLUMN liquidacion_vendedor_id INTEGER REFERENCES liquidaciones_vendedor(id)"))
                db.commit()
                print("Migracion liquidacion_vendedor_id boletas: columna creada OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion liquidacion_vendedor_id boletas: {e}")

        # Migrar modalidad_liquidacion en boletas (para editar liquidaciones desde el historial)
        try:
            cols_boletas = [c["name"] for c in inspector.get_columns("boletas")]
            if "modalidad_liquidacion" not in cols_boletas:
                db.execute(text("ALTER TABLE boletas ADD COLUMN modalidad_liquidacion VARCHAR"))
                db.commit()
                print("Migracion modalidad_liquidacion boletas: columna creada OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion modalidad_liquidacion boletas: {e}")

        # Migrar mes_baja en boletas (mes de baja del socio durante la cobranza)
        try:
            _dialect = engine.dialect.name
            if _dialect == "postgresql":
                db.execute(text("ALTER TABLE boletas ADD COLUMN IF NOT EXISTS mes_baja INTEGER"))
            else:
                cols_boletas = [c["name"] for c in inspector.get_columns("boletas")]
                if "mes_baja" not in cols_boletas:
                    db.execute(text("ALTER TABLE boletas ADD COLUMN mes_baja INTEGER"))
            db.commit()
            print("Migracion mes_baja boletas: OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion mes_baja boletas: {e}")

        # Migrar paso_* en boletas ("el cobrador dejó de cobrar este número →
        # pasó a otro cobrador / planilla nueva"). paso_origen_planilla_id = de
        # qué planilla salió (donde se dibuja la línea "PASÓ A ..."); paso_a =
        # etiqueta (texto, registro); paso_cuota = cuotas_pagadas al pasar.
        try:
            _dialect = engine.dialect.name
            if _dialect == "postgresql":
                db.execute(text("ALTER TABLE boletas ADD COLUMN IF NOT EXISTS paso_origen_planilla_id INTEGER"))
                db.execute(text("ALTER TABLE boletas ADD COLUMN IF NOT EXISTS paso_a VARCHAR"))
                db.execute(text("ALTER TABLE boletas ADD COLUMN IF NOT EXISTS paso_cuota INTEGER"))
            else:
                cols_boletas = [c["name"] for c in inspector.get_columns("boletas")]
                if "paso_origen_planilla_id" not in cols_boletas:
                    db.execute(text("ALTER TABLE boletas ADD COLUMN paso_origen_planilla_id INTEGER"))
                if "paso_a" not in cols_boletas:
                    db.execute(text("ALTER TABLE boletas ADD COLUMN paso_a VARCHAR"))
                if "paso_cuota" not in cols_boletas:
                    db.execute(text("ALTER TABLE boletas ADD COLUMN paso_cuota INTEGER"))
            db.commit()
            print("Migracion paso_* boletas: OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion paso_* boletas: {e}")

        # Migrar num_cuotas en taloneras
        try:
            _dialect = engine.dialect.name
            if _dialect == "postgresql":
                db.execute(text("ALTER TABLE taloneras ADD COLUMN IF NOT EXISTS num_cuotas INTEGER DEFAULT 12"))
            else:
                cols_tal = [c["name"] for c in inspector.get_columns("taloneras")]
                if "num_cuotas" not in cols_tal:
                    db.execute(text("ALTER TABLE taloneras ADD COLUMN num_cuotas INTEGER DEFAULT 12"))
            db.commit()
            print("Migracion num_cuotas taloneras: OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion num_cuotas taloneras: {e}")

        # Migrar num_digitos en taloneras (cantidad de cifras del numero, default 3)
        try:
            _dialect = engine.dialect.name
            if _dialect == "postgresql":
                db.execute(text("ALTER TABLE taloneras ADD COLUMN IF NOT EXISTS num_digitos INTEGER DEFAULT 3"))
            else:
                cols_tal = [c["name"] for c in inspector.get_columns("taloneras")]
                if "num_digitos" not in cols_tal:
                    db.execute(text("ALTER TABLE taloneras ADD COLUMN num_digitos INTEGER DEFAULT 3"))
            db.commit()
            print("Migracion num_digitos taloneras: OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion num_digitos taloneras: {e}")

        # Migrar nombre_completo / tratamiento en cobradores (30/08/2026).
        # Para los recibos de premio: "Entregado por la Cobradora Carolina Moyano".
        # El `nombre` de siempre es el apodo (CARO, MABEL) y se sigue usando en
        # todo el resto del sistema.
        try:
            _dialect = engine.dialect.name
            if _dialect == "postgresql":
                db.execute(text("ALTER TABLE cobradores ADD COLUMN IF NOT EXISTS nombre_completo VARCHAR"))
                db.execute(text("ALTER TABLE cobradores ADD COLUMN IF NOT EXISTS tratamiento VARCHAR DEFAULT 'Cobrador'"))
            else:
                cols_cob = [c["name"] for c in inspector.get_columns("cobradores")]
                if "nombre_completo" not in cols_cob:
                    db.execute(text("ALTER TABLE cobradores ADD COLUMN nombre_completo VARCHAR"))
                if "tratamiento" not in cols_cob:
                    db.execute(text("ALTER TABLE cobradores ADD COLUMN tratamiento VARCHAR DEFAULT 'Cobrador'"))
            db.commit()
            print("Migracion nombre_completo/tratamiento cobradores: OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion nombre_completo/tratamiento cobradores: {e}")

        # Corregir num_digitos para taloneras COMUN: deben ser 4 cifras (0001-9999).
        # El DEFAULT 3 fue incorrecto — solo las taloneras CONTADO usan 3 cifras.
        try:
            db.execute(text(
                "UPDATE taloneras SET num_digitos = 4 "
                "WHERE (tipo = 'COMUN' OR tipo IS NULL) AND (num_digitos IS NULL OR num_digitos < 4)"
            ))
            db.commit()
            print("Correccion num_digitos COMUN→4: OK")
        except Exception as e:
            db.rollback()
            print(f"Correccion num_digitos COMUN→4: {e}")

        # ─── PATA 0 (11/05/2026) ──────────────────────────────────────────────────
        # multiplicador pasa de Integer a Float para soportar PATA 0 (mult 0.67 = 2/3).
        # Fórmula nueva: num_series / 3.0 (en vez de num_series // 3).
        # PATA 0=0.6667 / PATA 1=1.0 / PATA 2=2.0 / PATA 3=3.0
        try:
            _dialect_m = engine.dialect.name
            if _dialect_m == "postgresql":
                # PostgreSQL: convertir tipo de columna a double precision
                db.execute(text(
                    "ALTER TABLE taloneras "
                    "ALTER COLUMN multiplicador TYPE DOUBLE PRECISION "
                    "USING multiplicador::double precision"
                ))
            # SQLite no requiere ALTER de tipo (es laxo con tipos).
            # Recalcular todos los multiplicadores con la fórmula nueva (num_series/3.0).
            # Solo aplica a taloneras COMUN; CONTADO tiene mult fijo = 1.0.
            # NO redondear: 2/3 periódico, pero × valor_cuota da exacto en Float.
            db.execute(text(
                "UPDATE taloneras SET multiplicador = "
                "CAST(num_series AS DOUBLE PRECISION) / 3.0 "
                "WHERE (tipo = 'COMUN' OR tipo IS NULL) AND num_series IS NOT NULL AND num_series > 0"
            )) if engine.dialect.name == "postgresql" else db.execute(text(
                "UPDATE taloneras SET multiplicador = "
                "CAST(num_series AS REAL) / 3.0 "
                "WHERE (tipo = 'COMUN' OR tipo IS NULL) AND num_series IS NOT NULL AND num_series > 0"
            ))
            db.commit()
            print("Migracion multiplicador FLOAT taloneras: OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion multiplicador FLOAT taloneras: {e}")

        # cuotas_equiv en liquidaciones_vendedor: pasa de Integer a Float (para soportar
        # ponderados con decimales cuando hay boletas de PATA 0 en la liquidación).
        try:
            _dialect_ce = engine.dialect.name
            if _dialect_ce == "postgresql":
                db.execute(text(
                    "ALTER TABLE liquidaciones_vendedor "
                    "ALTER COLUMN cuotas_equiv TYPE DOUBLE PRECISION "
                    "USING cuotas_equiv::double precision"
                ))
            # SQLite: tipos laxos, no hace falta ALTER.
            db.commit()
            print("Migracion cuotas_equiv FLOAT: OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion cuotas_equiv FLOAT: {e}")
        # ──────────────────────────────────────────────────────────────────────────

        # Migrar cuota_1_total en liquidaciones_vendedor
        try:
            _dialect = engine.dialect.name
            if _dialect == "postgresql":
                db.execute(text("ALTER TABLE liquidaciones_vendedor ADD COLUMN IF NOT EXISTS cuota_1_total REAL DEFAULT 0.0"))
            else:
                cols_liq = [c["name"] for c in inspector.get_columns("liquidaciones_vendedor")]
                if "cuota_1_total" not in cols_liq:
                    db.execute(text("ALTER TABLE liquidaciones_vendedor ADD COLUMN cuota_1_total REAL DEFAULT 0.0"))
            db.commit()
            print("Migracion cuota_1_total liquidaciones_vendedor: OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion cuota_1_total liquidaciones_vendedor: {e}")

        # Crear tabla liquidacion_contado_items (numeros del pool CONTADO liquidados)
        try:
            if "liquidacion_contado_items" not in inspector.get_table_names():
                if engine.dialect.name == "postgresql":
                    sql_lci = (
                        "CREATE TABLE liquidacion_contado_items ("
                        "id SERIAL PRIMARY KEY, "
                        "liquidacion_id INTEGER NOT NULL REFERENCES liquidaciones_vendedor(id), "
                        "talonera_id INTEGER NOT NULL REFERENCES taloneras(id), "
                        "numero INTEGER NOT NULL)"
                    )
                else:
                    sql_lci = (
                        "CREATE TABLE liquidacion_contado_items ("
                        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "liquidacion_id INTEGER NOT NULL REFERENCES liquidaciones_vendedor(id), "
                        "talonera_id INTEGER NOT NULL REFERENCES taloneras(id), "
                        "numero INTEGER NOT NULL)"
                    )
                db.execute(text(sql_lci))
                db.commit()
                print("Migracion liquidacion_contado_items: tabla creada OK")
        except Exception as e:
            db.rollback()
            print(f"Migracion liquidacion_contado_items: {e}")

        # Migrar columnas nuevas de "rendir" en liquidaciones_vendedor (cuotas extras + total a rendir)
        try:
            _dialect_lv = engine.dialect.name
            _nuevas_cols_lv = [
                ("cuotas_extras_cantidad",    "INTEGER DEFAULT 0"),
                ("cuotas_extras_valor",       "REAL DEFAULT 0.0"),
                ("cuotas_extras_monto",       "REAL DEFAULT 0.0"),
                ("comision_cuotas_extras",    "REAL DEFAULT 0.0"),
                ("total_a_rendir",            "REAL DEFAULT 0.0"),
                ("cuotas_extras_p0_cantidad", "INTEGER DEFAULT 0"),
                ("cuotas_extras_p0_valor",    "REAL DEFAULT 0.0"),
                ("cuotas_extras_p0_monto",    "REAL DEFAULT 0.0"),
                ("comision_cuotas_extras_p0", "REAL DEFAULT 0.0"),
            ]
            if _dialect_lv == "postgresql":
                for _col, _tipo in _nuevas_cols_lv:
                    db.execute(text("ALTER TABLE liquidaciones_vendedor ADD COLUMN IF NOT EXISTS " + _col + " " + _tipo))
            else:
                _cols_liq = [c["name"] for c in inspector.get_columns("liquidaciones_vendedor")]
                for _col, _tipo in _nuevas_cols_lv:
                    if _col not in _cols_liq:
                        db.execute(text("ALTER TABLE liquidaciones_vendedor ADD COLUMN " + _col + " " + _tipo))
            db.commit()
            print("Migracion columnas rendir liquidaciones_vendedor: OK")
        except Exception as e:
            db.rollback()
            print("Migracion columnas rendir liquidaciones_vendedor: " + str(e))

        # Migrar columna cuotas_equiv (cuotas ponderadas por multiplicador de PATA)
        try:
            _dialect_eq = engine.dialect.name
            if _dialect_eq == "postgresql":
                db.execute(text("ALTER TABLE liquidaciones_vendedor ADD COLUMN IF NOT EXISTS cuotas_equiv INTEGER DEFAULT 0"))
            else:
                _cols_eq = [c["name"] for c in inspector.get_columns("liquidaciones_vendedor")]
                if "cuotas_equiv" not in _cols_eq:
                    db.execute(text("ALTER TABLE liquidaciones_vendedor ADD COLUMN cuotas_equiv INTEGER DEFAULT 0"))
            db.commit()
            print("Migracion cuotas_equiv liquidaciones_vendedor: OK")
        except Exception as e:
            db.rollback()
            print("Migracion cuotas_equiv liquidaciones_vendedor: " + str(e))

        # Backfill cuotas_equiv para liquidaciones existentes:
        # Solo se puede recuperar con seguridad cuando contados_vendidos == 0 (todas las
        # boletas asociadas son de modalidad cuotas). Se calcula sumando el multiplicador
        # de la PATA de cada boleta atada a la liquidacion.
        try:
            # Solo procesar liquidaciones que tienen cuotas (cuotas_vendidas > 0)
            # y todavia no tienen cuotas_equiv calculado. Las que son sólo de
            # contado/extras no necesitan backfill (cuotas_equiv queda 0 = correcto).
            _liqs_pend = db.query(models.LiquidacionVendedor).filter(
                ((models.LiquidacionVendedor.cuotas_equiv == 0)
                 | (models.LiquidacionVendedor.cuotas_equiv.is_(None))),
                models.LiquidacionVendedor.cuotas_vendidas > 0,
            ).all()
            _backfilled = 0
            for _liq in _liqs_pend:
                if int(_liq.contados_vendidos or 0) > 0:
                    # Caso mixto: no podemos distinguir cuotas vs contado por boleta;
                    # dejamos cuotas_equiv = cuotas_vendidas (literal) como fallback seguro.
                    _liq.cuotas_equiv = float(_liq.cuotas_vendidas or 0)
                    _backfilled += 1
                    continue
                _bs = db.query(models.Boleta).filter_by(liquidacion_vendedor_id=_liq.id).all()
                _equiv = 0.0
                for _b in _bs:
                    # multiplicador es Float desde 11/05/2026 (PATA 0 = 0.67)
                    _mult = float((_b.talonera.multiplicador or 1.0) if _b.talonera else 1.0)
                    _equiv += _mult
                _liq.cuotas_equiv = _equiv if _equiv > 0 else float(_liq.cuotas_vendidas or 0)
                _backfilled += 1
            if _backfilled:
                db.commit()
                print("Backfill cuotas_equiv: " + str(_backfilled) + " liquidacion/es")
        except Exception as e:
            db.rollback()
            print("Backfill cuotas_equiv: " + str(e))

        # Migrar columna contados_equiv (contados ponderados por multiplicador de PATA)
        try:
            _dialect_ceq = engine.dialect.name
            if _dialect_ceq == "postgresql":
                db.execute(text("ALTER TABLE liquidaciones_vendedor ADD COLUMN IF NOT EXISTS contados_equiv DOUBLE PRECISION DEFAULT 0.0"))
            else:
                _cols_ceq = [c["name"] for c in inspector.get_columns("liquidaciones_vendedor")]
                if "contados_equiv" not in _cols_ceq:
                    db.execute(text("ALTER TABLE liquidaciones_vendedor ADD COLUMN contados_equiv REAL DEFAULT 0.0"))
            db.commit()
            print("Migracion contados_equiv liquidaciones_vendedor: OK")
        except Exception as e:
            db.rollback()
            print("Migracion contados_equiv liquidaciones_vendedor: " + str(e))

        # Backfill contados_equiv para liquidaciones existentes:
        # Suma el multiplicador de la PATA de cada boleta CONTADO (numero_especial o
        # numero_especial_2 no nulo) atada a la liquidacion. Idempotente: solo procesa
        # filas con contados_equiv en 0/NULL y contados_vendidos > 0.
        try:
            _liqs_ceq = db.query(models.LiquidacionVendedor).filter(
                ((models.LiquidacionVendedor.contados_equiv == 0)
                 | (models.LiquidacionVendedor.contados_equiv.is_(None))),
                models.LiquidacionVendedor.contados_vendidos > 0,
            ).all()
            _bf_ceq = 0
            for _liq in _liqs_ceq:
                _bs = db.query(models.Boleta).filter_by(liquidacion_vendedor_id=_liq.id).all()
                _eq = 0.0
                for _b in _bs:
                    _es_cont = (_b.numero_especial is not None) or (_b.numero_especial_2 is not None)
                    if not _es_cont:
                        continue
                    _eq += float((_b.talonera.multiplicador or 1.0) if _b.talonera else 1.0)
                _liq.contados_equiv = _eq if _eq > 0 else float(_liq.contados_vendidos or 0)
                _bf_ceq += 1
            if _bf_ceq:
                db.commit()
                print("Backfill contados_equiv: " + str(_bf_ceq) + " liquidacion/es")
        except Exception as e:
            db.rollback()
            print("Backfill contados_equiv: " + str(e))

        # ─── Backfill ciclo de boletas (16/05/2026, v3) ────────────────────────
        # Regla acordada con Sergio:
        #   VENDIDO    : rendida a la institución (con o sin socio), incluye
        #                contado y cuotas finalizadas (todas pagas).
        #   EN_COBRANZA: vendida en CUOTAS con cuotas pendientes y cobrador,
        #                y NO contado.
        # CAJA y SIN_VENDER y BAJA no se tocan.
        # Idempotente.
        #
        # PostgreSQL guarda `condicion` como ENUM nativo (tipo `condicionboleta`),
        # así que el resultado del CASE (que devuelve TEXT) hay que castearlo
        # explícitamente con `::condicionboleta`. En SQLite no hace falta porque
        # los enums se serializan como TEXT.
        try:
            dialect = engine.dialect.name
            then_en  = "'EN_COBRANZA'::condicionboleta" if dialect == "postgresql" else "'EN_COBRANZA'"
            else_ven = "'VENDIDO'::condicionboleta"     if dialect == "postgresql" else "'VENDIDO'"
            sql_backfill = (
                "UPDATE boletas SET condicion = CASE "
                "  WHEN cobrador_id IS NOT NULL "
                "   AND COALESCE(cuotas_pagadas, 0) < COALESCE(cuotas_pactadas, 0) "
                "   AND numero_especial IS NULL "
                "   AND numero_especial_2 IS NULL "
                "  THEN " + then_en + " "
                "  ELSE " + else_ven + " "
                "END "
                "WHERE condicion IN ('CAJA', 'VENDIDO', 'EN_COBRANZA') "
                "  AND (liquidacion_vendedor_id IS NOT NULL "
                "       OR comprador_id IS NOT NULL)"
            )
            r = db.execute(text(sql_backfill))
            db.commit()
            print("Backfill ciclo boletas v3: " + str(r.rowcount) + " filas actualizadas")
        except Exception as e:
            db.rollback()
            print("Backfill ciclo boletas v3: " + str(e))


                # Migrar columna periodicidad en gastos_contabilidad (UNICO | MENSUAL)
        try:
            is_pg = engine.dialect.name == "postgresql"
            if is_pg:
                db.execute(text(
                    "ALTER TABLE gastos_contabilidad "
                    "ADD COLUMN IF NOT EXISTS periodicidad VARCHAR DEFAULT 'UNICO'"
                ))
            else:
                _cols_gc = [r[1] for r in db.execute(text("PRAGMA table_info(gastos_contabilidad)")).fetchall()]
                if "periodicidad" not in _cols_gc:
                    db.execute(text(
                        "ALTER TABLE gastos_contabilidad "
                        "ADD COLUMN periodicidad VARCHAR DEFAULT 'UNICO'"
                    ))
            db.commit()
            print("Migracion periodicidad gastos_contabilidad: OK")
        except Exception as e:
            db.rollback()
            print("Migracion periodicidad gastos_contabilidad: " + str(e))

        if not db.query(models.User).filter_by(username="admin").first():
            import logging
            logging.getLogger(__name__).warning(
                "Creando usuario admin con contrasena por defecto 'admin123'"
            )
            admin = models.User(
                username="admin",
                email="admin@bomberos.com",
                hashed_password=auth_module.hash_password("admin123"),
                is_admin=True
            )
            db.add(admin)
            db.commit()
    finally:
        db.close()
