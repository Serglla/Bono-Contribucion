# Memoria sobre Sergio

> Este archivo sirve para que Claude recuerde información importante sobre Sergio entre sesiones.
> Compártelo al inicio de cada conversación para que Claude lo lea.

---

## Datos básicos
- **Nombre:** Sergio
- **Correo:** sergiollamera78@gmail.com

## Preferencias de comunicación
- Habla en español

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

## Proyectos y tareas conocidas

### bono-app (app de gestión de bono de bomberos)
- **Ubicación:** `C:\Users\sergi\OneDrive\Escritorio\MeIA\bono-app\`
- **Stack:** Python 3.12 + FastAPI + Jinja2 + Bootstrap 5 + SQLAlchemy 2.0 + SQLite (local) / PostgreSQL (Railway)
- **Levantar:** `py -3.12 -m uvicorn app.main:app --reload` desde la carpeta `bono-app`
- **Login por defecto:** usuario `admin`, contraseña `admin123`
- **Entidades:** Compradores, Taloneras, Boletas, Vendedores, Cobradores, Zonas
- **La app está subida a GitHub y Railway** — el entorno productivo está activo en Railway

#### Despliegue en Railway
- Servicio Bono-Contribucion, conectado a repo GitHub
- PostgreSQL en Railway (postgres-volume), variable DATABASE_URL
- Dockerfile: CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
- SIEMPRE hacer git push para que Railway tome los cambios

---

### Fixes técnicos importantes (acumulado)
- Python 3.14 incompatible con SQLAlchemy/passlib → usar siempre py -3.12
- Jinja2 cache bug → templates_config.py con cache_size=0
- bcrypt directo (sin passlib) en app/auth.py
- Starlette nuevo: TemplateResponse(request, "name.html", {ctx_sin_request})
- Pydantic v2 + Form + List[int]: usar siempre request.form().getlist("campo")
- Lock files git stale: `del .git\index.lock` y `del .git\HEAD.lock` desde CMD
- **Escritura de archivos grandes**: usar script Python en bash para evitar truncación del Write/Edit tool
- **Columnas nuevas en ORM (PostgreSQL)**: usar `deferred(Column(...))` si la columna puede no existir aún en la DB al momento del primer SELECT; evita "column does not exist"
- **Migraciones PostgreSQL**: usar `ALTER TABLE x ADD COLUMN IF NOT EXISTS ...` (no soportado en SQLite → detectar dialect con `engine.dialect.name`)
- **Jinja2 + dicts**: acceso por punto (`s.clave`) en Jinja2 lanza `UndefinedError` si la clave no existe en el dict — siempre incluir todas las claves en el dict o usar `s.get("clave", 0)`
- **_stats_bulk**: el dict de stats DEBE incluir la clave `"baja"` aunque sea 0, porque el template accede a `s.baja`
- **Sandbox bash mount stale**: a veces el mount /sessions/.../mnt/ no sincroniza con los Edit del file tool; confiar en lo que devuelve Read y verificar contenido con grep si bash da errores raros

---

### Modelo de datos — resumen de relaciones clave

#### Taloneras y boletas
- Cada talonera tiene `num_series` y `offset_series` (separación entre series)
- **PATA 1:** 3 series, offset 1501 → ej: 0001 / 1502 / 3003
- **PATA 2:** 6 series, offset 350 → ej: 4503 / 4853 / 5203 / 5553 / 5903 / 6253
- Función `calcular_numeros(numero_principal, num_series, offset)` en `app/routers/taloneras.py`
- Condiciones de boleta: SIN_VENDER → CAJA → VENDIDO (también BAJA, EN_COBRANZA)
  - BAJA y EN_COBRANZA solo vienen de cobranza, los vendedores no usan BAJA
- `Talonera.num_cuotas` (Integer, default 12, **deferred**) — cantidad de cuotas mensuales de la talonera

#### Zona-Cobrador (muchos-a-muchos, desde 02/05/2026)
- Tabla `zona_cobradores`: zona_id PK, cobrador_id PK, asignado_en (DateTime)
- Una zona puede tener múltiples cobradores simultáneamente
- `Zona.cobrador_id` legacy: columna en DB ignorada por ORM; datos migrados a zona_cobradores en startup
- `Zona.cobrador_id` (property) → ID del último cobrador (mayor asignado_en)
- `Zona.cobrador` (property) → objeto Cobrador del último asignado
- **CRÍTICO:** ZonaCobrador debe definirse ANTES que Zona y Cobrador en models.py

#### Zona-Vendedor
- `Zona.vendedor_id` FK a vendedores (nullable) — una zona = un vendedor
- Se vincula al crear el primer comprador con esa zona

---

### Módulo Vendedores — actualizado 06/05/2026 (noche)

#### Modelos nuevos/modificados
- `Vendedor.es_jefe_equipo` (Boolean, default False) — solo un jefe activo a la vez
- `LiquidacionVendedor` (tabla `liquidaciones_vendedor`): registra liquidación de comisión al vendedor
  - Campos: vendedor_id, fecha, cuotas_vendidas, **cuota_1_total**, monto_cuotas, comision_cuotas_pct, comision_cuotas,
    contados_vendidos, monto_contados, comision_contados_pct (default 30%), comision_contados, total_comision, observacion
  - **Posición en models.py:** definida ENTRE Vendedor y Cobrador (relación bidireccional)
- `Boleta.liquidacion_vendedor_id` (FK a liquidaciones_vendedor, nullable) — enlaza boleta con su liquidación
- `Boleta.liquidacion_vendedor` — relación ORM a LiquidacionVendedor
- `EntregaCaja.vendedor_id` (FK a vendedores, nullable) — a qué vendedor se entregó

#### Flujo correcto de boletas por vendedor (CRÍTICO — confirmado con Sergio)
```
SIN_VENDER
  → [Entrega a caja] → CAJA (sin liquidacion_vendedor_id) — boleta en mano del vendedor
  → [Liquidar vendedor] → CAJA (con liquidacion_vendedor_id) — liquidado, pendiente cargar comprador
  → [Cargar comprador en sistema] → VENDIDO — comprador registrado
