FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Varios workers = paralelismo real entre procesos: si un request bloquea su
# event loop (los handlers usan SQLAlchemy síncrono), los demás workers siguen
# atendiendo. Ajustable con la env var WEB_CONCURRENCY en Railway según el plan.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]
