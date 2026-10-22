from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import engine, get_db
from . import models, auth as auth_module
from .routers import auth, compradores, taloneras, vendedores, cobradores, reportes, zonas, sorteos, cobranza, backup, contabilidad

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
    # Esquema al día (Alembic). En Railway ya lo corrió el Dockerfile ANTES de
    # levantar los workers, así que acá es solo un chequeo rápido; en una PC
    # (uvicorn directo) migra lo que falte. Ver app/migraciones/__init__.py.
    from .migraciones import asegurar_esquema
    asegurar_esquema()

    from .database import SessionLocal
    db = SessionLocal()
    try:
        # Admin inicial: SOLO si no hay ningún administrador (base nueva).
        # Antes se creaba con 'admin123' fijo cada vez que no existía un usuario
        # llamado "admin": renombrarlo o borrarlo lo resucitaba en el próximo
        # deploy con la clave conocida. Ahora la clave sale de ADMIN_PASSWORD o,
        # si no está, se genera una al azar y se muestra UNA vez en el log.
        if not db.query(models.User).filter_by(is_admin=True).first():
            import logging
            import os
            import secrets
            _log = logging.getLogger(__name__)
            _pwd = os.getenv("ADMIN_PASSWORD") or secrets.token_urlsafe(12)
            try:
                db.add(models.User(
                    username="admin",
                    email="admin@bomberos.com",
                    hashed_password=auth_module.hash_password(_pwd),
                    is_admin=True
                ))
                db.commit()
                if os.getenv("ADMIN_PASSWORD"):
                    _log.warning("Creado usuario admin con la clave de ADMIN_PASSWORD")
                else:
                    _log.warning("Creado usuario admin con clave generada: %s  "
                                 "(guardala: no se vuelve a mostrar)", _pwd)
            except Exception:
                # Con 2 workers los dos arrancan a la vez: el segundo choca con el
                # username único y no pasa nada.
                db.rollback()
    finally:
        db.close()