```
- La liquidación al vendedor se hace sobre boletas en CAJA (sin liq_id) — NO sobre VENDIDO
- Las boletas conservan condición CAJA después de liquidar, solo pasan a VENDIDO cuando se carga el comprador
- Los vendedores NO tienen boletas en BAJA — la baja viene solo de cobranza

#### Modelo de comisión del vendedor (confirmado con Sergio)
- **Cuota 1**: el vendedor SIEMPRE se queda con la primera cuota (= valor_cuota por boleta). Es su comisión de venta. Se registra en `cuota_1_total`.
- **Comisión adicional cuotas**: % sobre el monto de cuotas (default 5%). Se paga sobre las boletas vendidas en cuotas (sin contado).
- **Comisión contado**: % sobre el valor TOTAL de la talonera = num_cuotas × valor_cuota (default 30%). Se paga por cada boleta vendida al contado. Ej: PATA 1 = 12 × $15.000 = $180.000 → 30% ≈ $50.000–$54.000.
- **Total a pagar** = cuota_1_total + comision_cuotas + comision_contados

#### Modal de liquidación — selección manual
- Las boletas pendientes se muestran individualmente agrupadas por PATA, con checkboxes clickeables
- Botones "Todo / Ninguno" por PATA + "Seleccionar todo / Deseleccionar todo" global
- Cálculo en tiempo real: cuota 1 + comisión adicional + comisión contado
- El botón "Confirmar" queda deshabilitado hasta seleccionar al menos una boleta
- Backend: `POST /{vid}/liquidar` acepta lista de `boleta_ids` via `request.form().getlist("boleta_ids")`

#### Router vendedores.py — endpoints clave
- `_stats_bulk(db)`: un solo SQL con GROUP BY vendedor+condicion. Dict con claves `caja`, `vendido`, `baja` (SIEMPRE las tres)
- `GET /vendedores/`: lista vendedores con stats (caja, baja, vendido), jefe_equipo
- `GET /vendedores/{vid}/detalle`: boletas agrupadas por PATA + `pendientes_json` + **entregas_vendedor + grupos_talonera + grupos_contado + nombres_contado + vendedores_all** (para el modal Entregar a Caja)
- `POST /vendedores/{vid}/liquidar`: acepta boleta_ids seleccionados, nuevo modelo de comisión
- `POST /vendedores/{vid}/toggle-jefe`: marca jefe (primero resetea todos, luego activa el nuevo)
- `POST /vendedores/entrega-caja`: SIN_VENDER → CAJA + REASIGNAR entre vendedores en CAJA (sin liquidar)
- `POST /vendedores/entrega-caja/{id}/editar` y `eliminar`: gestión del historial de entregas

#### Entrega a Caja — actualizado 06/05/2026 (noche)
**UI movida al detalle del vendedor (cambio importante):**
- La sección global de Entrega a Caja en `/vendedores/` SE QUITÓ por completo (form + tabla + modales).
  En su lugar hay un aviso: "Hacé doble clic sobre la fila del vendedor para entregarle boletas".
- En `/vendedores/{vid}/detalle` ahora hay **dos botones en orden de flujo**:
  1. **Entregar a Caja** (rojo) — abre modal con PATA / Desde / Hasta. El `vendedor_id` se manda fijo (el del detalle).
  2. **Liquidar vendedor** (verde) — sin cambios.
  Separados visualmente con una flecha `→`.
- Tabla **"Entregas a caja recibidas"** filtrada por ese vendedor (con editar/eliminar inline).
- IDs JS del modal de entrega en detalle: `ec2-pata`, `ec2-desde`, `ec2-hasta`, `ec2-btn`, `ec2-resultado`,
  función `entregarCajaVendedor()`, tabla `ec-tbody-vendedor`.

**Comportamiento dual del backend (sin cambios):**
1. Boletas en SIN_VENDER → pasan a CAJA con el vendedor elegido (cuenta como `nuevas`)
2. Boletas en CAJA sin `liquidacion_vendedor_id` y con vendedor distinto → reasigna al nuevo vendedor (cuenta como `reasignadas`). NO toca las liquidadas.

**No ensucia historial:** si `total = nuevas + reasignadas == 0`, hace `db.rollback()` y NO crea fila en EntregaCaja.

**Respuesta JSON:**
```json
{
  "ok": true,
  "nuevas": int,
  "reasignadas": int,
  "total": int,
  "actualizadas": int,        // backward compat = total
  "entrega_id": int|null,
  "vendedor_nombre": str|null,
  "vendedor_id": int,
  "vendedores_origen": [int]  // ids de vendedores que perdieron boletas
}
```

#### Templates vendedores
- `vendedores.html`: tabla limpia, doble-click → detalle, badge jefe, stats por vendor. **Ya no tiene la sección global de Entrega a Caja** (movida al detalle).
- `vendedor_detalle.html`: 3 tarjetas resumen + 2 botones de acción (Entregar a Caja → Liquidar) + leyenda + secciones por PATA + tabla "Entregas a caja recibidas" + historial de liquidaciones + 2 modales (Entregar a Caja, Liquidar)

#### 3 estados de boleta en el detalle de vendedor (colores)
- 🔵 Azul claro (`#e7f1ff`): CAJA sin liq_id — en mano del vendedor, se puede liquidar
- 🟢 Verde (`#d1e7dd`): CAJA con liq_id — liquidado, pendiente que se cargue el comprador
- 🔵 Azul opaco (bg-primary opacity .55): VENDIDO — comprador ya registrado en sistema

