from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .database import engine, get_db
from . import models, auth as auth_module
from .routers import auth, compradores, taloneras, vendedores, cobradores, reportes, zonas, sorteos, cobranza

models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Bonos Bomberos CDELU", version="1.0")

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


@app.get("/health")
def health():
    return {"status": "ok"}


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
        cols = [c["name"] for c in inspector.get_columns("zonas")]
        if "cobrador_id" not in cols:
            try:
                db.execute(text("ALTER TABLE zonas ADD COLUMN cobrador_id INTEGER REFERENCES cobradores(id)"))
                db.commit()
            except Exception as e:
                db.rollback()
                print(f"Migracion cobrador_id: {e}")

        # Migrar columna num_premios en sorteos (nueva)
        try:
            cols_sorteos = [c["name"] for c in inspector.get_columns("sorteos")]
            if "num_premios" not in cols_sorteos:
                db.execute(text("ALTER TABLE sorteos ADD COLUMN num_premios INTEGER DEFAULT 20"))
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion num_premios: {e}")

        # Migrar columna resultado_json en sorteos (nueva)
        try:
            cols_sorteos = [c["name"] for c in inspector.get_columns("sorteos")]
            if "resultado_json" not in cols_sorteos:
                db.execute(text("ALTER TABLE sorteos ADD COLUMN resultado_json TEXT"))
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion resultado_json: {e}")

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

        # Migrar columna cuotas_anticipadas en boletas
        try:
            cols_boletas = [c["name"] for c in inspector.get_columns("boletas")]
            if "cuotas_anticipadas" not in cols_boletas:
                db.execute(text("ALTER TABLE boletas ADD COLUMN cuotas_anticipadas INTEGER DEFAULT 1"))
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion cuotas_anticipadas: {e}")

        # Migrar columna num_cuotas en taloneras
        try:
            cols_taloneras = [c["name"] for c in inspector.get_columns("taloneras")]
            if "num_cuotas" not in cols_taloneras:
                db.execute(text("ALTER TABLE taloneras ADD COLUMN num_cuotas INTEGER DEFAULT 11"))
                db.commit()
        except Exception as e:
            db.rollback()
            print(f"Migracion num_cuotas taloneras: {e}")

        # planillas y planilla_id en boletas son creadas por create_all (modelo Planilla en models.py)

        if not db.query(models.User).filter_by(username="admin").first():
            import logging
            logging.getLogger(__name__).warning(
                "⚠️  Creando usuario admin con contraseña por defecto 'admin123' — "
                "cambiala inmediatamente desde /auth/usuarios"
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
