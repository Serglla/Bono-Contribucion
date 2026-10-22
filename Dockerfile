FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Varios workers = paralelismo real entre procesos: si un request bloquea su
# event loop (los handlers usan SQLAlchemy síncrono), los demás workers siguen
# atendiendo. Ajustable con la env var WEB_CONCURRENCY en Railway según el plan.
#
# Antes de levantar los workers se deja el esquema al día UNA vez (Alembic, ver
# app/migraciones). Si la migración falla, la app no arranca: mejor eso que
# atender con la base a medio migrar.
CMD ["sh", "-c", "python -m app.migraciones && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]