#### Migraciones en main.py (startup) — todas en try/except
- `es_jefe_equipo` en vendedores (BOOLEAN DEFAULT FALSE)
- `vendedor_id` en entregas_caja (INTEGER REFERENCES vendedores)
- CREATE TABLE `liquidaciones_vendedor` (si no existe)
- `liquidacion_vendedor_id` en boletas (INTEGER REFERENCES liquidaciones_vendedor)
- `num_cuotas` en taloneras (INTEGER DEFAULT 12) — usa IF NOT EXISTS en PostgreSQL
- `cuota_1_total` en liquidaciones_vendedor (REAL DEFAULT 0.0) — usa IF NOT EXISTS en PostgreSQL
- **CRÍTICO**: todas las migraciones deben estar dentro de try/except. La línea `inspector.get_columns()` fuera de try/except crashea todo el startup si falla.

---

### Módulo Cobranza (/cobranza/) — implementado 01-02/05/2026
- Página principal: selector mes/año, tarjetas por cobrador
- Planilla: template standalone hoja A4, 3 columnas, 4 bloques de 10 filas (120 slots)
- Cuotas anticipadas: celda negra + X blanca (cobrador no las gestiona)
- Tabs: Planillas | Emplantillado | Liquidación
- Modelos: Planilla (cobrador_id, numero, mes, anio, comision_pct), Liquidacion, LiquidacionDetalle

