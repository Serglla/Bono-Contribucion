# Memoria sobre Sergio

> Este archivo sirve para que Claude recuerde información importante sobre Sergio entre sesiones.
> Compártelo al inicio de cada conversación para que Claude lo lea.

---

## Datos básicos
- **Nombre:** Sergio
- **Correo:** sergiollamera78@gmail.com

## Preferencias de comunicación
- Habla en español

## Proyectos y tareas conocidas

### bono-app (app de gestión de bono de bomberos)
- **Ubicación:** `C:\Users\sergi\OneDrive\Escritorio\MeIA\bono-app\`
- **Stack:** Python 3.12 + FastAPI + Jinja2 + Bootstrap 5 + SQLAlchemy + SQLite (local) / PostgreSQL (Railway)
- **Levantar:** `py -3.12 -m uvicorn app.main:app --reload` desde la carpeta `bono-app`
- **Login por defecto:** usuario `admin`, contraseña `admin123`
- **Entidades:** Compradores, Taloneras, Boletas, Vendedores, Cobradores, Zonas
- **La app está subida a GitHub y Railway** — el entorno productivo está activo en Railway

#### Lógica de taloneras y boletas
- Cada talonera tiene `num_series` (números por boleta) y `offset_series` (separación entre series)
- El offset se calcula automáticamente del inicio de la serie 2 menos el inicio de la serie 1
- **PATA 1:** 3 series, offset 1501 → ej: 0001 / 1502 / 3003
- **PATA 2:** 6 series, offset 350 → ej: 4503 / 4853 / 5203 / 5553 / 5903 / 6253
- Los números adicionales se calculan automáticamente al crear una boleta
- Función `calcular_numeros(numero_principal, num_series, offset)` en `app/routers/taloneras.py`
- Los sorteos usan siempre 4 cifras (0001–9999)

#### Modelo Talonera (actualizado 03/05/2026)
- Campos: id, nombre, multiplicador, numero_inicio, numero_fin, num_series, offset_series, activa, color, **valor_cuota**
- `valor_cuota` (Float, default 0.0) — precio de cada cuota de la talonera
- Migración automática en startup: `ALTER TABLE taloneras ADD COLUMN valor_cuota REAL DEFAULT 0.0`

#### Formulario "Nueva Talonera" — diseño actual (03/05/2026)
- N° de series por boleta: **input libre** (text + inputmode=numeric), no dropdown
  - Al escribir 3 sugiere "PATA 1", 6 → "PATA 2", 9 → "PATA 3", 12 → "PATA 4" (en form-text debajo del campo)
  - Las series se regeneran con debounce 400ms
- Nombre: input texto libre, se autocompletado si coincide con PATA, editable
- Valor cuota $: input texto libre
- Series generadas dinámicamente según N° de series:
  - **Serie 1:** N° inicio y N° fin ambos manuales (required)
  - **Series 2+:** N° inicio manual, N° fin readonly con badge "auto" (fondo verde) — se calcula igual que la cantidad de la serie 1
  - El offset queda implícito: `serie_inicio[1] - serie_inicio[0]`
- Backend recibe: `nombre, num_series, serie_inicio[], serie_fin[], valor_cuota`
  - `numero_inicio = serie_inicio[0]`, `numero_fin = serie_fin[0]`, `offset = serie_inicio[1] - serie_inicio[0]`
- IMPORTANTE: usar `type="text" inputmode="numeric"` en todos los inputs numéricos del modal (type="number" dentro de Bootstrap modals causaba problemas de interacción en este entorno)

#### Edición y eliminación de taloneras (03/05/2026)
- Botón lápiz (azul) por cada talonera → abre modal edición compartido con todos los campos
- Botón papelera (rojo) por cada talonera → confirma y elimina
- **Regla de eliminación:** solo se puede eliminar si `count(boletas con condicion=VENDIDO) == 0`
  - Si tiene vendidas: el JS muestra alert antes de abrir el modal; el backend también lo verifica y redirige con `?error=tiene_vendidas&nombre=...`
  - El template muestra banner de error rojo en ese caso
- Eliminar borra primero todas las boletas de la talonera, luego la talonera
- Badge naranja con contador de vendidas visible en la card de cada talonera que las tenga
- Rutas: `POST /taloneras/{id}/editar` y `POST /taloneras/{id}/eliminar`
- Ruta listar acepta `?error=&nombre=` para mostrar mensaje de error

#### Sección Enumeración (03/05/2026)
- URL: `/taloneras/enumeracion` — vista global de todos los números 0001–9999
- Botón "Enumeración" en el encabezado de la sección Taloneras
- Template: `app/templates/enumeracion.html`
- Muestra cuadrícula de celdas 44×22px con colores por condición:
  - Naranja: VENDIDO | Gris oscuro: SIN_VENDER | Verde: CAJA | Celeste: EN_COBRANZA | Rojo: BAJA | Gris claro punteado: SIN_IMPRIMIR (no existe en DB)
- Contadores en encabezado: VENDIDOS / SIN VENDER / CAJA / EN COBRANZA / BAJA / SIN IMPRIMIR / REPETIDOS
- REPETIDOS = números que aparecen como numero_principal Y como adicional de otra boleta
- Filtro rápido por condición (botones debajo de la grilla, oculta/muestra celdas con JS)
- Hover sobre celda: zoom 1.3× + sombra + tooltip con número y condición

#### Funcionalidades implementadas (al 30/04/2026)
- Login / logout con JWT (cookies)
- ABM Compradores, Vendedores, Cobradores, Zonas
- ABM Taloneras con configuración de series y offset
- ABM Boletas con filtros (comprador, condición)
- Generar boletas automáticamente desde rango (sin duplicar existentes)
- Condiciones de boleta: VENDIDO, CAJA, BAJA, EN_COBRANZA, SIN_VENDER
- Auto-asignación de cobrador — al crear/editar un Comprador con zona, la boleta recibe automáticamente el cobrador de esa zona
- Tabla Compradores ampliada — columnas Fecha compra, Vendedor y Cobrador después de Zona

#### Modelo de datos — relaciones zona/cobrador (actualizado 02/05/2026)
- REEMPLAZADO Zona.cobrador_id FK por tabla muchos-a-muchos zona_cobradores
- zona_cobradores: zona_id PK, cobrador_id PK, asignado_en (DateTime) — una zona puede tener múltiples cobradores
- Zona.cobrador_id legacy: columna sigue en la DB (ignorada por ORM) — datos migrados a zona_cobradores en startup
- Zona.cobrador_id (property) devuelve el ID del último cobrador asignado (mayor asignado_en)
- Zona.cobrador (property) devuelve el objeto Cobrador del último asignado
- Cobrador.zonas (property) lista de zonas vía zona_cobradores
- Cobrador.zona_cobradores y Zona.zona_cobradores son relaciones ORM a ZonaCobrador
- CRITICO: ZonaCobrador debe definirse ANTES que Zona y Cobrador en models.py
  - Si va después: KeyError: ZonaCobrador / InvalidRequestError al arrancar
  - SQLAlchemy resuelve strings de relationship al configurar el mapper, necesita la clase ya registrada

#### Lógica clave — asignación de zonas a cobrador (nueva)
En routers/cobradores.py, función _actualizar_zonas(cobrador_id, zona_ids, db):
1. Elimina de zona_cobradores las zonas desmarcadas solo para este cobrador (no afecta a otros)
2. Agrega a zona_cobradores las zonas nuevas marcadas con timestamp datetime.utcnow()
- Regla clave: dos cobradores pueden estar en la misma zona simultáneamente

#### Lógica clave — auto-asignación cobrador al guardar comprador
En routers/compradores.py (crear y editar): usa z.cobrador_id (property) que devuelve el último asignado a la zona. No se necesita cambiar código — la property es transparente.

#### Preferencias de UI — tabla Compradores
- table-sm + font-size: 0.8rem + filas ~28px (compactas)
- Filtros inline por columna + sortable en todos los encabezados
- Fecha: almacena ISO en data-val, muestra dd/mm/yyyy
- Dirección: CSS text-transform: uppercase en la celda
- Sin botón eliminar en la tabla — solo lápiz; el eliminar queda en la pantalla de edición
- Columnas: N° Boleta · Apellido y Nombre · Dirección · Zona · Fecha compra · Vendedor · Cobrador · Teléfono · Acciones

#### Preferencias de UI — tabla Cobradores
- Columna "Zonas asignadas" con badges bg-danger-subtle text-danger
- Modales crear/editar con checkboxes de zonas en grilla scrolleable (max-height 220px)
- Checkboxes: checked si z.id in (c.zona_cobradores | map zona_id)
- Si zona ya tiene otros cobradores, se muestran todos en gris al lado del checkbox
- Texto de ayuda: "Una zona puede tener más de un cobrador activo simultáneamente"

#### Fixes técnicos importantes
- Python 3.14 incompatible con SQLAlchemy/passlib → usar siempre py -3.12
- Jinja2 cache bug → templates_config.py con cache_size=0
- bcrypt directo (sin passlib) en app/auth.py
- Starlette nuevo: TemplateResponse(request, "name.html", {ctx_sin_request})
- Pydantic v2 + Form + List[int]: usar siempre request.form().getlist("campo")
- Lock files git stale: del .git\index.lock y del .git\HEAD.lock desde CMD
- **IMPORTANTE:** En modales Bootstrap, usar `type="text" inputmode="numeric"` en lugar de `type="number"` para evitar problemas de interacción con inputs numéricos

#### Funcionalidades implementadas (al 01/05/2026)
- Vínculo Zona-Vendedor — cada Zona tiene vendedor_id FK a vendedores (nullable)
- Regla: una zona = un vendedor (se vincula al crear el primer comprador con esa zona)
- Formulario Nuevo Comprador: Fecha / Cuotas / Zona / Vendedor (zona primero, vendedor se autocompleta)
- Formulario Editar Comprador: campo Vendedor, al cambiar zona sugiere vendedor de esa zona
- Gestión de Zonas: columna "Vendedor asignado" con select editable inline
- Sección Sorteos: ABM de sorteos Tómbola Nocturna Entre Ríos (SEMANAL/MENSUAL/FINAL)
  - Campos: cifras (string CSV), num_premios, resultado_json
  - Scraper no funciona (sitio usa JS) — carga manual via modal
  - Módulo Ganadores: cruza resultados con boletas por 4c/3c/2c con exclusión
- Dashboard /reportes/: cards + tablas por talonera, zona, top vendedores/cobradores

#### Color de talonera por PATA — implementado 01/05/2026
- Talonera.color (String, default #ffffff) — migración automática
- Color picker por grupo en sección Taloneras
- Tabla Compradores: fila coloreada con --bs-table-bg: {color}40

#### Módulo Cobranza (/cobranza/) — implementado 01-02/05/2026
- Página principal: selector mes/año, tarjetas por cobrador con botón "Ver planilla"
- Planilla /cobranza/{cobrador_id}/planilla?mes=M&anio=A:
  - Template standalone (no extiende base.html) — hoja A4 exacta
  - 3 columnas, 4 bloques de 10 filas (120 slots total)
  - Lógica de PATA en el grid: agrupa boletas por PATA, salta al siguiente bloque de 10 al cambiar
  - Cuotas anticipadas (cobrador no las gestiona): celda negra + X blanca
- Tabs: Planillas | Emplantillado | Liquidación
- Emplantillado: crea Planilla y asigna boletas sin planilla_id del cobrador
- Liquidación: ingresa cuotas cobradas por boleta, calcula totales

#### Modelos relacionados a cobranza
- Planilla: cobrador_id, numero, mes, anio, comision_pct
- Liquidacion: planilla_id (unique), fecha, total_cuotas, monto_total, comision, neto
- LiquidacionDetalle: liquidacion_id, boleta_id, cuotas_cobradas

#### Exportación Excel de Socios — implementado 02/05/2026
- GET /compradores/exportar — archivo socios_YYYYMMDD.xlsx
- Dependencia: openpyxl==3.1.2

#### Despliegue en Railway
- Servicio Bono-Contribucion, conectado a repo GitHub
- PostgreSQL en Railway (postgres-volume), variable DATABASE_URL
- Dockerfile: CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
- SIEMPRE hacer git push para que Railway tome los cambios

#### Pendiente / próximos pasos
- Completar liquidación: precio por cuota para calcular monto_total, comisión y neto en pesos
- Sección especial de Recaudado (separada del dashboard)
- Posible importación de datos desde el Excel original
- Mejorar scraper automático (Selenium o Playwright)

## Preferencias de trabajo
- Va descubriendo requisitos a medida que prueba en la app — no define todo de antemano
- Prefiere ver el problema funcionando antes de validar
- Directo y al punto, sin rodeos
- Trabaja en Windows con PowerShell/CMD, Python 3.12
- Le gusta ir paso a paso y confirmar antes de avanzar
- Tiene modelos físicos reales (planillas en papel) como referencia — el digital debe replicarlos fielmente
- Ajusta visualmente: ve el resultado en pantalla y pide correcciones puntuales

## Notas adicionales
- Trabaja con planilla de bomberos CDELU 2026-2027 (lista de compradores de bono)

---
*Última actualización: 03 de mayo de 2026 — Enumeración global 0001–9999 + valor_cuota en Talonera + nueva UI modal taloneras (series con inicio/fin, input libre) + editar y eliminar taloneras*
