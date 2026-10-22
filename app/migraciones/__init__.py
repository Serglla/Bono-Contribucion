"""Esquema de la base: Alembic (relevamiento, punto 8).

CÓMO SE CAMBIA EL ESQUEMA DE AHORA EN MÁS
----------------------------------------
1. Cambiar el modelo en app/models.py.
2. Crear una revisión en alembic/versions/ (copiar 0001_base.py como guía; un
   ALTER por revisión, con `op.add_column(...)` etc.). Numerarlas 0002, 0003...
   y poner `down_revision` = la anterior.
3. Deployar. El Dockerfile corre `python -m app.migraciones` UNA vez antes de
   levantar los workers; cada worker además llama a `asegurar_esquema()` al
   arrancar, que no hace nada si la base ya está al día.

Antes, ~60 ALTER TABLE corrían en CADA arranque y en cada worker a la vez, con
errores que solo se imprimían. Esos pasos viejos quedaron en `legacy.py` y
corren UNA sola vez, solo en bases anteriores a Alembic.

QUÉ HACE `asegurar_esquema()`
  * Base vacía                → crea todo (create_all) + pasos viejos → marca 0001.
  * Base anterior a Alembic   → pasos viejos (por si estaba atrasada) → marca 0001.
  * Después, siempre          → `alembic upgrade head` (revisiones nuevas).
En Postgres todo va bajo un candado (pg_advisory_lock): si dos procesos
arrancan a la vez, el segundo espera y después encuentra la base al día.
"""
import logging
import os

from sqlalchemy import inspect, text

_log = logging.getLogger("migraciones")

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REVISION_BASE = "0001"
_CANDADO_PG = 7_270_001   # número cualquiera, fijo: identifica el candado de migración


def _config():
    from alembic.config import Config
    cfg = Config(os.path.join(RAIZ, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(RAIZ, "alembic"))
    return cfg


def _revision_actual(conn):
    if "alembic_version" not in inspect(conn).get_table_names():
        return None
    fila = conn.execute(text("SELECT version_num FROM alembic_version")).first()
    return fila[0] if fila else None


def _head(cfg):
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(cfg).get_current_head()


def _migrar(engine):
    from alembic import command
    from .. import models  # noqa: F401  (registra las tablas)
    from ..database import Base
    from . import legacy

    cfg = _config()
    with engine.connect() as conn:
        actual = _revision_actual(conn)
        tablas = set(inspect(conn).get_table_names())
    if actual is None:
        if "boletas" in tablas:
            _log.warning("Base anterior a Alembic: corriendo los pasos viejos una vez y marcando %s",
                         REVISION_BASE)
        else:
            _log.warning("Base vacía: creando el esquema completo")
        Base.metadata.create_all(bind=engine)
        legacy.aplicar()
        command.stamp(cfg, REVISION_BASE)
        actual = REVISION_BASE
    if actual != _head(cfg):
        command.upgrade(cfg, "head")
        _log.warning("Esquema actualizado a la revisión %s", _head(cfg))


def asegurar_esquema(engine=None):
    """Deja la base en la última revisión. Rápido si ya está al día."""
    from ..database import engine as _engine_app
    engine = engine or _engine_app
    cfg = _config()
    with engine.connect() as conn:
        if _revision_actual(conn) == _head(cfg):
            return
    if engine.dialect.name == "postgresql":
        with engine.connect() as lock:
            lock.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _CANDADO_PG})
            try:
                _migrar(engine)
            finally:
                lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _CANDADO_PG})
                lock.commit()
    else:
        _migrar(engine)