---

### Talonera Especial CONTADO — ETAPA 1 implementada 05/05/2026

**Concepto:** número especial por pago al contado. Pool de números de una talonera tipo CONTADO, asignados a boletas comunes.

**Modelo:**
- `Talonera.tipo` (String, default "COMUN") — "COMUN" o "CONTADO"
- `Boleta.numero_especial` (Integer, nullable, indexado)
- `Boleta.talonera_especial_id` (FK a taloneras, nullable) — foreign_keys explícito por 2 FKs a la misma tabla
- `Talonera.boletas` con `foreign_keys="Boleta.talonera_id"`

**Pendiente — ETAPA 2 (asignación):**
- Botón/checkbox "Pagada al contado" en pantalla de boleta
- Auto-asignar siguiente numero_especial disponible (max + 1 desde numero_inicio)
- Validar rango no agotado

**Pendiente — ETAPA 3 (sorteo CONTADO):**
- TipoSorteo.CONTADO ya existe en el enum
- En `routers/sorteos.py` ver_ganadores: cuando tipo == CONTADO, cruzar contra `Boleta.numero_especial`

---

### Otras funcionalidades implementadas
- Login/logout JWT (cookies)
- ABM Compradores, Vendedores, Cobradores, Zonas, Taloneras, Boletas
- Generación de boletas por rango (sin duplicar)
- Auto-asignación de cobrador al guardar comprador (por zona)
- Exportación Excel socios: GET /compradores/exportar (openpyxl==3.1.2)
- Dashboard /reportes/: cards + tablas por talonera, zona, top vendedores/cobradores
- Módulo Sorteos: ABM Tómbola Nocturna Entre Ríos (SEMANAL/MENSUAL/FINAL/CONTADO)
  - Scraper no funciona (sitio usa JS) — carga manual via modal
  - Módulo Ganadores: cruza 4c/3c/2c con exclusión
- Color por PATA: Talonera.color, picker en UI, fila compradores coloreada ({color}40)

---

### Preferencias de UI consolidadas
- Tablas: table-sm + font-size: 0.8rem + filas ~28px (compactas)
- Filtros inline por columna + sortable en todos los encabezados
- Fecha: almacena ISO en data-val, muestra dd/mm/yyyy
- Dirección: CSS text-transform: uppercase en la celda
- Sin botón eliminar en tablas — solo lápiz; el eliminar queda en la pantalla de edición
- Compradores: N° Boleta · Apellido y Nombre · Dirección · Zona · Fecha compra · Vendedor · Cobrador · Teléfono · Acciones
- Cobradores: badges bg-danger-subtle, checkboxes de zonas en grilla scrolleable (max-height 220px)
- **Vendedores**: la sección "Entrega a Caja" NO está en el listado global; vive en el detalle del vendedor (acceso por doble-click)

---

### Pendientes / próximos pasos
- **Talonera CONTADO Etapa 2**: asignación auto del número especial al pagar al contado
- **Talonera CONTADO Etapa 3**: módulo sorteo cruzando Boleta.numero_especial
- Sección especial de Recaudado (separada del dashboard)
- Posible importación de datos desde el Excel original
- Mejorar scraper automático (Selenium o Playwright)
- **Liquidación vendedor**: pendiente configurar `num_cuotas` en cada talonera desde la UI de taloneras (por ahora default 12)

---
*Última actualización: 06 de mayo de 2026 (noche) — Sección Entrega a Caja MOVIDA del listado global al detalle del vendedor. En el detalle ahora hay 2 botones en orden de flujo: Entregar a Caja → Liquidar vendedor. El historial de entregas se muestra filtrado por vendedor en su detalle. Endpoint `/vendedores/{vid}/detalle` ahora devuelve también entregas_vendedor + grupos_talonera + grupos_contado + nombres_contado + vendedores_all.*
