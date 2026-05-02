# Bonos Bomberos CDELU 2026-2027

App web para gestión de taloneras de bonos.

## Cómo correr localmente

```bash
pip install -r requirements.txt
cp .env.example .env   # completar con tus datos
uvicorn app.main:app --reload
```

Acceder en: http://localhost:8000  
Usuario por defecto: **admin** / **admin123**

## Subir a Railway

1. Crear cuenta en https://railway.app
2. Nuevo proyecto → "Deploy from GitHub repo"
3. Agregar servicio PostgreSQL (Railway lo provee gratis)
4. En Variables de entorno pegar:
   - `DATABASE_URL` → la URL de PostgreSQL que da Railway
   - `SECRET_KEY` → una clave larga aleatoria (ej: `openssl rand -hex 32`)
5. Deploy automático ✅

## Estructura

```
app/
  main.py          # Entrada de la app
  models.py        # Tablas de base de datos
  auth.py          # Autenticación JWT
  database.py      # Conexión DB
  routers/         # Endpoints por sección
  templates/       # Páginas HTML
```
