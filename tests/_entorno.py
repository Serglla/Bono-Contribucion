"""Entorno común de los tests. Importarlo ANTES que cualquier cosa de `app`.

- Apunta DATABASE_URL a una base SQLite TEMPORAL (nunca a bonos.db ni a
  Postgres de producción, aunque haya un .env con la URL real).
- Fija la campaña 2026-2027 sin leer la base (sorteo final jun-2027), para que
  las cuentas de cuotas no dependan de lo que haya cargado en Contabilidad.
"""
import atexit
import os
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)
# app.main monta "app/static" con ruta relativa: correr siempre desde la raíz.
os.chdir(RAIZ)

# En Windows la base a veces sigue abierta al salir y no se puede borrar:
# cada corrida limpia las que hayan quedado de corridas anteriores.
import glob  # noqa: E402
for _vieja in glob.glob(os.path.join(tempfile.gettempdir(), "bono_tests_*.db")):
    try:
        os.remove(_vieja)
    except OSError:
        pass

_fd, DB_PATH = tempfile.mkstemp(prefix="bono_tests_", suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = "sqlite:///" + DB_PATH.replace("\\", "/")
os.environ.setdefault("SECRET_KEY", "tests-secret")
os.environ.setdefault("PYTHONUTF8", "1")


@atexit.register
def _borrar_db():
    try:
        from app.database import engine
        engine.dispose()
    except Exception:
        pass
    try:
        os.remove(DB_PATH)
    except OSError:
        pass


from app import cuotas as _cuotas  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app import models  # noqa: E402,F401  (registra las tablas en Base)

Base.metadata.create_all(bind=engine)


def fijar_campana(**valores):
    """Congela la campaña con los defaults (o los valores pasados), sin base."""
    vals = dict(_cuotas.CAMPANA_DEFAULTS)
    vals.update(valores)
    # t en el futuro: el cache nunca vence durante los tests.
    _cuotas._campana_cache.update(t=time.monotonic() + 10**9, v=_cuotas._armar_campana(vals))


fijar_campana()


def bug_conocido(motivo):
    """Marca un test que documenta un error todavía sin arreglar (ver correr.py)."""
    def marcar(fn):
        fn.bug_conocido = motivo
        return fn
    return marcar


def base_limpia():
    """Sesión sobre una base vacía (borra todas las filas de todas las tablas)."""
    with engine.begin() as con:
        for tabla in reversed(Base.metadata.sorted_tables):
            con.execute(tabla.delete())
    return SessionLocal()
